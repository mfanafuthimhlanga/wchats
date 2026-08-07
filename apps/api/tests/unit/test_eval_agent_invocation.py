"""D1 is closed: the eval invokes the agent, and says what that produced.

Why this file exists
--------------------
`.dev/reference/measurement-layer-audit.md` D1: `eval.py` built every Ragas
sample with

    # For M6: use reference_answer as proxy agent_response to test the eval harness
    "agent_response": row[3],     # row[3] IS reference_answer

so the label was the prediction. Faithfulness and AnswerRelevancy approached 1.0
by construction, the score was invariant to the agent's model, prompt, retrieval
configuration and capability envelope, and everything built on top of it — the
configuration tuple, the deploy gate's eval half — was reasoning about a number
that measured nothing.

`.dev/plans/260807-d1-agent-invocation.md` P2 replaces that line with a real
turn. This file pins the five ways that fix could itself be wrong and be
invisible:

  1. **The eval path asks for `side_effects="recorded"`, always.** The seam
     grants eleven tools and six of them reach a real ProviderAdapter. One eval
     scenario in which the agent decides to refund executes a refund against the
     tenant's provider, nightly, unattended. Pinned statically (every call site
     in `eval.py`) and dynamically (the kwargs the seam actually receives).
  2. **A scenario whose agent call fails is EXCLUDED AND COUNTED, never scored
     0.** Zero is not a low score, it is the absence of one — the lesson
     `tests/evals/calibration/compute_correlation.py:485` already learned about a
     judge that errors.
  3. **A run where too few scenarios answered reports `unknown`, never `pass`.**
     Same `MIN_PAIR_RATE` shape, same argument: a metric over the rows that
     happened to succeed is not a measurement of the set that was scored.
  4. **THE D1 REGRESSION PIN** — `agent_response` is never the reference answer
     for a scored row, statically (no dict in `eval.py` can build one from the
     label) and dynamically (the samples that reach `run_ragas_eval`).
  5. **`retrieved_contexts` come from the AGENT's own retrieve result**, not the
     scenario's stored column. Scoring faithfulness against contexts the agent
     never saw is D1 in a different costume, and it would leave every number
     looking healthy.

No live PostgreSQL and no Agent SDK subprocess exist here, so the SDK turn is a
double at exactly one boundary — `_run_one_eval_turn` for the loop tests, and
`agent.build_agent_options` / `agent._run_sdk_turn` for the turn test. Nothing
in this file observes a real eval end to end; that is integration territory and
it SKIPS on this machine, which is unobserved, never a pass.
"""

from __future__ import annotations

import ast
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services import eval_service
from app.worker.tasks.runtime import eval as mod

_EVAL_PY = Path(mod.__file__).with_suffix(".py")

PRODUCTION = "postgresql://production/tenant"
BRANCH = "postgresql://neon-branch/tenant"

#: The seam every caller of the customer agent must go through (P1).
SEAM = "build_agent_options"


# ---------------------------------------------------------------------------
# Static half — read eval.py's own source
# ---------------------------------------------------------------------------


def _eval_tree() -> ast.Module:
    return ast.parse(_EVAL_PY.read_text(encoding="utf-8"))


def _calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            found.append(node)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            found.append(node)
    return found


def _dicts_with_key(tree: ast.AST, key: str) -> list[ast.Dict]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and any(
            isinstance(k, ast.Constant) and k.value == key for k in node.keys
        )
    ]


def test_the_eval_path_never_asks_for_live_side_effects():
    """THE ONE THAT STOPS AN EVAL MOVING MONEY.

    The seam takes `side_effects` as a MANDATORY parameter with no default
    precisely so this cannot be forgotten (BACKLOG 2.5) — but "mandatory" only
    forces a value to be supplied, not the right one. `side_effects="live"` here
    compiles, passes every other test in the suite, and executes a real refund
    the first night an eval scenario talks the agent into one. The agent cannot
    tell the two modes apart: same eleven tools, same system prompt, same
    capability envelope, same Actor gate. Nothing at runtime would raise.

    So it is pinned at the source, at every call site, requiring a literal — a
    variable would satisfy "a value was supplied" while carrying "live".
    """
    calls = _calls_named(_eval_tree(), SEAM)
    assert calls, (
        f"no {SEAM}(...) call site found in eval.py. Either the eval stopped "
        "invoking the agent (D1 is back) or it reaches the agent by a route "
        "this guard cannot see — both are worse than the thing it guards."
    )

    offences: list[str] = []
    for call in calls:
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        node = keywords.get("side_effects")
        if node is None:
            offences.append(f"line {call.lineno}: no side_effects= argument")
        elif not isinstance(node, ast.Constant):
            offences.append(
                f"line {call.lineno}: side_effects={ast.unparse(node)} is computed, "
                "so this guard cannot tell whether it is 'live'"
            )
        elif node.value != "recorded":
            offences.append(f"line {call.lineno}: side_effects={node.value!r}")

    assert offences == [], (
        f"the eval path asks the seam for side effects it must never have: "
        f"{offences}. Six of the eleven tools the seam grants reach a real "
        "ProviderAdapter — place_order, cancel_order, issue_refund, "
        "update_subscription, book_slot, update_customer_record — and this loop "
        "runs nightly against a real tenant with nobody watching."
    )


