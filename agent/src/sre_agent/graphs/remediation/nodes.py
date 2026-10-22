"""Remediation graph nodes: execute the runbook, verify recovery, close the incident."""

from __future__ import annotations

import asyncio
import logging
import time

from langgraph.runtime import Runtime

from sre_agent.config import ALERT_CLIENT_ERROR_RATIO, COMMANDER_MODEL, CRITICAL_CONTAINERS
from sre_agent.core.context import RunContext
from sre_agent.core.llm import LLMUnavailable, stream_llm
from sre_agent.graphs.remediation.actions import ACTIONS
from sre_agent.graphs.remediation.state import RemediationState
from sre_agent.integrations.docker import project_containers
from sre_agent.integrations.incident_store import update_remediation
from sre_agent.tools.observability import client_error_stats, probe_health

logger = logging.getLogger(__name__)

# Real traffic must flow for this long after the fix before recovery is judged.
RECOVERY_OBSERVATION_SECONDS = 20

CLOSURE_SYSTEM = (
    "You are the Incident Commander writing the incident closure note (3 sentences): what was "
    "done, the measured result, and whether follow-up is needed. Use only the facts given; if "
    "the service did not recover, say so plainly."
)


async def execute(state: RemediationState, runtime: Runtime[RunContext]) -> dict:
    """Run the runbook steps in order; stop at the first failing step."""
    emit, proposal = runtime.context.emit, state["proposal"]
    await runtime.context.stage("execute", "Execution")
    steps = proposal["steps"]
    await emit("agent_log", {"agent": "EXECUTOR", "type": "start",
        "msg": f"Running approved runbook '{proposal['runbook_id']}' on {proposal['target']} ({len(steps)} steps)"})
    results = []
    for i, step in enumerate(steps, 1):
        action, target = step["action"], step["target"]
        await emit("agent_log", {"agent": "EXECUTOR", "type": "step", "msg": f"{i}/{len(steps)} {action} {target}"})
        t0 = time.monotonic()
        try:
            ok, detail = await ACTIONS[action](**step)
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append({"action": action, "target": target, "ok": ok, "detail": detail,
                        "seconds": round(time.monotonic() - t0, 1)})
        await emit("agent_log", {"agent": "EXECUTOR", "type": "result" if ok else "error",
            "msg": f"{'✓' if ok else '✗'} {detail} ({results[-1]['seconds']}s)"})
        if not ok:
            break
    return {"steps": results}


async def verify(state: RemediationState, runtime: Runtime[RunContext]) -> dict:
    """Measure recovery independently of the steps: health, containers, real client traffic."""
    emit = runtime.context.emit
    await runtime.context.stage("recovery", "Recovery check")
    await emit("agent_log", {"agent": "VERIFIER", "type": "start",
        "msg": f"Measuring recovery: health, containers, and {RECOVERY_OBSERVATION_SECONDS}s of real client traffic…"})
    await asyncio.sleep(RECOVERY_OBSERVATION_SECONDS)

    health = await probe_health()
    states = {c["name"]: c["state"] for c in await project_containers()}
    critical_down = [n for n in CRITICAL_CONTAINERS if states.get(n) != "running"]
    try:
        client = await client_error_stats(f"{RECOVERY_OBSERVATION_SECONDS}s")
    except Exception as exc:
        logger.warning(f"client stats failed: {exc}")
        client = None
    client_ok = client is None or (client["total"] > 0 and client["ratio"] < ALERT_CLIENT_ERROR_RATIO)
    success = health["ok"] and not critical_down and client_ok and all(s["ok"] for s in state["steps"])

    await emit("agent_log", {"agent": "VERIFIER", "type": "result" if success else "error",
        "msg": f"/health: {health['status_code'] or health['error']} | critical containers down: "
               f"{critical_down or 'none'} | client last {RECOVERY_OBSERVATION_SECONDS}s: "
               + (f"{client['failed']}/{client['total']} failed" if client else "no data")
               + f" → {'RECOVERED' if success else 'NOT RECOVERED'}"})
    return {"verification": {"success": success, "health": health, "containers": states,
                             "critical_down": critical_down, "client": client}}


def _facts(state: RemediationState) -> str:
    v, client = state["verification"], state["verification"]["client"]
    steps = "\n".join(f"- {s['action']} {s['target']}: {'ok' if s['ok'] else 'FAILED'} — {s['detail']}"
                      for s in state["steps"])
    return (f"Diagnosed root cause: {state['report'].get('root_cause')}\n"
            f"Runbook: {state['proposal']['title']} on {state['proposal']['target']}\n"
            f"Steps executed:\n{steps}\n"
            f"Post-fix /health: {v['health']['status_code'] or v['health']['error']} {v['health']['text'][:200]}\n"
            f"Critical containers not running: {v['critical_down'] or 'none'}\n"
            "Synthetic client after fix: "
            + (f"{client['failed']}/{client['total']} requests failed" if client else "no data")
            + f"\nVerified outcome: {'RECOVERED' if v['success'] else 'NOT RECOVERED'}")


async def closure(state: RemediationState, runtime: Runtime[RunContext]) -> dict:
    """Write the closure note and record the outcome in incident memory."""
    ctx, v = runtime.context, state["verification"]
    await ctx.stage("closure", "Closure")
    facts = _facts(state)
    try:
        note = await stream_llm(CLOSURE_SYSTEM, facts, ctx.emit, "INCIDENT COMMANDER",
                                model=COMMANDER_MODEL, stats=ctx.stats)
    except LLMUnavailable as exc:
        # No AI narrative available — publish the measured facts verbatim instead.
        logger.warning(f"closure LLM unavailable: {exc}")
        note = facts

    if state.get("incident_id"):
        try:
            await update_remediation(state["incident_id"], {
                "success": v["success"], "closure": note, "runbook_id": state["proposal"]["runbook_id"],
                "target": state["proposal"]["target"], "steps": state["steps"],
                "verification": {k: v[k] for k in ("success", "critical_down", "client")},
            })
        except Exception as exc:
            logger.warning(f"update_remediation failed: {exc}")

    await ctx.emit("incident_closed", {"success": v["success"], "closure": note})
    return {"closure": note}
