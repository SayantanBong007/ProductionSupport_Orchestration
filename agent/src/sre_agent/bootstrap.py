"""
First-run setup of the bundled tooling (Portainer admin, Kibana data view,
Gitea admin), from the credentials in .env. Each step is idempotent and
skipped when its credentials are not configured.
"""

from __future__ import annotations

import asyncio
import logging
import shlex

import httpx

from sre_agent.config import (
    GITEA_ADMIN_EMAIL, GITEA_ADMIN_PASSWORD, GITEA_ADMIN_USER, GITEA_CONTAINER, GITEA_URL,
    KIBANA_URL, LOG_INDEX, PORTAINER_ADMIN_PASSWORD, PORTAINER_CONTAINER, PORTAINER_URL,
)
from sre_agent.integrations.docker import container_exec, container_logs

logger = logging.getLogger(__name__)


async def bootstrap_tooling(delay: float = 30):
    """Wait for the tooling containers to start, then configure them."""
    await asyncio.sleep(delay)
    await asyncio.gather(_init_portainer(), _init_kibana(), _init_gitea(), return_exceptions=True)


async def _init_portainer():
    if not PORTAINER_ADMIN_PASSWORD:
        logger.info("Portainer: PORTAINER_ADMIN_PASSWORD not set — skipping admin bootstrap")
        return
    for attempt in range(8):
        try:
            # Newer Portainer CE prints a one-time setup token to its own log.
            raw = await container_logs(PORTAINER_CONTAINER, tail=50)
            token = ""
            for line in raw.splitlines():
                if "setup_token=" in line:
                    token = line.split("setup_token=")[-1].strip().split()[0]
            headers = {"X-Setup-Token": token} if token else {}
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(f"{PORTAINER_URL}/api/users/admin/init",
                                 json={"Username": "admin", "Password": PORTAINER_ADMIN_PASSWORD},
                                 headers=headers)
            if r.status_code in (200, 409, 403):          # created, or already initialised
                logger.info(f"Portainer: admin user ready (HTTP {r.status_code})")
                return
        except Exception as exc:
            logger.debug(f"Portainer init attempt {attempt}: {exc}")
        await asyncio.sleep(10)
    logger.warning("Portainer: admin setup timed out")


async def _init_kibana():
    for _ in range(12):
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                if (await c.get(f"{KIBANA_URL}/api/status")).status_code == 200:
                    r = await c.post(
                        f"{KIBANA_URL}/api/data_views/data_view",
                        json={"data_view": {"title": f"{LOG_INDEX}*", "name": "Backend Logs",
                                            "timeFieldName": "@timestamp"}},
                        headers={"kbn-xsrf": "true"})
                    if r.status_code == 200 or (r.status_code == 400 and "Duplicate" in r.text):
                        logger.info("Kibana: log data view ready")
                        return
        except Exception:
            pass
        await asyncio.sleep(15)
    logger.warning("Kibana: data view setup timed out")


async def _init_gitea():
    if not (GITEA_ADMIN_USER and GITEA_ADMIN_PASSWORD and GITEA_ADMIN_EMAIL):
        logger.info("Gitea: GITEA_ADMIN_* not set — skipping admin bootstrap")
        return
    for _ in range(10):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                if (await c.get(f"{GITEA_URL}/api/v1/version")).status_code == 200:
                    break
        except Exception:
            pass
        await asyncio.sleep(10)

    # The Gitea CLI must run as the git user. Exit code 1 = user already exists.
    for _ in range(3):
        code, _ = await container_exec(GITEA_CONTAINER, [
            "su", "git", "-c",
            f"gitea admin user create --username {shlex.quote(GITEA_ADMIN_USER)} "
            f"--password {shlex.quote(GITEA_ADMIN_PASSWORD)} "
            f"--email {shlex.quote(GITEA_ADMIN_EMAIL)} --admin --must-change-password=false",
        ])
        if code in (0, 1):
            logger.info(f"Gitea: admin '{GITEA_ADMIN_USER}' ready")
            return
        await asyncio.sleep(5)
    logger.warning("Gitea: admin setup failed")
