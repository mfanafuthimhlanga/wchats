---
phase: "01-control-plane-skeleton"
plan: "02"
subsystem: "security, events, worker"
tags: ["fernet", "argon2", "emit", "celery", "redis", "pub-sub", "tdd"]
dependency_graph:
  requires:
    - "apps/api/app/core/config.py — Settings.NEON_ENCRYPTION_KEY and REDIS_URL"
    - "apps/api/app/models/job_event.py — JobEvent ORM model for emit() DB persist"
  provides:
    - "apps/api/app/core/security.py — fernet_encrypt, fernet_decrypt, hash_api_key, verify_api_key, generate_api_key"
    - "apps/api/app/services/events.py — emit() helper: atomic DB persist + Redis publish"
    - "apps/api/app/worker/celery_app.py — Celery factory: pipeline+runtime queues, acks_late, task_prerun signal"
  affects:
    - "Wave 3 (01-03) — provision_neon and apply_migrations tasks use fernet_encrypt/decrypt and emit()"
    - "Wave 4 (01-04) — FastAPI POST /tenants uses generate_api_key() and hash_api_key()"
    - "Wave 4 (01-04) — Auth dependency uses verify_api_key()"
    - "All Celery tasks — inherit acks_late=True and queue routing from celery_app.conf"
tech_stack:
  added: []
  patterns:
    - "Module-level PasswordHasher singleton avoids recreating argon2 tuning params per call"
    - "emit() accepts db+redis as arguments — callers own session lifecycle, tests inject mocks"
    - "emit() copies caller's payload dict before mutation (no side-effects)"
    - "task_prerun signal clears structlog contextvars before each Celery task (prevents request_id bleed)"
    - "TDD: test first (RED commit) then implement (GREEN commit) for Tasks 1 and 2"
key_files:
  created:
    - "apps/api/app/core/security.py"
    - "apps/api/app/services/events.py"
    - "apps/api/app/worker/celery_app.py"
    - "apps/api/tests/unit/test_security.py"
    - "apps/api/tests/unit/test_emit.py"
  modified: []
decisions:
  - "verify_api_key returns bool only (never raises VerifyMismatchError) — callers use if/else not try/except"
  - "emit() makes a copy of payload before adding 'at' timestamp — caller's dict is never mutated"
  - "task_default_queue=runtime so unrouted tasks don't accidentally land in pipeline queue"
  - "task_routes uses app.worker.tasks.pipeline.* pattern to auto-route all pipeline task modules"
metrics:
  duration: "~35 minutes"
  completed_date: "2026-05-12"
  tasks_completed: 3
  files_created: 5
---

# Phase 01 Plan 02: Security Helpers, emit() Helper, Celery App Factory Summary

Fernet encryption/decryption, argon2id API key hashing, the emit() event helper with atomic DB+Redis semantics, and the Celery app factory with two-queue configuration and global reliability settings implemented via TDD.

## Tasks Completed

| # | Name | Commit | Key Files |
|---|------|--------|-----------|
| 1 (RED) | Failing tests for security helpers | 0231488 | tests/unit/test_security.py |
| 1 (GREEN) | Security helpers — Fernet + argon2 | 0a943b2 | app/core/security.py |
| 2 (RED) | Failing tests for emit() helper | 2e8ea97 | tests/unit/test_emit.py |
| 2 (GREEN) | emit() helper — atomic DB persist + Redis publish | 001330e | app/services/events.py |
| 3 | Celery app factory — two queues + reliability settings | 51ae8c9 | app/worker/celery_app.py |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] "bcrypt" string in security.py docstring failed acceptance criterion**
- **Found during:** Task 1 GREEN phase verification
- **Issue:** The plan acceptance criterion requires zero occurrences of "bcrypt" in security.py. The initial docstring contained the word in "Do NOT import hashlib or bcrypt" comment text.
- **Fix:** Rewrote the docstring prohibition comment to not contain the literal string "bcrypt". Same intent, different wording.
- **Files modified:** `apps/api/app/core/security.py`
- **Commit:** 0a943b2

