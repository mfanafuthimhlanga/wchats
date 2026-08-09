"""
D6 P3 — what a label actually does once it exists.

P1 gave a label its own trust tier and walled the writer off from every model.
P2 built the queue that offers unlabelled rows and the route that writes one.
Neither said what changes downstream, and "the owner can now label a row" is
worth nothing until three separate claims are true and pinned:

  1. THE LABELLED ROW ENTERS THE EVAL. Before the label the row is inert by
     construction — `run_eval_suite`'s two selectors both carry
     `reference_answer != ''`, which is the whole reason a mined production
     failure is stored with an empty answer. Writing the answer is what makes
     the row fetchable, and nothing else about the row changes.

  2. IT DOES NOT JOIN THE GOLDEN SET. Membership of the golden set is an
     assertion somebody makes (`dataset = 'golden'`), never something a row
     inherits by becoming eligible — `dataset_of` and eval.py's row builder both
     say so, and the label write must not quietly say otherwise. The golden half
     runs UNSAMPLED every night precisely so consecutive runs are a paired
     per-item comparison; a row that joined it by side effect would change the
     set the comparison is over, and the run report has no way to say that
     happened.

  3. NOTHING AN OWNER LABELS REACHES A CUSTOMER. Settled by the owner on
     2026-08-08: this loop is EVAL-ONLY. `verified_qa` rows are served by
     `retrieval_service.verified_qa_lookup` AHEAD of retrieval, so one mistyped
     label would be answered to a real person with no eval between the typo and
     them.

THE THIRD CLAIM IS THE ONE THAT CHANGED SHAPE IN D6, AND IT IS WHY THIS FILE
EXISTS RATHER THAN A COMMENT. Before D6, promotion was unreachable because
`VERIFIED_QA_MIN_TRUST_TIER = 'human_verified'` sat above the ceiling of
anything the system could produce — a fact about the world, needing no flag.
D6 built a producer of `human_authored`, rank 3, which clears that minimum. So
the old guarantee is spent, and what holds the door now is THREE things, listed
strongest first — the first draft of this file named two and left out the one
carrying the load (D6 P3 review, finding 4):

    LOCK ZERO    — `promote_to_verified_qa` has no caller anywhere under `app/`.
                   `run_eval_suite` returns a literal `promoted: 0`. This is
                   what is actually holding the door today; the two below are
                   defence in depth behind it. Pinned in two modules
                   (test_eval_task.py, test_eval_service.py) and only those two.
    the RESOLVER — `select_promotion_candidates` gates on `eval_scenarios.
                    source`, the QUESTION's origin, which labelling never
                    touches. Swapping it to `label_trust_tier()` — which is the
                    resolver P1 argues is the RIGHT one for reasoning about an
                    answer — is the "obvious fix" that reads like a bug fix, but
                    it is INERT today: no selector projects `label_trust_tier`,
                    so no production scenario dict carries it and every row
                    falls through to the source-based tier either way.
                    `BACKLOG 4.12` is the change that makes it live.
    the DECISION  — `VERIFIED_QA_PROMOTION_DECISION["enabled"]`, consulted last
                    so that a row it refuses is COUNTED under its own reason.
                    Its refusal count is NOT a measurement of "what flipping the
                    decision would promote": the resolver gate refuses every
                    schema-allowed source first, so it is structurally 0 (D6 P3
                    review, finding 1).

The resolver and the decision are pinned below, separately, because a wall with
two bricks that fail together is a wall with one brick. Both live in mutable
module state, which is why `eval_service` now holds them as `MappingProxyType`
and why `TestTheLocksAreNotOneAssignmentAway` scans for the rebinds a proxy
cannot stop.

WHAT IS NOT PROVEN HERE, PLAINLY. There is no PostgreSQL server on this machine.
Migration 0016 has not been applied and cannot be; no `eval_scenarios` row has
ever carried a real `label_trust_tier`; every `-m integration` harness skips and
a skip is unobserved, never a pass. The database boundary is a double in every
test below, the SQL is asserted at the string level, and the task is driven
in-process. What is proven is the arithmetic, the SQL's shape and the gates —
not that Postgres accepts any of it.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
from contextlib import contextmanager
from types import MappingProxyType
from unittest.mock import MagicMock

import psycopg2
import pytest

from app.services import deployment_service
from app.services import eval_service
from app.worker.tasks.runtime import eval as mod


def _with_source_tier(monkeypatch, source: str, tier: str) -> None:
    """Register a hypothetical scenario source at *tier* for one test.

    `SCENARIO_SOURCE_TRUST_TIER` is a `MappingProxyType`, so `setitem` raises —
    that is the point of it (D6 P3 review, finding 4: the lock used to be a
    plain module dict any code in the process could open with one assignment).
    A test that needs a promotable tier replaces the whole mapping instead,
    which monkeypatch reverses by rebinding, not by mutating shared state.
    """
    monkeypatch.setattr(
        eval_service,
        "SCENARIO_SOURCE_TRUST_TIER",
        MappingProxyType({**eval_service.SCENARIO_SOURCE_TRUST_TIER, source: tier}),
    )


def _with_promotion_enabled(monkeypatch) -> None:
    """Flip the decision flag for one test, the same way and for the same
    reason as `_with_source_tier` above."""
    monkeypatch.setattr(
        eval_service,
        "VERIFIED_QA_PROMOTION_DECISION",
        MappingProxyType(
            {**eval_service.VERIFIED_QA_PROMOTION_DECISION, "enabled": True}
        ),
    )

PRODUCTION = "postgresql://production/tenant"
BRANCH = "postgresql://neon-branch/tenant"

# The tier the shipped label writer stamps, read out of the RUN RECORD's own
# statement of it rather than imported from the writer.
#
# THAT IS NOT A STYLE CHOICE. R2 in test_label_provenance.py refuses any module
# in this tree but two — the writer and the one API module that calls it — from
# so much as NAMING the writer's module or function, in a string constant
# included, because `import_module("app.services." + ...)` is how the earlier
# full-dotted-path version of that scan was evaded. It caught the first draft of
# this file, which is the only evidence worth having that it still works. The
# record and the writer are pinned equal in the one test module allowed to see
# both (test_the_tier_the_writer_stamps_is_the_tier_the_run_record_names), so
# this indirection costs nothing and keeps the wall whole.
HUMAN_AUTHORED = eval_service.VERIFIED_QA_PROMOTION_DECISION["producible_label_tier"]

# The row an owner has just labelled: a MINED question (so its origin is
# `customer_negative`, unchanged by the label) carrying an answer the owner
# wrote, and NO dataset designation — because the label write does not make one.
LABELLED_ID = "aaaaaaaa-0000-0000-0000-00000000000a"
OWNER_ANSWER = "Yes — within 14 days of delivery, unopened."

_GOLDEN_ROWS = [
    ("g0000000-0000-0000-0000-000000000001", "generated", "GQ1", "GA1", [], "golden"),
    ("g0000000-0000-0000-0000-000000000002", "generated", "GQ2", "GA2", [], "golden"),
]
_EXPLORATORY_ROWS = [
    ("11111111-1111-1111-1111-111111111111", "generated", "Q1", "A1", [], None),
    ("22222222-2222-2222-2222-222222222222", "generated", "Q2", "A2", [], None),
]
_LABELLED_ROW = (LABELLED_ID, "mined", "Do you refund?", OWNER_ANSWER, [], None)


# ---------------------------------------------------------------------------
# Harness — run_eval_suite with every boundary doubled
# ---------------------------------------------------------------------------


class _Cursor:
    """Dispatches on SQL text, like test_eval_task's, so a test can choose which
    selector returns which rows rather than relying on call order."""

    def __init__(self, golden_rows, exploratory_rows):
        self.golden_rows = list(golden_rows)
        self.exploratory_rows = list(exploratory_rows)
        self.executed: list[str] = []
        self._last: list = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if "FROM eval_scenarios" not in sql:
            self._last = []
            return
        if "dataset = %(golden)s" in sql:
            # The golden half is UNSAMPLED — the real query carries no LIMIT.
            self._last = self.golden_rows
            return
        # THE DOUBLE HONOURS `LIMIT %(limit)s` (D6 P3 review, finding 3). It used
        # to return the whole pool, so every test ran below
        # EXPLORATORY_SAMPLE_SIZE and could not tell "a label makes a row
        # ELIGIBLE" from "a label makes a row PRESENT in the run" — which is the
        # difference the docstring was overstating. `ORDER BY RANDOM()` is
        # deliberately NOT emulated: which rows a capped draw returns is the
        # database's business, and no assertion below depends on an identity
        # that only a shuffle could decide.
        limit = (params or {}).get("limit")
        self._last = (
            self.exploratory_rows
            if limit is None
            else self.exploratory_rows[:limit]
        )

    def fetchone(self):
        return None  # no recent 'running' run -> no idempotent skip

    def fetchall(self):
        return self._last

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _sync_db(mock_db):
    @contextmanager
    def _ctx():
        yield mock_db

    return _ctx


_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)


def _wire(monkeypatch, *, exploratory_rows, silent_ids=(), scores_by_id=None):
    """Double every boundary and return the recorder dict.

    `silent_ids` are scenarios the agent fails to answer — they are excluded
    from the scored set and counted in the invocation observation, which is the
    path that makes `valid` and `scored` different numbers.

    `scores_by_id` gives a scenario a score other than the 0.9 default on every
    metric, which is how a labelled row becomes the hard negative the deploy
    gate's pass rates then average in.
    """
    agent = MagicMock()
    agent.neon_project_id = "neon-project-1"
    agent.neon_connection_string = b"encrypted"
    db = MagicMock()
    db.get.return_value = agent

    monkeypatch.setattr(mod, "get_sync_db", _sync_db(db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: PRODUCTION)

    cursor = _Cursor(_GOLDEN_ROWS, exploratory_rows)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

    monkeypatch.setattr(mod, "mine_production_scenarios", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "store_scenarios", lambda *a, **kw: None)

    rec: dict = {
        "cursor": cursor,
        "invoked": [],
        "scored_input": [],
        "status": [],
        # What the run hands to write_eval_results — the rows that become
        # eval_results on PRODUCTION, which is where the deploy gate reads from.
        "results_written": [],
    }

    def _fake_invoke(*, agent_id, conn_str, scenarios, prompt_version_id):
        rec["invoked"].append([s["id"] for s in scenarios])
        answered = [s for s in scenarios if s["id"] not in set(silent_ids)]
        rows = [
            {
                **s,
                # NEVER the reference answer — a fixture that let those two be
                # equal would reinstate audit D1 inside this file's own harness.
                "agent_response": f"AGENT SAID: {s['question']}",
                "retrieved_contexts": [f"CTX for {s['id']}"],
            }
            for s in answered
        ]
        summary = mod.summarise_agent_invocation(
            [
                {
                    "scenario_id": s["id"],
                    "responded": s["id"] not in set(silent_ids),
                    "scorable": s["id"] not in set(silent_ids),
                    "error": None,
                    "retrieve_calls": 1,
                    "retrieve_at_cap": False,
                    "retrieve_unparsed": 0,
                    "retrieved_chunks": 1,
                    "side_effects": [],
                }
                for s in scenarios
            ],
            valid=len(scenarios),
            ceiling_skipped=0,
            ceiling_skipped_golden=0,
            per_turn_timeout_s=90,
            audit_capture_char_cap=1800,
            retrieved_context_chunk_char_cap=2000,
            pii_firewall_applied=False,
        )
        return rows, summary

    monkeypatch.setattr(mod, "_invoke_agent_for_scenarios", _fake_invoke)
    monkeypatch.setattr(
        mod, "update_eval_run_config", lambda run_id, patch, conn_str: True
    )
    monkeypatch.setattr(
        mod,
        "build_eval_run_config",
        lambda agent_id, conn_str, dataset=None: {
            "prompt_version_id": "pv-1",
            "config": {"model_id": "m", "dataset": dataset},
        },
    )
    monkeypatch.setattr(
        mod, "insert_eval_run", lambda run_id, kind, pv, config, conn_str: True
    )
    monkeypatch.setattr(mod, "create_branch", lambda pid, name: ("branch-1", BRANCH))
    monkeypatch.setattr(mod, "wait_for_neon_ready", lambda conn_str: None)

    overrides = dict(scores_by_id or {})

    def _fake_ragas(scenarios):
        rec["scored_input"].append(list(scenarios))
        return {
            "scores": [
                {
                    "scenario_id": s["id"],
                    **{m: overrides.get(s["id"], 0.9) for m in _METRICS},
                }
                for s in scenarios
            ],
            "means": {"faithfulness": 0.9},
        }

    monkeypatch.setattr(mod, "run_ragas_eval", _fake_ragas)
    monkeypatch.setattr(
        mod,
        "write_eval_results",
        lambda run_id, scores, conn: rec["results_written"].append(
            (run_id, list(scores), conn)
        ),
    )
    monkeypatch.setattr(
        mod,
        "update_eval_run_status",
        lambda run_id, status, finished_at, conn_str: rec["status"].append(status),
    )
    monkeypatch.setattr(mod, "delete_branch", lambda pid, bid: None)
    return rec


def _run(agent_id="agent-1"):
    mod.run_eval_suite.push_request(retries=0)
    try:
        return mod.run_eval_suite.run(agent_id)
    finally:
        mod.run_eval_suite.pop_request()


def _before(monkeypatch):
    """The run as it stands with the mined row still unlabelled: the selector
    never returns it, because `reference_answer != ''` excludes it."""
    _wire(monkeypatch, exploratory_rows=_EXPLORATORY_ROWS)
    return _run()


def _after(monkeypatch, silent_ids=()):
    """The same run once the owner has written an answer on that row."""
    _wire(
        monkeypatch,
        exploratory_rows=[*_EXPLORATORY_ROWS, _LABELLED_ROW],
        silent_ids=silent_ids,
    )
    return _run()


def _task_sql(name: str) -> str:
    """One of run_eval_suite's local SQL constants, read out of its source."""
    source = inspect.getsource(mod.run_eval_suite)
    match = re.search(rf'{name} = """(.*?)"""', source, re.DOTALL)
    assert match, f"{name} is no longer a triple-quoted local in run_eval_suite"
    return match.group(1)


