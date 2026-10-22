"""Tool definition shared by every agent tool."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sre_agent.config import MAX_TOOL_OUTPUT_CHARS


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                       # JSON schema of the arguments
    fn: Callable[..., Awaitable[str]]
    # Observations go into the evidence ledger; control tools (dispatching an
    # agent, submitting a diagnosis) do not.
    records_evidence: bool = True

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


def params(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties or {}, "required": required or []}


def clip(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated {len(text) - limit} chars]"


def utc(ts: float | datetime) -> str:
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts, tz=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now() -> str:
    return utc(datetime.now(timezone.utc))
