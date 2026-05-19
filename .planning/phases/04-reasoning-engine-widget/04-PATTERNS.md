# Phase 4: M4 Reasoning Engine + Widget v0 — Pattern Map

**Mapped:** 2026-05-16
**Files analyzed:** 14 new/modified files
**Analogs found:** 10 / 14

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `apps/api/alembic/versions/0004_agent_soul_fields.py` | migration | transform | `apps/api/alembic/versions/0003_agent_retrieval_strategy.py` | exact |
| `apps/api/alembic_tenant/versions/0003_tenant_agent_conversations.py` | migration | transform | `apps/api/alembic_tenant/versions/0002_documents_ingestion_columns.py` | exact |
| `apps/api/app/core/config.py` | config | — | `apps/api/app/core/config.py` (extend existing) | exact |
| `apps/api/app/worker/celery_app.py` | config | — | `apps/api/app/worker/celery_app.py` (extend existing) | exact |
| `apps/api/app/services/agent_prompt.py` | service | transform | `apps/api/app/core/config.py` (Settings model pattern) | partial |
| `apps/api/app/services/agent_tools.py` | service | request-response | `apps/api/app/services/retrieval_service.py` | role-match |
| `apps/api/app/worker/tasks/runtime/agent.py` | Celery task | event-driven | `apps/api/app/worker/tasks/runtime/retrieve.py` | exact |
| `apps/api/app/api/v1/agent_chat.py` | controller | request-response | `apps/api/app/api/v1/query.py` | exact |
| `apps/api/app/api/v1/widget.py` | controller | request-response | `apps/api/app/api/v1/query.py` + `documents.py` | role-match |
| `apps/api/app/main.py` | config | — | `apps/api/app/main.py` (extend existing) | exact |
| `apps/widget/` | component | event-driven | no analog — derive from RESEARCH.md Pattern 5 + 6 | none |
| `apps/admin/` | component | request-response | no analog — derive from RESEARCH.md + Next.js spec | none |
| `apps/api/tests/evals/` | test | batch | no analog — derive from RESEARCH.md §Eval Harness | none |
| `apps/api/tests/unit/test_*.py` + `tests/integration/` | test | CRUD | `apps/api/tests/conftest.py` | role-match |

---

## Pattern Assignments

### `apps/api/alembic/versions/0004_agent_soul_fields.py` (migration, transform)

**Analog:** `apps/api/alembic/versions/0003_agent_retrieval_strategy.py`

**Full file pattern** (lines 1–35):
```python
"""Add soul fields to agents table for M4 reasoning engine.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-16
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN soul_voice TEXT")
    op.execute(
        "ALTER TABLE agents ADD COLUMN soul_do_list JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE agents ADD COLUMN soul_donot_list JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute("ALTER TABLE agents ADD COLUMN soul_role TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS soul_role")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS soul_donot_list")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS soul_do_list")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS soul_voice")
```

**Key rules from analog (0003):**
- Use `op.execute()` with raw SQL strings — do not use Alembic column helpers
- `down_revision` must point to the immediately preceding revision
- Downgrade drops columns in reverse order with `DROP COLUMN IF EXISTS`

---

### `apps/api/alembic_tenant/versions/0003_tenant_agent_conversations.py` (migration, transform)

**Analog:** `apps/api/alembic_tenant/versions/0002_documents_ingestion_columns.py`

**Module header pattern** (lines 1–30 of 0002):
```python
"""Tenant DB v3 migration — fix conversations schema for M4 agent session continuity.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-16

Changes:
  conversations: ADD COLUMN agent_id UUID
                 ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                 ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                 (Keep external_id, started_at, ended_at — safe; M1-M3 do not write these)

Rationale: CONTEXT.md §R-01 — conversations table from 0001 is missing agent_id
and metadata columns required for M4 agent session continuity.
M1-M3 tasks do not write to conversations — no data-loss risk from adding columns.
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
```

**Upgrade pattern** (from 0002 lines 39–48):
```python
def upgrade() -> None:
    op.execute("ALTER TABLE conversations ADD COLUMN agent_id UUID")
    op.execute(
        "ALTER TABLE conversations ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE conversations ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    # Index for agent_id lookups (conversation ownership validation)
    op.execute("CREATE INDEX conversations_agent_id_idx ON conversations(agent_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS conversations_agent_id_idx")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS metadata")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS created_at")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS agent_id")
```

