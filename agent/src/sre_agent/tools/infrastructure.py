"""
Infrastructure tools — container state, exit codes, Docker events and
container stdout/stderr, scoped to this compose project's containers.
"""

from __future__ import annotations

import time

from sre_agent.integrations.docker import container_inspect, container_logs as _container_logs, docker_events as _docker_events
from sre_agent.integrations.docker import project_containers
from sre_agent.tools.base import Tool, clip, params, utc


async def _known_names() -> list[str]:
    return [c["name"] for c in await project_containers()]


async def _check_name(name: str) -> str | None:
    names = await _known_names()
    if name not in names:
        return f"unknown container '{name}'. Containers in this project: {', '.join(sorted(names))}"
    return None


async def list_containers() -> str:
    rows = await project_containers()
    lines = [f"{c['name']}: {c['state']} ({c['status']}) image={c['image']}"
             for c in sorted(rows, key=lambda c: c["name"])]
    return f"{len(rows)} containers in this deployment:\n" + "\n".join(lines)


async def inspect_container(name: str) -> str:
    if err := await _check_name(name):
        return err
    info = await container_inspect(name)
    if not info:
        return f"container '{name}' not found"
    st = info.get("State", {})
    health = st.get("Health") or {}
    lines = [
        f"container {name}",
        f"status={st.get('Status')} running={st.get('Running')} restarting={st.get('Restarting')}",
        f"exit_code={st.get('ExitCode')} oom_killed={st.get('OOMKilled')} error='{st.get('Error', '')}'",
        f"started_at={st.get('StartedAt')} finished_at={st.get('FinishedAt')}",
        f"restart_count={info.get('RestartCount')} "
        f"restart_policy={info.get('HostConfig', {}).get('RestartPolicy', {}).get('Name')}",
    ]
    if health:
        lines.append(f"healthcheck status={health.get('Status')} failing_streak={health.get('FailingStreak')}")
        for h in (health.get("Log") or [])[-2:]:
            lines.append(f"  check at {h.get('End', '')[:19]}Z exit={h.get('ExitCode')} "
                         f"output={str(h.get('Output', '')).strip()[:160]}")
    return "\n".join(lines)


async def container_logs(name: str, tail: int = 40, minutes: int = 0) -> str:
    if err := await _check_name(name):
        return err
    tail = max(1, min(int(tail), 200))
    since = int(time.time() - int(minutes) * 60) if minutes else None
    try:
        raw = await _container_logs(name, tail=tail, since_unix=since)
    except Exception as exc:
        return f"docker logs failed: {exc}"
    lines = [ln[:300] for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return f"docker logs {name}: (no output in range)"
    # Keep the most recent lines when the output must be clipped.
    body, budget = [], 1600
    for ln in reversed(lines):
        budget -= len(ln) + 1
        if budget < 0:
            break
        body.append(ln)
    return clip(f"docker logs {name} (last {len(body)} of {len(lines)} lines):\n" + "\n".join(reversed(body)))


async def docker_events(minutes: int = 15) -> str:
    minutes = max(1, min(int(minutes), 24 * 60))
    now = int(time.time())
    names = await _known_names()
    try:
        events = await _docker_events(now - minutes * 60, now, containers=names)
    except Exception as exc:
        return f"docker events failed: {exc}"
    noise = ("exec_create", "exec_start", "exec_die", "top", "attach", "resize")
    rows = []
    for e in events:
        action = e.get("Action") or e.get("status", "")
        if action.startswith(noise):
            continue
        attrs = e.get("Actor", {}).get("Attributes", {})
        extra = " ".join(f"{k}={attrs[k]}" for k in ("exitCode", "signal") if k in attrs)
        rows.append(f"{utc(e.get('time', 0))} {attrs.get('name', '?')} {action} {extra}".rstrip())
    if not rows:
        return f"no container lifecycle events in the last {minutes}m"
    return clip(f"container events, last {minutes}m (oldest first):\n" + "\n".join(rows[-40:]))


TOOLS = {
    "list_containers": Tool(
        "list_containers", "List every container in this deployment with its state and status.",
        params(), list_containers),
    "inspect_container": Tool(
        "inspect_container",
        "Inspect one container: status, exit code, OOM-killed flag, start/finish times, "
        "restart count and healthcheck results.",
        params({"name": {"type": "string"}}, ["name"]), inspect_container),
    "container_logs": Tool(
        "container_logs",
        "Read a container's stdout/stderr (with timestamps). Works for stopped containers too.",
        params({"name": {"type": "string"},
                "tail": {"type": "integer", "description": "max lines, default 40"},
                "minutes": {"type": "integer", "description": "only lines from the last N minutes; 0 = no limit"}},
               ["name"]), container_logs),
    "docker_events": Tool(
        "docker_events",
        "Container lifecycle events (start, stop, kill, die with exitCode, oom, restart, "
        "health_status) for this deployment over the last N minutes.",
        params({"minutes": {"type": "integer", "description": "lookback, default 15"}}), docker_events),
}
