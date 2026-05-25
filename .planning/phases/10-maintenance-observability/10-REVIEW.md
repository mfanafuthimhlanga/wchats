---
phase: 10-maintenance-observability
reviewed: 2026-05-25T00:00:00Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - apps/api/app/models/alert.py
  - apps/api/app/core/config.py
  - apps/api/app/worker/tasks/runtime/red_team.py
  - apps/api/app/services/digest_service.py
  - apps/api/app/worker/tasks/runtime/digest.py
  - apps/api/app/worker/celery_app.py
  - apps/api/app/services/alert_service.py
  - apps/api/app/worker/tasks/runtime/alert.py
  - apps/api/app/api/v1/observability.py
  - apps/admin/app/agents/[id]/components/AlertsBanner.tsx
  - apps/admin/app/agents/[id]/page.tsx
  - scripts/demo_m10.sh
findings:
  critical: 4
  warning: 5
  info: 2
  total: 11
status: issues_found
---

# Phase 10: Code Review Report

**Reviewed:** 2026-05-25T00:00:00Z
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

Phase 10 adds maintenance and observability infrastructure: daily alert checks, weekly
owner digests, alert CRUD routes, and a React `AlertsBanner` component. Security posture
on the FastAPI routes is solid — IDOR is properly guarded by `tenant_id` cross-checks on
both `list_alerts` and `resolve_alert`. Bearer auth in `AlertsBanner` is correctly
implemented via Clerk's `getToken()`. The Celery beat entries, serialization settings,
and `acks_late=True` flags are all present.

However, four blockers exist. Two are structural DB mismatches that cause every alert
check and every digest to silently return empty/zero results at runtime (the queries
target non-existent columns on the wrong database). One is a duplicate-alert race
condition caused by email firing before the DB row is committed. One is an SMTP
misconfiguration where `starttls()` is called without a prior `ehlo()`, which is
technically valid but breaks silently on strict MTA implementations and — more
critically — the pattern calls `starttls()` without any authentication (`login()`),
meaning all email delivery requires an open relay, which is rejected by every
production SMTP provider.

---

## Critical Issues

### CR-01: `alert_service` and `digest_service` query `eval_runs` and `red_team_runs` on the wrong database with phantom columns

**File:** `apps/api/app/services/alert_service.py:27-36`, `apps/api/app/services/digest_service.py:42-70`

**Issue:** Both `_get_latest_faithfulness()` and `_collect_digest_stats()` issue SQL
against the `db` argument, which is a **control DB** SQLAlchemy session (passed from
`get_sync_db()` in the Celery tasks). But `eval_runs` and `red_team_runs` exist only in
**per-tenant Neon DBs** (created by `alembic_tenant/versions/0001_tenant_v1_schema.py`).
The control DB has no `eval_runs` table and no `red_team_runs` table — confirmed by
inspection of all 12 control-DB migration files (`alembic/versions/0001`–`0012`).

Additionally, both queries reference columns that do not exist in the tenant-DB schema:
- `SELECT aggregate_scores FROM eval_runs` — the tenant `eval_runs` table has no
  `aggregate_scores` column (columns: `id, kind, started_at, finished_at, status`).
  `aggregate_scores` is a computed JSON blob built in `app/api/v1/evals.py` at
  query time from `eval_results` rows, never stored.
- `SELECT findings FROM red_team_runs WHERE agent_id = :agent_id` — the tenant
  `red_team_runs` table has no `agent_id` column (columns: `id, kind, started_at,
  finished_at, findings, max_severity, status, deployment_blocked`).

**Effect:** Every `run_alert_check` task and every `run_weekly_digest` task silently
returns zero faithfulness data and zero critical red team counts. The `except` blocks
swallow the resulting `ProgrammingError` / `UndefinedTableError` with a warning log.
No real alert is ever triggered on a genuine regression. The feature is entirely
non-functional.

**Fix:** These queries must run against the tenant DB via `psycopg2.connect(conn_str)`,
not against the control DB. The `conn_str` must be decrypted and passed to these
helper functions. Columns must match the actual tenant schema. For faithfulness, compute
it from `eval_results` joined to `eval_runs` (same approach as `evals.py` lines 126–148).
For red team critical count, filter by `kind = 'm7:{agent_id}'` instead of
`WHERE agent_id = :agent_id`.

