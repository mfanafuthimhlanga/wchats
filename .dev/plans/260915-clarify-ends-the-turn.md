# Clarify ends the turn

On eval run 735fb9fa the agent called `clarify` on all ten ambiguous openers, which is the
behaviour #255 changed the prompt to get. On five of them it went on in the same turn,
retrieved, and answered about a project the customer had never named. The customer read an
answer to a question they did not ask, and ADR 0012 reads the turn's LAST tool call as the
evidence that it asked, so the trailing `retrieve` made each row a `golden_failure`.

Issue: #280. Related: #255, #226, ADR 0012.

## The cause, in one sentence

`run_agent_loop` had no rule about `clarify`, so the question went back to the model as an
ordinary tool result and the model spent its remaining calls answering it itself.

## The fix

`app/services/agent_loop.py`. A `clarify` call that ran is the whole reply, so the turn
serves that question and stops.

```python
def _serve_the_clarifying_question(state, name, wire, text) -> bool:
    if name != CLARIFY_TOOL or wire.get("is_error"):
        return False
    state.response_parts = [text]
    return True
```

`_run_tool_call` returns that verdict, `_run_tool_calls` runs the whole batch and carries it
out, and `run_agent_loop` breaks with `stop_reason = CLARIFIED`. Six properties ride on the
shape.

- **The replacement.** The line REPLACES `response_parts` entirely, so every earlier model
  call's prose goes with anything this reply carried ahead of the call. The served text is
  the question and nothing else.
- **Every call in the batch still runs, and clarify runs last.** Returning on the first
  `clarify` cancelled an `escalate_to_human` beside it, so the conversation carried no
  marker, no mail left the building, and `escalated` came back False while the customer read
  a question. A stable sort moves the clarify to the end, which is where ADR 0012's rule
  looks for it.
- **An error wire ends nothing.** `clarify_tool` error-wires a `question` that is missing,
  blank, or not a string. This is the rule `_note_escalation` holds one function above, for
  the same reason.
- **Blank text ends nothing either.** `not text.strip()` in the loop, because the loop owns
  the served text and `clarify_tool` is one producer of the wire it reads. An empty bubble is
  also a row the eval drops.
- **The firewall still runs, on the narrowest allowlist there is.** The break leaves through
  `_turn_result`. A CLARIFIED turn skips `published_context`, so a `retrieve` in the same
  batch does not carry the tenant's published-contact exemption into a reply that is only a
  question.
- **The four validators do not judge it.** `_dispatch_validation_chain` returns None on
  CLARIFIED. They ask whether an ANSWER was grounded, safe and on strategy; a flagged
  question becomes a `bench_service` failing trace, a mined "unanswered" scenario, and at
  three a day a retrieval-strategy change.

The loop and not the prompt, because a prompt sentence cannot be mutated and observed to go
red. The prompt and the tool description changed too, and they carry what the model cannot
infer, that the question text is the whole customer-visible reply and a candidate list goes
inside it.

## What moved

| File | Change |
|---|---|
| `app/services/agent_loop.py` | `CLARIFIED`, `_serve_the_clarifying_question`, `_run_tool_calls`, `_run_tool_call` returns bool, `_turn_result` skips the allowlist |
| `app/services/agent_tools.py` | `clarify_tool` validates `question`; the description says the question is the whole reply |
| `app/services/agent_prompt.py` | a MUST line saying a clarify call ends the turn |
| `app/services/clarifying_check.py` | the docstrings no longer say the loop runs past clarify |
| `app/worker/tasks/runtime/agent.py` | `_dispatch_validation_chain` takes `stop_reason` and returns `str | None` |
| `tests/agent_loop_doubles.py` | `clarified` joins `STOP_REASONS` |
| `tests/unit/test_agent_loop.py` | `TestClarifyEndsTheTurn`, 14 tests; a sixth ending in the vocabulary test |
| `tests/unit/test_agent_tools.py` | five tests on the `question` argument and the description |
| `tests/unit/test_agent_prompt.py` | the new MUST line, pinned |
| `tests/unit/test_agent_task.py` | the chord skip, driven through the task so the wiring is covered |
| `tests/unit/test_judge_sees_agent_context.py` | the chord skip at the seam |
| `tests/unit/test_clarifying_check.py` | a stored row the live loop can no longer write |
| `tests/unit/test_turn_budget_ceiling.py` | the worst-case script calls `lookup_structured` where it called `clarify` |

The budget script needed the swap. It burned model calls on `clarify` to reach the six-call
worst case, and a clarify call now ends the turn on its second call, which prices the wrong
turn.

## What the loop does NOT do

It puts no bound on the served text. `is_clarifying_question` is the rule the scenario WRITER
applies to an owner's reference answer, and the eval applies to nothing else. The loop serves
whatever the model wrote inside `question`, so a model that writes sixty words ending in a
full stop serves that and the row fails. A bound in the loop would be the product asserting
its own score, so the prompt and the tool description are where the shape is asked for.

