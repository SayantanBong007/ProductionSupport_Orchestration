"""
Central runtime configuration — every environment-specific value is read here.

Nothing deployment-specific (container names, credentials, URLs, model ids)
should be written as a literal anywhere else in the agent. Defaults match the
service names in docker-compose.yml; secrets have no defaults.
"""

import os


def _list(name: str, default: str) -> list[str]:
    return [s.strip() for s in os.getenv(name, default).split(",") if s.strip()]


# ── Monitored services ────────────────────────────────────────────────────────
BACKEND_URL        = os.getenv("BACKEND_URL",        "http://backend:8000")
PROMETHEUS_URL     = os.getenv("PROMETHEUS_URL",     "http://prometheus:9090")
ELASTICSEARCH_URL  = os.getenv("ELASTICSEARCH_URL",  "http://elasticsearch:9200")
KIBANA_URL         = os.getenv("KIBANA_URL",         "http://kibana:5601")
PORTAINER_URL      = os.getenv("PORTAINER_URL",      "http://portainer:9000")
GITEA_URL          = os.getenv("GITEA_URL",          "http://gitea:3000")

# Critical containers: the watcher alerts when one is not running, and runbooks
# may only target these (see graphs/remediation/runbooks.yaml).
BACKEND_CONTAINER    = os.getenv("BACKEND_CONTAINER",  "poc-backend")
DATABASE_CONTAINER   = os.getenv("DATABASE_CONTAINER", "poc-postgres")
CRITICAL_CONTAINERS  = (BACKEND_CONTAINER, DATABASE_CONTAINER)

# Elasticsearch index pattern the backend ships logs to
LOG_INDEX = os.getenv("LOG_INDEX", "backend-logs")

# Black-box client metric (see loadgen/) — what users experience, measured
# independently of whether the backend can still report its own metrics.
SYNTHETIC_REQUESTS_METRIC = os.getenv("SYNTHETIC_REQUESTS_METRIC", "loadgen_requests_total")

# ── Continuous watcher (alert rules) ─────────────────────────────────────────
WATCH_INTERVAL_SECONDS   = float(os.getenv("WATCH_INTERVAL_SECONDS", "10"))
# An anomaly must persist for this many consecutive checks before it alerts
# (the equivalent of a Prometheus alert rule's `for:` clause).
ALERT_CONSECUTIVE_CHECKS = int(os.getenv("ALERT_CONSECUTIVE_CHECKS", "2"))
# Synthetic-client failure ratio (over the watcher's 30 s window) that counts as an outage.
ALERT_CLIENT_ERROR_RATIO = float(os.getenv("ALERT_CLIENT_ERROR_RATIO", "0.2"))

# ── Investigation budgets ────────────────────────────────────────────────────
COMMANDER_MAX_ROUNDS      = int(os.getenv("COMMANDER_MAX_ROUNDS", "8"))
SPECIALIST_MAX_ROUNDS     = int(os.getenv("SPECIALIST_MAX_ROUNDS", "4"))
# Shared budget for send-backs by the evidence verifier and the reviewer.
MAX_DIAGNOSIS_REVISIONS   = int(os.getenv("MAX_DIAGNOSIS_REVISIONS", "2"))
# A diagnosis is sent back for revision when less than this share of its
# cited evidence quotes can be found verbatim in the recorded tool outputs.
MIN_VERIFIED_EVIDENCE_RATIO = float(os.getenv("MIN_VERIFIED_EVIDENCE_RATIO", "1.0"))
MAX_TOOL_OUTPUT_CHARS     = int(os.getenv("MAX_TOOL_OUTPUT_CHARS", "1800"))
# Conversation size an agent sends per request. Providers cap single requests
# (Groq free tier: 8K tokens incl. tool schemas and max_tokens), so older tool
# outputs are compacted beyond this.
MAX_CONTEXT_CHARS         = int(os.getenv("MAX_CONTEXT_CHARS", "12000"))

# Approved remediation runbooks (the only actions the agents can propose).
RUNBOOKS_PATH = os.getenv("RUNBOOKS_PATH", os.path.join(os.path.dirname(__file__), "graphs", "remediation", "runbooks.yaml"))

# ── Database (used only by the restore_app_db_role runbook) ───────────────────
POSTGRES_ADMIN_URL = os.getenv("POSTGRES_ADMIN_URL", "")
APP_DB_USER        = os.getenv("APP_DB_USER", "")
APP_DB_PASSWORD    = os.getenv("APP_DB_PASSWORD", "")
APP_DB_NAME        = os.getenv("APP_DB_NAME", "")

# ── LLM (any OpenAI-compatible endpoint; Groq by default) ────────────────────
# OPENROUTER_API_KEY is accepted for backwards compatibility with older .env files.
LLM_API_KEY  = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
MODEL_NAME   = os.getenv("MODEL_NAME",   "openai/gpt-oss-120b")
# Per-role models. Providers rate-limit per model (Groq free tier: 8K tokens/min
# each), so agents that run at the same time are spread over different models.
COMMANDER_MODEL  = os.getenv("COMMANDER_MODEL") or MODEL_NAME
SPECIALIST_MODEL = os.getenv("SPECIALIST_MODEL") or "openai/gpt-oss-20b"   # reviewer + default
# The specialists run in parallel, so each gets its own model. The infra agent
# shares the commander's model: the commander is idle while specialists work.
LOGS_AGENT_MODEL    = os.getenv("LOGS_AGENT_MODEL")    or SPECIALIST_MODEL
METRICS_AGENT_MODEL = os.getenv("METRICS_AGENT_MODEL") or "qwen/qwen3.8-27b"
INFRA_AGENT_MODEL   = os.getenv("INFRA_AGENT_MODEL")   or COMMANDER_MODEL
# Tried in order when a model's quota is exhausted (e.g. a daily token limit).
LLM_FALLBACK_MODELS = _list("LLM_FALLBACK_MODELS", "qwen/qwen3.8-27b,openai/gpt-oss-20b")
# For reasoning models (e.g. gpt-oss): reasoning tokens count against max_tokens,
# so keep effort low for these short, latency-sensitive calls. Empty = not sent.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "")

# ── Security ──────────────────────────────────────────────────────────────────
# Required on every /action/* call (X-API-Key header). Unset → actions disabled.
AGENT_API_KEY         = os.getenv("AGENT_API_KEY", "")
# Shared secret for the backend's fault-injection endpoint.
FAULT_INJECTION_TOKEN = os.getenv("FAULT_INJECTION_TOKEN", "")

# ── Bootstrap credentials for the bundled tooling ────────────────────────────
PORTAINER_ADMIN_PASSWORD = os.getenv("PORTAINER_ADMIN_PASSWORD", "")
GITEA_ADMIN_USER         = os.getenv("GITEA_ADMIN_USER", "")
GITEA_ADMIN_PASSWORD     = os.getenv("GITEA_ADMIN_PASSWORD", "")
GITEA_ADMIN_EMAIL        = os.getenv("GITEA_ADMIN_EMAIL", "")
PORTAINER_CONTAINER      = os.getenv("PORTAINER_CONTAINER", "poc-portainer")
GITEA_CONTAINER          = os.getenv("GITEA_CONTAINER",     "poc-gitea")
