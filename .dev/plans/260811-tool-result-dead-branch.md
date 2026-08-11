# PLAN — the ToolResultBlock dead branch (`5.9`, `5.8`, and the production half)

**Date:** 2026-08-11 · **Branch:** `chore/local-postgres` · **Source:** BACKLOG `5.9`, `5.8`;
HANDOFF "Next moves" #4.

## Goal

Settle `5.9` statically (done — see Evidence), then fix what it found. `5.9` as filed scoped the dead
branch to `red_team_probe.py`. It is **also on the production customer turn path** (`agent.py:939`),
where it silently empties the grounding and retrieval measurement layer.

## Evidence (settled before any code change, no spend)

Three independent lines, all free:

1. **The SDK's own two transcript readers treat `tool_result` as a user-entry phenomenon.**
   `_internal/sessions.py:277-280` considers only `"type":"user"` lines and skips those containing
   `"tool_result"`; `_internal/session_summary.py:81-92` returns early unless `entry["type"] ==
   "user"`, then skips content carrying a `tool_result` block.
2. **42,334 real CLI transcript entries, 782 session files, on this machine**
   (`~/.claude/projects/**/*.jsonl` — the same `claude.exe` the SDK spawns): every single
   `tool_result`-carrying entry is `type:"user"` / `role:"user"`. **Assistant-carried count: 0.**
3. **The Messages API shape.** `tool_result` is a user-turn content block by construction; the
   assistant emits `tool_use`, the caller returns `tool_result`.

`message_parser.py:148` does have a `case "tool_result"` under `case "assistant"` — it is
forward-compatible defensiveness, not a shape the CLI produces. Nothing observed it produce one.

**Not claimed:** the 42k entries are session JSONL, while the SDK reads stdout stream-json. The two
are the same message vocabulary and line 1 is the SDK's own reading of it, but a direct observation
of the stdout stream would cost a live model call and was not taken.

## The three defects

**D-A — `red_team_probe.py:340,349` (`5.9`, as filed).** `if not isinstance(msg, AssistantMessage):
continue` skips every `UserMessage`, so `tool_results` is always empty and the probe transcript has
zero `skill=…` lines. `test_confused_deputy`'s assertions loop over that empty list ⇒ **vacuous pass
for ~$0.12**. Confirms `5.9`'s hypothesis.

**D-B — `agent.py:915,939` (NOT filed anywhere — the production half).** Same dead branch on
`run_agent_turn`. Consequences, each a channel that is read but never written:
- `agent.tool_result` job_events are never emitted ⇒ `retrieval_eval._fetch_turn_context`
  (`retrieval_eval.py:191`) always builds `retrieve_contexts == []`.
- `tc["result"]` is never set ⇒ `agent.py:1420` `retrieve_results` is always `[]` ⇒ the **Auditor
  (the grounding judge) is handed `retrieved_context_json == "[]"` on every turn**.
- `RETRIEVE_CHUNKS_KEY` is never set ⇒ `eval.py:495` `chunks` is always `[]` ⇒ every eval turn is
  excluded as `no_retrieval`. D1/P2's untruncated-chunk capture (closed as `2.13`) is inert.

**D-C — stacked underneath D-B, and it would survive fixing D-B alone.** `agent.py:940` reads
`getattr(block, "name", "unknown")`, but `ToolResultBlock` declares only `tool_use_id`, `content`,
`is_error` (`types.py:944-949`) — **no `name`**. So the emitted `tool_name` could only ever be
`"unknown"`, and `retrieval_eval.py:194`'s `payload.get("tool_name") == "retrieve"` filter could
never match. Fixing the message type without fixing this yields events that still join to nothing.

**D-D — fixture, blocking verification.** `test_red_team_rtx.py`'s `clean_tenant` binds
`from app.core.database import get_sync_db` at line 293, *above* its `with _control_db_redirected(...)`
at 296, so the patch never reaches the local name. Setup dies with `UnmappedInstanceError` on `None`.
This is the identical defect fixed in `test_ver01_adversarial_harness.py:960-977`, and `1.13b`
predicted it here ("the same fixture shape is used by `red_team_rtx` and `ver01` — unverified").
Now verified. `test_aud03_audit_gap.py:586` already binds inside; `test_act07_resolve_live.py` does
not import it there. This is the last instance.

## Approach

Correct shape for both consumers: track `tool_use_id -> tool_name` from `ToolUseBlock`, then handle
`ToolResultBlock` **inside `UserMessage`** and resolve the name by `tool_use_id`. This fixes D-C by
construction (the name comes from the tool_use, the only block that carries one) and is robust to
parallel tool calls, which `red_team_probe`'s single `pending_skill` variable is not.

Keep reading `AssistantMessage` for `ToolResultBlock` too — it costs one `isinstance` and the parser
can still produce one. Dead-branch tolerance, not dead-branch reliance.

## Phases

1. **D-D** — port the ver01 binding fix + naming assert into `test_red_team_rtx.py`. Unblocks
   observing `5.8`/`5.9`.
2. **D-A/5.8** — run `test_identity_bypass`, observe. Fix the needle↔message mismatch
   (`tools.py:648` "Identity verification required or session expired" vs the needle
   "requires identity verification" at `red_team_probe.py:211`), and pin the two to each other so
   they cannot drift.
3. **D-B/D-C** — fix `agent.py`'s stream loop; tests over a fake stream shaped like the **observed**
   CLI (tool_result in a `UserMessage`), asserting the event, the audit capture and the chunk key.
4. **D-A** — fix `red_team_probe.py`'s loop, same shape.
5. Mutation-prove every guard: revert each fix, observe red, restore from `HEAD`, observe green.
6. Gates: unit suite; integration flag-ON for the touched modules.

## Files

- `apps/api/app/worker/tasks/runtime/agent.py` (D-B, D-C)
- `apps/api/app/services/red_team_probe.py` (D-A, `5.8` needle)
- `apps/api/app/services/transactional/tools.py` (`5.8`, if the message is the side that moves)
- `apps/api/tests/integration/test_red_team_rtx.py` (D-D)
- new/edited unit tests for the stream shape + the needle pin

## Risks

- **Behaviour change on the production turn path.** Turning on a channel that has always been empty
  means the Auditor starts receiving real context and `retrieval_eval` starts scoring real strings.
  Verdicts will move. That is the point, but it must be stated in the trace: **every grounding
  verdict recorded before this fix was computed against an empty context.**
- `retrieved_context_json` is capped at 3 × 600 chars and reaches a jsonb column — unchanged here,
  but it stops being trivially small.
- Cost is unchanged: no new model calls; the eval's exclusions shrink.

## Tests

- Stream-shape test: `UserMessage(content=[ToolResultBlock(...)])` after
  `AssistantMessage(content=[ToolUseBlock(id=…, name="mcp__customer-tools__retrieve")])` ⇒
  `agent.tool_result` emitted with `tool_name == "retrieve"`, `tc["result"]` set,
  `RETRIEVE_CHUNKS_KEY` populated.
- Regression pin for D-C: assert the emitted `tool_name` is never `"unknown"` for a matched id.
- Needle pin: the dispatcher's IDV message and `_VERDICT_PATTERNS`' needle asserted against each
  other, both branches (no token, invalid token).
- A guard that the branch cannot silently die again: assert `UserMessage` is handled.
