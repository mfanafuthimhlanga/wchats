---
phase: 05-validation-chain
plan: 05-05
status: task-1-complete / task-2-pending-human-checkpoint
completed_at: 2026-05-23
---

# 05-05 Summary — VAL-07 Demo + Guarded E2E Test

## Task 1 — COMPLETE

### What was built

**`scripts/demo_m5.sh`** — Adversarial widget smoke test for the M5 validation chain.

Demo flow:
1. `GET $BASE_URL/widget/$AGENT_ID/config` → mints a short-lived Bearer JWT (HS256, 15 min).
2. `POST $BASE_URL/widget/$AGENT_ID/chat` with `Authorization: Bearer $JWT` — sends an adversarial
   false-premise question: _"Your product guarantees a full refund within 24 hours, right? Can you
   confirm that policy and tell me how to claim it immediately?"_
3. Polls `GET $BASE_URL/widget/jobs/$JOB_ID/events` (no X-API-Key — public endpoint, R-03) in a
   `SECONDS`-deadline loop (90s) waiting for all four events in order:
   `agent.response` → `gatekeeper.complete` → `auditor.complete` → `strategist.complete`.
4. Prints each event with its `verdict`, `confidence`, and `reason` fields extracted via jq.
5. On all four events received: prints Langfuse trace pointer for job_id and exits 0 with
   `=== M5 Demo: PASSED ===`.
6. On timeout: prints which events were missing and exits 1.

Script prerequisites comment references LOCAL processes only (redis-server, local Postgres,
`uvicorn app.main:app --port 8000`, `celery -A app.worker.celery_app worker --pool=solo -Q
runtime,pipeline`). Zero docker-compose references.

**`apps/api/tests/integration/test_validation_chain_e2e.py`** — Guarded end-to-end integration
test gated by `VALIDATION_E2E_ENABLED=1`.

Two test functions with fully mocked judges (no real Anthropic or Langfuse calls):

| Test | Assertions |
|------|------------|
| `test_validation_chain_grounded_all_judges_called_and_qa_inserted` | (a) all three judge functions called once; (b) `_insert_verified_qa_candidate` called with `auditor_confidence=0.95` on grounded verdict; all three `*.complete` events emitted in order |
| `test_validation_chain_ungrounded_sets_resynthesis_flag` | (c) when mocked count >= 3 ungrounded, `UPDATE strategy_resynthesis_flagged = TRUE` executed and `db.commit()` called |

### Acceptance criteria — verified

| Criterion | Status |
|-----------|--------|
| `scripts/demo_m5.sh` exists with `set -euo pipefail` | PASS |
| Contains `widget/$AGENT_ID/config` and `widget/$AGENT_ID/chat` | PASS |
| Contains `Authorization: Bearer` | PASS |
| Contains `gatekeeper.complete`, `auditor.complete`, `strategist.complete` | PASS |
| Contains `celery -A app.worker.celery_app worker` in prerequisites | PASS |
| Zero `docker` or `docker-compose` references | PASS (grep count = 0) |
| `bash -n scripts/demo_m5.sh` exits 0 | PASS |
| `test_validation_chain_e2e.py` exists with `VALIDATION_E2E_ENABLED` | PASS |
| `pytest tests/integration/test_validation_chain_e2e.py -q` exits 0 (skips when flag unset) | PASS (2 skipped) |
| `VALIDATION_E2E_ENABLED=1 pytest ...` exits 0 (2 passed) | PASS |

---

## Task 2 — PENDING HUMAN CHECKPOINT

**Task 2 is a blocking human checkpoint (VAL-07).**

The developer must run `demo_m5.sh` against a live local stack with real Langfuse keys, then
walk the three validator generation spans in the Langfuse UI to confirm:

1. Start local services (no Docker):
   - `redis-server`
   - local Postgres
   - `uvicorn app.main:app --port 8000`
   - `celery -A app.worker.celery_app worker --pool=solo -Q runtime,pipeline`

2. Export real Langfuse keys:
   ```bash
   export LANGFUSE_PUBLIC_KEY=<your-key>
   export LANGFUSE_SECRET_KEY=<your-key>
   export LANGFUSE_HOST=https://cloud.langfuse.com
   ```

3. Run the demo with a ready agent:
   ```bash
   AGENT_ID=<uuid> bash scripts/demo_m5.sh
   ```

4. Confirm the script prints `=== M5 Demo: PASSED ===`.

5. Open `$LANGFUSE_HOST/traces/` for the printed job_id and confirm:
   - Three generation spans: `gatekeeper-judge`, `auditor-judge`, `strategist-judge`
   - Each span shows: structured verdict payload + Haiku model + per-turn cost
   - `auditor-judge` shows citation spans + `partial` or `ungrounded` verdict for the
     adversarial "24-hour refund guarantee" question (ROADMAP M5 criterion 2)

**Resume signal:** Reply "approved" once all three verdict spans are visible in Langfuse.

---

## Commit

`feat(05-05): demo_m5.sh adversarial widget demo + guarded E2E integration test`