# ---------------------------------------------------------------------------
# 1. The labelled row enters the eval
# ---------------------------------------------------------------------------


class TestALabelledRowEntersTheEval:
    """`BACKLOG 2.4`'s "mined scenarios are inert by construction" is only half
    the story: the exclusion is correct, and until D6 nothing could move a row
    out of it. These are the tests that the move actually happens."""

    def test_labelling_makes_a_row_the_selector_returns(self, monkeypatch):
        before = _before(monkeypatch)
        after = _after(monkeypatch)

        assert before["attempted"] == 4
        assert after["attempted"] == 5, (
            "the labelled row was not fetched — an answer written by the owner "
            "that the nightly eval never selects is a label that did nothing"
        )
        assert after["valid"] == 5

    def test_the_labelled_row_is_put_to_the_agent(self, monkeypatch):
        rec = _wire(
            monkeypatch, exploratory_rows=[*_EXPLORATORY_ROWS, _LABELLED_ROW]
        )
        _run()

        assert rec["invoked"], "the agent was never invoked"
        assert LABELLED_ID in rec["invoked"][0]

    def test_the_owners_answer_is_the_reference_and_never_the_prediction(
        self, monkeypatch
    ):
        """The owner's exact text is what reaches the scorer as the LABEL.

        The first assertion is the load-bearing one and it caught a real
        mutation: carrying `row[2]` (the question) instead of `row[3]` through
        eval.py's row builder turns the run into Ragas scoring the agent against
        the question it was asked, silently, on the one row a human took the
        trouble to write.

        THE SECOND ASSERTION IS WEAKER THAN IT LOOKS, AND SAYING SO IS THE POINT.
        It reads like a pin against audit D1 (`agent_response = reference_answer`,
        which made faithfulness approach 1.0 by construction). It is not one.
        `_invoke_agent_for_scenarios` — real or doubled — sets `agent_response`
        from the turn with `{**s, "agent_response": ...}`, so it overwrites
        whatever the row builder put there and reinstating D1 upstream of it
        would not turn this red. It is a fixture sanity check: it fails if this
        module's own double ever starts feeding the scorer a self-answer. D1
        itself is pinned in test_eval_agent_invocation.py and by
        run_eval_for_agent's tautology refusal, which are the two places that
        can see it.
        """
        rec = _wire(
            monkeypatch, exploratory_rows=[*_EXPLORATORY_ROWS, _LABELLED_ROW]
        )
        _run()

        scored = {s["id"]: s for s in rec["scored_input"][0]}
        row = scored[LABELLED_ID]
        assert row["reference_answer"] == OWNER_ANSWER
        assert row["agent_response"] != row["reference_answer"], (
            "the labelled row was scored against its own label"
        )

    def test_the_selector_is_the_only_thing_standing_between_the_two_states(self):
        """The predicate that makes the "before" state above real.

        The two runs differ only in whether one row carries an answer, and that
        is not a property of the fixture — it is the predicate BOTH selectors
        filter on, spelled once in eval_service and asserted here to still be
        the thing eval.py filters on. If the task stopped filtering on it, the
        `_before` fixture would be modelling an exclusion the task no longer
        performs, and every count above would be arithmetic about nothing.
        """
        predicate = eval_service.SELECTOR_ELIGIBILITY_PREDICATE
        for name in ("_GOLDEN_SQL", "_EXPLORATORY_SQL", "_PRE_0014_SQL"):
            assert predicate in _task_sql(name), (
                f"{name} no longer excludes unlabelled rows with {predicate!r}"
            )