def test_no_scenario_dict_in_the_eval_can_carry_the_label_as_the_prediction():
    """THE D1 REGRESSION PIN, at the source.

    The defect was one key in one dict literal:

        "reference_answer": row[3],
        ...
        "agent_response": row[3],

    Two properties, because the second catches the spellings the first does not.
    A dict that builds both keys is the original shape; an `agent_response` whose
    VALUE mentions `reference_answer` at all is every rephrasing of it
    (`s["reference_answer"]`, `scenario.get("reference_answer", "")`, a fallback
    `response or reference_answer`).

    Deliberately an AST read rather than a regex: this module's own prose quotes
    the defective line verbatim, and the regex version of this test matched the
    comment.
    """
    tree = _eval_tree()

    both = [
        node
        for node in _dicts_with_key(tree, "agent_response")
        if any(
            isinstance(k, ast.Constant) and k.value == "reference_answer"
            for k in node.keys
        )
    ]
    assert both == [], (
        "eval.py builds a dict carrying both 'reference_answer' and "
        f"'agent_response' (line(s) {[n.lineno for n in both]}). That is the "
        "shape D1 had: the label and the prediction assembled in one place, "
        "from the same row, and every metric computed over them is a measure of "
        "the harness rather than of the agent."
    )

    leaks: list[str] = []
    for node in _dicts_with_key(tree, "agent_response"):
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "agent_response"):
                continue
            rendered = ast.unparse(value)
            if "reference_answer" in rendered:
                leaks.append(f"line {node.lineno}: agent_response = {rendered}")
    assert leaks == [], (
        f"eval.py derives the agent's response from the reference answer: "
        f"{leaks}. Faithfulness and AnswerRelevancy then approach 1.0 by "
        "construction and no change to the agent can move them."
    )


def test_the_stored_context_column_is_not_named_what_the_scorer_reads():
    """The other half of D1, and the one that leaves every number looking fine.

    `run_ragas_eval` reads `retrieved_contexts` off each sample. The scenario
    row's own `retrieved_contexts` column holds the chunks the SCENARIO was
    written from — for a `source='generated'` row, the exact chunks Haiku was
    instructed to answer from (`scenario_service.py:118`). Scoring the agent's
    answer against those measures the corpus the question came out of, not the
    retrieval the customer gets, and it does it while every metric reads high.

    So the fetched column is carried under a name the scorer does not read. This
    asserts the fetch does not put it back under the one it does.
    """
    tree = _eval_tree()
    offenders: list[str] = []
    for node in _dicts_with_key(tree, "reference_answer"):
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant):
                continue
            if key.value != "retrieved_contexts":
                continue
            offenders.append(f"line {node.lineno}: retrieved_contexts = {ast.unparse(value)}")
    assert offenders == [], (
        "the row fetched from eval_scenarios binds its stored contexts to "
        f"'retrieved_contexts', which is the key run_ragas_eval scores against: "
        f"{offenders}. Carry it as 'stored_retrieved_contexts' — the agent's own "
        "retrieve result is what the run must be scored on."
    )


# ---------------------------------------------------------------------------
# Dynamic half — one turn
# ---------------------------------------------------------------------------


class _Options:
    """Stand-in for the options object the seam returns. Not a MagicMock: it
    must be recognisably the seam's own object in a failure message."""

    def __repr__(self) -> str:  # pragma: no cover - only read on failure
        return "<options returned by the seam>"


def _db_ctx(db):
    @contextmanager
    def _ctx():
        yield db

    return _ctx


def _agent_row() -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.tenant_id = uuid.uuid4()
    agent.name = "Invocation Test Agent"
    agent.retrieval_strategy = {}
    return agent


def _turn(response_text="Returns are accepted within 14 days.", contexts=()):
    return {
        "response_text": response_text,
        "tool_calls_log": [
            {"tool_name": "retrieve", "input": {}, "result": c} for c in contexts
        ],
        "escalated": False,
        "escalation_reason": None,
        "escalation_context": None,
        "sdk_session_id": None,
        "total_cost_usd": 0.002,
        "num_turns": 3,
        "stop_reason": "end_turn",
    }


