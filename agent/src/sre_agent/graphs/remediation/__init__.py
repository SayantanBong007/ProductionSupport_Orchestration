"""Remediation graph: execute an approved runbook, measure recovery, close the incident."""

from __future__ import annotations

from sre_agent.core.context import RunContext
from sre_agent.core.llm import Emit
from sre_agent.graphs.remediation.graph import graph


async def run_remediation(report: dict, emit: Emit) -> dict:
    """Run the runbook proposed in ``report`` (already approved by a human)."""
    proposal = report.get("remediation")
    if not proposal:
        await emit("incident_closed", {"success": False,
                                       "closure": "No approved runbook was proposed — manual remediation required."})
        return {"success": False}
    await emit("agent_log", {"agent": "EXECUTOR", "type": "start",
        "msg": f"Human approved: {proposal['title']} → {proposal['target']}"})
    result = await graph.ainvoke(
        {"report": report, "proposal": proposal, "incident_id": report.get("_incident_id")},
        context=RunContext(emit=emit),
    )
    return {"success": result["verification"]["success"], "closure": result.get("closure", "")}


__all__ = ["graph", "run_remediation"]
