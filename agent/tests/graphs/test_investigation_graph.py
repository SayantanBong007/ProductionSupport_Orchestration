"""
End-to-end tests of the investigation graph with a scripted LLM.

The real LangGraph, agent loop, evidence ledger, verifier and runbook
validation run unchanged; only the LLM and the system probes are replaced.
"""

import asyncio
import importlib
import json
import re
from types import SimpleNamespace

import pytest

from sre_agent.agents import runner
from sre_agent.config import BACKEND_CONTAINER
from sre_agent.core.llm import LLMUnavailable
from sre_agent.graphs.investigation import run_investigation
from sre_agent.tools import INFRA_TOOLS, OBS_TOOLS

# Node modules (imported by path: the nodes package re-exports same-named functions).
report_node = importlib.import_module("sre_agent.graphs.investigation.nodes.report")
store_node = importlib.import_module("sre_agent.graphs.investigation.nodes.store")

LEAK_LOG = ("12x  2026-10-21T13:52:06Z → 2026-10-21T13:58:01Z  ERROR  GET /orders 503 — TimeoutError: "
            "QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 3.00")
ALERT = {"signals": ["http://backend:8000/health failing — HTTP 503"],
         "detected_at": "2026-10-21T13:52:30Z", "incident_start": "2026-10-21T13:52:10Z"}


def _call(name, args, cid):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _msg(content="", calls=None):
    return SimpleNamespace(content=content, tool_calls=calls or None)


def _evidence_id(messages, tool):
    """Find the id the ledger assigned to a tool result, as the LLM would read it."""
    for m in messages:
        if m["role"] == "tool":
            hit = re.match(rf"\[(E\d+)\] {tool}\(", m["content"])
            if hit:
                return hit.group(1)
    raise AssertionError(f"no {tool} evidence in conversation")


