---
phase: 04-reasoning-engine-widget
plan: "07"
subsystem: eval-harness
tags: [evals, integration-test, judge, scenarios, celery, sse]
dependency_graph:
  requires: [04-03, 04-04]
  provides: [eval-harness, integration-test-chain, 20-scenario-dataset]
  affects: [04-08]
tech_stack:
  added: [pytest-integration-guard, anthropic-judge-client]
  patterns: [asyncio-run-mock, INTEGRATION_TESTS_ENABLED-guard, AGENT_E2E_ENABLED-guard, deterministic-eval-phase]
key_files:
  created:
    - apps/api/tests/integration/test_agent_chat_integration.py
    - apps/api/tests/evals/__init__.py
    - apps/api/tests/evals/judge.py
    - apps/api/tests/evals/run_evals.py
    - apps/api/tests/evals/fixtures/demo_business_tenant.sql
    - apps/api/tests/evals/scenarios/S-001_golden_return_policy.json
    - apps/api/tests/evals/scenarios/S-002_golden_business_hours.json
    - apps/api/tests/evals/scenarios/S-003_golden_product_availability.json
    - apps/api/tests/evals/scenarios/S-004_golden_pricing.json
    - apps/api/tests/evals/scenarios/S-005_golden_contact_info.json
    - apps/api/tests/evals/scenarios/S-006_golden_shipping_policy.json
    - apps/api/tests/evals/scenarios/S-007_edge_ambiguous_return.json
    - apps/api/tests/evals/scenarios/S-008_edge_session_continuity.json
    - apps/api/tests/evals/scenarios/S-009_edge_partial_kb_match.json
    - apps/api/tests/evals/scenarios/S-010_edge_clarify_then_retrieve.json
    - apps/api/tests/evals/scenarios/S-011_edge_multi_turn_resolution.json
    - apps/api/tests/evals/scenarios/S-012_adv_system_prompt_extraction.json
    - apps/api/tests/evals/scenarios/S-013_adv_persona_override.json
    - apps/api/tests/evals/scenarios/S-014_adv_escalation_suppression.json
    - apps/api/tests/evals/scenarios/S-015_adv_lookup_table_injection.json
    - apps/api/tests/evals/scenarios/S-016_adv_indirect_injection.json
    - apps/api/tests/evals/scenarios/S-017_oos_no_kb_match.json
    - apps/api/tests/evals/scenarios/S-018_oos_out_of_domain.json
    - apps/api/tests/evals/scenarios/S-019_oos_competitor_redirect.json
    - apps/api/tests/evals/scenarios/S-020_oos_pii_request.json
decisions:
  - "[04-07] Integration test mocks asyncio.run boundary (not AsyncMock) — SDK subprocess never spawned in tests"
  - "[04-07] Deterministic eval (D5/D6/D7) gracefully skips missing responses/ directory — populated on first E2E run"
  - "[04-07] LLM judge guarded by @pytest.mark.skipif on test function, not module pytestmark — deterministic test always runs"
  - "[04-07] demo_business_tenant.sql uses zero vector(1024) for embeddings — eval scenarios mock retrieval, not real Voyage calls"
  - "[04-07] S-020 (PII request) has D6 table_attempt=users check — covers both out_of_scope and injection guard"
metrics:
  duration: "~22 min"
  completed: "2026-05-16"
  tasks: 2
  files: 25
---

# Phase 4 Plan 07: Eval Harness + Integration Test Summary

Integration test exercises the POST /agents/{id}/chat → Celery eager run_agent_turn → SSE event emission chain with asyncio.run mocked; eval harness covers all 8 dimensions with 20 Bella Vista Coffee scenarios (6/5/5/4 composition).

## Integration Test

**File:** `apps/api/tests/integration/test_agent_chat_integration.py`

**Test count:** 2
**Guard env var:** `INTEGRATION_TESTS_ENABLED=1`
**Default behavior:** Both tests skip when env var is unset

| Test | Description |
|------|-------------|
| `test_post_agent_chat_emits_thinking_then_response_via_eager_task` | POST /agents/{id}/chat → eager run_agent_turn → asserts agent.thinking before agent.response in job_events |
| `test_post_agent_chat_idempotent_on_retry` | Pre-insert agent.response event → trigger eager task → assert no new rows added |

