---
phase: 14
slug: transactional-tool-contract-capability-audit-substrate-typed
status: verified
threats_open: 0
asvs_level: 1
created: 2026-06-30
---

# Phase 14 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Register authored at plan time (STRIDE across all 8 plans); mitigations
> independently verified against the implementation by gsd-security-auditor.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Alembic migration → control DB schema | A wrong default (`enabled=true`) or a missing UNIQUE silently disables the whole phase's safety posture | DDL; capability/idempotency/audit schema |
| LLM tool args → typed Pydantic schemas | Untrusted, possibly prompt-injected arguments cross into the execution path | Tool arguments (amounts, ids, free text) |
| Dispatcher → capability envelope check | The single fail-closed gate deciding whether a mutating tool may run | `agent_id`, `skill`, capability snapshot |
| Dispatcher → reserve_idempotency (control DB) | Atomic reserve-before-execute; the DB decides the single winner under redelivery | `agent_id`, `skill`, `idempotency_key`, `args_hash` |
| Dispatcher → Actor seam (`call_actor_gate`) | The ONLY pre-execution hook for mutating tools; Phase 15 fills it | `agent_id`, `skill`, raw args |
| Tool execution → `tool_calls_audit` | The forensic record; an audit gap defeats retrospective alerting | `agent_id`, `skill`, arguments, result/error |
| `confirm_action` → `pending_confirmations` | Human-in-the-loop confirmation requests awaiting Phase 18 resolution | `agent_id`, `skill`, `action_reference` |
| Worker → Redis (rate limit / `rediss://` TLS) | Rate-limit counters and TLS transport for the runtime broker | counters; TLS-protected connection |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-14-01-01 | Elevation of Privilege | `capability_envelopes.enabled` default | mitigate | `enabled BOOLEAN NOT NULL DEFAULT false` (fail-closed) — `0014_transactional_substrate.py:47` | closed |
| T-14-01-02 | Repudiation | idempotency uniqueness | mitigate | `CONSTRAINT uq_tool_idempotency_keys UNIQUE (agent_id, skill, idempotency_key)` — `0014:119` | closed |
| T-14-01-03 | Information Disclosure | cross-tenant audit contamination | mitigate | `agent_id UUID NOT NULL` on all 4 tables; no tenant-PII columns — `0014:41,68,91,113` | closed |
| T-14-01-04 | Tampering | migration lineage | mitigate | `down_revision = "0013"`; roundtrip test asserts revision strings — `0014:32` | closed |
| T-14-01-SC | Tampering (supply chain) | dependency supply chain | accept | No new packages (alembic/sqlalchemy/psycopg2 already pinned) | closed |
| T-14-02-01 | Tampering | coerced tool args via prompt injection | mitigate | Typed Pydantic schemas; amount fields `Annotated[int, Field(ge=0)]`; no blob/SQL/URL/open-dict — `schemas.py:67-70,145-148` | closed |
| T-14-02-02 | Elevation of Privilege | runtime-inferred `mutating` flag | mitigate | `mutating: bool` is a literal definition-time field in `TransactionalToolDef` — `registry.py:59,82,97,113,128,141,157,172` | closed |
| T-14-02-03 | Tampering | real side effects leaking in Phase 14 | mitigate | `StubProviderAdapter` returns `[STUB]` outputs; `get_adapter()` always returns `_STUB_ADAPTER`; no network — `provider_adapter.py:81-171` | closed |
| T-14-02-SC | Tampering (supply chain) | dependency supply chain | accept | No new packages (pydantic/anthropic already pinned) | closed |
| T-14-03-01 | Elevation of Privilege | missing/disabled envelope treated as enabled | mitigate | Fail-closed: `row is None → no_envelope_row`; `not enabled → disabled`; no pass-through — `enforcement.py:237-263` | closed |
| T-14-03-02 | Repudiation | replay double-execute under acks_late | mitigate | control-DB `INSERT … ON CONFLICT DO NOTHING RETURNING` — `idempotency.py:174-188,473-478` | closed |
| T-14-03-03 | Information Disclosure | cross-tenant `capability_snapshot` | mitigate | Query scoped `WHERE agent_id = :a AND skill = :s LIMIT 1` — `enforcement.py:225-229` | closed |
| T-14-03-04 | Repudiation | audit gap on error path | mitigate | `write_audit_row` on success and every error path — `tools.py` (denial/mismatch/rate/block/error/success) | closed |
| T-14-03-05 | Denial of Service | rate-limit evasion | mitigate | Redis `INCR`+`EXPIRE` per `(agent_id, skill, window)` in one pipeline — `enforcement.py:311-319` | closed |
| T-14-03-SC | Tampering (supply chain) | dependency supply chain | accept | No new packages (redis/sqlalchemy/structlog already pinned) | closed |
| T-14-04-01 | Elevation of Privilege | bypassed Actor seam | mitigate | `call_actor_gate` awaited before adapter on every fresh execution; block → release + audit + is_error — `tools.py:292,324` | closed |
| T-14-04-02 | Repudiation | replay double-execute | mitigate | replay returns stored result before actor + adapter; `finalize_idempotency` on success only — `tools.py:200-208,377` | closed |
| T-14-04-03 | Tampering | capability bypass via `allowed_tools` listing | mitigate | `allowed_tools` suppresses SDK prompts only; handler envelope check is the real gate — `agent.py:562-585`, `tools.py:165` | closed |
| T-14-04-04 | Information Disclosure | cross-tenant audit/confirmation rows | mitigate | `agent_id` from per-call ContextVar; all writes scoped to it — `tools.py:143,590` | closed |
| T-14-04-05 | Repudiation | `confirm_action` duplicate rows | accept | Resolution dedup deferred to Phase 18 (resolves pending rows); PRD DDL unchanged. See accepted risks. | closed |
| T-14-04-SC | Tampering (supply chain) | dependency supply chain | accept | No new packages (claude_agent_sdk already pinned) | closed |
| T-14-05-01 | Tampering | legacy rows after column add | mitigate | `status TEXT NOT NULL DEFAULT 'completed'` — `0015_idempotency_reservation.py:63` | closed |
| T-14-05-02 | Denial of Service | downgrade path | mitigate | downgrade backfills `result = '{}'::jsonb WHERE result IS NULL` before `SET NOT NULL` — `0015:99-107` | closed |
| T-14-05-SC | Tampering (supply chain) | package installs | accept | No new packages (stdlib + existing sqlalchemy/alembic) | closed |
| T-14-06-01 | Elevation/Tampering | double-execution under concurrent redelivery | mitigate | atomic `INSERT … ON CONFLICT … DO NOTHING RETURNING`; loser → in_progress/replay, never executes — `idempotency.py:174-198` | closed |
| T-14-06-02 | Tampering | key reused with different args | mitigate | `compute_args_hash` excludes the key; `stored_hash != args_hash → args_mismatch` error — `idempotency.py:123-139,219-225` | closed |
| T-14-06-03 | Denial of Service | orphaned pending row deadlocks a key | mitigate | `_RESERVATION_LEASE_SECONDS = 120` + stale-pending reclaim `UPDATE … RETURNING` — `idempotency.py:92,243-285` | closed |
| T-14-06-04 | Information Disclosure | idempotency keys leaking to Redis | mitigate | control-DB only; zero `import redis`; `TestNoRedisUsage` retained — `idempotency.py` | closed |
| T-14-06-SC | Tampering (supply chain) | package installs | accept | No new packages (stdlib hashlib/json/asyncio + existing sqlalchemy) | closed |
| T-14-07-01 | Information Disclosure / Tampering | `rediss://` with disabled cert verification (MITM) | mitigate | default `ssl.CERT_REQUIRED` + hostname check; relaxation only via explicit `REDIS_TLS_INSECURE` + warning log — `enforcement.py:107-123` | closed |
| T-14-07-02 | Elevation of Privilege | fail-open if access check regresses | mitigate | `no_envelope_row`/`disabled` denials first; facade returns before any side effect — `enforcement.py:237-263,407-409` | closed |
| T-14-07-03 | Tampering | `amount_cents=0` mis-evaluates `max_amount_cents` | mitigate | explicit `None`-check amount selection (not falsy-`or`) — `enforcement.py:345-347` | closed |
| T-14-07-04 | Denial of Service | TTL-less rate-limit key leak | mitigate | `INCR`+`EXPIRE` issued atomically in one pipeline — `enforcement.py:315-319` | closed |
| T-14-07-SC | Tampering (supply chain) | package installs | accept | No new packages (existing redis/sqlalchemy + stdlib ssl/asyncio) | closed |
| T-14-08-01 | Elevation/Tampering | double-execution at the dispatcher | mitigate | `reserve_idempotency` (step 3) before actor (step 5) + adapter (step 6) — `tools.py:196,292,324` | closed |
| T-14-08-02 | Tampering | replay consuming rate budget | mitigate | replay branch returns cached result before `apply_rate_and_constraint_checks` (WR-01) — `tools.py:200-208,262` | closed |
| T-14-08-03 | Spoofing/Tampering | key reused with different args returns wrong result | mitigate | `args_mismatch → is_error`, no replay, no execute (WR-02) — `tools.py:210-239` | closed |
| T-14-08-04 | Denial of Service | reservation orphaned on denial/block/error | mitigate | `release_idempotency` on rate-denial, actor-block, adapter-error paths — `tools.py:265,296,336` | closed |
| T-14-08-05 | Denial of Service | unbounded `pending_confirmations` rows | mitigate | Capability gate (`check_capability_access` — `tools.py:609`) **+** durable dedup: partial unique index `uq_pending_confirmations_unresolved (agent_id, skill, arguments->>'action_reference') WHERE resolved_at IS NULL` (`0016_pending_confirmations_dedup_index.py`; mirrored in `pending_confirmation.py` model) — `confirm_action_tool` inserts via ORM and on `IntegrityError` returns the existing pending row instead of a duplicate — `tools.py` confirm_action commit/except block. **Remediated in this audit (commit below).** | closed |
| T-14-08-06 | Tampering | invalid-UUID insert from unset `agent_id` | mitigate | IN-03 precondition guard returns is_error before any DB write — `tools.py:148-160,593-605` | closed |
| T-14-08-07 | Repudiation | missing audit row on a failure path | mitigate | `write_audit_row` on capability denial, args_mismatch (AUD-01, commit a18dd35), rate/constraint denial, actor block, adapter error, success — `tools.py` | closed |
| T-14-08-SC | Tampering (supply chain) | package installs | accept | No new packages | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-14-01 | T-14-04-05 | `confirm_action` (mutating=False, no idempotency key) duplicate **resolution** dedup is deferred to Phase 18 (which builds the resolution UI/logic). Outstanding-row growth is now bounded by T-14-08-05's partial unique index; PRD DDL otherwise unchanged. | Mfanafuthi Mhlanga | 2026-06-30 |
| AR-14-SC | T-14-01-SC, T-14-02-SC, T-14-03-SC, T-14-04-SC, T-14-05-SC, T-14-06-SC, T-14-07-SC, T-14-08-SC | Supply-chain (package-install) threat accepted for all 8 plans: Phase 14 installed **no new packages** — only stdlib plus already-pinned, already-in-use deps (alembic, sqlalchemy, psycopg2, pydantic, anthropic, redis, structlog, claude_agent_sdk). | Mfanafuthi Mhlanga | 2026-06-30 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-06-30 | 42 | 41 | 1 | gsd-security-auditor (initial verification — T-14-08-05 open: declared dedup absent, `confirm_action` did a plain insert) |
| 2026-06-30 | 42 | 42 | 0 | orchestrator (remediated T-14-08-05: migration 0016 partial unique index + `IntegrityError` dedup in `confirm_action`; corrected overclaiming docstrings; 69 unit tests pass) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-06-30