def test_the_turn_goes_through_the_seam_and_asks_for_recorded_side_effects():
    """The dynamic half of the recorded-mode pin.

    The static test above proves the SOURCE says "recorded". This proves the
    argument survives to the seam — an eval that built its own options, or
    threaded the mode through a variable that lost it, would pass there and fail
    here.

    Every other seam argument is asserted too, because each is a way the eval
    could measure a different agent than the one production serves:
    a `verified_session_token` that is not "" would give the eval an identity
    posture no eval scenario has, a `resume` would let scenario N's answer be
    shaped by scenario N-1.
    """
    agent = _agent_row()
    db = MagicMock()
    db.get.return_value = agent

    async def fake_sdk_turn(**kwargs):
        fake_sdk_turn.kwargs = kwargs
        return _turn()

    with (
        patch.object(mod, "get_sync_db", _db_ctx(db)),
        patch(
            "app.worker.tasks.runtime.agent.build_agent_options",
            return_value=_Options(),
        ) as seam,
        patch(
            "app.worker.tasks.runtime.agent._run_sdk_turn",
            side_effect=fake_sdk_turn,
        ),
    ):
        result = mod._run_one_eval_turn(
            agent_id=str(agent.id),
            conn_str=PRODUCTION,
            question="What is the return policy?",
            prompt_version_id=None,
        )

    seam.assert_called_once()
    kwargs = seam.call_args.kwargs
    assert kwargs["side_effects"] == "recorded", (
        "the eval asked the seam for "
        f"{kwargs.get('side_effects')!r} side effects. 'live' here means an eval "
        "scenario that talks the agent into a refund issues one against the "
        "tenant's provider."
    )
    assert kwargs["agent"] is agent
    assert kwargs["conn_str"] == PRODUCTION
    assert kwargs["verified_session_token"] == "", (
        "an eval scenario is an unverified customer; a token here would give "
        "every identity-gated skill a posture no scenario carries evidence for"
    )
    assert kwargs["resume"] is None, (
        "resume= would carry one scenario's SDK session into the next, so "
        "scenario N's answer could be shaped by scenario N-1"
    )
    assert fake_sdk_turn.kwargs["options"] is seam.return_value, (
        "the SDK turn was not handed the seam's own options object — the eval "
        "is measuring an agent it assembled itself"
    )
    assert fake_sdk_turn.kwargs["message"] == "What is the return policy?"
    assert result["response_text"].startswith("Returns are accepted")


def test_the_turn_serves_the_prompt_version_the_run_is_attributed_to():
    """A score attributed to a prompt version that never produced it is
    BACKLOG 2.3's defect, and it is one `soul_override=None` away.

    `eval_runs.prompt_version_id` names the PRODUCTION label. If the turn is
    served the agent's live `soul_*` columns instead, the run certifies a prompt
    version that had no part in the answers it scored.
    """
    agent = _agent_row()
    db = MagicMock()
    db.get.return_value = agent
    soul = {"soul_role": "the production persona", "soul_voice": "clipped"}
    pv_id = "11111111-2222-3333-4444-555555555555"

    async def fake_sdk_turn(**kwargs):
        return _turn()

    with (
        patch.object(mod, "get_sync_db", _db_ctx(db)),
        patch(
            "app.worker.tasks.runtime.agent._resolve_turn_prompt_version",
            return_value=(pv_id, dict(soul), False),
        ) as resolve,
        patch(
            "app.worker.tasks.runtime.agent.build_agent_options",
            return_value=_Options(),
        ) as seam,
        patch(
            "app.worker.tasks.runtime.agent._run_sdk_turn",
            side_effect=fake_sdk_turn,
        ),
    ):
        mod._run_one_eval_turn(
            agent_id=str(agent.id),
            conn_str=PRODUCTION,
            question="Q",
            prompt_version_id=pv_id,
        )

    resolve.assert_called_once()
    assert resolve.call_args.kwargs["existing_prompt_version_id"] == pv_id, (
        "the eval re-rolled the canary instead of re-fetching the version the "
        "run is attributed to — an eval whose attribution is decided by "
        "random.random() cannot be compared to the next one"
    )
    assert seam.call_args.kwargs["soul_override"] == soul


