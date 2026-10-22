"""
Synthetic client — steady user-like traffic against the backend.

Measures what a user actually experiences (black-box), independent of the
backend's own metrics: when the backend process is dead it cannot report its
own failures, but this client still records every refused connection and
timeout. Exposes Prometheus metrics on :9100.
"""

import asyncio
import itertools
import logging
import os
import time

import httpx
from prometheus_client import Counter, Histogram, start_http_server

TARGET_URL = os.getenv("TARGET_URL", "http://backend:8000")
PATHS      = [p.strip() for p in os.getenv("TARGET_PATHS", "/orders,/health").split(",") if p.strip()]
RPS        = float(os.getenv("REQUESTS_PER_SECOND", "4"))
TIMEOUT    = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "5"))
PORT       = int(os.getenv("METRICS_PORT", "9100"))

REQUESTS = Counter("loadgen_requests_total", "Synthetic client requests by outcome",
                   ["path", "outcome"])
LATENCY  = Histogram("loadgen_request_duration_seconds", "Synthetic client request latency",
                     ["path"])

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("loadgen")


def _outcome(status: int) -> str:
    if status < 400:
        return "ok"
    return "http_5xx" if status >= 500 else "http_4xx"


async def _hit(client: httpx.AsyncClient, path: str):
    t0 = time.perf_counter()
    try:
        r = await client.get(f"{TARGET_URL}{path}")
        outcome = _outcome(r.status_code)
    except httpx.TimeoutException:
        outcome = "timeout"
    except httpx.ConnectError:
        outcome = "connect_error"
    except httpx.HTTPError:
        outcome = "error"
    REQUESTS.labels(path, outcome).inc()
    LATENCY.labels(path).observe(time.perf_counter() - t0)


async def main():
    start_http_server(PORT)
    logger.info(f"Synthetic client: {RPS} req/s against {TARGET_URL} {PATHS}")
    interval = 1.0 / RPS
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for path in itertools.cycle(PATHS):
            # Fire-and-forget so a slow backend lowers success, not request rate.
            asyncio.create_task(_hit(client, path))
            await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