# ---------------------------------------------------------------------------
# 1b. ELIGIBLE is not PRESENT — the cap the claim above is only true below
# ---------------------------------------------------------------------------


def _pool(n: int) -> list[tuple]:
    """*n* eligible, unlabelled-by-someone-else exploratory rows."""
    return [
        (
            f"eeeeeeee-0000-0000-0000-{i:012d}",
            "generated",
            f"Q{i}",
            f"A{i}",
            [],
            None,
        )
        for i in range(n)
    ]


class TestLabellingMakesARowEligibleNotPresent:
    """D6 P3 review, finding 3. Every test above runs with a THREE-row
    exploratory pool, so `attempted 4 -> 5` is an artefact of a pool below
    EXPLORATORY_SAMPLE_SIZE and cannot carry the unconditional claim P3 wrote
    into the task docstring, the reference doc and the commit message: "a
    labelled row is fetched, put to the agent and scored".

    `_EXPLORATORY_SQL` is `ORDER BY RANDOM() LIMIT 30`. Above 30 eligible rows a
    label does not raise `attempted` at all — it changes WHICH rows are drawn,
    and the owner's row has a 30/N chance per night with nothing in the run
    report to say it was not exercised. That is the labelling loop's feedback
    latency and it is unbounded (`BACKLOG 4.14`).
    """

    def test_the_exploratory_draw_is_capped_and_randomly_ordered(self):
        """The two clauses the claim above turns on, read off the task's SQL."""
        collapsed = " ".join(_task_sql("_EXPLORATORY_SQL").split())
        assert "ORDER BY RANDOM()" in collapsed, (
            "the exploratory draw stopped being random — if it is now ordered, "
            "a labelled row's chance of being measured is no longer 30/N and "
            "this class's argument needs rewriting"
        )
        assert "LIMIT %(limit)s" in collapsed
        assert eval_service.EXPLORATORY_SAMPLE_SIZE == 30

    def test_a_label_does_not_move_attempted_once_the_pool_is_at_the_cap(
        self, monkeypatch
    ):
        """The boundary case: exactly EXPLORATORY_SAMPLE_SIZE eligible rows.

        Adding a labelled row raises the eligible population by one and moves
        `attempted`, `valid` and `scored` by NOTHING, because the draw was
        already saturated. The 4 -> 5 arithmetic elsewhere in this file is the
        sub-cap regime, not the general one.
        """
        size = eval_service.EXPLORATORY_SAMPLE_SIZE
        pool = _pool(size)

        _wire(monkeypatch, exploratory_rows=pool)
        before = _run()
        _wire(monkeypatch, exploratory_rows=[*pool, _LABELLED_ROW])
        after = _run()

        expected = len(_GOLDEN_ROWS) + size
        assert before["attempted"] == expected
        assert after["attempted"] == expected, (
            "a label raised `attempted` above the exploratory cap — either the "
            "sample size changed or the selector stopped limiting"
        )
        assert (after["valid"], after["scored"]) == (
            before["valid"],
            before["scored"],
        )

    def test_a_label_in_a_large_pool_can_be_absent_from_a_run(self, monkeypatch):
        """The owner's own scenario: 200 eligible rows, one of them theirs.

        The run is the same size it was before they labelled anything, and on
        this run their row is not in it. WHICH row a saturated draw omits is the
        database's choice — the double truncates rather than shuffling, and the
        real query shuffles first — so what is asserted is that a run CAN omit
        it, which is all the claim needs and all the double can honestly show.
        """
        pool = _pool(200)
        rec = _wire(monkeypatch, exploratory_rows=[*pool, _LABELLED_ROW])
        result = _run()

        size = eval_service.EXPLORATORY_SAMPLE_SIZE
        assert result["attempted"] == len(_GOLDEN_ROWS) + size
        assert LABELLED_ID not in rec["invoked"][0], (
            "the double drew the labelled row anyway — this test is asserting "
            "nothing about the cap"
        )
        assert result["datasets"]["exploratory"]["scored"] == size