**CRITICAL NOTE from RESEARCH.md R-01:** The actual `conversations` table schema from 0001 is:
```sql
conversations(id UUID, external_id TEXT, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ)
```
Do NOT drop `external_id`/`started_at`/`ended_at` — keep them and ADD the three new columns.

---

### `apps/api/app/core/config.py` (config, extend existing)

**Analog:** `apps/api/app/core/config.py` (the file itself — add fields to existing `Settings` class)

**Existing pattern to follow** (lines 24–68):
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_find_env_file(), extra="ignore")

    # ... existing fields ...
    COHERE_API_KEY: str | None = None  # optional pattern to copy for SMTP_* fields
```

**New fields to add** (insert after `COHERE_API_KEY` at line 64):
```python
    # M4: Widget JWT auth
    JWT_SECRET: str = "dev-secret-change-in-production"

    # M4: Escalation email (all optional — fallback to structlog WARNING if not set)
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_FROM: str | None = None
    OWNER_EMAIL: str | None = None
```

**`__repr__` must NOT be changed** — it already suppresses all field values (lines 67–68).

---

### `apps/api/app/worker/celery_app.py` (config, extend existing)

**Analog:** `apps/api/app/worker/celery_app.py` (the file itself — update the `include` list)

**Existing include list pattern** (lines 68–77):
```python
    include=[
        "app.worker.tasks.pipeline.provision",
        "app.worker.tasks.pipeline.migrations",
        "app.worker.tasks.pipeline.parse",
        "app.worker.tasks.pipeline.chunk",
        "app.worker.tasks.pipeline.metadata",
        "app.worker.tasks.pipeline.embed",
        # M3: hybrid retrieval task (runtime queue)
        "app.worker.tasks.runtime.retrieve",
        # M4: agent turn task (runtime queue)
        "app.worker.tasks.runtime.agent",
    ],
```

**Only change:** Append `"app.worker.tasks.runtime.agent"` with a comment. No other changes.

---

### `apps/api/app/services/agent_prompt.py` (service, transform)

**No direct analog** — pure function, derive from the locked specification in CONTEXT.md.

**Pattern to implement** (from CONTEXT.md §System Prompt Assembly):
```python
"""
agent_prompt — Assemble system prompt from agent soul fields at call time.

Design: system prompt is NEVER stored as a blob. Assembled at call time from
structured soul fields so the admin UI can present structured inputs (AGT-11)
and the prompt is auditable and testable.
"""
from app.models.agent import Agent


def build_system_prompt(agent: Agent) -> str:
    """Assemble system prompt from agent soul fields.

    Args:
        agent: Agent ORM model with soul_role, soul_voice, soul_do_list,
               soul_donot_list fields (added by migration 0004).

    Returns:
        Complete system prompt string for ClaudeSDKClient.
    """
    role = agent.soul_role or "customer service"
    voice = agent.soul_voice or "helpful and professional"
    do_list = "\n".join(f"- {item}" for item in (agent.soul_do_list or []))
    donot_list = "\n".join(f"- {item}" for item in (agent.soul_donot_list or []))
    return f"""You are a {role} agent for {agent.name}.
Voice and tone: {voice}
Do: {do_list or "(none specified)"}
Do not: {donot_list or "(none specified)"}
Always ground answers in retrieved content. Cite sources in your response.

When you have sources to cite, end your response with:
CITATIONS:
- Document: <document_name> | Section: <section_or_ordinal>
"""
```

**Imports pattern** — no heavy deps; only the `Agent` ORM model.

---

### `apps/api/app/services/agent_tools.py` (service, request-response)

**Analog:** `apps/api/app/services/retrieval_service.py` (for retrieve tool structure)

**Imports pattern** (from retrieval_service.py lines 1–35 + documents.py psycopg2 pattern):
```python
import psycopg2
import structlog
from pydantic import BaseModel

from app.services.retrieval_service import embed_query, rrf_fuse, rerank, RetrievalStrategy
from app.core.config import settings