`turn_asked_to_clarify`'s False branch is no longer reachable from the live loop, which ends a
turn on a successful clarify and sorts a clarify to the end of its own batch. The rule still
reads the log, because it also runs over rows written before #280, mined production traces,
replayed conversations and red-team transcripts.

## Risks this leaves open

- **A non-ambiguous scenario whose agent clarifies leaves the scored set.** `_measured_row`
  returns None for a responded turn that retrieved nothing and is not marked `ambiguous`, so
  a clarify on one of the ten control follow-ups is reported as `no_retrieval` rather than as
  the false clarification it is. `.dev/reference/260913-evals-clarify-openers.md` already
  names that rule as the next one to add.
- **"At most twice per conversation" is prompt text and nothing enforces it.** The tool
  description asks for it, no counter checks it, and a model that clarifies on every turn
  loops the customer with no ceiling but `MAX_MODEL_CALLS_PER_TURN`, which bounds one turn
  rather than the conversation.
- **The clarify question is not held to the rule the eval scores it by.** Stated above, and
  it is the reason an ambiguous row can still fail with a clarify call plainly in its log.

## Mutations

Eight, each one line or one block, each applied to a file, observed red, restored from the
text it replaced, and the file's sha256 checked after every restore.

```
app/services/agent_loop.py          968d61f3a2a55d2d2b4aabc0866c7d18fd352eaa8159f8dc67b66fce93abbf90
app/services/agent_tools.py         0d21b34979d6549fb0b97d9d1596282ecd037fab592e50665236424f3995d0e1
app/worker/tasks/runtime/agent.py   c7468357168d4e3a718e33705fcf62e43cb9fc609e0628f0f29f1d6a69107788
```

Clean, for reading the numbers below against: `tests/unit/test_agent_loop.py` is 99 passed,
`test_agent_tools.py` 33, and `test_agent_task.py` with `test_judge_sees_agent_context.py`
47 between them.

| # | Mutation | Suite | Observed |
|---|---|---|---|
| a1 | `_serve_the_clarifying_question` always returns False | test_agent_loop | 12 failed, 87 passed |
| a2 | the stop kept, `state.response_parts = [text]` removed | test_agent_loop | 9 failed, 90 passed |
| b | `name != CLARIFY_TOOL` dropped, so every tool that ran ends the turn | test_agent_loop | 14 failed, 85 passed |
| c | `_run_tool_calls` returns on the first call that ended the turn | test_agent_loop | 3 failed, 96 passed |
| d | the clarify-last `sorted` key dropped, batch runs in the model's order | test_agent_loop | 3 failed, 96 passed |
| e | `not text.strip()` dropped from the guard | test_agent_loop | 1 failed, 98 passed |
| f | the `stop_reason == CLARIFIED` skip removed from `_dispatch_validation_chain` | test_agent_task + test_judge_sees_agent_context | 2 failed, 45 passed |
| g | `_turn_result` builds the allowlist on a clarified turn too | test_agent_loop | 1 failed, 98 passed |
| h | `clarify_tool` validates nothing | test_agent_tools | 3 failed, 30 passed |

### a1, the fix removed entirely

```
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_turn_that_asked_then_answered_serves_the_question
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_served_text_of_an_asking_turn_passes_the_text_rule
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_tool_log_ends_on_clarify_so_the_row_reads_as_asked
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_turn_whose_only_call_is_clarify_serves_exactly_the_question
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_prose_in_the_same_reply_as_the_clarify_call_is_not_served
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_clarify_beside_an_escalation_escalates_and_still_asks
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_clarify_beside_a_confirmation_runs_the_confirmation
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_batch_keeps_the_order_the_model_wrote_around_the_clarify
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_clarified_turn_widens_no_pii_allowlist
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_retrieve_before_the_clarify_is_kept_and_still_asks
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_firewall_still_scans_a_question_that_ends_the_turn
FAILED tests/unit/test_agent_loop.py::TestTheStopReasonVocabulary::test_every_ending_records_a_word_the_doubles_know
12 failed, 87 passed in 15.73s
```

Eleven of the class's fourteen tests, plus the vocabulary test from another class. The three
that hold are the controls, `test_a_retrieving_turn_is_untouched`,
`test_a_clarify_that_did_not_run_ends_nothing` and `test_a_blank_question_ends_nothing`, each
of which is about a turn that does NOT end on a question.

### a2, the stop without the replacement

```
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_turn_that_asked_then_answered_serves_the_question
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_served_text_of_an_asking_turn_passes_the_text_rule
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_turn_whose_only_call_is_clarify_serves_exactly_the_question
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_prose_in_the_same_reply_as_the_clarify_call_is_not_served
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_clarify_beside_an_escalation_escalates_and_still_asks
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_clarify_beside_a_confirmation_runs_the_confirmation
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_clarified_turn_widens_no_pii_allowlist
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_retrieve_before_the_clarify_is_kept_and_still_asks
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_firewall_still_scans_a_question_that_ends_the_turn
9 failed, 90 passed in 15.62s
```

