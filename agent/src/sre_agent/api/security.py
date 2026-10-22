"""API-key guard for state-changing endpoints."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from sre_agent.config import AGENT_API_KEY


def require_api_key(x_api_key: str | None = Header(default=None)):
    """Every /action/* endpoint stops containers or changes DB state — never callable anonymously."""
    if not AGENT_API_KEY:
        raise HTTPException(503, "AGENT_API_KEY is not configured — actions are disabled")
    if not secrets.compare_digest(x_api_key or "", AGENT_API_KEY):
        raise HTTPException(401, "Invalid or missing X-API-Key")
