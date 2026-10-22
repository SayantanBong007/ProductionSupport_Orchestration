"""
Continuous watcher — generic alert rules, evaluated every WATCH_INTERVAL_SECONDS.

Rules: critical containers running · health endpoint OK · synthetic-client
error ratio below threshold. A rule must keep failing for
ALERT_CONSECUTIVE_CHECKS checks (an alert rule's `for:` clause) before it
raises an alert. The watcher never knows what fault, if any, was injected.
"""

from __future__ import annotations

import asyncio
import logging

from sre_agent.config import (
    ALERT_CLIENT_ERROR_RATIO, ALERT_CONSECUTIVE_CHECKS, BACKEND_URL, CRITICAL_CONTAINERS, WATCH_INTERVAL_SECONDS,
)
from sre_agent.incidents import IncidentManager
from sre_agent.integrations.docker import project_containers
from sre_agent.tools.base import utc_now
from sre_agent.tools.observability import client_error_stats, probe_health

logger = logging.getLogger(__name__)

# Window of the synthetic-client error-ratio rule: short, so a recovered
# service stops alerting quickly; long enough for several Prometheus scrapes.
CLIENT_WINDOW_SECONDS = 30


async def evaluate() -> tuple[list[str], dict]:
    """Evaluate the alert rules once. Returns (failing signals, status snapshot for the UI)."""
    health, containers, client = await asyncio.gather(
        probe_health(), project_containers(), client_error_stats(f"{CLIENT_WINDOW_SECONDS}s"),
        return_exceptions=True)

    signals: list[str] = []
    states = {c["name"]: c["state"] for c in containers} if isinstance(containers, list) else {}
    if states:
        for name in CRITICAL_CONTAINERS:
            state = states.get(name, "missing")
            if state != "running":
                signals.append(f"container {name} is {state}")

    if isinstance(health, dict) and not health["ok"]:
        detail = (f"HTTP {health['status_code']}: {health['text'][:160]}" if health["status_code"]
                  else str(health["error"])[:160])
        signals.append(f"{BACKEND_URL}/health failing — {detail}")

    if isinstance(client, dict) and client["total"] >= 5 and client["ratio"] > ALERT_CLIENT_ERROR_RATIO:
        signals.append(f"synthetic client: {client['failed']}/{client['total']} requests failed in the "
                       f"last {CLIENT_WINDOW_SECONDS}s ({client['ratio']:.0%}, "
                       f"threshold {ALERT_CLIENT_ERROR_RATIO:.0%})")

    if isinstance(health, dict):
        backend = "healthy" if health["ok"] else ("unhealthy" if health["status_code"] else "unreachable")
        body = health["body"] if isinstance(health["body"], dict) else {}
        database = body.get("database", "unknown")
    else:
        backend, database = "unknown", "unknown"
    status = {"backend": backend, "database": database,
              "client_error_pct": round(client["ratio"] * 100) if isinstance(client, dict) else None,
              "containers": states}
    return signals, status


class Watcher:
    def __init__(self, incidents: IncidentManager):
        self.incidents = incidents
        self.failing_since: str | None = None   # first failing check of the current streak
        self._consecutive = 0

    async def run(self):
        await self._wait_for_backend()
        # Let the error-ratio window fill with post-start traffic, so failures
        # from before this process started cannot raise an alert.
        await asyncio.sleep(CLIENT_WINDOW_SECONDS + 5)
        logger.info(f"Continuous watcher active — every {WATCH_INTERVAL_SECONDS:g}s")
        await self.incidents.emit("watcher_active", {
            "message": f"Continuous watcher online — checks every {WATCH_INTERVAL_SECONDS:g}s"})
        while True:
            try:
                await self._tick()
            except Exception as exc:
                logger.warning(f"Watcher error: {exc}")
            await asyncio.sleep(WATCH_INTERVAL_SECONDS)

    async def _tick(self):
        signals, status = await evaluate()
        await self.incidents.emit("status", status)
        if signals:
            self._consecutive += 1
            self.failing_since = self.failing_since or utc_now()
        else:
            self._consecutive, self.failing_since = 0, None

        if signals and self._consecutive >= ALERT_CONSECUTIVE_CHECKS and self.incidents.accepts_alerts():
            logger.warning(f"Alert: {signals}")
            await self.incidents.start_investigation(
                {"signals": signals, "detected_at": utc_now(), "incident_start": self.failing_since}, auto=True)

    @staticmethod
    async def _wait_for_backend(timeout: float = 180):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline and not (await probe_health())["ok"]:
            await asyncio.sleep(5)
