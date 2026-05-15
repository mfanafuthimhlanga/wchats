# Plan 03-01 Summary — M3 Foundation

**Executed:** 2026-05-16  
**Plan file:** 03-01-PLAN.md

## What was built

### 1. Alembic migration `0003_agent_retrieval_strategy.py`
- `revision = "0003"`, `down_revision = "0002"`
- `upgrade()`: raw SQL `ALTER TABLE agents ADD COLUMN retrieval_strategy JSONB NOT NULL DEFAULT '{}'::jsonb`
- `downgrade()`: raw SQL `ALTER TABLE agents DROP COLUMN IF EXISTS retrieval_strategy`
- Follows 0001 raw-SQL pattern (not `op.add_column`)

### 2. `apps/api/app/core/config.py` — COHERE_API_KEY
- Added `COHERE_API_KEY: str | None = None` after `VOYAGE_API_KEY`
- Optional so existing deployments without the key are not broken
- Comment links to RET-05 (Cohere reranker fallback)

### 3. `apps/api/app/models/agent.py` — Agent ORM update
- Added `retrieval_strategy: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))`
- Placed after `status` column, consistent with migration column order
- Uses existing `JSONB` import (already present in file); no new imports needed

### 4. Wave 0 test stubs (all xfail)
- `tests/unit/retrieval/__init__.py` — empty package marker
- `tests/unit/retrieval/test_retrieval_service.py` — 4 stubs for Plan 02
- `tests/unit/retrieval/test_retrieve_task.py` — 2 stubs for Plan 03
- `tests/integration/test_query_route.py` — 1 stub for Plan 04

## Verification results

```
Migration OK    — revision=0003, down_revision=0002 ✓
Settings OK     — COHERE_API_KEY in Settings source ✓
Agent OK        — retrieval_strategy in Agent source ✓
6 tests collected; 6 xfailed (all stubs behave correctly) ✓
```

## Decisions made

- Used `JSONB` (not generic `JSON`) in the ORM to match the existing `soul` column pattern and stay consistent with the PostgreSQL-specific migration SQL.
- `COHERE_API_KEY` is `str | None = None` (optional) so that the API can start without a Cohere key; the reranker fallback logic in Plan 05 will check for `None` before attempting Cohere calls.
- `server_default=text("'{}'::jsonb")` mirrors the DB migration default exactly so SQLAlchemy INSERT statements without an explicit value do not violate the NOT NULL constraint.
