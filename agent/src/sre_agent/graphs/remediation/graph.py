"""
Remediation graph — runs a human-approved runbook and verifies the outcome.

    execute ─► verify ─► closure

  execute  runs the runbook's steps (runbooks.yaml) through the fixed action
           vocabulary in actions.py; stops at the first failing step
  verify   measures recovery independently: health endpoint, container
           states, and real synthetic-client traffic after the fix
  closure  closure note from the recorded facts; outcome written back to
           incident memory so future investigations can learn from it
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from sre_agent.core.context import RunContext
from sre_agent.graphs.remediation import nodes
from sre_agent.graphs.remediation.state import RemediationState


def build_graph():
    g = StateGraph(RemediationState, context_schema=RunContext)
    g.add_node("execute", nodes.execute)
    g.add_node("verify", nodes.verify)
    g.add_node("closure", nodes.closure)
    g.add_edge(START, "execute")
    g.add_edge("execute", "verify")
    g.add_edge("verify", "closure")
    g.add_edge("closure", END)
    return g.compile(name="remediation")


graph = build_graph()
