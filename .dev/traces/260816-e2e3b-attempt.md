# Trace: E2E-3b attempted, unobserved — the tenant cold-start pathology (7.14, 7.15)

Goal was one live turn under full judge context with `RETRIEVAL_FAITHFULNESS_SAMPLE_RATE=1.0`.
**No verdict was produced.** What the attempt bought instead is a production defect on the
founding architecture, located and fixed the same day.

## Setup that worked

- Local stack: control DB + broker overridden to localhost (`.env` points CONTROL_DB at live Neon
  and REDIS at Upstash — the local-run overrides are mandatory, and the precheck refusal that
  caught the Neon URL is worth keeping as a pattern).
- Queues verified empty before the costed run (the stale-task hazard from E2E-3).
- Agent `e2e1-probe-agent` (`c14d13a1`), status `ready`; widget path drove the turn with no key.

## What happened, three jobs

1. `cbdc378b`: task received, `psycopg2.connect(conn_str, connect_timeout=5)` at
   `agent.py:1499` walked six resolved addresses at 5s each against a suspended Neon endpoint,
   all timed out, task raised — **zero job_events emitted. The customer-facing result of a cold
   tenant is a silently dead job.** No retry: OperationalError was not retryable.
2. `fbf8d10e`: identical, even though the endpoint's compute finished waking 8s into the attempt
   (Neon operations log: `start_compute finished 09:58:47Z`, task started 09:58:39).
3. `b2209bac` after explicit pre-warm: the turn RAN — thinking, two parallel tool calls (the
   `5.21` shape, first time observed live), retrieval executed — but vector and bm25 searches took
   **60-90s each** against the tenant endpoint, the endpoint's 5-minute suspend cycled mid-turn,
   a TimeoutError triggered the task's existing retry, and the retry ground the same path. Aborted
   after 25+ minutes; all three jobs marked `failed` with the cause, queues purged.

## Diagnosis chain

TCP to the endpoint connects even when suspended (the proxy accepts and holds while waking), so
`Test-NetConnection` lies about readiness. A bad-credential psycopg2 probe is the honest check:
2.2s `password authentication failed` = warm; >5s timeout = waking. Wake ~8-20s. `connect_timeout=5`
can never survive it, and psycopg2 burns the budget per address (3 IPv6 + 3 IPv4).

## Consequences

- `7.14` (fixed same session): connect timeout named and raised, OperationalError made retryable
  in the wake window, terminal failure emits `agent.failed` with `error_type`.
- `7.15` (open): the 60-90s per-query latency and mid-turn suspend churn are an environment
  question this fix does not answer — E2E-3b needs a rerun, and if latency persists, the v4/v6
  path and a pre-warm on widget `config` are the candidates.
- DeepSeek was NOT the problem: the agent loop ran and produced tool calls through the endpoint.

## Not established

Everything E2E-3b exists to establish: no verdict under full context, `5.13`/`5.15` still open,
`citation_coverage` still never non-NULL.
