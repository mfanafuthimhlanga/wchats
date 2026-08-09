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
the old guarantee is spent, and what holds the door now is two edits'-worth of
deliberate choice:

    the RESOLVER  — `select_promotion_candidates` gates on `eval_scenarios.
                    source`, the QUESTION's origin, which labelling never
                    touches. Swapping it to `label_trust_tier()` — which is the
                    resolver P1 argues is the RIGHT one for reasoning about an
                    answer — would open the door in one line.
    the DECISION  — `VERIFIED_QA_PROMOTION_DECISION["enabled"]`, consulted last
                    so that a row it refuses is COUNTED under its own reason.

Both are pinned below, separately, because a wall with two bricks that fail
together is a wall with one brick.

WHAT IS NOT PROVEN HERE, PLAINLY. There is no PostgreSQL server on this machine.
Migration 0016 has not been applied and cannot be; no `eval_scenarios` row has
ever carried a real `label_trust_tier`; every `-m integration` harness skips and
a skip is unobserved, never a pass. The database boundary is a double in every
test below, the SQL is asserted at the string level, and the task is driven
in-process. What is proven is the arithmetic, the SQL's shape and the gates —
not that Postgres accepts any of it.
"""

from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from unittest.mock import MagicMock

import psycopg2
import pytest

from app.services import eval_service
from app.worker.tasks.runtime import eval as mod

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
        self._last = (
            self.golden_rows if "dataset = %(golden)s" in sql else self.exploratory_rows
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


def _wire(monkeypatch, *, exploratory_rows, silent_ids=()):
    """Double every boundary and return the recorder dict.

    `silent_ids` are scenarios the agent fails to answer — they are excluded
    from the scored set and counted in the invocation observation, which is the
    path that makes `valid` and `scored` different numbers.
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

    rec: dict = {"cursor": cursor, "invoked": [], "scored_input": [], "status": []}

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

    def _fake_ragas(scenarios):
        rec["scored_input"].append(list(scenarios))
        return {
            "scores": [
                {
                    "scenario_id": s["id"],
                    "faithfulness": 0.9,
                    "answer_relevancy": 0.9,
                    "context_precision": 0.9,
                    "context_recall": 0.9,
                }
                for s in scenarios
            ],
            "means": {"faithfulness": 0.9},
        }

    monkeypatch.setattr(mod, "run_ragas_eval", _fake_ragas)
    monkeypatch.setattr(mod, "write_eval_results", lambda r, s, c: None)
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
        """The row reaching the scorer carries the owner's text as the LABEL and
        the agent's own turn as the prediction.

        This is audit D1 aimed at the new row: eval.py used to set
        `agent_response = reference_answer`, which made faithfulness approach 1.0
        by construction. A human-labelled row is the most tempting place for that
        to come back, because its reference answer is the best text in the
        database and scoring against it looks like a good idea.
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
# 2. Golden membership is asserted, never inherited
# ---------------------------------------------------------------------------


class TestGoldenMembershipIsNeverInherited:
    """The golden half runs unsampled every night so that two runs are a PAIRED
    per-item comparison. A row that joined it as a side effect of being labelled
    would change the comparison's population, and nothing in the run report
    could say that had happened."""

    # The other half of this claim — that the label UPDATE assigns exactly four
    # columns and `dataset` is not one of them — is asserted in
    # test_label_provenance.py, the only test module R2 permits to read the
    # writer's SQL. See that file's TestTheWriteChangesNothingElse.

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
        """
        monkeypatch.setitem(
            eval_service.SCENARIO_SOURCE_TRUST_TIER, "mined", "human_authored"
        )
        scenario, score = _labelled_scenario()
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
        """`refused` is a measurement: how many rows the decision is holding.

        An early `return []` would report the same zero promotions and destroy
        the number the owner needs in order to judge, later, whether flipping
        the decision is worth anything.
        """
        monkeypatch.setitem(
            eval_service.SCENARIO_SOURCE_TRUST_TIER, "mined", "human_authored"
        )
        connect_calls: list = []
        monkeypatch.setattr(
            eval_service.psycopg2,
            "connect",
            lambda *a, **kw: connect_calls.append(a) or MagicMock(),
        )
        scenario, score = _labelled_scenario()

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
        observation count travels with each so the reader can see the gap."""
        after = _after(monkeypatch, silent_ids=(LABELLED_ID,))
        exploratory = after["datasets"]["exploratory"]["metrics"]

        for name, metric in exploratory.items():
            assert metric["measured"] is True, name
            assert metric["observations"] == 2, (
                f"{name} claims {metric['observations']} observations over an "
                "exploratory half in which only 2 rows produced a score"
            )


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
