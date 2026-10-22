"""
Specialist agents the Incident Commander can dispatch.

Each specialist has a domain and a toolset scoped to it. Their prompt
describes a method, not an answer: nothing here knows what failure modes the
monitored system has.
"""

from __future__ import annotations

from dataclasses import dataclass

from sre_agent.config import INFRA_AGENT_MODEL, LOGS_AGENT_MODEL, METRICS_AGENT_MODEL
from sre_agent.tools import INFRA_TOOLSET, LOGS_TOOLSET, METRICS_TOOLSET, Tool
from sre_agent.tools.base import params


@dataclass
class Specialist:
    key: str
    label: str
    domain: str
    tools: list[Tool]
    model: str


SPECIALISTS = {
    "logs": Specialist(
        "logs", "LOGS AGENT",
        "application logs in Elasticsearch and container stdout/stderr",
        LOGS_TOOLSET, LOGS_AGENT_MODEL),
    "metrics": Specialist(
        "metrics", "METRICS AGENT",
        "Prometheus metrics (server-side and synthetic-client traffic) and the live health endpoint",
        METRICS_TOOLSET, METRICS_AGENT_MODEL),
    "infrastructure": Specialist(
        "infrastructure", "INFRA AGENT",
        "container states, exit codes, Docker lifecycle events and container output",
        INFRA_TOOLSET, INFRA_AGENT_MODEL),
}


# Final tool every specialist must call: structured findings, each tied to an
# evidence id and a verbatim quote so it can be verified before the commander sees it.
REPORT_FINDINGS = Tool(
    "report_findings",
    "Report your findings to the Incident Commander. Each finding's quote is machine-checked "
    "against the cited evidence output; unverifiable findings are flagged to the commander.",
    params({
        "findings": {"type": "array", "maxItems": 6, "items": {"type": "object", "properties": {
            "finding": {"type": "string", "description": "one observation, with its timestamp if known"},
            "evidence_id": {"type": "string", "description": "e.g. E7"},
            "quote": {"type": "string", "description": "exact substring of that evidence output (one line)"}},
            "required": ["finding", "evidence_id", "quote"]}},
        "checked_normal": {"type": "array", "items": {"type": "string"},
                           "description": "things you checked that looked normal"},
    }, ["findings"]),
    fn=None, records_evidence=False)


def specialist_system_prompt(spec: Specialist, max_rounds: int) -> str:
    return (
        f"You are the {spec.label.title()} on an SRE incident-response team. Your domain: {spec.domain}.\n"
        "The Incident Commander gives you a task. Investigate it with your tools, then call "
        "report_findings.\n\n"
        "Method: start broad, then narrow down. Establish WHEN things changed (timestamps) and the "
        "EXACT error text. Look for the earliest abnormal signal, not just the loudest one.\n\n"
        "Rules: every finding must cite the evidence id [E#] it came from and a quote copied "
        "character-for-character from that tool output (one short line). Report only what your "
        "tools returned — a number or event you did not observe must not appear in a finding. "
        f"You have at most {max_rounds - 1} rounds of tool calls before you must report."
    )
