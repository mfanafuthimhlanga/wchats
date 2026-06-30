---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
plan: "01"
subsystem: credential-service
status: complete
tags: [credential-service, HKDF, Fernet, tenant-isolation, ContextVar, migration]
completed: "2026-06-30"
duration: "22 minutes"

dependency_graph:
  requires:
    - "apps/api/alembic_tenant/versions/0006_red_team_runs_status.py (chain head before 0007)"
    - "apps/api/app/core/config.py (NEON_ENCRYPTION_KEY pattern)"
    - "apps/api/app/services/agent_tools.py (ContextVar block)"
  provides:
    - "INT-01 substrate: integration_credentials tenant-DB table + PLATFORM_CREDENTIAL_KEY setting"
    - "INT-02 primitives: CredentialHandle + _derive_tenant_fernet + _fetch_credential_config"
    - "_tenant_id_var ContextVar as HKDF salt source (resolves RESEARCH Open Question 1)"
    - "adapters package init for Wave 2 adapter imports"
  affects:
    - "apps/api/app/worker/tasks/runtime/agent.py (build_tool_server call site)"
    - "apps/api/tests/conftest.py (PLATFORM_CREDENTIAL_KEY env var for test collection)"

tech_stack:
  added:
    - "PLATFORM_CREDENTIAL_KEY: str setting (no default, fail-fast)"
    - "cryptography.hazmat.primitives.kdf.hkdf.HKDF (already installed, new usage pattern)"
    - "asyncio.to_thread for psycopg2 in async credential fetch"
  patterns:
    - "HKDF(SHA-256, salt=tenant_id, info=b'wchats-integration-credential') per-call — no caching"
    - "frozen dataclass CredentialHandle with redacted __repr__/__str__"
    - "ContextVar tenant_id for HKDF salt — never passed as Celery task arg"

key_files:
  created:
    - "apps/api/alembic_tenant/versions/0007_integration_credentials.py"
    - "apps/api/app/services/transactional/credential_service.py"
    - "apps/api/app/services/transactional/adapters/__init__.py"
    - "apps/api/tests/unit/test_credential_service.py"
  modified:
    - "apps/api/app/core/config.py (PLATFORM_CREDENTIAL_KEY field added)"
    - "apps/api/app/services/agent_tools.py (_tenant_id_var + build_tool_server signature)"
    - "apps/api/app/worker/tasks/runtime/agent.py (tenant_id=str(agent.tenant_id) call site)"
    - "apps/api/tests/conftest.py (PLATFORM_CREDENTIAL_KEY os.environ.setdefault)"

decisions:
  - "HKDF salt is tenant_id (UUID string) — gives per-tenant key uniqueness from a shared master key (INT-01)"
  - "HKDF info=b'wchats-integration-credential' — application-specific context prevents cross-purpose key reuse"
  - "CredentialHandle is frozen dataclass (not namedtuple) — __repr__ override is explicit and testable"
  - "_fetch_credential_config returns None on empty conn_str (no raise) — callers raise ProviderNotConfiguredError"
  - "tenant_id flows via ContextVar not task arg — satisfies CLAUDE.md rule 4 and T-16-06"
  - "PLATFORM_CREDENTIAL_KEY has no default in Settings — fail-fast at startup enforces production hygiene"
  - "build_tool_server tenant_id param defaults to empty string — preserves backward compat for existing test call sites"

metrics:
  tasks_completed: 3
  tasks_total: 3
  files_created: 4
  files_modified: 4
  tests_added: 4
  tests_passing: 4
  duration_minutes: 22
---

# Phase 16 Plan 01: Credential Substrate (migration 0007, HKDF derivation, CredentialHandle, _tenant_id_var) Summary

Per-tenant HKDF→Fernet credential substrate built: migration 0007 creates `integration_credentials` in every tenant Neon DB; `_derive_tenant_fernet` derives a unique Fernet key per tenant via HKDF(SHA-256); `CredentialHandle` wraps decrypted credentials with a redacted repr; `_tenant_id_var` ContextVar supplies the HKDF salt from `build_tool_server` without touching Celery task args.

## What Was Built

### Task 1 — Migration 0007 + PLATFORM_CREDENTIAL_KEY (commit d5debf8)

