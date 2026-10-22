"""Only one incident run is active: reset or a new alert cancels the previous run."""

import asyncio

from sre_agent import incidents
from sre_agent.api.events import EventBus
from sre_agent.incidents import IncidentManager

ALERT = {"signals": ["container poc-backend is exited"]}


def _manager(published: list) -> IncidentManager:
    bus = EventBus()

    async def publish(event, data):
        published.append((event, data))
    bus.publish = publish
    return IncidentManager(bus, quiet_after_fix_seconds=10)


def _slow_investigation(name: str, finished: list):
    async def run(emit, alert):
        await emit("agent_log", {"msg": f"{name} started"})
        await asyncio.sleep(0.05)
        await emit("agent_log", {"msg": f"{name} late event"})
        finished.append(name)
        return {"remediation": None}
    return run


def test_reset_cancels_the_running_investigation(monkeypatch):
    published, finished = [], []
    monkeypatch.setattr(incidents, "run_investigation", _slow_investigation("A", finished))

    async def scenario():
        m = _manager(published)
        await m.start_investigation(ALERT, auto=True)
        await asyncio.sleep(0)            # let the run start
        await m.reset()
        await asyncio.sleep(0.1)
        return m

    m = asyncio.run(scenario())
    assert finished == []                                     # cancelled, never completed
    assert not any(d.get("msg") == "A late event" for _, d in published)
    assert m.phase == "idle"


def test_new_investigation_replaces_the_previous_one(monkeypatch):
    published, finished = [], []
    runs = iter([_slow_investigation("A", finished), _slow_investigation("B", finished)])
    monkeypatch.setattr(incidents, "run_investigation", lambda emit, alert: next(runs)(emit, alert))

    async def scenario():
        m = _manager(published)
        await m.start_investigation(ALERT, auto=True)
        await asyncio.sleep(0)
        await m.start_investigation(ALERT, auto=True)
        await asyncio.sleep(0.1)
        return m

    m = asyncio.run(scenario())
    assert finished == ["B"]
    run_ids = {d["run_id"] for e, d in published if e == "agent_log"}
    assert run_ids == {1, 2} and m.run_id == 2
    assert not any(d.get("msg") == "A late event" for _, d in published)
    assert m.phase == "manual_required"