**2. [Rule 1 - Bug] "import hashlib" literal string in security.py docstring failed acceptance criterion**
- **Found during:** Task 1 GREEN phase — second test run after fixing bug #1
- **Issue:** The plan acceptance criterion also checks for "import hashlib" absence. The revised comment "Do NOT import hashlib or any alternative key-derivation library" contained the exact string "import hashlib".
- **Fix:** Rewrote to "Do not bring in standard-library key-derivation modules" — describes the prohibition without using the forbidden string.
- **Files modified:** `apps/api/app/core/security.py`
- **Commit:** 0a943b2

**3. [Rule 1 - Bug] Invalid Fernet key (27 bytes) in test_emit.py caused cross-test contamination**
- **Found during:** Task 3 — running full unit test suite after celery_app.py was written
- **Issue:** `test_emit.py` set `NEON_ENCRYPTION_KEY` to a hardcoded base64 string that decoded to 27 bytes. Fernet requires exactly 32 bytes. When pytest ran both test files in the same process, os.environ.setdefault won with the 27-byte key before test_security.py's valid key was set, causing test_security.py's fernet tests to fail.
- **Fix:** Changed the hardcoded key in test_emit.py to use `base64.urlsafe_b64encode(os.urandom(32)).decode()` — same pattern as test_security.py.
- **Files modified:** `apps/api/tests/unit/test_emit.py`
- **Commit:** 51ae8c9

## Verification Results

All 5 plan-specified checks pass:

1. `python -c "from app.core.security import fernet_encrypt, fernet_decrypt; ct=fernet_encrypt('test'); assert fernet_decrypt(ct)=='test'; print('OK')"` — PASSED
2. `python -c "from app.core.security import hash_api_key, verify_api_key; h=hash_api_key('k'); assert verify_api_key(h,'k')==True; assert verify_api_key(h,'x')==False; print('OK')"` — PASSED
3. `python -c "from app.services.events import emit; print('emit importable')"` — PASSED
4. `python -c "from app.worker.celery_app import celery_app; assert celery_app.conf.task_acks_late==True; print('celery OK')"` — PASSED
5. `grep -c "BaseHTTPMiddleware" app/core/security.py` — 0 (PASSED)

Unit test suite: 31 tests passed (14 security + 17 emit).

## Known Stubs

None. All three modules implement full production behaviour with no placeholder values or hardcoded returns. emit() makes real DB and Redis calls (injectable for testing). security.py reads NEON_ENCRYPTION_KEY from settings at call time.

## Threat Flags

No new network endpoints, auth paths, or file access patterns introduced beyond the plan's threat model.

- T-02-01 (fernet_decrypt logging): fernet_decrypt does not log; docstring explicitly warns caller not to log the return value.
- T-02-02 (verify_api_key timing): returns bool only; argon2-cffi verify() is timing-safe.
- T-02-03 (emit payload PII): M1 payloads contain only job metadata.
- T-02-04 (pickle deserialization): JSON serializer enforced at all Celery levels; pickle not referenced anywhere.
- T-02-05 (task submission rate limiting): Accepted; M1 is internal API only.

## Self-Check: PASSED

Files verified to exist:
- apps/api/app/core/security.py — FOUND
- apps/api/app/services/events.py — FOUND
- apps/api/app/worker/celery_app.py — FOUND
- apps/api/tests/unit/test_security.py — FOUND
- apps/api/tests/unit/test_emit.py — FOUND

Commits verified:
- 0231488 — test(01-02): RED phase security tests
- 0a943b2 — feat(01-02): security.py implementation
- 2e8ea97 — test(01-02): RED phase emit tests
- 001330e — feat(01-02): events.py implementation
- 51ae8c9 — feat(01-02): celery_app.py implementation
