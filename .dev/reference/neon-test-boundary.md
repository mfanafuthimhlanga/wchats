# The Neon test boundary — where it is, why it is there, and how it is kept honest

**Date:** 2026-08-10 · **Branch:** `chore/local-postgres` · **Commit:** `115f052`
**Closes:** BACKLOG `1.7` (`test_provision.py` ×2, `test_chain.py` ×2)

---

## The diagnosis in `1.7` was wrong, and the suggested fix would have leaked money

`1.7` recorded the cause as the placeholder key at `tests/integration/conftest.py:59`
(`os.environ.setdefault("NEON_API_KEY", "test_neon_key_integration")`) and framed the choice as
"a real key, or mock the client". The placeholder was real, but it was the *second* problem. The
first is that **the mock those tests carried has never intercepted a single call**:

| | |
|---|---|
| **Wrong library** | `respx` patches `httpx`. `app/services/neon.py` and the State-B re-fetch path in `provision.py` both use **`requests`**. `httpx` is nowhere in a Neon call. |
| **Wrong process** | The mock was entered in the **pytest process**. `provision_neon` runs in the **Celery worker subprocess** started by the `celery_worker` fixture. A transport patch cannot cross a process boundary. |

So every run of these four tests was making real, unauthenticated HTTPS calls to
`console.neon.tech`. Observed, verbatim, from worker stderr before the fix:

```
provision_neon.neon_api_error ... status_code=401
detail='{"request_id":"58fd7378-658d-4398-b032-b8020b2eec95","code":"",
         "message":"supplied credentials do not pass authentication"}'
Exception: Neon API fatal 401 - chain aborted
```

**Therefore exporting the real key would not have fixed these tests — it would have turned them
into an unattended project factory.** Neither file contains any Neon teardown at all. Four
provisioning dispatches per run, each creating a real billable project, none deleted.

The env-var mechanics were confirmed rather than assumed:

```
os.environ after setdefault  == EXPORTED_SENTINEL_VALUE
settings.NEON_API_KEY is the exported sentinel: True
settings.NEON_API_KEY is the .env real key: False
```

An exported `NEON_API_KEY` survives `setdefault` and outranks `apps/api/.env` in
pydantic-settings, and `subprocess` hands it to the worker via `os.environ.copy()`. The key would
have reached `create_neon_project`. The mechanism in the brief is correct; that is exactly what
makes it dangerous.

---

## Decision: mock at the boundary (option b), for these four tests only

**Neither test asserts a property of Neon.** They assert properties of our code:

| test | what it actually asserts |
|---|---|
| `test_provision_neon_idempotency` | our idempotency guard creates exactly one project |
| `test_provision_neon_stores_encrypted_connection_string` | Fernet ciphertext lands in a BYTEA column |
| `test_full_chain_completes` | agent → `ready`, job → `complete`, tenant schema exists |
| `test_event_sequence_in_order` | six events, exact order |

Reasons the live API is the wrong instrument **here**:

1. **The idempotency test dispatches twice on purpose.** Against real Neon, the single failure it
   exists to catch — a broken guard — is the run that creates a *second* real project. The red run
   is the leaking run. That is backwards.
2. **`test_chain` needs `apply_migrations` to run the tenant chain to head.** Against Neon that is
   cold-compute warm-up plus a full DDL chain over the internet, per test. Locally it is seconds,
   and it still exercises the real Alembic chain against real Postgres with pgvector.
3. **Quota and cost.** Eight of the owner's real projects already exist on the account.
4. **CI.** A secret-gated, network-dependent test in the default gate is a test that gets muted.

Real-Neon coverage was not dropped — it already has a home at `tests/e2e/test_neon_e2e.py` behind
`-m e2e`, which `-m integration` deselects. (That file had its own defects; see below.)

---

## How it is built

`apps/api/tests/integration/_neon_stub.py`, loaded **into the worker process** with
`celery worker --include=tests.integration._neon_stub`. Celery imports `--include` modules during
start-up, before the consumer accepts a task, so the patch always precedes the first Neon call.

**Only the transport is faked** — `HTTPAdapter.send`, the last hop before the socket:

- `requests` builds the real `PreparedRequest`, real URL, real query string, real auth header.
- The response is assembled by the adapter's own `build_response` over a `urllib3.HTTPResponse`.
- `app/services/neon.py` does its real `r.ok` triage and real `r.json()` parsing.

A URL typo, a wrong param name, or a misread response shape still fails the test. Only the network
is absent.

**Fail-closed, never fall-back** — the property that separates this from a tautology:

| mechanism | effect |
|---|---|
| Unmodelled `console.neon.tech` endpoint | **raises** `NeonStubError`; never proxied to the real API |
| Other hosts | pass straight through (Postgres and Redis stay real) |
| Missing stub config at import | worker aborts at start-up |
| `wait_until_installed()` | fixture **fails the test** unless the stub reports itself installed *in the worker's own pid* |
| Worker `NEON_API_KEY` overwritten with a placeholder | even a stub that failed to load holds no credential capable of creating a project |

The stub journals every intercepted call to JSONL, which the test process reads back. That is how
`test_provision_neon_idempotency` now counts `POST /projects` — the check its docstring claimed
("Verifies respx call count") and its body never performed.

### A second defect the 401 was hiding

