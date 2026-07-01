---
phase: 17
slug: customer-identity-verification-email-sms-otp-per-skill-server-enforced
status: ready
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-01
---

# Phase 17 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 17-RESEARCH.md "## Validation Architecture" and the 6 PLAN.md `<verify>` blocks.
> `nyquist_compliant` / `wave_0_complete` flip to `true` during execution once the test files below exist and pass (they are co-created by the TDD tasks in 17-03/17-04/17-06 and the route/migration tasks in 17-05/17-01).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing in `apps/api` dev dependencies) |
| **Config file** | `apps/api/pytest.ini` (existing) |
| **Quick run command** | `cd apps/api && python -m pytest tests/unit/test_identity_service.py tests/unit/test_transactional_tools.py -x -q` |
| **Full suite command** | `cd apps/api && python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~30 seconds (unit, mocked Redis/SMTP/SMS); migration integration test needs a live local tenant Postgres |

---

## Sampling Rate

- **After every task commit:** Run `cd apps/api && python -m pytest tests/unit/test_identity_service.py tests/unit/test_transactional_tools.py -x -q`
- **After every plan wave:** Run `cd apps/api && python -m pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~30 seconds (unit); the one integration test (migration 0008 roundtrip) requires a live local tenant Postgres — no Docker

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 17-01-01 | 01 | 1 | IDV-01 | — | Config exposes `VERIFIED_SESSION_TTL_SECONDS=3600`, `OTP_MAX_ATTEMPTS=5`, `SMS_PROVIDER='twilio'`; provider creds default `None` (no hard-coded secrets) | unit | `cd apps/api && python -c "from app.core.config import settings; assert settings.VERIFIED_SESSION_TTL_SECONDS==3600; assert settings.OTP_MAX_ATTEMPTS==5; assert settings.SMS_PROVIDER=='twilio'; assert settings.TWILIO_ACCOUNT_SID is None; print('config-ok')"` | ✅ existing | ⬜ pending |
| 17-01-02 | 01 | 1 | IDV-01 | cross-tenant IDOR | Migration 0008 chains from 0007; `customer_identities` is per-tenant (no `agent_id`), `UNIQUE(external_id)` | unit | `cd apps/api && python -c "import importlib.util,glob; p=glob.glob('alembic_tenant/versions/0008_customer_identities.py')[0]; s=importlib.util.spec_from_file_location('m8',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); assert m.revision=='0008'; assert m.down_revision=='0007'; print('migration-module-ok')"` | ✅ existing | ⬜ pending |
| 17-01-03 | 01 | 1 | IDV-01 | — | `customer_identities` exists in a **live** tenant DB after `alembic upgrade head`; migration is idempotent on re-run | integration | `cd apps/api && python -m pytest tests/integration/test_migrations.py -k "0008 or customer_identities" -x -q` | ❌ W0 | ⬜ pending |
| 17-02-01 | 02 | 1 | IDV-03 | supply-chain (T-17-SC) | `twilio` package verified legitimate (pypi + github + exact pin) via **blocking-human** checkpoint before pinning | manual | checkpoint:human-verify (exempt from automated verify) | n/a | ⬜ pending |
| 17-02-02 | 02 | 1 | IDV-03 | supply-chain (T-17-SC) | `twilio==9.10.9` exact pin present; import succeeds | unit | `cd apps/api && grep -q 'twilio==9.10.9' pyproject.toml && python -c "import twilio; print('twilio', twilio.__version__)"` | ✅ existing | ⬜ pending |
| 17-03-01 | 03 | 1 | IDV-05 | agent-prose bypass | `_verified_session_token_var` ContextVar defaults `''`; `build_tool_server` accepts `verified_session_token` param — agent cannot set it | unit | `cd apps/api && python -c "import inspect; from app.services.agent_tools import build_tool_server, _verified_session_token_var; assert _verified_session_token_var.get()==''; assert 'verified_session_token' in inspect.signature(build_tool_server).parameters; print('contextvar-ok')"` | ✅ existing | ⬜ pending |
| 17-03-02 | 03 | 1 | IDV-05 | agent-prose bypass | `run_agent_turn` task carries `verified_session_token` (default `''`); token is NOT a connection string | unit | `cd apps/api && python -c "import inspect; from app.worker.tasks.runtime.agent import run_agent_turn; params=inspect.signature(run_agent_turn.run).parameters; assert 'verified_session_token' in params; assert params['verified_session_token'].default==''; print('task-signature-ok')"` | ✅ existing | ⬜ pending |
| 17-03-03 | 03 | 1 | IDV-05 | session theft (no-log) | ContextVar threads end-to-end; token never logged | unit | `cd apps/api && python -m pytest tests/unit/test_agent_tools_contextvar.py -x -q` | ❌ W0 | ⬜ pending |
| 17-04-01 | 04 | 2 | IDV-02, IDV-05 | brute force, timing, replay | 6-digit code from `secrets`; stored SHA-256 (never plaintext); `hmac.compare_digest`; session token hashed; single-use Redis key | unit (tdd) | `cd apps/api && python -m pytest tests/unit/test_identity_service.py -k "otp_code or hash or session_token or redis_key" -x -q` | ❌ W0 | ⬜ pending |
| 17-04-02 | 04 | 2 | IDV-02, IDV-03 | SMS cost-abuse, delivery | Email via fire-and-forget SMTP (never raises); SMS via `SmsProvider` seam (Twilio default, swappable) | unit (tdd) | `cd apps/api && python -m pytest tests/unit/test_identity_service.py -k "email or sms or provider or deliver" -x -q` | ❌ W0 | ⬜ pending |
| 17-04-03 | 04 | 2 | IDV-02, IDV-05 | replay, session expiry, lockout | `check_verified_session` queries tenant DB, enforces `session_expires_at > NOW()`; 5th wrong attempt locks out; expired code → 400 | unit (tdd) | `cd apps/api && python -m pytest tests/unit/test_identity_service.py -x -q` | ❌ W0 | ⬜ pending |
| 17-05-01 | 05 | 3 | IDV-02, IDV-03 | enumeration oracle | Request/verify schemas; uniform 400 for wrong vs expired code (no oracle) | unit | `cd apps/api && python -m pytest tests/unit/test_identity_routes.py -k "schema" -x -q` | ❌ W0 | ⬜ pending |
| 17-05-02 | 05 | 3 | IDV-02, IDV-03 | SMS flooding | Routes validate widget JWT first; per-IP 10/min + per-external_id 3/10min rate limits; 200/400/429 responses | unit | `cd apps/api && python -m pytest tests/unit/test_identity_routes.py -x -q` | ❌ W0 | ⬜ pending |
| 17-05-03 | 05 | 3 | IDV-05 | session theft (no-log) | Raw `verified_session_token` transported client→task arg unmodified; never logged | unit | `cd apps/api && python -m pytest tests/unit/test_identity_routes.py -k "dispatch or chat_token" -x -q` | ❌ W0 | ⬜ pending |
| 17-06-01 | 06 | 3 | IDV-04, IDV-05 | agent-prose bypass, IDOR | Step 2.5 gate blocks when `requires_identity_verification=True` and no/expired session; runs BEFORE `reserve_idempotency`; writes audit row | unit (tdd) | `cd apps/api && python -m pytest tests/unit/test_transactional_tools.py -k "idv" -x -q` | ❌ W0 | ⬜ pending |
| 17-06-02 | 06 | 3 | IDV-04, IDV-05 | agent-prose bypass | Full dispatcher suite green; valid session → tool proceeds; idempotency key reusable after IDV failure | unit (tdd) | `cd apps/api && python -m pytest tests/unit/test_transactional_tools.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

These test files are co-created by the plans that produce the corresponding code (TDD tasks in 17-04 and 17-06; scaffolds in 17-03/17-05/17-01) — there is no separate Wave 0 plan. Confirm each exists and is green before its wave merges.

- [ ] `apps/api/tests/unit/test_identity_service.py` — OTP engine, session issuance, email/SMS seam, `check_verified_session` (IDV-01..03, IDV-05 service layer) — created in 17-04
- [ ] `apps/api/tests/unit/test_agent_tools_contextvar.py` — ContextVar threading (IDV-05 plumbing) — created in 17-03
- [ ] `apps/api/tests/unit/test_identity_routes.py` — HTTP endpoint behavior, rate limiting, JWT validation, 200/400/429 (IDV-02/03/05) — created in 17-05
- [ ] `apps/api/tests/unit/test_transactional_tools.py` — Step 2.5 IDV gate (IDV-04/05) — created/extended in 17-06
- [ ] `apps/api/tests/integration/test_migrations.py` — migration 0008 live roundtrip (IDV-01) — created in 17-01
- [ ] Test fixtures: mock Redis for OTP challenge state; mock SMTP for email delivery; mock `SmsProvider`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `twilio` package is legitimate before pinning | IDV-03 | Supply-chain [SUS] gate — a new external dependency must be human-verified, never auto-approved | 17-02 Task 1 checkpoint: confirm `pypi.org/project/twilio` + `github.com/twilio/twilio-python`, then approve the exact `twilio==9.10.9` pin |
| Real OTP delivery to a live inbox / ZA phone number | IDV-02, IDV-03 | Automated tests mock SMTP and the SMS provider; real deliverability + Twilio cost/latency can only be observed against live providers | During UAT: request an OTP to a real email + a real ZA (+27) mobile, confirm receipt within the code TTL, and verify the code issues a session |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies — **met** (every task above has an `<automated>` command; the one checkpoint task is exempt)
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify — **met**
- [ ] Wave 0 covers all MISSING references — pending (test files land during execution)
- [ ] No watch-mode flags — **met** (all commands `-x -q`, no `--watch`)
- [ ] Feedback latency < 30s — **met** for unit; integration migration test bounded by local Postgres
- [ ] `nyquist_compliant: true` set in frontmatter — pending (set once all Wave 0 test files exist and pass)

**Approval:** pending
