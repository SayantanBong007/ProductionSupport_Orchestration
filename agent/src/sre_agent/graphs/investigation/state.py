"""Graph state for the investigation — plain, serialisable data only.

Runtime dependencies (event emitter, evidence ledger, LLM usage counters) live
in ``sre_agent.core.context.RunContext``, not here.
"""

from __future__ import annotations

from typing import Optional

from typing_extensions import TypedDict


class InvestigationState(TypedDict, total=False):
    alert:              dict            # input: signals, detected_at, incident_start
    triage:             str             # first snapshot, as text for the commander
    commander_messages: Optional[list]  # commander conversation, kept to resume on revision
    diagnosis:          Optional[dict]  # submit_diagnosis arguments
    verification:       Optional[dict]  # citation check results
    review:             Optional[dict]  # reviewer verdict
    revisions:          int             # send-backs so far (verifier + reviewer)
    proposal:           Optional[dict]  # remediation runbook proposal
    report:             Optional[dict]  # output: published incident report
    incident_id:        Optional[str]
    error:              Optional[str]   # why the AI investigation could not complete
