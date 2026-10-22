"""
Investigation graph — wiring and routing.

    triage ─► commander ─► verify ─► review ─► planner ─► report ─► store
                  ▲           │         │
                  └─ revise ◄─┴─────────┘   (quotes not found / reasoning challenged)

  triage     snapshot of the deployment, recorded as evidence
  commander  Incident Commander (LLM) dispatches the logs / metrics / infrastructure
             specialists and submits a diagnosis citing evidence
  verify     every quote must appear in the recorded tool output
  review     Reviewer (LLM): does the root cause follow from the evidence?
  revise     either check can send the diagnosis back (MAX_DIAGNOSIS_REVISIONS)
  planner    Remediation Planner (LLM) picks an approved runbook, or none
  report     measures user impact and publishes the report for human approval
  store      archives the record and indexes it in incident memory

Nothing in the graph decides the root cause. If the LLM is unavailable the
report says so and proposes no fix.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from sre_agent.config import MAX_DIAGNOSIS_REVISIONS
from sre_agent.core.context import RunContext
from sre_agent.graphs.investigation import nodes
from sre_agent.graphs.investigation.state import InvestigationState


def _can_revise(state: InvestigationState) -> bool:
    return state.get("revisions", 0) < MAX_DIAGNOSIS_REVISIONS


def route_after_verify(state: InvestigationState) -> str:
    verification = state.get("verification")
    if verification is None:                      # no diagnosis at all
        return "report"
    if not verification["passed"]:
        return "revise" if _can_revise(state) else "planner"
    return "review"


def route_after_review(state: InvestigationState) -> str:
    review = state.get("review") or {}
    if review.get("verdict") == "challenge" and _can_revise(state):
        return "revise"
    return "planner"


def build_graph():
    g = StateGraph(InvestigationState, context_schema=RunContext)
    g.add_node("triage", nodes.triage)
    g.add_node("commander", nodes.commander)
    g.add_node("verify", nodes.verify)
    g.add_node("review", nodes.review)
    g.add_node("revise", nodes.revise)
    g.add_node("planner", nodes.planner)
    g.add_node("report", nodes.report)
    g.add_node("store", nodes.store)

    g.add_edge(START, "triage")
    g.add_edge("triage", "commander")
    g.add_edge("commander", "verify")
    g.add_conditional_edges("verify", route_after_verify, ["revise", "review", "planner", "report"])
    g.add_conditional_edges("review", route_after_review, ["revise", "planner"])
    g.add_edge("revise", "commander")
    g.add_edge("planner", "report")
    g.add_edge("report", "store")
    g.add_edge("store", END)
    return g.compile(name="investigation")


graph = build_graph()
