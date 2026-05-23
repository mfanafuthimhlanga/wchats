# Phase 5: Validation Chain — Pattern Map

**Mapped:** 2026-05-23
**Files analyzed:** 9 new/modified files
**Analogs found:** 9 / 9

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `apps/api/app/services/validation_service.py` | service | request-response (Haiku API) | `apps/api/tests/evals/judge.py` | role-match (same Anthropic pattern, production vs eval) |
| `apps/api/app/worker/tasks/runtime/validators.py` | task (Celery) | request-response + CRUD | `apps/api/app/worker/tasks/runtime/agent.py` | exact (runtime queue, acks_late, idempotency, DB write) |
| `apps/api/alembic/versions/0010_agent_strategy_resynthesis_flag.py` | migration (control DB) | CRUD | `apps/api/alembic/versions/0009_agent_widget_config.py` | exact (ALTER TABLE agents ADD COLUMN) |
| `apps/api/alembic_tenant/versions/0004_verified_qa_candidates.py` | migration (tenant DB) | CRUD | `apps/api/alembic_tenant/versions/0003_tenant_agent_conversations.py` | exact (CREATE TABLE + indexes in tenant DB) |
| `apps/api/app/models/agent.py` | model | CRUD | `apps/api/app/models/agent.py` itself | exact (add `Mapped[bool]` column) |
| `apps/api/app/worker/tasks/runtime/agent.py` | task (Celery) | request-response | itself | exact (add chain dispatch after agent.response emit) |
| `apps/api/tests/unit/test_validators.py` | test | — | `apps/api/tests/unit/test_agent_task.py` | exact (same mock strategy: patch at module boundary, fake_emit, canned results) |
| `apps/api/pyproject.toml` | config | — | itself | exact (add langfuse==3.12.1 to dependencies list) |
| `scripts/demo_m5.sh` | script | — | `scripts/demo_m3.sh` | role-match (bash smoke test, set -euo pipefail, env var guards, curl + jq) |

---

## Pattern Assignments

### `apps/api/app/services/validation_service.py` (service, request-response)

**Analog:** `apps/api/tests/evals/judge.py`

**Imports pattern** (`judge.py` lines 1–27):
```python
import json
import structlog
from typing import Any

log = structlog.get_logger(__name__)
```
For the production service, expand to:
```python
import os
import structlog
from typing import Literal
import anthropic
from pydantic import BaseModel, field_validator
from langfuse import Langfuse

log = structlog.get_logger(__name__)

ANTHROPIC_CLIENT = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

_langfuse: Langfuse | None = None
try:
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        _langfuse = Langfuse()
except Exception:
    pass  # Langfuse unavailable — validation still runs, just not logged
```

**Core pattern — Haiku judge call via tool_use** (`judge.py` lines 146–176, upgraded to tool-use):
```python
# judge.py uses system-prompt JSON (lines 146-176). Replace with tool-use for
# guaranteed schema conformance. Pattern from RESEARCH.md §Pattern 3.
def call_gatekeeper(question: str, response_text: str) -> GatekeeperVerdict:
    response = ANTHROPIC_CLIENT.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        system="You are a response quality judge. Call submit_verdict with your evaluation.",
        messages=[{"role": "user", "content": f"QUESTION:\n{question}\n\nRESPONSE:\n{response_text}"}],
        tools=[{
            "name": "submit_verdict",
            "description": "Submit a verdict on whether the response addresses the question.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["pass", "fail", "needs_clarification"]},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "reason": {"type": "string"},
                },
                "required": ["verdict", "confidence", "reason"],
            },
        }],
        tool_choice={"type": "tool", "name": "submit_verdict"},
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_verdict":
            return GatekeeperVerdict.model_validate(block.input)
    raise ValueError("No tool_use block returned by judge")
```

**Error handling pattern** (`judge.py` lines 165–176):
```python
# judge.py wraps entire call in try/except, returns ERROR sentinel — copy this:
except Exception as exc:
    log.warning("judge.error", dimension=dimension, error=str(exc))
    return {
        "dimension": dimension,
        "verdict": "ERROR",
        "score": 0,
        "reason": str(exc),
    }
# For validation_service.py: return None instead of dict — let Celery task handle None gracefully.
```