Example fix for `_get_latest_faithfulness` in `alert_service.py`:
```python
import psycopg2

def _get_latest_faithfulness(agent_id: str, conn_str: str) -> float | None:
    """Fetch latest faithfulness score from the TENANT DB eval_results table."""
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT AVG(er.score)
                    FROM eval_results er
                    JOIN eval_runs r ON er.eval_run_id = r.id
                    WHERE r.kind = %s
                      AND r.status = 'complete'
                      AND er.metric = 'faithfulness'
                      AND r.started_at = (
                          SELECT MAX(started_at) FROM eval_runs
                          WHERE kind = %s AND status = 'complete'
                      )
                    """,
                    (f"m6:{agent_id}", f"m6:{agent_id}"),
                )
                row = cur.fetchone()
                return float(row[0]) if row and row[0] is not None else None
        finally:
            conn.close()
    except Exception as exc:
        log.warning("alert_service.faithfulness_fetch_failed", agent_id=agent_id, error=str(exc))
        return None
```

---

### CR-02: `check_and_write_alerts` sends email before the DB row is committed — duplicate email on `db.commit()` failure

**File:** `apps/api/app/services/alert_service.py:119-134`

**Issue:** In the `check_and_write_alerts` function, `send_alert_email(...)` is called
**after** `_write_alert(...)` (which calls `db.add` + `db.commit`) but the email call
is **outside** the `if db is not None:` guard for writing, meaning the email fires
regardless of whether the DB transaction succeeded. Worse, if `db.commit()` inside
`_write_alert` raises (e.g., `IntegrityError` from the `alerts_type_check` constraint),
the exception propagates upward and the email was never sent. If the commit succeeds but
the caller's surrounding transaction rolls back after `check_and_write_alerts` returns,
the email is sent but no persistent alert row exists — duplicates will fire on the next
daily run because `_active_alert_exists` returns `False`.

More concretely: the duplicate-alert guard (`_active_alert_exists`) and the `_write_alert`
call both run inside the same synchronous `with get_sync_db() as db:` block in
`run_alert_check`. The guard and the write are safe. But `send_alert_email` is called
from inside `check_and_write_alerts` after `db.commit()` succeeds for that single
`_write_alert` call. If **two** alerts are triggered (both `eval_regression` and
`red_team_critical`), the first alert's `_write_alert` commits, its email fires, then the
second `_write_alert` calls `db.commit()` again. This double-commit within one SQLAlchemy
session is valid for the sync case but is fragile.

The real risk is: if the outer `run_alert_check` task fails **after** `check_and_write_alerts`
returns (e.g., during the `log.info` line that follows), and Celery retries the task,
`_active_alert_exists` now returns `True` (the row was committed), so no second email is
sent. This is actually the safe path. However, if `_write_alert`'s `db.commit()` throws
and the exception is caught by the broad `except Exception` in `run_alert_check`'s retry
block, the email was **not** sent but the task is retried — and on retry the guard fires
(the row from the partial commit may or may not exist depending on the error). This is
an inconsistent state.

**Fix:** Move `send_alert_email` to after `db.commit()` succeeds and make it conditional
on a successful write. Return the alert object from `_write_alert` and send email only
on confirmed persist:

```python
if faithfulness is not None and faithfulness < settings.ALERT_FAITHFULNESS_THRESHOLD:
    if db is None or not _active_alert_exists(agent_id, "eval_regression", db):
        msg = f"Faithfulness {faithfulness:.2f} is below threshold {settings.ALERT_FAITHFULNESS_THRESHOLD}."
        if db is not None:
            alert = _write_alert(agent_id, "eval_regression", "warning", msg, db)
            new_alerts.append(alert)
            # Email fires only after successful DB commit (inside _write_alert)
            send_alert_email(agent_name, agent_id, "eval_regression", msg)
        # If db is None (test path), still send email
        elif db is None:
            send_alert_email(agent_name, agent_id, "eval_regression", msg)
```

