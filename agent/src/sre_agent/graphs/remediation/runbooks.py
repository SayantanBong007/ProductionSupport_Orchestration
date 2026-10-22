"""Loads and validates the approved runbook catalog (runbooks.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from string import Template

import yaml

from sre_agent import config
from sre_agent.graphs.remediation.actions import ACTIONS


@dataclass
class Runbook:
    id: str
    title: str
    when_to_use: str
    risk: str
    allowed_targets: list[str]
    steps: list[dict] = field(default_factory=list)

    def rendered_steps(self, target: str) -> list[dict]:
        return [{**s, "target": s.get("target") or target} for s in self.steps]


def _substitute(value):
    if isinstance(value, str):
        return Template(value).safe_substitute(vars(config))
    if isinstance(value, list):
        return [_substitute(v) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v) for k, v in value.items()}
    return value


def load_runbooks(path: str = config.RUNBOOKS_PATH) -> dict[str, Runbook]:
    with open(path, encoding="utf-8") as f:
        raw = _substitute(yaml.safe_load(f) or {})
    books: dict[str, Runbook] = {}
    for rb in raw.get("runbooks", []):
        unknown = {s.get("action") for s in rb.get("steps", [])} - set(ACTIONS)
        if unknown:
            raise ValueError(f"runbook {rb.get('id')}: unsupported actions {sorted(unknown)}")
        books[rb["id"]] = Runbook(
            id=rb["id"], title=rb["title"], when_to_use=" ".join(rb["when_to_use"].split()),
            risk=rb.get("risk", ""), allowed_targets=list(rb.get("allowed_targets", [])),
            steps=list(rb.get("steps", [])))
    return books


def validate_proposal(proposal: dict, books: dict[str, Runbook]) -> str | None:
    """Return an error message if the proposal is not allowed, else None."""
    rid = proposal.get("runbook_id")
    if rid in (None, "", "none"):
        return None
    if rid not in books:
        return f"unknown runbook '{rid}'. Valid ids: {', '.join(books)} or 'none'"
    target = proposal.get("target", "")
    if target not in books[rid].allowed_targets:
        return f"target '{target}' is not allowed for {rid}; allowed: {', '.join(books[rid].allowed_targets)}"
    return None


def catalog_text(books: dict[str, Runbook]) -> str:
    return "\n".join(
        f"- id: {b.id}\n  title: {b.title}\n  when_to_use: {b.when_to_use}\n"
        f"  risk: {b.risk}\n  allowed_targets: {', '.join(b.allowed_targets)}\n"
        f"  steps: {' → '.join(s['action'] + (' ' + s['target'] if s.get('target') else '') for s in b.steps)}"
        for b in books.values())
