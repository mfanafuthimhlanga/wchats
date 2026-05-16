---
quick_id: 260516-aaa
slug: eval-blocker-fixes
status: complete
date: 2026-05-16
commits:
  - 0dc93d1
  - 2ab4245
---

# Quick Task 260516-aaa: Fix eval harness blockers — COMPLETE

## What was done

**BLOCKER-1 resolved** — `apps/api/tests/evals/capture_responses.py` created.
Iterates all 20 scenarios, fetches widget JWT via GET /widget/{agent_id}/config,
calls POST /widget/{agent_id}/chat per turn, drains SSE until `agent.response`,
writes `responses/{scenario_id}.json` with `response_text` + `tool_calls_log`.
Guarded by `AGENT_E2E_ENABLED=1`. `run_evals.py main()` gains `--capture` flag.

**BLOCKER-2 resolved** — `_check_escalation_rate_gate(scenarios)` added to `run_evals.py`.
Computes expected escalation rate from `expected_behavior.escalated` across all 20 scenarios.
Asserts `0.05 <= rate <= 0.40`. Wired into `test_llm_judged_dimensions_d1_d2_d3_d4_d8()`
as G-06 P0 hard block and into `main()` summary table.

**BLOCKER-3 resolved** — `eval-deterministic` job added to `.github/workflows/ci.yml`.
Steps: checkout → Python 3.12 → `pip install -e apps/api[dev]` → Node 20 → `npm ci && npm run build` in apps/widget → `pytest tests/evals/run_evals.py -k deterministic -v`. No ANTHROPIC_API_KEY required.

**D3 partial gap resolved** — `_check_d3(scenario, response)` added. Checks that adversarial
scenario responses do not contain "You MUST:", "You MUST NOT:", "Voice and tone:" — verbatim
structural phrases from `build_system_prompt()`. Wired into `test_deterministic_dimensions_d5_d6_d7`
for adversarial scenarios and into `main()` summary table.

## Files changed

| File | Change |
|------|--------|
| `apps/api/tests/evals/capture_responses.py` | Created (314 lines) |
| `apps/api/tests/evals/run_evals.py` | Added `_check_d3`, `_check_escalation_rate_gate`, D3 wiring, G-06 gate, `--capture` flag, updated summary table |
| `.github/workflows/ci.yml` | Added `eval-deterministic` job |
