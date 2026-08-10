# SSE live-event delivery: what was actually wrong

Task D of the integration-failures workflow, 2026-08-10/11, branch `chore/local-postgres`.
Target: `apps/api/tests/integration/test_sse.py::test_sse_receives_live_events_after_replay`.

## Verdict

**The product was not at fault and is unchanged.** `app/services/sse.py` is byte-identical to
what it was before this work (mutated twice for proofs, restored from HEAD both times).

The test was emitting events that were not events. It published to `job_events:{job_id}` and
never wrote a `job_events` row. `sse.py` deliberately reads event *data* from the DB only and
treats a pub/sub message purely as a signal to re-query early — its module docstring (sse.py:16-24)
states this and gives the reason: Redis pub/sub is fire-and-forget, so a message published while
the listener is between `listen()` calls is gone permanently, and the DB is the only durable
record. A publish with no row behind it is a doorbell at an empty house. The re-query found
nothing new, the terminal event never arrived, and the Phase-2 loop emitted keepalives until the
30s bound fired.

This is why the two earlier runs sat for 10 and 40 minutes: the stream was healthy and doing
exactly what it was told, forever.

## The probe that settled it

Rather than reason from the code, a probe drove the app directly and timestamped every chunk
(deleted after use; reproduced in the collector now living in the test file).

Timeline, three emissions on one job:

```
[  8.38s] START status=200
[  8.41s] LINE 'event: job.started'          <- DB replay
[  8.41s] replay observed -> emitting live
[  8.55s] emitted neon.project.creating       (insert + publish)
[  8.56s] LINE 'event: neon.project.creating' <- delivered 10ms later
[  8.70s] emitted job.complete                (insert + publish)
[  8.72s] LINE 'event: job.complete'
[  8.72s] STREAM ENDED
```

An earlier probe variant additionally published an event with **no** DB row. It never appeared.

Two conclusions, both measured:

1. Publish-without-persist is never delivered. That is the design, working.
2. The pub/sub wake-up is **healthy and fast** — 10ms, against a `POLL_INTERVAL_S` of 3.0s. It was
   never the suspect. Delivery latency was never the problem; there was nothing to deliver.

## Three things had to change in the test

### 1. Emit the way production emits

`_emit_live()` now does what `app/services/events.py:emit` does at every Celery checkpoint —
persist the row **and** publish the message. That alone makes the stream terminate.

### 2. Stop sleeping; wait for the stream's actual progress

The publisher slept 0.5s and then published. That was a race it was losing badly. The request was
still 7.6s deep in argon2 when the publisher fired (see below), so it published into a channel
with **no subscriber yet** and Redis discarded the message. Even a correct emit would have been
lost. Lengthening the sleep would have converted a race into a slow race.

The publisher now blocks until the server has *written* the replay event, then emits. No duration
anywhere in the test. Every emission is provably post-replay: the Phase-1 `SELECT` has already run
against a snapshot that cannot contain a row committed after it.

Reaching that signal meant **not using httpx**. `httpx.ASGITransport` (0.28.1,
`httpx/_transports/asgi.py:128-187`) accumulates every `http.response.body` message into
`body_parts` and builds the `Response` only *after* `await self.app(...)` returns. For a JSON
route that is invisible; for an SSE route the server holds open on purpose, the client sees
nothing until the stream closes. Measured: every line of a three-event stream arrived at the same
instant, 0.27s after the terminal event, including the replay event written 5 seconds earlier.

`_SSEStream` drives the ASGI app directly with its own `send`. Nothing is stubbed — routing, auth,
`EventSourceResponse` and `event_generator` all run exactly as under uvicorn. ASGITransport itself
does no more than this, minus the buffering. Cost: one dict of ASGI scope.

**Worth knowing generally: no test in this repo can observe SSE timing through httpx.** Any future
test that needs to see a stream progress must use `_SSEStream` or a real server.

### 3. The argon2 cost was a fixture defect

`_setup_test_job` inserted a tenant with `api_key_hash` but a NULL `api_key_prefix`. Every
production writer of a tenant row sets it — `app/api/v1/tenants.py:40`, and both branches of
`app/api/v1/webhooks.py` (:102, :191) — so that row was not a realistic row.

The consequence is not cosmetic. `get_current_tenant` (`app/api/deps.py:170-190`) looks the prefix
up on an index and runs **one** argon2 verify; a NULL prefix drops the request into the legacy
fallback that scans every prefix-less tenant and runs argon2 against each. argon2 is deliberately
expensive and entirely synchronous, so the scan **blocks the event loop** — 7.6s across the 13
tenant rows in the local control DB, all of it before the generator reached its Redis subscribe.

