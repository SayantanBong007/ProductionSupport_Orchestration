"""
Incident Commander — the supervisor agent.

Forms hypotheses, dispatches the specialist agents (in parallel when it asks
for several at once), consults incident memory, and submits a diagnosis that
cites evidence ids with verbatim quotes. Specialist findings are verified
against the evidence ledger before the commander sees them.
"""

from __future__ import annotations

import logging

from langgraph.runtime import Runtime

from sre_agent.agents import (
    REPORT_FINDINGS, SPECIALISTS, AgentError, run_agent, specialist_system_prompt,
)
from sre_agent.config import (
    COMMANDER_MAX_ROUNDS, COMMANDER_MODEL, MAX_DIAGNOSIS_REVISIONS, SPECIALIST_MAX_ROUNDS,
)
from sre_agent.core.context import RunContext
from sre_agent.core.evidence import verify_citations
from sre_agent.core.llm import LLMUnavailable
from sre_agent.graphs.investigation.prompts import alert_text, commander_system
from sre_agent.graphs.investigation.schemas import SUBMIT_DIAGNOSIS
from sre_agent.graphs.investigation.state import InvestigationState
from sre_agent.tools import MEMORY_TOOLSET, Tool
from sre_agent.tools.base import params, utc_now

logger = logging.getLogger(__name__)

# A revision only needs to address feedback, so it gets a smaller budget.
_REVISION_ROUNDS = 3


async def _verified_findings(label: str, report: dict, ctx: RunContext) -> str:
    """Check a specialist's findings against the ledger before the commander sees them.

    Verifying at the source stops a specialist's invented detail from steering
    the commander's reasoning: unverifiable findings are passed on, but marked.
    """
    findings = report.get("findings") or []
    checked = verify_citations(
        [{"id": f.get("evidence_id", ""), "quote": f.get("quote", ""), "finding": f.get("finding", "")}
         for f in findings], ctx.ledger)
    lines = []
    for c in checked:
        mark = "✓" if c["verified"] else "✗ UNVERIFIED"
        lines.append(f"{mark} [{c['id']}] {c['finding']} — quote: \"{c['quote']}\""
                     + ("" if c["verified"] else f" ({c['reason']}; do not rely on this)"))
        await ctx.emit("agent_log", {"agent": label, "type": "result" if c["verified"] else "error",
            "msg": f"{'✓' if c['verified'] else '✗'} [{c['id']}] {c['finding'][:260]}"})
    normal = report.get("checked_normal") or []
    ok = sum(c["verified"] for c in checked)
    return (f"{label} findings ({ok}/{len(checked)} verified against tool output):\n"
            + ("\n".join(lines) or "(no findings reported)")
            + (("\nChecked and normal: " + "; ".join(normal)) if normal else ""))


def _dispatch_tool(alert: dict, ctx: RunContext) -> Tool:
    """The commander's tool for sending a specialist agent on a task."""
    containers = ctx.ledger.get("E1").output if ctx.ledger.get("E1") else ""

    async def dispatch_specialist(specialist: str, task: str) -> str:
        spec = SPECIALISTS.get(specialist)
        if spec is None:
            return f"error: unknown specialist '{specialist}'. Choose from: {', '.join(SPECIALISTS)}"
        await ctx.emit("agent_log", {"agent": "INCIDENT COMMANDER", "type": "step",
            "msg": f"dispatch → {spec.label}: {task[:300]}"})
        first_new = len(ctx.ledger)
        try:
            run = await run_agent(
                name=spec.label, model=spec.model, tools=spec.tools, final_tool=REPORT_FINDINGS,
                ledger=ctx.ledger, emit=ctx.emit, max_rounds=SPECIALIST_MAX_ROUNDS, stats=ctx.stats,
                system=specialist_system_prompt(spec, SPECIALIST_MAX_ROUNDS),
                user=(f"{alert_text(alert)}\nCurrent time: {utc_now()}\nEvents before "
                      f"{alert.get('incident_start')} may belong to earlier, resolved incidents.\n\n"
                      f"{containers}\n\nTask from the Incident Commander:\n{task}"),
            )
        except (AgentError, LLMUnavailable) as exc:
            await ctx.emit("agent_log", {"agent": spec.label, "type": "error", "msg": f"failed: {exc}"})
            # Salvage what it observed: raw tool output is still citable evidence.
            raw = [e for e in list(ctx.ledger)[first_new:] if e.agent == spec.label]
            return (f"{spec.label} could not finish the task ({exc}). Raw observations it made "
                    "(unsummarised; quote from these verbatim):\n"
                    + ("\n".join(f"[{e.id}] {e.tool}\n{e.output[:500]}" for e in raw) or "(none)"))
        return await _verified_findings(spec.label, run.final_args or {}, ctx)

    return Tool(
        "dispatch_specialist",
        "Send a specialist agent to investigate a specific question. Returns its findings with evidence ids.",
        params({"specialist": {"type": "string", "enum": list(SPECIALISTS)},
                "task": {"type": "string", "description": "precise question to investigate"}},
               ["specialist", "task"]),
        dispatch_specialist, records_evidence=False)


async def commander(state: InvestigationState, runtime: Runtime[RunContext]) -> dict:
    ctx = runtime.context
    resuming = bool(state.get("commander_messages"))
    await ctx.stage("investigation",
                    f"Investigation — revision {state.get('revisions', 0)}" if resuming else "Investigation",
                    grouped=True)
    await ctx.emit("agent_log", {"agent": "INCIDENT COMMANDER", "type": "start",
        "msg": "Revising the diagnosis based on feedback…" if resuming
               else "Analysing alert and triage evidence; planning the investigation…"})

    user = (f"{alert_text(state['alert'])}\nCurrent time: {utc_now()}\n\n"
            f"Triage evidence:\n{state['triage']}\n\nInvestigate and submit your diagnosis.")
    try:
        run = await run_agent(
            name="INCIDENT COMMANDER", model=COMMANDER_MODEL,
            tools=MEMORY_TOOLSET + [_dispatch_tool(state["alert"], ctx)], final_tool=SUBMIT_DIAGNOSIS,
            ledger=ctx.ledger, emit=ctx.emit, stats=ctx.stats,
            max_rounds=_REVISION_ROUNDS if resuming else COMMANDER_MAX_ROUNDS, max_tokens=1600,
            system=commander_system(), user=user, messages=state.get("commander_messages"),
        )
    except (AgentError, LLMUnavailable) as exc:
        logger.warning(f"commander failed: {exc}")
        if resuming and state.get("diagnosis"):
            # Keep the earlier diagnosis (its evidence was verified); the
            # reviewer's open concerns travel with it into the report.
            await ctx.emit("agent_log", {"agent": "INCIDENT COMMANDER", "type": "error",
                "msg": f"Revision failed ({exc}) — keeping the previous diagnosis; open concerns will be reported"})
            return {"revisions": MAX_DIAGNOSIS_REVISIONS}
        await ctx.emit("agent_log", {"agent": "INCIDENT COMMANDER", "type": "error",
            "msg": f"Investigation could not complete: {exc}"})
        return {"diagnosis": None, "error": str(exc)}

    diag = run.final_args or {}
    await ctx.emit("agent_log", {"agent": "INCIDENT COMMANDER", "type": "result",
        "msg": f"Diagnosis submitted: {diag.get('root_cause', '?')} "
               f"[{diag.get('failure_category', '?')}, {diag.get('severity', '?')}, "
               f"self-assessed confidence {diag.get('confidence', '?')}]"})
    return {"diagnosis": diag, "commander_messages": run.messages, "error": None}