def test_the_eval_turn_writes_no_job_events():
    """The SSE half of "persistence and SSE differ by design".

    `_run_sdk_turn` emits `agent.tool_call` / `agent.tool_result` through the db
    and redis handles it is given. On the eval path the job_id names no `jobs`
    row, so those events would be sixty scenarios' worth of orphan rows in the
    CONTROL DB's `job_events` — the table the SSE replay endpoint and the ops
    room read. That is the tenant-data pollution approach (b) was chosen to
    avoid, one table over.
    """
    agent = _agent_row()
    db = MagicMock()
    db.get.return_value = agent
    seen: dict = {}

    async def fake_sdk_turn(**kwargs):
        seen.update(kwargs)
        return _turn()

    with (
        patch.object(mod, "get_sync_db", _db_ctx(db)),
        patch(
            "app.worker.tasks.runtime.agent.build_agent_options",
            return_value=_Options(),
        ),
        patch(
            "app.worker.tasks.runtime.agent._run_sdk_turn",
            side_effect=fake_sdk_turn,
        ),
    ):
        mod._run_one_eval_turn(
            agent_id=str(agent.id),
            conn_str=PRODUCTION,
            question="Q",
            prompt_version_id=None,
        )

    sink = seen["db"]
    assert seen["redis"] is sink
    assert isinstance(sink, mod._EvalEventSink), (
        f"the eval turn was handed {sink!r} as its event sink. A real Session "
        "here writes job_events rows under a job id that names no job."
    )
    # The sink satisfies both halves of emit()'s contract without persisting.
    assert sink.publish("job_events:x", "{}") == 0
    assert sink.add(object()) is None
    assert sink.commit() is None


# ---------------------------------------------------------------------------
# Dynamic half — the loop over a run's scenarios
# ---------------------------------------------------------------------------


def _scenarios(n: int, dataset: str = "exploratory") -> list[dict]:
    return [
        {
            "id": f"s{i}",
            "source": "generated",
            "question": f"Question {i}?",
            "reference_answer": f"The reference answer for {i}.",
            "stored_retrieved_contexts": [f"STORED CONTEXT {i}"],
            "dataset": dataset,
        }
        for i in range(n)
    ]


def _invoke(scenarios, turn_for, side_effects_for=None):
    """Drive the real loop with the SDK turn doubled at one boundary."""
    calls: list[str] = []

    def _fake_turn(*, agent_id, conn_str, question, prompt_version_id):
        calls.append(question)
        return turn_for(question)

    def _fake_recorded():
        return list(side_effects_for(calls[-1])) if side_effects_for else []

    with (
        patch.object(mod, "_run_one_eval_turn", side_effect=_fake_turn),
        patch(
            "app.services.agent_tools.get_recorded_side_effects",
            side_effect=_fake_recorded,
        ),
    ):
        rows, summary = mod._invoke_agent_for_scenarios(
            agent_id="agent-1",
            conn_str=PRODUCTION,
            scenarios=scenarios,
            prompt_version_id=None,
        )
    return rows, summary, calls


def test_the_agent_is_invoked_once_per_scenario():
    scenarios = _scenarios(4)
    rows, summary, calls = _invoke(scenarios, lambda q: _turn(f"ANSWER to {q}"))

    assert calls == [s["question"] for s in scenarios], (
        f"the agent was asked {calls} for scenarios {[s['question'] for s in scenarios]}"
    )
    assert summary["attempted"] == 4
    assert summary["responded"] == 4
    assert len(rows) == 4


def test_a_scored_row_never_carries_the_reference_answer_as_its_response():
    """THE D1 REGRESSION PIN, at run time.

    The static pin proves no dict in `eval.py` can assemble the tautology. This
    proves the plumbing does not reassemble it: every row that reaches the
    scorer carries the AGENT's text, and it is not the label.
    """
    scenarios = _scenarios(3)
    rows, _summary, _calls = _invoke(scenarios, lambda q: _turn(f"ANSWER to {q}"))

    assert rows, "nothing was scored, so this pin proved nothing"
    for row in rows:
        assert row["agent_response"] != row["reference_answer"], (
            f"scenario {row['id']} is being scored against its own label "
            f"({row['agent_response']!r}) — D1 is back, and Faithfulness will "
            "read ~1.0 for every agent this system ever deploys"
        )
        assert row["agent_response"].startswith("ANSWER to "), (
            "the scored response did not come from the agent turn"
        )


def test_the_contexts_scored_are_the_ones_the_agent_retrieved():
    """Not the scenario's stored column, which is what the answer was WRITTEN
    from. Scoring against those is D1 in a different costume: every metric reads
    high and nothing about retrieval is measured."""
    scenarios = _scenarios(2)
    rows, _summary, _calls = _invoke(
        scenarios,
        lambda q: _turn(f"ANSWER to {q}", contexts=[f"AGENT RETRIEVED for {q}"]),
    )

    for row in rows:
        assert row["retrieved_contexts"] == [
            f"AGENT RETRIEVED for {row['question']}"
        ], (
            f"scored contexts for {row['id']} are {row['retrieved_contexts']!r}"
        )
        assert row["stored_retrieved_contexts"] != row["retrieved_contexts"], (
            "the stored column and the agent's own retrieval are the same "
            "object — this test cannot tell them apart, so it proves nothing"
        )


