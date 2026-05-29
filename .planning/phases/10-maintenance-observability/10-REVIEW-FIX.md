---
phase: 10-maintenance-observability
fixed_at: 2026-05-25T00:00:00Z
review_path: .planning/phases/10-maintenance-observability/10-REVIEW.md
iteration: 1
findings_in_scope: 9
fixed: 9
skipped: 0
status: all_fixed
---

# Phase 10: Code Review Fix Report

**Fixed at:** 2026-05-25
**Source review:** `.planning/phases/10-maintenance-observability/10-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 9 (4 Critical + 5 Warning)
- Fixed: 9
- Skipped: 0

## Fixed Issues

### CR-01: Tenant DB queries for faithfulness and red-team stats

**Files modified:** `apps/api/app/services/alert_service.py`, `apps/api/app/services/digest_service.py`, `apps/api/app/worker/tasks/runtime/alert.py`
**Commit:** `e19c72b`
**Applied fix:**
- Rewrote `_get_latest_faithfulness` in `alert_service.py` to use `psycopg2.connect(conn_str)` against the tenant DB. Computes faithfulness via `AVG(eval_results.score) JOIN eval_runs` filtered by `kind='m6:{agent_id}'` (no `aggregate_scores` column exists).
- Rewrote `_get_latest_critical_count` in `alert_service.py` to use tenant DB and filter `red_team_runs` by `kind='m7:{agent_id}'` (no `agent_id` column in tenant schema).
- Updated `check_and_write_alerts` signature to accept `conn_str: str | None` and `tenant_id: str | None` instead of passing control-DB session for metric lookups.
- Rewrote `_collect_digest_stats` in `digest_service.py` to fetch all four metrics (faithfulness, red-team count, conversations, escalations) from the tenant DB in a single `psycopg2` connection. Removed dead control-DB queries.
- Updated `run_alert_check` task in `alert.py` to decrypt `agent.neon_connection_string` via `fernet_decrypt` and pass `conn_str` to `check_and_write_alerts`. Connection string never in task args (CTL-08).

**Note:** CR-02 (email ordering) and CR-03 (SMTP login call) were applied in the same `alert_service.py` rewrite. CR-03's `config.py` change is in a separate commit.

---

### CR-02: Email fires only after successful DB commit

**Files modified:** `apps/api/app/services/alert_service.py` (included in CR-01 commit `e19c72b`)
**Commit:** `e19c72b`
**Applied fix:**
Moved both `send_alert_email(...)` calls inside the `if db is not None:` block, after `_write_alert` returns (which calls `db.commit()` internally). Email now fires only after the row is durably committed. The test path (`db is None`) still sends email directly via an explicit `else` branch.

---

### CR-03: SMTP authentication credentials added to config and used in send functions

**Files modified:** `apps/api/app/core/config.py`, `apps/api/app/services/alert_service.py` (login call in CR-01 commit), `apps/api/app/services/digest_service.py` (login call in CR-01 commit)
**Commit:** `54d6073` (config.py); `e19c72b` (login calls in send functions)
**Applied fix:**
- Added `SMTP_USER: str | None = None` and `SMTP_PASSWORD: str | None = None` to `Settings` in `config.py`, making SMTP authentication architecturally possible.
- Both `send_alert_email` (alert_service) and `send_digest_email` (digest_service) now call `server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)` after `server.starttls()` when both credentials are present.

---

### CR-04: Demo script greps for actual registered task function names

**Files modified:** `scripts/demo_m10.sh`
**Commit:** `b506eaf`
**Applied fix:**
Changed the `grep` in Section 5 from `"digest-weekly"` / `"alert-daily"` (beat schedule entry keys, not visible in `celery inspect registered`) to `"run_weekly_digest_beat"` / `"run_alert_check_beat"` (the actual registered task function names). Added a clarifying comment explaining that `celery inspect registered` lists task names, not schedule keys.

---

### WR-01: tenant_id added to Alert model and resolve route

**Files modified:** `apps/api/app/models/alert.py`, `apps/api/app/api/v1/observability.py`, `apps/api/alembic/versions/0013_alert_tenant_id.py`, `apps/api/app/services/alert_service.py` (populate in `_write_alert`, included in CR-01 commit)
**Commit:** `2852dd8`
**Applied fix:**
- Added `tenant_id: Mapped[uuid.UUID | None]` (nullable) to the `Alert` ORM model.
- `_write_alert` in `alert_service.py` now accepts and stores `tenant_id`.
- `run_alert_check` task passes `tenant_id=str(agent.tenant_id)` to `check_and_write_alerts`.
- `resolve_alert` endpoint in `observability.py` adds a direct `alert.tenant_id == tenant.id` check as defense-in-depth (skipped when `tenant_id` is NULL for legacy rows).
- Migration `0013_alert_tenant_id.py` adds the column, backfills via JOIN with `agents`, and creates a unique partial index for WR-04.

---

### WR-02: digest_runs row committed before email

**Files modified:** `apps/api/app/worker/tasks/runtime/digest.py`
**Commit:** `9ebf15c`
**Applied fix:**
Reordered `run_weekly_digest`: the `INSERT INTO digest_runs` + `db.commit()` now precede `send_digest_email(...)`. The committed row acts as an idempotency anchor — on Celery retry the 7-day guard finds the row and skips without resending the email.

---

### WR-03: AlertsBanner fetchAlerts wrapped in useCallback

**Files modified:** `apps/admin/app/agents/[id]/components/AlertsBanner.tsx`
**Commit:** `61aa07a`
**Applied fix:**
- Added `useCallback` to the import.
- Converted `fetchAlerts` from a plain `async` function to `useCallback(async () => {...}, [agentId, getToken, apiBase])`.
- Updated `useEffect` dependency array from `[agentId]` to `[fetchAlerts]`.
- Removed the `// eslint-disable-next-line react-hooks/exhaustive-deps` suppression comment.
The polling interval now always holds a fresh reference that reflects the current `agentId`, `getToken`, and `apiBase` values.

---

### WR-04: Unique partial index on alerts(agent_id, alert_type) WHERE resolved_at IS NULL

**Files modified:** `apps/api/app/models/alert.py` (comment), `apps/api/alembic/versions/0013_alert_tenant_id.py` (index DDL)
**Commit:** `2852dd8` (combined with WR-01 migration)
**Applied fix:**
Migration `0013` creates `CREATE UNIQUE INDEX alerts_agent_alert_type_unresolved_idx ON alerts(agent_id, alert_type) WHERE resolved_at IS NULL`. The existing `_active_alert_exists` check in `alert_service.py` remains as a fast application-level short-circuit; the unique index provides DB-level enforcement against concurrent duplicate inserts.

---

### WR-05: AlertsBanner gated on auth state and agent data

**Files modified:** `apps/admin/app/agents/[id]/page.tsx`
**Commit:** `4020061`
**Applied fix:**
Changed `<AlertsBanner agentId={id} />` to `{isLoaded && isSignedIn && agent && <AlertsBanner agentId={id} />}`. The component is now mounted only when Clerk auth is confirmed and agent data has loaded, eliminating the superfluous fetch during the loading skeleton state.

---

## Skipped Issues

None.

---

_Fixed: 2026-05-25_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
