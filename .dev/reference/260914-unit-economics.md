# What one customer turn costs, read from the ledger

The measured cost of serving one customer message on staging, and where the platform's
stored cost figures stop short of it. For anyone reading `turn_metrics.cost_usd`,
`cost_per_session` on `GET /agents/{id}/metrics`, or `tenant_usage_daily`.

All figures are from the Bantuson agent's tenant database on staging, read 2026-09-14,
priced with book `2026-08-23.1` and fx `usd_zar-2026-08-24` (R16.02 per dollar). Every
call in the window was served by `openai/gpt-5.6-luna`.

## One turn, job `33c926d7`, 2026-09-13 13:09 UTC

| purpose | calls | input tok | output tok | USD |
|---|---|---|---|---|
| agent_turn | 2 | 943 | 128 | 0.0010918 |
| gatekeeper | 1 | 361 | 61 | 0.0001454 |
| auditor | 1 | 1309 | 202 | 0.0005042 |
| strategist | 1 | 437 | 69 | 0.0001702 |
| **whole turn** | 5 | | | **0.0019116** |

`turn_metrics.cost_usd` for this job is 0.0010918. That is the `agent_turn` rows alone,
57% of what the turn cost.

## The day, five turns across two conversations

| purpose | calls | USD |
|---|---|---|
| agent_turn | 10 | 0.0079498 |
| gatekeeper | 5 | 0.0009304 |
| auditor | 5 | 0.0048736 |
| strategist | 5 | 0.0010556 |
| judge_retrieval_faithfulness | 6 (2 turns sampled) | 0.0061714 |
| **all five turns** | 31 | **0.0209808** |

The stored sum of `turn_metrics.cost_usd` over the same five turns is 0.0079498, 38% of
the real figure. A sampled turn carries three faithfulness calls at about 0.003, more
than its agent turn. Per turn, whole: about 0.0042 USD, about 7 South African cents.

## How the figures are produced today

- Every model call lands in the tenant's `model_calls` table with a `purpose` and the
  turn's `job_id`. The three validators and the faithfulness judge bill to the same
  `job_id` as the agent turn (`validators.py:_ledger_for`, `retrieval_eval.py:_turn_ledger`),
  so the whole turn is one `WHERE job_id = ...`.
- `_turn_cost_usd` in `agent.py` prices only the calls the agent loop teed into
  `AgentTurn.calls`, then writes that to `turn_metrics.cost_usd` at the end of the agent
  task. The judges have not run yet at that moment; they are dispatched on a Celery chain
  after the write.
- `compute_agent_metrics` divides `SUM(turn_metrics.cost_usd)` by distinct conversations,
  so `cost_per_session` inherits the same 38 to 57% undercount.
- `rollup_model_calls_beat` (`usage.py`) prices every tenant's ledger for the closed CAT
  day into the control table `tenant_usage_daily`, one row per (tenant, purpose, day).
  It is correct for the days the beat ran, and it is the only place judge cost is summed.
  It is read by nothing: no route, no MCP tool.

## What is missing

1. **A whole-turn figure at the turn grain.** Nothing joins the judges back to the turn
   after they run. The fix is a read, not a write: price `model_calls WHERE job_id`
   at read time, since the ledger already holds every call. A column written at agent
   time can never include the judges.
2. **Per conversation and per agent per day.** Only per tenant per purpose per day
   exists, and only in the control DB, and only if the beat ran.
3. **Anything a person can read.** `tenant_usage_daily` reaches no route and no MCP
   tool. On staging its newest rows are 2026-09-05, eight days before the ledger's newest
   call. The beat fires at 00:30 UTC and needs `worker-runtime` up at that minute; a
   missed day is backfilled only by hand through the `day` override (#257 covers
   scheduled jobs on a parked staging).
4. **Embeddings.** Voyage calls for retrieval and ingestion write no `model_calls` row.
   `PURPOSE_ROUTES` has no embedding purpose and the ledger hook wraps the OpenAI client
   only. Retrieval cost per turn is unknown, never zero.
5. **The four `scenario_generation` rows with a NULL `job_id`** are billed to the tenant
   but to no run. Any per-job rollup misses them, and a per-agent read misses any row
   whose `agent_id` is NULL; the daily rollup misses neither.

## Reading it yourself

The tenant DSN is the agent's `neon_connection_string` on the control DB, decrypted with
`NEON_ENCRYPTION_KEY`. Both come from `railway variables -s api-service -e staging --kv`,
written to a file first (the CLI and a heredoc share stdin). Then:

```sql
SELECT purpose, count(*), sum(input_tokens), sum(output_tokens)
FROM model_calls WHERE job_id = '33c926d7-9fde-42ea-8860-9043b17bba53'
GROUP BY purpose;
```

Price rows with `app.domain.pricing.cost_usd(ModelCall)`; it raises `UnknownPrice` rather
than returning zero for a model the book does not know.
