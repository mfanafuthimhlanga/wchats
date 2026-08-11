# Review-fix pass, 2026-08-11 — measurements and mutation proofs

Fixes for the 12 findings of `.dev/reference/260811-adversarial-review-local-postgres.md`,
on `chore/local-postgres`. Code commit `d4f65e2`.

Every number below is from a run performed here. Nothing is estimated, and the one
guard that turned out to be a tautology was **deleted**, not kept for the shape of it.

---

## 1. Gate runs (verbatim final lines)

Integration, from `apps/api`, with `INTEGRATION_DB_URL=postgresql://wchats:wchats@localhost:5432/wchats_control`
and `REDIS_URL=redis://localhost:6379/0`, `INTEGRATION_TESTS_ENABLED` **unset**:

```
.venv/Scripts/python.exe -m pytest tests/integration -m integration -q --no-header -p no:cacheprovider
15 passed, 22 skipped, 24 deselected in 109.31s (0:01:49)
```

Unit:

```
.venv/Scripts/python.exe -m pytest tests/unit -q --no-header -p no:cacheprovider \
  --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py
2164 passed, 13 skipped, 30 warnings in 397.83s (0:06:37)
```

**Delta arithmetic, exact — so no pre-existing test changed status.** Baseline
2127 passed / 12 skipped. New tests: `test_neon_teardown.py` 7 → 21 (+14),
`test_tenant_db_identifier_guard.py` +18, `test_config_error_redaction.py` +3,
`test_test_route_paths_resolve.py` +2 passed and +1 skipped (the parametrised
`_NOT_APP_ROUTES` pin, empty by design). 2127 + 14 + 18 + 3 + 2 = **2164**;
12 + 1 = **13**.

Additionally, and for the first time in repo history, the kill-9 test RUNS:

```
INTEGRATION_TESTS_ENABLED=1 pytest tests/integration/test_worker_kill.py -m integration
1 passed in 61.51s (0:01:01)
```

## 2. Neon safety

Read-only `GET https://console.neon.tech/api/v2/projects`, key read from
`apps/api/.env` in-process, never printed. Checked **before** any work and
**after** the final suites:

```
HTTP 200 / live project_count = 8 / BASELINE_COUNT=8
MISSING_FROM_LIVE=[] / EXTRA_NOT_IN_BASELINE=[] / BASELINE_INTACT=YES / LEAK=NONE
```

Identical ids both times: `dark-snow-18891572`, `round-king-00493014`,
`nameless-fog-19651218`, `floral-bar-83436685`, `morning-math-61244033`,
`gentle-cell-49949671`, `cool-pond-11127703`, `dry-band-71216365`.

**I created no Neon project and deleted no Neon project.** No test in this pass
reached the Neon API: the integration provisioning tests run against
`tests/integration/_neon_stub.py` inside the worker, and `-m e2e` is deselected.

---

## 3. Mutation proofs

Protocol: mutate, RUN, observe red, restore, RUN, observe green. Restores after
commit `d4f65e2` are `git checkout HEAD -- <path>`, verified with
`git status --porcelain`.

### M1 — the stub-installed proof is scoped to THIS worker
Selector: `pytest tests/integration/test_provision.py::test_provision_neon_idempotency -m integration`
Mutation: `tests/integration/_neon_stub.py`, `"worker_id": os.environ[_ENV_WORKER_ID]`
→ `"worker_id": "a-different-worker-that-once-installed-the-stub"`.

RED:
```
E  Failed: tests.integration._neon_stub never reported installed for worker
   neonstub-df0659cf within 180.0s (worker still alive, journal ...neon_calls.jsonl
   holds [{'event': 'installed', 'pid': 5004, 'ts': '...', 'worker_id':
   'a-different-worker-that-once-installed-the-stub'}]). Refusing to run — an
   un-stubbed worker would call the real Neon API.
1 error in 181.87s (0:03:01)
```
GREEN after restore: `1 passed in 33.62s`

