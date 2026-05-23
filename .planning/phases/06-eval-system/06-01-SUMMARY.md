---
phase: "06"
plan: "06-01"
title: "Foundation: DB migration 0005 + Settings additions + Celery beat config"
status: complete
completed_at: "2026-05-23"
commits:
  - 088ea50  # feat(06-01): add tenant DB migration 0005
  - 67118d8  # feat(06-01): add eval threshold settings
  - 4efa68b  # feat(06-01): add Celery beat schedule and eval task module
---

# 06-01 Summary — Foundation

## What Was Built

Three atomic tasks establishing the M6 foundation:

### Task 1 — Tenant DB Migration 0005
**File:** `apps/api/alembic_tenant/versions/0005_verified_qa_eval_scenarios.py`
**Commit:** 088ea50

Creates two new tenant DB tables:

- `verified_qa` — promoted QA pairs with HNSW-indexed `question_vector VECTOR(1024)`.
  Source constraint: `CHECK (source IN ('sandbox_test', 'production_promotion', 'human_authored'))`.
  HNSW index `verified_qa_vector_idx` on `question_vector vector_cosine_ops` for cosine
  similarity lookup at retrieval time (D-25, threshold 0.93).

- `eval_scenarios` — test scenarios for the nightly eval harness.
  Source constraint: `CHECK (source IN ('generated', 'mined'))`.
  Fields: `reference_answer TEXT NOT NULL`, `retrieved_contexts JSONB NOT NULL DEFAULT '[]'::jsonb`.

`eval_runs` and `eval_results` from migration 0001 are untouched (D-07).
`down_revision = "0004"` — chains correctly from the M5 verified_qa_candidates migration.
Downgrade drops `eval_scenarios` first, then `verified_qa`.

### Task 2 — Settings Additions
**File:** `apps/api/app/core/config.py`
**Commit:** 67118d8

Added three float fields to the `Settings` class after the existing M5 threshold:

```python
# M6: Eval system thresholds — Ragas metric promotion gates + retrieval cache
EVAL_FAITHFULNESS_THRESHOLD: float = 0.90
EVAL_RELEVANCY_THRESHOLD: float = 0.90
VERIFIED_QA_HIT_THRESHOLD: float = 0.93
```

Per D-28 (LOCKED). All defaults match the PRD specification. Existing deployments are
not broken (all fields have defaults).

### Task 3 — Celery Beat Schedule
**File:** `apps/api/app/worker/celery_app.py`
**Commit:** 4efa68b

Three changes:
1. Added `from celery.schedules import crontab` import.
2. Added `"app.worker.tasks.runtime.eval"` to the `include` list for task autodiscovery.
3. Added `beat_schedule` to `celery_app.conf.update(...)`:

```python
beat_schedule={
    "eval-nightly": {
        "task": "app.worker.tasks.runtime.eval.run_eval_suite_beat",
        "schedule": crontab(hour=2, minute=0),
    },
},
```

Per D-19/D-20 (LOCKED). Beat process is a separate local process:
`celery -A app.worker.celery_app beat --loglevel=info`

## Verification Results

All three plan verification commands passed:

```
python -c "from app.core.config import settings; print(settings.EVAL_FAITHFULNESS_THRESHOLD, settings.EVAL_RELEVANCY_THRESHOLD, settings.VERIFIED_QA_HIT_THRESHOLD)"
# → 0.9 0.9 0.93

python -c "from app.worker.celery_app import celery_app; print(celery_app.conf.include); print(list(celery_app.conf.beat_schedule.keys()))"
# → [..., 'app.worker.tasks.runtime.eval']
# → ['eval-nightly']

python -c "import ast; ast.parse(open('alembic_tenant/versions/0005_verified_qa_eval_scenarios.py').read()); print('migration parse ok')"
# → migration parse ok
```

## Acceptance Criteria

- [x] File `apps/api/alembic_tenant/versions/0005_verified_qa_eval_scenarios.py` exists
- [x] File contains `revision = "0005"` and `down_revision = "0004"`
- [x] File contains `CREATE TABLE verified_qa` with `VECTOR(1024) NOT NULL` column `question_vector`
- [x] File contains `CHECK (source IN ('sandbox_test','production_promotion','human_authored'))` for verified_qa.source
- [x] File contains `CREATE INDEX verified_qa_vector_idx ON verified_qa USING hnsw (question_vector vector_cosine_ops)`
- [x] File contains `CREATE TABLE eval_scenarios` with `CHECK (source IN ('generated','mined'))`
- [x] File contains `reference_answer TEXT NOT NULL` and `retrieved_contexts JSONB NOT NULL DEFAULT '[]'::jsonb`
- [x] File does NOT contain `CREATE TABLE eval_runs` or `CREATE TABLE eval_results`
- [x] `downgrade()` drops `eval_scenarios` before `verified_qa`
- [x] `apps/api/app/core/config.py` contains `EVAL_FAITHFULNESS_THRESHOLD: float = 0.90`
- [x] `apps/api/app/core/config.py` contains `EVAL_RELEVANCY_THRESHOLD: float = 0.90`
- [x] `apps/api/app/core/config.py` contains `VERIFIED_QA_HIT_THRESHOLD: float = 0.93`
- [x] `apps/api/app/worker/celery_app.py` contains `from celery.schedules import crontab`
- [x] `apps/api/app/worker/celery_app.py` include list contains `"app.worker.tasks.runtime.eval"`
- [x] `apps/api/app/worker/celery_app.py` contains `beat_schedule` with key `"eval-nightly"`
- [x] beat_schedule `"eval-nightly"` task value is `"app.worker.tasks.runtime.eval.run_eval_suite_beat"`
- [x] beat_schedule uses `crontab(hour=2, minute=0)`

## Files Modified

| File | Change |
|------|--------|
| `apps/api/alembic_tenant/versions/0005_verified_qa_eval_scenarios.py` | Created (96 lines) |
| `apps/api/app/core/config.py` | +5 lines (M6 eval thresholds) |
| `apps/api/app/worker/celery_app.py` | +15 lines (crontab import, eval include, beat_schedule) |

## Next Plan

**06-02** — `eval_service.py` Ragas 0.4.x harness (4 metrics) + Neon branch management (`neon_service.py`)
