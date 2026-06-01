---
slug: empty-answer-on-retrieve
status: resolved
trigger: "Phase 12 deployed agent fe230a9d returns an empty agent.response (text=\"\") when it must retrieve to answer; no-retrieve questions answer fine"
created: 2026-06-01
updated: 2026-06-01
phase: 12
---

# Debug: Agent returns empty answer when it retrieves

## Symptoms

- **Expected:** A grounded, cited answer to questions about Bantuson / W Chats (e.g. "What is W Chats and who is Bantuson?").
- **Actual:** the `agent.response` event has `"text": ""` (response_length=0). The agent emits one `retrieve` tool_call, then produces empty text. All three M5 validators correctly flag it: gatekeeper=`fail` ("completely empty"), auditor=`ungrounded`, strategist=`revise`.
- **Errors:** No exception/traceback. Worker log shows `citation_block_missing ... response_length=0` then `run_agent_turn.complete ... citation_count=0` — the turn "succeeds" but the text is empty.
- **Timeline:** Observed 2026-05-30 during Phase 12 live tunnel testing (agent `fe230a9d-09f0-4043-b2f1-4506a2ef0059`, Neon `nameless-fog-19651218`). Introduced by the Phase 12 plan 12-01 **D-10** change (commit `15468e2`).
- **Reproduction:** A live agent turn (POST `/widget/{id}/chat`) with a question that requires retrieval. Does NOT need the tunnel — reproduces locally against the same agent.

## Key contrast (the tell)

- No-retrieve question -- "What is W Chats?" -> **707-char answer**, `tool_call_count=0`. OK (job `8a8bc9a6`)
- Retrieve question -- "What is W Chats and who is Bantuson?" -> 1 `retrieve` tool_call -> **empty text**. FAIL (job `53157a8d`)

So the agent breaks specifically **after it makes a retrieve tool call** -- it does not get to compose the final grounded answer.

## Prime suspects -- `apps/api/app/worker/tasks/runtime/agent.py` (12-01 D-10 change, commit `15468e2`)

1. **`max_turns=3` (line ~532, was 10).** The SDK turn budget may be exhausted by the retrieve round-trip (emit `tool_use` -> receive `tool_result` -> ...) leaving no turn to emit the final answer text. Capping *retrieve calls* (the D-10 intent, for the Voyage 3 RPM free tier) got conflated with capping *total agent turns*. Likely fix: raise `max_turns` (~6) and cap retrieve calls with a **tool-level guard** (block the 3rd retrieve) instead of throttling total turns.
2. **`max_budget_usd=0.05` (line ~533).** A retrieving turn spends more tokens; if it hits the $0.05 cap mid-turn the SDK may stop before composing the answer -> empty text. Co-suspect; check the SDK stop reason.
3. **System-prompt "Call the `retrieve` tool AT MOST ONCE per response" (line ~515) + MUST-ground rule.** After one retrieve returns low-relevance chunks, a strict grounding rule may make the agent return empty rather than answer -- i.e. behaving "correctly" but uselessly. Check retrieval relevance for the Bantuson query too.

## Current Focus

- **hypothesis:** `max_turns=3` cuts the agent off after the retrieve tool round-trip, before it emits the final answer text.
- **test:** locally reproduce a retrieval-requiring turn; instrument/inspect the SDK message stream to find the **stop reason** on the empty turn (max_turns hit vs max_budget hit vs model genuinely returned empty vs retrieve returned nothing). Then try `max_turns=6` (+ a retrieve-call tool-level cap) and re-run.
- **expecting:** with more turns, the agent composes a non-empty grounded answer while retrieve calls stay <=2.
- **next_action:** RESOLVED -- see Resolution section.

## Evidence

