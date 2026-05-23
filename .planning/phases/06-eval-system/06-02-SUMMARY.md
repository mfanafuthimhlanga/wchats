---
phase: "06"
plan: "06-02"
title: "eval_service.py — Ragas 0.4.x harness + verified_qa promotion"
status: complete
completed_at: "2026-05-23"
commits:
  - "29b52ee feat(06-02): Ragas 0.4.x eval harness — run_ragas_eval, write_eval_results, update_eval_run_status"
  - "a49636f feat(06-02): verified_qa promotion helper — promote_to_verified_qa + run_eval_for_agent"
files_created:
  - apps/api/app/services/eval_service.py
files_modified:
  - apps/api/pyproject.toml
---

## What Was Built

`apps/api/app/services/eval_service.py` — complete Ragas 0.4.x evaluation harness for Veridian M6.

### Task 1: Ragas 0.4.x evaluation harness

**Functions created:**

- `run_ragas_eval(scenarios, branch_conn_str) -> dict` — builds `EvaluationDataset.from_list()` from scenarios using `reference` field (D-02 LOCKED), runs Ragas evaluate() with four metrics (Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall), returns per-scenario scores and per-metric means.
- `write_eval_results(eval_run_id, scenario_scores, branch_conn_str) -> None` — inserts four rows per scenario (one per metric) into `eval_results` on the tenant branch DB via psycopg2 try/finally pattern.
- `update_eval_run_status(eval_run_id, status, finished_at, branch_conn_str) -> None` — UPDATE on `eval_runs` to set status and optionally `finished_at = NOW()`.

**Ragas 0.4.x API decisions:**

The plan specified `LangchainLLMWrapper(ChatAnthropic())` but Ragas 0.4.3 rejects `LangchainLLMWrapper` for `ragas.metrics.collections` metrics — it requires an `InstructorBaseRagasLLM`. The actual implementation uses:

```python
from ragas.llms import InstructorLLM
import instructor, anthropic

_anthropic_client = instructor.from_anthropic(anthropic.Anthropic())
llm = InstructorLLM(client=_anthropic_client, model=HAIKU_MODEL, provider="anthropic")
```

This uses the `instructor` library (1.12.0 already present) to wrap the Anthropic SDK — the correct Ragas 0.4.x path for Anthropic models. Metric instances are created per-call inside `run_ragas_eval()` since they require the llm at construction: `Faithfulness(llm=llm)`, etc.

All D-01/D-02/D-04 constraints are satisfied:
- Import: `from ragas.metrics.collections import Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall` (D-01 LOCKED)
- Dataset field: `"reference"` — no `ground_truths` anywhere in the file (D-02 LOCKED)
- Four metrics measured per scenario (D-04 LOCKED)

**Dependencies added to `pyproject.toml`:**
- `ragas>=0.4.0,<0.5.0`
- `instructor>=1.0.0,<2.0.0` (was already installed 1.12.0 — now declared)

### Task 2: verified_qa promotion helper

**Functions created:**

- `promote_to_verified_qa(scenarios, scenario_scores, branch_conn_str) -> int` — iterates scenario_scores, applies both threshold gates (D-21 LOCKED), embeds question via `_get_vo().embed([question], model="voyage-3", input_type="query")` (D-23 LOCKED), inserts to `verified_qa` with `source='sandbox_test'` and `promoted_by='system'` (D-22 LOCKED), ON CONFLICT DO NOTHING for Celery retry idempotency. Returns count of promoted rows.
- `run_eval_for_agent(eval_run_id, scenarios, branch_conn_str) -> dict` — top-level orchestrator for the 06-05 Celery task: status update → Ragas eval → DB writes → promotion → status complete. Catches exceptions, sets status 'failed', re-raises.

**Key patterns:**
- `str(question_vector)` bound to `%s::vector` — matching `retrieval_service.py` psycopg2 vector cast pattern
- All DB writes use psycopg2 `try/finally/conn.close()` — no context manager `with conn:`
- Branch connection string flows as a local variable only — never stored, never logged (D-10/D-18)

## Verification Results

All three plan verification commands pass:
1. `python -c "import ast; ast.parse(open('app/services/eval_service.py').read()); print('parse ok')"` → `parse ok`
2. `python -c "from app.services.eval_service import run_ragas_eval, write_eval_results, update_eval_run_status, promote_to_verified_qa; print('all imports ok')"` → `all imports ok`
3. `python -c "import app.services.eval_service as m; assert 'ragas.metrics.collections' in open(...).read(); assert 'ground_truths' not in open(...).read(); print('ragas API ok')"` → `ragas API ok`

## Success Criteria

- [x] `apps/api/app/services/eval_service.py` created with full Ragas 0.4.x harness
- [x] `run_eval_for_agent()` function implemented
- [x] `verified_qa` promotion logic implemented
- [x] Task 1 committed individually (commit 29b52ee)
- [x] Task 2 committed individually (commit a49636f)
- [x] `.planning/phases/06-eval-system/06-02-SUMMARY.md` written and committed
- [x] STATE.md and ROADMAP.md NOT modified