**Pydantic verdict models with normalizing field_validator** (RESEARCH.md §Code Examples):
```python
class GatekeeperVerdict(BaseModel):
    verdict: Literal["pass", "fail", "needs_clarification"]
    confidence: float
    reason: str

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_verdict(cls, v: str) -> str:
        return v.lower().replace("-", "_")

class CitationSpan(BaseModel):
    claim: str
    source_chunk: str
    supported: bool

class AuditorVerdict(BaseModel):
    verdict: Literal["grounded", "ungrounded", "partial"]
    confidence: float
    citation_spans: list[CitationSpan]
    reason: str

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_verdict(cls, v: str) -> str:
        return v.lower()

class StrategistVerdict(BaseModel):
    verdict: Literal["ship", "revise", "escalate"]
    confidence: float
    issues: list[str]
    reason: str

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_verdict(cls, v: str) -> str:
        return v.lower()
```

**Langfuse logging helper** (RESEARCH.md §Pattern 5 + §Code Examples):
```python
def _log_verdict(
    judge_name: str, agent_id: str, job_id: str,
    input_payload: dict, verdict_dict: dict,
    model: str = "claude-haiku-4-5",
) -> None:
    if _langfuse is None:
        return
    try:
        with _langfuse.start_as_current_generation(
            name=f"{judge_name}-judge",
            model=model,
            input=input_payload,
            output=verdict_dict,
            metadata={"agent_id": agent_id, "job_id": job_id},
        ):
            pass

        _langfuse.create_score(
            name=f"{judge_name}_verdict",
            value=verdict_dict.get("verdict", "unknown"),
            trace_id=job_id,
            data_type="CATEGORICAL",
        )
        _langfuse.flush()
    except Exception as exc:
        log.warning("langfuse.log_failed", judge=judge_name, error=str(exc))
```

---

### `apps/api/app/worker/tasks/runtime/validators.py` (task, request-response + CRUD)

**Analog:** `apps/api/app/worker/tasks/runtime/agent.py`

**Imports pattern** (`agent.py` lines 36–68):
```python
import ssl
import structlog
from sqlalchemy import text as sa_text
import redis as redis_lib
from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.events import emit
from app.worker.celery_app import celery_app
import psycopg2
import json
import uuid

log = structlog.get_logger(__name__)

_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)
```

**Task decorator pattern** (`retrieve.py` lines 65–72, `agent.py` lines 364–370):
```python
@celery_app.task(
    bind=True,
    acks_late=True,          # CLAUDE.md: non-negotiable
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_gatekeeper",   # distinct name per validator
)
def run_gatekeeper(self, agent_id: str, job_id: str, response_text: str, question: str) -> dict:
```

**Idempotency guard pattern** (`agent.py` lines 405–415):
```python
with get_sync_db() as db:
    existing = db.execute(
        sa_text(
            "SELECT 1 FROM job_events"
            " WHERE job_id = :jid AND event_type = 'gatekeeper.complete' LIMIT 1"
        ),
        {"jid": job_id},
    ).fetchone()
    if existing:
        log.info("run_gatekeeper.idempotent_skip", job_id=job_id)
        return {"status": "already_complete"}
```

**Agent fetch from control DB** (`agent.py` lines 420–428):
```python
    agent = db.get(Agent, agent_id)
    if agent is None:
        log.error("run_gatekeeper.agent_not_found", job_id=job_id, agent_id=agent_id)
        return {}
```

**conn_str decrypt pattern for Auditor** (`agent.py` line 442):
```python
    conn_str = fernet_decrypt(agent.neon_connection_string)
```

**psycopg2 tenant DB write pattern** (`agent.py` lines 100–112 `_create_conversation_row`):
```python
def _insert_verified_qa_candidate(
    conn_str: str, conversation_id: str, question: str,
    answer: str, citations: list[dict], auditor_confidence: float,
) -> None:
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO verified_qa_candidates
                  (id, conversation_id, question, answer, citations, auditor_confidence, queued_at, status)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, NOW(), 'pending')
                ON CONFLICT DO NOTHING
                """,
                (str(uuid.uuid4()), conversation_id, question, answer,
                 json.dumps(citations), auditor_confidence),
            )
        conn.commit()
    finally:
        conn.close()
```

**strategy_resynthesis_flagged update pattern** (RESEARCH.md §Pattern 6):
```python
# In run_auditor, after AuditorVerdict — uses control DB (db from get_sync_db context):
if verdict.verdict == "ungrounded":
    recent_ungrounded = db.execute(
        sa_text("""
            SELECT COUNT(*) FROM job_events
            WHERE event_type = 'auditor.complete'
              AND payload->>'agent_id' = :agent_id
              AND payload->>'verdict' = 'ungrounded'
              AND created_at > NOW() - INTERVAL '24 hours'
        """),
        {"agent_id": agent_id},
    ).scalar()

    if recent_ungrounded >= 3:
        db.execute(
            sa_text("UPDATE agents SET strategy_resynthesis_flagged = TRUE WHERE id = :id"),
            {"id": agent_id},
        )
        db.commit()
```

