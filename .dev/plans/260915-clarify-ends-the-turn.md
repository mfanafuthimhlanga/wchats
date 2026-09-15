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

`_run_tool_call` returns that verdict, `_run_tool_calls` carries the first True out of one
reply's calls, and `run_agent_loop` breaks with `stop_reason = CLARIFIED`. Three properties
ride on the shape.

- **The truncation.** `response_parts` becomes the question alone, so prose the same reply
  carried ahead of the call is dropped and the served text satisfies `is_clarifying_question`,
  the rule the scenario writer already holds an owner's reference to.
- **An error wire ends nothing.** `clarify_tool` raises KeyError when the model sends no
  `question` and `dispatch` turns that into an error wire. This is the rule `_note_escalation`
  holds one function above, for the same reason.
- **The firewall still runs.** The break leaves through `_turn_result`, which scans whatever
  `response_parts` holds, so a question quoting a customer's address is deflected exactly as
  an answer quoting it is.

The loop and not the prompt, because a prompt sentence cannot be mutated and observed to go
red. `clarify_tool`'s docstring already claimed "the clarifying question as the agent's
response text"; the loop is what now makes that true.

## What moved

| File | Change |
|---|---|
| `app/services/agent_loop.py` | `CLARIFIED`, `_serve_the_clarifying_question`, `_run_tool_calls`, `_run_tool_call` returns bool |
| `tests/agent_loop_doubles.py` | `clarified` joins `STOP_REASONS` |
| `tests/unit/test_agent_loop.py` | `TestClarifyEndsTheTurn`, 10 tests; a sixth ending in the vocabulary test |
| `tests/unit/test_turn_budget_ceiling.py` | the worst-case script calls `lookup_structured` where it called `clarify` |

The budget script needed the swap. It burned model calls on `clarify` to reach the six-call
worst case, and a clarify call now ends the turn on its second call, which prices the wrong
turn.

## Mutations

Each mutation applied to `app/services/agent_loop.py`, observed red, restored from the text it
replaced, and the file's sha256 checked against `5de2931478667628f01497e1cdd762c6c8b64f337371a01c3b799871636fd426`
after every restore. Command in all three cases:

```
.venv/Scripts/python.exe -m pytest tests/unit/test_agent_loop.py -q -p no:randomly
```

| # | Mutation | Observed |
|---|---|---|
| a1 | `_serve_the_clarifying_question` returns False always, `response_parts` untouched | 9 failed, 86 passed |
| a2 | the stop kept, `state.response_parts = [text]` removed | 7 failed, 88 passed |
| b | `name != CLARIFY_TOOL` dropped, so every tool that ran ends the turn | 16 failed, 79 passed |

### a1, the fix removed entirely

```
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_turn_that_asked_then_answered_serves_the_question
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_served_text_of_an_asking_turn_passes_the_text_rule
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_tool_log_ends_on_clarify_so_the_row_reads_as_asked
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_turn_whose_only_call_is_clarify_serves_exactly_the_question
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_prose_in_the_same_reply_as_the_clarify_call_is_not_served
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_tool_after_clarify_in_the_same_reply_never_runs
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_retrieve_before_the_clarify_is_kept_and_still_asks
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_firewall_still_scans_a_question_that_ends_the_turn
FAILED tests/unit/test_agent_loop.py::TestTheStopReasonVocabulary::test_every_ending_records_a_word_the_doubles_know
9 failed, 86 passed in 16.57s
```

This is the repro. The first line is issue #280 itself, and its message carries the served
text.

Nine of the class's ten tests go red. The two that hold are the controls,
`test_a_retrieving_turn_is_untouched` and `test_a_clarify_that_did_not_run_ends_nothing`,
because neither is about clarify ending a turn.

### a2, the stop without the truncation

```
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_turn_that_asked_then_answered_serves_the_question
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_served_text_of_an_asking_turn_passes_the_text_rule
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_turn_whose_only_call_is_clarify_serves_exactly_the_question
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_prose_in_the_same_reply_as_the_clarify_call_is_not_served
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_tool_after_clarify_in_the_same_reply_never_runs
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_retrieve_before_the_clarify_is_kept_and_still_asks
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_the_firewall_still_scans_a_question_that_ends_the_turn
7 failed, 88 passed in 21.36s
```

The stop alone serves an empty string on a reply that carried no text of its own. The
truncation is what puts the question in front of the customer, and this says so.
`test_the_tool_log_ends_on_clarify_so_the_row_reads_as_asked` survives, because the stop is
the half that rule reads.

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
FAILED tests/unit/test_agent_loop.py::TestTheEventWritesLeaveTheEventLoop::test_no_control_db_commit_runs_on_the_turns_thread
FAILED tests/unit/test_agent_loop.py::TestTheEventWritesLeaveTheEventLoop::test_the_redis_publish_stays_on_the_turns_thread
16 failed, 79 passed in 15.60s
```

The control, on its own:

```
        out, _ = await _drive(
            _turn(client, tools=[_tool("retrieve", _echo_handler)])
        )

>       assert out["response_text"] == answer
E       AssertionError: assert 'echo returns' == 'Fourteen days, unopened.'
E
E         - Fourteen days, unopened.
E         + echo returns

tests\unit\test_agent_loop.py:1494: AssertionError
FAILED tests/unit/test_agent_loop.py::TestClarifyEndsTheTurn::test_a_retrieving_turn_is_untouched
1 failed, 94 deselected in 16.56s
```

A retrieving turn serves the chunk instead of the answer, which is what a rule reaching past
`clarify` costs. Fourteen tests outside the new class go red with it, so the blast radius of a
wrong tool name is visible rather than inferred.
