"""Incident-memory tool — lets agents consult past investigations and their outcomes."""

from __future__ import annotations

from sre_agent.integrations.incident_store import search_similar
from sre_agent.tools.base import Tool, params


async def search_past_incidents(description: str) -> str:
    try:
        past = await search_similar(description, k=3)
    except Exception as exc:
        return f"incident memory unavailable: {exc}"
    if not past:
        return "no past incidents in memory"
    lines = []
    for p in past:
        outcome = {True: "remediation succeeded", False: "remediation FAILED",
                   None: "not remediated"}[p.get("remediation_success")]
        lines.append(f"{p['incident_id']} ({p.get('timestamp', '')}, similarity {p['similarity']}): "
                     f"[{p.get('failure_category')}/{p.get('severity')}] {p.get('root_cause')} "
                     f"— runbook {p.get('runbook')}, {outcome}")
    return "past incidents most similar to the description:\n" + "\n".join(lines)


TOOLS = {
    "search_past_incidents": Tool(
        "search_past_incidents",
        "Semantic search over previously diagnosed incidents; returns their root cause, the "
        "runbook applied and whether remediation succeeded. Similar is not identical — verify "
        "against current evidence.",
        params({"description": {"type": "string", "description": "symptoms / hypothesis to match"}},
               ["description"]), search_past_incidents),
}
