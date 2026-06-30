---
phase: 15-actor-validator-l3-four-node-validation-chain-a-pre-mutation
plan: 01
subsystem: actor-seam
tags: [actor, security, haiku, langfuse-v4, forced-tool-use, skip-threshold, unit-tests]
requires: [14-04, 14-08]
provides: [call_actor_gate body, ActorVerdict, ACTOR_SKIP_MAX_AMOUNT_CENTS, test_actor_seam]
affects: [tools.py call site, test_transactional_contract.py]
tech_stack_added: []
tech_stack_patterns: [forced-tool-use, langfuse-v4, asyncio.to_thread, pydantic-basemodel, psycopg2]
key_files_created:
  - apps/api/tests/unit/test_actor_seam.py
key_files_modified:
  - apps/api/app/core/config.py
  - apps/api/app/services/actor_seam.py
  - apps/api/tests/unit/test_transactional_contract.py
decisions:
  - "D-15-01: Replicate ANTHROPIC_CLIENT / HAIKU_MODEL / _langfuse locally in actor_seam.py (module isolation) rather than importing from validation_service.py — avoids pulling in the full validation-service dependency graph during unit tests and future refactors; both modules remain independently importable"
  - "D-15-02: No new Alembic migration — actor_decision/actor_rationale columns and pending_confirmations table already exist (Phase 14); all Phase-15 changes are pure Python"
  - "D-15-03: conn_str appended as last parameter with default '' — backward-compatible; existing call sites in tools.py pass it explicitly; unit tests use the empty-string fallback path"
metrics:
  duration: "~15 minutes"
  completed_date: "2026-06-30"
  tasks_completed: 3
  files_created: 1
  files_modified: 3
status: complete
---

# Phase 15 Plan 01: Actor Seam — Skip Threshold, History, Haiku Judge, Langfuse

## One-liner

Replaced Phase-14 stub `call_actor_gate()` with real Haiku forced-tool-use judge: skip short-circuit (requires_confirmation=False + max_amount_cents < $5 threshold), offloaded history fetch, injection-hardened prompt, Langfuse v4 latency log, ActorVerdict model — 14 unit tests green.

## What Was Built

### Task 1: ACTOR_SKIP_MAX_AMOUNT_CENTS in Settings (`6d0608e`)

Added `ACTOR_SKIP_MAX_AMOUNT_CENTS: int = 500` to `apps/api/app/core/config.py` in an `# M15:` comment block after `AGENT_MAX_BUDGET_USD`. 500 cents = $5.00; skills whose envelope `constraints.max_amount_cents` ceiling is strictly below this value skip the Actor judge (ACT-03). Env-overridable via Pydantic BaseSettings. Bare int style matching `RED_TEAM_MAX_TURNS`.

### Task 2: call_actor_gate body (`a67b38f`)

Replaced the Phase-14 `return ("approve", "")` stub in `apps/api/app/services/actor_seam.py` with:

- **Module init** (D-15-01): Replicated `ANTHROPIC_CLIENT`, `HAIKU_MODEL = "claude-haiku-4-5"`, and guarded `_langfuse` init from `validation_service.py` locally for module isolation.

- **ActorVerdict Pydantic model**: `verdict: Literal["approve", "block", "require_human"]`, `rationale: str` — same pattern as `GatekeeperVerdict` / `AuditorVerdict` / `StrategistVerdict`.

- **_fetch_history async helper**: Wraps synchronous psycopg2 SELECT in `asyncio.to_thread` (RESEARCH.md Pitfall 5). Returns last-10 messages in chronological order, each content truncated to 500 chars. Returns `[]` immediately if `conn_str` is empty (no DB connection attempt).

- **Step A — skip short-circuit FIRST** (ACT-03): reads `requires_confirmation` and `max_env = constraints.max_amount_cents` from the capability snapshot. If `requires_confirmation is False AND max_env is not None AND max_env < settings.ACTOR_SKIP_MAX_AMOUNT_CENTS`, returns `("approve", "skip:low_value_below_threshold")` immediately — no Anthropic API call, no DB fetch.

