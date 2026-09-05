# W Chats

A multi-tenant platform where a business owner completes signup, ingest and deploy and gets a
customer service agent that is grounded, evaluated and red-teamed before it goes live. FastAPI,
Celery, Redis and Alembic behind a Next.js console and a Preact widget; `apps/api/pyproject.toml`
and the two `package.json` files name every library and its version.

Words here mean one thing each; `CONTEXT.md` defines Agent, Tenant, Customer, Judge and Verdict.
Read it before naming a module, writing a log line, or describing the system back to the owner.
Design decisions are ADRs in `docs/adr/`.

## Branching and merging

```
main   trunk. Receives only work branches.
work   off main, days not weeks:  feat/<scope> · fix/<scope> · chore/<scope> · spike/<scope>
```

- `spike/*` merges nowhere; its learning lands in a `.dev/traces/` note.
- Claude merges, granted by the owner on 2026-09-05: `gh pr merge --merge`, a merge commit rather
  than a squash.
- Stacked PRs are normal here. Merge in dependency order, base before head, and delete a merged
  branch only once nothing is stacked on it.
- **A merge to `main` is a deploy.** Railway redeploys from `main` on every merge
  (`.dev/reference/260902-credential-locations.md`).

## Gates

A merge into `main` has all of these green, run for real and observed, never asserted. Every
behaviour change carries a test; a regression reaching `main` also earns a `.dev/retro.md`
paragraph naming what the plan failed to anticipate.

```bash
# backend (apps/api)
uv sync --extra dev --extra pipeline    # BOTH extras. `--extra dev` alone uninstalls docling
.venv/Scripts/python.exe scripts/gates.py full
#   static  ruff (count-pinned), import contracts, lizard, source assertions: the four steps that
#           never import app code, and what the Stop hook runs
#   mypy    the type baseline alone, which is what CI's Type-check job runs
#   fast    static + mypy + whole-suite collection
#   full    fast + the unit suite. mutmut is dead config on native Windows

# admin (apps/admin)
pnpm exec tsc --noEmit             # exit 0, zero errors
pnpm run check:no-dusk-tokens      # exit 0
pnpm run check:ops-room-wiring     # exit 0
pnpm run check:chart-render        # exit 0, about 26s. Measures every eval-chart pin, mark and
                                   # leader head against the chart box in Chromium, 9 fixtures x 4
                                   # viewports. The only gate reading rendered geometry
pnpm run test:unit                 # 69, browserless
pnpm run test:e2e                  # 135 tests, not 113: corrected 2026-08-12 by running it.
                                   # Observed 7 failed / 128 passed / 35.9 min; all 7 are 90s
                                   # networkidle timeouts beside Clerk load errors, not assertion
                                   # failures. Read .dev/PRODUCTION-READINESS.md 3.8 first

# widget (apps/widget)
pnpm run build && node scripts/check-size.mjs     # 20480 bytes gzipped or less
```

**CI is readable from this box** with `gh api`: check-run conclusions and whole job logs, on the
auth `gh` has. For a pushed head it beats a local run and is the only way to observe the
Integration job. Commands in `.dev/reference/260904-reading-ci-from-this-box.md`, to run before
writing that something cannot be checked.

**A negative test never observed to fail is indistinguishable from a tautology.** For any guard,
absence pin or fail-closed path: mutate it, observe red, restore from `HEAD`, observe green,
record the output.

## Project rules, hard-won, not relaxed

1. **Connection strings never in Celery task args.** A task takes `tenant_id` / `agent_id` and
   fetches and decrypts from the control DB at runtime.
2. **`acks_late=True` AND idempotency** on every task. Two requirements, both always.
3. **Langfuse v4 API only.** `start_span()` and `start_generation()` are gone.
4. **Ragas 0.4.x API only.** `ragas.metrics.collections`, `MetricResult`, and `reference` rather
   than `ground_truths`.
5. **No pg_search or pgbm25**, deprecated on Neon March 2026. BM25 is native `tsvector` plus
   `ts_rank_cd`.