log = structlog.get_logger(__name__)
```

**Module-level state pattern** (from retrieve.py lines 60–62 — safe for worker_pool=solo):
```python
# Module-level globals for tenant-scoped state — safe for worker_pool=solo
# (single-threaded; no concurrent tasks share this state)
_conn_str: str = ""
_agent_id: str = ""
_agent_name: str = ""
_strategy: RetrievalStrategy | None = None
```

**Tool allowlist pattern** (from RESEARCH.md Anti-Patterns, R-05):
```python
# ALLOWED_LOOKUP_TABLES must be a frozenset — prevents SQL injection via table name
ALLOWED_LOOKUP_TABLES: frozenset[str] = frozenset({"chunks", "documents", "chunk_metadata"})

MAX_CHUNKS: int = 5
MAX_CHUNK_TOKENS: int = 500
```

**build_tool_server factory pattern** (from RESEARCH.md Pattern 7):
```python
def build_tool_server(conn_str: str, agent_id: str, agent_name: str,
                      strategy: RetrievalStrategy):
    """Factory: inject tenant-scoped state into module globals, return server.

    Called once per run_agent_turn invocation. Module-level globals are safe
    because worker_pool=solo runs tasks sequentially in the main process.
    """
    global _conn_str, _agent_id, _agent_name, _strategy
    _conn_str = conn_str
    _agent_id = agent_id
    _agent_name = agent_name
    _strategy = strategy
    # Return the MCP server object with all four tools registered
    # (exact SDK API for claude-agent-sdk==0.1.81 — see AI-SPEC.md §4)
    ...
```

**retrieve tool — call retrieval_service directly** (from RESEARCH.md §Specific Ideas):
```python
# CRITICAL: Do NOT call retrieve_and_rank.apply_async() — already inside Celery task.
# Call retrieval_service functions directly:
query_vector = embed_query(query)
rrf_result = rrf_fuse(_conn_str, query_vector, query, _strategy)
reranked = rerank(query, rrf_result["fused"], _strategy)
chunks = reranked[:MAX_CHUNKS]
```

**lookup_structured allowlist check** (security — from CONTEXT.md):
```python
if table not in ALLOWED_LOOKUP_TABLES:
    return {"is_error": True, "message": f"Table '{table}' is not allowed"}
```

**psycopg2 tenant DB pattern** (from documents.py lines 170–215):
```python
tenant_conn = psycopg2.connect(_conn_str, connect_timeout=5)
try:
    with tenant_conn.cursor() as cur:
        cur.execute("SELECT ... FROM ... WHERE ...", params)
        rows = cur.fetchall()
finally:
    tenant_conn.close()
```

---

### `apps/api/app/worker/tasks/runtime/agent.py` (Celery task, event-driven)

**Analog:** `apps/api/app/worker/tasks/runtime/retrieve.py` — EXACT MATCH. Copy structure verbatim.

**Imports pattern** (retrieve.py lines 34–56):
```python
import asyncio
import ssl
import structlog
from datetime import datetime, timezone

import redis as redis_lib
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job import Job
from app.services.events import emit
from app.services.agent_prompt import build_system_prompt
from app.services.agent_tools import build_tool_server
from app.services.retrieval_service import RetrievalStrategy
from app.worker.celery_app import celery_app
```

**Module-level Redis client** (retrieve.py lines 59–62 — copy verbatim):
```python
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)
```

**Task decorator** (from CONTEXT.md §Celery Task Contract):
```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_agent_turn",
)
def run_agent_turn(self, job_id: str, agent_id: str, message: str,
                   conversation_id: str | None) -> dict:
```

**Idempotency guard** (retrieve.py lines 93–102 — copy pattern, change event_type):
```python
existing = db.execute(
    sa_text(
        "SELECT 1 FROM job_events"
        " WHERE job_id = :jid AND event_type = 'agent.response' LIMIT 1"
    ),
    {"jid": job_id},
).fetchone()
if existing:
    log.info("run_agent_turn.idempotent_skip", job_id=job_id)
    return {"status": "already_complete", "job_id": job_id}
```

**Agent + Job fetch** (retrieve.py lines 108–128):
```python
agent = db.get(Agent, agent_id)
if agent is None:
    log.error("run_agent_turn.agent_not_found", job_id=job_id, agent_id=agent_id)
    return {}

job = db.get(Job, job_id)
if job is None:
    log.error("run_agent_turn.job_not_found", job_id=job_id)
    return {}
