"""Graph state for remediation — plain, serialisable data only."""

from __future__ import annotations

from typing import Optional

from typing_extensions import TypedDict


class RemediationState(TypedDict, total=False):
    report:       dict            # input: the approved investigation report
    proposal:     dict            # input: the approved runbook proposal
    incident_id:  Optional[str]
    steps:        list            # executed step results
    verification: Optional[dict]  # measured recovery
    closure:      Optional[str]   # closure note