**Exception / retry pattern** (`agent.py` lines 615–643):
```python
    except Exception as exc:
        log.error("run_gatekeeper.failed", job_id=job_id, agent_id=agent_id, error=str(exc))
        if self.request.retries >= self.max_retries:
            pass  # validators: do not emit agent.failed; log and return {}
        else:
            countdown = 2 ** self.request.retries
            raise self.retry(exc=exc, countdown=countdown)
```

---

### `apps/api/app/worker/tasks/runtime/agent.py` — modification (add validator chain dispatch)

**Analog:** itself (`agent.py` lines 586–613)

**Insertion point** — after the `emit(job_id, "agent.response", ...)` call and `db.commit()` (lines 589–604), before the closing `log.info`:
```python
# --- M5: dispatch validation chain after agent.response is emitted ---
# Use celery chain with .si() (immutable) — chord is broken on solo pool.
# Import at top of file:
#   from celery import chain as celery_chain
#   from app.worker.tasks.runtime.validators import run_gatekeeper, run_auditor, run_strategist
import json as _json
retrieved_context_json = _json.dumps([
    tc.get("result", {}) for tc in tool_calls_log
    if tc.get("tool_name") == "retrieve"
][:3])  # top-3 chunks only (Redis message size guard)

celery_chain(
    run_gatekeeper.si(str(agent_id), job_id, response_text, message),
    run_auditor.si(str(agent_id), job_id, response_text, message,
                   retrieved_context_json, str(local_conversation_id)),
    run_strategist.si(str(agent_id), job_id, response_text, message),
).apply_async(queue="runtime")
```

---

### `apps/api/alembic/versions/0010_agent_strategy_resynthesis_flag.py` (migration, control DB)

**Analog:** `apps/api/alembic/versions/0009_agent_widget_config.py`

**Full file structure** (`0009_agent_widget_config.py` lines 1–36):
```python
"""Add strategy_resynthesis_flagged boolean column to agents table for M5 validation chain.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-23
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN strategy_resynthesis_flagged BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS strategy_resynthesis_flagged")
```

---

### `apps/api/alembic_tenant/versions/0004_verified_qa_candidates.py` (migration, tenant DB)

**Analog:** `apps/api/alembic_tenant/versions/0003_tenant_agent_conversations.py`

**Full file structure** (`0003_tenant_agent_conversations.py` lines 1–57):
```python
"""Tenant DB v4 migration — verified_qa_candidates staging table for M5 validation chain.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-23
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE verified_qa_candidates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            citations JSONB NOT NULL DEFAULT '[]'::jsonb,
            auditor_confidence FLOAT NOT NULL,
            queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected'))
        )
    """)
    op.execute("CREATE INDEX vqa_candidates_conversation_idx ON verified_qa_candidates(conversation_id)")
    op.execute("CREATE INDEX vqa_candidates_status_idx ON verified_qa_candidates(status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verified_qa_candidates")
```

---

### `apps/api/app/models/agent.py` — modification (add column)

**Analog:** itself — current file lines 51–53 (widget_config column, most recent addition):
```python
# M4.2: widget design customization (appearance + colors + typography)
widget_config: Mapped[dict] = mapped_column(
    JSONB, nullable=False, server_default=text("'{}'::jsonb")
)
```
Copy this pattern for the new boolean column. Add after `widget_config`:
```python
# M5: validation chain — persistent Auditor ungrounded failures trigger resynthesis
strategy_resynthesis_flagged: Mapped[bool] = mapped_column(
    nullable=False, server_default=text("false")
)
```
Import `Boolean` is not needed — SQLAlchemy infers bool from `Mapped[bool]`.

---

### `apps/api/tests/unit/test_validators.py` (test)

**Analog:** `apps/api/tests/unit/test_agent_task.py`

**Module-level monkeypatch pattern** (`test_agent_task.py` lines 38–69):
- No external SDK needs patching for validators (validators use `anthropic` directly, not the Agent SDK)
- But `langfuse` module-level init must be guarded. Set env vars in conftest or per-test:
```python
import os
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "test-pk")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "test-sk")
os.environ.setdefault("LANGFUSE_HOST", "http://localhost:3000")
```

**Agent mock helper** (`test_agent_task.py` lines 76–87):
```python
def _make_agent(agent_id: str | None = None) -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.UUID(agent_id) if agent_id else uuid.uuid4()
    agent.name = "Test Agent"
    agent.soul_role = "customer service representative"
    agent.soul_voice = "helpful"
    agent.soul_do_list = []
    agent.soul_donot_list = []
    agent.retrieval_strategy = {}
    agent.neon_connection_string = b"encrypted-bytes"
    agent.strategy_resynthesis_flagged = False  # M5: new field must be in mock
    return agent
```