---

### CR-03: SMTP `starttls()` called without `login()` — all production SMTP providers will reject delivery

**File:** `apps/api/app/services/alert_service.py:93-97`, `apps/api/app/services/digest_service.py:122-126`

**Issue:** Both `send_alert_email` and `send_digest_email` connect to the SMTP server,
call `server.starttls()` to upgrade to TLS, and immediately call `server.sendmail()` —
with no `server.login()` call. Every production SMTP provider (SendGrid, Mailgun, AWS
SES, Gmail SMTP) requires authenticated `AUTH LOGIN` or `AUTH PLAIN` after `STARTTLS`.
Without a login call, `sendmail()` will be rejected with `SMTPSenderRefused` (code 530:
"5.7.0 Authentication Required") or `SMTPRecipientsRefused`. Since both functions wrap
`sendmail()` in `except Exception`, this error is silently swallowed and the warning log
line reads "alert_service.email_failed" with the rejection message — the alert fires but
the owner never receives it.

Additionally, `SMTP_PASSWORD` / `SMTP_USER` configuration fields are absent from `config.py`
entirely, making it architecturally impossible to add authentication without a config change.

**Fix:** Add `SMTP_USER` and `SMTP_PASSWORD` optional fields to `config.py`:
```python
SMTP_USER: str | None = None
SMTP_PASSWORD: str | None = None
```

Then in both send functions, add login after `starttls()`:
```python
server.starttls()
if settings.SMTP_USER and settings.SMTP_PASSWORD:
    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
server.sendmail(...)
```

---

### CR-04: Demo script verifies beat task names with `celery inspect registered` — this checks worker-registered *tasks*, not beat schedule entries

**File:** `scripts/demo_m10.sh:244-256`

**Issue:** Section 5 runs `celery -A app.worker.celery_app inspect registered` and greps
for `"digest-weekly"` and `"alert-daily"`. But `inspect registered` lists the **Celery
task names** registered on the worker (e.g.,
`app.worker.tasks.runtime.digest.run_weekly_digest_beat`), not the beat schedule
**entry names** (`"digest-weekly"`, `"alert-daily"`) defined in `celery_app.conf.beat_schedule`.
The grep will never match — the beat entry keys are only visible in
`celery inspect scheduled` (for due tasks) or via reading `beat_schedule` directly.
The assertion always outputs `[FAIL]` when the worker is running correctly, giving a
false negative that causes `ALL_PASSED=false` and exit code 1.

**Fix:** Either grep for the actual registered task names:
```bash
if echo "$BEATS_OUTPUT" | grep -q "run_weekly_digest_beat" && \
   echo "$BEATS_OUTPUT" | grep -q "run_alert_check_beat"; then
    echo "[PASS] OPS-02/OPS-04: beats registered"
```
Or use `celery inspect scheduled` / `celery beat --inspect` to validate beat entries
by their schedule key names.

---

## Warnings

### WR-01: `Alert` ORM model missing `tenant_id` field — cross-tenant alert leakage is prevented only by FK chain, not direct column

**File:** `apps/api/app/models/alert.py:16`

**Issue:** The `Alert` model stores `agent_id` but not `tenant_id`. The IDOR guard in
`observability.py` correctly fetches the `Agent` from the control DB and checks
`agent.tenant_id == tenant.id` before querying alerts. However, the `resolve_alert`
endpoint (line 59) fetches the alert by `alert_id` and only verifies `alert.agent_id == agent_id`
(line 60) — not `alert.agent_id` → `agent.tenant_id == tenant.id`. The prior agent
ownership check (lines 57-58) provides the correct guard, but the two-step check
creates a TOCTOU window: between the agent check and the alert fetch, a concurrent
request could transfer agent ownership (though the app has no agent-transfer feature
today). More practically, the two-query pattern is fragile — if the agent check is
ever refactored out or short-circuited, the alert is exposed without a direct
ownership column.

**Fix:** Add `tenant_id` to the `alerts` table and model, populate it in `_write_alert`,
and add a direct `alert.tenant_id == tenant.id` check in the resolve route as a
defense-in-depth guard.

