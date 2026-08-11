# TRACE — the ToolResultBlock dead branch

**Date:** 2026-08-11 · **Branch:** `chore/local-postgres` · **Commit:** `dc67d37`
**Plan:** `.dev/plans/260811-tool-result-dead-branch.md` ·
**Proofs:** `.dev/reference/260811-tool-result-mutation-proofs.md`

Started as HANDOFF next-move #4: settle `5.9` statically before authorising ~$0.12, and confirm
`5.8` for free. Both settled. `5.9` was **larger than filed**.

## What actually changed

`git diff --stat 4621cdd..dc67d37 -- apps/api/app/` — three product files:

| File | Change |
|---|---|
| `worker/tasks/runtime/agent.py` | tool results read from `UserMessage`; tool name resolved by `tool_use_id` |
| `services/red_team_probe.py` | same two fixes; IDV needles derived rather than copied |
| `services/transactional/tools.py` | the three IDV texts become constants (no behaviour change) |

## `5.9` — confirmed, and it was on the production path too

**As filed:** `red_team_probe.py:349` collects `ToolResultBlock` only inside `AssistantMessage`,
the CLI emits tool results as `type:"user"`, so the branch is dead and `test_confused_deputy` is a
vacuous pass. **Confirmed** — with the branch removed the transcript is `'\n'`, zero `skill=` lines,
and RTX-01's assertions all iterate that empty list (M3).

**Not filed anywhere: `agent.py:915,939` has the identical dead branch on `run_agent_turn`.** Three
readers were reading a channel nothing ever wrote:

- `agent.tool_result` job_events never emitted ⇒ `retrieval_eval._fetch_turn_context` always built
  `retrieve_contexts == []`.
- `tc["result"]` never set ⇒ `agent.py:1420`'s `retrieve_results` always `[]` ⇒ **the Auditor, the
  grounding judge, was handed `retrieved_context_json == "[]"` on every turn ever run.**
- `RETRIEVE_CHUNKS_KEY` never set ⇒ `eval.py:495` saw zero chunks ⇒ every eval row excluded as
  `no_retrieval`. D1/P2's untruncated-chunk capture — closed as `2.13` — was inert from the day it
  landed.

**And a second defect stacked underneath, which fixing the message type alone would NOT have
fixed.** The handler read `getattr(block, "name", "unknown")`, but `ToolResultBlock` declares only
`tool_use_id` / `content` / `is_error` (`types.py:944-949`). `"unknown"` was the only value it could
ever produce, and `retrieval_eval.py:194` joins on `tool_name == "retrieve"`. A reachable branch
would have emitted events that still joined to nothing. Both consumers now resolve the name by
joining `tool_use_id` back to the `ToolUseBlock` — which also fixes mis-attribution when the model
issues parallel tool calls, a case `red_team_probe`'s single `pending_skill` variable got wrong by
construction.

### The evidence, gathered before touching code and without spending anything

1. The SDK's **own** transcript readers treat `tool_result` as a user-entry phenomenon
   (`_internal/sessions.py:277-280`, `_internal/session_summary.py:81-92`).
2. **42,334** `tool_result`-carrying entries across **782** real CLI session transcripts on this
   machine: all `type:"user"`, **zero** assistant-carried.
3. The Messages API shape: `tool_result` is a user-turn content block.

`message_parser.py:148` can build one under `assistant`; both consumers still handle that —
tolerance, not reliance.

## `5.8` — confirmed by running it, and it was three messages, not two

Predicted statically, then observed: `AssertionError: assert 'succeeded' == 'identity_required'`,
with `tool_calls_audit.written has_error=True` on both attempts — **the product blocked correctly
and the probe labelled the block a successful attack.**

The row said the needle missed `tools.py:648`. It also missed `tools.py:612`
("Identity verification check failed") — the fail-closed path. Three messages, one needle, one match.
Fixed structurally: the texts are constants in `tools.py` and `_VERDICT_PATTERNS` derives its needles
from `IDV_BLOCK_MESSAGES`, so a message edited or added there moves the needle with it. An AST guard
(`test_every_idv_return_site_uses_a_pinned_constant`) refuses a fourth message added as an inline
literal, which is the shape the original defect had.

## Three things found only by running tests that had never run

1. **`test_red_team_rtx`'s `clean_tenant` had the ver01 binding bug.** `from app.core.database import
   get_sync_db` above `with _control_db_redirected(...)`, so the patch never reached the local name;
   setup died on `UnmappedInstanceError`. `1.13b` predicted this exact thing here and marked it
   unverified. Now verified and fixed — and it was the **last** instance (`aud03` already binds
   inside; `act07` does not import it there).
2. **`test_identity_bypass` does make a live model call**, contradicting the module docstring's
   "needs neither Redis nor the Anthropic API". Attempt 3 (a genuinely verified session) proceeds
   past step 2.5, and what sits immediately past step 2.5 is the Actor gate. It 401'd.
3. **`test_value_bound_evasion` too** — it verifies a session precisely so it can reach the rate
   layer, so every chained refund hits the Actor gate.

Both docstring claims were the same shape as VER-01's "every mutating call dies at the IDV gate":
a confident statement produced by a method that could not have checked it, on a test that had never
executed.

**Attempt 3 was split into its own test.** Gating it inline made the whole test skip without a key,
converting two real observed assertions into `1 skipped` — and a skip is unobserved, which is this
repo's own rule about its metrics and holds for its test suite.

## Observed

Targeted:

```
tests/unit/test_agent_tool_result_stream.py     12 passed          (new)
tests/unit/test_idv_message_verdict_pin.py       9 passed          (new)
tests/unit/test_red_team_probe.py               23 passed          (18 pre-existing + 5 new)
directly-related modules (5 files)             172 passed
integration test_red_team_rtx.py    1 passed, 3 skipped, 0 failed  (was: 1 error at setup)
```

Gates, run by the session at tip `5102ddf` and observed, not relayed:

```
unit                        2193 passed, 13 skipped, 0 failed   716.68s
                            grep -cE "^(FAILED|ERROR)" -> 0