```

**conn_str decrypt** (retrieve.py line 136 — copy verbatim):
```python
conn_str = fernet_decrypt(agent.neon_connection_string)
```

**asyncio bridge** (from RESEARCH.md Pattern 1 — CRITICAL):
```python
# CORRECT pattern for Windows worker_pool=solo + Python 3.12
result = asyncio.run(_run_sdk_turn(
    message=message,
    options=options,
    job_id=job_id,
    db=db,
    redis=_redis,
))
# NEVER use: loop.run_until_complete() — deprecated Python 3.10+, broken 3.12+
```

**SSE events sequence** (emit() call pattern from retrieve.py lines 141–205):
```python
emit(job_id, "agent.thinking", {"agent_id": agent_id}, db, _redis)
# ... after each tool call:
emit(job_id, "agent.tool_call", {"tool_name": ..., "input": ...}, db, _redis)
emit(job_id, "agent.tool_result", {"tool_name": ..., "summary": ...}, db, _redis)
# ... terminal event:
emit(job_id, "agent.response", {"text": ..., "citations": [...], "conversation_id": ...}, db, _redis)
# ... if escalated:
emit(job_id, "agent.escalated", {"reason": ..., "context": ...}, db, _redis)
```

**Error + retry pattern** (retrieve.py lines 219–249 — copy verbatim, adapt event name):
```python
except Exception as exc:
    log.error("run_agent_turn.failed", job_id=job_id, agent_id=agent_id, error=str(exc))
    if self.request.retries >= self.max_retries:
        try:
            with get_sync_db() as db2:
                job2 = db2.get(Job, job_id)
                if job2:
                    job2.status = "failed"
                    job2.finished_at = datetime.now(timezone.utc)
                    db2.commit()
                    emit(job_id, "agent.failed", {"error": str(exc)}, db2, _redis)
        except Exception:
            pass
    else:
        countdown = 2 ** self.request.retries
        raise self.retry(exc=exc, countdown=countdown)
```

---

### `apps/api/app/api/v1/agent_chat.py` (controller, request-response)

**Analog:** `apps/api/app/api/v1/query.py` — EXACT MATCH for route structure.

**Imports pattern** (query.py lines 18–37):
```python
import structlog
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant
from app.worker.tasks.runtime.agent import run_agent_turn