**DB context mock** (`test_agent_task.py` lines 98–103):
```python
def _make_db_ctx(db: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx
```

**Test structure pattern** (`test_agent_task.py` lines 145–170 — idempotency skip test):
```python
def test_gatekeeper_idempotency_skip():
    from app.worker.tasks.runtime.validators import run_gatekeeper

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = MagicMock()  # row exists

    with (
        patch("app.worker.tasks.runtime.validators.get_sync_db", return_value=_make_db_ctx(mock_db)),
    ):
        result = run_gatekeeper.run(
            agent_id=agent_id, job_id=job_id, response_text="text", question="q"
        )

    assert result == {"status": "already_complete"}
```

**Pydantic model validation test pattern** (direct unit test, no mocks needed):
```python
def test_gatekeeper_verdict_model_validates():
    from app.services.validation_service import GatekeeperVerdict

    v = GatekeeperVerdict.model_validate({
        "verdict": "Pass",   # uppercase — field_validator normalizes to lower
        "confidence": 0.92,
        "reason": "Response addresses the question",
    })
    assert v.verdict == "pass"
    assert v.confidence == 0.92
```

**Langfuse log test pattern** — mock `_langfuse` at module level:
```python
def test_langfuse_logged_on_verdict(monkeypatch):
    mock_lf = MagicMock()
    monkeypatch.setattr("app.services.validation_service._langfuse", mock_lf)
    # ... call the validator task ... assert mock_lf.start_as_current_generation.called
```

---

### `apps/api/pyproject.toml` — modification (add langfuse dependency)

**Analog:** itself, `dependencies` list (lines 6–33).

Add after `anthropic==0.101.0` (line 26):
```toml
"langfuse==3.12.1",
```

---

### `scripts/demo_m5.sh` (script)

**Analog:** `scripts/demo_m3.sh`

**Full script structure** (`demo_m3.sh` lines 1–69):
```bash
#!/usr/bin/env bash
# demo_m5.sh — Veridian M5 validation chain smoke test
#
# Prerequisites:
#   - Local services running: Redis (redis-server), Postgres, uvicorn, Celery worker
#   - M4 agent with soul configured and data ingested (widget-accessible)
#   - AGENT_ID env var set
#   - jq installed
#
# Usage:
#   AGENT_ID=<uuid> bash scripts/demo_m5.sh
#   BASE_URL=http://localhost:8000 AGENT_ID=<uuid> bash scripts/demo_m5.sh
#
# Exit codes:
#   0 — demo passed
#   1 — any step failed

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
AGENT_ID="${AGENT_ID:-}"

if [[ -z "$AGENT_ID" ]]; then
    echo "ERROR: AGENT_ID required"
    exit 1
fi
```
Then POST adversarial query to `/v1/chat/agent/{AGENT_ID}/message` (widget endpoint from M4), poll job events for `agent.response`, then poll for `gatekeeper.complete`, `auditor.complete`, `strategist.complete` events. Print verdicts. Exit 0 if all three complete within 90s.

Key difference from `demo_m3.sh`: no `X-API-Key` header (widget endpoint is public); uses the widget chat endpoint path from M4 (`/v1/widget/{AGENT_ID}/chat` or the verified path from M4 implementation).

---

## Shared Patterns

### Celery Task Skeleton (acks_late + idempotency)
**Source:** `apps/api/app/worker/tasks/runtime/agent.py` lines 364–415 and `apps/api/app/worker/tasks/runtime/retrieve.py` lines 65–95
**Apply to:** `validators.py` (all three tasks: `run_gatekeeper`, `run_auditor`, `run_strategist`)
```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_gatekeeper",
)
def run_gatekeeper(self, agent_id: str, job_id: str, response_text: str, question: str) -> dict:
    with get_sync_db() as db:
        # 1. Idempotency guard (check job_events for '{validator}.complete')
        # 2. Fetch agent (agent = db.get(Agent, agent_id))
        # 3. Call validation_service judge function
        # 4. Log to Langfuse
        # 5. Emit SSE event (optional)
    return {}
```

### Module-level Redis Client
**Source:** `apps/api/app/worker/tasks/runtime/agent.py` lines 74–76
**Apply to:** `validators.py`
```python
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)
```

