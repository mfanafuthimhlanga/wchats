# TRACE — a local PostgreSQL, and the four defects it immediately found

**Branch:** `chore/local-postgres`. **Date:** 2026-08-10. Plan: `.dev/plans/260810-local-postgres.md`.

---

## What is now running

**PostgreSQL 17.6, localhost:5432**, binaries at `C:\Users\Bantu\pgsql`, cluster at
`C:\Users\Bantu\pgdata`. No admin, no service, no registry — removal is deleting those two
directories. 17.6 because `ci.yml` and `nightly.yml` both run `postgres:17-alpine`.

Redis was **already** installed and answering `PONG` on 6379 — HANDOFF never said so.

**Port note.** Started on 5433 out of caution, then moved to **5432**: every default in the test
corpus points at 5432 (`tests/conftest.py:45,49`, `tests/integration/conftest.py:41`,
`test_act07_resolve_live.py:145,147`, and more), nothing else on this machine uses it, and CI's
service container runs there. 5433 was manufacturing failures, not avoiding them.

**Tuned for 4 GB:** `shared_buffers=128MB`, `max_connections=50`, `work_mem=4MB`. Also `fsync=off`,
`synchronous_commit=off`, `full_page_writes=off` — **this is a disposable test cluster and those
settings make it unsafe for anything else.** Do not point real data at it.

## The headline

**The control chain ran green against a real database for the first time in this project's history.**
All 19 revisions, `0001` → `0019 (head)`, 13 tables. Every prior claim about it rested on
source-text assertions.

## The four defects, none of which any test could have caught

Each had never executed. The suite has only ever skipped, so none of these was visible.

| # | Defect | Why it survived |
|---|---|---|
| 1 | **Six sites INSERT into `tenants(api_key)`**, a column `0006` renamed to `api_key_hash` — `test_query_route.py:78,267`, `test_sse.py:128`, `test_worker_kill.py:97`, `test_agent_chat_integration.py:102`, `test_neon_e2e.py:115` | `BACKLOG 1.1` records this cause as **"found and fixed"**. The `ci-green` fix changed **only `conftest.py`**. Five siblings were never touched, and nothing ran to say so. |
| 2 | **`subprocess.Popen(["celery", ...])`** raised `FileNotFoundError` on every run — the console script lives in `.venv/Scripts/` and is only on PATH when the venv is activated, which the repo's own gate never does | The comment directly above it records fixing this fixture's *hardcoded cwd*. One layer was fixed; the next was never seen. The `ci-green` plan predicted exactly this: *"Expect a second layer."* |
| 3 | **Five `:name::jsonb` paramstyle collisions** — SQLAlchemy `text()` will not bind `:soul` when `::` follows, so a literal `:soul::jsonb` reached Postgres → `syntax error at or near ":"`. Sites: `conftest.py:159`, `test_sse.py:74,140`, `test_worker_kill.py:109`, `test_neon_e2e.py:129`. Replaced with `CAST(:x AS jsonb)` | Never executed. |
| 4 | My own 5433 choice (above) | — |

**The pattern is the point.** Three of the four are *fixes that were recorded as complete*. `1.1`
says the causes "were found and fixed but have never executed remotely". They were found, partly
fixed, and the gap was invisible because *unobserved is not passing* — the principle this repo
already writes down for metrics, holding equally for its own test suite.

## Integration suite — executing for the first time

| run | result |
|---|---|
| first contact | 13 failed, 2 passed, 21 skipped, **4 errors** |
| after defect 1 + 2 | 12 failed, 3 passed, 21 skipped, 4 errors |
| after defect 3 + 4 | **16 failed, 3 passed, 21 skipped, 0 errors** |

The rising failure count is progress, not regression: setup **errors** became executed **failures**,
and the SSE failures moved from SQL syntax errors to assertion failures — they now reach their
assertions. 24 deselected (not `-m integration`).

**16 failures remain and are not yet diagnosed.** They are real and they are the substance of `1.1`.

## The tenant chain is blocked on pgvector, and that is where this stops

`alembic_tenant` `0001` does `CREATE EXTENSION vector`. The EDB binaries zip ships `pg_trgm` but not
pgvector:

```
psycopg2.errors.FeatureNotSupported: extension "vector" is not available
DETAIL: Could not open extension control file
        "C:/Users/Bantu/pgsql/share/extension/vector.control"
```

Building it from official source was the right move — MSVC Build Tools 2022 is installed, `cl.exe`
14.44, and the PostgreSQL binaries were themselves compiled by msvc-19.44, the same toolset. pgvector
v0.8.1 cloned and `nmake /F Makefile.win` reached the compiler, then:

```
crtdefs.h(10): fatal error C1083: Cannot open include file: 'corecrt.h'
```

**No Windows SDK is installed** — `C:\Program Files (x86)\Windows Kits\10\Include` does not exist.
`corecrt.h` is a UCRT header that ships with it. Installing one needs the Visual Studio Installer and
almost certainly admin, and **this session is not administrator**.

So `3.5` and D6's `0016` remain unproven — but the blocker is now **one named component**, not "no
database".

## What is now unblocked that was not

- The **control** chain is proven. `1.1`'s unit half can be worked locally.
- The integration suite runs; its 16 failures are diagnosable for the first time.
- `2.14` (`update_eval_run_config`'s jsonb merge) is testable against the control DB.

## Still blocked

- Everything tenant-DB: `3.5`, `0016`, and therefore the labelling loop's 503.
- `0.1` — `capture_responses.py` needs a live *ingested* agent, which needs a tenant DB.
- The metric being observed to move — same reason.
