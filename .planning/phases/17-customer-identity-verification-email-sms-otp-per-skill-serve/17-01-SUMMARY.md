---
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
plan: "01"
subsystem: identity-verification
tags: [migration, config, integration-test, IDV-01, tenant-db]
dependency_graph:
  requires: [alembic_tenant/versions/0007_integration_credentials.py]
  provides: [alembic_tenant/versions/0008_customer_identities.py, M17-OTP-settings, IDV-01-substrate]
  affects: [identity_service.py (Phase 17 plans 02-04), widget.py OTP routes (plans 03-04)]
tech_stack:
  added: []
  patterns: [op.execute() raw SQL migration, IF NOT EXISTS idempotent DDL, pydantic-settings M17 block]
key_files:
  created:
    - apps/api/alembic_tenant/versions/0008_customer_identities.py
    - apps/api/tests/integration/test_migrations.py (test_migration_0008_creates_customer_identities added)
  modified:
    - apps/api/app/core/config.py
decisions:
  - "OD-1 per-tenant scope: customer_identities has NO agent_id column; UNIQUE(external_id) alone ensures one verified session per identity per tenant"
  - "OD-3 Redis-only OTP state: this migration creates ONLY the durable verified-session record; no otp_pending table"
  - "OD-4 global session TTL: VERIFIED_SESSION_TTL_SECONDS=3600 (1 hour) as phase-wide default"
  - "OD-2 Twilio default: SMS_PROVIDER='twilio'; all credentials default to None (fail-safe — unset = SMS not configured)"
metrics:
  duration: ~30 min
  completed: "2026-07-01T16:27:41Z"
  tasks_completed: 3
  files_modified: 3
status: complete
---

# Phase 17 Plan 01: Identity DB Foundation + M17 Config Summary

One-liner: Per-tenant `customer_identities` migration (0008, down_revision 0007) with SHA-256 session-token hashing, UNIQUE(external_id), two lookup indexes, and 11 M17 OTP/SMS settings in `config.py`.

## What Was Built

### Task 1 — M17 OTP + SMS provider settings block (config.py)
**Commit: 299e4df**

Inserted a two-block M17 config section immediately after the existing SMTP block (`SMTP_PASSWORD`):

**OTP identity verification block** (5 settings, OD-4 lock):
- `VERIFIED_SESSION_TTL_SECONDS: int = 3600` — 1-hour verified-session lifetime (OD-4)
- `OTP_EMAIL_TTL_SECONDS: int = 600` — 10-min email OTP window
- `OTP_SMS_TTL_SECONDS: int = 300` — 5-min SMS OTP window  
- `OTP_MAX_ATTEMPTS: int = 5` — max verify attempts before challenge expires
- `OTP_SEND_MAX_PER_WINDOW: int = 3` — max sends per external_id per 10-min window

**SMS OTP provider block** (6 settings, OD-2 lock):
- `SMS_PROVIDER: str = "twilio"` — Twilio default (OD-2)
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` (E.164) — all `str | None = None`
- `AT_API_KEY`, `AT_USERNAME`, `AT_SENDER_ID` — Africa's Talking, all `str | None = None`

All 11 settings load with correct defaults when no OTP/SMS env vars are set (verified by `python -c "from app.core.config import settings; assert settings.VERIFIED_SESSION_TTL_SECONDS==3600; ..."`).

### Task 2 — Migration 0008 customer_identities (alembic_tenant)
**Commit: 996c9a2**

Created `apps/api/alembic_tenant/versions/0008_customer_identities.py` following the 0007 analog exactly:

- `revision = "0008"`, `down_revision = "0007"` — chains from integration_credentials
- `upgrade()`: `CREATE TABLE IF NOT EXISTS customer_identities (...)` with all required columns:
  - `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
  - `external_id TEXT NOT NULL` + `CONSTRAINT uq_customer_identities_external_id UNIQUE (external_id)`
  - `verified_at`, `verification_method`, `session_token_hash`, `session_expires_at`, `created_at`, `updated_at` — all TIMESTAMPTZ NOT NULL with appropriate defaults
  - **No `agent_id` column** (OD-1: per-tenant scope, not per-agent)
- Two lookup indexes with `IF NOT EXISTS` guards:
  - `ix_customer_identities_token_hash ON customer_identities (session_token_hash)`
  - `ix_customer_identities_expires_at ON customer_identities (session_expires_at)`
