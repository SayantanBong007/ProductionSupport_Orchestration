"""
Runtime context shared by the graphs.

These are per-run dependencies, injected through LangGraph's runtime context
(``context_schema`` / ``Runtime``) rather than stored in graph state, so the
state stays plain, serialisable data. Every field has a default, so the graphs
also run standalone (LangGraph Studio, tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sre_agent.core.evidence import EvidenceLedger
from sre_agent.core.llm import Emit


async def _discard(event: str, data: dict) -> None:
    pass


@dataclass
class RunContext:
    emit: Emit = _discard                                           # streams progress events to the UI
    ledger: EvidenceLedger = field(default_factory=EvidenceLedger)  # every observation the agents make
    stats: dict = field(default_factory=dict)                       # LLM calls / tokens per agent

    async def stage(self, key: str, title: str, grouped: bool = False):
        """Open a new section of the live trace. ``grouped`` sections show one
        block per agent, so agents working in parallel do not interleave."""
        await self.emit("stage", {"stage": key, "title": title, "grouped": grouped})