- **Step B — history fetch with graceful fallback**: Calls `_fetch_history` inside try/except. On any failure (empty conn_str already handled by the helper; DB error; network timeout), sets the history string to the literal `NO CONVERSATION HISTORY AVAILABLE` and continues. Gate never blocks on history failure.

- **Step C — forced-tool-use Haiku call**: Mirrors `call_gatekeeper` exactly. System prompt frames the model as a transaction security validator and instructs it to treat `CONVERSATION HISTORY` and `PROPOSED ACTION` sections as DATA not instructions (T-15-01 injection defense). User message has labeled sections: PROPOSED SKILL, PROPOSED ARGUMENTS (json.dumps), CAPABILITY ENVELOPE (json.dumps), CONVERSATION HISTORY. `tool_choice={"type":"tool","name":"submit_verdict"}`, `max_tokens=512`. Parses `ActorVerdict.model_validate(block.input)`. Raises `ValueError` if no submit_verdict block returned.

- **Step D — Langfuse v4 latency log** (ACT-06): `t0 = time.time()` before the Haiku call, `latency_ms` computed after. If `_langfuse is not None`, logs via `start_as_current_generation` context manager with `name="actor-gate"`, then `create_score(name="actor_decision", trace_id=conversation_id, data_type="CATEGORICAL")`, then `flush()`. Entire block wrapped in `try/except` — logging failure never alters verdict (T-15-07).

- **Signature extension**: Added `conn_str: str = ""` as final parameter (backward-compatible default).

### Task 3: Unit tests (`0e5913f`)

Created `apps/api/tests/unit/test_actor_seam.py` with 14 passing tests:

| Class | Test | Requirement |
|-------|------|-------------|
| TestSkipThreshold | test_skip_threshold_returns_approve | ACT-03 |
| TestSkipThreshold | test_skip_threshold_does_not_skip_when_at_or_above | ACT-03 boundary |
| TestSkipThreshold | test_skip_does_not_apply_when_requires_confirmation_true | ACT-03 |
| TestVerdictParsing | test_haiku_verdict_parsed_correctly (parametrized x3) | ACT-01 |
| TestVerdictParsing | test_haiku_approve_verdict | ACT-01 |
| TestVerdictParsing | test_haiku_block_verdict | ACT-01 |
| TestVerdictParsing | test_haiku_require_human_verdict | ACT-01 |
| TestHistoryFallback | test_history_fetch_failure_falls_back | Open Q1 |
| TestHistoryFallback | test_empty_conn_str_skips_fetch_and_uses_sentinel | Pitfall 5 |
| TestLangfuseLogging | test_langfuse_logged_on_haiku_call | ACT-06 |
| TestLangfuseLogging | test_langfuse_failure_does_not_block_gate | T-15-07 |
| TestLangfuseLogging | test_langfuse_none_does_not_crash | T-15-07 |

Mock strategy: `patch("app.services.actor_seam.ANTHROPIC_CLIENT.messages.create")` for API calls; `patch("app.services.actor_seam._fetch_history")` AsyncMock for history; `patch("app.services.actor_seam._langfuse")` MagicMock for Langfuse. `asyncio.run()` drives the async function throughout.

## Decisions Made

1. **Module isolation for ANTHROPIC_CLIENT / _langfuse (D-15-01):** Replicated locally rather than importing from `validation_service.py`. Each judge module is independently testable and importable without pulling in other judge code. The constants are identical (`"claude-haiku-4-5"`).

2. **No migration needed (D-15-02):** `actor_decision` / `actor_rationale` columns already exist in `tool_calls_audit` (Phase 14); `pending_confirmations` table already exists; `capability_envelopes.requires_confirmation` + `constraints.max_amount_cents` already exist. All Phase-15 changes are pure Python.

