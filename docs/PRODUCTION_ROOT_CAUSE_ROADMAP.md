# Phase 2 Roadmap — Per-Request Root-Causing in Production

## Problem this solves

The POC (see [ARCHITECTURE.md](ARCHITECTURE.md)) detects and fixes **categorical, service-wide
failures** — the whole backend is down, the whole database is down, the connection pool is
exhausted. One signal check, one fix, applied to the whole service.

Real production incidents are often the opposite shape: **individual requests failing
intermittently** while the service overall looks healthy. Example — users upload
photos, and some fraction of uploads fail. Today that's diagnosed manually:

```
Some uploads fail
    → check logs
    → find which service raised the error
    → pull the stack trace
    → go read the code at that line
    → figure out what's actually wrong
```

This is **per-request root-causing**, not service-health classification. It needs different
inputs and a different agent chain than the POC's service-level agent team.

## Why the POC's agents can't do this as-is

| POC capability | What per-request root-causing needs instead |
| --- | --- |
| Aggregate metrics (error rate, 503 count) | One specific failing request, isolated by correlation ID |
| `logger.error(f"... {exc}")` — message string only | Full stack trace captured in the log record |
| Docker container state (`running` / `exited`) | Pod state + deployment history (did a rollout cause this?) |
| No source code access | Read access to the exact deployed commit |
| Fix = restart/start a container | Fix = a suggested code or config change for a human to merge |

None of this is a rewrite of the POC's architecture — it's the **same StateGraph pattern**
(a commander dispatching tool-calling specialists, evidence-verified), pointed at different,
request-scoped tools.

## Proposed architecture, on a typical production stack

```
FAILING REQUEST REPORTED (correlation_id known)
         │
         ▼
   TRACE AGENT           → Elasticsearch: pull every log line for this correlation_id,
                            across every service the request touched
         │
         ▼
   DEPLOY CORRELATION AGENT → ArgoCD API: what commit/version was deployed to the
                            implicated service, and was there a rollout near the
                            failure timestamp? (deployment-causality — the POC has
                            no equivalent of this check)
         │
         ▼
   CONFIG AGENT           → Gitea: diff the service's current deployed config
                            (feature flags, endpoint URLs, credentials refs)
                            against last-known-good
         │
         ▼
   CODE AGENT (LLM + RAG) → Bitbucket: pull the exact file/function named in the
                            stack trace, AT the commit ArgoCD says is live —
                            not head-of-branch, the actual deployed version.
                            LLM reasons over that snippet + the stack trace.
         │
         ▼
   IMPACT AGENT           → Metabase: % of requests failing, by region/device/
                            time-window — real business-impact signal instead
                            of a Prometheus gauge
         │
         ▼
   MANAGER FINAL (LLM)    → Root cause narrative + suggested fix
         │
         ▼
   ── HUMAN REVIEW GATE ──
         │
         ▼
   SUGGESTED FIX           → Bitbucket PR (code) or config diff (Gitea) for a
                            human to review and merge — NOT an automated
                            restart/deploy. Production code changes always
                            go through review; only infra-level actions
                            (like the POC's container restarts) are safe to
                            automate directly.
```

This keeps the POC's core principle intact: **the LLM only narrates and suggests — every
decision point (which commit is live, what the diff is, whether to merge) stays deterministic
or human-owned.**

## Prerequisites to close before building this

1. **Correlation/request ID** threaded through logs across every service in the upload path —
   without this, the Trace Agent has nothing to isolate a single failing request by.
2. **Structured stack trace capture** in application logging — today's `logger.error(f"...
   {exc}")` pattern only logs the exception message, not the traceback. Needs
   `traceback.format_exc()` (or equivalent structured exception logging) shipped to
   Elasticsearch.
3. **ArgoCD API access** (read-only service account) to query deployment history and current
   live commit SHA per service.
4. **Bitbucket API access** (read-only) to fetch file contents at a specific commit.
5. **Gitea config repo conventions** — a clear, diffable structure for "current" vs
   "last-known-good" config per service.

## What ships automated vs. what stays human-owned

| Step | Automated? |
| --- | --- |
| Pulling logs, stack trace, deploy history, config diff | Yes — same tool-calling pattern as the POC's specialist agents |
| Root cause narrative | Yes (LLM) — same as POC |
| Restarting infra (pod, connection pool) | Yes, if that's the actual fix — same as POC |
| Code fix | **No** — opens a suggested Bitbucket PR, human merges |
| Config fix | **No** — opens a suggested config diff, human merges |

This is the same human-approval-gate principle as the POC's remediation graph, just drawn in a
different place: infra actions auto-execute after approval, code/config changes require a
human to actually author the merge.
