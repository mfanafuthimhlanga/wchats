---
phase: 17
slug: customer-identity-verification-email-sms-otp-per-skill-serve
status: verified
threats_open: 0
asvs_level: 2
created: 2026-07-04
---

# SECURITY.md — Phase 17 Threat Verification

**Phase:** 17 — Customer Identity Verification (Email/SMS OTP, per skill, server-enforced)
**Audited:** 2026-07-04
**ASVS Level:** 2
**Auditor:** gsd-security-auditor (Claude Sonnet 5)
**block_on:** high
**Threats Closed:** 20/20 (19 mitigate + 1 accept)
**Register authored at plan time; re-verified against post-review-fix code (commits through `d4cd7e8`).**

---

## Threat Verification Results

### Closed Threats

| Threat ID | Category | Disposition | Evidence |
|-----------|----------|-------------|----------|
| T-17-01 | Info Disclosure / IDOR | mitigate | `apps/api/app/services/identity_service.py:452-487` — `check_verified_session` queries only `session_token_hash` + `session_expires_at` against the tenant `conn_str`; no `agent_id` filter (OD-1). `apps/api/alembic_tenant/versions/0008_customer_identities.py:55-66` — schema has no plaintext token/code column, only `session_token_hash TEXT NOT NULL`. |
| T-17-02 | EoP (IDOR) | mitigate | `apps/api/alembic_tenant/versions/0008_customer_identities.py` — table defined in the tenant-DB migration tree only. Confirmed: `apps/api/alembic/versions/` (control DB) contains no `customer_identities` migration — `ls` of that directory returns no match. |
| T-17-03 | Tampering | mitigate | `0008_customer_identities.py:55,67,71` (`CREATE TABLE/INDEX IF NOT EXISTS`) and `:78-80` (`DROP INDEX/TABLE IF EXISTS`) — all DDL additive-only and idempotent. |
| T-17-04 | EoP (brute force) | mitigate | `identity_service.py:387-388` fast-path lockout (`attempts >= OTP_MAX_ATTEMPTS`), `:395-397` Nth-attempt lockout; `config.py:95` `OTP_MAX_ATTEMPTS: int = 5`. CR-01 confirmed: `identity_service.py:394` `await redis.set(key, json.dumps(data), keepttl=True)` preserves the original TTL on a wrong-attempt write (no TTL reset/extension bug). |
| T-17-05 | Repudiation (replay) | mitigate | `identity_service.py:401` `await redis.delete(key)` executes immediately before `:404` `generate_session_token()` — delete-first / single-use confirmed by line order. |
| T-17-06 | Info Disclosure (timing) | mitigate* | `identity_service.py:94` `hmac.compare_digest(stored_hash, hash_otp_code(submitted_code))` — constant-time compare for the brute-forceable 6-digit OTP code (the actual timing-attack-relevant secret, 10^6 keyspace). *Note: `check_verified_session` (`:469-483`) compares the 256-bit session-token hash via a SQL `WHERE session_token_hash = %s` equality, not an in-process `hmac.compare_digest`. This is standard hash-then-DB-lookup practice for high-entropy tokens (`secrets.token_urlsafe(32)`) and not a practically exploitable channel — flagged as a minor observation, not a blocker, since the register's literal wording ("hmac.compare_digest for all comparisons") is not applied to the session-lookup path. |
| T-17-07 | EoP (session fixation) | mitigate | `identity_service.py:404` `generate_session_token()` = `secrets.token_urlsafe(32)`, invoked server-side only after `:390` code-correctness check passes. `apps/api/app/schemas/widget.py:69-80` `OtpVerifyBody` has no token field — the client can only submit `otp_code`, never inject a token. |
| T-17-08 | Info Disclosure (at rest) | mitigate | `identity_service.py:79-85` `hash_otp_code` and `:106-113` `hash_session_token` both SHA-256; `:147` `store_otp_challenge` stores `code_hash` only; `:415-422` UPSERT stores `session_token_hash` only; `:449` raw token returned once, never persisted. |
| T-17-11 | Info Disclosure (logs) | mitigate | Grep across `identity_service.py`, `agent_tools.py`, `agent.py`, `widget.py` for `log.*` calls referencing `otp_code` / `verified_session_token` returns zero matches. IN-04 confirmed: `identity_service.py:258,261,282,285` — `to_domain=_log_domain` (domain-only, e.g. `example.com`) replaces the prior full-address `to=to_email` field in all three `send_otp_email` log calls. |
| T-17-12 | Tampering (ContextVar bleed) | mitigate | `agent_tools.py:160` `ContextVar("verified_session_token", default="")`; `:631` `.set()` call is scoped per-task by `build_tool_server`, called once per Celery task invocation (PROD-14 precedent documented at `:592-596`). |
| T-17-13 | EoP (agent-prose bypass) | mitigate | `apps/api/app/services/transactional/tools.py:208-301` — Step 2.5 gate is deterministic Python (`snapshot.get(...)`, `_verified_session_token_var.get()`, `check_verified_session()`); no agent/LLM output is read anywhere in the block. Gate precedes the adapter call (Step 6, `:487+`) unconditionally. |
| T-17-14 | EoP (replay/expiry) | mitigate | `identity_service.py:477-479` SQL `WHERE session_token_hash = %s AND session_expires_at > NOW()` re-evaluated on every call (no caching). WR-02 confirmed: `tools.py:242-275` wraps `check_verified_session` in `try/except Exception`, fails **closed** (`is_error: True`) and writes an audit row with `error="identity_verification.check_failed"` on any DB exception — verified in `identity_service.py` via the same code path exercised by the `OtpStorageError` design pattern. |
| T-17-16 | Info Disclosure (Twilio creds) | mitigate | `config.py:101-106` — `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `AT_API_KEY`, `AT_USERNAME`, `AT_SENDER_ID` all `str \| None = None`. `pyproject.toml` grepped for SID/TOKEN/API_KEY literals — zero matches. |
| T-17-17 | Spoofing (agent fabricating token) | **accept** | Verified the accept rationale holds. Grep for `_verified_session_token_var.set(` across the entire `apps/api` tree returns exactly one production call site: `agent_tools.py:631`, inside `build_tool_server`. `build_tool_server` is called only from `agent.py:532-540` (`run_agent_turn`), with the value sourced exclusively from the Celery task's 5th positional argument (`agent.py:404`), itself populated only from `widget.py:437-446` (`body.verified_session_token or ""`, from the client's `WidgetChatRequest`). No `@tool`-decorated handler (in `agent_tools.py` or `transactional/tools.py`) writes to this ContextVar — the agent has no code path to set or influence it mid-turn. |
| T-17-18 | DoS (SMS cost / flooding) | mitigate | `identity_service.py:326-333` — per-`external_id` send cap via atomic `redis.set(nx=True, ex=window_ttl)` + `incr`, ceiling `OTP_SEND_MAX_PER_WINDOW`. `widget.py:581-590` — per-IP 10/min on `/identity/request` via the same atomic pattern. Both identity routes require `validate_widget_jwt` first (`:574`, `:653`). |
| T-17-19 | Info Disclosure (enumeration) | mitigate | `widget.py:608-611` — `/identity/request` always returns `204` regardless of `external_id` validity. `widget.py:690-694` — `/identity/verify` raises the identical `HTTPException(400, detail="Invalid or expired code")` for both the wrong-code and expired-code cases (both raise `OtpInvalid` in `identity_service.py:378` and `:398` with no distinguishing detail surfaced to the route). |
| T-17-20 | Spoofing (JWT bypass) | mitigate | `widget.py:574` and `:653` — `validate_widget_jwt(credentials.credentials, str(agent_id))` is the first statement in both `post_widget_identity_request` and `post_widget_identity_verify`; `validate_widget_jwt` (`:181-205`) enforces signature + expiry + `agent_id` claim match. |
| T-17-21 | DoS / logic (idempotency slot) | mitigate | `tools.py` — the entire Step 2.5 IDV block (`:208-301`) precedes the `# ---- 3. Reserve idempotency` comment and `reserve_idempotency(...)` call (`:303-308`). Both IDV block-return paths (`required`, `check_failed`, `invalid_or_expired`) `return` before that line is ever reached. |
| T-17-22 | Repudiation (missing audit) | mitigate | `tools.py` — three distinct `write_audit_row(...)` calls inside Step 2.5: `error="identity_verification.required"` (`:218-229`), `error="identity_verification.check_failed"` (`:255-266`, the WR-02 addition), `error="identity_verification.invalid_or_expired"` (`:278-289`). Exactly one audit row per block path, including the new DB-error path — AUD-01 symmetry holds. |
| T-17-SC | Tampering (twilio supply chain) | mitigate | `.planning/phases/17.../17-02-PLAN.md` Task 1 — blocking `checkpoint:human-verify` gate requiring literal `"approved"` before the pin (confirmed executed per `17-02-SUMMARY.md` self-check). `pyproject.toml:50-51` — `twilio==9.10.9` exact pin with provenance comment. `identity_service.py:176` — `from twilio.rest import Client` imported lazily inside `send()`, not at module level, contained within `_deliver_otp`'s try/except (`:299-303`). |

**19/19 `mitigate`-disposition threats CLOSED. 1/1 `accept`-disposition threat (T-17-17) verified and holds.**

---

### Open Threats

None.

---

### Unregistered Flags

Checked `## Threat Flags` sections in all six plan summaries (17-01 through 17-06):

- `17-02-SUMMARY.md:89-91` — "No new threat surface introduced beyond what the threat model already covers."
- `17-04-SUMMARY.md:128-130` — "None — no new network endpoints, auth paths, file access patterns, or schema changes introduced."
- `17-05-SUMMARY.md:147-149` — "No new network endpoints, auth paths, or schema changes beyond what is documented in the plan's threat model."
- `17-01-SUMMARY.md`, `17-03-SUMMARY.md`, `17-06-SUMMARY.md` — no `## Threat Flags` heading present; these carry a `## Threat Coverage` / `## Threat Mitigations Verified` / `## Threat Mitigations Confirmed` section instead, each mapping cleanly to existing T-17-xx IDs (no new surface claimed).

**No unregistered flags found.**

---

### Additional Hardening Confirmed (Post-Plan Code Review + Fix Pass)

The task brief specified a code-review + fix pass occurred after the plans were authored. Cross-checked `17-REVIEW.md` and `17-REVIEW-FIX.md` against current code — all cited fixes are present:

| ID | Finding | Fix Verified In Code |
|----|---------|----------------------|
| CR-01 | `verify_otp` wrong-attempt write reset the Redis TTL | `identity_service.py:394` `redis.set(..., keepttl=True)` — confirmed present |
| CR-02 | SMS delivery could raise uncaught | `identity_service.py:299-303` `try/except` around `provider.send(...)`, mirrors email fire-and-forget pattern — confirmed present |
| WR-01 | `AfricasTalkingProvider.__init__` eagerly imported `africastalking` | `identity_service.py:194-201` — `import africastalking` moved inside `send()` — confirmed present |
| WR-02 | `check_verified_session` DB errors propagated as unhandled exceptions | `tools.py:242-275` — `try/except Exception`, fail-closed, audit row `identity_verification.check_failed` — confirmed present (also mapped to T-17-14 above) |
| IN-01 | Hardcoded test DB credentials | `tests/integration/test_migrations.py` — `os.getenv("TEST_ADMIN_DB_URL", ...)` — confirmed present (test-only, no production threat mapping) |
| IN-02 | `agent_id` unused in `check_verified_session` — maintenance trap | `identity_service.py:453` inline comment added — confirmed present |
| IN-03 | Raw `verified_session_token` at rest in Celery/Redis task args | `widget.py:431-436` — `THREAT MODEL NOTE (Phase 17 accepted trade-off)` comment documents the residual exposure and defers Fernet encryption to Phase 18. This is a **documented accepted risk**, not a T-17-xx register entry — logged below. |
| IN-04 | Email address logged as PII | `identity_service.py:258,261,282,285` — `to_domain=_log_domain` — confirmed present (mapped to T-17-11 above) |
| IN-05 | `verify_otp` UPSERT failure after Redis delete → bare 500 | `identity_service.py:56-62` `OtpStorageError`, `:440-447` catch+raise; `widget.py:701-708` → HTTP 503 with `Retry-After: 30` — confirmed present |

All 9 review findings confirmed fixed in the current committed code (matches `17-REVIEW-FIX.md` claim of 7/7 in-scope + 2 prior Critical/Warning).

---

### Accepted Risks Log

| Threat ID | Risk Description | Accepted Rationale | Accepted In |
|-----------|-------------------|---------------------|-------------|
| T-17-17 | Agent cannot fabricate a verified session token | Token origin is the Celery task's 5th positional arg only, sourced from the widget client's `WidgetChatRequest.verified_session_token`; the agent/LLM has no tool or code path to set `_verified_session_token_var`. Enforcement gate (Step 2.5, tools.py) additionally re-validates against the tenant DB on every call regardless of ContextVar provenance. | Plan 17-03 (T-17-17), re-verified 2026-07-04 |
| IN-03 (not a T-17-xx ID) | Raw `verified_session_token` is at rest in Redis for the duration of Celery `runtime`-queue processing (JSON-serialized task args) | Redis is not a public endpoint; token TTL is bounded (`VERIFIED_SESSION_TTL_SECONDS=3600`); token grants access only to IDV-gated tools, not admin/tenant-level access. Full mitigation (Fernet encryption of the token before task dispatch, consistent with the `neon_connection_string` pattern) is explicitly deferred to Phase 18. | `widget.py:431-436` (17-REVIEW-FIX.md commit `42a032a`) |
| T-17-06 (partial/minor) | `check_verified_session` compares the session-token hash via SQL equality rather than `hmac.compare_digest` | 256-bit token (`secrets.token_urlsafe(32)`), SHA-256 hashed before lookup; hash-then-DB-lookup is standard practice for high-entropy tokens and not a practically exploitable timing channel, unlike the 6-digit OTP code (which IS protected by `hmac.compare_digest`). Not elevated to BLOCKER. | This audit, 2026-07-04 |

---

## Audit Trail

**Required reading loaded (all files, before any analysis):**
- `.planning/phases/17-customer-identity-verification-email-sms-otp-per-skill-serve/17-0{1,2,3,4,5,6}-PLAN.md`
- `.planning/phases/17-customer-identity-verification-email-sms-otp-per-skill-serve/17-0{1,2,3,4,5,6}-SUMMARY.md`
- `.planning/phases/17-customer-identity-verification-email-sms-otp-per-skill-serve/17-VERIFICATION.md`
- `.planning/phases/17-customer-identity-verification-email-sms-otp-per-skill-serve/17-REVIEW.md`
- `.planning/phases/17-customer-identity-verification-email-sms-otp-per-skill-serve/17-REVIEW-FIX.md`

**Implementation files read (read-only, never modified):**
- `apps/api/app/services/identity_service.py` (full, 488 lines)
- `apps/api/app/services/transactional/tools.py` (full, 917 lines)
- `apps/api/app/api/v1/widget.py` (full, 759 lines)
- `apps/api/app/schemas/widget.py` (full, 100 lines)
- `apps/api/app/services/agent_tools.py` (lines 139-160, 560-640)
- `apps/api/app/worker/tasks/runtime/agent.py` (lines 390-540)
- `apps/api/app/core/config.py` (M17 settings block, lines 91-106)
- `apps/api/alembic_tenant/versions/0008_customer_identities.py` (full, 81 lines)
- `apps/api/pyproject.toml` (dependency block + twilio pin)

**Corroborating behavioral check (not sole evidence, supplementary to source grep):**
`cd apps/api && python -m pytest tests/unit/test_identity_service.py tests/unit/test_identity_routes.py tests/unit/test_transactional_tools.py tests/unit/test_agent_tools_contextvar.py -q` → **141 passed**, 0 failed.

**No implementation files were modified.**

---

_Verified: 2026-07-04_
_Verifier: Claude Sonnet 5 (gsd-security-auditor)_