def test_a_failing_scenario_is_excluded_and_counted_never_scored_zero():
    """Zero is not a low score, it is the absence of one.

    A scored 0 would move Faithfulness with the failure rate of the Agent SDK
    rather than with the agent's behaviour — and in the direction that reads as
    a quality regression, so the first response would be to change the agent.
    `compute_correlation.py:485` learned this about a judge that errors; this is
    the same rule one layer earlier.
    """
    scenarios = _scenarios(4)

    def _turn_for(question):
        if question == "Question 2?":
            raise TimeoutError("SDK subprocess never answered")
        return _turn(f"ANSWER to {question}")

    rows, summary, calls = _invoke(scenarios, _turn_for)

    assert len(calls) == 4, "the loop stopped at the first failure"
    assert [r["id"] for r in rows] == ["s0", "s1", "s3"], (
        f"the failing scenario reached the scorer: {[r['id'] for r in rows]}"
    )
    assert summary["attempted"] == 4
    assert summary["responded"] == 3
    assert summary["failed"] == 1
    assert summary["errors"] == {"TimeoutError": 1}
    # …and nothing anywhere is a zero standing in for the missing observation.
    assert all(r.get("agent_response") for r in rows)


def test_a_turn_that_returns_no_text_is_counted_apart_from_one_that_raised():
    """Empty `response_text` is the max_turns / max_budget signature (agent.py's
    D-10 notes) — a different failure from an exception, and one that would be
    scored as "the agent said nothing" if it were not excluded."""
    scenarios = _scenarios(3)

    def _turn_for(question):
        return _turn("" if question == "Question 1?" else f"ANSWER to {question}")

    rows, summary, _calls = _invoke(scenarios, _turn_for)

    assert [r["id"] for r in rows] == ["s0", "s2"]
    assert summary["responded"] == 2
    assert summary["empty"] == 1
    assert summary["failed"] == 0


def test_a_run_below_the_response_rate_floor_reports_unknown():
    """MISSING DATA IS NEVER PASSING DATA.

    Ten scenarios, four answers. Every score in that run is real and four of ten
    is not a measurement of the agent's quality. Same floor shape as
    `compute_correlation.py`'s MIN_PAIR_RATE, and the same argument: a metric
    over the rows that happened to succeed is not a measurement of the set that
    was scored.
    """
    scenarios = _scenarios(10)

    def _turn_for(question):
        index = int(question.split()[1].rstrip("?"))
        if index >= 4:
            raise RuntimeError("provider unavailable")
        return _turn(f"ANSWER to {question}")

    rows, summary, _calls = _invoke(scenarios, _turn_for)

    assert len(rows) == 4
    assert summary["response_rate"] == pytest.approx(0.4)
    assert summary["status"] == eval_service.AGENT_INVOCATION_UNKNOWN, (
        f"a run where 4 of 10 scenarios answered reported {summary['status']!r}"
    )
    assert (
        eval_service.invocation_provenance(summary)["agent_invoked"] is False
    ), (
        "the run certified itself as having measured the agent on a 40% "
        "response rate — the deploy gate would ship on it"
    )
    # The denominator travels, so the reader can see WHY it is unknown.
    assert summary["attempted"] == 10 and summary["responded"] == 4


def test_a_run_that_invoked_nothing_is_unknown_not_measured():
    """A rate over an empty denominator is unknown, never a pass. Zero valid
    rows is the state a brand-new tenant is in every night until someone writes
    a scenario."""
    rows, summary, calls = _invoke([], lambda q: _turn())

    assert rows == [] and calls == []
    assert summary["attempted"] == 0
    assert summary["response_rate"] is None
    assert summary["status"] == eval_service.AGENT_INVOCATION_UNKNOWN
    assert eval_service.invocation_provenance(summary)["agent_invoked"] is False


def test_the_mutating_skill_attempts_are_carried_out_of_the_turn():
    """The recording is eval signal, not debris (the owner, 2026-08-07).

    That the agent CHOSE to call `issue_refund` is capability-envelope
    adherence — the audit's confusion matrix has a whole cell for it — and it is
    invisible unless it is carried out of the turn it happened in. The
    ContextVar sink is per-turn; nothing else reads it.
    """
    scenarios = _scenarios(2)
    attempts = {
        "Question 0?": [
            {
                "kind": "transactional.adapter",
                "detail": {"skill": "issue_refund", "amount": 40.0},
            },
            {"kind": "retrieval_metrics.write", "detail": {"row": {"mrr": 1.0}}},
        ],
        "Question 1?": [
            {"kind": "transactional.declined", "detail": {"skill": "place_order"}},
        ],
    }

    _rows, summary, _calls = _invoke(
        scenarios,
        lambda q: _turn(f"ANSWER to {q}"),
        side_effects_for=lambda q: attempts[q],
    )

    recorded = summary["side_effect_attempts"]
    assert recorded["counts"] == {
        "transactional.adapter": 1,
        "retrieval_metrics.write": 1,
        "transactional.declined": 1,
    }
    kinds = [a["kind"] for a in recorded["capability_attempts"]]
    assert "transactional.adapter" in kinds and "transactional.declined" in kinds, (
        "an eval that records only what WOULD have executed cannot tell 'the "
        "agent never tried' from 'the agent tried and the envelope stopped it', "
        "and those are opposite cells of the confusion matrix"
    )
    assert "retrieval_metrics.write" not in kinds, (
        "telemetry is counted, not carried — one metrics row per retrieve per "
        "scenario is tens of kilobytes of float in a jsonb column"
    )
    assert recorded["capability_attempts"][0]["scenario_id"] == "s0", (
        "the attempt is not attributable to the scenario that produced it"
    )


