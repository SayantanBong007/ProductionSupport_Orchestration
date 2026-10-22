# Configuration

All settings are environment variables. `docker-compose.yml` passes them to the containers from
`.env` (start from `.env.example`). The agent reads every setting in one place:
`agent/src/sre_agent/config.py`.

Variables marked **required** make `docker compose` refuse to start when missing.

## Secrets and credentials

| Variable | Required | Purpose |
|---|---|---|
| `LLM_API_KEY` | yes (for AI) | API key of the LLM provider. Without it every investigation reports "AI unavailable". |
| `AGENT_API_KEY` | yes | Required `X-API-Key` header on every `/action/*` call. The UI receives it from the server. |
| `POSTGRES_PASSWORD` | yes | PostgreSQL superuser password. Used by the `restore_app_db_role` runbook and the database readiness check. |
| `APP_DB_USER`, `APP_DB_PASSWORD` | yes | The backend's database role, created on first start by `backend/db-init/02-app-user.sh`. |
| `POSTGRES_DB` | no (`pocdb`) | Database name. |
| `FAULT_INJECTION_TOKEN` | no | Enables the backend's fault-injection endpoint. Leave it empty in any real deployment. |
| `GRAFANA_ADMIN_PASSWORD` | yes | Grafana `admin` password. |
| `PORTAINER_ADMIN_PASSWORD` | no | Portainer admin password (at least 12 characters). Empty = skip auto-setup. |
| `GITEA_ADMIN_USER`, `GITEA_ADMIN_PASSWORD`, `GITEA_ADMIN_EMAIL` | no | Gitea admin account. Empty = skip auto-setup. |

> Database credentials are applied only when the `postgres_data` volume is first created.
> After changing them, run `docker compose down -v` (this deletes the demo data).

## LLM

| Variable | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Any OpenAI-compatible endpoint. |
| `COMMANDER_MODEL` | `openai/gpt-oss-120b` | Incident Commander, Remediation Planner, closure note. |
| `SPECIALIST_MODEL` | `openai/gpt-oss-20b` | Reviewer, and the default for the logs agent. |
| `LOGS_AGENT_MODEL` | `SPECIALIST_MODEL` | Logs specialist. |
| `METRICS_AGENT_MODEL` | `qwen/qwen3.8-27b` | Metrics specialist. |
| `INFRA_AGENT_MODEL` | `COMMANDER_MODEL` | Infrastructure specialist (the commander is idle while specialists run). |
| `LLM_FALLBACK_MODELS` | `qwen/qwen3.8-27b,openai/gpt-oss-20b` | Tried in order when a model's quota is exhausted. |
| `LLM_REASONING_EFFORT` | empty | `low` / `medium` / `high` for reasoning models (gpt-oss). `low` keeps calls fast. |

Providers rate-limit **per model**, and the three specialists run in parallel, so each one should
use a different model. A test (`tests/unit/test_specialists.py`) enforces this for the defaults.

## Alerting (watcher)

| Variable | Default | Purpose |
|---|---|---|
| `WATCH_INTERVAL_SECONDS` | `10` | How often the alert rules are evaluated. |
| `ALERT_CONSECUTIVE_CHECKS` | `2` | Failing checks in a row before an alert (the `for:` clause). |
| `ALERT_CLIENT_ERROR_RATIO` | `0.2` | Synthetic-client failure ratio that counts as an outage. Also the recovery threshold after a fix. |
| `BACKEND_CONTAINER` | `poc-backend` | Critical container; alerted on when not running. |
| `DATABASE_CONTAINER` | `poc-postgres` | Critical container; alerted on when not running. |

## Investigation budgets

| Variable | Default | Purpose |
|---|---|---|
| `COMMANDER_MAX_ROUNDS` | `8` | Commander turns per investigation. |
| `SPECIALIST_MAX_ROUNDS` | `4` | Tool-calling rounds per specialist task. |
| `MAX_DIAGNOSIS_REVISIONS` | `2` | Send-backs shared by the verifier and the reviewer. |
| `MIN_VERIFIED_EVIDENCE_RATIO` | `1.0` | Share of citations that must verify for a diagnosis to pass. |
| `MAX_TOOL_OUTPUT_CHARS` | `1800` | Truncation limit for a single tool result. |
| `MAX_CONTEXT_CHARS` | `12000` | Conversation size per request; older tool outputs are compacted beyond this. |

## Endpoints and data

| Variable | Default | Purpose |
|---|---|---|
| `BACKEND_URL` | `http://backend:8000` | Monitored service (health checks, fault injection). |
| `PROMETHEUS_URL` | `http://prometheus:9090` | Metrics. |
| `ELASTICSEARCH_URL` | `http://elasticsearch:9200` | Logs. |
| `LOG_INDEX` | `backend-logs` | Elasticsearch index prefix for application logs. |
| `SYNTHETIC_REQUESTS_METRIC` | `loadgen_requests_total` | Counter used for user-impact and alert measurements. |
| `RUNBOOKS_PATH` | bundled `runbooks.yaml` | Approved runbook catalog. |
| `REPORTS_DIR` | `/app/reports` | JSON audit record per incident (mounted to `./reports`). |
| `CHROMA_DIR` | `/app/chroma` | Incident memory (Docker volume `chroma_data`). |

## Backend and synthetic client

| Variable | Default | Purpose |
|---|---|---|
| `DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW` | `5`, `10` | SQLAlchemy connection pool of the backend. |
| `DB_POOL_TIMEOUT` | `3` | Seconds a request waits for a pool connection. |
| `TARGET_PATHS` | `/orders,/health` | Paths the synthetic client requests. |
| `REQUESTS_PER_SECOND` | `4` | Synthetic client request rate. |