# ---------------------------------------------------------------------------
# 2. Golden membership is asserted, never inherited
# ---------------------------------------------------------------------------


class TestGoldenMembershipIsNeverInherited:
    """The golden half runs unsampled every night so that two runs are a PAIRED
    per-item comparison. A row that joined it as a side effect of being labelled
    would change the comparison's population, and nothing in the run report
    could say that had happened."""

    # The other half of this claim — that the label UPDATE assigns exactly four
    # columns and `dataset` is not one of them — is asserted by set EQUALITY in
    # test_eval_label_queue.py::TestTheLabelWrite::
    # test_a_label_is_recorded_at_the_human_authored_tier, which parses the SET
    # clause off the statement the REAL route emits against a recording cursor.
    # P3 briefly duplicated it against the writer's SQL constant and the review
    # deleted the copy (finding 2): asserting it through the route needs no
    # reference to the writer at all, so it does not trip R2 and does not cost
    # the ignored-new-files control a `--deselect`.

    def test_a_freshly_labelled_row_resolves_to_exploratory(self):
        """It carries no designation, and NULL is not golden."""
        assert eval_service.dataset_of(None) == eval_service.DATASET_EXPLORATORY
        assert eval_service.dataset_of("") == eval_service.DATASET_EXPLORATORY

    def test_the_golden_selector_cannot_be_reached_by_labelling(self):
        """The golden query keys on `dataset`, on nothing the label write sets.

        Asserted against the task's own SQL rather than against a summary of it,
        because the failure being excluded is a future clause — `OR
        label_trust_tier = 'human_authored'`, say — that would look like an
        improvement to whoever wrote it.
        """
        golden_sql = _task_sql("_GOLDEN_SQL")
        assert "dataset = %(golden)s" in golden_sql
        for column in ("label_trust_tier", "labelled_by", "labelled_at"):
            assert column not in golden_sql, (
                f"the golden selector reads {column} — membership of the fixed "
                "set would then be inherited from a label rather than asserted"
            )

    def test_labelling_does_not_move_the_golden_denominator(self, monkeypatch):
        before = _before(monkeypatch)
        after = _after(monkeypatch)

        assert before["datasets"]["golden"]["attempted"] == 2
        assert after["datasets"]["golden"]["attempted"] == 2, (
            "the labelled row landed in the golden set"
        )
        assert before["datasets"]["exploratory"]["attempted"] == 2
        assert after["datasets"]["exploratory"]["attempted"] == 3
        assert after["golden_set_present"] is True


# ---------------------------------------------------------------------------
# 3. Nothing an owner labels reaches a customer
# ---------------------------------------------------------------------------


def _labelled_scenario(source="mined"):
    scenario = {
        "id": "s-labelled",
        "source": source,
        "question": "Do you refund?",
        "reference_answer": OWNER_ANSWER,
        "label_trust_tier": HUMAN_AUTHORED,
        "labelled_by": "owner@example.com",
        "citations": [],
    }
    score = {
        "scenario_id": "s-labelled",
        "faithfulness": 1.0,
        "answer_relevancy": 1.0,
        "context_precision": 1.0,
        "context_recall": 1.0,
    }
    return scenario, score


