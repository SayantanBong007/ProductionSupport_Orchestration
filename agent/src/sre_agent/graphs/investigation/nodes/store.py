"""Store — archive the full incident record and index it in incident memory (no LLM)."""

from __future__ import annotations

import logging

from langgraph.runtime import Runtime

from sre_agent.core.context import RunContext
from sre_agent.graphs.investigation.state import InvestigationState
from sre_agent.integrations.incident_store import store_incident

logger = logging.getLogger(__name__)


async def store(state: InvestigationState, runtime: Runtime[RunContext]) -> dict:
    ctx, report = runtime.context, state["report"]
    incident_id = "incident_" + report["_timestamp"].replace(":", "-").replace("T", "_").rstrip("Z")
    record = {"incident_id": incident_id, "created_at": report["_timestamp"], "alert": state["alert"],
              "diagnosis": state.get("diagnosis"), "verification": state.get("verification"),
              "remediation_proposal": state.get("proposal"), "report": report,
              "evidence_ledger": ctx.ledger.to_list(), "remediation": None}
    try:
        await store_incident(incident_id, record)
        await ctx.emit("agent_log", {"agent": "INCIDENT MEMORY", "type": "result",
            "msg": f"Archived {incident_id}: {len(ctx.ledger)} evidence records, "
                   f"{report['llm_usage']['calls']} LLM calls, {report['llm_usage']['tokens']} tokens"})
    except Exception as exc:
        logger.warning(f"store_incident failed: {exc}")
        await ctx.emit("agent_log", {"agent": "INCIDENT MEMORY", "type": "error", "msg": f"archive failed: {exc}"})
    return {"incident_id": incident_id, "report": {**report, "_incident_id": incident_id}}