- `timestamp: 2026-05-30` -- SSE stream (job `53157a8d`): `agent.thinking` -> `agent.tool_call` (retrieve, query "Bantuson Mhlanga W Chats background experience") -> `agent.response` `text=""` -> `gatekeeper.complete` fail / `auditor.complete` ungrounded / `strategist.complete` revise. Captured in `apps/api/_runlogs/tunnel/sse_body.txt`.
- `timestamp: 2026-05-30` -- worker log (job `53157a8d`): `run_agent_turn.first_turn` -> `citation_block_missing response_length=0` -> `run_agent_turn.complete citation_count=0 escalated=False` (no error). In `apps/api/_runlogs/tunnel/celery.err.log`.
- `timestamp: 2026-05-30` -- worker log (job `8a8bc9a6`, "What is W Chats?"): `run_agent_turn.complete` with response_length=707, tool_call_count=0 (did NOT retrieve). Confirms non-retrieve answers work.
- `timestamp: 2026-06-01` -- suspect code: `apps/api/app/worker/tasks/runtime/agent.py` lines ~510-555 (`max_turns=3`, `max_budget_usd=0.05`, "retrieve AT MOST ONCE" prompt). Changed in commit `15468e2` (12-01). Original was `max_turns=10`.
- `timestamp: 2026-06-01` -- SDK source analysis: `claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport._build_command()` line 259: `if self._options.max_turns: cmd.extend(["--max-turns", str(self._options.max_turns)])`. The Claude Code CLI's `--max-turns` counter includes tool call iterations. With `max_turns=3`: turn 1 = model emits `retrieve` tool_use; turn 2 = model receives tool_result; the model needs turn 3 to compose text -> max_turns=3 means max_turns is exhausted exactly when the final synthesis turn is needed. The CLI emits `{"type": "result", "subtype": "error_max_turns", "is_error": true}` which is parsed as a `ResultMessage` by `client.receive_response()` -- the loop exits immediately with `response_text=""`.
- `timestamp: 2026-06-01` -- confirmed: `ResultMessage` is yielded even on error (subtype=error_max_turns), `receive_response()` terminates after the ResultMessage, `response_text` stays `""`. No exception is raised -> `run_agent_turn.complete` logs normally with empty response.
- `timestamp: 2026-06-01` -- RE-OPENED: live job `02a0ee6d` (max_turns=6 active) STILL returns empty text. tool_call_count=1 (one retrieve), 76.4s total turn (~38s of thinking before retrieve). NOT hitting the turn cap. New suspect: `max_budget_usd=0.05`.
- `timestamp: 2026-06-01` -- ResultMessage type inspection (sdk 0.1.81): fields are `subtype`, `is_error`, `num_turns`, `total_cost_usd`, `stop_reason`, `api_error_status`, `session_id`, `duration_ms`, `usage`. These are the disambiguating fields -- all stop paths (max_turns, max_budget, execution_error) produce the SAME empty `response_text` signature with no exception.
- `timestamp: 2026-06-01` -- D-10 fix phase 2 applied: (1) `_run_sdk_turn` now logs `_run_sdk_turn.result` info line with all stop-reason fields every turn; (2) `max_budget_usd` raised from 0.05 to `settings.AGENT_MAX_BUDGET_USD` (default 0.50); (3) `AGENT_MAX_BUDGET_USD: float = 0.50` added to `Settings` in `config.py`. All 28 unit tests pass.

## Eliminated

- `max_budget_usd=0.05` as primary cause (CYCLE 1, incorrect): the turn completes in ~31s total (under 90s timeout), tool_call_count=1 only; budget cap would require many more tokens. This reasoning was wrong -- cycle 2 live evidence showed 76.4s with ~38s of thinking, which is consistent with the budget being hit.
- Retrieval returning empty results: `tool_call_count=1` confirmed a retrieve DID execute; the auditor sees no retrieved context only because response_text is empty (the context is lost when the SDK stops before synthesis).
- Exception / silent failure path: worker log shows `run_agent_turn.complete` (not `run_agent_turn.failed`), confirming the task succeeded but produced empty text.
- `max_turns=3` as the SOLE cause (CYCLE 1, incorrect): cycle 2 showed max_turns=6 in effect with only 1 retrieve tool call, yet still empty text. max_turns=3 was a real bug but insufficient alone.

## Resolution