**Why a worker id and not the pid the finding asked for.** Measured first:
`.venv/Scripts/python.exe` on Windows is a launcher shim that re-spawns the real
uv-managed interpreter, so the process importing the stub is a *child* of the one
`Popen` returned. Process table captured live:
```
ProcessId ParentProcessId cmd
      248           11920 ...\.venv\Scripts\python.exe -m celery -A app.worker...
     8568             248 ...\uv\python\cpython-3.12...\python.exe -m celery -A app.worker...
RECORDS=[{'event': 'installed', 'pid': 8568, ...}]   POPEN_PID=248
```
`pid == self.proc.pid` is unconditionally false here — it would have failed
closed on every run, which is a different bug rather than a fix. First attempt at
it did exactly that: `Failed: ... never reported installed in pid 12756 ... holds
[{'event': 'installed', 'pid': 9792, ...}]`.

### M2 — the idempotency test is no longer satisfiable by a task that never ran
Selector: `pytest tests/integration/test_provision.py::test_provision_neon_idempotency -m integration`
Mutation: second dispatch `queue="pipeline"` → `queue="mutant_queue_no_consumer"` —
the reviewer's own mutation, which under the old `time.sleep(5)` form produced
`1 passed in 59.55s`.

RED: `1 failed in 155.26s (0:02:35)` — `second.get(timeout=120)` never returns,
because nothing consumes that queue.
GREEN after restore: `1 passed in 34.67s`

Housekeeping: the mutation left one Redis list behind.
`purged mutant_queue_no_consumer: 1 / remaining keys matching mutant*: []`

### M3 — the SSE bound now covers the emitter task
Selector: `timeout --signal=KILL 150 pytest tests/integration/test_sse.py::test_sse_receives_live_events_after_replay -m integration`
Mutation: `_SSEStream(... {"x-api-key": raw_key})` → `{"x-api-key": "bogus-key-forces-401"}`,
so the stream closes having written zero `event:` lines and the emitter's
`wait_for_events(1)` can never be satisfied.

RED — but **bounded**, which is the whole point:
```
E   raise TimeoutError from exc_val
E   TimeoutError
FAILED tests/integration/test_sse.py::test_sse_receives_live_events_after_replay
1 failed in 58.13s
=== EXIT_CODE=1  ELAPSED=64s ===
```
The reviewer's measurement of the same mutation before the fix was
`EXIT_CODE=137  ELAPSED=155s` — killed externally, no summary printed. 58.13s is
the 30s bound plus ~28s of `app.main` import.

GREEN after restore: `1 passed in 27.43s` / `EXIT_CODE=0 ELAPSED=35s`

### M4 — nightly.yml cannot go back to deleting by name
Selector: `pytest tests/unit/test_neon_teardown.py`
Mutation: replaced the id-scoped teardown step with the old sweep
(`client.projects()` + `p.name.startswith('vrd-')` + `project_delete`).

RED: `6 failed, 15 passed in 1.16s` — all five forbidden-token parametrisations
plus `test_teardown_goes_through_the_id_scoped_helper`.
GREEN after restore: `21 passed in 0.46s`

Note the guard strips whole-line YAML comments before scanning, so the workflow's
prose explanation of the sweep it no longer performs does not read as the
violation — the failure mode this repo already hit once in the docling gate.

### M5 — a leaked project fails the test instead of printing
Selector: `pytest tests/unit/test_neon_teardown.py::TestE2ETeardownIsLoud`
Mutation: `pytest.fail(...)` → `print(...)` in `test_neon_e2e.py`'s
`delete_project` handler.

RED:
```
E  AssertionError: the delete_project handler does not call pytest.fail. A print
   here is swallowed by pytest's stdout capture on any run that is otherwise
   green — which is precisely the run where a silent leak matters.
1 failed, 1 passed in 0.79s
```
GREEN after restore: `2 passed in 0.23s`

### M6 — the ledger is written while polling, not at teardown
Selector: `pytest tests/unit/test_neon_teardown.py::TestE2ETeardownIsLoud`
Mutation: removed `record_created_project(row[1])` from the status-polling loop
and re-added it beside `resolve_project_id` in the `finally`.

