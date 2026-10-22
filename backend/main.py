"""
Demo backend — FastAPI + PostgreSQL.
Exposes /health, /orders, /metrics, /admin/exhaust-pool.
Ships structured logs to Elasticsearch from a background thread.
"""

import json
import os
import queue
import secrets
import time
import logging
import threading
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ── Prometheus metrics ─────────────────────────────────────────────────────
# Recorded by middleware for every request; error rates are derived in PromQL
# with rate(), so nothing here is a lifetime-cumulative ratio.
REQUEST_COUNT   = Counter("backend_requests_total",   "HTTP requests served", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("backend_request_duration_seconds", "HTTP request latency", ["path"])
DB_UP           = Gauge("backend_database_connected",  "Result of the last /health DB probe: 1=up 0=down")

# ── Elasticsearch log shipper ───────────────────────────────────────────────
ES_URL    = os.getenv("ELASTICSEARCH_URL", "http://elasticsearch:9200")
LOG_INDEX = os.getenv("LOG_INDEX", "backend-logs")


class ESHandler(logging.Handler):
    """Queues log records and bulk-ships them to Elasticsearch from a daemon
    thread, so a slow or unavailable Elasticsearch never blocks a request."""

    def __init__(self):
        super().__init__()
        self._q: queue.Queue = queue.Queue(maxsize=10_000)
        threading.Thread(target=self._ship, name="es-log-shipper", daemon=True).start()

    def emit(self, record):
        doc = {
            "@timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
                                  .strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "message": self.format(record),
            "service": "backend",
            "logger": record.name,
        }
        try:
            self._q.put_nowait(doc)
        except queue.Full:
            pass  # drop rather than block the request path

    def _ship(self):
        with httpx.Client(timeout=5.0) as client:
            while True:
                docs = [self._q.get()]
                while len(docs) < 500:
                    try:
                        docs.append(self._q.get_nowait())
                    except queue.Empty:
                        break
                body = "".join(
                    json.dumps({"index": {}}) + "\n" + json.dumps(d) + "\n" for d in docs
                )
                try:
                    client.post(f"{ES_URL}/{LOG_INDEX}/_bulk", content=body,
                                headers={"Content-Type": "application/x-ndjson"})
                except Exception:
                    pass


# ── Logging setup ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("backend")
es_handler = ESHandler()
es_handler.setLevel(logging.INFO)
logger.addHandler(es_handler)



class _HideFaultInjection(logging.Filter):
    """The fault-injection endpoint is test harness, not part of the system under
    test: it must leave no trace in any log the agents can read (stdout included),
    only its real effects."""

    def filter(self, record):
        return "/admin/" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_HideFaultInjection())

# ── Database ────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ["DATABASE_URL"]
POOL_SIZE         = int(os.getenv("DB_POOL_SIZE", "5"))
POOL_MAX_OVERFLOW = int(os.getenv("DB_POOL_MAX_OVERFLOW", "10"))
# Must stay below the agent's 5 s health-check timeout so an exhausted pool
# surfaces as a 503 with the real QueuePool error rather than a client timeout.
POOL_TIMEOUT      = float(os.getenv("DB_POOL_TIMEOUT", "3"))

engine = create_async_engine(
    DATABASE_URL, pool_pre_ping=True,
    pool_size=POOL_SIZE, max_overflow=POOL_MAX_OVERFLOW, pool_timeout=POOL_TIMEOUT,
    # Killing the postgres container tears down its network endpoint rather than
    # rejecting the connection, so a bare TCP connect can hang well past any
    # sensible health-check window (OS-level timeout, not asyncpg's). Cap both
    # connection setup and query execution so /health always fails fast and
    # reliably reports "disconnected" instead of hanging indefinitely.
    connect_args={"timeout": 3, "command_timeout": 3},
)
Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Connection-pool instrumentation, read live from SQLAlchemy at scrape time.
_pool = engine.sync_engine.pool
POOL_CHECKED_OUT = Gauge("backend_db_pool_checked_out", "DB connections currently checked out of the pool")
POOL_CHECKED_OUT.set_function(lambda: _pool.checkedout())
POOL_CAPACITY = Gauge("backend_db_pool_capacity", "Max DB connections the pool can hand out (pool_size + max_overflow)")
POOL_CAPACITY.set(POOL_SIZE + POOL_MAX_OVERFLOW)
IN_FLIGHT = Gauge("backend_http_requests_in_progress", "HTTP requests currently being handled")

app = FastAPI(title="Demo Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def record_metrics(request: Request, call_next):
    t0 = time.perf_counter()
    status = 500
    tracked = request.url.path != "/metrics"
    if tracked:
        IN_FLIGHT.inc()
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        if tracked:
            IN_FLIGHT.dec()
        # Label by route template (not raw URL) to keep series cardinality bounded.
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        if path != "/metrics":
            REQUEST_COUNT.labels(request.method, path, str(status)).inc()
            REQUEST_LATENCY.labels(path).observe(time.perf_counter() - t0)

# ── Fault injection: connection leak ───────────────────────────────────────
# Connections checked out by /admin/exhaust-pool and deliberately never returned.
# This is a genuine leak — every subsequent request blocks for POOL_TIMEOUT and
# then fails with SQLAlchemy's own TimeoutError. Cleared by a process restart.
_leaked: list = []
FAULT_INJECTION_TOKEN = os.getenv("FAULT_INJECTION_TOKEN", "")


@app.post("/admin/exhaust-pool")
async def admin_exhaust_pool(x_fault_token: str | None = Header(default=None)):
    """Leak every pool slot (pool_size + max_overflow connections)."""
    if not FAULT_INJECTION_TOKEN:
        raise HTTPException(404)
    if not secrets.compare_digest(x_fault_token or "", FAULT_INJECTION_TOKEN):
        raise HTTPException(401, "Invalid or missing X-Fault-Token")

    target = POOL_SIZE + POOL_MAX_OVERFLOW
    while len(_leaked) < target:
        conn = await engine.connect()
        await conn.execute(text("SELECT 1"))
        _leaked.append(conn)
    return {"ok": True, "leaked": len(_leaked)}


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        async with Session() as s:
            await s.execute(text("SELECT 1"))
        DB_UP.set(1)
        logger.debug("Health check OK")
        return {"status": "healthy", "database": "connected"}
    except Exception as exc:
        DB_UP.set(0)
        logger.error(f"Health check FAILED — {type(exc).__name__}: {exc}")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": "disconnected",
                     "error": f"{type(exc).__name__}: {exc}"}
        )


@app.get("/orders")
async def get_orders():
    t0 = time.perf_counter()

    try:
        async with Session() as s:
            result = await s.execute(text(
                "SELECT o.id, o.product_name, o.quantity, o.price, o.status, u.name "
                "FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id LIMIT 20"
            ))
            rows = [dict(r._mapping) for r in result]

        latency = time.perf_counter() - t0
        logger.info(f"GET /orders 200 — {len(rows)} rows in {latency:.3f}s")
        return {"orders": rows, "count": len(rows)}

    except Exception as exc:
        logger.error(f"GET /orders 503 — {type(exc).__name__}: {exc}")
        return JSONResponse(
            status_code=503,
            content={"error": "Service unavailable", "detail": str(exc)}
        )


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