log = structlog.get_logger(__name__)
router = APIRouter(tags=["agent_chat"])
```

**Auth pattern** — `get_current_tenant` dependency (same as query.py, documents.py):
```python
async def post_agent_chat(
    agent_id: UUID,
    body: AgentChatRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> AgentChatResponse:
```

**Agent ownership validation** (query.py lines 76–90 — copy verbatim):
```python
result = await db.execute(
    select(Agent).where(
        Agent.id == agent_id,
        Agent.tenant_id == tenant.id,
        Agent.deleted_at.is_(None),
    )
)
agent = result.scalar_one_or_none()
if agent is None:
    raise HTTPException(status_code=404, detail="Agent not found")
if agent.status != "ready":
    raise HTTPException(
        status_code=409,
        detail=f"Agent is not ready (status={agent.status})",
    )
```

**Job creation + dispatch** (query.py lines 95–113 — copy pattern):
```python
job = Job(
    tenant_id=tenant.id,
    agent_id=agent.id,
    kind="agent_turn",
    status="pending",
)
db.add(job)
await db.commit()
await db.refresh(job)

run_agent_turn.apply_async(
    args=[str(job.id), str(agent.id), body.message, body.conversation_id],
    queue="runtime",
)
```

**202 response** (query.py lines 124–128):
```python
return AgentChatResponse(
    job_id=job.id,
    status="pending",
    events_url=f"/jobs/{job.id}/events",
    conversation_id=body.conversation_id,  # echoed back; new conv_id emitted in agent.response SSE
)
```

**GET /agents/{id}/conversations pattern** (query.py lines 136–196 for list pattern):
```python
# Query conversations from tenant DB via psycopg2 (not control DB)
# Pattern from documents.py lines 285–329 (tenant DB query):
conn_str = fernet_decrypt(agent.neon_connection_string)
tenant_conn = psycopg2.connect(conn_str, connect_timeout=5)
try:
    with tenant_conn.cursor() as cur:
        cur.execute(
            "SELECT id, created_at, metadata FROM conversations "
            "WHERE agent_id = %s ORDER BY created_at DESC LIMIT 50",
            (str(agent_id),),
        )
        rows = cur.fetchall()
finally:
    tenant_conn.close()
```

---

### `apps/api/app/api/v1/widget.py` (controller, request-response)

**Analog:** `apps/api/app/api/v1/query.py` (route structure) + `documents.py` (tenant DB access)

**Special CORS pattern** (from CONTEXT.md §CORS/CSP — widget endpoints differ from protected routes):
```python
# Widget endpoints use Access-Control-Allow-Origin: * (public widget)
# Applied via Response object in each route handler:
from fastapi import Response

@router.get("/widget/{agent_id}/config")
async def get_widget_config(
    agent_id: UUID,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
) -> WidgetConfigResponse:
    response.headers["Access-Control-Allow-Origin"] = "*"
    # No get_current_tenant — this is a PUBLIC endpoint
    ...
```

**JWT generation pattern** (from RESEARCH.md Pattern 2 — python-jose):
```python
from datetime import datetime, timedelta
from jose import jwt

def create_widget_jwt(agent_id: str) -> str:
    payload = {
        "sub": "widget",
        "agent_id": agent_id,
        "exp": datetime.utcnow() + timedelta(seconds=900),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
```

**JWT validation pattern** (from RESEARCH.md Pattern 2):
```python
from jose import JWTError

def validate_widget_jwt(token: str, expected_agent_id: str) -> dict:
    try:
        claims = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if claims.get("agent_id") != expected_agent_id:
        raise HTTPException(status_code=401, detail="Token agent_id mismatch")
    return claims
```

**POST /widget/{id}/chat Bearer JWT validation** (from CONTEXT.md):
```python
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer_scheme = HTTPBearer()

@router.post("/widget/{agent_id}/chat", status_code=202)
async def post_widget_chat(
    agent_id: UUID,
    body: WidgetChatRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_async_db),
) -> WidgetChatResponse:
    claims = validate_widget_jwt(credentials.credentials, str(agent_id))
    # claims["agent_id"] == str(agent_id) verified inside validate_widget_jwt
```

**Rate limiting pattern** (from RESEARCH.md Pattern 3):
```python
import time
# Use async Redis client (get_async_redis dependency):
bucket = str(int(time.time()) // 60)
key = f"rate:{agent_id}:{bucket}"
count = await redis.incr(key)
if count == 1:
    await redis.expire(key, 60)
if count > 60:
    raise HTTPException(status_code=429, detail="Rate limit exceeded")
```

**Widget SSE endpoint** (from RESEARCH.md R-06 — public endpoint scoped by job UUID):
```python
# Add GET /widget/jobs/{job_id}/events — public endpoint, no auth
# Reuse existing event_generator from sse.py — only the route wrapper changes
@router.get("/widget/jobs/{job_id}/events")
async def widget_job_events(job_id: UUID, ...):
    # Reuse: from app.api.v1.jobs import event_generator (or equivalent)
    # job_id UUID4 is unguessable — security by obscurity acceptable for M4
```

---

### `apps/api/app/main.py` (config, extend existing)

**Analog:** `apps/api/app/main.py` itself — add two router imports and two `include_router` calls.

**Existing router registration pattern** (lines 105–112):
```python
from app.api.v1 import agents, documents, health, jobs, tenants, query  # noqa: E402

app.include_router(tenants.router)
app.include_router(agents.router)
app.include_router(documents.router)
app.include_router(jobs.router)
app.include_router(health.router)
app.include_router(query.router)
```

**New lines to add** (append to import and include_router calls):
```python
from app.api.v1 import agents, documents, health, jobs, tenants, query, agent_chat, widget  # noqa: E402

# ... existing includes ...
app.include_router(agent_chat.router)
app.include_router(widget.router)
```

**CORS change** — add widget origin to `CORS_ORIGINS` in settings. The `CORSMiddleware` at lines 75–81 uses `settings.CORS_ORIGINS` which is already a `list[str]`. Widget endpoints that need `*` CORS must set the response header directly (not via the global middleware, which never allows wildcard per T-04-06).

---

### `apps/widget/` (Preact component, event-driven)

**No analog in codebase** — new directory. Derive from RESEARCH.md Pattern 5 (Vite config) and Pattern 6 (EventSource SSE).

**Vite config pattern** (RESEARCH.md Pattern 5):
```javascript
// apps/widget/vite.config.js
import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact()],
  build: {
    lib: {
      entry: 'src/index.jsx',
      name: 'VeridianWidget',
      fileName: 'widget',
      formats: ['iife'],
    },
    minify: 'terser',
    terserOptions: {
      compress: { drop_console: true, passes: 2 },
      mangle: true,
    },
    rollupOptions: {
      output: {
        manualChunks: undefined,
        inlineDynamicImports: true,
      },
    },
  },
})
```

**package.json postbuild size gate** (RESEARCH.md Pattern 5 — use Node zlib for Windows compat):
```json
{
  "scripts": {
    "build": "vite build",
    "postbuild": "node scripts/check-size.mjs"
  }
}
```

**check-size.mjs** (Windows-compatible via Node zlib — not gzip CLI):
```javascript
import { readFileSync } from 'fs'
import { gzipSync } from 'zlib'
const raw = readFileSync('dist/widget.iife.js')
const size = gzipSync(raw).length
if (size > 20480) {
  console.error(`BUNDLE SIZE EXCEEDED: ${size} bytes (limit 20480)`)
  process.exit(1)
}
console.log(`Bundle size OK: ${size} bytes`)
```

**EventSource SSE consumption** (RESEARCH.md Pattern 6):
```javascript
// apps/widget/src/Widget.jsx — SSE stream consumer
function startSSEStream(jobId, handlers) {
  const es = new EventSource(`/widget/jobs/${jobId}/events`)  // public endpoint (R-06 resolution)
  es.addEventListener('agent.thinking', (e) => handlers.onThinking(JSON.parse(e.data)))
  es.addEventListener('agent.tool_call', (e) => handlers.onToolCall(JSON.parse(e.data)))
  es.addEventListener('agent.response', (e) => {
    handlers.onResponse(JSON.parse(e.data))
    es.close()
  })
  es.addEventListener('agent.escalated', (e) => handlers.onEscalated(JSON.parse(e.data)))
  es.onerror = () => { handlers.onError(); es.close() }
  return es
}
```

**JWT storage — never localStorage** (CONTEXT.md §Widget JWT Security):
```javascript
// In Widget.jsx module scope — NOT localStorage, NOT sessionStorage
let _jwt = null

