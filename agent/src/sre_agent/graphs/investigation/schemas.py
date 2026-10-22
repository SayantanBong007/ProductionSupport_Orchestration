"""Structured outputs of the investigation agents, declared as final tools.

Each agent ends by calling its tool; the arguments are the agent's answer.
"""

from __future__ import annotations

from sre_agent.tools import Tool
from sre_agent.tools.base import params

FAILURE_CATEGORIES = [
    "dependency_unavailable", "service_unavailable", "resource_exhaustion",
    "authentication_failure", "network_failure", "configuration_error",
    "application_error", "unknown",
]

SUBMIT_DIAGNOSIS = Tool(
    "submit_diagnosis",
    "Submit the final diagnosis. Every evidence quote is machine-checked against the recorded tool output.",
    params({
        "summary":   {"type": "string", "description": "3-4 sentences for the on-call engineer: what happened, why, impact"},
        "root_cause": {"type": "string", "description": "one sentence: the underlying cause, not the symptom"},
        "failure_category": {"type": "string", "enum": FAILURE_CATEGORIES},
        "affected_components": {"type": "array", "items": {"type": "string"}},
        "severity": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
        "timeline": {"type": "array", "items": {"type": "object", "properties": {
            "time": {"type": "string"}, "event": {"type": "string"}, "evidence_id": {"type": "string"}},
            "required": ["time", "event"]}},
        "evidence": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
            "id": {"type": "string", "description": "evidence id, e.g. E4"},
            "quote": {"type": "string", "description": "exact substring copied from that evidence output"},
            "supports": {"type": "string", "description": "what this evidence proves"}},
            "required": ["id", "quote", "supports"]}},
        "confidence": {"type": "number", "description": "0-1, how certain you are of the root cause"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    }, ["summary", "root_cause", "failure_category", "affected_components", "severity",
        "evidence", "confidence"]),
    fn=None, records_evidence=False)

REVIEW_DIAGNOSIS = Tool(
    "review_diagnosis",
    "Submit your review of the diagnosis.",
    params({
        "verdict": {"type": "string", "enum": ["accept", "challenge"]},
        "unsupported_claims": {"type": "array", "items": {"type": "string"},
                               "description": "causal claims in the diagnosis that the evidence does not establish"},
        "untested_alternatives": {"type": "array", "items": {"type": "string"},
                                  "description": "plausible explanations the evidence does not rule out"},
        "suggested_checks": {"type": "array", "items": {"type": "string"},
                             "description": "concrete checks (logs, metrics, container state) that would decide it"},
    }, ["verdict"]),
    fn=None, records_evidence=False)


def propose_remediation_tool(runbook_ids: list[str]) -> Tool:
    return Tool(
        "propose_remediation",
        "Propose one runbook from the approved catalog, or 'none' if no runbook addresses the root cause.",
        params({
            "runbook_id": {"type": "string", "enum": runbook_ids + ["none"]},
            "target": {"type": "string", "description": "container to act on; must be in the runbook's allowed_targets"},
            "rationale": {"type": "string", "description": "why this runbook fixes the diagnosed root cause"},
            "expected_outcome": {"type": "string"},
        }, ["runbook_id", "rationale"]),
        fn=None, records_evidence=False)
