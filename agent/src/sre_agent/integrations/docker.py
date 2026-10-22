"""
Docker daemon HTTP API via Unix socket — fully async, no blocking SDK.
All container operations (stop/start/restart/status/exec) go through here.
"""

import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger("docker_utils")
_SOCKET = "/var/run/docker.sock"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=_SOCKET),
        base_url="http://docker",
        timeout=30,
    )


async def container_stop(name: str, timeout: int = 5) -> bool:
    try:
        async with _client() as c:
            r = await c.post(f"/containers/{name}/stop", params={"t": str(timeout)})
            return r.status_code in (204, 304)
    except Exception as exc:
        logger.warning(f"container_stop({name}): {exc}")
        return False


async def container_start(name: str) -> bool:
    try:
        async with _client() as c:
            r = await c.post(f"/containers/{name}/start")
            return r.status_code in (204, 304)
    except Exception as exc:
        logger.warning(f"container_start({name}): {exc}")
        return False


async def container_restart(name: str, timeout: int = 20) -> bool:
    try:
        async with _client() as c:
            r = await c.post(f"/containers/{name}/restart", params={"t": str(timeout)})
            return r.status_code == 204
    except Exception as exc:
        logger.warning(f"container_restart({name}): {exc}")
        return False


async def container_status(name: str) -> str:
    try:
        async with _client() as c:
            r = await c.get(f"/containers/{name}/json")
            if r.status_code == 200:
                return r.json()["State"]["Status"]
            return "not_found"
    except Exception as exc:
        logger.warning(f"container_status({name}): {exc}")
        return "unknown"


async def container_statuses(names: list[str]) -> dict:
    results = await asyncio.gather(*[container_status(n) for n in names])
    return dict(zip(names, results))


async def container_inspect(name: str) -> dict | None:
    """Full `docker inspect` document, or None if the container does not exist."""
    async with _client() as c:
        r = await c.get(f"/containers/{name}/json")
        return r.json() if r.status_code == 200 else None


def _demux(raw: bytes) -> str:
    """Docker multiplexes stdout/stderr of non-TTY containers with 8-byte frame headers."""
    out, i = [], 0
    while i + 8 <= len(raw) and raw[i] in (0, 1, 2) and raw[i + 1:i + 4] == b"\x00\x00\x00":
        size = int.from_bytes(raw[i + 4:i + 8], "big")
        out.append(raw[i + 8:i + 8 + size])
        i += 8 + size
    if i == 0:                      # TTY container — raw stream
        return raw.decode("utf-8", errors="replace")
    return b"".join(out).decode("utf-8", errors="replace")


async def container_logs(name: str, tail: int = 50, since_unix: int | None = None) -> str:
    params = {"stdout": "1", "stderr": "1", "timestamps": "1", "tail": str(tail)}
    if since_unix:
        params["since"] = str(since_unix)
    async with _client() as c:
        r = await c.get(f"/containers/{name}/logs", params=params)
        if r.status_code != 200:
            raise RuntimeError(f"docker logs {name}: HTTP {r.status_code} {r.text[:200]}")
        return _demux(r.content)


async def docker_events(since_unix: int, until_unix: int, containers: list[str] | None = None) -> list[dict]:
    """Container lifecycle events (start, die, stop, kill, oom, restart, health_status ...)."""
    filters = {"type": ["container"]}
    if containers:
        filters["container"] = containers
    async with _client() as c:
        r = await c.get("/events", params={
            "since": str(since_unix), "until": str(until_unix), "filters": json.dumps(filters),
        })
        events = []
        for line in r.text.splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
        return events


_project: str | None = None


async def compose_project() -> str | None:
    """The compose project this agent runs in (from its own container's labels)."""
    global _project
    if _project is None:
        info = await container_inspect(os.getenv("HOSTNAME", ""))
        _project = ((info or {}).get("Config", {}).get("Labels", {}) or {}).get(
            "com.docker.compose.project", "")
    return _project or None


async def project_containers() -> list[dict]:
    """All containers of this compose project: name, state, status, image."""
    project = await compose_project()
    params = {"all": "1"}
    if project:
        params["filters"] = json.dumps({"label": [f"com.docker.compose.project={project}"]})
    async with _client() as c:
        r = await c.get("/containers/json", params=params)
        r.raise_for_status()
        return [
            {"name": (ct.get("Names") or ["?"])[0].lstrip("/"),
             "state": ct.get("State", ""), "status": ct.get("Status", ""),
             "image": ct.get("Image", "")}
            for ct in r.json()
        ]


async def container_exec(name: str, cmd: list[str]) -> tuple[int, str]:
    """Run a command inside a running container. Returns (exit_code, message)."""
    try:
        async with _client() as c:
            # Create exec instance
            r = await c.post(f"/containers/{name}/exec", json={
                "Cmd": cmd,
                "AttachStdout": True,
                "AttachStderr": True,
            })
            if r.status_code != 201:
                return -1, f"exec create failed: {r.status_code} {r.text}"
            exec_id = r.json()["Id"]

            # Start (wait for completion)
            await c.post(f"/exec/{exec_id}/start",
                         json={"Detach": False, "Tty": False},
                         timeout=30)

            # Inspect exit code
            r2 = await c.get(f"/exec/{exec_id}/json")
            exit_code = r2.json().get("ExitCode", 0) if r2.status_code == 200 else 0
            return exit_code, "ok"
    except Exception as exc:
        logger.warning(f"container_exec({name}, {cmd}): {exc}")
        return -1, str(exc)