async function loadConfig(agentId) {
  const res = await fetch(`/widget/${agentId}/config`)
  const data = await res.json()
  _jwt = data.jwt  // stored only in JS module scope
  return data
}
```

**Design tokens** (from RESEARCH.md §Design System — Design G "Parchment & Wine"):
```css
/* apps/widget/src/widget.css */
:host {
  --accent: #7B1C3A;
  --gold: #B8860B;
  --bg: #FAF7F2;
  --text: #1A1A1A;
  font-family: system-ui, sans-serif;
}
```

---

### `apps/admin/` (Next.js component, request-response)

**No analog in codebase** — new directory. Created via `create-next-app`.

**Key pattern from RESEARCH.md R-08** — soul editor page MUST be a client component:
```typescript
// apps/admin/app/agents/[id]/soul/page.tsx
'use client'  // REQUIRED: page uses useState, useRef, useEffect

import { useState, useEffect } from 'react'

export default function SoulEditorPage({ params }: { params: { id: string } }) {
  const [agent, setAgent] = useState(null)
  const [apiKey, setApiKey] = useState('')

  useEffect(() => {
    // Client-side fetch with X-API-Key from session state
    fetch(`/api/v1/agents/${params.id}`, {
      headers: { 'X-API-Key': apiKey }
    }).then(r => r.json()).then(setAgent)
  }, [params.id, apiKey])

  const handleSave = async (soulFields) => {
    await fetch(`/api/v1/agents/${params.id}`, {
      method: 'PATCH',
      headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify(soulFields),
    })
  }
  // ...
}
```

**Design tokens for admin** (Inter font, not system-ui):
```css
/* apps/admin/globals.css */
:root {
  --accent: #7B1C3A;
  --gold: #B8860B;
}
body { font-family: 'Inter', sans-serif; }
```

---

### `apps/api/tests/unit/test_*.py` and `tests/integration/` (tests)

**Analog:** `apps/api/tests/conftest.py`

**conftest.py env setup pattern** (lines 29–56 — copy and extend):
```python
# Add to conftest.py after existing os.environ.setdefault calls:
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-tests-only")
os.environ.setdefault("SMTP_HOST", "")     # not set → fallback to log
os.environ.setdefault("SMTP_FROM", "")
os.environ.setdefault("OWNER_EMAIL", "")
```

**mock_db_session fixture** (lines 99–108 — reuse as-is for agent task tests):
```python
@pytest.fixture
def mock_db_session():
    session = MagicMock(spec=Session)
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)
    return session
