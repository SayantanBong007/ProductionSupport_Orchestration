"""FastAPI application: wires the event bus, incident manager and watcher together.

Run with:  uvicorn sre_agent.api.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sre_agent.api.events import EventBus
from sre_agent.api.routes import router
from sre_agent.bootstrap import bootstrap_tooling
from sre_agent.incidents import IncidentManager
from sre_agent.watcher import CLIENT_WINDOW_SECONDS, Watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app() -> FastAPI:
    bus = EventBus()
    incidents = IncidentManager(bus, quiet_after_fix_seconds=CLIENT_WINDOW_SECONDS + 15)
    bus.extra = lambda: {"phase": incidents.phase}
    watcher = Watcher(incidents)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = [asyncio.create_task(watcher.run()), asyncio.create_task(bootstrap_tooling())]
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="SRE Agent", lifespan=lifespan)
    app.state.incidents = incidents
    app.state.watcher = watcher
    app.include_router(router)
    return app


app = create_app()
