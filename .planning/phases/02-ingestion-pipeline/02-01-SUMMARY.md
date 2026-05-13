---
phase: 02-ingestion-pipeline
plan: "01"
subsystem: ingestion
tags: [docling, chonkie, voyageai, anthropic, tenacity, alembic, uuid5, prompt-injection]

# Dependency graph
requires:
  - phase: 01-control-plane
    provides: "M1 tenant DB schema (0001 migration), Settings class, conftest.py env-var pattern"
provides:
  - "0002 tenant migration — source_hash, parse_status, chunk_count on documents; entities and chunk_entities tables"
  - "app/utils/chunk_id.py — deterministic_chunk_id() with uuid5(NAMESPACE_URL) for upsert idempotency"
  - "app/utils/sanitize.py — sanitize_chunk_text() strips prompt-injection markers before DB write"
  - "Extended Settings: ANTHROPIC_API_KEY, VOYAGE_API_KEY, MAX_UPLOAD_SIZE_MB"
  - "Runtime dependencies installed: docling==2.93.0, chonkie==1.6.5, voyageai==0.3.7, anthropic==0.101.0, python-multipart==0.0.20, tenacity==9.1.2"
affects:
  - 02-02-docling-parsing
  - 02-03-chunking
  - 02-04-metadata-entities
  - 02-05-embedding
  - 02-06-routes-chain
  - 02-07-demo

# Tech tracking
tech-stack:
  added:
    - docling==2.93.0 (layout-aware PDF parsing — DocLayNet + TableFormer ML models)
    - chonkie==1.6.5 (structure-aware chunking — HybridChunker)
    - voyageai==0.3.7 (Voyage embedding and reranking API client)
    - anthropic==0.101.0 (Claude Haiku metadata enrichment via structured outputs)
    - python-multipart==0.0.20 (multipart/form-data file upload support)
    - tenacity==9.1.2 (retry logic with exponential backoff for external APIs)
  patterns:
    - "uuid5(NAMESPACE_URL, '{doc_id}:{ordinal}') — deterministic chunk IDs for upsert idempotency"
    - "Compiled regex injection stripping before DB write — prompt injection mitigation at write time"
    - "pydantic-settings env vars set in conftest.py before any app import"
    - "UNIQUE(normalized, type) on entities — deduplication without explicit SELECT"

key-files:
  created:
    - apps/api/app/utils/__init__.py
    - apps/api/app/utils/chunk_id.py
    - apps/api/app/utils/sanitize.py
    - apps/api/alembic_tenant/versions/0002_documents_ingestion_columns.py
    - apps/api/.env.example
    - apps/api/tests/unit/test_chunk_id.py
    - apps/api/tests/unit/test_sanitize.py
  modified:
    - apps/api/pyproject.toml
    - apps/api/app/core/config.py
    - apps/api/tests/conftest.py

key-decisions:
  - "docling==2.93.0 installed via fallback (unpinned first, then chonkie/voyageai/anthropic pinned) — pinned docling==2.93.0 install failed on Windows due to torch 2.5.1->2.11.0 conflict with intermediate uninstall rollback; actual installed version is 2.93.0"
  - "CHUNK_UUID_NAMESPACE pinned to uuid.NAMESPACE_URL (6ba7b810-9dad-11d1-80b4-00c04fd430c8) with explicit deployment warning — must never change"
  - "sanitize regex applied at write time (not read time) — consistent protection regardless of retrieval path"
  - "entities/chunk_entities tables created in 0002 migration (not deferred) — prerequisite storage layer for Wave 4 entity extraction"
  - "No FK indexes on chunk_entities in 0002 — deferred to M3 if retrieval-side queries warrant (per plan spec)"

patterns-established:
  - "utils/ package for stdlib-only, import-free helpers — keeps them testable without app import overhead"
  - "Migration follows 0001 style: raw SQL via op.execute(), docstring naming revision + what it adds"

requirements-completed:
  - ING-05
  - ING-06

# Metrics
duration: ~35min
completed: 2026-05-13
---

# Phase 2 Plan 01: Foundation — Summary

**Deterministic chunk UUIDs (uuid5 NAMESPACE_URL), prompt-injection sanitization, 0002 tenant migration (source_hash/parse_status/chunk_count + entities/chunk_entities), and all six M2 runtime dependencies installed**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-05-13T (Wave 1 execution start)
- **Completed:** 2026-05-13
- **Tasks:** 3/3
- **Files modified:** 10 (7 created, 3 modified)

## Accomplishments

- All six M2 runtime dependencies installed at exact pinned versions (docling 2.93.0, chonkie 1.6.5, voyageai 0.3.7, anthropic 0.101.0, python-multipart 0.0.20, tenacity 9.1.2)
- 0002 tenant migration creates the `entities` and `chunk_entities` tables needed by Wave 4 entity extraction, and adds `source_hash`, `parse_status`, `chunk_count` to `documents`
- `deterministic_chunk_id()` utility establishes the upsert-safe chunk ID contract used by all later waves
- `sanitize_chunk_text()` utility provides prompt-injection mitigation at DB write time, covering all known injection markers
- 110 unit tests passing (was 100 before this plan — 10 new tests added)