- `downgrade()`: drops both indexes then table, all with `IF EXISTS` guards (T-17-03)
- `op.execute()` raw SQL only — no `op.create_table()`, no imports from `app.*`

Module docstring documents OD-1 (no agent_id, per-tenant scope) and OD-3 (no otp_pending table).

### Task 3 — Migration 0008 roundtrip integration test
**Commit: 0a67068**

Added `test_migration_0008_creates_customer_identities` to `apps/api/tests/integration/test_migrations.py`:

The test (when local Postgres is available):
1. Creates a unique fresh test DB (`wchats_test_0008_<uuid>`)
2. Calls `run_tenant_migrations(conn_url)` — applies alembic upgrade head (= 0008)
3. Asserts `get_current_alembic_revision() == "0008"`
4. Queries `information_schema.tables` → asserts `customer_identities` exists
5. Queries `information_schema.table_constraints` → asserts `uq_customer_identities_external_id` UNIQUE
6. Queries `pg_indexes` → asserts both `ix_customer_identities_token_hash` and `ix_customer_identities_expires_at` exist
7. Asserts `otp_pending` table does NOT exist (OD-3 guard)
8. Re-runs `run_tenant_migrations` → asserts idempotent (revision still "0008")
9. Tears down in `finally` block

## Deviations from Plan

### Deviation: Live Postgres unavailable — integration test deferred (environment gate)

**Rule applied:** Project pattern (live-gate deferral — consistent with Phases 13/15/16)

**Found during:** Task 3 verification

**Issue:** The plan requires `python -m pytest tests/integration/test_migrations.py -k "0008 or customer_identities" -x -q` to PASS, which requires a local PostgreSQL at `wchats:wchats@localhost:5432`. The `postgresql-x64-17` Windows service is registered but its binary (`C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe`) does not exist — PostgreSQL 17 appears uninstalled despite the service registry entry. Attempts to install via `winget install PostgreSQL.PostgreSQL.17 --silent` required UAC elevation and hung in the unattended environment. A portable binaries download was attempted but cancelled to avoid indefinite wait.

**Impact:** The test is written correctly and collected (1 test, verified via `--collect-only`). All source-level acceptance criteria are met (DDL guards, UNIQUE constraint, indexes, no agent_id column). The behavioral acceptance criteria (live DB proof) are deferred.

**Resolution:** The integration test code is committed at `0a67068`. To verify live:
```bash
# Ensure PostgreSQL is running locally (wchats:wchats@localhost:5432)
cd apps/api
python -m pytest tests/integration/test_migrations.py -k "0008 or customer_identities" -x -q
```

**Operator migration for real Neon tenants** (alternative live verification):
```python
from app.services.migrations import run_tenant_migrations, get_current_alembic_revision
conn_str = agent.neon_direct_connection_string  # direct, non-pooled URI
run_tenant_migrations(conn_str)
assert get_current_alembic_revision(conn_str) == "0008"
```

This is consistent with the project's deferred-gate pattern (Phase 13 AWS gates, Phase 15 ACT-06 latency, Phase 16 Stripe live gate).

## Threat Coverage

All T-17-01, T-17-02, T-17-03 mitigations are present in the migration:

| Threat | Mitigation applied | Verification |
|--------|--------------------|--------------|
| T-17-01 (token disclosure) | Only `session_token_hash` (SHA-256 hex) column — no plaintext token or OTP column | Source: migration DDL |
| T-17-02 (IDOR) | Table lives in TENANT DB (per Neon project), not control DB | Source: `alembic_tenant/` not `alembic/` |
| T-17-03 (migration tampering) | All DDL uses `IF NOT EXISTS`/`IF EXISTS` — additive, no data mutation | Source: migration upgrade/downgrade |

## Known Stubs

None. This plan creates a migration and config — no UI rendering paths or data wiring.

## Self-Check

### Created files exist:
- `apps/api/alembic_tenant/versions/0008_customer_identities.py` — FOUND (verified: revision="0008", down_revision="0007")
- `apps/api/app/core/config.py` — MODIFIED (verified: all 11 M17 settings load)
- `apps/api/tests/integration/test_migrations.py` — MODIFIED (verified: 1 new test collected)

### Commits exist:
- `299e4df` feat(17-01): M17 config settings — FOUND
- `996c9a2` feat(17-01): migration 0008 — FOUND
- `0a67068` test(17-01): roundtrip integration test — FOUND

## Self-Check: PASSED (source-level)

Source checks pass. Behavioral check (live DB test) deferred — see deviation above.
