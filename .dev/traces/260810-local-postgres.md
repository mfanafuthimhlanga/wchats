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

---

# ADDENDUM — pgvector built without admin, and both chains proven

The Windows SDK blocker dissolved. `vswhere -products *` returns **nothing**: the Build Tools
install is orphaned — `VC\Tools\MSVC\14.44.35207` exists on disk but no VS product is registered, so
there is no "Modify" UI to add an SDK through. That ruled out the installer route entirely.

**Microsoft ships the Windows SDK as NuGet packages**, which are plain zips needing no admin:

```
Microsoft.Windows.SDK.CPP      10.0.28000.2526   153 MB   headers (ucrt, um, shared)
Microsoft.Windows.SDK.CPP.x64  10.0.28000.2526    50 MB   x64 libs
```

Extracted to `C:\Users\Bantu\pg-setup\sdk`, then `INCLUDE`/`LIB` pointed at them alongside the MSVC
toolset. pgvector **v0.8.1** cloned from the official repo and built with `nmake /F Makefile.win` —
no third-party binary was downloaded, which matters for a shared library loaded into a database
server. The toolset is the same one that compiled these PostgreSQL binaries (msvc-19.44).

```
vector.dll                274,944 bytes -> C:\Users\Bantu\pgsql\lib
vector.control                149 bytes -> C:\Users\Bantu\pgsql\share\extension
CREATE EXTENSION vector   -> extversion 0.8.1
```

## Both chains, against a real database

| chain | driver | result |
|---|---|---|
| control (`alembic/`) | `alembic upgrade head` | `0001` → **`0019 (head)`**, 13 tables |
| tenant (`alembic_tenant/`) | **`run_tenant_migrations` — the production path**, not the CLI | **`0016 (head)`**, 24 tables |

Driven through `run_tenant_migrations` deliberately: it is what `apply_migrations` calls in
production, so what is proven is the real path rather than an adjacent one — the lesson D1 paid for
with its seam.

Verified on the tenant DB:

- `embeddings_vector_hnsw_idx` exists — **only a real pgvector can create it**, so `0001` is
  genuinely satisfied rather than skipped.
- `eval_runs.config` is `jsonb` (`0013` — the column D1's `agent_invoked` lives in).
- `eval_scenarios` carries `dataset` (`0014`) and `label_trust_tier`/`labelled_by`/`labelled_at`
  (D6's `0016`).

## The 0016 roundtrip

`3.5` asked for roundtrips, not just upgrades. `alembic_tenant`'s `env.py` requires an injected
connection, so `-x url=` fails with `KeyError: 'url'`; the downgrade was driven through the same
injected-connection pattern `run_tenant_migrations` uses.

```
downgrade -1  -> head 0015, label columns 0, eval_scenarios_label_trust_tier_check_v1 gone
upgrade head  -> head 0016, label columns 3, constraint restored
```

**A false alarm worth recording.** A first check reported "3 CHECK constraints left behind" after the
downgrade. They were `pg_enum_typid_label_index`, `pg_seclabel_object_index` and
`pg_shseclabel_object_index` — PostgreSQL's own system catalogs, swept up by a `conname LIKE
'%label%'` pattern. The downgrade is clean. Recorded because a too-broad verification query that
*looks* like it found a defect is its own hazard.

## Integration suite

| run | result |
|---|---|
| first contact | 13F / 2P / 21S / **4E** |
| after the api_key + celery fixes | 12F / 3P / 21S / 4E |
| after the cast + port fixes | 16F / 3P / 21S / 0E |
| **after pgvector** | **14F / 5P / 21S / 0E** |

`test_apply_migrations_creates_v1_schema` and `test_apply_migrations_idempotent` now pass —
they were failing only for want of the extension. **14 failures remain, undiagnosed**, and they are
`1.1`'s real body of work.

## Server lifetime — a trap for the next session

The cluster died once mid-session: it was started by a backgrounded shell command, and when the
harness reaped that task it took the postgres process tree with it. Start it detached (`nohup`, or
`Start-Process`), or better, register it as a service with admin:

```
pg_ctl register -N postgresql-17-local -D "C:/Users/Bantu/pgdata" -S auto
```

Disk: ~1.6 GB total (binaries, cluster, SDK, pgvector source). Both installer zips deleted.

---

# ADDENDUM 2 — the suite terminates, and what the last ten are

| run | result |
|---|---|
| first contact | 13F / 2P / 21S / **4E** |
| after `api_key` + `celery` | 12F / 3P / 4E |
| after casts + port | 16F / 3P / 0E |
| after pgvector | 14F / 5P / 0E |
| **after prefix + revisions + stream bounds** | **10F / 9P / 21S / 0E, 3m43s** |

Passes went 2 → 9, errors 4 → 0, and — the part that matters most — **the suite finishes.**

## The hang, which my own fix exposed

Correcting the `/api/v1` prefix made the SSE tests connect for the first time, and
`test_sse_receives_live_events_after_replay` then hung indefinitely. Two runs sat on it, 10 minutes
and 40 minutes, both stopping at exactly test 34 of 40.

`AsyncClient(timeout=10.0)` is a **per-request** timeout. It does not bound `aiter_lines()` on a
stream the server holds open by design; the loop exits only on its own break condition, so a stream
delivering fewer events than expected waits forever. All three loops in the module are now inside
`asyncio.timeout(SSE_STREAM_TIMEOUT_S = 30)`.

**This is worth more than the one test.** A hanging test burns a whole CI job budget and reports
nothing — and `0.3` records CI dying at a hard 15-minute wall clock with every job cancelled. These
tests have never run on a runner. Switched on before this fix, they would have produced exactly that
signature, and the cause would have been read as the billing cap.

## The last ten, grouped

Filed as `BACKLOG 1.6`–`1.9` so they are tracked rather than living in this trace.

| class | count | state |
|---|---|---|
| `HybridChunker` lazy import + docling absent | 4 | environmental; should **skip**, not fail (`4.1`, `4.4`) |
| Neon API with a placeholder key | 4 | needs an owner decision: real scoped key, or mock at the boundary |
| Celery task args empty on dispatch | 1 | undiagnosed |
| SSE live events never reach the stream | 1 | narrowed, cause unknown |

## Nine defects, and what they have in common

5 × `tenants(api_key)` · 1 × `celery` not on PATH · 5 × `:name::jsonb` · 8 × missing `/api/v1` ·
2 × hardcoded alembic revision · 3 × unbounded stream loop.

Three of these were recorded somewhere as *already fixed*. Every one was invisible for the same
reason: **the suite had never executed.** The repo already writes this down for metrics — a metric
over zero observations is `unknown`, never `pass`. It holds identically for a test suite, and this
session is the evidence.