---

### WR-02: `run_weekly_digest` sends email before inserting the `digest_runs` row — retry sends duplicate email

**File:** `apps/api/app/worker/tasks/runtime/digest.py:79-88`

**Issue:** In `run_weekly_digest`, `send_digest_email(...)` is called at line 79, and
the `INSERT INTO digest_runs` row is committed at line 87-88. If the task fails between
lines 79 and 88 (e.g., DB write error, network timeout), Celery retries the task. On
retry, the idempotency guard at line 63 (`SELECT id FROM digest_runs WHERE agent_id
AND sent_at >= :since`) finds no row (the insert never committed) and allows the digest
email to be sent again. The owner receives a duplicate weekly digest.

**Fix:** Insert and commit the `digest_runs` row **before** calling `send_digest_email`.
Use a two-phase approach: commit the row first (idempotency anchor), then send email.
Email failure is already fire-and-forget; the row ensures at-most-once delivery on retry.

```python
# Insert digest_runs row FIRST (idempotency anchor)
db.execute(
    text("INSERT INTO digest_runs (agent_id, payload) VALUES (:agent_id, :payload::jsonb)"),
    {"agent_id": agent_id, "payload": json.dumps(stats)},
)
db.commit()
# Email is fire-and-forget; row is already committed so retry won't duplicate
send_digest_email(agent.name, agent_id, stats)
```

---

### WR-03: `AlertsBanner` polling loop captures stale `fetchAlerts` closure — `agentId` prop change does not reset interval

**File:** `apps/admin/app/agents/[id]/components/AlertsBanner.tsx:65-70`

**Issue:** The `useEffect` at line 65 creates an interval using `setInterval(fetchAlerts, 30_000)`.
`fetchAlerts` is defined inside the component body (line 52) as an `async` function that
captures `agentId`, `getToken`, and `apiBase` from the outer closure. The effect runs
once on mount and whenever `agentId` changes (correct dependency array at line 70).
However, because `fetchAlerts` is **not** in the `useEffect` dependency array (the
eslint-disable comment at line 69 suppresses the warning), if `getToken` is updated by
Clerk between renders (e.g., token refresh), the interval continues to use the stale
`getToken` reference captured at mount time. In practice Clerk's `getToken` reference
is stable, but the eslint-disable is suppressing a real signal.

More concretely: if `agentId` changes (navigating between agents via the same mounted
component), the effect cleanup runs `clearInterval(id)` and re-registers with the new
`agentId` correctly. This part works. The stale closure risk is limited to `getToken`
and `apiBase`. Since `apiBase` is static and Clerk's `getToken` is stable, this is low
risk but the suppressed lint rule masks any future regression.

**Fix:** Move `fetchAlerts` inside the `useEffect` or add it to the dependency array
and wrap it in `useCallback`:
```tsx
const fetchAlerts = useCallback(async () => {
  const token = await getToken()
  if (!token) return
  try {
    const res = await fetch(`${apiBase}/api/v1/agents/${agentId}/alerts`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (res.ok) setAlerts(await res.json())
  } catch { /* silent */ }
}, [agentId, getToken, apiBase])

useEffect(() => {
  fetchAlerts()
  const id = setInterval(fetchAlerts, 30_000)
  return () => clearInterval(id)
}, [fetchAlerts])
```

---

### WR-04: `run_alert_check` task has no idempotency guard — daily beat can create duplicate alerts

**File:** `apps/api/app/worker/tasks/runtime/alert.py:44-58`

**Issue:** `run_weekly_digest` implements a 7-day idempotency window via `digest_runs`.
`run_red_team` implements a 30-minute idempotency window via a `red_team_runs` query.
`run_alert_check` has no equivalent guard. If `run_alert_check_beat` dispatches tasks
and a network hiccup causes duplicate delivery (Celery `acks_late=True` + worker
crash before ack), two `run_alert_check` tasks may execute concurrently for the same
`agent_id`. The `_active_alert_exists` check in `check_and_write_alerts` provides
partial protection (the second concurrent execution will see the row written by the
first), but only if both tasks check **after** the first task's `db.commit()`. In the
concurrent case, both tasks can pass the guard before either commits, resulting in two
identical alert rows being written.

