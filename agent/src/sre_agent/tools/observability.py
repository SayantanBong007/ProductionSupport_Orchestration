"""
Observability tools — health endpoint, Prometheus and Elasticsearch.

Tools return raw observations as compact text. They never classify or
interpret; deciding what the data means is the agents' job.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict

import httpx

from sre_agent.config import BACKEND_URL, ELASTICSEARCH_URL, LOG_INDEX, PROMETHEUS_URL, SYNTHETIC_REQUESTS_METRIC
from sre_agent.tools.base import Tool, clip, params, utc


# ── Shared probes (also used by the watcher and the remediation verifier) ─────

async def probe_health() -> dict:
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(f"{BACKEND_URL}/health")
        try:
            body = r.json()
        except ValueError:
            body = None
        return {"ok": r.status_code == 200, "status_code": r.status_code, "body": body,
                "text": r.text[:500], "error": None,
                "latency_ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "status_code": None, "body": None, "text": "",
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round((time.perf_counter() - t0) * 1000)}


async def prom_instant(promql: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql})
    data = r.json()
    if data.get("status") != "success":
        raise ValueError(data.get("error", f"HTTP {r.status_code}"))
    return data["data"]["result"]


async def client_error_stats(window: str) -> dict | None:
    """Synthetic-client outcome counts over ``window`` (e.g. '60s', '5m').

    Returns None when Prometheus has no samples (client not scraped yet).
    """
    series = await prom_instant(
        f"sum by (path, outcome) (increase({SYNTHETIC_REQUESTS_METRIC}[{window}]))")
    if not series:
        return None
    by_path: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "failed": 0.0})
    by_outcome: dict[str, float] = defaultdict(float)
    for s in series:
        path, outcome = s["metric"].get("path", "?"), s["metric"].get("outcome", "?")
        v = float(s["value"][1])
        by_path[path]["total"] += v
        by_outcome[outcome] += v
        if outcome != "ok":
            by_path[path]["failed"] += v
    total  = sum(p["total"] for p in by_path.values())
    failed = sum(p["failed"] for p in by_path.values())
    return {"window": window, "total": round(total), "failed": round(failed),
            "ratio": (failed / total) if total else 0.0,
            "by_path": {k: {kk: round(vv) for kk, vv in v.items()} for k, v in by_path.items()},
            "by_outcome": {k: round(v) for k, v in by_outcome.items()}}


# ── Agent tools ───────────────────────────────────────────────────────────────

def _fmt_labels(metric: dict) -> str:
    name = metric.get("__name__", "")
    labels = ",".join(f'{k}="{v}"' for k, v in sorted(metric.items()) if k != "__name__")
    return f"{name}{{{labels}}}" if labels else (name or "{}")


async def check_service_health() -> str:
    h = await probe_health()
    if h["error"]:
        return f"GET {BACKEND_URL}/health -> request failed after {h['latency_ms']} ms: {h['error']}"
    return (f"GET {BACKEND_URL}/health -> HTTP {h['status_code']} in {h['latency_ms']} ms\n"
            f"body: {h['text']}")


async def prometheus_query(promql: str) -> str:
    try:
        result = await prom_instant(promql)
    except Exception as exc:
        return f"query error: {exc}"
    if not result:
        return f"{promql}\n(no series returned)"
    lines = [f"{_fmt_labels(s['metric'])} = {s['value'][1]}  (at {utc(float(s['value'][0]))})"
             for s in result[:25]]
    if len(result) > 25:
        lines.append(f"… {len(result) - 25} more series")
    return clip(f"{promql}\n" + "\n".join(lines))


async def prometheus_range(promql: str, minutes: int = 15, step_seconds: int = 0) -> str:
    minutes = max(1, min(int(minutes), 180))
    end = time.time()
    step = int(step_seconds) or max(10, minutes * 60 // 40)
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{PROMETHEUS_URL}/api/v1/query_range", params={
                "query": promql, "start": end - minutes * 60, "end": end, "step": step})
        data = r.json()
        if data.get("status") != "success":
            return f"query error: {data.get('error', r.status_code)}"
        result = data["data"]["result"]
    except Exception as exc:
        return f"query error: {exc}"
    if not result:
        return f"{promql} over last {minutes}m\n(no series returned)"

    out = [f"{promql} over last {minutes}m (step {step}s)"]
    for s in result[:8]:
        vals = [(float(t), float(v)) for t, v in s["values"]]
        nums = [v for _, v in vals]
        out.append(f"{_fmt_labels(s['metric'])}: {len(vals)} points, first {nums[0]:g} at {utc(vals[0][0])}, "
                   f"last {nums[-1]:g} at {utc(vals[-1][0])}, min {min(nums):g}, max {max(nums):g}")
        # Value changes, so the agent can see when something shifted and
        # where samples stop (a gap means the target stopped reporting).
        changes, prev_v, prev_t = [], None, None
        for t, v in vals:
            if prev_t is not None and t - prev_t > step * 1.5:
                changes.append(f"gap {utc(prev_t)} → {utc(t)}")
            if prev_v is None or v != prev_v:
                changes.append(f"{utc(t)}={v:g}")
            prev_v, prev_t = v, t
        if end - vals[-1][0] > step * 2:
            changes.append(f"no samples since {utc(vals[-1][0])}")
        out.append("  changes: " + ", ".join(changes[:14]) + (" …" if len(changes) > 14 else ""))
    if len(result) > 8:
        out.append(f"… {len(result) - 8} more series")
    return clip("\n".join(out))


async def list_metrics(contains: str = "") -> str:
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{PROMETHEUS_URL}/api/v1/metadata")
        meta = r.json().get("data", {})
    except Exception as exc:
        return f"metadata error: {exc}"
    names = sorted(n for n in meta if contains.lower() in n.lower()
                   and not n.startswith(("go_", "process_", "prometheus_", "net_conntrack", "promhttp")))
    lines = [f"{n} ({meta[n][0].get('type')}): {meta[n][0].get('help', '')}" for n in names[:60]]
    return clip(f"{len(names)} metrics matching '{contains}':\n" + "\n".join(lines)) if lines \
        else f"no metrics matching '{contains}'"


def _signature(msg: str) -> str:
    """Group log lines that differ only in numbers/ids."""
    return re.sub(r"\d+", "#", msg)[:200]


async def search_logs(query: str = "*", minutes: int = 15, level: str = "") -> str:
    minutes = max(1, min(int(minutes), 24 * 60))
    must: list[dict] = []
    if query and query.strip() not in ("*", ""):
        must.append({"query_string": {"query": query, "default_field": "message",
                                      "default_operator": "AND"}})
    if level:
        must.append({"match": {"level": level.upper()}})
    body = {
        "size": 200, "track_total_hits": True,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {"bool": {"must": must or [{"match_all": {}}],
                           "filter": [{"range": {"@timestamp": {"gte": f"now-{minutes}m"}}}]}},
        "aggs": {"levels": {"terms": {"field": "level.keyword", "size": 10}}},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{ELASTICSEARCH_URL}/{LOG_INDEX}*/_search", json=body)
        data = r.json()
        if "error" in data:
            return f"search error: {str(data['error'])[:300]}"
    except Exception as exc:
        return f"search error: {exc}"

    total  = data["hits"]["total"]["value"]
    levels = ", ".join(f"{b['key']}: {b['doc_count']}"
                       for b in data.get("aggregations", {}).get("levels", {}).get("buckets", []))
    hits = [h["_source"] for h in data["hits"]["hits"]]
    header = (f"index {LOG_INDEX}, last {minutes}m, query='{query}'"
              f"{', level=' + level.upper() if level else ''}: {total} entries ({levels or 'none'})")
    if not hits:
        return header

    groups: dict[str, dict] = {}
    for h in hits:                                  # newest first
        msg, ts = h.get("message", ""), (h.get("@timestamp", "")[:19] + "Z")
        g = groups.setdefault(_signature(msg), {"n": 0, "last": ts, "first": ts,
                                                 "level": h.get("level", ""), "msg": msg})
        g["n"] += 1
        g["first"] = ts
    lines = [f"{g['n']}x  {g['first']} → {g['last']}  {g['level']}  {g['msg'][:300]}"
             for g in sorted(groups.values(), key=lambda g: -g["n"])[:12]]
    sampled = f" (grouped from newest {len(hits)})" if total > len(hits) else ""
    return clip(header + f"\ndistinct messages{sampled}:\n" + "\n".join(lines))


TOOLS = {
    "check_service_health": Tool(
        "check_service_health",
        "Call the backend service's /health endpoint now; returns HTTP status, latency and body.",
        params(), check_service_health),
    "prometheus_query": Tool(
        "prometheus_query",
        "Run an instant PromQL query against Prometheus. Use list_metrics first to discover "
        "metric names. Example: sum by (status) (increase(backend_requests_total[5m])).",
        params({"promql": {"type": "string"}}, ["promql"]), prometheus_query),
    "prometheus_range": Tool(
        "prometheus_range",
        "Run a PromQL range query over the last N minutes; returns per-series summary, value "
        "changes with timestamps, and gaps where the target stopped reporting.",
        params({"promql": {"type": "string"},
                "minutes": {"type": "integer", "description": "lookback, default 15"},
                "step_seconds": {"type": "integer", "description": "0 = automatic"}},
               ["promql"]), prometheus_range),
    "list_metrics": Tool(
        "list_metrics",
        "List Prometheus metric names, types and help text, optionally filtered by substring.",
        params({"contains": {"type": "string"}}), list_metrics),
    "search_logs": Tool(
        "search_logs",
        "Search application logs in Elasticsearch over the last N minutes. `query` is Lucene "
        "query_string syntax on the message field ('*' for all). Returns counts per level and "
        "distinct messages with first/last timestamps.",
        params({"query": {"type": "string"},
                "minutes": {"type": "integer", "description": "lookback, default 15"},
                "level": {"type": "string", "description": "optional: ERROR, WARNING, INFO"}}),
        search_logs),
}