class TestNoLabelReachesACustomer:
    """EVAL-ONLY, settled by the owner 2026-08-08.

    Two locks, tested separately. Testing them together would mean one test
    passing on the strength of either, which is how a wall loses a brick without
    anybody noticing.
    """

    def test_the_tier_the_writer_stamps_now_clears_the_minimum(self):
        """The old guarantee is spent, and this is the test that says so.

        Before D6 the gate was above the ceiling of anything the system could
        produce, so no flag was needed and eval_service said as much in a
        comment. `human_authored` outranks `human_verified`. Anyone reading that
        comment today would draw a conclusion that is no longer true, and this
        assertion is what makes the change audible rather than a matter of
        remembering.
        """
        rank = eval_service.trust_tier_rank
        assert rank(HUMAN_AUTHORED) >= rank(
            eval_service.VERIFIED_QA_MIN_TRUST_TIER
        ), (
            "the tier the label writer stamps no longer clears the promotion "
            "minimum — if that is deliberate, the two locks below are now "
            "three and this file should say so"
        )

    def test_lock_one_the_gate_reads_the_questions_origin(self):
        """A human-labelled row is refused for its SOURCE, before anything else.

        The refusal reason is the assertion. `customer_negative` means the gate
        resolved `source='mined'` — the QUESTION's origin. If someone swaps the
        gate to `label_trust_tier()`, which is the resolver P1 argues is the
        right one for reasoning about an ANSWER, this row stops being refused
        here and the reason changes, so the swap cannot be silent.
        """
        scenario, score = _labelled_scenario()
        candidates, refusals = eval_service.select_promotion_candidates(
            [scenario], [score]
        )

        assert candidates == []
        assert refusals == {"trust_tier:customer_negative": 1}, (
            "a top-scoring human-labelled row was not refused on its origin's "
            f"tier: {refusals}"
        )

    def test_lock_two_the_decision_refuses_a_row_that_clears_every_other_gate(
        self, monkeypatch
    ):
        """With lock one lifted, the row is STILL refused — by the decision.

        Lifting lock one is exactly the one-line change the docstring above
        warns about, simulated here by making the row's source promotable. A
        1.0/1.0 human-authored answer is then eligible on every property the
        gate reasons about, and the only thing left between it and a customer is
        the owner's decision.

        THE SOURCE REGISTERED IS HYPOTHETICAL, NEVER A REAL ONE (D6 P3 review,
        finding 12). This used to alias `'mined'` — a source the shipped schema
        allows — so for the duration of the test the process held a state that
        `test_no_schema_allowed_source_can_produce_a_human_label_tier` asserts is
        impossible. `'owner_written'` is the name every pre-existing test in
        test_eval_service.py uses for exactly this, and it makes the same point
        without installing a contradiction another test would catch if a
        teardown ever failed.
        """
        _with_source_tier(monkeypatch, "owner_written", HUMAN_AUTHORED)
        scenario, score = _labelled_scenario(source="owner_written")
        candidates, refusals = eval_service.select_promotion_candidates(
            [scenario], [score]
        )

        assert candidates == [], (
            "a human-labelled answer became a promotion candidate — "
            "retrieval_service.verified_qa_lookup would serve it to a customer "
            "ahead of retrieval, and the owner settled on eval-only"
        )
        assert refusals == {eval_service.PROMOTION_DISABLED_REFUSAL: 1}

    def test_the_decision_refusal_is_counted_not_swallowed(self, monkeypatch):
        """A row the decision refuses is accounted for, not dropped.

        WHAT THIS TEST DOES NOT SHOW, STATED BECAUSE IT USED TO CLAIM IT DID (D6
        P3 review, finding 1). Its old docstring called `refused` "a measurement:
        how many rows the decision is holding", and the number the owner needs to
        judge the flip. It is neither. The test can only reach the decision gate
        by first lifting the resolver gate, which is the complement of its own
        blind spot: with the shipped configuration this count is 0 for every
        schema-allowed source, nothing under `app/` calls this function, and
        `run_eval_suite` does not return `refusals`. What IS pinned here is the
        accounting invariant — `promoted + refused == scored`, so a promotion
        rate cannot be constructed without its denominator — and that the
        refusal carries the decision's own reason rather than being swallowed.
        `test_the_decision_gate_is_never_reached_by_a_schema_allowed_source`
        below is the companion that shows the count IS zero unlifted.
        """
        _with_source_tier(monkeypatch, "owner_written", HUMAN_AUTHORED)
        connect_calls: list = []
        monkeypatch.setattr(
            eval_service.psycopg2,
            "connect",
            lambda *a, **kw: connect_calls.append(a) or MagicMock(),
        )
        scenario, score = _labelled_scenario(source="owner_written")

        result = eval_service.promote_to_verified_qa(
            [scenario], [score], "postgresql://production"
        )

        assert result["promoted"] == 0
        assert result["scored"] == 1
        assert result["promoted"] + result["refused"] == result["scored"]
        assert result["refusals"] == {eval_service.PROMOTION_DISABLED_REFUSAL: 1}
        assert connect_calls == [], (
            "the promotion path opened the tenant database while the decision "
            "is off — 'did an eval write to verified_qa?' must be answerable by "
            "observing that it never connected"
        )

    def test_the_decision_gate_is_never_reached_by_a_schema_allowed_source(self):
        """The number the ordering was justified by is structurally zero.

        D6 P3 argued the decision gate is consulted LAST so that
        `refusals[PROMOTION_DISABLED_REFUSAL]` measures "how many rows would
        have been written into verified_qa if the owner flipped the decision".
        The review disproved it by direct probe and this is that probe, kept.

        Every source the shipped schema allows, each row carrying a
        human-authored label tier, a non-empty owner answer and 1.0/1.0 scores —
        the most promotable row the system can construct without lifting a lock.
        All of them are refused by the TIER gate, which runs first, so the
        decision gate's count is 0. An owner shown that 0 would read "flipping
        the decision promotes nothing"; what it means is "the resolver refused
        them all before the decision was asked".

        The gate ordering is unchanged — a refused row keeping its most specific
        reason is a real benefit — but the prose that justified it by this
        number is gone from eval_service, and this test is what keeps it gone.
        """
        sources = sorted(eval_service.SCENARIO_SOURCE_TRUST_TIER)
        assert sources, "the source->tier mapping is empty"

        scenarios = []
        scores = []
        for i, source in enumerate(sources):
            scenario, score = _labelled_scenario(source=source)
            scenario["id"] = score["scenario_id"] = f"s-{i}"
            scenarios.append(scenario)
            scores.append(score)

        candidates, refusals = eval_service.select_promotion_candidates(
            scenarios, scores
        )

        assert candidates == []
        assert refusals.get(eval_service.PROMOTION_DISABLED_REFUSAL, 0) == 0, (
            "a schema-allowed source reached the decision gate — if that is "
            "deliberate, the refusal count has become readable and "
            "PROMOTION_DISABLED_REFUSAL's comment must be rewritten again"
        )
        assert sum(refusals.values()) == len(sources)
        assert all(reason.startswith("trust_tier:") for reason in refusals), (
            f"something other than the tier gate refused these rows: {refusals}"
        )

    def test_the_recorded_decision_names_the_decision_not_an_absent_producer(self):
        """The reason on every run must describe the world as it is now.

        It used to read "no row is promotable until a correction UI produces
        human-verified answers". D6 built that correction UI. A run stamping the
        old sentence tells a later reader the door is held by an absent producer,
        when the producer exists and the door is held by a decision — and a stale
        statement a reader believes is worse than the absence the sentence was
        written to avoid.
        """
        decision = eval_service.VERIFIED_QA_PROMOTION_DECISION
        rank = eval_service.trust_tier_rank

        assert decision["enabled"] is False
        assert decision["scope"] == "eval_only"
        assert decision["decided_on"] == "2026-08-08"
        assert decision["refusal_reason"] == eval_service.PROMOTION_DISABLED_REFUSAL
        # The record contradicts its own former justification, in its own terms:
        # it names a tier that IS producible and that clears its own minimum.
        assert rank(decision["producible_label_tier"]) >= rank(
            decision["min_trust_tier"]
        )
        assert "2026-08-08" in decision["reason"]
        assert "eval-only" in decision["reason"]
        assert "until a correction UI" not in decision["reason"], (
            "the run still records the pre-D6 justification"
        )

    def test_the_recorded_decision_stays_flat_because_the_copy_is_shallow(self):
        """`build_eval_run_config` copies this with `dict(...)`.

        That is a SHALLOW copy, so a nested dict or list here would be handed to
        every caller by reference and one caller's mutation would rewrite what
        every subsequent run records. The existing copy test only observes the
        top level, so it would not see it.
        """
        decision = eval_service.VERIFIED_QA_PROMOTION_DECISION

        assert set(decision) == {
            "enabled",
            "min_trust_tier",
            "scope",
            "decided_on",
            "producible_label_tier",
            "refusal_reason",
            "reason",
        }
        for key, value in decision.items():
            assert isinstance(value, (str, bool)), (
                f"{key} is a {type(value).__name__} — dict() copies it by "
                "reference and a caller can poison the constant"
            )

    def test_the_run_reports_the_flag_beside_the_zero(self, monkeypatch):
        """`promoted: 0` alone does not distinguish policy from outcome.

        Zero is also what an ENABLED run that happened to promote nothing
        reports. Since D6 those are genuinely different states — the system can
        now produce a label that would qualify — so the boolean travels with the
        count it explains.
        """
        result = _after(monkeypatch)

        assert result["promoted"] == 0
        assert result["promotion_enabled"] is False
        assert (
            result["promotion_disabled_reason"]
            == eval_service.VERIFIED_QA_PROMOTION_DECISION["reason"]
        )