- root_cause: TWO causes. (1) `max_turns=3` in cycle 1 (fixed in commit `132f529`). (2) `max_budget_usd=0.05` is too low for a turn with extended thinking (~38s) + retrieve + synthesis on Sonnet -- the budget is exhausted before the model emits final text. The CLI emits `result{subtype:error_max_budget, is_error:true}` -> `receive_response()` terminates -> `response_text=""` with no exception raised.
- fix: Three-part fix across two commits. (1) `max_turns` 3->6. (2) Tool-level retrieve cap in `agent_tools.py`. (3) `max_budget_usd` raised from 0.05 to `settings.AGENT_MAX_BUDGET_USD` (default 0.50, env-configurable); added `AGENT_MAX_BUDGET_USD` to `Settings`; added `_run_sdk_turn.result` diagnostic log line to permanently surface the SDK stop reason.
- verification: 28 unit tests pass. **LIVE-VERIFIED 2026-06-01 (job `fdf93abd`, via localhost.run tunnel):** "What is W Chats and who is Bantuson?" → `agent.response.text` length=**1741**, citations=**1** (grounded answer about Bantuson + W Chats). Instrumented line: `_run_sdk_turn.result subtype=success is_error=False num_turns=2 stop_reason=end_turn response_length=1741 total_cost_usd=0.0629784`. **The $0.063 cost > the old $0.05 cap — definitive confirmation that `max_budget_usd=0.05` was the binding constraint** (and num_turns=2 confirms max_turns=6 had headroom). Both fixes (max_turns + budget) were required. RESOLVED.
- files_changed:
  - `apps/api/app/worker/tasks/runtime/agent.py` -- ResultMessage instrumentation (log subtype/is_error/num_turns/total_cost_usd); max_budget_usd -> settings.AGENT_MAX_BUDGET_USD; updated comments
  - `apps/api/app/core/config.py` -- added AGENT_MAX_BUDGET_USD: float = 0.50
  - `apps/api/tests/unit/test_agent_task.py` -- added test_max_budget_uses_settings_not_hardcoded, test_result_message_stop_reason_logged

## RE-OPENED 2026-06-01 -- fix #1 (max_turns) FAILED live verification

The `132f529` fix was unit-verified but **failed the live end-to-end test**. Status reverted to `investigating`. The `max_turns=3` diagnosis was incomplete -- raising it to 6 did NOT fix the empty answer.

**New live evidence (job `02a0ee6d`, max_turns=6 in effect, via localhost.run tunnel):**
- `agent.response.text` length = **0** (STILL empty), citations = 0. Validators again: gatekeeper fail / auditor ungrounded / strategist **escalate** (confidence 0.3).
- Worker timeline: `build_tool_server.ready` 11:38:18 -> `retrieve_tool.start call_count=1 query='W Chats Bantuson'` **11:38:56** (approx 38s of model activity BEFORE the single retrieve) -> `citation_block_missing response_length=0` 11:39:08 -> `run_agent_turn.complete` 11:39:18. **Total turn 76.4s.**
- **Only ONE retrieve** (`tool_call_count=1`) and `max_turns=6` -> the turn is NOT hitting the turn cap. So `error_max_turns` is NOT the (sole) cause. The earlier static-analysis conclusion was wrong/incomplete.

**New prime hypothesis -- `max_budget_usd=0.05` (agent.py ~line 533) is too low.** This was eliminated prematurely (the earlier "~31s, too few tokens" reasoning is contradicted by this 76s turn). With extended thinking ON (~38s of pre-retrieve thinking) + the retrieved context fed back + the system prompt, a Sonnet turn easily exceeds $0.05. The SDK likely stops on `result{subtype: error_max_budget...}` (or similar) -> empty `response_text`, no exception -- identical empty-text signature to the max_turns path.

**Re-opened Current Focus:**
- hypothesis: `max_budget_usd=0.05` is exhausted by extended-thinking + retrieve + synthesis, stopping the turn before the model emits final text.
- test: log/inspect the SDK `ResultMessage` (subtype, num_turns, total_cost_usd) in `_run_sdk_turn` to see the ACTUAL stop reason (do NOT guess -- instrument it). Then raise/remove `max_budget_usd` (e.g. 0.50) -- and/or check whether extended thinking should be reduced -- and re-run.
- next_action: instrument `_run_sdk_turn` to capture and log the SDK ResultMessage stop reason (subtype + total_cost_usd + num_turns); this single datum disambiguates budget vs turns vs empty-model-output. Verify against a live turn.
- IMPORTANT: keep the prior fixes (max_turns=6 + retrieve cap) -- they are correct and necessary; this is an ADDITIONAL cause. Re-verify live (non-empty grounded answer to "who is Bantuson?") before declaring resolved this time.

