"""
Incident lifecycle — the single owner of "what is happening right now".

    idle ─► investigating ─► awaiting_approval ─► fixing ─► resolved
      ▲            │                                  │
      │            └─► manual_required                └─► failed
      └──────────────────── reset ──────────────────────────┘

"broken" means an operator injected a fault and the watcher has not yet
raised an alert for it.

Each incident is a *run* with its own id. Only one run is active at a time:
starting a new one or resetting cancels the previous run, and events from a
cancelled run are dropped, so the UI never shows two incidents interleaved.
"""

from __future__ import annotations

import asyncio
import logging
import traceback

from sre_agent.api.events import EventBus
from sre_agent.core.llm import Emit
from sre_agent.graphs.investigation import run_investigation
from sre_agent.graphs.remediation import run_remediation

logger = logging.getLogger(__name__)

# Phases in which a new alert may start an investigation.
ALERTABLE_PHASES = ("idle", "broken", "resolved")
# Phases in which an operator may inject a fault.
INJECTABLE_PHASES = ("idle", "resolved")


class IncidentManager:
    def __init__(self, bus: EventBus, quiet_after_fix_seconds: float):
        self.bus = bus
        self.phase = "idle"
        self.report: dict | None = None
        self.alert: dict | None = None
        self.scenario: str | None = None   # last injected fault — display only, never given to the agents
        self.run_id = 0
        self._run_emit: Emit = bus.publish
        self._task: asyncio.Task | None = None
        self._quiet_after_fix = quiet_after_fix_seconds
        self._quiet_until = 0.0

    @property
    def emit(self) -> Emit:
        """Publish an event that is not tied to an incident run (status, watcher, operator)."""
        return self.bus.publish

    def snapshot(self) -> dict:
        return {"phase": self.phase, "scenario": self.scenario, "alert": self.alert,
                "run_id": self.run_id, "investigation": self.report}

    # ── Runs ─────────────────────────────────────────────────────────────────

    def _cancel_active_run(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def _new_run(self) -> Emit:
        """Cancel any active run and return the event emitter for a new one."""
        self._cancel_active_run()
        self.run_id += 1
        run_id = self.run_id

        async def emit(event: str, data: dict):
            if run_id == self.run_id:            # drop late events from a cancelled run
                await self.bus.publish(event, {**data, "run_id": run_id})

        self._run_emit = emit
        return emit

    # ── Alerts and investigation ─────────────────────────────────────────────

    def accepts_alerts(self) -> bool:
        """After a fix, the error-ratio window still holds pre-fix failures — don't re-alert on them."""
        return self.phase in ALERTABLE_PHASES and asyncio.get_running_loop().time() >= self._quiet_until

    async def start_investigation(self, alert: dict, auto: bool):
        emit = self._new_run()
        self.phase, self.report, self.alert = "investigating", None, alert
        await emit("investigation_started", {
            "auto": auto, "signals": alert["signals"],
            "message": "Alert raised — Incident Commander taking the incident…" if auto
                       else "Manual investigation requested — Incident Commander taking the incident…"})
        self._task = asyncio.create_task(self._investigate(alert, emit))

    async def _investigate(self, alert: dict, emit: Emit):
        try:
            self.report = await run_investigation(emit, alert)
            if self.report.get("remediation"):
                self.phase = "awaiting_approval"
                await emit("awaiting_approval", {
                    "message": "Investigation complete — awaiting human approval to run the proposed runbook."})
            else:
                self.phase = "manual_required"
                await emit("manual_required", {
                    "message": "Investigation complete — no approved runbook applies; manual action required."})
        except asyncio.CancelledError:
            logger.info("Investigation cancelled")
            raise
        except Exception as exc:
            logger.error(f"Investigation failed: {exc}\n{traceback.format_exc()}")
            self.phase = "failed"
            await emit("error", {"message": str(exc)})

    # ── Human approval and remediation ───────────────────────────────────────

    async def approve(self) -> dict:
        if self.phase != "awaiting_approval":
            return {"ok": False, "error": f"Not awaiting approval (phase: {self.phase})"}
        if not self.report or not self.report.get("remediation"):
            return {"ok": False, "error": "No remediation proposal to approve"}
        self.phase = "fixing"
        emit = self._run_emit                    # remediation continues the same run
        await emit("fix_started", {"message": "Human approved — executing runbook…"})
        self._task = asyncio.create_task(self._remediate(self.report, emit))
        return {"ok": True}

    async def _remediate(self, report: dict, emit: Emit):
        try:
            result = await run_remediation(report, emit)
            self.phase = "resolved" if result.get("success") else "failed"
            if result.get("success"):
                self._quiet_until = asyncio.get_running_loop().time() + self._quiet_after_fix
        except asyncio.CancelledError:
            logger.info("Remediation cancelled")
            raise
        except Exception as exc:
            logger.error(f"Remediation failed: {exc}\n{traceback.format_exc()}")
            self.phase = "failed"
            await emit("error", {"message": str(exc)})

    # ── Operator actions ─────────────────────────────────────────────────────

    def injection_blocked(self) -> dict | None:
        if self.phase not in INJECTABLE_PHASES:
            return {"ok": False, "error": f"An incident is in progress (phase: {self.phase}) — reset first"}
        return None

    async def fault_injected(self, scenario: str, message: str):
        self.phase, self.scenario, self.report = "broken", scenario, None
        await self.emit("broken", {"message": message, "scenario": scenario})

    async def reset(self):
        """Cancel whatever is running and return to idle."""
        self._cancel_active_run()
        self.run_id += 1                          # late events from the cancelled run are dropped
        self.phase, self.report, self.scenario, self.alert = "idle", None, None, None
        await self.emit("reset", {"message": "Ready"})