def test_an_attempt_made_by_a_scenario_that_then_failed_is_still_recorded():
    """The read happens on BOTH paths. A scenario that drove the agent to
    attempt a refund and then timed out still observed the attempt, and the
    attempt is the more valuable of the two observations."""
    scenarios = _scenarios(1)

    def _turn_for(question):
        raise TimeoutError("died after the tool call")

    _rows, summary, _calls = _invoke(
        scenarios,
        _turn_for,
        side_effects_for=lambda q: [
            {"kind": "transactional.adapter", "detail": {"skill": "issue_refund"}}
        ],
    )

    assert summary["failed"] == 1
    assert summary["side_effect_attempts"]["counts"] == {"transactional.adapter": 1}


def test_the_side_effect_mode_is_returned_to_live_when_the_loop_ends():
    """A stale 'recorded' is the failure a customer finds, not us.

    The mode is a ContextVar and the Celery prefork pool does not isolate them
    per task. Left in 'recorded', the next thing to run in this context stops
    refunding real customers, stops sending escalation mail and stops writing
    retrieval metrics — with no error raised anywhere.
    """
    from app.services import agent_tools

    agent_tools._side_effects_var.set("recorded")

    def _boom(question):
        raise RuntimeError("every scenario failed")

    _invoke(_scenarios(2), _boom)

    assert agent_tools.current_side_effect_mode() == "live", (
        "the invocation loop left the process context in recorded mode"
    )


def test_the_per_run_ceiling_bounds_the_calls_and_says_what_it_skipped():
    """Cost, bounded and reported. The golden set runs unsampled and the
    exploratory sample rotates, so a tenant's designations decide the nightly
    bill; the ceiling stops that being unbounded, and reporting what it skipped
    is what keeps a truncated run from reading like a complete one."""
    ceiling = eval_service.AGENT_INVOCATION_MAX_CALLS_PER_RUN
    scenarios = _scenarios(ceiling + 3, dataset="golden")

    rows, summary, calls = _invoke(scenarios, lambda q: _turn(f"A to {q}"))

    assert len(calls) == ceiling, (
        f"{len(calls)} SDK turns ran against a ceiling of {ceiling}"
    )
    assert len(rows) == ceiling
    assert summary["attempted"] == ceiling
    assert summary["valid"] == ceiling + 3
    assert summary["ceiling_skipped"] == 3
    assert summary["ceiling_skipped_golden"] == 3, (
        "golden rows were skipped without the run saying so — the paired "
        "per-item delta the golden set exists for silently does not cover them"
    )
    # A ceiling that suppressed rows must not also suppress the measurement: the
    # rate is over what was ATTEMPTED, and what was not attempted is reported
    # beside it rather than folded in.
    assert summary["response_rate"] == 1.0
    assert summary["status"] == eval_service.AGENT_INVOCATION_MEASURED


def test_the_bounds_the_run_ran_under_are_on_the_run():
    """Both bounds in provenance (the plan's P2, last bullet), plus the retrieve
    truncation.

    Faithfulness over a context that was CUT marks a claim unsupported when the
    support was merely beyond the cap. A reader of a low faithfulness score has
    to be able to see the cap without going to a different module at a different
    commit to find it.
    """
    from app.worker.tasks.runtime.agent import (
        AGENT_TURN_TIMEOUT_S,
        RETRIEVE_RESULT_CAPTURE_CHARS,
    )

    scenarios = _scenarios(2)
    at_cap = "x" * RETRIEVE_RESULT_CAPTURE_CHARS

    def _turn_for(question):
        if question == "Question 0?":
            return _turn(f"A to {question}", contexts=[at_cap])
        return _turn(f"A to {question}", contexts=["short context"])

    _rows, summary, _calls = _invoke(scenarios, _turn_for)

    assert summary["concurrency"] == eval_service.AGENT_INVOCATION_CONCURRENCY == 1
    assert summary["max_calls_per_run"] == eval_service.AGENT_INVOCATION_MAX_CALLS_PER_RUN
    assert summary["per_turn_timeout_s"] == AGENT_TURN_TIMEOUT_S
    assert summary["max_wall_clock_s"] == (
        eval_service.AGENT_INVOCATION_MAX_CALLS_PER_RUN * AGENT_TURN_TIMEOUT_S
    )
    assert summary["retrieved_context_char_cap"] == RETRIEVE_RESULT_CAPTURE_CHARS
    assert summary["retrieved_context_at_cap"] == 1, (
        "a retrieve result that came back exactly at the cap was not reported "
        "as such — a faithfulness miss caused by truncation then reads as an "
        "ungrounded claim"
    )
    assert summary["pii_firewall_applied"] is False, (
        "the eval scores the agent's own text, not the deflection a customer "
        "would receive; that difference is stated on the run, not implied"
    )