```

**asyncio.run mock pattern** (from RESEARCH.md §Unit Tests — for agent task tests):
```python
# test_agent_task.py — mock at asyncio.run boundary (NOT AsyncMock for SDK):
with patch("app.worker.tasks.runtime.agent.asyncio.run") as mock_run:
    mock_run.return_value = {
        "text": "Here is the answer.",
        "citations": [{"document": "FAQ.pdf", "section": "1"}],
        "sdk_session_id": "sdk-session-123",
    }
    result = run_agent_turn.apply(args=[job_id, agent_id, "test message", None])
```

**Integration test guard pattern** (from RESEARCH.md §Integration Tests):
```python
# test_agent_chat_integration.py
import pytest
import os

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_TESTS_ENABLED"),
    reason="Set INTEGRATION_TESTS_ENABLED=1 to run integration tests",
)
```

**E2E guard pattern** (from RESEARCH.md §Eval Tests):
```python
# tests/evals/run_evals.py
pytestmark = pytest.mark.skipif(
    not os.getenv("AGENT_E2E_ENABLED"),
    reason="Set AGENT_E2E_ENABLED=1 to run E2E eval tests (requires real Claude API)",
)
```

---

### `apps/api/tests/evals/` (eval harness, batch)

**No analog in codebase** — new directory. Derive from RESEARCH.md §Eval Harness.

**LLM judge pattern** (from RESEARCH.md §Eval Tests — direct anthropic client, NOT Agent SDK):
```python
# tests/evals/judge.py
import anthropic

client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY from env

def judge(dimension: str, scenario: dict, response: str) -> dict:
    """Call claude-sonnet-4-5-20251001 as LLM judge for dimensions D1-D4, D8."""
    msg = client.messages.create(
        model="claude-sonnet-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": build_judge_prompt(dimension, scenario, response)}],
    )
    return parse_judge_response(msg.content[0].text)
```

**Scenario file format** (from RESEARCH.md §Eval Harness):
```json
{
  "id": "S-001",
  "description": "Basic product question answered from retrieved content",
  "user_message": "What are your opening hours?",
  "expected_dimensions": ["D1", "D5"],
  "ground_truth_source": "FAQ.pdf",
  "deterministic_checks": {
    "D5": {"regex": "CITATIONS:\\n- Document: .+ \\| Section: .+"},
    "D6": {"expected_tool": "retrieve"}
  }
}
```

---

## Shared Patterns

### Authentication (X-API-Key — protected routes)

**Source:** `apps/api/app/api/deps.py` (via `get_current_tenant` dependency)
**Apply to:** `agent_chat.py` routes (POST /agents/{id}/chat, GET /agents/{id}/conversations)

```python
# Import pattern used in query.py (line 25) and documents.py (line 46):
from app.api.deps import get_current_tenant
from app.models.tenant import Tenant

# Dependency injection:
tenant: Tenant = Depends(get_current_tenant)
```

### Authentication (Bearer JWT — widget routes)

**Source:** RESEARCH.md Pattern 2 (python-jose) — new for M4
**Apply to:** `widget.py` POST /widget/{id}/chat only

```python
from jose import jwt, JWTError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

bearer_scheme = HTTPBearer()
```

### Agent Ownership Validation (T-02-06-01 pattern)

**Source:** `apps/api/app/api/v1/query.py` lines 76–90 AND `documents.py` lines 105–119
**Apply to:** Both `agent_chat.py` and `widget.py` route handlers

```python
result = await db.execute(
    select(Agent).where(
        Agent.id == agent_id,
        Agent.tenant_id == tenant.id,
        Agent.deleted_at.is_(None),
    )
)
agent = result.scalar_one_or_none()
if agent is None:
    raise HTTPException(status_code=404, detail="Agent not found")
```

### Fernet Decrypt (conn_str at runtime)

**Source:** `apps/api/app/core/security.py` line 60 AND `retrieve.py` line 136
**Apply to:** `agent.py` task (conn_str decrypt), `agent_chat.py` GET conversations (tenant DB query)

```python
from app.core.security import fernet_decrypt