6. **No Docker.** Local processes only: `redis-server`, PostgreSQL, `uvicorn`,
   `celery -A app.worker.celery_app worker`.
7. **FastAPI never does work inline.** Long-running work goes to Celery.
8. **Two Celery queues always:** `pipeline` (ingestion, build) and `runtime` (evals, agent calls).
9. **Per-tenant Neon projects**, not schema-per-tenant, so an eval can branch a tenant database.

## Deploy

The api `preDeployCommand` (`apps/api/railway.api.toml`) brings **the control DB and every tenant
database** to head before any of the four services serve new code. Tenants alone ships onto an
unmigrated control schema; the control DB alone leaves a tenant still at revision 0017 meeting a
column that is not there (#64).

## Environment

- **4 GB RAM.** No parallel test workers, small fixtures, one agent at a time.
- **PostgreSQL 17.6 runs on `localhost:5432`**: binaries `C:\Users\Bantu\pgsql`, cluster
  `C:\Users\Bantu\pgdata`, pgvector 0.8.1, `fsync=off`, disposable, databases `wchats_control` and
  `wchats_tenant_probe`. Migrations are applied and verified here: `run_tenant_migrations(dsn)`
  against `wchats_tenant_probe` is the production path and round trips up, down and up.
  `-m integration` still needs `INTEGRATION_TESTS_ENABLED`, and two of its modules spend money.
  `CONTROL_DB_URL` in `.env` is live Neon production and never substitutes for the local cluster.
- **Test the constraint, do not quote it.** "No PostgreSQL on this machine" outlived the truth by
  eight days, reaching a docstring, two BACKLOG rows, a plan and a trace before anyone opened a
  socket.
- **Toolchains get disk-cleaned.** `apps/api/.venv`, both `node_modules` and `.next` have been
  removed before. Restore with the `uv sync` above and `pnpm install` in each front end; check a
  gate can run before reporting it green.
- **Ingestion needs S3.** Uploads go to S3 and `parse` and `chunk` read them back. Locally, set
  `S3_ENDPOINT_URL` (a dev seam, refused when `ENVIRONMENT=production`), `S3_UPLOADS_BUCKET` and
  AWS credentials, and run MinIO's Windows binary as a plain process. Set
  `EMBEDDING_PROVIDER=voyage`; the `bedrock` default reaches real AWS.
- `scripts/probe_environment.py` reports what this shell actually reaches.

## Architecture

- **Programmatic core, agentic edges.** Deterministic code for anything testable, a model only
  for open-ended judgement; a Judge is one typed tool call. `app/services/tool_loop.py` is the
  owned bounded loop; no agent framework (ADR 0008).
- **A call site never names a model.** `app/core/model_client` routes every purpose in
  `PURPOSE_ROUTES`; one provider serves every row, and the route names it, not the base url.
- **SSE via Redis pub/sub.** A Celery task publishes to `job_events:{job_id}` and the SSE endpoint
  subscribes; `job_events` persists them for late-join replay.
- **Measurement honesty.** A metric over zero valid observations is `unknown`, never `pass`.
  Missing data is never passing data, and a model-generated label never gates a deploy or reaches
  a customer (`.dev/reference/measurement-layer-audit.md`).
- The console is GOTHAM "Bone on Graphite" (`DESIGN.md`).

## Where the rest lives

- **Open work is a GitHub issue**, and `wayfinder:map` is the path, so a task that discovers
  work opens one: `docs/agents/issue-tracker.md`, labels in `docs/agents/triage-labels.md`.
- **`.dev/` layout, the Workflow engine, model discipline, the suspended comprehension gate,
  the frozen `.planning/` archive**: `.dev/reference/260905-working-conventions.md`.
- Handoffs are conversation-scoped: `/handoff` writes to the OS temp directory, SessionStart
  injects the newest, and repo state lives in issues, ADRs and traces.
- Commits are `type(scope): message`, and PowerShell breaks on a multi-line `-m`, so write the
  message to a file and `git commit -F`.

These are good defaults for this repo, not laws. Global instructions set the standard; this file
adds what is specific to this codebase. A developer's instruction in the moment overrides both.