class ScriptedLLM:
    """Per-agent queues of responses; a response may be a function of the conversation."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    async def __call__(self, messages, *, model, agent, **kwargs):
        self.calls.append((agent, model))
        step = self.script[agent].pop(0)
        return step(messages) if callable(step) else step


@pytest.fixture
def system(monkeypatch):
    """Replace system probes with fixed observations of a leaking connection pool."""
    async def list_containers():
        return f"2 containers in this deployment:\n{BACKEND_CONTAINER}: running (Up 10 minutes)\npoc-postgres: running"

    async def health():
        return "GET http://backend:8000/health -> HTTP 503 in 3004 ms\nbody: {\"status\":\"unhealthy\"}"

    async def search_logs(query="*", minutes=15, level=""):
        return f"index backend-logs, last {minutes}m, query='{query}': 12 entries (ERROR: 12)\n{LEAK_LOG}"

    async def docker_events(minutes=15):
        return f"no container lifecycle events in the last {minutes}m"

    async def impact(window):
        return {"window": window, "total": 100, "failed": 60, "ratio": 0.6,
                "by_path": {"/orders": {"total": 50, "failed": 50}}, "by_outcome": {"ok": 40, "http_5xx": 60}}

    stored = {}

    async def store(incident_id, record):
        stored[incident_id] = record

    monkeypatch.setattr(INFRA_TOOLS["list_containers"], "fn", list_containers)
    monkeypatch.setattr(OBS_TOOLS["check_service_health"], "fn", health)
    monkeypatch.setattr(OBS_TOOLS["search_logs"], "fn", search_logs)
    monkeypatch.setattr(INFRA_TOOLS["docker_events"], "fn", docker_events)
    monkeypatch.setattr(report_node, "client_error_stats", impact)
    monkeypatch.setattr(store_node, "store_incident", store)
    return stored


def _diagnosis(quote_for):
    def submit(messages):
        logs_id = _evidence_id_from_findings(messages)
        return _msg(calls=[_call("submit_diagnosis", {
            "summary": "Backend connection pool exhausted; requests time out.",
            "root_cause": "All SQLAlchemy pool connections are checked out and never returned.",
            "failure_category": "resource_exhaustion", "affected_components": [BACKEND_CONTAINER],
            "severity": "CRITICAL", "confidence": 0.9,
            "evidence": [{"id": logs_id, "quote": quote_for, "supports": "pool exhausted"},
                         {"id": "E1", "quote": f"{BACKEND_CONTAINER}: running", "supports": "process alive"}],
        }, "c-submit")])
    return submit


def _evidence_id_from_findings(messages):
    for m in messages:
        if m["role"] == "tool" and m["content"].startswith("LOGS AGENT findings"):
            return re.search(r"\[(E\d+)\]", m["content"]).group(1)
    raise AssertionError("commander never received the logs findings")


def _specialist(tool, args, finding, quote):
    def report(m):
        return _msg(calls=[_call("report_findings", {"findings": [
            {"finding": finding, "evidence_id": _evidence_id(m, tool), "quote": quote}]}, f"r-{tool}")])
    return [_msg(calls=[_call(tool, args, f"c-{tool}")]), report]


def _script(commander_tail, logs_quote="QueuePool limit of size 5 overflow 10 reached"):
    return {
        "INCIDENT COMMANDER": [
            _msg("Hypotheses: DB down vs pool exhaustion. Dispatching in parallel.", calls=[
                _call("dispatch_specialist", {"specialist": "logs", "task": "find DB errors"}, "c1"),
                _call("dispatch_specialist", {"specialist": "infrastructure", "task": "container events"}, "c2"),
            ]),
            *commander_tail,
        ],
        "LOGS AGENT": _specialist("search_logs", {"query": "error", "minutes": 10},
                                  "pool timeouts on every request", logs_quote),
        "INFRA AGENT": _specialist("docker_events", {"minutes": 10},
                                   "no container restarts", "no container lifecycle events"),
        "REVIEWER": [_ACCEPT, _ACCEPT, _ACCEPT],
        "REMEDIATION PLANNER": [
            _msg(calls=[_call("propose_remediation", {"runbook_id": "restart_service", "target": BACKEND_CONTAINER,
                                                      "rationale": "fresh process releases the pool"}, "p1")]),
        ],
    }


_ACCEPT = _msg(calls=[_call("review_diagnosis", {"verdict": "accept"}, "rv")])


async def _noop(event, data):
    pass


def test_commander_dispatches_specialists_and_grounded_diagnosis_is_accepted(system, monkeypatch):
    llm = ScriptedLLM(_script([_diagnosis("QueuePool limit of size 5 overflow 10 reached")]))
    monkeypatch.setattr(runner, "chat", llm)

    report = asyncio.run(run_investigation(_noop, ALERT))

    assert report["ai_available"] and report["verification_passed"]
    assert report["evidence_verified"] == report["evidence_total"] == 2
    assert report["confidence"] == 0.9                       # 0.9 × 2/2 verified
    assert report["revisions"] == 0
    assert report["remediation"]["runbook_id"] == "restart_service"
    assert [s["action"] for s in report["remediation"]["steps"]] == ["docker_restart", "wait_http_healthy"]
    assert report["impact"]["failed"] == 60
    # specialists ran on the specialist model, the commander on its own
    models = dict(llm.calls)
    assert models["LOGS AGENT"] != models["INCIDENT COMMANDER"]
    # the full ledger is archived with the incident
    [record] = system.values()
    assert {e["tool"] for e in record["evidence_ledger"]} >= {"list_containers", "search_logs", "docker_events"}


def test_unsupported_diagnosis_is_sent_back_and_revised(system, monkeypatch):
    llm = ScriptedLLM(_script([
        _diagnosis("database container crashed with exit code 137"),   # invented
        _diagnosis("QueuePool limit of size 5 overflow 10 reached"),     # grounded
    ]))
    monkeypatch.setattr(runner, "chat", llm)

    report = asyncio.run(run_investigation(_noop, ALERT))

    assert report["revisions"] == 1
    assert report["verification_passed"]
    commander_turns = [a for a, _ in llm.calls if a == "INCIDENT COMMANDER"]
    assert len(commander_turns) == 3


def test_reviewer_challenge_sends_diagnosis_back_with_its_concerns(system, monkeypatch):
    seen = {}

    def resubmit(messages):
        seen["feedback"] = messages[-1]["content"]
        return _diagnosis("QueuePool limit of size 5 overflow 10 reached")(messages)

    script = _script([_diagnosis("QueuePool limit of size 5 overflow 10 reached"), resubmit])
    script["REVIEWER"] = [
        _msg(calls=[_call("review_diagnosis", {
            "verdict": "challenge",
            "unsupported_claims": ["'pool too small for load' — no request-rate evidence"],
            "untested_alternatives": ["connections leaked (checked out, never returned)"],
            "suggested_checks": ["compare pool checked-out connections with in-flight requests"]}, "rv1")]),
        _ACCEPT,
    ]
    monkeypatch.setattr(runner, "chat", ScriptedLLM(script))

    report = asyncio.run(run_investigation(_noop, ALERT))

    assert report["revisions"] == 1
    assert "The Reviewer challenged your diagnosis" in seen["feedback"]
    assert "connections leaked" in seen["feedback"]
    assert report["review"]["verdict"] == "accept"


def test_failed_revision_keeps_verified_diagnosis_with_open_concerns(system, monkeypatch):
    def provider_error(messages):
        raise LLMUnavailable("bad request: tool_use_failed")

    challenge = _msg(calls=[_call("review_diagnosis", {
        "verdict": "challenge", "untested_alternatives": ["connection leak vs load"]}, "rv")])
    script = _script([_diagnosis("QueuePool limit of size 5 overflow 10 reached"), provider_error])
    script["REVIEWER"] = [challenge, challenge]
    monkeypatch.setattr(runner, "chat", ScriptedLLM(script))

    report = asyncio.run(run_investigation(_noop, ALERT))

    assert report["ai_available"] and report["root_cause"].startswith("All SQLAlchemy pool connections")
    assert report["review"]["verdict"] == "challenge"
    assert report["review"]["untested_alternatives"] == ["connection leak vs load"]


def test_invented_specialist_finding_reaches_commander_flagged(system, monkeypatch):
    seen = {}

    def submit(messages):
        seen["findings"] = next(m["content"] for m in messages
                                if m["role"] == "tool" and m["content"].startswith("LOGS AGENT findings"))
        return _diagnosis("QueuePool limit of size 5 overflow 10 reached")(messages)

    llm = ScriptedLLM(_script([submit], logs_quote="requests rose from 200 to 600 per minute"))
    monkeypatch.setattr(runner, "chat", llm)

    asyncio.run(run_investigation(_noop, ALERT))

    assert "(0/1 verified" in seen["findings"]
    assert "✗ UNVERIFIED" in seen["findings"] and "do not rely on this" in seen["findings"]


def test_invalid_runbook_target_is_rejected_then_corrected(system, monkeypatch):
    script = _script([_diagnosis("QueuePool limit of size 5 overflow 10 reached")])
    script["REMEDIATION PLANNER"] = [
        _msg(calls=[_call("propose_remediation", {"runbook_id": "restart_service", "target": "poc-postgres",
                                                  "rationale": "x"}, "p1")]),
        _msg(calls=[_call("propose_remediation", {"runbook_id": "restart_service", "target": BACKEND_CONTAINER,
                                                  "rationale": "fresh process releases the pool"}, "p2")]),
    ]
    monkeypatch.setattr(runner, "chat", ScriptedLLM(script))

    report = asyncio.run(run_investigation(_noop, ALERT))
    assert report["remediation"]["target"] == BACKEND_CONTAINER


def test_llm_outage_produces_no_diagnosis_and_no_fix(system, monkeypatch):
    async def down(*a, **k):
        raise LLMUnavailable("HTTP 404: model not found")
    monkeypatch.setattr(runner, "chat", down)

    report = asyncio.run(run_investigation(_noop, ALERT))

    assert report["ai_available"] is False
    assert report["remediation"] is None
    assert "404" in report["summary"]