def test_the_retrieve_cap_is_read_from_the_turn_path_not_copied():
    """One copy of the number, and the eval imports it.

    A second literal here is the audit's D3 defect wearing new clothes: the
    deploy gate's eval query fails open to this day because one call site kept
    its own copy of a column name. If agent.py's capture is retuned, this run's
    provenance has to move with it or the run reports a cap it did not use.
    """
    import inspect

    from app.worker.tasks.runtime import agent as agent_module

    source = inspect.getsource(agent_module._run_sdk_turn)
    assert "RETRIEVE_RESULT_CAPTURE_CHARS" in source, (
        "_run_sdk_turn no longer truncates through the named constant, so the "
        "cap the eval stamps on every run is no longer the cap it ran under"
    )
    assert "[:1800]" not in source, (
        "the literal is back beside the constant — two copies, one of which "
        "will move first"
    )


def test_the_loop_refuses_to_run_if_the_concurrency_bound_moves_without_it():
    """The provenance says concurrency=1 and the loop is sequential. A run whose
    record claims a bound nothing enforced is this phase's whole subject, so the
    disagreement raises instead of being recorded."""
    with patch.object(mod, "AGENT_INVOCATION_CONCURRENCY", 4):
        with pytest.raises(RuntimeError, match="AGENT_INVOCATION_CONCURRENCY"):
            mod._invoke_agent_for_scenarios(
                agent_id="agent-1",
                conn_str=PRODUCTION,
                scenarios=_scenarios(1),
                prompt_version_id=None,
            )


# ---------------------------------------------------------------------------
# The whole task — the provenance P3 reads
# ---------------------------------------------------------------------------


@pytest.fixture
def task_wired(monkeypatch):
    """run_eval_suite with every boundary doubled EXCEPT the invocation loop.

    Deliberately different from `test_eval_task.py`'s `wired`, which doubles the
    loop: this fixture doubles only the SDK turn, so the real
    `_invoke_agent_for_scenarios` runs and the rows that reach `run_ragas_eval`
    are the ones the real code assembled.
    """
    import psycopg2

    agent = MagicMock()
    agent.neon_project_id = "neon-project-1"
    agent.neon_connection_string = b"encrypted"
    agent.id = uuid.uuid4()
    agent.tenant_id = uuid.uuid4()
    agent.name = "A"
    agent.retrieval_strategy = {}

    db = MagicMock()
    db.get.return_value = agent
    monkeypatch.setattr(mod, "get_sync_db", _db_ctx(db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: PRODUCTION)

    rows = [
        ("s0", "generated", "Question 0?", "Reference 0.", ["STORED 0"], "golden"),
        ("s1", "generated", "Question 1?", "Reference 1.", ["STORED 1"], None),
    ]

    class _Cursor:
        def __init__(self):
            self._last: list = []

        def execute(self, sql, params=None):
            if "FROM eval_scenarios" not in sql:
                self._last = []
            elif "dataset = %(golden)s" in sql:
                self._last = [rows[0]]
            else:
                self._last = [rows[1]]

        def fetchone(self):
            return None

        def fetchall(self):
            return self._last

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    conn = MagicMock()
    conn.cursor.return_value = _Cursor()
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **kw: conn)
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

    monkeypatch.setattr(mod, "mine_production_scenarios", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "store_scenarios", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "create_branch", lambda p, n: ("branch-1", BRANCH))
    monkeypatch.setattr(mod, "delete_branch", lambda p, b: None)
    monkeypatch.setattr(mod, "wait_for_neon_ready", lambda c: None)
    monkeypatch.setattr(mod, "write_eval_results", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "update_eval_run_status", lambda *a, **kw: None)
    monkeypatch.setattr(
        mod,
        "build_eval_run_config",
        lambda agent_id, conn_str, dataset=None: {
            "prompt_version_id": None,
            "config": {"dataset": dataset},
        },
    )
    monkeypatch.setattr(mod, "insert_eval_run", lambda *a, **kw: True)

    rec: dict = {"scored": [], "patched": []}
    monkeypatch.setattr(
        mod,
        "run_ragas_eval",
        lambda scenarios: (
            rec["scored"].append(list(scenarios))
            or {"scores": [], "means": {}, "sent": 0, "returned": 0, "unattributed": 0}
        ),
    )
    monkeypatch.setattr(
        mod,
        "update_eval_run_config",
        lambda run_id, patch_dict, conn_str: (
            rec["patched"].append((run_id, patch_dict, conn_str)) or True
        ),
    )

    def _fake_turn(*, agent_id, conn_str, question, prompt_version_id):
        return _turn(f"AGENT ANSWER to {question}", contexts=[f"AGENT CTX {question}"])

    monkeypatch.setattr(mod, "_run_one_eval_turn", _fake_turn)
    return rec


