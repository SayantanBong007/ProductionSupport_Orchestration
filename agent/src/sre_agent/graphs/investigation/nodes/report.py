"""Report — measure user impact and publish the incident report for human approval (no LLM)."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from langgraph.runtime import Runtime

from sre_agent.core.context import RunContext
from sre_agent.graphs.investigation.state import InvestigationState
from sre_agent.tools.base import utc_now
from sre_agent.tools.observability import client_error_stats

logger = logging.getLogger(__name__)


async def _measure_impact(incident_start: str | None) -> dict | None:
    """Synthetic-client failures since the incident started — what users actually saw."""
    try:
        start = datetime.fromisoformat(incident_start.replace("Z", "+00:00")) if incident_start else None
        seconds = (datetime.now(timezone.utc) - start).total_seconds() if start else 300
        return await client_error_stats(f"{max(60, math.ceil(seconds))}s")
    except Exception as exc:
        logger.warning(f"impact measurement failed: {exc}")
        return None


async def report(state: InvestigationState, runtime: Runtime[RunContext]) -> dict:
    ctx, diag, verification = runtime.context, state.get("diagnosis"), state.get("verification")
    await ctx.stage("report", "Diagnosis report")
    impact = await _measure_impact(state["alert"].get("incident_start"))
    if impact:
        await ctx.emit("agent_log", {"agent": "IMPACT", "type": "result",
            "msg": f"Synthetic client since incident start: {impact['failed']}/{impact['total']} requests "
                   f"failed ({impact['ratio']:.0%}) — outcomes {impact['by_outcome']}"})

    usage = {"calls": sum(v["calls"] for v in ctx.stats.values()),
             "tokens": sum(v["prompt_tokens"] + v["completion_tokens"] for v in ctx.stats.values()),
             "by_agent": ctx.stats}
    base = {"alert": state["alert"], "impact": impact, "llm_usage": usage,
            "evidence_count": len(ctx.ledger), "_timestamp": utc_now()}

    if not diag:
        result = {**base, "ai_available": False,
                  "summary": f"The AI investigation could not complete ({state.get('error')}). No diagnosis "
                             "was produced; the evidence gathered so far is archived with this incident.",
                  "root_cause": "Undetermined — AI investigation unavailable",
                  "failure_category": "unknown", "severity": None, "confidence": None,
                  "evidence": [], "evidence_verified": 0, "evidence_total": 0, "remediation": None}
    else:
        model_conf = float(diag.get("confidence") or 0)
        result = {**base, "ai_available": True,
                  "summary": diag.get("summary", ""), "root_cause": diag.get("root_cause", ""),
                  "failure_category": diag.get("failure_category", "unknown"),
                  "severity": diag.get("severity"),
                  "affected_components": diag.get("affected_components", []),
                  "timeline": diag.get("timeline", []), "open_questions": diag.get("open_questions", []),
                  # Self-assessed confidence, discounted by the share of citations that
                  # could actually be verified against the recorded tool output.
                  "model_confidence": model_conf,
                  "confidence": round(model_conf * verification["ratio"], 2),
                  "evidence": verification["citations"], "evidence_verified": verification["verified"],
                  "evidence_total": verification["total"], "verification_passed": verification["passed"],
                  "revisions": state.get("revisions", 0),
                  # Concerns still open when the revision budget ran out.
                  "review": state.get("review"),
                  "remediation": state.get("proposal")}
    await ctx.emit("final_report", result)
    return {"report": result}