**Mock strategy:**
- `@patch("app.worker.tasks.runtime.agent.asyncio.run")` returns canned dict — no Claude API, no SDK subprocess
- `_create_conversation_row` patched to return sentinel UUID
- `_persist_messages` and `_set_sdk_session_id` patched to no-op

## Eval Harness File Inventory

| File | Purpose |
|------|---------|
| `tests/evals/__init__.py` | Package marker (empty) |
| `tests/evals/judge.py` | LLM judge wrapper — `claude-sonnet-4-5-20251001` via direct anthropic SDK |
| `tests/evals/run_evals.py` | Main harness — deterministic + LLM-judged phases + CLI main() |
| `tests/evals/fixtures/demo_business_tenant.sql` | Bella Vista Coffee corpus: 6 docs, 18 chunks, 18 chunk_metadata, 18 embeddings |
| `tests/evals/scenarios/S-001 through S-020` | 20 scenario JSON files (6/5/5/4) |
| `tests/evals/responses/` | NOT committed — populated during E2E runs; gitignored |

## Dimension Coverage Table (D1-D8)

| # | Dimension | Measurement | Guard |
|---|-----------|-------------|-------|
| D1 | Grounding fidelity | LLM judge (claude-sonnet-4-5-20251001) | AGENT_E2E_ENABLED |
| D2 | Escalation accuracy | LLM judge | AGENT_E2E_ENABLED |
| D3 | Prompt injection resistance | LLM judge | AGENT_E2E_ENABLED |
| D4 | Session continuity | LLM judge | AGENT_E2E_ENABLED |
| D5 | Citation format compliance | Deterministic regex | None — always runs |
| D6 | Tool call correctness | Deterministic assertions | None — always runs |
| D7 | Widget bundle size | Deterministic zlib gzip | None — skips if bundle missing |
| D8 | Knowledge gap honesty | LLM judge | AGENT_E2E_ENABLED |

## Scenario Composition

| Category | Count | IDs |
|----------|-------|-----|
| golden_path | 6 | S-001 through S-006 |
| edge | 5 | S-007 through S-011 |
| adversarial | 5 | S-012 through S-016 |
| out_of_scope | 4 | S-017 through S-020 |
| **Total** | **20** | |

## Deterministic Phase Exit Status

```
pytest tests/evals/run_evals.py -k deterministic -q
1 passed in 1.06s
```

**Exit code: 0** — passes without ANTHROPIC_API_KEY or local services.

Skipped checks: D5 and D6 scenario checks skip gracefully when `tests/evals/responses/` directory is absent (responses populated only during full E2E runs).

D7 bundle check skips when `widget/dist/widget.iife.js` does not exist (built by Plan 04-05).

## Judge Model + API Path

- **Model:** `claude-sonnet-4-5-20251001` (NOT Haiku — avoids self-evaluation bias per AI-SPEC.md §5.2)
- **API path:** `anthropic.Anthropic().messages.create(...)` — direct SDK, NOT Claude Agent SDK
- **Max tokens:** 256 per judge call (cost cap T-04-07-01)
- **System prompt:** JSON-only verdict `{dimension, verdict, score, reason}` enforced
- **Agent system_prompt:** never passed to judge (T-04-07-02)

## Notes on responses/ Directory

The `tests/evals/responses/` directory is:
- **Not created** by this plan — it does not exist yet
- **Populated** during full E2E runs (`AGENT_E2E_ENABLED=1`) which run the live agent against each scenario and save `{scenario_id}.json` files
- **Gitignored** per Plan 04-05 `.gitignore` precedent (T-04-07-03 — no PII in git)
- **Required for:** D5/D6 deterministic checks on specific scenarios; D1-D4/D8 LLM judge calls
- When absent, `test_deterministic_dimensions_d5_d6_d7` logs SKIPPED per scenario and does not fail

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- `apps/api/tests/integration/test_agent_chat_integration.py` exists: FOUND
- `apps/api/tests/evals/judge.py` exists: FOUND
- `apps/api/tests/evals/run_evals.py` exists: FOUND
- 20 scenario files exist: FOUND (verified by composition check)
- `apps/api/tests/evals/fixtures/demo_business_tenant.sql` exists: FOUND
- Task 1 commit d35d725: FOUND
- Task 2 commit c438337: FOUND
- Deterministic pytest exit 0: VERIFIED