RED:
```
E  AssertionError: the ledger is written no earlier than teardown. A CI timeout
   kills the process before any `finally` runs...
E  assert 350 < 348
```
GREEN after restore: `2 passed in 0.24s`

### M7 — settings errors do not echo secrets
Selector: `pytest tests/unit/test_config_error_redaction.py`
Mutation: `hide_input_in_errors=True` removed from `Settings.model_config`.

RED: `3 failed in 1.02s`, including
```
E  'not-an-int' is contained here:
E    ut_value='not-an-int', input_type=str]
```
GREEN after restore: `3 passed in 0.40s`

The original leak was reproduced by accident while probing worker start-up: a
`Settings()` built without `PLATFORM_CREDENTIAL_KEY` printed
`Field required [type=missing, input_value={'NEON_API_KEY': 'stub-ke…<tail of a real key from apps/api/.env>'}]`.
The real fragment is deliberately not reproduced here.

### M8 — the database name is validated before any connection
Selector: `pytest tests/unit/test_tenant_db_identifier_guard.py`
Mutation: `if not _SAFE_DB_NAME.match(db_name):` → `if False:`.

RED: `16 failed, 2 passed in 16.36s`
GREEN after restore: `18 passed in 0.28s`

**Care note, recorded because it was a real risk I took.** With the check
disabled, the hostile names reached the live local Postgres as DDL — including
`x"; DROP DATABASE wchats_control; --`. PostgreSQL refused them (`CREATE`/`DROP
DATABASE` cannot run inside the implicit transaction of a multi-statement simple
query), which is why the tests went red on `ProgrammingError` instead of
`ValueError`. Inventory verified immediately afterwards:
```
postgres / template0 / template1 / wchats_control / wchats_tenant_probe
```
Nothing dropped, nothing created. The right mutation would have targeted
`_checked` in isolation; a future proof of this guard should.

### M9 — the docling gate still catches a real collection error
Selector: `pytest tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_skips_when_docling_is_absent`
Mutation: `import definitely_no_such_module_xyz` at line 1 of the gated file.

RED:
```
E  ERROR tests/integration/test_ingestion_chain.py
E  !!!!!! Interrupted: 1 error during collection !!!!!!
E  assert 2 != 2   (returncode)
1 failed in 6.42s
```
GREEN after restore: `1 passed in 6.44s`

**And the false positive the narrowing removes, measured directly.** With a
`test_chain_error_path` added to the gated file:
```
--- ABSENT  rc=5  stdout tail: '\nno tests collected in 1.27s\n'
    OLD CHECK ("error" in whole stdout): False   NEW CHECK (summary line): False
--- PRESENT rc=0  stdout tail: '...::test_chain_error_path\n\n5 tests collected in 1.20s\n'
    OLD CHECK ("error" in whole stdout): True    NEW CHECK (summary line): False
```
So the old form is not reachable *today* through the absent direction — the
module-level skip means no ids are printed. It becomes reachable the moment the
gate stops firing, which is the failure this guard exists to report: the old
check would then have said "collection errored rather than skipping", naming a
cause that did not happen, while the correct per-name assertion two lines below
named the real one. Probe reverted; `8 passed in 25.06s`.

### M10 — the premise of the kill-9 test is enforced
Selector: `INTEGRATION_TESTS_ENABLED=1 pytest tests/integration/test_worker_kill.py -m integration`
Mutation: kill point moved from `neon.project.ready` to `job.complete`, so the
chain has finished before the worker dies and the resumption is never exercised.

RED:
```
E  AssertionError: the chain completed before the kill landed, so nothing was
   interrupted and the resumption below proves nothing. This is a failed premise,
   not a passing test — rerun, or move the kill earlier...
E  assert 'ready' != 'ready'
1 failed in 34.81s
```
GREEN after restore: `1 passed in 61.51s`

### M11 — the SSE close budget is a live assertion
Selector: `pytest tests/integration/test_sse.py::test_sse_closes_on_completed_job -m integration`
Mutation: `SSE_CLOSE_BUDGET_S = 5.0` → `0.001`.

