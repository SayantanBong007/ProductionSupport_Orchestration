"""
Runbook action vocabulary — the only things an approved runbook can do.

Each action returns (ok, detail) describing what was actually observed.
"""

from __future__ import annotations

import asyncio
import time

from sre_agent.config import APP_DB_NAME, APP_DB_PASSWORD, APP_DB_USER, POSTGRES_ADMIN_URL
from sre_agent.integrations.docker import container_restart, container_start, container_status
from sre_agent.tools.observability import probe_health


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _poll(check, timeout: float, interval: float = 2.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        ok, last = await check()
        if ok:
            return True, last
        await asyncio.sleep(interval)
    return False, last


async def docker_start(target: str, **_) -> tuple[bool, str]:
    ok = await container_start(target)
    return ok, f"start {target}: {'accepted' if ok else 'failed'} (now {await container_status(target)})"


async def docker_restart(target: str, **_) -> tuple[bool, str]:
    ok = await container_restart(target, timeout=20)
    return ok, f"restart {target}: {'done' if ok else 'failed'} (now {await container_status(target)})"


async def wait_container_running(target: str, timeout: float = 30, **_) -> tuple[bool, str]:
    async def check():
        s = await container_status(target)
        return s == "running", s
    ok, last = await _poll(check, timeout)
    return ok, f"{target} status: {last}"


async def wait_postgres_ready(target: str, timeout: float = 60, **_) -> tuple[bool, str]:
    import asyncpg

    async def check():
        try:
            conn = await asyncpg.connect(POSTGRES_ADMIN_URL, timeout=3)
            await conn.close()
            return True, "accepting connections"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
    ok, last = await _poll(check, timeout)
    return ok, f"postgres: {last}"


async def wait_http_healthy(target: str, timeout: float = 60, **_) -> tuple[bool, str]:
    async def check():
        h = await probe_health()
        return h["ok"], (f"HTTP {h['status_code']} {h['text'][:120]}" if h["status_code"] else h["error"])
    ok, last = await _poll(check, timeout)
    return ok, f"/health: {last}"


async def sql_restore_app_role(target: str, **_) -> tuple[bool, str]:
    import asyncpg
    if not (POSTGRES_ADMIN_URL and APP_DB_USER and APP_DB_PASSWORD and APP_DB_NAME):
        return False, "POSTGRES_ADMIN_URL / APP_DB_* are not configured"
    user, db = _quote_ident(APP_DB_USER), _quote_ident(APP_DB_NAME)
    conn = await asyncpg.connect(POSTGRES_ADMIN_URL, timeout=5)
    try:
        await conn.execute(f"ALTER ROLE {user} WITH LOGIN PASSWORD {_quote_literal(APP_DB_PASSWORD)}")
        await conn.execute(f"GRANT ALL PRIVILEGES ON DATABASE {db} TO {user}")
        await conn.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {user}")
        await conn.execute(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {user}")
    finally:
        await conn.close()
    return True, f"role {APP_DB_USER}: LOGIN + password restored, privileges granted"


ACTIONS = {
    "docker_start": docker_start,
    "docker_restart": docker_restart,
    "wait_container_running": wait_container_running,
    "wait_postgres_ready": wait_postgres_ready,
    "wait_http_healthy": wait_http_healthy,
    "sql_restore_app_role": sql_restore_app_role,
}
