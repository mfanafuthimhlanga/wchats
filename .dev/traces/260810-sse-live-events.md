# Trace — SSE live-event delivery (BACKLOG 1.9, 1.11)

**2026-08-10/11 · `chore/local-postgres` · commits `79601cd`, `18f2c26`**
Written 2026-08-11 to close the trace gap the adversarial review flagged. Reconstructed from
`.dev/reference/sse-live-event-delivery.md`; a record, not a fresh investigation.

## Verdict: test defect. `app/services/sse.py` is byte-identical.

The test published to `job_events:{job_id}` and never wrote a `job_events` row. `sse.py`
deliberately reads event *data* from the DB only and treats pub/sub as a wake-up signal to
re-query early (sse.py:16-24) — because Redis pub/sub is fire-and-forget, so a message published
while the listener is between `listen()` calls is gone permanently. A publish with no row behind
it is a doorbell at an empty house: the re-query found nothing, the terminal event never arrived,
and the loop emitted keepalives until the 30s bound fired. That is why two earlier runs sat for
10 and 40 minutes — the stream was healthy and doing exactly what it was told, forever.

## What changed

- `_emit_live()` — persist **and** publish, exactly as `app/services/events.py:emit` does.
- `_SSEStream` — drives the ASGI app directly instead of `httpx.ASGITransport`, which buffers the
  whole response and only builds it after the app returns. That buffering removed the only signal
  a test could synchronise on, so the old test guessed with `sleep(0.5)` while the request was
  still 7.6s deep in argon2.
- `_setup_test_job` populates `api_key_prefix`, because a NULL prefix drops the request into a
  legacy fallback that runs argon2 against every prefix-less tenant — measured at 7.6s of
  event-loop blocking, all of it before the generator reached its Redis subscribe. That was
  BACKLOG 1.11's load-flake, not a separate defect.
- `POLL_INTERVAL_S` monkeypatched to 10x the stream bound, so the DB-poll fallback cannot fire
  inside the test's own window and a pass isolates the pub/sub path.
- A decoy event, published but never persisted, asserted never to arrive.

Measured timeline from the probe that settled it: replay at +0.03s, live event delivered 10ms
after emission, stream ended 0.34s after connect.

## What it missed, found by the 2026-08-11 review

`await emitter_task` was placed **outside** `asyncio.timeout(...)`. Any early close leaves the
emitter parked forever on a Condition nothing will notify again — the same hang the commit
message was about. Proven: with a bogus api key the test survived an external SIGKILL at 150s.
Fixed at `d4f65e2` (inside the bound, cancelled in the `finally`); now `1 failed in 58.13s`,
`EXIT_CODE=1`, under the same mutation.

`test_sse_closes_on_completed_job` also still read through the buffered transport, so its
`elapsed < 5.0` measured response buffering rather than close latency. Moved onto `_SSEStream`:
0.546s standalone, 0.422s under the full suite.
