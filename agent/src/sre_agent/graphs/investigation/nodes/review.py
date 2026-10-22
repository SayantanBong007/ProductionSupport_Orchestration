"""
Reviewer — a second, skeptical agent.

The verifier only proves the quotes are real; the reviewer checks the
reasoning: does the root cause follow from the evidence, and which
alternatives were not ruled out?
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from sre_agent.agents import SPECIALISTS, AgentError, run_agent
from sre_agent.config import SPECIALIST_MODEL
from sre_agent.core.context import RunContext
from sre_agent.core.llm import LLMUnavailable
from sre_agent.graphs.investigation.prompts import REVIEWER_SYSTEM
from sre_agent.graphs.investigation.schemas import REVIEW_DIAGNOSIS
from sre_agent.graphs.investigation.state import InvestigationState


async def review(state: InvestigationState, runtime: Runtime[RunContext]) -> dict:
    ctx, diag = runtime.context, state["diagnosis"]
    await ctx.stage("review", "Review")
    await ctx.emit("agent_log", {"agent": "REVIEWER", "type": "start",
        "msg": "Challenging the diagnosis: does the root cause follow from the evidence?"})
    verified = [c for c in state["verification"]["citations"] if c["verified"]]
    deployment = ctx.ledger.get("E1").output if ctx.ledger.get("E1") else "(unknown)"
    team = "\n".join(f"- {s.label}: {s.domain}" for s in SPECIALISTS.values())
    user = (f"Deployment (Docker Compose):\n{deployment}\n\nThe team's specialists and their tools:\n{team}\n\n"
            f"Incident start (first failing check): {state['alert'].get('incident_start')}\n"
            f"Alert: {'; '.join(state['alert'].get('signals', []))}\n\n"
            f"Diagnosis\nroot cause: {diag.get('root_cause')}\nsummary: {diag.get('summary')}\n"
            f"timeline: {diag.get('timeline')}\n\nVerified evidence:\n"
            + "\n".join(f"- [{c['id']}] \"{c.get('quote')}\" — {c.get('supports')}" for c in verified))
    try:
        run = await run_agent(name="REVIEWER", model=SPECIALIST_MODEL, tools=[], final_tool=REVIEW_DIAGNOSIS,
                              ledger=ctx.ledger, emit=ctx.emit, stats=ctx.stats, max_rounds=1,
                              system=REVIEWER_SYSTEM, user=user, max_tokens=900)
        result = run.final_args or {"verdict": "accept"}
    except (AgentError, LLMUnavailable) as exc:
        await ctx.emit("agent_log", {"agent": "REVIEWER", "type": "error", "msg": f"review unavailable: {exc}"})
        return {"review": None}

    concerns = (result.get("unsupported_claims") or []) + (result.get("untested_alternatives") or [])
    await ctx.emit("agent_log", {"agent": "REVIEWER", "type": "result" if result["verdict"] == "accept" else "error",
        "msg": ("Accepted" if result["verdict"] == "accept" else "Challenged")
               + (": " + " | ".join(concerns)[:600] if concerns else "")})
    return {"review": result}