# ---------------------------------------------------------------------------
# 4. (attempted, valid, scored) stay honest with a labelled row in the run
# ---------------------------------------------------------------------------


class TestTheCountsStayHonest:
    """A label adds to the DENOMINATOR whether or not it produces a score.

    `valid` is the count of rows that could be scored; `scored` is the count
    that were. A labelled row the agent could not answer must raise the first
    and not the second, or the run reports a measurement over a population it
    did not measure.
    """

    def test_the_triple_moves_by_exactly_one(self, monkeypatch):
        before = _before(monkeypatch)
        after = _after(monkeypatch)

        assert (before["attempted"], before["valid"], before["scored"]) == (4, 4, 4)
        assert (after["attempted"], after["valid"], after["scored"]) == (5, 5, 5)

    def test_a_labelled_row_the_agent_could_not_answer_is_valid_but_unscored(
        self, monkeypatch
    ):
        """The failure this forbids: counting the label as a measurement.

        The row was fetched and carries an answer, so it is attempted and valid.
        The agent produced nothing for it, so it is not scored — never scored 0,
        and never scored against its own reference answer, which is what
        `valid == scored` would quietly mean.
        """
        after = _after(monkeypatch, silent_ids=(LABELLED_ID,))

        assert after["attempted"] == 5
        assert after["valid"] == 5, "the denominator dropped a row it fetched"
        assert after["scored"] == 4
        assert after["datasets"]["exploratory"]["valid"] == 3
        assert after["datasets"]["exploratory"]["scored"] == 2
        assert after["datasets"]["golden"]["scored"] == 2

    def test_the_unscored_labelled_row_does_not_move_a_metric(self, monkeypatch):
        """Its metrics are means over the rows that produced numbers, and the
        observation count travels with each so the reader can see the gap.

        THE OBSERVATION COUNT IS THE ASSERTION; `measured` DELIBERATELY IS NOT
        (D6 P3 review, finding 9). The first version of this test also asserted
        `metric["measured"] is True` over TWO observations, which pins a
        behaviour that sits against this project's own stated floor:
        `summarise_run_validity` sets `measured: bool(values)` — true at n=1 —
        while `eval_service.MIN_SCORED_OBSERVATIONS = 3` exists precisely
        because "a rate alone cannot refuse a one-observation run". The floor is
        applied to the INVOCATION and never per metric per dataset. Someone
        closing that gap correctly would make a two-observation exploratory
        faithfulness report `measured: false, value: null, observations: 2`, and
        an assertion that it must be `measured` would go red and read as a
        regression rather than as the fix it is. So the count is asserted and
        the flag is only recorded, with the tension named.
        """
        after = _after(monkeypatch, silent_ids=(LABELLED_ID,))
        exploratory = after["datasets"]["exploratory"]["metrics"]

        assert exploratory, "the exploratory half reported no metrics at all"
        for name, metric in exploratory.items():
            assert metric["observations"] == 2, (
                f"{name} claims {metric['observations']} observations over an "
                "exploratory half in which only 2 rows produced a score"
            )
            # Documenting current behaviour, NOT pinning it: today `measured` is
            # bool(values), so 2 < MIN_SCORED_OBSERVATIONS still reads measured.
            # A future per-metric floor would flip this and should not have to
            # edit this file to do it.
            assert metric["measured"] == (metric["observations"] > 0)


