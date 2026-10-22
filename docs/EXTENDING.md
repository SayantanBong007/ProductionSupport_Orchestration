# Extending the agent

All paths below are relative to `agent/src/sre_agent/`. Run the tests after any change:
`cd agent && pytest`.

## Add a tool

A tool is an async function that returns **raw observations as text**. It must not interpret
them; the agents do that.

1. Write the function in the matching module under `tools/` (`observability.py`,
   `infrastructure.py` or `memory.py`) and register it in that module's `TOOLS` dict:

   ```python
   async def queue_depth(queue: str) -> str:
       ...
       return f"queue {queue}: {depth} messages, oldest {age}s"

   TOOLS["queue_depth"] = Tool(
       "queue_depth", "Current depth and oldest-message age of a message queue.",
       params({"queue": {"type": "string"}}, ["queue"]), queue_depth)
   ```

2. Add it to a specialist's toolset in `tools/__init__.py`.

Every call is recorded in the evidence ledger automatically, so the result becomes citable as
`E<n>`. Keep output compact (it is clipped at `MAX_TOOL_OUTPUT_CHARS`) and include timestamps
where they exist.

## Add a specialist

In `agents/specialists.py`, add an entry to `SPECIALISTS` with a key, a label, a one-line domain
description, its toolset and its model. The commander learns about it automatically: the
`dispatch_specialist` tool and the commander prompt are generated from `SPECIALISTS`. Give it a model
that the other specialists do not use (see [CONFIGURATION.md](CONFIGURATION.md)), and add its label
and colour to `AGENTS` in `api/static/index.html`.

## Add a runbook

Runbooks live in `graphs/remediation/runbooks.yaml`:

```yaml
  - id: restart_worker
    title: Restart the worker container
    when_to_use: >
      The worker is running but has stopped consuming its queue.
    risk: In-flight jobs are retried.
    allowed_targets: ["${WORKER_CONTAINER}"]
    steps:
      - action: docker_restart
      - action: wait_container_running
        timeout: 30
```

- `${VAR}` is replaced with values from `config.py`.
- A step without `target` acts on the target the planner chose.
- `when_to_use` is what the planner reads, so describe the *situation*, not a specific incident.
- Steps may only use the actions defined in `graphs/remediation/actions.py`; the loader rejects
  anything else.

**New action:** add an async function to `actions.py` that returns `(ok, detail)` describing what
was actually observed, and register it in `ACTIONS`.

## Add an alert rule

Alert rules are in `watcher.py` → `evaluate()`. Append a human-readable signal when the rule fails:

```python
if queue_depth > QUEUE_DEPTH_THRESHOLD:
    signals.append(f"queue {name}: {queue_depth} messages (threshold {QUEUE_DEPTH_THRESHOLD})")
```

Describe what was *observed*, not a suspected cause; the signal text is all the commander gets
to start from.

## Use a different LLM provider

Set `LLM_BASE_URL`, `LLM_API_KEY` and the model variables in `.env`. The provider must support
OpenAI-style tool calling. For example, with OpenAI itself:

```env
LLM_BASE_URL=https://api.openai.com/v1
COMMANDER_MODEL=gpt-4.1
SPECIALIST_MODEL=gpt-4.1-mini
METRICS_AGENT_MODEL=gpt-4.1-mini
```

## Open the graphs in LangGraph Studio

`agent/langgraph.json` registers both graphs:

```bash
cd agent
pip install -e . "langgraph-cli[inmem]"
langgraph dev
```

Every field of the runtime context has a default, so the graphs run in Studio without the API.
The tools still need the monitored services, so start the compose stack first and point
`BACKEND_URL`, `PROMETHEUS_URL` and `ELASTICSEARCH_URL` at `localhost`.

## Tests

| Folder | Covers |
|---|---|
| `tests/unit/` | Evidence verification, runbook loading and allow-listing, the agent loop (budgets, compaction), LLM model fallback, specialist model separation. |
| `tests/graphs/` | The real investigation graph run against a **scripted LLM**: parallel dispatch, verifier and reviewer send-backs, invalid runbook proposals, LLM outage. |

To test a new graph path, add a script entry for each agent involved in
`tests/graphs/test_investigation_graph.py` (see `_script()`).