Fixed by populating the prefix. Side effect worth flagging to whoever owns it:
**`test_sse_closes_on_completed_job` was failing for this reason and now passes** — it asserts
closure within 5s and was taking 6.8s, essentially all of it argon2. Its assertion was correct and
is unchanged; only the fixture moved. Closes in ~0.3s now.

## Two guards keep the test non-vacuous

`event_generator` has two ways to notice a new row: the pub/sub wake-up, and a fallback DB poll
every `POLL_INTERVAL_S`. With the emit fixed, the poll alone would deliver every event and the
test would pass while proving nothing about the mechanism the architecture rests on.

- **`POLL_INTERVAL_S` is monkeypatched to 10x the stream bound** (300s vs a 30s ceiling). The
  fallback cannot fire inside the window the test will wait, so the only path to a terminating
  stream is the pub/sub wake-up. Break the wake-up and the test *fails* rather than slows. A
  precondition assert pins the relationship so the isolation argument cannot silently invert.
- **A decoy** (`DECOY_UNPERSISTED_EVENT`) is published with no row behind it and asserted never to
  arrive — pinning the contract the old test had exactly backwards.

## Mutation proofs

All four: mutate, run, observe red, `git checkout HEAD -- <file>`, run, observe green.
Selector for 1-3:

```
.venv/Scripts/python.exe -m pytest \
  "tests/integration/test_sse.py::test_sse_receives_live_events_after_replay" \
  -m integration -q --no-header -p no:cacheprovider
```

| # | Mutation | Red | Green |
|---|---|---|---|
| 1 | `sse.py:_next_pubsub_message` — prepend `await asyncio.sleep(86400)` (wake-up dead, poll is the only path) | `E TimeoutError` / `1 failed in 71.05s` | `1 passed in 39.75s` |
| 2 | `sse.py` Phase-2 — capture the pub/sub message and yield its payload as an event | `AssertionError: Unexpected event sequence: ['job.started', 'decoy.published.but.never.persisted', 'neon.project.creating', 'neon.project.creating', 'job.complete']` / `1 failed in 35.83s` | `1 passed in 37.90s` |
| 3 | `_emit_live` — drop the `_insert_events` call, publish only (the original defect) | `E anyio.WouldBlock` / `E asyncio.exceptions.CancelledError` / `E TimeoutError` / `1 failed in 72.35s` | `1 passed in 38.86s` |
| 4 | `_setup_test_job` — `api_key_prefix: None` (back to the legacy argon2 scan). Selector: `::test_sse_closes_on_completed_job` | `AssertionError: SSE stream took 5.5s to close - expected < 5s` / `1 failed in 66.29s` | `1 passed in 36.55s` |

Mutation 2 is the sharpest: the decoy was delivered **and** `neon.project.creating` arrived twice,
once from the pub/sub payload and once from the DB — precisely the duplication the DB-as-truth
design exists to prevent.

Mutation 4 depends on there being prefix-less tenants to scan (13 at time of measurement,
`select count(*) filter (where api_key_prefix is null) from tenants`). If that table is ever
cleaned, the mutation stops going red — the guard is real but its *proof* is environment-dependent.

## Gate results

- Integration: `15 passed, 22 skipped, 24 deselected in 237.91s (0:03:57)` — 0 failed, 0 errors.
  Baseline at task start was 10 failed / 9 passed. The other eight fixes are other agents' commits
  on this branch (`fe45291`, `115f052`, `0e2efe7`, `4164fe6`); mine are the two SSE tests.
- Unit gate: `2127 passed, 12 skipped, 30 warnings in 455.13s (0:07:35)` — 0 failed. Baseline was
  2112 passed; the extra 15 are other agents' new tests. No regression.
- Neon: nothing created, nothing deleted. Listed `GET /api/v2/projects` after the run — 8 projects,
  exactly the 8 in `C:/Users/Bantu/pg-setup/neon-baseline.txt`, no extras.

## Not done

- `test_sse_replays_prior_events` takes ~47s. That is import/warm-up cost landing on the first test
  in the file, not stream latency (it does no live emission at all). Not investigated.
- Tests A and C still read through the buffered httpx transport. Correct for what they assert —
  neither cares *when* a line arrives, only that it does — so they were left alone. If either ever
  grows a timing assertion it must move to `_SSEStream`.
