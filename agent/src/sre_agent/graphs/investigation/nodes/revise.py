"""Revise — hand the verifier's or reviewer's feedback back to the commander (no LLM)."""

from __future__ import annotations

from sre_agent.graphs.investigation.state import InvestigationState


def _bullets(items: list[str] | None) -> str:
    return "\n".join(f"- {x}" for x in items or []) or "- (none)"


async def revise(state: InvestigationState) -> dict:
    verification, review = state["verification"], state.get("review") or {}
    if not verification["passed"]:
        failed = [c for c in verification["citations"] if not c["verified"]]
        feedback = ("The evidence verifier rejected your diagnosis. These citations could not be "
                    "verified:\n" + "\n".join(f"- {c['id']}: \"{c.get('quote', '')}\" — {c['reason']}" for c in failed)
                    + "\nQuotes must be exact substrings of the cited evidence output. Re-check the "
                      "evidence (dispatch specialists again if needed) and resubmit.")
    else:
        feedback = ("The Reviewer challenged your diagnosis.\n"
                    f"Unsupported claims:\n{_bullets(review.get('unsupported_claims'))}\n"
                    f"Alternatives not ruled out:\n{_bullets(review.get('untested_alternatives'))}\n"
                    f"Suggested checks:\n{_bullets(review.get('suggested_checks'))}\n"
                    "Run the checks that can decide this (dispatch specialists), then resubmit. If the "
                    "evidence cannot decide it, say so in the root cause and open_questions and lower "
                    "your confidence.")
    messages = list(state["commander_messages"]) + [{"role": "user", "content": feedback}]
    return {"commander_messages": messages, "revisions": state.get("revisions", 0) + 1, "review": None}
