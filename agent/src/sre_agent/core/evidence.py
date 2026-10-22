"""
Evidence ledger — every observation the agents make is recorded here.

Each tool call (Prometheus query, log search, container inspection, ...) gets
an id (E1, E2, ...) and its raw output is stored verbatim. Agents must cite
these ids, with an exact quote, for every claim in their diagnosis.
verify_citations() checks each quote against the recorded output, so a claim
the tools never returned cannot pass as verified evidence.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class Evidence:
    id: str
    agent: str
    tool: str
    args: dict
    output: str
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


class EvidenceLedger:
    def __init__(self):
        self._items: list[Evidence] = []

    def add(self, agent: str, tool: str, args: dict, output: str) -> Evidence:
        ev = Evidence(id=f"E{len(self._items) + 1}", agent=agent, tool=tool, args=args, output=output)
        self._items.append(ev)
        return ev

    def get(self, evidence_id: str) -> Evidence | None:
        evidence_id = (evidence_id or "").strip().strip("[]").upper()
        return next((e for e in self._items if e.id == evidence_id), None)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def to_list(self) -> list[dict]:
        return [asdict(e) for e in self._items]


def _normalise(text: str) -> str:
    text = text.replace("‑", "-").replace("–", "-").replace("—", "-")
    text = text.strip().strip("`'\"").lower()
    return re.sub(r"\s+", " ", text)


# Quotes shorter than this are too generic to prove anything ("error", "503").
MIN_QUOTE_CHARS = 8


def _fragments_in_order(fragments: list[str], text: str) -> bool:
    pos = 0
    for frag in fragments:
        idx = text.find(frag, pos)
        if idx < 0:
            return False
        pos = idx + len(frag)
    return True


def verify_citations(citations: list[dict], ledger: EvidenceLedger) -> list[dict]:
    """Return each citation annotated with ``verified`` and a ``reason``.

    An ellipsis ("…" or "...") marks omitted text: every fragment around it
    must then appear in the cited output, in order.
    """
    checked = []
    for c in citations or []:
        eid, quote = str(c.get("id", "")), str(c.get("quote", ""))
        ev = ledger.get(eid)
        fragments = [f for f in (_normalise(p) for p in re.split(r"…|\.\.\.", quote)) if f]
        if ev is None:
            ok, reason = False, f"evidence {eid or '(none)'} does not exist"
        elif not fragments or max(len(f) for f in fragments) < MIN_QUOTE_CHARS:
            ok, reason = False, "quote too short to be meaningful"
        elif _fragments_in_order(fragments, _normalise(ev.output)):
            ok, reason = True, f"found verbatim in {ev.id} ({ev.tool})"
        else:
            ok, reason = False, f"quote not found in {ev.id} ({ev.tool}) output"
        checked.append({**c, "id": ev.id if ev else eid, "verified": ok, "reason": reason})
    return checked