conn_str = fernet_decrypt(agent.neon_connection_string)
# NEVER log conn_str — T-02-01
```

### SSE Event Emission

**Source:** `apps/api/app/services/events.py` — `emit()` function (lines 36–84)
**Apply to:** `agent.py` Celery task (all agent.* events)

```python
from app.services.events import emit

# Signature: emit(job_id: UUID, event_type: str, payload: dict | None, db: Session, redis: SyncRedis)
emit(job_id, "agent.thinking", {"agent_id": agent_id}, db, _redis)
```

### Module-Level Sync Redis Client

**Source:** `apps/api/app/worker/tasks/runtime/retrieve.py` lines 59–62 AND `provision.py` lines 76–78
**Apply to:** `agent.py` Celery task

```python
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)
```

### CELERY_TASK_ALWAYS_EAGER Test Guard

**Source:** `apps/api/tests/conftest.py` line 56
**Apply to:** All new unit and integration test files (inherit from conftest.py — no extra action needed)

### Structlog Logging

**Source:** `apps/api/app/worker/tasks/runtime/retrieve.py` line 56
**Apply to:** All new Python modules

```python
import structlog
log = structlog.get_logger(__name__)
```

### Psycopg2 Tenant DB Pattern

**Source:** `apps/api/app/api/v1/documents.py` lines 170–215 and 285–329
**Apply to:** `agent_tools.py` (lookup_structured tool), `agent_chat.py` (GET conversations), `agent.py` task (conversation + message persistence)

```python
import psycopg2

tenant_conn = psycopg2.connect(conn_str, connect_timeout=5)
try:
    with tenant_conn.cursor() as cur:
        cur.execute("SELECT ... FROM ... WHERE ...", (param,))
        rows = cur.fetchall()
finally:
    tenant_conn.close()
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `apps/api/app/services/agent_prompt.py` | service | transform | Pure function with no codebase equivalent; derive entirely from CONTEXT.md locked spec |
| `apps/widget/` | component | event-driven | No frontend code in codebase; derive from RESEARCH.md Pattern 5+6 and UI-SPEC.md |
| `apps/admin/` | component | request-response | No Next.js code in codebase; scaffold via create-next-app, then add single soul editor page |
| `apps/api/tests/evals/` | test | batch | No eval harness exists; derive from RESEARCH.md §Eval Harness + AI-SPEC.md §5 |

---

## Critical Warnings (Propagate to Planner)

1. **R-01 — Tenant DB schema mismatch is BLOCKING.** Wave 0 MUST include migration `0003_tenant_agent_conversations.py` before any agent code runs. The `conversations` table is missing `agent_id` and `metadata` columns.

2. **R-02 — SDK session_id divergence.** On first turn, capture `ResultMessage.session_id` and store in `conversations.metadata["sdk_session_id"]`. Pass `resume=stored_sdk_session_id` on subsequent turns (NOT the local conversation UUID).

3. **R-04 — Vite output filename.** With `formats: ['iife']`, output is `dist/widget.iife.js` not `dist/widget.js`. All references (postbuild script, iframe `src`) must use `widget.iife.js`.

4. **R-05 — MCP tool namespace.** In `allowed_tools`, tools must use full MCP namespace: `"mcp__customer-tools__retrieve"` not `"retrieve"`.

5. **R-06 — Widget SSE auth gap.** Existing `/jobs/{id}/events` requires `X-API-Key`. Add new PUBLIC endpoint `GET /widget/jobs/{job_id}/events` in `widget.py`. EventSource cannot send custom headers.

6. **asyncio.run() only.** Never use `loop.run_until_complete()` — broken in Python 3.12. Confirmed by retrieve.py precedent using synchronous patterns (no asyncio bridge needed in retrieve.py but required in agent.py).

7. **No `apply_async` inside tools.** The `retrieve` tool must call `retrieval_service.rrf_fuse()` and `retrieval_service.rerank()` directly — NOT via `retrieve_and_rank.apply_async()`.

---

## Metadata

**Analog search scope:** `apps/api/app/`, `apps/api/alembic/`, `apps/api/alembic_tenant/`, `apps/api/tests/`
**Files read:** 13 source files
**Pattern extraction date:** 2026-05-16
