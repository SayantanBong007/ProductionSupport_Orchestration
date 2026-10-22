"""Remediation Planner — choose a runbook from the approved catalog, or none."""

from __future__ import annotations

from langgraph.runtime import Runtime

from sre_agent.agents import AgentError, run_agent
from sre_agent.config import COMMANDER_MODEL
from sre_agent.core.context import RunContext
from sre_agent.core.llm import LLMUnavailable
from sre_agent.graphs.investigation.prompts import planner_system
from sre_agent.graphs.investigation.schemas import propose_remediation_tool
from sre_agent.graphs.investigation.state import InvestigationState
from sre_agent.graphs.remediation.runbooks import catalog_text, load_runbooks, validate_proposal


async def planner(state: InvestigationState, runtime: Runtime[RunContext]) -> dict:
    ctx, diag, verification = runtime.context, state["diagnosis"], state["verification"]
    await ctx.stage("plan", "Remediation plan")
    books = load_runbooks()
    tool = propose_remediation_tool(list(books))
    verified = [c for c in verification["citations"] if c["verified"]]
    await ctx.emit("agent_log", {"agent": "REMEDIATION PLANNER", "type": "start",
        "msg": f"Selecting a remediation from {len(books)} approved runbooks…"})
    user = (f"Diagnosis\nroot cause: {diag.get('root_cause')}\ncategory: {diag.get('failure_category')}\n"
            f"affected components: {', '.join(diag.get('affected_components', []))}\n"
            f"summary: {diag.get('summary')}\nverified evidence:\n"
            + "\n".join(f"- {c['id']}: {c.get('quote')} ({c.get('supports')})" for c in verified)
            + f"\nverified {verification['verified']}/{verification['total']} citations")

    # One retry if the proposal is not allowed by the catalog.
    messages, proposal = None, None
    for _ in range(2):
        try:
            run = await run_agent(name="REMEDIATION PLANNER", model=COMMANDER_MODEL, tools=[], final_tool=tool,
                                  ledger=ctx.ledger, emit=ctx.emit, stats=ctx.stats, max_rounds=2,
                                  system=planner_system(catalog_text(books)), user=user,
                                  messages=messages, max_tokens=800)
        except (AgentError, LLMUnavailable) as exc:
            await ctx.emit("agent_log", {"agent": "REMEDIATION PLANNER", "type": "error", "msg": f"failed: {exc}"})
            return {"proposal": None}
        error = validate_proposal(run.final_args or {}, books)
        if not error:
            proposal = run.final_args
            break
        await ctx.emit("agent_log", {"agent": "REMEDIATION PLANNER", "type": "error", "msg": f"rejected: {error}"})
        messages = run.messages + [{"role": "user", "content": f"Invalid proposal: {error}. Choose again."}]

    if not proposal or proposal.get("runbook_id") in (None, "", "none"):
        await ctx.emit("agent_log", {"agent": "REMEDIATION PLANNER", "type": "result",
            "msg": "No approved runbook fits — manual remediation required"
                   + (f": {proposal.get('rationale')}" if proposal else "")})
        return {"proposal": None}

    rb = books[proposal["runbook_id"]]
    proposal = {**proposal, "title": rb.title, "risk": rb.risk, "steps": rb.rendered_steps(proposal["target"])}
    await ctx.emit("agent_log", {"agent": "REMEDIATION PLANNER", "type": "result",
        "msg": f"Proposed: {rb.title} → {proposal['target']} — {proposal.get('rationale', '')}"})
    return {"proposal": proposal}