## Task Commits

1. **Task 02-01-01: Add M2 deps, extend Settings, update .env.example and conftest** - `55627bc` (feat)
2. **Task 02-01-02: Create 0002 tenant migration** - `6fd5872` (feat)
3. **Task 02-01-03: Create chunk_id and sanitize utils + unit tests** - `0a4e8a6` (feat)

## Installed Package Versions

| Package | Planned | Installed |
|---------|---------|-----------|
| docling | 2.93.0 | 2.93.0 |
| chonkie | 1.6.5 | 1.6.5 |
| voyageai | 0.3.7 | 0.3.7 |
| anthropic | 0.101.0 | 0.101.0 |
| python-multipart | 0.0.20 | 0.0.20 |
| tenacity | 9.1.2 | 9.1.2 |

All packages installed at planned pinned versions. docling installation triggered a torch upgrade from 2.5.1 to 2.11.0 (a docling transitive dependency upgrade — no impact on Veridian functionality).

## Files Created/Modified

**Created:**
- `apps/api/app/utils/__init__.py` — empty package marker
- `apps/api/app/utils/chunk_id.py` — `deterministic_chunk_id()` + `CHUNK_UUID_NAMESPACE` constant
- `apps/api/app/utils/sanitize.py` — `sanitize_chunk_text()` with compiled injection-pattern regex
- `apps/api/alembic_tenant/versions/0002_documents_ingestion_columns.py` — revision 0002, down_revision 0001
- `apps/api/.env.example` — all env vars documented with placeholder values
- `apps/api/tests/unit/test_chunk_id.py` — 5 tests (TestDeterministicChunkId)
- `apps/api/tests/unit/test_sanitize.py` — 5 tests (TestSanitizeChunkText)

**Modified:**
- `apps/api/pyproject.toml` — 6 new runtime dependencies appended
- `apps/api/app/core/config.py` — ANTHROPIC_API_KEY, VOYAGE_API_KEY, MAX_UPLOAD_SIZE_MB added to Settings
- `apps/api/tests/conftest.py` — 3 new env var setdefault() calls before app imports

## Decisions Made

- **docling install fallback path:** The pinned `docling==2.93.0` install failed on Windows because pip tried to uninstall torch 2.5.1 (rolling back when the new torch 2.11.0 dist-info write failed). Resolution: installed docling without pin first (which resolved the torch constraint), then installed the remaining packages pinned. Final installed version is 2.93.0 as planned.

- **CHUNK_UUID_NAMESPACE stabilized:** The namespace UUID is pinned to `uuid.NAMESPACE_URL` (RFC 4122 well-known value). A comment in the source code explicitly warns "MUST never change across deployments." This is a write-time correctness requirement for upsert idempotency.

- **Injection sanitization at write time:** `sanitize_chunk_text()` is called before INSERT in the chunks table. Applying the mitigation at read/retrieval time would be incomplete (some retrieval paths bypass the function). Write-time application is the correct architecture.

## Deviations from Plan

### Auto-fixed Issues

None during implementation.

### Installation Deviation

**[Not a code deviation — installation path only]** `docling==2.93.0` pinned install failed on Windows (torch version conflict with intermediate file-rename failure). Resolved by installing docling unpinned first, then the other packages pinned. Actual installed docling version is 2.93.0 (same as planned). The pyproject.toml pinned version (2.93.0) is correct and will work correctly in Docker (Linux) where the torch conflict does not apply.

**Impact:** Zero impact on code correctness. pyproject.toml contains the intended pinned versions.

## Issues Encountered

- `.env.example` did not exist in the repo before this plan. Created fresh with all M1 and M2 variables documented.
- `pip install -e ".[dev]"` failed due to setuptools `build_editable` not supported by the installed pip version on this Windows environment. Resolved by installing packages directly with `pip install <package>`.

## pytest Output

```
110 passed in 20.57s
```

New tests (10 added by this plan):
- `tests/unit/test_chunk_id.py` — 5 tests
- `tests/unit/test_sanitize.py` — 5 tests

## Known Stubs

None. This plan creates foundation utilities with no UI rendering or data sources — no stubs applicable.

## Threat Flags

No new network endpoints or auth paths introduced. Settings fields inherit existing `__repr__` suppression (T-02-01-01 — already mitigated in Settings class). No new threat surface beyond what is documented in the plan's threat model.

## Next Phase Readiness

- Wave 2 (02-02: Docling parsing task) can proceed — `docling` is installed, `chunk_id` utility is available, Settings exposes required API keys
- All 6 M2 dependencies are importable
- Migration 0002 is in version control and ready to apply against tenant DBs in Wave 7 demo
- Entity storage tables are available for Wave 4 entity extraction

---
*Phase: 02-ingestion-pipeline*
*Completed: 2026-05-13*