Created `apps/api/alembic_tenant/versions/0007_integration_credentials.py`:
- `revision="0007"`, `down_revision="0006"` (advances alembic_tenant chain)
- `CREATE TABLE IF NOT EXISTS integration_credentials` with `id UUID PK`, `provider_type TEXT NOT NULL`, `credential_data BYTEA NOT NULL`, `config_data JSONB NOT NULL DEFAULT '{}'`, `currency_code TEXT NOT NULL DEFAULT 'USD'`, `enabled_skills JSONB NOT NULL DEFAULT '[]'`, `created_at/updated_at TIMESTAMP WITH TIME ZONE`
- `CREATE INDEX IF NOT EXISTS ix_integration_credentials_provider_type`
- `downgrade()` drops index then table, both IF EXISTS

Modified `apps/api/app/core/config.py`:
- Added `PLATFORM_CREDENTIAL_KEY: str` immediately after `NEON_ENCRYPTION_KEY`
- No default value → Settings instantiation fails at startup if env var is unset (same fail-fast behavior as NEON_ENCRYPTION_KEY)
- Existing `__repr__` suppression at line 148 covers the new field automatically

### Task 2 — _tenant_id_var ContextVar + build_tool_server wiring (commit bd0765b)

Modified `apps/api/app/services/agent_tools.py`:
- Added `_tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="")` after existing ContextVars
- One-line comment: `# HKDF salt source for per-tenant credential derivation (INT-01)`
- Added `tenant_id: str = ""` parameter to `build_tool_server` signature (default="" for backward compat)
- Added `_tenant_id_var.set(tenant_id)` immediately after `_agent_id_var.set(agent_id)` in function body
- All 6 existing `.set()` calls are preserved (no PROD-14 regression)

Modified `apps/api/app/worker/tasks/runtime/agent.py`:
- Updated `build_tool_server(...)` call to pass `tenant_id=str(agent.tenant_id)` (matches key_link pattern)
- `agent.tenant_id` is a `Mapped[UUID]` field confirmed on the Agent ORM model (line 29)
- Resolves RESEARCH Open Question 1: tenant_id flows via ContextVar, never as a Celery task arg

### Task 3 — credential_service.py + adapters/__init__.py + Wave 0 tests (commits 6d91ac3 + 8511208)

**RED commit** (`test(16-01)`): `test_credential_service.py` with 4 failing tests (module not found)

**GREEN commit** (`feat(16-01)`):

Created `apps/api/app/services/transactional/credential_service.py`:
- `ProviderNotConfiguredError(Exception)`, `CredentialDecryptionError(Exception)`
- `@dataclass(frozen=True) class CredentialHandle`: `_raw: str`, `use() -> str`, `__repr__ -> "<CredentialHandle:redacted>"`, `__str__ = __repr__`
- `_derive_tenant_fernet(platform_master_key: bytes, tenant_id: str) -> Fernet`: constructs a FRESH `HKDF(SHA256, length=32, salt=tenant_id.encode(), info=b"wchats-integration-credential")` per call (never cached, avoids AlreadyFinalized)
- `@dataclass(frozen=True) class _CredentialConfig`: `provider_type, credential_data, config_data, currency_code`
- `async _fetch_credential_config(conn_str: str, skill: str) -> _CredentialConfig | None`: returns `None` immediately if `conn_str` is falsy; otherwise runs `asyncio.to_thread(_sync_fetch)` executing `SELECT provider_type, credential_data, config_data, currency_code FROM integration_credentials WHERE enabled_skills @> %s::jsonb LIMIT 1` via psycopg2; structlog warning on exception (never raises)
- No `settings.NEON_ENCRYPTION_KEY` reference

Created `apps/api/app/services/transactional/adapters/__init__.py`:
- Empty package init with docstring; Wave 2 adapter modules import lazily inside `get_adapter_for_skill()`

Modified `apps/api/tests/conftest.py`:
- Added `os.environ.setdefault("PLATFORM_CREDENTIAL_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())` (Rule 3 auto-fix: required env var missing broke pytest collection)

## Test Results

