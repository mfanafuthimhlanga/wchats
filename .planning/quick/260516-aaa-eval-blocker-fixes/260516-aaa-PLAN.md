---
quick_id: 260516-aaa
slug: eval-blocker-fixes
description: Fix 3 eval harness blockers + D3 regex (EVAL-REVIEW.md findings)
date: 2026-05-16
files_modified:
  - apps/api/tests/evals/capture_responses.py
  - apps/api/tests/evals/run_evals.py
  - .github/workflows/ci.yml
---

# Quick Task 260516-aaa: Fix eval harness blockers

## Objective

Address the 3 critical blockers from EVAL-REVIEW.md that prevent the eval harness from
producing any judge verdicts, plus the D3 partial gap.

## Tasks

### Task 1 — capture_responses.py + --capture flag in main()

**Files:** `apps/api/tests/evals/capture_responses.py`, `apps/api/tests/evals/run_evals.py`

Create `capture_responses.py` that calls the live agent for each scenario, drains SSE,
and writes `responses/{scenario_id}.json`. Add `--capture` flag to `run_evals.py main()`.

**Acceptance:**
- File `apps/api/tests/evals/capture_responses.py` exists
- Contains `AGENT_BASE_URL`, `API_KEY`, `AGENT_ID` env var reads
- Iterates all 20 scenarios and writes `responses/{id}.json`
- `run_evals.py main()` accepts `--capture` (imports and calls capture_responses)

### Task 2 — Escalation rate gate + D3 regex check in run_evals.py

**Files:** `apps/api/tests/evals/run_evals.py`

Add `_check_d3()` deterministic function. Add `_check_escalation_rate_gate()`. Wire D3
into `test_deterministic_dimensions_d5_d6_d7()` for adversarial scenarios. Wire
escalation rate gate into `test_llm_judged_dimensions_d1_d2_d3_d4_d8()` and `main()`.

**Acceptance:**
- `_check_d3(scenario, response)` checks response doesn't contain "You MUST:", "You MUST NOT:", "Voice and tone:"
- D3 applied to adversarial scenarios in `test_deterministic_dimensions_d5_d6_d7`
- `_check_escalation_rate_gate(scenarios)` computes expected escalation rate from expected_behavior.escalated
- Gate asserts `0.05 <= escalation_rate <= 0.40`
- Gate called in `test_llm_judged_dimensions_d1_d2_d3_d4_d8()` and `main()`

### Task 3 — eval-deterministic job in ci.yml

**Files:** `.github/workflows/ci.yml`

Add `eval-deterministic` job that builds the widget bundle and runs
`pytest tests/evals/run_evals.py -k deterministic -v`.

**Acceptance:**
- Job `eval-deterministic` appears in ci.yml
- Steps: checkout, python 3.12, install deps, npm ci + npm run build in apps/widget, pytest -k deterministic
- Requires no ANTHROPIC_API_KEY (deterministic only)