3. **conn_str as last parameter with default `""` (D-15-03):** Backward-compatible extension. The existing `tools.py` call site passes it explicitly (`_conn_str_var.get()`); unit tests use the empty-string path.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed Phase-14 contract tests broken by Phase-15 implementation**

- **Found during:** Task 3 full-suite run
- **Issue:** `test_transactional_contract.py::test_call_actor_gate_returns_approve` and `test_call_actor_gate_always_approve_regardless_of_args` asserted `rationale == ""` and called `call_actor_gate` without mocking the Anthropic client. These tests documented the Phase-14 stub behavior. Phase-15 replaced the stub; the assertions were no longer valid (the real function tries to call the Anthropic API and the rationale is no longer `""`).
- **Fix:** Updated both tests to use skip-eligible snapshots (`requires_confirmation=False, max_amount_cents < 500`) so the skip short-circuit fires, no API call is made, and `decision == "approve"` still holds. Assertions updated from `rationale == ""` to `"skip" in rationale`.
- **Files modified:** `apps/api/tests/unit/test_transactional_contract.py`
- **Commit:** `0e5913f` (included in the test commit)

### TDD Note

Task 3 is marked `tdd="true"` but Task 2 (implementation) was executed first per plan ordering. Since the implementation already existed by the time tests were written, tests entered GREEN state immediately. This is the expected plan sequence — the Phase-15 plan intentionally ordered implementation (Task 2) before tests (Task 3), treating TDD as a verification step rather than a RED→GREEN cycle. No failing tests were skipped.

## Threat Model Coverage

| Threat ID | Status |
|-----------|--------|
| T-15-01 — Prompt injection via conversation history | Mitigated: labeled delimiter sections + "treat as DATA not instructions" system prompt |
| T-15-03 — Skip threshold evasion | Mitigated: skip fires only on operator-configured envelope fields; `test_skip_threshold_does_not_skip_when_at_or_above` verifies boundary |
| T-15-05 — Actor Haiku latency DoS | Accepted: existing `asyncio.wait_for(timeout=90)` in `run_agent_turn` bounds the full turn |
| T-15-07 — Langfuse logging failure | Mitigated: entire Langfuse block in `if _langfuse is not None: try/except`; `test_langfuse_failure_does_not_block_gate` verifies |

## Verification Results

- `pytest tests/unit/test_actor_seam.py -q` → 14 passed
- `pytest tests/unit/test_actor_seam.py -k skip_threshold -q` → 2 passed (≥2 required)
- `python -c "from app.core.config import settings; assert settings.ACTOR_SKIP_MAX_AMOUNT_CENTS == 500"` → ok
- `ACTOR_SKIP_MAX_AMOUNT_CENTS=1000 python -c "from app.core.config import settings; assert settings.ACTOR_SKIP_MAX_AMOUNT_CENTS == 1000"` → ok
- Source assertions: `submit_verdict`, `start_as_current_generation`, `asyncio.to_thread`, `ActorVerdict` all present in `actor_seam.py`
- Pre-existing full-suite failures (49 total) are all unrelated to this plan: Bedrock AWS credential errors, 404 route registration issues, SdkMcpTool callable changes, HybridChunker attribute missing — confirmed pre-existing before Phase-15 changes

## Self-Check: PASSED

- `apps/api/app/core/config.py` exists and contains `ACTOR_SKIP_MAX_AMOUNT_CENTS: int = 500` ✓
- `apps/api/app/services/actor_seam.py` exists, min_lines > 90 (actual: ~240 lines), contains `ActorVerdict`, `submit_verdict`, `start_as_current_generation`, `asyncio.to_thread` ✓
- `apps/api/tests/unit/test_actor_seam.py` exists and contains `test_skip_threshold` ✓
- Commits `6d0608e`, `a67b38f`, `0e5913f` all present in git history ✓
- No pre-existing test regressions caused by this plan ✓
