---
phase: 05-validation-chain
plan: 01
wave: 1
status: complete
date: 2026-05-23
commits:
  - e6efe6e  feat(05-01): add langfuse dependency + LANGFUSE settings + conftest env
  - 265079a  feat(05-01): add control-DB migration 0010 + tenant-DB migration 0004 + Agent ORM field
  - 2502946  test(05-01): scaffold test_validators.py with 7 xfail stubs (VAL-01..VAL-06)
---

# Plan 05-01 Summary — Foundation: dependency, schema, and Wave-0 test scaffold

## What was done

Plan 05-01 lays the non-negotiable foundation for the M5 validation chain: a pinned
Langfuse dependency, four new Settings fields, two Alembic migrations (one per DB),
a new Agent ORM boolean column, and a complete set of xfail test stubs covering all
VAL-01 through VAL-06 acceptance criteria.

---

## Task 1 — Dependency + Settings + env scaffold

**Files changed:** `apps/api/pyproject.toml`, `apps/api/app/core/config.py`,
`apps/api/.env.example`, `apps/api/tests/conftest.py`

### langfuse pin

Added to `pyproject.toml` dependencies immediately after `anthropic==0.101.0`:

```
"langfuse==3.12.1",
```

### New Settings fields (M5 block)

Added to `apps/api/app/core/config.py`:

| Field | Type | Default |
|-------|------|---------|
| `LANGFUSE_PUBLIC_KEY` | `str \| None` | `None` |
| `LANGFUSE_SECRET_KEY` | `str \| None` | `None` |
| `LANGFUSE_HOST` | `str` | `"https://cloud.langfuse.com"` |
| `VERIFIED_QA_CONFIDENCE_THRESHOLD` | `float` | `0.90` |

Keys are optional (validation chain still runs when unset); threshold is the D-19
per-tenant default.

### .env.example

Added M5 comment block with commented-out placeholder entries for all four fields.

### conftest.py

Added three `os.environ.setdefault(...)` calls immediately after the existing
`ANTHROPIC_API_KEY` block so module-level `Langfuse()` init in `validation_service.py`
never raises during test discovery (RESEARCH Pitfall 5):

```python
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "test-pk")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "test-sk")
os.environ.setdefault("LANGFUSE_HOST", "http://localhost:3000")
```

**Verification:** `python -c "from app.core.config import settings; assert settings.VERIFIED_QA_CONFIDENCE_THRESHOLD==0.90"` exits 0.

---

## Task 2 — Both Alembic migrations + Agent ORM field

### Control DB migration 0010

**File:** `apps/api/alembic/versions/0010_agent_strategy_resynthesis_flag.py`

```
revision:       "0010"
down_revision:  "0009"
```

`upgrade()`: `ALTER TABLE agents ADD COLUMN strategy_resynthesis_flagged BOOLEAN NOT NULL DEFAULT FALSE`
`downgrade()`: `ALTER TABLE agents DROP COLUMN IF EXISTS strategy_resynthesis_flagged`

### Tenant DB migration 0004

**File:** `apps/api/alembic_tenant/versions/0004_verified_qa_candidates.py`

```
revision:       "0004"
down_revision:  "0003"
```

`upgrade()` creates `verified_qa_candidates` with all eight D-20 LOCKED columns:

| Column | Type | Constraint |
|--------|------|-----------|
| `id` | UUID | PRIMARY KEY DEFAULT gen_random_uuid() |
| `conversation_id` | UUID | NOT NULL |
| `question` | TEXT | NOT NULL |
| `answer` | TEXT | NOT NULL |
| `citations` | JSONB | NOT NULL DEFAULT '[]'::jsonb |
| `auditor_confidence` | FLOAT | NOT NULL |
| `queued_at` | TIMESTAMPTZ | NOT NULL DEFAULT NOW() |
| `status` | TEXT | NOT NULL DEFAULT 'pending' CHECK IN ('pending','approved','rejected') |

Two indexes: `vqa_candidates_conversation_idx` (conversation_id), `vqa_candidates_status_idx` (status).

`downgrade()`: `DROP TABLE IF EXISTS verified_qa_candidates`

### Agent ORM column

Added to `apps/api/app/models/agent.py` immediately after `widget_config`:

```python
# M5: validation chain — persistent Auditor ungrounded failures trigger resynthesis
strategy_resynthesis_flagged: Mapped[bool] = mapped_column(
    nullable=False, server_default=text("false")
)
```

No `Boolean` import needed — SQLAlchemy infers from `Mapped[bool]`.

