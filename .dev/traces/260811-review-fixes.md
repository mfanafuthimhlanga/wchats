# Trace — adversarial-review fixes

**2026-08-11 · `chore/local-postgres` · commit `d4f65e2`**
Input: `.dev/reference/260811-adversarial-review-local-postgres.md` (12 findings).
Evidence: `.dev/reference/260811-review-fix-mutation-proofs.md` (13 mutation proofs, both gate
runs, two Neon baseline checks).

## Measured, on this tree

- Integration: `15 passed, 22 skipped, 24 deselected in 109.31s` — 0 failed, 0 errors.
- Unit: `2164 passed, 13 skipped, 30 warnings in 397.83s` — 0 failed. Delta arithmetic exact.
- `INTEGRATION_TESTS_ENABLED=1 test_worker_kill.py`: `1 passed in 61.51s` — **first time ever**.
- Neon: 8/8 baseline projects present before and after. Created nothing, deleted nothing.

## Neon safety (both findings, first priority)

- **`nightly.yml` deleted by name pattern across the whole account.** Replaced with a run-scoped
  **ledger**: `tests/e2e/_neon_teardown.py` gains `record_created_project` / `ledger_ids` /
  `forget_project` / `drain_ledger`, and `python -m tests.e2e._neon_teardown` deletes only ids the
  run recorded, exiting non-zero on a survivor. The test writes the id **while polling**, not at
  teardown, because a CI timeout kills the process before any `finally` runs.
- **`test_worker_kill.py` was the last un-stubbed provisioning dispatch.** Ported onto a new
  `neon_stub_worker_factory`, which the kill-9 shape needs because it spawns two workers against
  one journal and one tenant DB.

## Decisions, where the obvious move was rejected

- **Worker id, not pid, in the stub-installed proof.** The finding asked for `pid ==
  self.proc.pid`. Measured first: `.venv/Scripts/python.exe` is a launcher shim, so the importing
  process is a child (`Popen.pid == 248` vs record `pid: 8568`). A pid check fails closed on every
  run — a different bug, not a fix. The first attempt did exactly that and was caught by running it.
- **`AsyncResult.get()`, not a longer sleep,** for the idempotency test. Waiting longer keeps the
  vacuity; only the return value is positive evidence the second task executed.
- **`_SSEStream` for the close test, not a raised threshold.** The old margin was defended against
  a number that included transport buffering. On the real stream, the loaded run (0.422s) is
  *faster* than the standalone one (0.546s), which is the tell.
- **The kill-9 redelivery is dispatched by the test.** `BROKER_VISIBILITY_TIMEOUT_S = 7200` means
  the broker's own re-queue cannot be waited for; the original 60s poll could never have passed.

## Deviation, stated plainly

I wrote an assertion that the killed message stays in kombu's `unacked` hash, and presented it as
a direct observation of `acks_late=True`. **It is a tautology**: it passed with `task_acks_late`
flipped to `False` (`1 passed in 63.89s`), because on the `solo` pool the ack is flushed by a
consumer loop a SIGKILL'd worker never returns to. Deleted rather than kept, gap written into the
test docstring and BACKLOG **1.12**.

That probe also surfaced a real leak: four orphaned `provision_neon` messages, one per run, each
armed to redeliver 2 hours later against deleted rows. Purged, and the test now purges its own.

## Also fixed

`hide_input_in_errors=True` on `Settings` (a `ValidationError` reprs the whole settings input
dict — tripped by accident twice, printing a real key's tail); two unprefixed `/api/v1` paths plus
a unit-gate scan so the next cannot hide behind a skip; DDL identifier validation in `_tenant_db`;
the docling gate's `"error" not in stdout` narrowed to the summary line.

## Backlog transaction

Deleted `1.8`, `1.9`, `1.11` (all closed and passing). Rewrote `1.1` to the measured numbers.
Added `1.12` (acks_late unobserved) and `1.13` (22 skips are unobserved).