The stop alone serves an empty string on a reply that carried no text of its own. The
replacement is what puts the question in front of the customer.
`test_the_tool_log_ends_on_clarify_so_the_row_reads_as_asked` and the vocabulary test survive,
because the stop is the half those two read.

### b, every tool ends the turn

```
FAILED tests/unit/test_agent_loop.py::TestOneModelCall::test_the_text_of_earlier_calls_survives_a_choiceless_reply
FAILED tests/unit/test_agent_loop.py::TestTheToolRoundTrip::test_the_loop_reaches_a_final_answer
FAILED tests/unit/test_agent_loop.py::TestTheToolRoundTrip::test_the_assistant_turn_is_replayed_with_its_tool_calls
FAILED tests/unit/test_agent_loop.py::TestTheToolRoundTrip::test_the_tool_message_carries_the_call_id_and_the_text
FAILED tests/unit/test_agent_loop.py::TestTheToolRoundTrip::test_two_tool_calls_in_one_reply_run_in_order
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_retrieve_before_the_clarify_is_kept_and_still_asks
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_retrieving_turn_is_untouched
FAILED tests/unit/test_agent_loop.py::TestTheCeilings::test_the_loop_stops_at_max_model_calls
FAILED tests/unit/test_agent_loop.py::TestTheCeilings::test_spend_over_the_ceiling_stops_the_turn
FAILED tests/unit/test_agent_loop.py::TestTheCeilings::test_spend_under_the_ceiling_runs_the_turn_out
FAILED tests/unit/test_agent_loop.py::TestTheCeilings::test_an_unpriced_call_counts_against_the_ceiling_and_stops_the_turn
FAILED tests/unit/test_agent_loop.py::TestTheCeilings::test_an_unpriced_call_under_the_ceiling_still_runs_the_turn_out
FAILED tests/unit/test_agent_loop.py::TestTheCeilings::test_the_log_line_says_the_charge_was_substituted
FAILED tests/unit/test_agent_loop.py::TestTheStopReasonVocabulary::test_every_ending_records_a_word_the_doubles_know
14 failed, 85 passed in 15.64s
```

Twelve tests outside the new class go red, so the blast radius of a rule reaching past
`clarify` is visible rather than inferred. Both ceilings stop reporting their own endings,
because the first retrieve now ends the turn.

### c, the defect this review found

```
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_clarify_beside_an_escalation_escalates_and_still_asks
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_clarify_beside_a_confirmation_runs_the_confirmation
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_batch_keeps_the_order_the_model_wrote_around_the_clarify
3 failed, 96 passed in 12.91s
```

The first version of this fix returned on the first call that ended the turn, and the
escalation beside it never ran. The message on the first line names it.

```
E       AssertionError: the escalation was cancelled by the clarify beside it, so nothing
E       marked the conversation and no mail left the building
```

### d, the clarify no longer sorts to the end

```
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_clarify_beside_an_escalation_escalates_and_still_asks
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_clarify_beside_a_confirmation_runs_the_confirmation
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_batch_keeps_the_order_the_model_wrote_around_the_clarify
3 failed, 96 passed in 13.09s
```

The same three, for the other half. Every call runs, and the tool log ends on the escalation
rather than on the clarify, so `turn_asked_to_clarify` reads the row as a turn that answered.

### e, a blank question ends the turn

```
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_blank_question_ends_nothing
1 failed, 98 passed in 12.96s
```

```
E         + clarified
```

The turn ended with `stop_reason` CLARIFIED on whitespace, and the customer reads an empty
bubble. In the shipped path `clarify_tool` refuses that argument first, so this guard is the
loop's own and fires on any other producer of a blank wire.

### f, the validators judge a clarifying question

```
FAILED tests/unit/test_agent_task.py::test_a_clarified_turn_dispatches_no_validator_chain
FAILED tests/unit/test_judge_sees_agent_context.py::TestAClarifiedTurnIsNotJudged::test_a_clarified_turn_dispatches_nothing
2 failed, 45 passed, 16 warnings in 17.90s
```

Both halves go red together, which is the point of driving the seam and the task separately.
The task's test would pass on a seam that skipped correctly but was handed no `stop_reason`;
the seam's test would pass on a task that never sent one.

### g, the allowlist widens for a question

```
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_clarified_turn_widens_no_pii_allowlist
1 failed, 98 passed in 15.73s
```

```
E       assert 1 == 0
```

One published chunk reached the firewall's exemption list for a reply that quotes nothing.

### h, `clarify_tool` validates nothing

```
FAILED tests/unit/test_agent_tools.py::test_clarify_error_wires_a_question_that_is_not_a_string
FAILED tests/unit/test_agent_tools.py::test_clarify_error_wires_a_blank_question
FAILED tests/unit/test_agent_tools.py::test_clarify_error_wires_a_missing_question
3 failed, 30 passed in 13.09s
```

```
E       KeyError: 'is_error'
```

The wire came back servable. In the live loop `{"question": 3}` then reaches `wire_text`,
whose `str.join` raises TypeError out of `_run_tool_call`, where nothing catches it, so the
customer turn dies.
