import asyncio
import json
from types import SimpleNamespace

from sre_agent.agents import REPORT_FINDINGS, runner
from sre_agent.agents.runner import compact, run_agent
from sre_agent.core.evidence import EvidenceLedger
from sre_agent.tools.base import Tool, params


def _tool_msg(name, args, cid):
    call = SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    return SimpleNamespace(content="", tool_calls=[call])


def test_tool_calls_past_the_budget_are_refused_until_the_agent_reports(monkeypatch):
    executed = []

    async def probe():
        executed.append(1)
        return "probe output line"

    replies = iter([
        _tool_msg("probe", {}, "1"),            # round 1: within budget → executed
        _tool_msg("probe", {}, "2"),            # round 2 (budget spent): refused, not executed
        _tool_msg("report_findings", {"findings": []}, "3"),   # grace round: reports
    ])

    async def fake_chat(messages, **kwargs):
        if len(executed) == 1 and kwargs["tool_choice"] == "required":
            assert {t["function"]["name"] for t in kwargs["tools"]} == {"probe", "report_findings"}
        return next(replies)
    monkeypatch.setattr(runner, "chat", fake_chat)

    async def noop(*a):
        pass

    run = asyncio.run(run_agent(name="T", model="m", tools=[Tool("probe", "", params(), probe)],
                                final_tool=REPORT_FINDINGS, ledger=EvidenceLedger(), emit=noop,
                                max_rounds=2, system="s", user="u"))
    assert run.final_args == {"findings": []}
    assert executed == [1]
    assert any("not executed: tool budget exhausted" in (m.get("content") or "") for m in run.messages)


def test_compaction_shrinks_oldest_tool_outputs_first():
    msgs = [{"role": "system", "content": "s" * 100},
            {"role": "tool", "tool_call_id": "1", "content": "old " * 500},
            {"role": "tool", "tool_call_id": "2", "content": "new " * 500}]
    compact(msgs, budget_chars=2600)
    assert msgs[1]["content"].endswith("[compacted]")
    assert msgs[2]["content"] == "new " * 500          # newest observation kept intact
    assert msgs[0]["content"] == "s" * 100             # instructions never touched


def test_compaction_summarises_old_tool_call_arguments_when_still_too_big():
    big_args = json.dumps({"summary": "x" * 3000})
    msgs = [{"role": "assistant", "content": "", "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "submit_diagnosis", "arguments": big_args}}]},
            {"role": "tool", "tool_call_id": "1", "content": "received"},
            {"role": "user", "content": "feedback"},
            {"role": "assistant", "content": "ok"}]
    compact(msgs, budget_chars=1000)
    args = msgs[0]["tool_calls"][0]["function"]["arguments"]
    assert len(args) < 300 and "compacted" in json.loads(args)


def test_compaction_is_a_no_op_within_budget():
    msgs = [{"role": "tool", "tool_call_id": "1", "content": "x" * 1000}]
    compact(msgs, budget_chars=5000)
    assert msgs[0]["content"] == "x" * 1000