integration (flag OFF)        15 passed, 47 skipped, 0 failed   281.05s
```

Unit is +26 on the 2167/13 baseline, every one a test added here. Flag-OFF's skips moved 22 → 47
because `red_team_rtx` is no longer deselected: those tests now skip under the OFF flag rather than
being excluded from collection. Same state, more honestly counted.

**The first full-gate run failed** — `test_agent_options_seam::test_agent_py_has_no_nested_function_definitions`,
1 failed / 2192 passed. Real, and fixed in `5102ddf` (see Deviations). The figures above are the
second run, on the shipped tree.

Integration flag-ON, whole directory, nothing deselected:

```
33 passed, 24 skipped, 5 failed in 572.21s
```

**5 failed, none of them this change.** Not comparable to the morning's `28/5/1` either — that run
collected 34 tests, this one 62. Attribution: `5.6` (owner decision, known), `ver01` (needs
`ANTHROPIC_API_KEY` in `os.environ`), and three that had **never executed before** — filed as `1.15`
(a test stale against D1/P3's evidence gate) and `1.16` ×2 (a fixture inserting `NULL` into a
`NOT NULL` column). The bottom three were **proved** pre-existing, not assumed:

```
git checkout 4621cdd -- apps/api/app/   # product code only, tests unchanged
3 failed in 36.58s                       # identical set
git checkout HEAD -- apps/api/app/       # clean restore, git status empty
```

**That run also found a live product defect unrelated to this work — `1.14`.** Every
`run_deployment_checklist` logs `blast_radius_fetch_failed … syntax error at or near ":"`:
`deployment_service.py:1237`/`:1253` write `(:window_days::text || ' days')::interval`, SQLAlchemy
leaves the bindparam unbound where `::` abuts it (the error's `[parameters: …]` shows `window_days`
absent), and the caller catches and falls back. Every `configured_max_*` / `observed_max_*` in the
blast-radius payload is `None` on every run while the thresholds beside them populate from settings.
Sixth instance of the `:name::type` class `1.1` records, and the first in production code. **Filed,
not fixed** — it is outside this change's scope, it needs its own test against a real DB (the
existing ones mock the session, which is why five phases missed it), and it sits on the deploy gate's
threat surface. Same family as `5.13`: a fail-soft `except` turning a permanently broken statement
into a plausible empty state.

Six mutation proofs, all red first time, all restored green — full verbatim output in the reference
file.

## Deviations from the plan

- The plan expected to change `tools.py`'s IDV *message* or the needle. It changed neither
  semantically: the messages are byte-identical, only their storage moved to constants. Customer-
  facing prose is a product surface; the matcher is the thing that was wrong.
- The plan did not anticipate the parallel-tool-call mis-attribution in `red_team_probe`. Fixed with
  the same `tool_use_id` join, since it was free once the map existed.
- A harness defect cost one debugging cycle: `_sdk_blocks()` re-imported per call, so tests compared
  instances of one class object against `isinstance` of another and collected nothing — manufacturing
  the exact symptom under test. Now cached, with the reason written at the definition.
- **The handler was written nested inside `_run_sdk_turn` and had to be lifted** (`5102ddf`).
  `agent.py` forbids nested `def`s — the static seam guards attribute calls to the module-scope
  function containing them, so a nested def can hide a second `ClaudeAgentOptions` construction.
  Caught by the full gate, not by review and not by the touched modules: the guard lives in
  `test_agent_options_seam.py`, which this change does not touch and which no "related modules"
  selection would have picked up. M1 and M2 were re-proved against the lifted shape rather than
  carried over.
- While lifting, the `getattr(..., default)` reads became direct attribute access. `ToolResultBlock`
  declares `tool_use_id` and `content`, so a shape mismatch should raise rather than quietly produce
  a plausible value — which is the second standing rule of the retro family this change adds, applied
  to itself.

## What is still unproven, and what it would cost

Every test here constructs SDK dataclasses directly, so they verify the **loop** against the observed
shape — never that the CLI emits that shape. The three evidence lines carry that claim. One live turn
(`test_confused_deputy`, ~$0.12) closes it, and it is **now worth spending**, which it was not before:
before the fix it could only have bought a vacuous pass.

## How long it has been true

`git log -S "elif isinstance(block, ToolResultBlock):" -- .../agent.py` returns **one** commit:
`2b38648`, **2026-05-16** — "feat(04-03): add run_agent_turn Celery task". The branch was born dead
and has never executed once, across ~3 months, 23 phases, a seven-defect measurement audit and two
tier-2 judgements. It is live on `main` (`57be16b`) as of this writing, which is what makes it a
regression-policy case rather than a branch-local slip — hence the `retro.md` entry (Family I).

## Consequence the owner should know about

Every grounding verdict this platform has ever recorded was computed against an **empty** retrieved
context, and every eval row was excluded as `no_retrieval`. Turning the channel on means Auditor
verdicts and eval scores will move. Nothing historical is trustworthy as a baseline — which is
consistent with `3.6`'s "it is a scorer, not yet an eval" and with the D1 tier-2 read that the
pipeline "has never measured anything".
