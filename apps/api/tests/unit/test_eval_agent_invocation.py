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

No live PostgreSQL and no provider endpoint exist here, so the turn is a double
at exactly one boundary - `_run_one_eval_turn` for the loop tests, and
`agent_loop.build_agent_turn` / `agent_loop.run_agent_loop` for the turn test.
Nothing in this file observes a real eval end to end; that is integration
territory and it SKIPS on this machine, which is unobserved, never a pass.
"""

from __future__ import annotations

import ast
import importlib
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services import eval_service
from app.worker.tasks.runtime import eval as mod

_EVAL_PY = Path(mod.__file__).with_suffix(".py")

PRODUCTION = "postgresql://production/tenant"

#: The eval run these turns belong to. It is the job_id every model call of the
#: run bills under, agent turns and judges alike, so `model_calls WHERE
#: job_id = <run_id>` returns the whole run rather than the judge half of it.
RUN_ID = "eeeeeeee-1111-2222-3333-444444444444"

#: The seam every caller of the customer agent must go through (P1, ADR 0008).
SEAM = "build_agent_turn"


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


def test_the_stored_context_column_is_never_read_back_by_the_eval():
    """The half the name-check could not see, and the review proved it could not.

    The claim "the stored column is structurally out of reach of the scorer" was
    false. A ONE-TOKEN fallback —

        "retrieved_contexts": contexts or scenario["stored_retrieved_contexts"],

    — left all 163 tests in the three eval modules green, because the test above
    inspects dict literals carrying a `reference_answer` key and the SCORED row
    builds its scenario fields with `**scenario`, and because every dynamic test
    supplies a non-empty retrieve result so the fallback never fires. The blind
    spot was exactly the case the fallback exists for: the agent answered without
    retrieving. Scoring then runs faithfulness against the <=5 chunks Haiku was
    told to write the reference answer FROM, which is D1 restored with the metric
    reading high.

    So the guard is on the READ, not on the name: the string
    'stored_retrieved_contexts' may appear in eval.py only as a dict KEY. Any
    subscript, `.get()` or comparison against it is a route back.
    """
    tree = _eval_tree()
    key_nodes = {
        id(k)
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for k in node.keys
        if isinstance(k, ast.Constant)
    }
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and node.value == "stored_retrieved_contexts"
        and id(node) not in key_nodes
    ]
    assert reads == [], (
        "eval.py READS the scenario's stored context column at line(s) "
        f"{[n.lineno for n in reads]}. It may only ever WRITE it, under a name "
        "run_ragas_eval does not score. A fallback from the agent's own "
        "retrieval to the stored column is D1's second half: the answer is then "
        "graded against the chunks it was written from."
    )


def test_the_eval_reaches_a_turn_only_through_the_seam_and_never_through_the_task():
    """The companion the side-effects guard needs to mean what it says.

    `test_the_eval_path_never_asks_for_live_side_effects` enumerates
    `build_agent_turn(...)` call sites INSIDE eval.py, so it is blind to a
    route to a live turn that does not name that function here. Adding
    `run_agent_turn(...)` or `run_agent_turn.apply_async(...)` for a subset of
    scenarios — approach (a) creeping back — leaves the existing call site intact
    and every offence check passing, while the new path runs the chat turn with
    side_effects='live': it writes conversations, messages and turn_metrics rows
    into the table mine_production_scenarios reads (so the eval starts generating
    its own future test set from its own output, the reason (a) was rejected) and
    it can move money.

    So: eval.py may not mention the task at all. The seam is the only door, and
    tests/unit/test_agent_options_seam.py's MODULES_ALLOWED_TO_CONSTRUCT_OPTIONS
    is the only list of who may open one.
    """
    tree = _eval_tree()
    mentions = [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "run_agent_turn")
        or (isinstance(node, ast.Attribute) and node.attr == "run_agent_turn")
        or (
            isinstance(node, ast.alias)
            and (node.name == "run_agent_turn" or node.asname == "run_agent_turn")
        )
    ]
    assert mentions == [], (
        f"eval.py references run_agent_turn at line(s) {mentions}. The eval "
        "reaches the agent through build_agent_turn + run_agent_loop, which "
        "is what lets it demand side_effects='recorded'. Dispatching the chat "
        "task instead runs the turn LIVE and writes tenant data, and the "
        "side-effects guard cannot see it."
    )


def test_the_constant_that_claims_the_agent_is_invoked_is_pinned_to_the_code():
    """EVAL_INVOKES_AGENT is a claim about eval.py, so eval.py has to be read.

    It was flipped to True in the same commit that DELETED its only pin
    (`test_the_task_still_scores_the_reference_answer_against_itself`, which read
    eval.py's source and asserted the constant agreed with it). Nothing else
    reads the constant — a grep across app/ and tests/ finds only its own
    definition — so a future edit that reverts the invocation would leave the
    module declaring EVAL_INVOKES_AGENT = True, which the block comment three
    lines above it names as "the tautology with a newer comment".

    Both directions, because a one-way check is satisfied by deleting the
    constant AND the call site together.
    """
    call_sites = _calls_named(_eval_tree(), SEAM)

    if eval_service.EVAL_INVOKES_AGENT:
        assert call_sites, (
            "eval_service.EVAL_INVOKES_AGENT is True and eval.py contains no "
            f"{SEAM}(...) call site. The constant is stamped on nothing and "
            "asserts something the code does not do — D1 with a newer comment."
        )
    else:
        assert not call_sites, (
            "eval_service.EVAL_INVOKES_AGENT is False while eval.py invokes the "
            f"agent through {SEAM} at line(s) "
            f"{[c.lineno for c in call_sites]}. A run would be stamped as "
            "un-measured while measuring, which is the fail-open direction."
        )


# ---------------------------------------------------------------------------
# Dynamic half — one turn
# ---------------------------------------------------------------------------


class _Options:
    """Stand-in for the options object the seam returns. Not a MagicMock: it
    must be recognisably the seam's own object in a failure message.

    It carries `calls` and `ledger` because the eval writes the turn's ledger rows
    after the turn, off the event loop, and reads both to do it.
    """

    def __init__(self, ledger=None, calls=None) -> None:
        self.calls: list = list(calls or [])
        self.ledger = ledger if ledger is not None else (lambda call: None)

    def __repr__(self) -> str:  # pragma: no cover - only read on failure
        return "<options returned by the seam>"


def _a_model_call():
    """One finished row, the shape a ledger is handed."""
    from datetime import datetime, timezone

    from app.domain.model_call import ModelCall, ModelSource

    return ModelCall(
        purpose="agent_turn",
        provider="openai",
        requested_model="gpt-5.6-luna",
        served_model="gpt-5.6-luna",
        model_source=ModelSource.REPORTED,
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
        tenant_id="11111111-1111-1111-1111-111111111111",
    )


def _assert_ledger_writes_to(ledger, dsn):
    """Drive a real ModelCall through `ledger` and assert where the row lands.

    `callable(ledger)` is satisfied by `lambda call: None`, which records nothing,
    spends the tenant's money and leaves no row. That is the failure #46 ended. The value
    is what matters, so this asserts it.
    """
    assert ledger is not None, "the caller built a turn with no ledger at all"
    call = _a_model_call()
    with patch("app.core.model_client.record_model_call") as write:
        ledger(call)

    write.assert_called_once()
    assert write.call_args.args[0] is call
    assert write.call_args.args[1] == dsn, (
        f"the ledger wrote this turn's model_calls row to {write.call_args.args[1]!r} "
        f"rather than to {dsn!r}, the database the turn was served from"
    )


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


def _loop_module():
    """agent_loop.py, imported lazily.

    Lazy for the same reason eval.py's own imports of it are: this module reads
    eval.py's syntax tree, and binding the turn's whole import graph at
    COLLECTION time would make these guards depend on what else pytest imported
    first. A guard whose meaning depends on collection order is not a guard.
    """
    from app.services import agent_loop

    return agent_loop


#: The default retrieve result behind `_turn`. Non-empty, because a responded
#: turn that retrieved NOTHING is excluded from scoring by design (Faithfulness
#: over an empty context list is structurally 0 or NaN) — so a default of `()`
#: would make every loop test below a test of the exclusion branch instead of a
#: test of what it is named for.
DEFAULT_CONTEXT = "Returns are accepted within 14 days of delivery."


def _retrieve_entry(chunks, *, unparsed=False):
    """A `tool_calls_log` retrieve entry shaped as agent.py's capture leaves it.

    Both captures, because the eval must read the SECOND one: `result` is the
    audit copy (a repr of the SDK content block, cut at the capture cap) and
    RETRIEVE_CHUNKS_KEY is the same result decoded into one string per chunk.
    A test that supplied only `result` could not tell the two apart, which is
    how scoring a repr blob survived review.
    """
    loop = _loop_module()
    chunks = list(chunks)
    return {
        "tool_name": "retrieve",
        "input": {},
        "result": repr([{"type": "text", "text": str(chunks)}])[
            : loop.RETRIEVE_RESULT_CAPTURE_CHARS
        ],
        loop.RETRIEVE_CHUNKS_KEY: [] if unparsed else chunks,
        loop.RETRIEVE_CHUNKS_SOURCE_KEY: (
            loop.RETRIEVE_CHUNKS_UNPARSED if unparsed else loop.RETRIEVE_CHUNKS_PARSED
        ),
    }


def _turn(
    response_text="Returns are accepted within 14 days.",
    contexts=(DEFAULT_CONTEXT,),
    *,
    retrieve_calls=None,
    unparsed=False,
):
    """The dict `run_agent_loop` returns.

    `contexts` is the list of CHUNK TEXTS the retrieve call came back with, and
    by default one retrieve call carries all of them — which is the production
    shape (`retrieve` returns up to MAX_CHUNKS chunks in one result).
    `retrieve_calls` splits them across N calls when a test needs that;
    `retrieve_calls=0` is a turn the agent answered without retrieving at all.
    """
    contexts = list(contexts)
    if retrieve_calls is None:
        retrieve_calls = 1 if (contexts or unparsed) else 0
    if retrieve_calls == 0:
        log: list[dict] = []
    elif retrieve_calls == 1:
        log = [_retrieve_entry(contexts, unparsed=unparsed)]
    else:
        log = [
            _retrieve_entry([c], unparsed=unparsed)
            for c in contexts[:retrieve_calls]
        ]
    return {
        "response_text": response_text,
        "tool_calls_log": log,
        "escalated": False,
        "escalation_reason": None,
        "escalation_context": None,
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
    posture no eval scenario has, and a non-empty `history` would let scenario
    N's answer be shaped by scenario N-1.
    """
    agent = _agent_row()
    db = MagicMock()
    db.get.return_value = agent

    async def fake_loop(*args, **kwargs):
        fake_loop.args = args
        fake_loop.kwargs = kwargs
        return _turn()

    with (
        patch.object(mod, "get_sync_db", _db_ctx(db)),
        patch(
            "app.services.agent_loop.build_agent_turn",
            return_value=_Options(),
        ) as seam,
        patch(
            "app.services.agent_loop.run_agent_loop",
            side_effect=fake_loop,
        ),
    ):
        result = mod._run_one_eval_turn(
            agent_id=str(agent.id),
            conn_str=PRODUCTION,
            run_id=RUN_ID,
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
    _assert_ledger_writes_to(kwargs["ledger"], PRODUCTION)
    assert kwargs["job_id"] == RUN_ID, (
        "the eval turn billed under "
        f"{kwargs.get('job_id')!r}, an id that names no job. The judges bill under "
        "the run id, so model_calls WHERE job_id = <run_id> returns the judge half "
        "of the run and none of the agent turns, and the agent's own eval traffic "
        "is indistinguishable from live customer traffic under purpose='agent_turn'."
    )
    assert fake_loop.kwargs["turn"] is seam.return_value, (
        "the loop was not handed the seam's own turn object, so the eval is "
        "measuring an agent it assembled itself"
    )
    assert fake_loop.kwargs["history"] == [], (
        "the eval turn carried conversation history. Scenarios are independent "
        "by construction, and history here lets scenario N's answer be shaped "
        f"by scenario N-1; got {fake_loop.kwargs['history']!r}"
    )
    assert fake_loop.args[0] == "What is the return policy?"
    assert result["response_text"].startswith("Returns are accepted")


def _drive_one_eval_turn(*, calls, loop):
    """Run `_run_one_eval_turn` over a seam whose turn already carries `calls`.

    Returns every `(row, target)` pair that reached `record_model_call`. The seam
    is handed a real `ledger_recorder` bound to the production dsn, because the
    question is which database the rows land in, and a double would answer it for
    the double.
    """
    from app.core.model_client import ledger_recorder

    agent = _agent_row()
    db = MagicMock()
    db.get.return_value = agent
    writes: list[tuple] = []

    with (
        patch.object(mod, "get_sync_db", _db_ctx(db)),
        patch(
            "app.services.agent_loop.build_agent_turn",
            return_value=_Options(ledger=ledger_recorder(PRODUCTION), calls=calls),
        ),
        patch("app.services.agent_loop.run_agent_loop", side_effect=loop),
        patch(
            "app.core.model_client.record_model_call",
            side_effect=lambda row, target: writes.append((row, target)),
        ),
    ):
        yield_value = None
        try:
            yield_value = mod._run_one_eval_turn(
                agent_id=str(agent.id),
                conn_str=PRODUCTION,
                run_id=RUN_ID,
                question="What is the return policy?",
                prompt_version_id=None,
            )
        except Exception as exc:
            # Returned rather than raised. Both callers want the ledger writes, and
            # one of them wants the failure as well.
            yield_value = exc
    return writes, yield_value


def test_an_eval_turn_writes_its_model_calls_rows_to_the_tenant_ledger():
    """The rows a scenario's turn recorded reach `record_model_call` after it.

    `_drive_eval_turn`'s `finally` is the only thing that takes them there, and
    deleting it left the suite green because every turn double in this file
    carried an empty `calls`. A run that invokes sixty scenarios a night and
    records none of them is the failure #46 ended, one path over from the chat
    task.
    """
    call = _a_model_call()

    async def _loop(*_args, **_kwargs):
        return _turn()

    writes, result = _drive_one_eval_turn(calls=[call], loop=_loop)

    assert writes == [(call, PRODUCTION)], (
        "an eval turn recorded a model call and the ledger saw "
        f"{writes}. The run spent the tenant's money with no row to show for it."
    )
    assert result["response_text"].startswith("Returns are accepted")


def test_an_eval_turn_that_died_still_writes_the_calls_it_already_paid_for():
    """A scenario that raised has still been billed for the calls it made.

    The eval catches this one scenario and carries on, so the rows are the only
    record that the turn cost anything at all. `_drive_eval_turn` writes them from
    a `finally` for that reason, and this is what says so.
    """
    call = _a_model_call()

    async def _loop(*_args, **_kwargs):
        raise RuntimeError("the provider hung up mid-scenario")

    writes, result = _drive_one_eval_turn(calls=[call], loop=_loop)

    assert isinstance(result, RuntimeError), (
        f"the scenario failure was swallowed inside the turn; got {result!r}. "
        "`_invoke_agent_for_scenarios` is what excludes and counts it."
    )
    assert writes == [(call, PRODUCTION)], (
        f"a scenario that died lost the rows it had already paid for: {writes}"
    )


def test_every_model_call_of_an_eval_turn_is_billed_under_the_run():
    """The id the run's agent turns bill under, stamped where it is stamped.

    `make_async_client` builds the `CallContext` that every `ModelCall` the ledger
    hook records carries, and `job_id` is the field a rollup groups by. The seam
    passes its `job_id` straight into it, so the id the factory is asked for IS the
    id on the rows. The judges bill under the run id (`_run_ledger`); before this,
    the agent turns billed under a uuid minted per scenario that named no job.
    """
    agent = _agent_row()
    db = MagicMock()
    db.get.return_value = agent

    async def fake_loop(*args, **kwargs):
        return _turn()

    with (
        patch.object(mod, "get_sync_db", _db_ctx(db)),
        patch("app.services.agent_loop.make_async_client") as factory,
        patch("app.services.agent_loop.run_agent_loop", side_effect=fake_loop),
    ):
        mod._run_one_eval_turn(
            agent_id=str(agent.id),
            conn_str=PRODUCTION,
            run_id=RUN_ID,
            question="Q",
            prompt_version_id=None,
        )

    kwargs = factory.call_args.kwargs
    assert kwargs["job_id"] == RUN_ID, (
        f"every model call of this eval turn is stamped job_id={kwargs.get('job_id')!r}. "
        "A synthesised id names no job, so the run's agent spend cannot be found "
        "from the run, and it is indistinguishable from live customer traffic."
    )
    assert kwargs["tenant_id"] == str(agent.tenant_id)
    assert kwargs["agent_id"] == str(agent.id)


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

    async def fake_loop(*args, **kwargs):
        return _turn()

    with (
        patch.object(mod, "get_sync_db", _db_ctx(db)),
        patch(
            "app.worker.tasks.runtime.agent._resolve_turn_prompt_version",
            return_value=(pv_id, dict(soul), False),
        ) as resolve,
        patch(
            "app.services.agent_loop.build_agent_turn",
            return_value=_Options(),
        ) as seam,
        patch(
            "app.services.agent_loop.run_agent_loop",
            side_effect=fake_loop,
        ),
    ):
        mod._run_one_eval_turn(
            agent_id=str(agent.id),
            conn_str=PRODUCTION,
            run_id=RUN_ID,
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

    `run_agent_loop` emits `agent.tool_call` / `agent.tool_result` through the db
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

    async def fake_loop(*args, **kwargs):
        seen.update(kwargs)
        return _turn()

    with (
        patch.object(mod, "get_sync_db", _db_ctx(db)),
        patch(
            "app.services.agent_loop.build_agent_turn",
            return_value=_Options(),
        ),
        patch(
            "app.services.agent_loop.run_agent_loop",
            side_effect=fake_loop,
        ),
    ):
        mod._run_one_eval_turn(
            agent_id=str(agent.id),
            conn_str=PRODUCTION,
            run_id=RUN_ID,
            question="Q",
            prompt_version_id=None,
        )

    sink = seen["db"]
    assert seen["redis"] is sink
    assert isinstance(sink, mod._EvalEventSink), (
        f"the eval turn was handed {sink!r} as its event sink. A real Session "
        "here writes job_events rows under a job id that names no job."
    )

    # emit() IS RUN AGAINST THE SINK, not merely poked at method by method.
    # Asserting isinstance and that three named methods return None proves the
    # sink's own surface and nothing about the COUPLING: emit currently uses
    # publish / add / commit, and the day it gains a db.flush() or a
    # db.refresh(event) every eval turn raises AttributeError inside
    # run_agent_loop, all sixty scenarios count as failed, and the eval silently
    # stops measuring anything with only the invocation counters to say why.
    from app.services.events import emit

    emit("job-1", "agent.tool_call", {"tool_name": "retrieve"}, sink, sink)
    emit("job-1", "agent.response", {"text": "hi"}, sink, sink)


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

    def _fake_turn(*, agent_id, conn_str, run_id, question, prompt_version_id):
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
            run_id=RUN_ID,
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


def test_a_scenario_the_agent_answered_without_retrieving_never_reaches_the_scorer():
    """The case every other context test was blind to, and the one a fallback
    to the stored column would fire in.

    Faithfulness, ContextPrecision and ContextRecall over an EMPTY context list
    are structurally 0 or NaN. An agent that answers "what are your opening
    hours?" from its system prompt with no retrieve call has done nothing wrong,
    and scoring it produces a 0 that reads as a grounding collapse — the same
    "zero is not a low score" error the failure path already refuses, one metric
    over. It is excluded and counted in its own bucket, and it is NOT a failure:
    it does not depress `response_rate`, because the agent did respond.

    The second assertion is the D1 one: with the row gone, nothing can quietly
    substitute the chunks the reference answer was WRITTEN from.
    """
    scenarios = _scenarios(4)

    def _turn_for(question):
        if question == "Question 2?":
            return _turn(f"ANSWER to {question}", contexts=[])
        return _turn(f"ANSWER to {question}", contexts=[f"CTX for {question}"])

    rows, summary, calls = _invoke(scenarios, _turn_for)

    assert len(calls) == 4
    assert [r["id"] for r in rows] == ["s0", "s1", "s3"], (
        f"the no-retrieval scenario reached the scorer: {[r['id'] for r in rows]}"
    )
    assert summary["responded"] == 4, "a no-retrieval answer is still an answer"
    assert summary["failed"] == 0 and summary["empty"] == 0
    assert summary["no_retrieval"] == 1
    assert summary["scorable"] == 3
    assert summary["response_rate"] == 1.0, (
        "answering without retrieving was counted as a failure to answer"
    )
    for row in rows:
        assert row["retrieved_contexts"], (
            f"scenario {row['id']} was scored against an empty context list"
        )
        assert row["retrieved_contexts"] != row["stored_retrieved_contexts"], (
            "the stored column reached the scorer — that is D1's second half"
        )


def test_a_retrieve_result_that_cannot_be_read_is_counted_apart_from_no_retrieval():
    """"Retrieved nothing" and "retrieved something unreadable" are different.

    agent.py decodes the framed retrieve payload back into per-chunk strings; a
    payload it cannot parse yields no chunks. Reporting that as `no_retrieval`
    would say the agent never called retrieve when it did, and would hide a
    decode regression behind a plausible behavioural explanation — missing data
    dressed as observed data.
    """
    scenarios = _scenarios(3)

    def _turn_for(question):
        if question == "Question 1?":
            return _turn(f"ANSWER to {question}", contexts=["ctx"], unparsed=True)
        return _turn(f"ANSWER to {question}", contexts=[f"CTX {question}"])

    rows, summary, _calls = _invoke(scenarios, _turn_for)

    assert [r["id"] for r in rows] == ["s0", "s2"]
    assert summary["retrieved_context_unparsed"] == 1
    assert summary["retrieved_nothing_scorable"] == 1
    assert summary["no_retrieval"] == 0, (
        "an unreadable retrieve result was reported as the agent not "
        "retrieving at all"
    )
    assert summary["scorable"] == 2


def test_a_run_with_too_few_scored_rows_is_unknown_however_good_its_rate_is():
    """THE ABSOLUTE FLOOR — compute_correlation.py's MIN_PAIRS, restored here.

    A rate alone cannot refuse a one-observation run: one labelled scenario, one
    answer, response_rate 1.0, and the run certifies a deploy off a single
    observation. That is the class MIN_PAIRS exists to refuse, and the reason
    given for omitting it — "the denominator travels, so a consumer can apply
    its own floor" — was wrong about the only consumer there is: the deploy gate
    reads `agent_invoked`, which is computed here.
    """
    floor = eval_service.MIN_SCORED_OBSERVATIONS
    below = _scenarios(floor - 1)
    _rows, summary, _calls = _invoke(below, lambda q: _turn(f"A to {q}"))

    assert summary["response_rate"] == 1.0
    assert summary["scorable"] == floor - 1
    assert summary["status"] == eval_service.AGENT_INVOCATION_UNKNOWN, (
        f"a run of {floor - 1} observations at a perfect response rate "
        "certified itself as a measurement"
    )
    assert eval_service.invocation_provenance(summary)["agent_invoked"] is False

    # …and exactly one more observation clears it, so the floor is a floor and
    # not an unconditional refusal.
    _rows, summary, _calls = _invoke(
        _scenarios(floor), lambda q: _turn(f"A to {q}")
    )
    assert summary["scorable"] == floor
    assert summary["status"] == eval_service.AGENT_INVOCATION_MEASURED


def test_the_run_reports_coverage_against_what_the_tenant_designated():
    """A second, explicit denominator — the one the ceiling hides.

    `response_rate` divides by what the ceiling ALLOWED, so a tenant with 200
    labelled rows whose first 60 all answer reports 1.0 while 140 rows were
    never put to the agent. compute_correlation.py:498 divides by `valid`; this
    module's rate quietly did not, so the coverage figure is carried beside it
    rather than folded into it.
    """
    ceiling = eval_service.AGENT_INVOCATION_MAX_CALLS_PER_RUN
    _rows, summary, _calls = _invoke(
        _scenarios(ceiling + 40), lambda q: _turn(f"A to {q}")
    )

    assert summary["response_rate"] == 1.0
    assert summary["valid"] == ceiling + 40
    assert summary["coverage_rate"] == pytest.approx(ceiling / (ceiling + 40)), (
        "the run reports a perfect response rate and no figure that shows 40 "
        "labelled rows were never asked"
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

    def _boom(question):
        raise RuntimeError("every scenario failed")

    # try/finally, because a ContextVar set here is PROCESS-WIDE for the rest of
    # this worker. If the loop ever stops resetting — the regression this test
    # guards — the assertion below fails AND leaves every subsequent test in the
    # same process running in recorded mode, so the first real failure would
    # arrive buried in a cascade of unrelated ones.
    agent_tools._side_effects_var.set("recorded")
    try:
        _invoke(_scenarios(2), _boom)

        assert agent_tools.current_side_effect_mode() == "live", (
            "the invocation loop left the process context in recorded mode"
        )
    finally:
        agent_tools.reset_side_effect_context()


def test_an_attempt_is_attributed_to_the_scenario_that_made_it_and_no_other():
    """The sink is emptied BEFORE each turn, not only inside build_agent_turn.

    Everything `_run_one_eval_turn` does before it reaches the seam can raise:
    get_sync_db(), the agent row lookup, _resolve_turn_prompt_version. The
    unconditional read after the turn then returned the PREVIOUS scenario's sink
    a second time — so a scenario that drove a refund attempt, followed by a
    scenario whose control-DB session blipped, produced TWO transactional.adapter
    entries, the second carrying the second scenario's id for an attempt it never
    made. A fabricated observation, in the exact confusion-matrix cell the
    recording exists to populate.

    Driven against the REAL agent_tools sink rather than a per-question double:
    the doubled version of this in
    test_an_attempt_made_by_a_scenario_that_then_failed_is_still_recorded cannot
    see the defect, because a double that returns a fresh list per question is
    already doing the reset the code was missing.
    """
    from app.services import agent_tools

    def _turn_for(*, agent_id, conn_str, run_id, question, prompt_version_id):
        if question == "Question 0?":
            agent_tools.record_suppressed_side_effect(
                "transactional.adapter", {"skill": "issue_refund", "amount": 40.0}
            )
            return _turn(f"ANSWER to {question}")
        # Scenario 1 dies BEFORE the seam — so nothing resets the sink for it.
        raise RuntimeError("agent row disappeared mid-run")

    agent_tools.reset_side_effect_context()
    try:
        with patch.object(mod, "_run_one_eval_turn", side_effect=_turn_for):
            _rows, summary = mod._invoke_agent_for_scenarios(
                agent_id="agent-1",
                conn_str=PRODUCTION,
                run_id=RUN_ID,
                scenarios=_scenarios(2),
                prompt_version_id=None,
            )
    finally:
        agent_tools.reset_side_effect_context()

    recorded = summary["side_effect_attempts"]
    assert recorded["counts"] == {"transactional.adapter": 1}, (
        f"the run recorded {recorded['counts']} for one refund attempt — the "
        "scenario that died before the seam re-read the previous scenario's sink"
    )
    attributed = [a["scenario_id"] for a in recorded["capability_attempts"]]
    assert attributed == ["s0"], (
        f"the refund attempt is attributed to {attributed}. s1 never reached the "
        "agent, so an entry naming it is an observation the run invented."
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
    truncation — measured at the boundary that actually cuts the evidence.

    Faithfulness over a context that was CUT marks a claim unsupported when the
    support was merely beyond the cap. A reader of a low faithfulness score has
    to be able to see the cap without going to a different module at a different
    commit to find it.

    THE CAP THAT IS REPORTED IS THE ONE THAT BINDS. `retrieved_context_at_cap`
    used to be derived from RETRIEVE_RESULT_CAPTURE_CHARS (1800), the cap on the
    AUDIT copy of the tool result — which five 2000-char chunks exceed by
    construction, so the figure was ~100% on every retrieving turn and was a
    constant wearing an observation's name. It is now derived from
    agent_tools.CHUNK_CONTENT_CHAR_LIMIT, the per-chunk cap on the text the
    judge is given, where being at the boundary really is evidence of a cut.
    """
    from app.services.agent_loop import RETRIEVE_RESULT_CAPTURE_CHARS
    from app.services.agent_tools import CHUNK_CONTENT_CHAR_LIMIT
    from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S

    scenarios = _scenarios(3)
    at_cap = "x" * CHUNK_CONTENT_CHAR_LIMIT

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
    assert summary["retrieved_context_chunk_char_cap"] == CHUNK_CONTENT_CHAR_LIMIT
    assert summary["audit_capture_char_cap"] == RETRIEVE_RESULT_CAPTURE_CHARS
    assert summary["retrieved_context_source"] == "agent_retrieve_chunks"
    assert summary["retrieved_context_chunks"] == 3
    assert summary["retrieved_context_unparsed"] == 0
    assert summary["retrieved_context_at_cap"] == 1, (
        f"{summary['retrieved_context_at_cap']} of 3 turns reported a chunk at "
        "the cap. One chunk was built at exactly the per-chunk cap and two were "
        "short — a figure that is not 1 here is either blind to the cut or "
        "true by construction, and the second is what it was before."
    )
    assert summary["pii_firewall_applied"] is False, (
        "the eval scores the agent's own text, not the deflection a customer "
        "would receive; that difference is stated on the run, not implied"
    )


def test_a_run_that_only_ever_saw_short_chunks_reports_none_at_the_cap():
    """The complement, and the reason the test above is not a tautology.

    A guard that only ever observes the TRUE case cannot tell a constant from an
    observation, and the figure it replaced WAS a constant.
    """
    _rows, summary, _calls = _invoke(
        _scenarios(3), lambda q: _turn(f"A to {q}", contexts=["short chunk"])
    )

    assert summary["responded"] == 3 and summary["scorable"] == 3
    assert summary["retrieved_context_at_cap"] == 0, (
        "every turn reported a context at the cap while no chunk was anywhere "
        "near it — the figure is a constant, not an observation"
    )


def test_a_full_retrieval_of_uncut_chunks_is_not_reported_as_truncated():
    """THE PRODUCTION SHAPE, and the only fixture that separates the two caps.

    `retrieved_context_at_cap` used to be `len(result) >= 1800` over the AUDIT
    capture — a repr of the whole tool-result block. Five chunks of up to 2000
    chars each blow past 1800 whatever they contain, so the figure was 1 on
    essentially every retrieving turn: a constant wearing an observation's name,
    reported beside a low faithfulness score as if it explained it.

    This is that case and only that case: three 700-char chunks. The repr of
    them is far over the 1800-char audit cap (so the OLD derivation says
    "truncated") while every chunk is far under the 2000-char per-chunk cap that
    can actually cut evidence (so the NEW one says "nothing was cut"). Nothing
    was cut. The short-chunk and at-cap tests above cannot tell the two apart —
    for a single short chunk the audit repr is short too, and for a 2000-char
    chunk both caps trip.
    """
    from app.services.agent_loop import RETRIEVE_RESULT_CAPTURE_CHARS
    from app.services.agent_tools import CHUNK_CONTENT_CHAR_LIMIT

    chunks = ["c" * 700, "d" * 700, "e" * 700]
    for chunk in chunks:
        assert len(chunk) < CHUNK_CONTENT_CHAR_LIMIT

    entry = _retrieve_entry(chunks)
    assert len(entry["result"]) >= RETRIEVE_RESULT_CAPTURE_CHARS, (
        "this fixture does not reproduce the production shape: the audit "
        "capture must be AT its cap while no chunk is at the per-chunk cap, or "
        "the two derivations are indistinguishable here"
    )

    _rows, summary, _calls = _invoke(
        _scenarios(3), lambda q: _turn(f"A to {q}", contexts=chunks)
    )

    assert summary["scorable"] == 3
    assert summary["retrieved_context_chunks"] == 9
    assert summary["retrieved_context_at_cap"] == 0, (
        f"{summary['retrieved_context_at_cap']} of 3 turns were reported as "
        "hitting the context cap while every chunk came back whole. That is the "
        "audit capture's 1800-char bound being read as the evidence bound — a "
        "faithfulness miss would be excused by a truncation that never happened."
    )


def _bound_consuming_positions(tree: ast.AST) -> list[ast.Constant]:
    """Every integer constant used AS A BOUND: a slice upper, or a `timeout=`.

    Scoped deliberately. The first version of the guard below forbade the bare
    integers 1800 and 90 anywhere in agent.py, so an unrelated future literal —
    `if latency_ms > 90`, a 90-day retention window, a `[:1800]` truncation of
    something else — would fail it for a reason unconnected to the bound, and
    the pressure would be to rename the innocent literal rather than to preserve
    the invariant. These two syntactic positions are exactly where a SECOND copy
    of a turn bound would do damage.
    """
    found: list[ast.Constant] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            upper = node.slice.upper
            if isinstance(upper, ast.Constant) and type(upper.value) is int:
                found.append(upper)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in ("timeout", "connect_timeout") and isinstance(
                    kw.value, ast.Constant
                ):
                    if type(kw.value.value) is int:
                        found.append(kw.value)
    return found


def _module_tree(module_name: str) -> ast.Module:
    """The syntax tree of one already-imported app module, by dotted name."""
    module = importlib.import_module(module_name)
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def _module_constant(tree: ast.AST, name: str) -> tuple[int, int]:
    """(value, lineno) of a module-scope `NAME = <int>` assignment. Exactly one."""
    defined = [
        (node.value.value, node.lineno)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    assert len(defined) == 1, (
        f"{name} is not defined exactly once at module scope (found {defined}) "
        "— the eval imports it by that name"
    )
    return defined[0]


#: Where each turn bound is DEFINED, and therefore where its one copy must live.
#: ADR 0008 split them: the retrieve capture belongs to the loop that writes it,
#: and the wall-clock ceiling belongs to the Celery task that enforces it.
TURN_BOUNDS = [
    ("app.services.agent_loop", "RETRIEVE_RESULT_CAPTURE_CHARS"),
    ("app.worker.tasks.runtime.agent", "AGENT_TURN_TIMEOUT_S"),
]


@pytest.mark.parametrize("module_name, constant_name", TURN_BOUNDS)
def test_the_turn_bounds_are_read_from_one_copy_of_the_number(module_name, constant_name):
    """One copy of each number, in its owning module, read by that module.

    A second literal is the audit's D3 defect wearing new clothes: the deploy
    gate's eval query fails open to this day because one call site kept its own
    copy of a column name. If the retrieve capture is retuned or the turn
    timeout moves, this run's provenance has to move with it or every run
    reports a bound it did not run under.

    ANTI-TAUTOLOGY NOTE. The first version of this test read the source as text
    and asserted the constant's NAME appeared and `[:1800]` did not. Both halves
    were satisfied by prose: the name appears in the comment above the slice, and
    a reformatted literal is not the substring `[:1800]`. Mutating the slice back
    to a literal left it green, a guard demonstrated only inside the complement
    of its own blind spot (BACKLOG 3.3's defect class). It reads the syntax tree
    now, where a comment does not exist and formatting cannot hide an integer.
    """
    tree = _module_tree(module_name)
    value, _lineno = _module_constant(tree, constant_name)

    literals = [
        node for node in _bound_consuming_positions(tree) if node.value == value
    ]
    assert literals == [], (
        f"the integer {value} is used as a BOUND (a slice upper or a timeout= "
        f"argument) in {module_name} at line(s) {[n.lineno for n in literals]} "
        f"instead of through {constant_name}. Two copies of a bound, one of "
        "which will move first, and the run's provenance will keep reporting "
        "the other."
    )

    names = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == constant_name
        and isinstance(node.ctx, ast.Load)
    ]
    assert names, (
        f"nothing in {module_name} READS {constant_name}, so it is defined for "
        "the eval's benefit and no longer bounds the turn it describes"
    )


@pytest.mark.parametrize("module_name, constant_name", TURN_BOUNDS)
def test_the_eval_imports_the_turn_bounds_rather_than_restating_them(
    module_name, constant_name
):
    """…AND THE EVAL IMPORTS IT — the half the docstring above claimed and
    nothing tested.

    The guard above reads the owning module only. Replacing eval.py's
    `from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S` with a
    local `AGENT_TURN_TIMEOUT_S = 90` left it green, and left
    test_the_bounds_the_run_ran_under_are_on_the_run green too — that test
    imports the constant from agent.py and compares 90 to 90. The second copy is
    exactly the D3 shape the first guard's own docstring cites.
    """
    value, _lineno = _module_constant(_module_tree(module_name), constant_name)

    eval_tree = _eval_tree()

    redefined = [
        node.lineno
        for node in ast.walk(eval_tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == constant_name for t in node.targets)
    ]
    assert redefined == [], (
        f"eval.py assigns its own {constant_name} at line(s) {redefined}. The "
        f"run's provenance would then report a bound {module_name} is not "
        "enforcing."
    )

    literals = [
        node for node in _bound_consuming_positions(eval_tree) if node.value == value
    ]
    assert literals == [], (
        f"the integer {value} is used as a bound in eval.py at line(s) "
        f"{[n.lineno for n in literals]} rather than through {constant_name}."
    )

    imported = [
        node.lineno
        for node in ast.walk(eval_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == module_name
        and any(alias.name == constant_name for alias in node.names)
    ]
    assert imported, (
        f"eval.py does not import {constant_name} from {module_name}. Either it "
        "stopped stamping the bound on the run, or it grew its own copy of the "
        "number."
    )


def test_the_broker_lets_a_run_reach_the_ceiling_the_run_advertises():
    """A run cannot claim a bound the transport will not let it reach.

    `max_wall_clock_s` on every run is AGENT_INVOCATION_MAX_CALLS_PER_RUN x
    AGENT_TURN_TIMEOUT_S = 5400 s. The broker's visibility_timeout was 3600 s,
    with a comment reasoning about provisioning taking 60 s — so a run that
    actually consumed the ceiling it stamps on itself was REDELIVERED at 60
    minutes and a second worker began driving the same agent concurrently. The
    run's own record described a bound the transport refused.

    A relation, not a copy: celery_app cannot import eval_service (ragas,
    instructor and anthropic at module scope, in a module every task and the API
    process imports), so the two numbers live apart and this is what stops them
    drifting.
    """
    from app.worker.celery_app import BROKER_VISIBILITY_TIMEOUT_S, celery_app
    from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S

    worst_case = (
        eval_service.AGENT_INVOCATION_MAX_CALLS_PER_RUN * AGENT_TURN_TIMEOUT_S
    )
    assert BROKER_VISIBILITY_TIMEOUT_S > worst_case, (
        f"visibility_timeout is {BROKER_VISIBILITY_TIMEOUT_S}s and one eval run "
        f"may legitimately take {worst_case}s. Redis redelivers the message at "
        "the timeout and a second worker runs the same agent's whole scenario "
        "set concurrently — double the bill, two eval_runs rows, and the "
        "duplicate is invisible from either row."
    )
    assert (
        celery_app.conf.broker_transport_options["visibility_timeout"]
        == BROKER_VISIBILITY_TIMEOUT_S
    ), "the configured transport option is not the constant this test pins"


def test_the_idempotency_window_covers_a_run_that_uses_its_whole_ceiling():
    """The other half of the same defect.

    The guard skips a dispatch when a 'running' eval_runs row for this agent is
    younger than the window. At a flat 10 minutes against a 90-minute worst
    case, a redelivered or manually re-triggered run 20 minutes in saw no recent
    row and started a SECOND concurrent invocation — the guard was in place and
    could not fire.
    """
    import re

    source = _EVAL_PY.read_text(encoding="utf-8")
    assert "INTERVAL '10 minutes'" not in source, (
        "the eval_runs idempotency window is still a flat 10 minutes, which a "
        "run consuming its own ceiling exceeds nine times over"
    )
    assert re.search(r"INTERVAL '1 second'", source), (
        "the window is no longer expressed in seconds, so it cannot be derived "
        "from the two bounds the run is stamped with"
    )

    from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S

    window = (
        eval_service.AGENT_INVOCATION_MAX_CALLS_PER_RUN
        * mod._agent_turn_timeout_s()
        + eval_service.EVAL_RUN_IDEMPOTENCY_SLACK_S
    )
    assert mod._agent_turn_timeout_s() == AGENT_TURN_TIMEOUT_S
    assert window > (
        eval_service.AGENT_INVOCATION_MAX_CALLS_PER_RUN * AGENT_TURN_TIMEOUT_S
    ), "the idempotency window does not cover a run that uses its whole ceiling"


def test_the_loop_refuses_to_run_if_the_concurrency_bound_moves_without_it():
    """The provenance says concurrency=1 and the loop is sequential. A run whose
    record claims a bound nothing enforced is this phase's whole subject, so the
    disagreement raises instead of being recorded."""
    with patch.object(mod, "AGENT_INVOCATION_CONCURRENCY", 4):
        with pytest.raises(RuntimeError, match="AGENT_INVOCATION_CONCURRENCY"):
            mod._invoke_agent_for_scenarios(
                agent_id="agent-1",
                conn_str=PRODUCTION,
                run_id=RUN_ID,
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

    # FOUR rows, not two: eval_service.MIN_SCORED_OBSERVATIONS is the absolute
    # floor under a measurement and a two-row run is below it, so a two-row
    # fixture would put every test here on the fail-closed branch.
    rows = [
        ("s0", "generated", "Question 0?", "Reference 0.", ["STORED 0"], "golden"),
        ("s1", "generated", "Question 1?", "Reference 1.", ["STORED 1"], None),
        ("s2", "generated", "Question 2?", "Reference 2.", ["STORED 2"], "golden"),
        ("s3", "generated", "Question 3?", "Reference 3.", ["STORED 3"], None),
    ]

    class _Cursor:
        def __init__(self):
            self._last: list = []

        def execute(self, sql, params=None):
            if "FROM eval_scenarios" not in sql:
                self._last = []
            elif "dataset = %(golden)s" in sql:
                self._last = [rows[0], rows[2]]
            else:
                self._last = [rows[1], rows[3]]

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
        lambda scenarios, ledger: (
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

    def _fake_turn(*, agent_id, conn_str, run_id, question, prompt_version_id):
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
    assert len(samples) == 4
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
        "the provenance was written somewhere other than production, so the "
        "run record and the claim about it live in different databases "
        "(audit D2)"
    )
    assert patch_dict["agent_invoked"] is True
    assert patch_dict["scored_response_source"] == "agent_response"
    assert patch_dict["dimensions_not_exercised"] == []
    assert patch_dict["agent_invocation"]["responded"] == 4
    assert result["invocation_recorded"] is True


def test_a_run_below_the_floor_writes_no_scores_and_so_cannot_report_a_pass(
    task_wired, monkeypatch
):
    """THE FAIL-CLOSED WINDOW, closed where the observation is.

    'A run below the floor reports unknown, never pass' was true of
    `config["agent_invocation"]["status"]` and `config["agent_invoked"]` — and
    NOTHING outside eval_service reads either. Everything a consumer does read
    reported a pass: the surviving rows were scored, write_eval_results wrote
    them, update_eval_run_status marked the run 'complete', and
    deployment_service._fetch_eval_summary_sync built a non-empty `pass_rates`
    from them and returned EVAL_SIGNAL_MEASURED. So a run in which 3 of 4
    scenarios timed out shipped on one observation.

    The gate learning to read `agent_invoked` is P3. Until then the refusal is
    here: below the floor, nothing reaches the scorer and nothing is written, so
    the gate finds no eval_results and returns EVAL_SIGNAL_NO_VALID_SCORES,
    which apply_signal_evidence_gate already refuses.
    """
    statuses: list[tuple] = []
    monkeypatch.setattr(
        mod,
        "update_eval_run_status",
        lambda run_id, status, finished_at, conn_str: statuses.append(
            (status, conn_str)
        ),
    )
    written: list = []
    monkeypatch.setattr(
        mod,
        "write_eval_results",
        lambda run_id, scores, conn_str: written.append((run_id, scores, conn_str)),
    )

    def _mostly_dead(*, agent_id, conn_str, run_id, question, prompt_version_id):
        if question == "Question 0?":
            return _turn(f"AGENT ANSWER to {question}", contexts=["CTX"])
        raise TimeoutError("SDK subprocess never answered")

    monkeypatch.setattr(mod, "_run_one_eval_turn", _mostly_dead)

    result = _run_task()

    assert result["agent_invoked"] is False
    assert result["agent_invocation"]["status"] == eval_service.AGENT_INVOCATION_UNKNOWN
    assert task_wired["scored"] == [], (
        "run_ragas_eval was called for a run that did not measure the agent — "
        "and a judge bill was incurred to produce a number nobody may use"
    )
    assert written == [], (
        "eval_results rows were written for a below-floor run. "
        "deployment_service builds pass_rates from exactly those rows and "
        "returns EVAL_SIGNAL_MEASURED, so the deploy ships on one observation."
    )
    # …and the run still ENDS, terminally, on production, carrying its reason.
    assert statuses == [("complete", PRODUCTION)], (
        f"the run's terminal status writes were {statuses}. A run that measured "
        "too little still has to finish, or production cannot tell it from a "
        "hung one — which is audit D2."
    )
    assert task_wired["patched"], "the invocation observation was never recorded"
    _run_id, patch_dict, _conn = task_wired["patched"][0]
    assert patch_dict["agent_invoked"] is False
    assert patch_dict["agent_invocation"]["responded"] == 1
    assert patch_dict["agent_invocation"]["failed"] == 3


def test_a_run_where_nothing_reached_the_scorer_does_not_claim_agent_sourced_scores(
    task_wired, monkeypatch
):
    """`scored_response_source` is a claim about a SET, so it needs the set.

    Derived from `attempted`, it said "the scored responses came from the agent"
    about a run where every turn raised and nothing was scored at all — a claim
    about rows that do not exist, which a future consumer could read as evidence
    of an agent-sourced measurement.
    """
    def _all_dead(*, agent_id, conn_str, run_id, question, prompt_version_id):
        raise TimeoutError("SDK subprocess never answered")

    monkeypatch.setattr(mod, "_run_one_eval_turn", _all_dead)

    _run_task()

    _run_id, patch_dict, _conn = task_wired["patched"][0]
    assert patch_dict["agent_invocation"]["attempted"] == 4
    assert patch_dict["agent_invocation"]["scorable"] == 0
    assert patch_dict["scored_response_source"] == (
        eval_service.EVAL_RESPONSE_SOURCE_NONE_SCORED
    ), (
        "a run that scored nothing reported "
        f"{patch_dict['scored_response_source']!r} — a statement about the "
        "provenance of an empty set"
    )
    assert patch_dict["scored_response_source"] != (
        eval_service.EVAL_RESPONSE_SOURCE_PENDING
    ), "the invocation phase DID run; 'pending' would say it had not"


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
        lambda scenarios, ledger: order.append("score")
        or {"scores": [], "means": {}, "sent": 0, "returned": 0, "unattributed": 0},
    )

    _run_task()

    assert order == [
        "insert",
        "turn",
        "turn",
        "turn",
        "turn",
        "patch",
        "score",
    ], (
        f"the run's write order is {order}. 'insert' must precede every 'turn' "
        "(the row is the idempotency key), and 'patch' must precede 'score' (a "
        "judge outage must not take the record of what the agent did with it)."
    )