**Verification:** `python -c "from app.models.agent import Agent; assert hasattr(Agent,'strategy_resynthesis_flagged')"` exits 0.

---

## Task 3 — Wave-0 test scaffold

**Files changed:** `apps/api/tests/unit/test_validators.py` (new),
`apps/api/tests/unit/test_agent_task.py` (appended)

### test_validators.py — 7 xfail stubs

All seven functions cover VAL-01 through VAL-06:

| Function | VAL ID | Intent |
|----------|--------|--------|
| `test_gatekeeper_verdict` | VAL-01 | GatekeeperVerdict Pydantic model validates + normalises |
| `test_run_gatekeeper_task` | VAL-02 | run_gatekeeper idempotency + emit |
| `test_auditor_verdict` | VAL-03 | AuditorVerdict Pydantic model validates + normalises |
| `test_auditor_inserts_candidate` | VAL-04 | run_auditor inserts verified_qa_candidates when confident |
| `test_strategist_verdict` | VAL-05 | StrategistVerdict Pydantic model validates + normalises |
| `test_langfuse_logged` | VAL-05 | _log_verdict() calls start_as_current_generation |
| `test_resynthesis_flag` | VAL-06 | run_auditor sets strategy_resynthesis_flagged on 3+ ungrounded |

Each stub is marked `@pytest.mark.xfail(reason="implemented in 05-02/05-03", strict=False)`.
Symbol imports are inside test bodies so collection never fails on not-yet-created modules.

`_make_agent()` helper includes `strategy_resynthesis_flagged = False` (RESEARCH Pitfall 4).

### test_agent_task.py — appended stub

`test_validators_dispatched` xfail stub added at end of file:
```python
@pytest.mark.xfail(reason="implemented in 05-04", strict=False)
def test_validators_dispatched():
    ...
```

**Verification:**
- `pytest tests/unit/test_validators.py --collect-only -q` → 7 tests collected, exit 0
- `pytest tests/unit/test_validators.py -q` → `7 xfailed`, exit 0

---

## Acceptance criteria verification

| Criterion | Result |
|-----------|--------|
| `pyproject.toml` contains `"langfuse==3.12.1"` | PASS |
| `config.py` has LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST + VERIFIED_QA_CONFIDENCE_THRESHOLD | PASS |
| `config.py` contains `VERIFIED_QA_CONFIDENCE_THRESHOLD: float = 0.90` | PASS |
| `.env.example` contains `LANGFUSE_HOST=` | PASS |
| `conftest.py` contains `os.environ.setdefault("LANGFUSE_PUBLIC_KEY"` | PASS |
| 0010 migration exists with revision="0010", down_revision="0009" | PASS |
| 0010 migration contains `ALTER TABLE agents ADD COLUMN strategy_resynthesis_flagged BOOLEAN NOT NULL DEFAULT FALSE` | PASS |
| 0004 migration exists with revision="0004", down_revision="0003" | PASS |
| 0004 migration contains `CREATE TABLE verified_qa_candidates` with all 8 columns and 2 indexes | PASS |
| `agent.py` contains `strategy_resynthesis_flagged: Mapped[bool]` | PASS |
| `test_validators.py` exists with all 7 function names and xfail markers | PASS |
| `test_agent_task.py` contains `def test_validators_dispatched` | PASS |
| Settings imports without error | PASS |
| ORM field exists at runtime | PASS |
| `pytest test_validators.py --collect-only -q` exits 0 (7 collected) | PASS |
| `pytest test_validators.py -q` exits 0 (7 xfailed) | PASS |

---

## Threat mitigations

| Threat ID | Mitigation |
|-----------|-----------|
| T-05-01-01 (Langfuse credentials) | .env.example has placeholder values; real keys from env via Settings |
| T-05-01-02 (migration applied to wrong DB) | 0010 → `alembic/` (control), 0004 → `alembic_tenant/` (tenant); chains explicit |
| T-05-01-03 (module-level Langfuse() crash) | conftest sets test vars; Plan 02 lazy-init with try/except guard |

---

## Next: Wave 2 — Plan 05-02 (validation_service.py)

Plan 05-02 implements `validation_service.py` with three Pydantic verdict models
(GatekeeperVerdict, AuditorVerdict, StrategistVerdict), three Haiku tool-use judge
calls, and the Langfuse v4 `_log_verdict()` helper. It will turn the `test_gatekeeper_verdict`,
`test_auditor_verdict`, `test_strategist_verdict`, and `test_langfuse_logged` stubs green.