# ---------------------------------------------------------------------------
# 5. The deploy gate — the LIVE downstream consumer P3 stopped short of
# ---------------------------------------------------------------------------


class TestALabelChangesWhatTheDeployGateReads:
    """D6 P3 review, finding 5. P3's downstream analysis stopped at
    `verified_qa`, which has no caller — while the consumer that reads every
    score a labelled row produces went unmentioned and untested.

    The chain, hop by hop:

        run_eval_suite -> write_eval_results        -> eval_results (PRODUCTION)
        _fetch_eval_summary_sync: AVG(score) GROUP BY metric  -> pass_rates
        run_deployment_checklist puts eval_summary on the orchestrator payload
        the orchestrator's ship/warn/block conditions read the rates

    WHERE THE THRESHOLD ACTUALLY LIVES, because the review's own wording put it
    one hop further than the code does. `apply_signal_evidence_gate` never reads
    `pass_rates` — it is a one-way floor on the signal's PRESENCE (measured,
    agent_invoked) and on red-team severity. The 0.85 ship bar and the
    [0.70, 0.85) warning band are prose in `_DEPLOYMENT_SYSTEM_PROMPT`, applied
    by the orchestrator model. So the gate cannot rescue a rate that labelling
    depressed either: the deterministic half can only make a recommendation more
    conservative.

    WHY THIS MATTERS FOR THE LABELLING LOOP. The queue is populated with mined
    production FAILURES — questions the agent got wrong. Answering them adds
    hard negatives to the scored exploratory population, so an owner working
    the queue can DEPRESS their own pass rates and see deploys refused for a
    reason nothing connects back to their labelling. The inverse is equally
    live: an owner who pastes the agent's own answer in as the reference
    inflates faithfulness. And an owner-authored answer is not grounded in the
    retrieved corpus by construction, so context_recall over labelled rows is a
    different measurement from context_recall over Haiku-written references —
    averaged into one dataset mean, because no selector projects
    `label_trust_tier` (`BACKLOG 4.12`).
    """

    def test_the_labelled_rows_score_is_written_to_eval_results(self, monkeypatch):
        """Hop one, driven through the real task: the row the owner labelled is
        in what the run persists to PRODUCTION, on every metric."""
        rec = _wire(monkeypatch, exploratory_rows=[*_EXPLORATORY_ROWS, _LABELLED_ROW])
        _run()

        assert len(rec["results_written"]) == 1
        _run_id, scores, conn = rec["results_written"][0]
        assert conn == PRODUCTION, (
            "results went somewhere other than production — the deploy gate "
            "reads production, so a labelled row's score would never reach it"
        )
        written = {s["scenario_id"]: s for s in scores}
        assert LABELLED_ID in written, (
            "the labelled row was scored and then not persisted; the deploy "
            f"gate would never see it. persisted: {sorted(written)}"
        )
        for metric in _METRICS:
            assert written[LABELLED_ID][metric] is not None

    def test_the_pass_rate_query_cannot_exclude_a_labelled_row(self):
        """Hop two, at the string level: the aggregation is unfiltered.

        `pass_rates` is `AVG(score) GROUP BY metric` over one run's
        `eval_results`. It keys on `eval_run_id` and nothing else, so there is
        no provenance filter to fall through — a human-authored reference and a
        Haiku-written one contribute to the same mean, indistinguishably. That
        is the fact `BACKLOG 4.12` would change, and it is asserted rather than
        assumed so 4.12 cannot land while this comment quietly goes stale.
        """
        source = inspect.getsource(deployment_service._fetch_eval_summary_sync)
        collapsed = " ".join(source.split())
        assert "AVG(score), COUNT(score) FROM eval_results" in collapsed
        assert "WHERE eval_run_id = %s GROUP BY metric" in collapsed
        for column in ("label_trust_tier", "labelled_by", "labelled_at"):
            assert column not in source, (
                f"the pass-rate aggregation now mentions {column} — if it has "
                "learned to separate human-authored references from model ones, "
                "this test and BACKLOG 4.12 both need rewriting"
            )

    def test_a_hard_negative_label_lowers_the_rate_the_orchestrator_reads(
        self, monkeypatch
    ):
        """Hops one and two joined: the same rows, averaged the way the
        collector averages them.

        The aggregate is computed HERE, in Python, over the rows the real run
        actually handed to `write_eval_results` — there is no PostgreSQL on this
        machine, so `AVG(score) GROUP BY metric` cannot be executed and is not
        claimed to have been. What the previous test pins is that the collector's
        aggregation has no other input and no provenance filter; what this one
        shows is the arithmetic consequence.

        A mined failure the owner answers correctly is a question the agent gets
        WRONG — that is why it was mined. Scoring 0.2 on it drags the run's
        faithfulness under the 0.85 ship bar the orchestrator prompt states,
        from a run that was at 0.9 the night before the owner did the work.
        """

        def _rates(scores: list[dict]) -> dict[str, float]:
            out = {}
            for metric in _METRICS:
                values = [
                    s[metric] for s in scores if s.get(metric) is not None
                ]
                if values:
                    out[metric] = sum(values) / len(values)
            return out

        _wire(monkeypatch, exploratory_rows=[*_EXPLORATORY_ROWS, _LABELLED_ROW])
        rec_before = _wire(monkeypatch, exploratory_rows=_EXPLORATORY_ROWS)
        _run()
        before = _rates(rec_before["results_written"][0][1])

        rec_after = _wire(
            monkeypatch,
            exploratory_rows=[*_EXPLORATORY_ROWS, _LABELLED_ROW],
            scores_by_id={LABELLED_ID: 0.2},
        )
        _run()
        after = _rates(rec_after["results_written"][0][1])

        assert before["faithfulness"] == pytest.approx(0.9)
        assert after["faithfulness"] < before["faithfulness"], (
            "labelling a mined failure left the pass rate untouched — if that "
            "is now true, the population the gate averages has changed"
        )
        assert after["faithfulness"] < 0.85, (
            "the ship bar stated in _DEPLOYMENT_SYSTEM_PROMPT is 0.85; this "
            f"run reads {after['faithfulness']:.3f} and must be below it for "
            "the point of this test to be observed rather than argued"
        )

    def test_the_ship_bar_is_prose_in_the_prompt_not_code_in_the_gate(self):
        """Names the hop the review's wording put one step too far.

        `apply_signal_evidence_gate` is deterministic and refuses on the signal
        STATE. The rate threshold is a sentence in the orchestrator's system
        prompt, read by a model. Both facts are pinned here because "the deploy
        gate blocks on the pass rate" is the plausible misreading, and it would
        make someone look for a threshold in Python that is not there.
        """
        gate_source = inspect.getsource(deployment_service.apply_signal_evidence_gate)
        body = gate_source.split('"""', 2)[-1]
        assert "pass_rates" not in body, (
            "apply_signal_evidence_gate now reads pass_rates — it used to be a "
            "floor on the signal's presence only, and a threshold here is a "
            "second opinion about quality rather than a floor under evidence"
        )
        prompt = deployment_service._DEPLOYMENT_SYSTEM_PROMPT
        assert "all eval metrics >= 0.85" in prompt
        assert "Any eval metric pass_rate in [0.70, 0.85)" in prompt