**Process lesson:** the first cycle declared "resolved" on unit-test verification alone. Empty-text has multiple causes with the SAME signature (error_max_turns AND error_max_budget both yield empty `response_text` with no exception). Must capture the SDK ResultMessage subtype to distinguish, and must live-verify a non-empty answer before closing.

## D-10 Fix Phase 2 -- instrumentation + budget fix (2026-06-01)

**Changes applied (all 28 unit tests pass):**
- `apps/api/app/core/config.py`: Added `AGENT_MAX_BUDGET_USD: float = 0.50` to Settings (env-configurable via `AGENT_MAX_BUDGET_USD` env var).
- `apps/api/app/worker/tasks/runtime/agent.py`:
  - `_run_sdk_turn` ResultMessage handler now logs `_run_sdk_turn.result` (info, always) and `_run_sdk_turn.sdk_error` (warning, when `is_error=True`) with fields: `subtype`, `is_error`, `num_turns`, `total_cost_usd`, `stop_reason`, `api_error_status`, `response_length`. This is a permanent diagnostic improvement -- the single log line disambiguates all SDK stop paths.
  - `max_budget_usd=0.05` replaced with `max_budget_usd=settings.AGENT_MAX_BUDGET_USD` (default 0.50). The prior fixes (max_turns=6 + retrieve cap) are retained unchanged.
- `apps/api/tests/unit/test_agent_task.py`: Added tests `test_max_budget_uses_settings_not_hardcoded` and `test_result_message_stop_reason_logged` -- both pass.

**Unit evidence:**
- All 28 tests pass: `pytest tests/unit/test_agent_task.py tests/unit/test_agent_tools.py`.
- `test_max_budget_uses_settings_not_hardcoded`: asserts `max_budget_usd >= 0.50` in ClaudeAgentOptions.
- `test_result_message_stop_reason_logged`: asserts `_run_sdk_turn.result` log line emitted with `subtype`, `is_error`, `num_turns`, `total_cost_usd`, `response_length` fields; and `_run_sdk_turn.sdk_error` warning emitted on `is_error=True`.

**Status: awaiting live verification** -- do NOT mark resolved until a live turn produces non-empty `agent.response.text` with at least 1 citation, AND the worker log shows `_run_sdk_turn.result subtype=success` (or `is_error=False`).

## CHECKPOINT: human-action required -- live end-to-end verification

**What to run:**
1. Start local stack: `redis-server`, PostgreSQL already running, then in `apps/api/`:
   - `celery -A app.worker.celery_app worker -Q pipeline,runtime --loglevel=info`
   - `uvicorn app.main:app --port 8000`
2. Start tunnel: `ssh -R 80:localhost:8000 nokey@localhost.run` (or `start_demo.ps1`).
3. POST to widget: `curl -X POST https://<tunnel-url>/widget/fe230a9d-09f0-4043-b2f1-4506a2ef0059/chat -H "Content-Type: application/json" -d "{\"message\": \"What is W Chats and who is Bantuson?\"}"`
4. Watch the Celery worker log for the new `_run_sdk_turn.result` log line.

**What to confirm (success):**
- The `_run_sdk_turn.result` log line appears with `subtype=success` and `is_error=false`.
- The SSE stream produces a non-empty `agent.response.text` (length > 0) with at least 1 citation.
- If both are true: update `status: resolved` in this file's frontmatter.

**What to report (failure path):**
- If `_run_sdk_turn.result` shows `is_error=true`: report the exact `subtype`, `total_cost_usd`, and `num_turns` from the log line. This is the next disambiguation data point.
  - `subtype=error_max_budget`: raise `AGENT_MAX_BUDGET_USD` further in `.env` (e.g. 1.00) or investigate whether extended thinking should be disabled for this agent type.
  - `subtype=error_max_turns`: the turn is still hitting the turns cap -- raise `max_turns` further.
  - `subtype=error_during_execution`: look for the `errors` field in the log and the `_run_sdk_turn.sdk_error` warning for details.

**Log line to look for (structlog JSON format):**
```
event=_run_sdk_turn.result job_id=<...> subtype=success is_error=false num_turns=<N> total_cost_usd=<USD> response_length=<>0>
```