**Fix:** Add a time-window guard before dispatching per-agent tasks, or use a database
`INSERT ... WHERE NOT EXISTS` pattern (advisory lock or unique partial index on
`(agent_id, alert_type)` where `resolved_at IS NULL`) to enforce at-most-one unresolved
alert per type at the DB level.

---

### WR-05: `page.tsx` renders `AlertsBanner` unconditionally before authentication state is known

**File:** `apps/admin/app/agents/[id]/page.tsx:351`

**Issue:** `<AlertsBanner agentId={id} />` is rendered at line 351 inside the main
`return`. The `AlertsBanner` component immediately calls `fetchAlerts()` on mount
(line 66 of `AlertsBanner.tsx`), which calls `getToken()`. If `isLoaded` is `false`
(Clerk not yet initialized), `getToken()` returns `null`, and the fetch is skipped
correctly. However, `AlertsBanner` has no awareness of `isLoaded` / `isSignedIn` — it
only checks `if (!token) return`. If the component is mounted on a route that can be
accessed while signed out (currently guarded by Clerk middleware, but defensible), the
fetch silently skips and the banner shows nothing, which is acceptable behavior but
creates an uncommunicative failure mode.

The deeper issue: `AlertsBanner` is rendered even during the `agentQuery.isPending`
state (the loading skeleton). This causes a superfluous fetch to `/api/v1/agents/${agentId}/alerts`
before the agent's own data has loaded. The response will be either 403 (if the token
corresponds to a different tenant's agent ID) or an empty array. Either way it is a
wasted request.

**Fix:** Gate `AlertsBanner` on `isLoaded && !!isSignedIn && !!agent`:
```tsx
{isLoaded && isSignedIn && agent && <AlertsBanner agentId={id} />}
```

---

## Info

### IN-01: `Alert` model lacks a CHECK constraint on `alert_type` and `severity` at the ORM level

**File:** `apps/api/app/models/alert.py:17-18`

**Issue:** The DB migration (`0012_alerts_digest_runs.py` lines 29-30) defines
`CONSTRAINT alerts_type_check CHECK (alert_type IN ('eval_regression', 'red_team_critical'))`
and a matching severity check. The ORM model uses bare `String(50)` / `String(20)` without
a corresponding `CheckConstraint` in `__table_args__`. This means application-level code
that constructs `Alert` objects bypassing the DB (e.g., tests using mocked sessions) can
write invalid `alert_type` values that would be rejected by the real DB, making unit
tests unreliable as regression guards.

**Fix:** Add `CheckConstraint` to `__table_args__`:
```python
from sqlalchemy import CheckConstraint
__table_args__ = (
    Index("alerts_agent_id_idx", "agent_id"),
    Index("alerts_resolved_at_idx", "resolved_at"),
    CheckConstraint("alert_type IN ('eval_regression', 'red_team_critical')", name="alerts_type_check"),
    CheckConstraint("severity IN ('warning', 'critical')", name="alerts_severity_check"),
)
```

---

### IN-02: `page.tsx` line 328 — dead branch: `step3Done` is hardcoded `false`, making `step3Done ? configurePanel : testPanel` always resolve to `testPanel`

**File:** `apps/admin/app/agents/[id]/page.tsx:146`, `apps/admin/app/agents/[id]/page.tsx:328`

**Issue:** `step3Done` is declared as `const step3Done = false` at line 146 with a
comment acknowledging this is a temporary stub. Line 328 evaluates
`step3Done ? configurePanel : testPanel` — the `true` branch (`configurePanel`) is
unreachable code. This is a known placeholder, but the dead branch means that even
after M6 is complete and `step3Done` should be dynamic, the hardcoded `false` will
silently prevent the configure panel from ever being shown again in that position.

**Fix:** Remove the ternary entirely (`panel = testPanel`) with a TODO comment, or
wire up the real `step3Done` derivation from an actual query. Leaving an always-false
ternary in the dispatch logic is a maintenance trap.

---

_Reviewed: 2026-05-25T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