Both files handed `INTEGRATION_DB_URL` — **the control DB** — back as the tenant connection URI.
That could never have worked: `alembic_tenant/env.py` sets no `version_table`, so both chains use
the default `alembic_version`, and `wchats_control` already holds the control head there. Running
the tenant chain against it would fail on an unlocatable revision or corrupt control migration
state. Nobody had seen it because provisioning died at the 401 long before `apply_migrations` ran.

The `neon_stub_worker` fixture now creates a **throwaway local database per module**
(`wchats_stub_tenant_<hex>`), hands that URL back as the connection URI, and drops it in a
`finally`. The tenant chain genuinely runs to head, and `test_full_chain_completes` verifies the
tables and revision exist rather than trusting a `ready` flag.

It also replaces the fixed `time.sleep(4)` worker-readiness guess with a Celery control ping.

---

## The E2E test this decision leans on could not have run, and would have leaked

Choosing (b) puts weight on `tests/e2e/test_neon_e2e.py` being the real-Neon proof. It was not fit
for that:

1. **The teardown deleted nothing on exactly the runs that needed it.** `neon_project_id` was
   assigned at step 5, *after* `assert agent_status == "ready"`. Every failing run left the local
   variable `None`, so the `finally` deleted nothing while a real project sat in the account.
   Fixed: teardown re-reads the id from the control DB, where `provision_neon` commits it the
   moment the Neon API returns — the idempotency save point exists precisely so it survives a
   crash.
2. **Deletion went through the `neon_api` SDK inside a bare `except`.** An absent SDK meant a
   silent leak. Now `requests`, **verified** by a follow-up probe, and a leak prints
   `!!! NEON PROJECT LEAKED — delete it manually: project_id=…`.
3. **`subprocess.Popen(["celery", …])`** raised `FileNotFoundError` on every unactivated run — the
   fifth sibling of the defect `260810-local-postgres` records as fixed in `conftest.py` only.

New helpers live in `tests/e2e/_neon_teardown.py` with seven unit tests in
`tests/unit/test_neon_teardown.py`.

**The 404-probe assumption was verified against the live API**, not assumed: one throwaway project
`shiny-dew-59328379` (`wchats-teardown-probe-a0b401cc`) created and deleted through
`delete_project()`. Count 8 → 9 → 8.

**Not done:** the full `-m e2e` chain was not run against live Neon. Only the teardown path was.

---

## Mutation proofs

Every guard was mutated, run, observed red, restored from `HEAD`, run again, observed green.
Verbatim output is in the task record; the selectors and mutants:

| # | mutant | selector | red |
|---|---|---|---|
| 1 | `--include` points at a non-existent module | `test_provision_neon_stores_encrypted_connection_string` | `Failed: Neon stub worker exited with code 1 before the stub reported installed … no test may run against the real Neon API.` |
| 2 | store `result["pooled_uri"].encode()` instead of the ciphertext | same | `connection string was stored verbatim — it is not encrypted at rest` |
| 3 | direct URI fetched with `pooled=true` (Pitfall 1) | same | `expected both pooled and direct connection_uri fetches, got {'true'}` |
| 4 | `run_tenant_migrations` returns immediately | `test_full_chain_completes` | `schema_version must be recorded once migrations complete` |
| 5 | idempotency contract removed (state-A guard, job guard, state-B branch) | `test_provision_neon_idempotency` | `the stub recorded 2: ['stub-proj-dd536d1de8ba', 'stub-proj-7b7195ed3a2d']` |
| 6 | `create_neon_project` calls an unmodelled endpoint | `test_provision_neon_stores_encrypted_connection_string` | `NeonStubError: … no route for GET …/operations. The stub never proxies to the real API` |

**Proof 4 is worth reading twice.** Under a no-op migration the agent still reached `ready`, the
job still reached `complete`, and all six events still fired — the three assertions the old test
had. It failed only on the assertions added here. The old `test_full_chain_completes` could not
tell a working migration from one that did nothing.

**Proof 5 is the argument for option (b) restated as evidence.** That mutant creates a second
project. Against the live API it would have created — and leaked — a second *real* one, on the
run where the suite was doing its job.

---

## Results (observed, this machine)

```
Integration: 2 failed, 13 passed, 22 skipped, 24 deselected in 157.03s
             (baseline 10F/9P/21S; four closed here, four in fe45291)
Unit gate:   2120 passed, 12 skipped in 426.20s
```

Remaining integration failures are `1.8` (`test_query_route`) and `1.9` (`test_sse`) — not this
work.

## Neon account

| | |
|---|---|
| Before | 8 projects, exact name match with `C:/Users/Bantu/pg-setup/neon-baseline.txt` |
| Created | `shiny-dew-59328379` (teardown verification only) |
| Deleted | `shiny-dew-59328379` — the only project this run created |
| After | 8 projects, exact name match; none missing, none extra |

No project the run did not create was ever passed to a delete call. The stubbed integration tests
create nothing: the worker they run in holds a placeholder key and a transport that refuses to
reach the network.

---

## Rules for the next person

1. **A mock in the pytest process does nothing to a Celery worker subprocess.** Anything a task
   touches must be stubbed inside the worker (`--include`), or not at all.
2. **`respx` is for `httpx`. The Neon client is `requests`.** They do not overlap.
3. **A stub that can silently fall back to the network is worse than no stub.** Prove installation
   in the process under test, and refuse to run without it.
4. **Never hand the control DB back as a tenant connection URI.** Both Alembic chains share
   `alembic_version`.
5. **Teardown reads the resource id from the database, not from a local variable** the failing
   path never assigned.