```
pytest tests/unit/test_credential_service.py -v
4 passed in 0.89s

- test_hkdf_per_tenant_isolation   PASSED  (T-16-10: cross-tenant decrypt raises InvalidToken; no AlreadyFinalized)
- test_handle_repr_redacted        PASSED  (T-16-01: repr=="<CredentialHandle:redacted>"; raw absent)
- test_no_credential_in_tool_schema PASSED  (INT-02: no api_key/credential/secret/password/token in schemas)
- test_fetch_credential_config_none_when_missing PASSED  (empty conn_str returns None, psycopg2.connect not called)
```

Full suite (unit tests excluding pre-existing failures): **87 passed, 0 new failures**.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] PLATFORM_CREDENTIAL_KEY missing from test conftest caused pytest collection failures**
- **Found during:** Task 3 GREEN phase (full suite run)
- **Issue:** Adding `PLATFORM_CREDENTIAL_KEY: str` with no default to `Settings` caused `ValidationError` during `settings = Settings()` at module import time, breaking pytest collection for any test file that imports an `app.*` module
- **Fix:** Added `os.environ.setdefault("PLATFORM_CREDENTIAL_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())` to `tests/conftest.py` alongside the existing `NEON_ENCRYPTION_KEY` pattern (same convention)
- **Files modified:** `apps/api/tests/conftest.py`
- **Commit:** 8511208

**2. [Rule 2 - Security] Docstring mention of NEON_ENCRYPTION_KEY removed to satisfy acceptance criterion**
- **Found during:** Task 3 GREEN verification
- **Issue:** The advisory comment in `credential_service.py` docstring contained the literal string `settings.NEON_ENCRYPTION_KEY` — the acceptance criterion checks for absence of this string anywhere in the file
- **Fix:** Replaced the comment with a version that does not contain the reference string while preserving the intent
- **Files modified:** `apps/api/app/services/transactional/credential_service.py`
- **Commit:** 8511208

### Pre-existing Failures (Out of Scope)

Confirmed pre-existing (present before this plan's commits):
- `tests/unit/retrieval/test_retrieval_service.py::TestEmbedQuery::test_uses_query_input_type` — Bedrock embedding validation failure (network/credentials)
- `tests/unit/test_agent_chat_routes.py` — route test collection error
- `tests/unit/test_transactional_tools.py::TestBadSchemaRejection` — sdk_tool.handler attribute error (Phase 14 issue)

Logged to deferred-items.md per deviation rule scope boundary.

## Threat Surface Scan

All implemented threat mitigations match the plan's `<threat_model>`:

| Threat ID | Status | Evidence |
|-----------|--------|----------|
| T-16-01 | Mitigated | `__repr__`/`__str__` return redacted marker; `test_handle_repr_redacted` asserts raw absent |
| T-16-05 | Mitigated | `PLATFORM_CREDENTIAL_KEY` has no default; `Settings.__repr__` suppression covers it |
| T-16-06 | Mitigated | `tenant_id` via `_tenant_id_var` ContextVar, `tenant_id=str(agent.tenant_id)` at call site |
| T-16-07 | Mitigated | `CredentialHandle` is frozen dataclass with no `__json__`; not stored in ContextVar or Redis |
| T-16-10 | Mitigated | Fresh HKDF per call; `test_hkdf_per_tenant_isolation` proves cross-tenant decrypt fails |

No new threat surface introduced beyond what the plan declared.

## Self-Check: PASSED

Files exist:
- `apps/api/alembic_tenant/versions/0007_integration_credentials.py` — FOUND
- `apps/api/app/core/config.py` (modified) — FOUND
- `apps/api/app/services/agent_tools.py` (modified) — FOUND
- `apps/api/app/worker/tasks/runtime/agent.py` (modified) — FOUND
- `apps/api/app/services/transactional/credential_service.py` — FOUND
- `apps/api/app/services/transactional/adapters/__init__.py` — FOUND
- `apps/api/tests/unit/test_credential_service.py` — FOUND

Commits verified in git log:
- d5debf8 `feat(16-01): migration 0007` — FOUND
- bd0765b `feat(16-01): _tenant_id_var ContextVar` — FOUND
- 6d91ac3 `test(16-01): RED` — FOUND
- 8511208 `feat(16-01): credential_service.py + adapters package` — FOUND