RED:
```
E  AssertionError: SSE stream took 0.50s to close — expected < 0.001s with the
   terminal event already in the DB
E  assert 0.5 < 0.001
1 failed in 27.87s
```
GREEN after restore: `1 passed in 24.38s`

### M12 — an unprefixed route path is caught even in a skipped file
Selector: `pytest tests/unit/test_test_route_paths_resolve.py`
Mutation: `f"/api/v1/agents/{agent_id}/chat"` → `f"/agents/{agent_id}/chat"` in
`test_agent_chat_integration.py`, a module that never collects without
`INTEGRATION_TESTS_ENABLED=1`.

RED:
```
E  AssertionError: These test call sites address paths the app does not mount...
E      tests\integration\test_agent_chat_integration.py:175  /agents/{}/chat
1 failed, 1 passed, 1 skipped in 29.98s
```
GREEN after restore: `2 passed, 1 skipped in 24.70s`

The nine paths the scan governs today, all resolving:
```
test_agent_chat_integration.py:175  /api/v1/agents/{}/chat
test_agent_e2e.py:35                /api/v1/agents/{}/chat
test_agent_e2e.py:51                /widget/jobs/{}/events
test_deploy_gate_redteam.py:307     /api/v1/agents/{}/approve-deployment
test_query_route.py:249/351/403     /api/v1/agents/{}/query
test_query_route.py:417             /api/v1/agents/{}/queries
test_sse.py:460                     /api/v1/jobs/{}/events
```

---

## 4. A guard I wrote, measured, and then DELETED

`test_worker_kill.py` briefly asserted that the killed message was still in
kombu's `unacked` hash, presented as a direct observation of `acks_late=True`.
It is a tautology on this configuration. Measured twice:

| mutation | result |
|---|---|
| `provision.py` `acks_late=True` → `False` | `1 passed in 63.34s` |
| `celery_app.py` `task_acks_late=True` → `False` (global) | `1 passed in 63.89s` |

Probe of the hash itself during the second run:
```
PROBE unacked_total=4 matching=1
PROBE entry: [{"body": "W1siYmEzZTc3ZDAt...", ... "chain": [{"task": "app.worker.tasks.pipeline.migrations.apply_migrations", ...
```
On the `solo` pool the ack — early or late — is flushed by the consumer loop, and
a mid-task SIGKILL means the worker never returns to it. The Redis-side entry
therefore survives under both settings. The assertion was removed and the gap
written into the test's docstring and BACKLOG 1.12, because a guard that has
never been seen to fail is indistinguishable from a comment.

**It also surfaced a leak I then fixed.** `unacked_total=4` was four orphaned
`provision_neon` messages, one per run of this test. `visibility_timeout` is
7200s (`celery_app.py:75`), so each would have been redelivered two hours later
against a tenant whose rows the teardown had already deleted. Purged by hand
(`unacked entries before: 4 → after: 0`, `unacked_index 4 → 0`) and the test now
purges its own in the `finally`; verified after the next run:
`unacked entries after the run: 0 | unacked_index: 0`.

---

## 5. What is still not proven

- **`acks_late` and `task_reject_on_worker_lost` are observed by nothing.** See §4
  and BACKLOG 1.12. Proving them needs either a non-solo pool or a broker whose
  `visibility_timeout` a test can lower.
- **The kill-9 redelivery is dispatched by the test, not by the broker.** A
  7200s visibility timeout cannot be waited out. What is proven is the resumption
  and the idempotency guard across an unrecoverable death.
- **22 integration tests still skip**, and a skip is unobserved. `test_worker_kill.py`
  is now the one member of that set proven to pass when its flag is set.
- **The nightly ledger has never run in CI**, because CI is capped (BACKLOG 0.3).
  Its helpers are unit-proven and its workflow wiring is pinned by a guard; the
  end-to-end reclamation is not observed.
- **`test_neon_e2e.py` itself has never been executed here.** Its teardown
  changes are proved by AST guard and by unit tests of the helpers, not by a real
  Neon run.
