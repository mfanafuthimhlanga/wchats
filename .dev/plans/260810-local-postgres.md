# A local PostgreSQL — closing BACKLOG 0.2

**Branch:** `chore/local-postgres` off `chore/post-merge-state` (doc-only base, so the merged-state
reconciliation is not lost). `main` is at `57be16b`.

**Why now:** the owner freed ~30 GB (30.1 GB observed) specifically so live runs are possible. `0.2`
is the single highest-leverage row in the backlog — it is the precondition under half of §3 and §4,
and under every "unprovable here" caveat D1 and D6 shipped with.

---

## What is actually on this machine

Measured 2026-08-10, not assumed:

- **No PostgreSQL service exists** — the `postgresql-x64-17` registration HANDOFF describes is gone.
- Nothing listens on 5432-5435.
- `C:\Program Files\PostgreSQL\18\` exists and contains **`data/` (initialised, `PG_VERSION` = 18)
  and `pg_env.bat` — but NO `bin/`.** The binaries were disk-cleaned; the data cluster survived.
  That is the "stale registration pointing at a deleted binary" seen up close.
- **Not an administrator.** The EDB installer and `winget` service registration are therefore out.

## Approach

**Portable binaries, fresh cluster, user-writable paths, non-default port.** No admin, no service,
no registry, and removal is `rm -rf` of two directories.

| | |
|---|---|
| Binaries | `postgresql-17.6-1-windows-x64-binaries.zip` (314 MB) → `C:\Users\Bantu\pgsql` |
| Cluster | fresh `initdb` → `C:\Users\Bantu\pgdata` |
| Port | **5433** — leaves 5432 free, and the repo's own docs talk about 5432-5435 |

**Why 17.6 and not 18:** `ci.yml:101` and `nightly.yml` both use `postgres:17-alpine`. Matching the
CI image is the whole point — a local integration pass has to mean something about CI, and Neon runs
16/17 too. The leftover 18 cluster is **not reused**: it sits under `Program Files` (not writable
without admin), its superuser password is unknown, and its state is unknown. Fresh is cheaper than
forensics.

**Disk budget.** ~314 MB download + ~1.2 GB extracted + a small cluster. The owner asked for sparing
use of 30 GB; this is roughly 5% of it. The zip is deleted after extraction.

## What this unblocks, in order of value

1. **The migration roundtrips (`3.5`, and D6's `0016`).** `alembic_tenant` 0013/0014/0015/0016 have
   **never executed anywhere**. 0016 is why every label attempt returns 503 today.
2. **`2.14`** — `update_eval_run_config`'s jsonb merge, asserted against a cursor double and never run.
3. **The integration suite** — 26 files that have only ever skipped. A skip is unobserved, never a pass.
4. **`0.1`'s real precondition** — `capture_responses.py` needs a live, ingested agent.
5. **The metric being observed to move** — the one thing the D1 tier-2 judge said the branch had not
   earned.

## Hard constraint, and it is not negotiable

**`CONTROL_DB_URL` points at live Neon production.** Every command in this phase names the local
instance explicitly. Nothing here reads or writes Neon. `0.6`'s `count(*)` is the owner's to run
against production, not mine.

## Risks

- **4 GB RAM.** Postgres plus the test suite plus five Langfuse daemon threads on one machine. Tune
  `shared_buffers` down; do not run the unit suite and integration suite concurrently.
- **Migrations have never run.** Expect them to fail on first contact — that is the point, and a
  failure here is a genuine finding, not a setback. `3.5` exists because source-text assertions are
  all that has ever checked them.
- **Integration tests have never run either.** `conftest.py` had a hardcoded absolute path fixed in
  `ci-green` P1 and has still never reached a live database. Expect a second layer of breakage.

## Definition of done

Not "PostgreSQL is installed." **`alembic upgrade head` observed green on both the control and
tenant chains, and the integration suite observed with a real pass/fail count** — recorded verbatim,
with whatever failed named rather than smoothed.
