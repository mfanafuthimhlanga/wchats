---
phase: 05-validation-chain
plan: 03
type: summary
wave: 3
status: complete
completed_at: 2026-05-23
---

# Plan 05-03 Summary: Validator Celery Tasks

## What Was Built

Created `apps/api/app/worker/tasks/runtime/validators.py` — the three runtime-queue
Celery tasks that wrap the Plan-02 judge functions with idempotency, Langfuse logging,
SSE completion events, verified_qa_candidates insert (Auditor, D-19), and the
strategy_resynthesis_flagged logic (Auditor, D-10/VAL-06). Registered the module in
celery_app.conf.include. All seven validator unit tests pass with zero xfail remaining.

## Task Signatures

```python
@celery_app.task(bind=True, acks_late=True, max_retries=2, default_retry_delay=5,
                 queue="runtime", name="run_gatekeeper")
def run_gatekeeper(self, agent_id: str, job_id: str, response_text: str, question: str) -> dict

@celery_app.task(bind=True, acks_late=True, max_retries=2, default_retry_delay=5,
                 queue="runtime", name="run_auditor")
def run_auditor(self, agent_id: str, job_id: str, response_text: str, question: str,
                retrieved_context_json: str, conversation_id: str) -> dict

@celery_app.task(bind=True, acks_late=True, max_retries=2, default_retry_delay=5,
                 queue="runtime", name="run_strategist")
def run_strategist(self, agent_id: str, job_id: str, response_text: str, question: str) -> dict
```

## Per-Judge Idempotency Event Names

| Task | Idempotency Guard Event |
|------|------------------------|
| run_gatekeeper | `gatekeeper.complete` |
| run_auditor | `auditor.complete` |
| run_strategist | `strategist.complete` |

Each guard runs `SELECT 1 FROM job_events WHERE job_id = :jid AND event_type = '<judge>.complete' LIMIT 1`
and returns `{"status": "already_complete"}` if the row exists.

## verified_qa_candidates Insert Path (D-19)

**Helper:** `_insert_verified_qa_candidate(conn_str, conversation_id, question, answer, citations, auditor_confidence)`

- Threshold source: `(agent.retrieval_strategy or {}).get("verified_qa_threshold", settings.VERIFIED_QA_CONFIDENCE_THRESHOLD)`
  — per-agent override in `retrieval_strategy` JSONB with global default `0.90` from Settings.
- Insert condition: `verdict.verdict == "grounded" AND verdict.confidence >= threshold`
- conn_str: decrypted at runtime via `fernet_decrypt(agent.neon_connection_string)` — never in task args (CTL-08).
- Columns (migration order): `id, conversation_id, question, answer, citations, auditor_confidence, queued_at, status`
- citations bound as `json.dumps([s.model_dump() for s in verdict.citation_spans])` cast to `%s::jsonb`
- `ON CONFLICT DO NOTHING` ensures insert-idempotency.

## Resynthesis Count Query and Flag Condition (D-10 / VAL-06)

```sql
SELECT COUNT(*) FROM job_events
WHERE event_type = 'auditor.complete'
  AND payload->>'agent_id' = :agent_id
  AND payload->>'verdict' = 'ungrounded'
  AND created_at > NOW() - INTERVAL '24 hours'
```

- `auditor.complete` is emitted **before** the count query, so the current ungrounded verdict
  is included in the 24h window (3 consecutive ungrounded verdicts sets the flag).
- Flag condition: `recent_ungrounded >= 3`
- SQL: `UPDATE agents SET strategy_resynthesis_flagged = TRUE WHERE id = :id` + `db.commit()`

## Safety Constraints Met

- No `asyncio` anywhere in validators.py (`grep` returns 0)
- No `agent.failed` emit anywhere in validators.py (`grep` returns 0)
- `acks_late=True` on all three task decorators
- `queue="runtime"` on all three task decorators
- `bind=True` on all three task decorators
- Validators never raise to Celery on exhaustion — `log.error + return {}`

## celery_app.py include Update

```python
# M5: validation chain (Gatekeeper, Auditor, Strategist)
"app.worker.tasks.runtime.validators",
```
Appended as the last entry in `celery_app.conf.include`.

## Test Results

```
tests/unit/test_validators.py::test_gatekeeper_verdict        PASSED
tests/unit/test_validators.py::test_run_gatekeeper_task       PASSED   (was xfail)
tests/unit/test_validators.py::test_auditor_verdict           PASSED
tests/unit/test_validators.py::test_auditor_inserts_candidate PASSED   (was xfail)
tests/unit/test_validators.py::test_strategist_verdict        PASSED
tests/unit/test_validators.py::test_langfuse_logged           PASSED
tests/unit/test_validators.py::test_resynthesis_flag          PASSED   (was xfail)

7 passed, 0 xfail in 10.70s
```

## Files Modified

- `apps/api/app/worker/tasks/runtime/validators.py` — created (570 lines)
- `apps/api/app/worker/celery_app.py` — include list updated
- `apps/api/tests/unit/test_validators.py` — 3 xfail stubs de-xfailed and implemented

## Requirements Satisfied

| Requirement | Status |
|-------------|--------|
| VAL-01: GatekeeperVerdict model (Plan 02) | Satisfied |
| VAL-02: run_gatekeeper task with idempotency + emit | Satisfied |
| VAL-03: AuditorVerdict model (Plan 02) | Satisfied |
| VAL-04: run_auditor inserts verified_qa_candidates | Satisfied |
| VAL-05: StrategistVerdict + _log_verdict (Plans 02/01) | Satisfied |
| VAL-06: strategy_resynthesis_flagged after 3 ungrounded | Satisfied |
