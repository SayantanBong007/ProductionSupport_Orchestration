"""
HTTP routes.

  GET  /  /events  /state  /health          read-only
  POST /action/*                            X-API-Key required

The fault-injection actions only break the system. They never start an
investigation or tell the agents what was injected — the watcher has to
detect it on its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from sre_agent.api.security import require_api_key
from sre_agent.config import AGENT_API_KEY, BACKEND_CONTAINER, BACKEND_URL, DATABASE_CONTAINER, FAULT_INJECTION_TOKEN
from sre_agent.incidents import IncidentManager
from sre_agent.integrations.docker import container_stop
from sre_agent.tools.base import utc_now
from sre_agent.watcher import Watcher, evaluate

logger = logging.getLogger(__name__)

router = APIRouter()
actions = APIRouter(prefix="/action", dependencies=[Depends(require_api_key)])

_UI = Path(__file__).parent / "static" / "index.html"


def _incidents(request: Request) -> IncidentManager:
    return request.app.state.incidents


def _watcher(request: Request) -> Watcher:
    return request.app.state.watcher


# ── Read-only ─────────────────────────────────────────────────────────────────

@router.get("/")
async def ui():
    # The UI's action calls need AGENT_API_KEY; it is injected from .env so it is
    # never typed or displayed. The UI port is bound to localhost (docker-compose.yml).
    html = _UI.read_text(encoding="utf-8").replace(
        "__AGENT_API_KEY__", json.dumps(AGENT_API_KEY).replace("</", "<\\/"))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/events")
async def events(request: Request):
    incidents = _incidents(request)
    q = incidents.bus.subscribe()

    async def stream():
        yield f"event: sync\ndata: {json.dumps({'phase': incidents.phase, 'scenario': incidents.scenario})}\n\n"
        try:
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            incidents.bus.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/state")
async def state(request: Request):
    return _incidents(request).snapshot()


@router.get("/health")
async def health():
    return {"status": "healthy", "service": "sre-agent"}


# ── Fault injection (operator) ────────────────────────────────────────────────

async def _stop(request: Request, container: str, scenario: str):
    incidents = _incidents(request)
    if blocked := incidents.injection_blocked():
        return blocked
    if not await container_stop(container, timeout=5):
        msg = f"Failed to stop {container} — check the Docker socket mount"
        await incidents.emit("error", {"message": msg})
        return {"ok": False, "error": msg}
    await incidents.fault_injected(scenario, f"Operator injected a fault: {container} stopped")
    return {"ok": True}


@actions.post("/break-pool")
async def break_pool(request: Request):
    """Make the backend leak every DB pool connection (real SQLAlchemy pool exhaustion)."""
    incidents = _incidents(request)
    if blocked := incidents.injection_blocked():
        return blocked
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{BACKEND_URL}/admin/exhaust-pool", headers={"X-Fault-Token": FAULT_INJECTION_TOKEN})
            r.raise_for_status()
    except Exception as exc:
        msg = f"Fault injection failed: {exc}"
        await incidents.emit("error", {"message": msg})
        return {"ok": False, "error": msg}
    await incidents.fault_injected("pool", "Operator injected a fault: backend DB connections leaked")
    return {"ok": True}


@actions.post("/break-backend")
async def break_backend(request: Request):
    return await _stop(request, BACKEND_CONTAINER, "backend")


@actions.post("/break-postgres")
async def break_postgres(request: Request):
    return await _stop(request, DATABASE_CONTAINER, "postgres")


# ── Incident control ──────────────────────────────────────────────────────────

@actions.post("/investigate")
async def investigate(request: Request):
    incidents = _incidents(request)
    if incidents.phase in ("investigating", "fixing"):
        return {"ok": False, "error": f"Busy ({incidents.phase})"}
    signals, _ = await evaluate()
    await incidents.start_investigation({
        "signals": signals or ["operator requested an investigation; no alert rule is currently failing"],
        "detected_at": utc_now(), "incident_start": _watcher(request).failing_since or utc_now(),
    }, auto=False)
    return {"ok": True}


@actions.post("/approve")
async def approve(request: Request):
    return await _incidents(request).approve()


@actions.post("/reset")
async def reset(request: Request):
    await _incidents(request).reset()
    return {"ok": True}


router.include_router(actions)