def _run_task(agent_id="agent-1"):
    mod.run_eval_suite.push_request(retries=0)
    try:
        return mod.run_eval_suite.run(agent_id)
    finally:
        mod.run_eval_suite.pop_request()


def test_the_task_hands_the_scorer_agent_responses_and_agent_contexts(task_wired):
    """End of the D1 chain: what actually reaches Ragas."""
    result = _run_task()

    assert task_wired["scored"], "run_ragas_eval was never called"
    samples = task_wired["scored"][0]
    assert len(samples) == 2
    for sample in samples:
        assert sample["agent_response"] == f"AGENT ANSWER to {sample['question']}"
        assert sample["agent_response"] != sample["reference_answer"]
        assert sample["retrieved_contexts"] == [f"AGENT CTX {sample['question']}"]
        assert sample["retrieved_contexts"] != sample["stored_retrieved_contexts"]
    assert result["agent_invoked"] is True


def test_the_run_records_that_the_agent_was_invoked(task_wired):
    """The provenance field P3 consumes, written on PRODUCTION.

    P3 refuses an eval signal whose `agent_invoked` is false OR ABSENT — every
    run persisted before this phase was produced by the tautology and carries
    nothing, so a gate that only checked `false` would keep shipping on the
    whole of history.
    """
    result = _run_task()

    assert task_wired["patched"], (
        "the run's config was never patched, so eval_runs still says "
        "agent_invoked=false for a run that invoked the agent"
    )
    run_id, patch_dict, conn_str = task_wired["patched"][0]
    assert run_id == result["run_id"]
    assert conn_str == PRODUCTION, (
        "the provenance was written somewhere other than production — the Neon "
        "branch is deleted in `finally` and takes the claim with it (audit D2)"
    )
    assert patch_dict["agent_invoked"] is True
    assert patch_dict["scored_response_source"] == "agent_response"
    assert patch_dict["dimensions_not_exercised"] == []
    assert patch_dict["agent_invocation"]["responded"] == 2
    assert result["invocation_recorded"] is True


def test_the_row_exists_before_the_first_turn_and_is_corrected_after_the_last(
    task_wired, monkeypatch
):
    """Fail-closed ORDERING, which is the whole reason there are two writes.

    The eval_runs row is also the per-agent idempotency key ('m6:{agent_id}' with
    a 10-minute window), so it has to exist before the first of sixty SDK turns —
    inserting it afterwards would let a second beat dispatch double-invoke and
    double-bill. It is therefore inserted carrying agent_invoked=false (see
    test_config_at_insert_says_the_agent_has_not_been_invoked_yet in
    test_eval_service.py) and corrected once the invocation has reported. A run
    that dies in between keeps the false and the deploy gate refuses it.

    The correction lands BEFORE scoring, too: the invocation is the expensive,
    unrepeatable half, and a judge outage must not take the record of what the
    agent did down with it.
    """
    order: list[str] = []

    monkeypatch.setattr(
        mod, "insert_eval_run", lambda *a, **kw: order.append("insert") or True
    )
    real_turn = mod._run_one_eval_turn

    def _traced_turn(**kwargs):
        order.append("turn")
        return real_turn(**kwargs)

    monkeypatch.setattr(mod, "_run_one_eval_turn", _traced_turn)
    monkeypatch.setattr(
        mod,
        "update_eval_run_config",
        lambda *a, **kw: order.append("patch") or True,
    )
    monkeypatch.setattr(
        mod,
        "run_ragas_eval",
        lambda scenarios: order.append("score")
        or {"scores": [], "means": {}, "sent": 0, "returned": 0, "unattributed": 0},
    )

    _run_task()

    assert order == ["insert", "turn", "turn", "patch", "score"], (
        f"the run's write order is {order}. 'insert' must precede every 'turn' "
        "(the row is the idempotency key), and 'patch' must precede 'score' (a "
        "judge outage must not take the record of what the agent did with it)."
    )
