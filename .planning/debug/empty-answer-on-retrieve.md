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

- No-retrieve question — "What is W Chats?" → **707-char answer**, `tool_call_count=0`. ✅ (job `8a8bc9a6`)
- Retrieve question — "What is W Chats and who is Bantuson?" → 1 `retrieve` tool_call → **empty text**. ❌ (job `53157a8d`)

So the agent breaks specifically **after it makes a retrieve tool call** — it does not get to compose the final grounded answer.

## Prime suspects — `apps/api/app/worker/tasks/runtime/agent.py` (12-01 D-10 change, commit `15468e2`)

1. **`max_turns=3` (line ~532, was 10).** The SDK turn budget may be exhausted by the retrieve round-trip (emit `tool_use` → receive `tool_result` → …) leaving no turn to emit the final answer text. Capping *retrieve calls* (the D-10 intent, for the Voyage 3 RPM free tier) got conflated with capping *total agent turns*. Likely fix: raise `max_turns` (~6) and cap retrieve calls with a **tool-level guard** (block the 3rd retrieve) instead of throttling total turns.
2. **`max_budget_usd=0.05` (line ~533).** A retrieving turn spends more tokens; if it hits the $0.05 cap mid-turn the SDK may stop before composing the answer → empty text. Co-suspect; check the SDK stop reason.
3. **System-prompt "Call the `retrieve` tool AT MOST ONCE per response" (line ~515) + MUST-ground rule.** After one retrieve returns low-relevance chunks, a strict grounding rule may make the agent return empty rather than answer — i.e. behaving "correctly" but uselessly. Check retrieval relevance for the Bantuson query too.

## Current Focus

- **hypothesis:** `max_turns=3` cuts the agent off after the retrieve tool round-trip, before it emits the final answer text.
- **test:** locally reproduce a retrieval-requiring turn; instrument/inspect the SDK message stream to find the **stop reason** on the empty turn (max_turns hit vs max_budget hit vs model genuinely returned empty vs retrieve returned nothing). Then try `max_turns=6` (+ a retrieve-call tool-level cap) and re-run.
- **expecting:** with more turns, the agent composes a non-empty grounded answer while retrieve calls stay ≤2.
- **next_action:** RESOLVED — see Resolution section.

## Evidence

- `timestamp: 2026-05-30` — SSE stream (job `53157a8d`): `agent.thinking` → `agent.tool_call` (retrieve, query "Bantuson Mhlanga W Chats background experience") → `agent.response` `text=""` → `gatekeeper.complete` fail / `auditor.complete` ungrounded / `strategist.complete` revise. Captured in `apps/api/_runlogs/tunnel/sse_body.txt`.
- `timestamp: 2026-05-30` — worker log (job `53157a8d`): `run_agent_turn.first_turn` → `citation_block_missing response_length=0` → `run_agent_turn.complete citation_count=0 escalated=False` (no error). In `apps/api/_runlogs/tunnel/celery.err.log`.
- `timestamp: 2026-05-30` — worker log (job `8a8bc9a6`, "What is W Chats?"): `run_agent_turn.complete` with response_length=707, tool_call_count=0 (did NOT retrieve). Confirms non-retrieve answers work.
- `timestamp: 2026-06-01` — suspect code: `apps/api/app/worker/tasks/runtime/agent.py` lines ~510–555 (`max_turns=3`, `max_budget_usd=0.05`, "retrieve AT MOST ONCE" prompt). Changed in commit `15468e2` (12-01). Original was `max_turns=10`.
- `timestamp: 2026-06-01` — SDK source analysis: `claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport._build_command()` line 259: `if self._options.max_turns: cmd.extend(["--max-turns", str(self._options.max_turns)])`. The Claude Code CLI's `--max-turns` counter includes tool call iterations. With `max_turns=3`: turn 1 = model emits `retrieve` tool_use; turn 2 = model receives tool_result; the model needs turn 3 to compose text → max_turns=3 means max_turns is exhausted exactly when the final synthesis turn is needed. The CLI emits `{"type": "result", "subtype": "error_max_turns", "is_error": true}` which is parsed as a `ResultMessage` by `client.receive_response()` — the loop exits immediately with `response_text=""`.
- `timestamp: 2026-06-01` — confirmed: `ResultMessage` is yielded even on error (subtype=error_max_turns), `receive_response()` terminates after the ResultMessage, `response_text` stays `""`. No exception is raised → `run_agent_turn.complete` logs normally with empty response.

## Eliminated

- `max_budget_usd=0.05` as primary cause: the turn completes in ~31s total (under 90s timeout), tool_call_count=1 only; budget cap would require many more tokens. Possible secondary contributor but not root cause.
- Retrieval returning empty results: `tool_call_count=1` confirmed a retrieve DID execute; the auditor sees no retrieved context only because response_text is empty (the context is lost when the SDK stops before synthesis).
- Exception / silent failure path: worker log shows `run_agent_turn.complete` (not `run_agent_turn.failed`), confirming the task succeeded but produced empty text.

## Resolution

- root_cause: `max_turns=3` in `ClaudeAgentOptions` (set in D-10 commit `15468e2`) was too low. The Claude Code CLI's `--max-turns 3` counter counts each model-calls-tool iteration as a turn. A retrieve round-trip (thinking+tool_use = turn 1, tool_result → model needs turn 2 or 3 to synthesize) uses up the entire turn budget before the final text answer is composed. The CLI emits `result{subtype:error_max_turns, is_error:true}` which the SDK's `receive_response()` yields as a `ResultMessage` and terminates — `response_text` stays `""`. No exception is raised, so `run_agent_turn` logs success with an empty answer.
- fix: Two-part fix: (1) Raised `max_turns` from 3 to 6 in `apps/api/app/worker/tasks/runtime/agent.py` — gives the agent enough turns to retrieve and synthesize. (2) Added a tool-level per-turn retrieve call counter (`_retrieve_call_count`) in `apps/api/app/services/agent_tools.py` — blocks the 3rd+ retrieve call per turn with `is_error=True`, enforcing the Voyage 3 RPM guard independently of `max_turns`. Counter is reset to 0 by `build_tool_server()` at the start of each `run_agent_turn` invocation. The system-prompt guard ("AT MOST ONCE") is retained as a belt-and-suspenders prompt hint.
- verification: All 26 unit tests pass (`pytest tests/unit/test_agent_task.py tests/unit/test_agent_tools.py`). New tests added: `test_max_turns_allows_synthesis_after_retrieve` (asserts max_turns >= 6), `test_retrieve_tool_blocked_on_third_call` (asserts is_error on 3rd call), `test_retrieve_tool_counter_reset_by_build_tool_server` (asserts counter=0 after build_tool_server). Live repro plan: run `start_demo.ps1`, POST to `/widget/fe230a9d.../chat` with "What is W Chats and who is Bantuson?" — expect non-empty `agent.response.text` and at least one citation.
- files_changed:
  - `apps/api/app/worker/tasks/runtime/agent.py` — max_turns 3→6, updated D-10 comments
  - `apps/api/app/services/agent_tools.py` — added `_retrieve_call_count` global, D-10 cap check in `retrieve_tool`, counter reset in `build_tool_server`
  - `apps/api/tests/unit/test_agent_task.py` — renamed + updated `test_max_turns_capped_to_three` → `test_max_turns_allows_synthesis_after_retrieve` (asserts max_turns >= 6)
  - `apps/api/tests/unit/test_agent_tools.py` — added tests 15 (blocked on 3rd call) and 16 (counter reset)
