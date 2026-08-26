# ToolResult with the outcome enum (#45)

Ticket #45, decision #7 on map #4. On `feat/tool-result` off main at the #43 merge:
the type through the dispatcher (`461bc82`), the review round with the glossary line
(`913880b`). Runs parallel to PR #71 (#44); no shared files.

## The type and the mapping

`app/domain/tool_result.py`: frozen `ToolResult(skill, outcome, text, stored_wire)`
with `Outcome` an enum `ok | denied | requires_human | error`; `is_error` is derived
and `to_wire` is the one edge building the SDK `{"content", "is_error"}` dict, pinned
byte-identical per branch against literals captured at the pre-change commit.
`stored_wire` exists for the replay branch, whose stored bytes are arbitrary tenant
JSON that must return identically.

Every dispatcher return site is typed (16 in `_execute_transactional_tool`, 3 in
`_execute_adapter_and_audit`). Denied means the system said no; error means something
broke. After review the split is seven denied, five error: `in_progress` (a concurrent
duplicate is a refusal) and `args_mismatch` (the code's own security-relevant
rejection) moved from error to denied, with no wire movement since both sit on
`is_error` true. The one live `require_human` arm is `requires_human`; the
recorded-mode not-executed sites are denied because their wire has always said
`is_error` true. Every failure branch carries a one-line why note, and
`tests/unit/test_dispatcher_outcomes.py` drives every branch so a flipped mapping goes
red; the two remaps were its first observed reds.

`run_transactional_skill` is the shared validate-then-dispatch entry (six
ValidationError copies collapsed; unknown skills raise the named `UnknownSkillError`).
The probe consumes the type on every path; the `confirm_action` path was the review
catch, still wire-parsing so routed-to-approval tagged as succeeded. It now reads the
outcome, and `awaiting human approval` joined the needles for the wire-only victim
turn. `ProbeToolResult` survives as a subclass holding `verdict_tag`, which reads a
services constant the domain rung cannot import.

## Facts sent elsewhere

- #52 (comment): the probe's needle-based verdict_tag credits the identity gate when
  an IDV check raises; the rebuilt Attacker should read outcomes, not needles.
- #72: the 14 transactional schema models are safe to freeze; #45's AST pass found
  zero real mutations and zero `model_copy` calls.
- #73: the confirmation resolver reports "denied" to the owner for adapter errors;
  `_execute_adapter_and_audit` never returns denied, so the label discards the type
  information #45 created.

## Evidence, observed

- The defect test documents the old indistinguishability: ok and requires_human shared
  the key set `{"content"}` with no `is_error` on either.
- Red-first at every stage, including `4 failed, 21 passed` on the outcome table with
  exactly the two remapped branches red, and the probe collapse observed as `an
  approver was asked and the probe recorded ok` before the fix.
- `full gates passed in 718.7s.` at `461bc82` and `632.2s.` at the review round; suite
  `2492 passed` then collection `2531`.
- Baselines only fell: five lowerings at the type commit, then `confirm_action_tool`
  split under the standard and its pin deleted on found gone (121 pinned functions
  remain). Two source-slicing tests deleted per the resolution's own prediction, their
  claims held by behaviour tests beside them.
- `Outcome` entered CONTEXT.md under Transactional; its avoid list reserves "verdict"
  for the Harness.