# ---------------------------------------------------------------------------
# 6. The locks live in module state, and module state can be rebound
# ---------------------------------------------------------------------------


_LOCK_CONSTANTS = ("SCENARIO_SOURCE_TRUST_TIER", "VERIFIED_QA_PROMOTION_DECISION")


def _app_python_files() -> list[str]:
    root = os.path.dirname(os.path.dirname(inspect.getfile(eval_service)))
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        out.extend(
            os.path.join(dirpath, name)
            for name in filenames
            if name.endswith(".py")
        )
    return sorted(out)


def _lock_mutations(path: str) -> list[str]:
    """Every way *path* writes to one of the two lock constants.

    Three shapes, because `MappingProxyType` only stops the first:

      X[k] = v            — TypeError at runtime, still worth naming statically
      mod.X = {...}       — REBINDS the module attribute; a proxy cannot stop it
      X.update(...) etc.  — AttributeError at runtime, same reasoning as the first
    """
    hits: list[str] = []
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    def _root_name(node) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    for node in ast.walk(tree):
        targets: list = []
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Subscript):
                if _root_name(target.value) in _LOCK_CONSTANTS:
                    hits.append(f"line {node.lineno}: subscript assignment")
            elif isinstance(target, ast.Attribute):
                if target.attr in _LOCK_CONSTANTS:
                    hits.append(f"line {node.lineno}: rebinds .{target.attr}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("update", "setdefault", "pop", "clear"):
                if _root_name(node.func.value) in _LOCK_CONSTANTS:
                    hits.append(f"line {node.lineno}: .{node.func.attr}()")
    return hits


class TestTheLocksAreNotOneAssignmentAway:
    """D6 P3 review, finding 4.

    Both locks were plain module-level dicts read at call time, so ANY module in
    the process could lift both with two lines —

        eval_service.VERIFIED_QA_PROMOTION_DECISION["enabled"] = True
        eval_service.SCENARIO_SOURCE_TRUST_TIER["mined"] = "human_authored"

    — for the life of that process, with nothing in the tree watching. Four call
    sites already performed exactly that mutation via `monkeypatch.setitem`, so
    the shape was idiomatic and discoverable. Meanwhile the label WRITER, a
    strictly less dangerous surface, carries four independently-pinned
    restrictions plus a route-level credential guard.

    `MappingProxyType` closes the assignment. It does NOT close rebinding the
    module attribute, which no runtime type can, so that is what the scan is
    for.
    """

    def test_the_constants_are_read_only_mappings(self):
        for name in _LOCK_CONSTANTS:
            constant = getattr(eval_service, name)
            assert isinstance(constant, MappingProxyType), (
                f"{name} is a {type(constant).__name__} — one assignment from "
                "any module in the process lifts a lock on a customer-facing "
                "write for the life of that process"
            )
            with pytest.raises(TypeError):
                constant["a_key_no_lock_should_accept"] = True

    def test_no_module_under_app_writes_to_either_lock(self):
        offenders: dict[str, list[str]] = {}
        for path in _app_python_files():
            hits = _lock_mutations(path)
            if hits:
                offenders[os.path.basename(path)] = hits

        assert offenders == {}, (
            "a module writes to one of the promotion locks: "
            f"{offenders}. Both are read at call time by "
            "select_promotion_candidates, so a write anywhere in the process "
            "opens the customer-facing verified_qa path for the life of it"
        )

    def test_the_decision_still_copies_flat_and_whole(self):
        """`dict()` over a proxy still yields a fresh, independent plain dict —
        the property build_eval_run_config's shallow copy depends on."""
        copied = dict(eval_service.VERIFIED_QA_PROMOTION_DECISION)
        assert type(copied) is dict
        assert set(copied) == set(eval_service.VERIFIED_QA_PROMOTION_DECISION)
        copied["enabled"] = True
        assert eval_service.VERIFIED_QA_PROMOTION_DECISION["enabled"] is False


@pytest.mark.parametrize(
    "column", ["label_trust_tier", "labelled_by", "labelled_at", "dataset"]
)
def test_the_exploratory_selector_keys_on_none_of_the_label_columns(column):
    """The companion to the golden pin, in the other direction.

    The exploratory query must not start SELECTING for labels either — an
    `ORDER BY labelled_at DESC` would make the rotating half stop rotating and
    quietly become "the most recently labelled 30", which is a fixed set with
    none of the golden set's properties. `dataset` is in this list because the
    exploratory query keys on it only in the NEGATIVE (`IS NULL OR <> golden`),
    which is asserted separately in test_eval_task.
    """
    exploratory_sql = _task_sql("_EXPLORATORY_SQL")
    if column == "dataset":
        assert "dataset IS NULL OR dataset <> %(golden)s" in exploratory_sql
        return
    assert column not in exploratory_sql


def test_psycopg2_is_imported_so_the_harness_doubles_the_real_boundary():
    """The double replaces `mod.psycopg2.connect`; if the task stopped importing
    psycopg2, every test above would pass against an unwired boundary."""
    assert mod.psycopg2 is psycopg2
