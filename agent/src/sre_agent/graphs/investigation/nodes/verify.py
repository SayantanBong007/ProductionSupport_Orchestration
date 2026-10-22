"""Evidence verifier — every quote in the diagnosis must appear in the recorded tool output (no LLM)."""

from __future__ import annotations

from langgraph.runtime import Runtime

from sre_agent.config import MIN_VERIFIED_EVIDENCE_RATIO
from sre_agent.core.context import RunContext
from sre_agent.core.evidence import verify_citations
from sre_agent.graphs.investigation.state import InvestigationState


async def verify(state: InvestigationState, runtime: Runtime[RunContext]) -> dict:
    emit, diag = runtime.context.emit, state.get("diagnosis")
    if not diag:
        return {"verification": None}
    await runtime.context.stage("verify", "Evidence check")
    checked = verify_citations(diag.get("evidence", []), runtime.context.ledger)
    ok = sum(c["verified"] for c in checked)
    ratio = ok / len(checked) if checked else 0.0
    for c in checked:
        await emit("agent_log", {"agent": "EVIDENCE VERIFIER", "type": "result" if c["verified"] else "error",
            "msg": f"{'✓' if c['verified'] else '✗'} {c['id']}: \"{str(c.get('quote', ''))[:120]}\" — {c['reason']}"})
    passed = bool(checked) and ratio >= MIN_VERIFIED_EVIDENCE_RATIO
    await emit("agent_log", {"agent": "EVIDENCE VERIFIER", "type": "result",
        "msg": f"{ok}/{len(checked)} citations verified against recorded tool output — "
               + ("diagnosis accepted" if passed else "diagnosis NOT sufficiently supported")})
    return {"verification": {"citations": checked, "verified": ok, "total": len(checked),
                             "ratio": ratio, "passed": passed}}
