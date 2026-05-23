---
phase: 05-validation-chain
plan: 02
status: complete
wave: 2
completed: 2026-05-23
tests_passed: 4
tests_de_xfailed: [test_gatekeeper_verdict, test_auditor_verdict, test_strategist_verdict, test_langfuse_logged]
---

# Plan 05-02 Summary: Verdict Models + Haiku Judge Functions + Langfuse Helper

## What Was Built

`apps/api/app/services/validation_service.py` — the production judge service for the M5
validation chain. Provides three Pydantic verdict models, three synchronous Haiku
tool-use judge functions, and a Langfuse v3 logging helper.

---

## Three Verdict Model Schemas

### GatekeeperVerdict
```python
class GatekeeperVerdict(BaseModel):
    verdict: Literal["pass", "fail", "needs_clarification"]  # D-06 LOCKED
    confidence: float   # 0.0–1.0
    reason: str
    @field_validator("verdict", mode="before")
    def normalize_verdict(cls, v) -> str: return v.lower().replace("-", "_")
```

### CitationSpan + AuditorVerdict
```python
class CitationSpan(BaseModel):
    claim: str          # excerpt from response text
    source_chunk: str   # excerpt from retrieved context
    supported: bool

class AuditorVerdict(BaseModel):
    verdict: Literal["grounded", "ungrounded", "partial"]  # D-08 LOCKED
    confidence: float   # >= 0.90 triggers verified_qa_candidates insert (D-19)
    citation_spans: list[CitationSpan]  # D-09: claim → retrieved passage mapping
    reason: str
    @field_validator("verdict", mode="before")
    def normalize_verdict(cls, v) -> str: return v.lower().replace("-", "_")
```

### StrategistVerdict
```python
class StrategistVerdict(BaseModel):
    verdict: Literal["ship", "revise", "escalate"]  # D-12 LOCKED
    confidence: float
    issues: list[str]   # specific issues found; empty on "ship"
    reason: str
    @field_validator("verdict", mode="before")
    def normalize_verdict(cls, v) -> str: return v.lower().replace("-", "_")
```

All three models carry `@field_validator("verdict", mode="before")` that calls
`.lower().replace("-", "_")` — this handles Claude returning capitalized values like
"Pass", "SHIP", "Grounded" (Pitfall 6 normalization).

---

## Tool-Use Call Signature

All three judge functions share the same structural pattern:

```python
ANTHROPIC_CLIENT.messages.create(
    model=HAIKU_MODEL,       # "claude-haiku-4-5"
    max_tokens=512,
    system=<judge instruction>,
    messages=[{"role": "user", "content": <delimited prompt>}],
    tools=[{"name": "submit_verdict", "description": ..., "input_schema": {...}}],
    tool_choice={"type": "tool", "name": "submit_verdict"},  # forced tool-use
)
# Extract:
for block in response.content:
    if block.type == "tool_use" and block.name == "submit_verdict":
        return Model.model_validate(block.input)
raise ValueError("No tool_use block returned by judge")
```

Forced `tool_choice` guarantees schema conformance — no fragile JSON parsing (RESEARCH Pattern 3).

---

## Delimited Prompt Injection Mitigation (T-05-02-01)

User-supplied text is **never concatenated as instructions**. Each judge function places
untrusted content inside explicitly labeled delimited sections. The system prompt instructs
the judge to treat all content after section headers as data to evaluate:

| Judge | Sections Used |
|-------|--------------|
| Gatekeeper | `QUESTION:` / `RESPONSE:` |
| Auditor | `QUESTION:` / `RESPONSE:` / `RETRIEVED CONTEXT:` |
| Strategist | `AGENT ROLE:` / `AGENT VOICE:` / `MUST DO:` / `MUST NOT:` / `QUESTION:` / `RESPONSE:` |

---

## Langfuse Helper API Used

`_log_verdict(judge_name, agent_id, job_id, input_payload, verdict_dict)`:

```python
# SDK v3 canonical pattern (D-16 / CLAUDE.md Rule 6):
with _langfuse.start_as_current_generation(
    name=f"{judge_name}-judge",
    model=HAIKU_MODEL,
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
```

- `start_as_current_generation` — v3 context manager (NOT `start_span` / `start_generation` / `.trace()`)
- `create_score(data_type="CATEGORICAL")` — categorical verdict value for Langfuse score tracking
- `flush()` — required in Celery workers (long-lived processes); ensures telemetry ships (Pitfall 2)
- Wrapped in `try/except log.warning(...)` — Langfuse failure never crashes validation
- No-ops when `_langfuse is None` (lazy-guarded module-level init — Pitfall 5)

---

## Module-Level Lazy Guard

```python
_langfuse: Langfuse | None = None
try:
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        _langfuse = Langfuse()
except Exception:
    pass  # validation still runs, just not logged
```

Prevents import-time crash in CI/test environments where `LANGFUSE_PUBLIC_KEY` is absent.

---

## Test Stubs De-Xfailed

| Test | Requirement | Status |
|------|-------------|--------|
| `test_gatekeeper_verdict` | VAL-01: GatekeeperVerdict validates + normalises | de-xfailed, GREEN |
| `test_auditor_verdict` | VAL-03: AuditorVerdict validates citation spans | de-xfailed, GREEN |
| `test_strategist_verdict` | VAL-05: StrategistVerdict validates + normalises | de-xfailed, GREEN |
| `test_langfuse_logged` | VAL-05: _log_verdict calls start_as_current_generation + flush | de-xfailed, GREEN |

Still xfail (implemented in 05-03):
- `test_run_gatekeeper_task` — Celery task idempotency (Plan 05-03)
- `test_auditor_inserts_candidate` — verified_qa_candidates insert (Plan 05-03)
- `test_resynthesis_flag` — strategy_resynthesis_flagged update (Plan 05-03)

---

## Acceptance Criteria Status

| Criterion | Status |
|-----------|--------|
| `class GatekeeperVerdict`, `class CitationSpan`, `class AuditorVerdict`, `class StrategistVerdict` present | PASS |
| Locked enums: `["pass","fail","needs_clarification"]`, `["grounded","ungrounded","partial"]`, `["ship","revise","escalate"]` | PASS |
| `@field_validator("verdict", mode="before")` on each model | PASS |
| `def call_gatekeeper(`, `def call_auditor(`, `def call_strategist(`, `def _log_verdict(` | PASS |
| `tool_choice={"type": "tool", "name": "submit_verdict"}` | PASS |
| `start_as_current_generation` + `create_score` + `.flush()` present | PASS |
| `start_span` / `.trace(` NOT in file (grep returns 0) | PASS |
| `RETRIEVED CONTEXT:` label (auditor) + `AGENT ROLE:` / `MUST NOT:` labels (strategist) | PASS |
| 4 tests pass: `pytest tests/unit/test_validators.py::test_gatekeeper_verdict ...::test_langfuse_logged -x -q` | PASS |
| `python -c "...assert 'asyncio' not in src..."` exits 0 | PASS |

---

## Commits

- `feat(05-02): verdict Pydantic models + de-xfail model tests (RED->GREEN)` — Task 1
- `feat(05-02): Haiku judge functions + Langfuse helper + de-xfail test_langfuse_logged` — Task 2