### Connection String Decryption (Auditor only — needs tenant DB)
**Source:** `apps/api/app/worker/tasks/runtime/agent.py` line 442
**Apply to:** `run_auditor` task only (to write `verified_qa_candidates`)
```python
conn_str = fernet_decrypt(agent.neon_connection_string)
```
Never pass `conn_str` as a task argument.

### psycopg2 Tenant DB Write
**Source:** `apps/api/app/worker/tasks/runtime/agent.py` lines 100–112 (`_create_conversation_row`)
**Apply to:** `_insert_verified_qa_candidate()` helper in `validators.py`
```python
conn = psycopg2.connect(conn_str, connect_timeout=5)
try:
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()
finally:
    conn.close()
```

### SSE emit (optional for validators)
**Source:** `apps/api/app/worker/tasks/runtime/agent.py` lines 589–598
**Apply to:** `validators.py` (informational only — emit `gatekeeper.complete` etc.)
```python
from app.services.events import emit
emit(job_id, "gatekeeper.complete", verdict.model_dump(), db, _redis)
```

### structlog logger
**Source:** `apps/api/app/worker/tasks/runtime/agent.py` line 68; `apps/api/app/services/retrieval_service.py` line 35
**Apply to:** `validation_service.py`, `validators.py`
```python
log = structlog.get_logger(__name__)
```

### Alembic migration file skeleton
**Source:** `apps/api/alembic/versions/0009_agent_widget_config.py` lines 18–26
**Apply to:** `0010_agent_strategy_resynthesis_flag.py`
```python
from typing import Sequence, Union
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
```

### Celery include list registration
**Source:** `apps/api/app/worker/celery_app.py` lines 68–79
**Apply to:** `celery_app.py` (add one line to `include` list)
```python
include=[
    # ... existing entries ...
    "app.worker.tasks.runtime.agent",
    "app.worker.tasks.runtime.validators",  # M5: Gatekeeper, Auditor, Strategist
],
```

### Langfuse lazy init guard
**Source:** RESEARCH.md §Pitfall 5 (anti-pattern), §Code Examples (Langfuse Pattern)
**Apply to:** `validators.py` module level
```python
import os
from langfuse import Langfuse
_langfuse: Langfuse | None = None
try:
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        _langfuse = Langfuse()
except Exception:
    pass
```

### Celery chain dispatch (validator fan-out from agent.py)
**Source:** RESEARCH.md §Pattern 1 — safe on `worker_pool=solo`
**Apply to:** `apps/api/app/worker/tasks/runtime/agent.py` (modification)
```python
from celery import chain as celery_chain
from app.worker.tasks.runtime.validators import run_gatekeeper, run_auditor, run_strategist

celery_chain(
    run_gatekeeper.si(str(agent_id), job_id, response_text, message),
    run_auditor.si(str(agent_id), job_id, response_text, message,
                   retrieved_context_json, str(local_conversation_id)),
    run_strategist.si(str(agent_id), job_id, response_text, message),
).apply_async(queue="runtime")
```
`chain` with `.si()` (immutable signatures) works on `worker_pool=solo`. Do NOT use `chord` (deadlocks on solo pool per RESEARCH.md §Pitfall 1).

---

## No Analog Found

None. All files have close analogs in the codebase.

---

## Critical Constraints to Enforce

| Constraint | Source | Applies To |
|------------|--------|------------|
| `acks_late=True` on every task | CLAUDE.md | `validators.py` all three tasks |
| No conn_str in task args | CLAUDE.md CTL-08 | `validators.py` — Auditor receives `agent_id`, decrypts at runtime |
| No Celery chord | RESEARCH.md §Pitfall 1 | `agent.py` modification — use `chain` |
| No asyncio in validators | RESEARCH.md §Anti-Patterns | `validators.py` — sync `anthropic.Anthropic().messages.create()` only |
| Langfuse `start_as_current_generation()` only | CLAUDE.md / RESEARCH.md | `validation_service.py` `_log_verdict()` |
| `_langfuse.flush()` after each task | RESEARCH.md §Pitfall 2 | `validators.py` each task body |
| Pydantic `model_validate()` before any DB write | RESEARCH.md §Anti-Patterns | `validators.py` Auditor before insert |
| `strategy_resynthesis_flagged` in Agent ORM mock | RESEARCH.md §Pitfall 4 | `test_validators.py` `_make_agent()` helper |
| No Docker in demo script | CLAUDE.md | `demo_m5.sh` — target `http://localhost:8000` |

---

## Metadata

**Analog search scope:** `apps/api/app/`, `apps/api/alembic/`, `apps/api/alembic_tenant/`, `apps/api/tests/unit/`, `scripts/`
**Files read:** 12 source files
**Pattern extraction date:** 2026-05-23
