"""Investigation graph: from an alert to a verified, reviewed diagnosis and a remediation proposal."""

from __future__ import annotations

from sre_agent.core.context import RunContext
from sre_agent.core.evidence import EvidenceLedger
from sre_agent.core.llm import Emit
from sre_agent.graphs.investigation.graph import graph


async def run_investigation(emit: Emit, alert: dict) -> dict:
    """Investigate the incident described by ``alert`` (signals, detected_at, incident_start).

    Returns the published report.
    """
    result = await graph.ainvoke(
        {"alert": alert, "revisions": 0, "commander_messages": None},
        context=RunContext(emit=emit, ledger=EvidenceLedger(), stats={}),
    )
    return result.get("report") or {}


__all__ = ["graph", "run_investigation"]
