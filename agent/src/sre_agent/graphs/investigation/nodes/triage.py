"""Triage — record a first snapshot of the deployment as evidence (no LLM)."""

from __future__ import annotations

from langgraph.runtime import Runtime

from sre_agent.core.context import RunContext
from sre_agent.graphs.investigation.state import InvestigationState
from sre_agent.tools import INFRA_TOOLS, OBS_TOOLS


async def triage(state: InvestigationState, runtime: Runtime[RunContext]) -> dict:
    emit, ledger = runtime.context.emit, runtime.context.ledger
    await runtime.context.stage("triage", "Triage — first snapshot")
    await emit("agent_log", {"agent": "TRIAGE", "type": "start",
        "msg": "Recording initial snapshot: deployment containers + live health endpoint"})
    lines = []
    for tool in (INFRA_TOOLS["list_containers"], OBS_TOOLS["check_service_health"]):
        try:
            out = await tool.fn()
        except Exception as exc:
            out = f"error: {type(exc).__name__}: {exc}"
        ev = ledger.add("TRIAGE", tool.name, {}, out)
        await emit("agent_log", {"agent": "TRIAGE", "type": "result",
            "msg": f"[{ev.id}] {out.splitlines()[0][:220]}"})
        lines.append(f"[{ev.id}] {tool.name}()\n{out}")
    return {"triage": "\n\n".join(lines)}
