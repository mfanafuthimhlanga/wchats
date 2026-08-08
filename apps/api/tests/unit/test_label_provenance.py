"""
D6 P1 — the trust tier that nothing could produce, and the wall around it.

`eval_service.LABEL_TRUST_TIERS` has declared `human_verified` (2) and
`human_authored` (3) since D5, and nothing in the system could produce either:
the only tier resolver was `SCENARIO_SOURCE_TRUST_TIER`, which maps every
scenario source the schema allows to `model_generated` or `customer_negative`.
So `VERIFIED_QA_MIN_TRUST_TIER = "human_verified"` gated on a tier no row could
carry, and the customer-facing verified-answer path was dead code by
construction.

This file covers the two halves of closing that:

1. THE TIER IS CARRIED BY THE LABEL, NOT INFERRED FROM THE QUESTION'S ORIGIN.
   `eval_scenarios.source` answers "where did this QUESTION come from?". A mined
   production failure the owner then answers by hand is `customer_negative` in
   origin and `human_authored` in label, simultaneously, and both statements are
   true. Collapsing them into one column is how a model-written string ends up
   admitted on a human tier — the failure `promotable_answer`'s docstring
   already warns about. `eval_service.label_trust_tier()` keeps them apart, and
   its fallback direction is pinned: it can downgrade a label to its origin's
   tier, and it can never manufacture a human claim out of an origin.

2. NO MODEL MAY EVER WRITE AT A HUMAN TIER — STRUCTURALLY.
   A trust tier is a claim about who wrote a string, and it is worth exactly the
   difficulty of forging it. Four independent restrictions, each pinned
   separately below so that removing any one of them turns a test red rather
   than quietly halving the wall:

     R1  the writer has no tier parameter        (TestR1NoTierParameter)
     R2  only app/api/ may import the writer     (TestR2ImportBoundary)
     R3  model-driven writers cannot write the
         label columns                           (TestR3TheModelWritersCannotWrite)
     R4  the writer refuses inside a Celery task
         or an agent tool context                (TestR4RuntimeContextGuard)

What is NOT proven here, plainly: migration 0016 has not been applied. There is
no PostgreSQL server on this machine, every `-m integration` harness skips, and
a skip is unobserved rather than passing. Nothing below has seen a real
`eval_scenarios` row, a real CHECK constraint rejection, or a real Celery worker.
The SQL is asserted at the string level and the guards are exercised against
real Celery and ContextVar state in-process.
"""

from __future__ import annotations

import ast
import inspect
import os
import re

import pytest

# ---------------------------------------------------------------------------
# Paths and the schema's own source list (parsed, never restated)
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(__file__)
API_ROOT = os.path.normpath(os.path.join(_TESTS_DIR, "../.."))
APP_DIR = os.path.join(API_ROOT, "app")
TESTS_DIR = os.path.join(API_ROOT, "tests")
LABEL_SERVICE_PATH = os.path.normpath(
    os.path.join(APP_DIR, "services", "label_service.py")
)
_MIGRATION_0011 = os.path.normpath(
    os.path.join(API_ROOT, "alembic_tenant/versions/0011_eval_scenarios_provenance.py")
)

LABEL_COLUMNS = ("label_trust_tier", "labelled_by", "labelled_at")


def _schema_allowed_scenario_sources() -> list[str]:
    """The eval_scenarios.source values the CHECK constraint permits.

    Parsed out of migration 0011 for the same reason test_eval_service parses
    it: hardcoding the list here would let the schema and the trust tables drift
    apart silently, which is the exact failure the tables exist to prevent.
    """
    with open(_MIGRATION_0011, encoding="utf-8") as fh:
        source = fh.read()
    clauses = re.findall(r"CHECK \(source IN \(([^)]*)\)\)", source)
    assert clauses, (
        "Could not find the eval_scenarios.source CHECK constraint in migration "
        "0011 — a parse failure here is a real failure, not a skip."
    )
    allowed: set[str] = set()
    for clause in clauses:
        allowed.update(
            part.strip().strip("'\"") for part in clause.split(",") if part.strip()
        )
    return sorted(allowed)


SCHEMA_ALLOWED_SOURCES = _schema_allowed_scenario_sources()


def _python_files(root: str) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                found.append(os.path.normpath(os.path.join(dirpath, name)))
    return sorted(found)


def _parse(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=path)


class _RecordingCursor:
    """Minimal psycopg2 cursor stand-in that records what was executed."""

    def __init__(self, rowcount: int = 1):
        self.executed: list[tuple[str, dict]] = []
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params or {}))


class _RecordingConn:
    def __init__(self, rowcount: int = 1):
        self.cursor_obj = _RecordingCursor(rowcount=rowcount)
        self.cursor_calls = 0

    def cursor(self):
        self.cursor_calls += 1
        return self.cursor_obj


class _ExplodingConn:
    """A connection that fails the test if anything touches the database."""

    def cursor(self):  # pragma: no cover - reaching this IS the failure
        raise AssertionError(
            "the guard let the call reach the database before refusing"
        )


@pytest.fixture(autouse=True)
def _no_ambient_agent_context():
    """Start every test in this module with no agent context in scope.

    NOT a convenience. `agent_tools.build_tool_server()` sets `_agent_id_var`
    and never clears it — by design, since it is setting up a turn — so any test
    module that calls it (tests/unit/test_agent_tools.py:686 passes
    agent_id='agent-reset-test') leaves that ContextVar set for the REST OF THE
    PYTEST PROCESS. Run in isolation this module passes; run after that one,
    R4's guard correctly reports an agent context and every test that expects
    the guard to be silent fails.

    Which is the guard behaving exactly as specified, on a stale fact. The same
    thing is true of a Celery worker thread: once a turn has run, `_agent_id_var`
    stays set, so R4's agent-context arm cannot distinguish "an agent is driving
    this call" from "an agent drove a call in this thread earlier". That
    direction is fail-CLOSED — it refuses more, never less — and it is why R4 is
    the last line rather than the only one (R2's import boundary and R3's
    column-level restriction do not depend on which thread is asking). It is
    recorded as a finding rather than papered over.

    This fixture therefore establishes the precondition each test claims to
    exercise, and restores whatever was there before so it cannot in turn become
    the module that pollutes the next one.
    """
    from app.services.agent_tools import _agent_id_var

    token = _agent_id_var.set("")
    try:
        yield
    finally:
        _agent_id_var.reset(token)


# ---------------------------------------------------------------------------
# The vocabulary: a label's tier, and where it may and may not come from
# ---------------------------------------------------------------------------


class TestLabelTierVocabulary:
    def test_the_human_tiers_are_the_two_the_hierarchy_already_declared(self):
        """HUMAN_LABEL_TIERS names tiers LABEL_TRUST_TIERS already ranks.

        A human tier that the hierarchy does not rank would resolve through
        trust_tier_rank's default to `unknown` — a label asserting a human and
        ranking below a model's output.
        """
        from app.services.eval_service import (
            HUMAN_LABEL_TIERS,
            LABEL_TRUST_TIERS,
            trust_tier_rank,
        )

        assert set(HUMAN_LABEL_TIERS) <= set(LABEL_TRUST_TIERS)
        assert HUMAN_LABEL_TIERS == ("human_verified", "human_authored")
        for tier in HUMAN_LABEL_TIERS:
            assert trust_tier_rank(tier) > trust_tier_rank("customer_negative")

    def test_the_column_name_is_declared_once_and_matches_the_migration(self):
        from app.services.eval_service import LABEL_TIER_COLUMN

        assert LABEL_TIER_COLUMN == "label_trust_tier"
        with open(
            os.path.join(
                API_ROOT,
                "alembic_tenant/versions/0016_eval_scenario_label_provenance.py",
            ),
            encoding="utf-8",
        ) as fh:
            assert f"ADD COLUMN IF NOT EXISTS {LABEL_TIER_COLUMN} TEXT" in fh.read()

    def test_an_unlabelled_row_falls_back_to_its_origins_tier(self):
        """NULL means "nobody claimed anything", which is every row today."""
        from app.services.eval_service import label_trust_tier, scenario_trust_tier

        for source in SCHEMA_ALLOWED_SOURCES:
            row = {"source": source, "reference_answer": "x"}
            assert label_trust_tier(row) == scenario_trust_tier(source)
            assert label_trust_tier({**row, "label_trust_tier": None}) == (
                scenario_trust_tier(source)
            )

    def test_a_human_label_outranks_the_questions_origin(self):
        """The case the column exists for.

        A mined production failure is `customer_negative` in origin. Once the
        owner writes the answer, the LABEL is human_authored — and the row's
        `source` still says 'mined', because that is still where the question
        came from. Both facts survive.
        """
        from app.services.eval_service import (
            is_human_labelled,
            label_trust_tier,
            scenario_trust_tier,
            trust_tier_rank,
        )

        row = {
            "source": "mined",
            "reference_answer": "We refund within 14 days of delivery.",
            "label_trust_tier": "human_authored",
        }
        assert label_trust_tier(row) == "human_authored"
        assert is_human_labelled(row) is True
        assert row["source"] == "mined"
        assert scenario_trust_tier(row["source"]) == "customer_negative"
        assert trust_tier_rank(label_trust_tier(row)) > trust_tier_rank(
            scenario_trust_tier(row["source"])
        )

    @pytest.mark.parametrize(
        "value",
        ["model_generated", "customer_negative", "unknown", "HUMAN_AUTHORED", "human", 7],
    )
    def test_a_value_the_check_forbids_fails_closed_to_unknown(self, value):
        """0016's CHECK admits only NULL or a human tier in that column.

        Anything else means the column was written by something that bypassed
        both the service layer and the database constraint, and a provenance
        nobody can account for is worth less than one that has been accounted
        for and found untrustworthy. So it resolves to `unknown`, which ranks
        BELOW model_generated — never to the source's tier, which would silently
        launder a corrupt value back into a plausible one.
        """
        from app.services.eval_service import label_trust_tier, trust_tier_rank

        row = {"source": "mined", "label_trust_tier": value}
        assert label_trust_tier(row) == "unknown"
        assert trust_tier_rank(label_trust_tier(row)) < trust_tier_rank(
            "model_generated"
        )

    @pytest.mark.parametrize("source", SCHEMA_ALLOWED_SOURCES)
    def test_no_schema_allowed_source_can_produce_a_human_label_tier(self, source):
        """The guard that makes label_trust_tier()'s fallback safe.

        The fallback reads the origin's tier when the label claims nothing. That
        is only sound while no origin can resolve to a human tier — otherwise
        `source` would be back to deciding what a label is worth, which is the
        collapse this whole column exists to prevent, reintroduced through the
        one branch that looks harmless.
        """
        from app.services.eval_service import is_human_labelled, label_trust_tier

        row = {"source": source, "reference_answer": "x"}
        assert label_trust_tier(row) not in ("human_verified", "human_authored")
        assert is_human_labelled(row) is False

    def test_a_row_predating_0016_is_not_human_labelled(self):
        """No `label_trust_tier` key at all — the shape every row selected from
        a pre-0016 tenant DB has."""
        from app.services.eval_service import is_human_labelled

        assert is_human_labelled({"source": "generated"}) is False
        assert is_human_labelled({}) is False

    def test_is_human_label_tier_rejects_everything_else(self):
        from app.services.eval_service import is_human_label_tier

        assert is_human_label_tier("human_authored") is True
        assert is_human_label_tier("human_verified") is True
        for value in (None, "", "model_generated", "customer_negative", "unknown"):
            assert is_human_label_tier(value) is False


# ---------------------------------------------------------------------------
# R1 — there is no tier parameter
# ---------------------------------------------------------------------------


class TestR1NoTierParameter:
    def test_the_writer_has_no_tier_parameter(self):
        """The tier is what the function asserts, not what its caller asks for.

        A `tier=` argument would make `human_authored` nameable from wherever
        the function is nameable, and the whole hierarchy would then rest on
        every call site passing the honest value. There is no parameter to pass
        it into.
        """
        from app.services.label_service import record_human_label

        params = inspect.signature(record_human_label).parameters
        assert set(params) == {
            "conn",
            "scenario_id",
            "reference_answer",
            "labelled_by",
        }, f"unexpected parameters on record_human_label: {sorted(params)}"
        for name in params:
            assert "tier" not in name.lower()
            assert "trust" not in name.lower()

    def test_everything_after_conn_is_keyword_only(self):
        """Three same-typed strings in a row is a transposition waiting to
        happen, and transposing `labelled_by` with `reference_answer` writes the
        author's name into the answer a customer-facing eval then scores."""
        from app.services.label_service import record_human_label

        params = inspect.signature(record_human_label).parameters
        assert params["conn"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for name in ("scenario_id", "reference_answer", "labelled_by"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY

    def test_the_stamped_tier_is_a_human_tier_the_migration_admits(self):
        from app.services.eval_service import HUMAN_LABEL_TIERS
        from app.services.label_service import HUMAN_AUTHORED_TIER

        assert HUMAN_AUTHORED_TIER == "human_authored"
        assert HUMAN_AUTHORED_TIER in HUMAN_LABEL_TIERS


# ---------------------------------------------------------------------------
# R2 — the import boundary
# ---------------------------------------------------------------------------


def _references_label_writer(path: str) -> list[str]:
    """Every AST-level reference to the human-label writer in *path*.

    AST rather than a text search on purpose: a text search trips on prose that
    merely names the module (eval_service's comment about where the write path
    lives), and prose is not reachability. This looks at imports, names,
    attribute access, and string constants that spell the module's import path
    (the importlib back door).
    """
    hits: list[str] = []
    watched = {"label_service", "record_human_label"}
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "label_service" in alias.name:
                    hits.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and "label_service" in node.module:
                hits.append(f"from {node.module} import ...")
            for alias in node.names:
                if alias.name in watched:
                    hits.append(f"from ... import {alias.name}")
        elif isinstance(node, ast.Name) and node.id in watched:
            hits.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in watched:
            hits.append(f"attribute .{node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "app.services.label_service" in node.value:
                hits.append("string 'app.services.label_service'")
    return hits


class TestR2ImportBoundary:
    def test_no_model_driven_module_may_import_the_human_label_writer(self):
        """The writer is reachable from an authenticated HTTP request and from
        nowhere else in the tree.

        `app/worker/` is every Celery task. The rest of `app/services/` is every
        agent tool, every judge, every scenario producer and the eval service
        itself — and the eval service is imported BY the tasks, so a single
        import there would hand the writer to every task in the system through
        the back door.
        """
        allowed_prefix = os.path.join(APP_DIR, "api") + os.sep
        offenders: dict[str, list[str]] = {}
        for path in _python_files(APP_DIR):
            if path == LABEL_SERVICE_PATH or path.startswith(allowed_prefix):
                continue
            hits = _references_label_writer(path)
            if hits:
                offenders[os.path.relpath(path, API_ROOT)] = hits

        assert offenders == {}, (
            "a human trust tier must be unreachable from any agent, task or "
            f"judge — these modules reference the writer: {offenders}"
        )

    def test_no_conftest_fixture_may_import_the_human_label_writer(self):
        """A fixture that can stamp `human_authored` makes every test that uses
        it a producer of human labels, and the tier then means "a fixture said
        so" in exactly the place the wall is supposed to be tested."""
        offenders: dict[str, list[str]] = {}
        for path in _python_files(TESTS_DIR):
            if os.path.basename(path) != "conftest.py":
                continue
            hits = _references_label_writer(path)
            if hits:
                offenders[os.path.relpath(path, API_ROOT)] = hits

        assert offenders == {}, f"conftest fixtures reference the writer: {offenders}"

    def test_the_boundary_check_can_actually_see_a_reference(self):
        """The detector is exercised against a file that DOES reference the
        writer, so a scan that silently matches nothing cannot pass as a wall.

        Without this, a typo in the watched-name set would make every assertion
        above vacuously true.
        """
        hits = _references_label_writer(os.path.abspath(__file__))
        assert hits, (
            "_references_label_writer found nothing in this very file, which "
            "imports record_human_label — the detector is broken, so every "
            "boundary assertion above is vacuous"
        )

    def test_the_label_service_does_not_import_a_worker_or_an_agent_at_module_scope(
        self,
    ):
        """The wall runs both ways.

        `agent_tools` is imported lazily inside `_current_agent_id()` precisely
        so that this module does not pull the agent stack into the API process,
        and so that a circular import cannot make the guard the thing that fails
        to load. A module-scope import of a worker or agent module here would
        also invert the dependency the boundary test above relies on.
        """
        tree = _parse(LABEL_SERVICE_PATH)
        module_level_imports: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                module_level_imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_level_imports.append(node.module)

        for name in module_level_imports:
            assert not name.startswith("app.worker"), (
                f"label_service imports {name} at module scope"
            )
            assert "agent" not in name, (
                f"label_service imports {name} at module scope"
            )


# ---------------------------------------------------------------------------
# R3 — the model-driven writers cannot write the label columns
# ---------------------------------------------------------------------------


def _scenario_write_statements(path: str) -> list[str]:
    """String constants in *path* that INSERT into or UPDATE eval_scenarios."""
    statements: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            collapsed = " ".join(node.value.split()).upper()
            if (
                "INSERT INTO EVAL_SCENARIOS" in collapsed
                or "UPDATE EVAL_SCENARIOS" in collapsed
            ):
                statements.append(node.value)
    return statements


class TestR3TheModelWritersCannotWrite:
    def test_only_the_label_writer_writes_the_label_columns(self):
        """Every model-driven producer routes through
        `scenario_service.store_scenarios` or
        `scenario_service.insert_provenance_scenario` — generated suites, mined
        production failures, promoted traces, contained red-team findings.
        Neither statement names a label-provenance column, so those producers
        physically cannot populate one. The failure mode is a NULL tier, which
        reads as "no human labelled this".
        """
        offenders: dict[str, list[str]] = {}
        for path in _python_files(APP_DIR):
            if path == LABEL_SERVICE_PATH:
                continue
            for statement in _scenario_write_statements(path):
                named = [c for c in LABEL_COLUMNS if c in statement]
                if named:
                    offenders.setdefault(
                        os.path.relpath(path, API_ROOT), []
                    ).extend(named)

        assert offenders == {}, (
            "an eval_scenarios write outside label_service names a label "
            f"provenance column: {offenders}"
        )

    def test_the_label_writer_does_write_them(self):
        """The mirror of the test above, so the scan cannot pass by finding no
        eval_scenarios writes at all."""
        statements = _scenario_write_statements(LABEL_SERVICE_PATH)
        assert statements, "label_service contains no eval_scenarios write"
        joined = " ".join(statements)
        for column in LABEL_COLUMNS:
            assert column in joined, f"label_service never writes {column}"

    def test_the_scenario_service_insert_paths_name_no_label_column(self):
        """Stated directly against the two functions as well as against the
        tree, because these two are the ones a future producer will copy."""
        from app.services import scenario_service

        for fn in (
            scenario_service.store_scenarios,
            scenario_service.insert_provenance_scenario,
        ):
            source = inspect.getsource(fn)
            for column in LABEL_COLUMNS:
                assert column not in source, (
                    f"{fn.__name__} names {column} — a model-driven producer "
                    "must not be able to populate a label provenance column"
                )

    def test_the_human_write_does_not_touch_the_questions_origin(self):
        """`source` says where the QUESTION came from and stays true after
        somebody else writes the answer. A write that changed it would erase the
        provenance of the question in the act of recording the provenance of the
        label."""
        from app.services import label_service

        statements = _scenario_write_statements(LABEL_SERVICE_PATH)
        joined = " ".join(statements)
        assert "source" not in joined, (
            "the human-label UPDATE touches eval_scenarios.source"
        )
        assert "dataset" not in joined, (
            "the human-label UPDATE touches eval_scenarios.dataset — golden-set "
            "membership is a separate assertion, never inherited from a label"
        )
        assert label_service is not None


# ---------------------------------------------------------------------------
# R4 — the runtime context guard
# ---------------------------------------------------------------------------


class TestR4RuntimeContextGuard:
    def test_a_celery_task_context_refuses_the_human_label(self):
        """Exercised against Celery's real current-task stack, not a mock.

        `celery._state.get_current_task()` is what a running task actually sets,
        so pushing onto that stack puts this call in the same state a task body
        is in. A refusal here is a refusal in the worker.
        """
        from celery import _state

        from app.services.label_service import HumanLabelRefused, record_human_label

        class _FakeTask:
            name = "app.worker.tasks.runtime.eval.run_eval_suite"

        _state.push_current_task(_FakeTask())
        try:
            with pytest.raises(HumanLabelRefused) as excinfo:
                record_human_label(
                    _ExplodingConn(),
                    scenario_id="11111111-1111-1111-1111-111111111111",
                    reference_answer="an answer a task tried to call human",
                    labelled_by="run_eval_suite",
                )
        finally:
            _state.pop_current_task()

        assert "Celery task" in str(excinfo.value)
        assert "run_eval_suite" in str(excinfo.value)

    def test_an_agent_tool_context_refuses_the_human_label(self):
        """The agent's own ContextVar, set the way build_tool_server sets it."""
        from app.services.agent_tools import _agent_id_var
        from app.services.label_service import HumanLabelRefused, record_human_label

        token = _agent_id_var.set("6f1c0e2a-0000-4000-8000-000000000001")
        try:
            with pytest.raises(HumanLabelRefused) as excinfo:
                record_human_label(
                    _ExplodingConn(),
                    scenario_id="11111111-1111-1111-1111-111111111111",
                    reference_answer="an answer an agent tried to call human",
                    labelled_by="retrieve_tool",
                )
        finally:
            _agent_id_var.reset(token)

        assert "agent tool" in str(excinfo.value)

    def test_the_guard_fires_before_the_database_is_touched(self):
        """_ExplodingConn above raises on `.cursor()`. The two tests reaching
        HumanLabelRefused rather than AssertionError is the proof; this test
        states it so the intent survives a refactor of the fakes."""
        from app.services import label_service

        source = inspect.getsource(label_service.record_human_label)
        assert source.index("assert_human_context()") < source.index("conn.cursor()")
        assert callable(label_service.assert_human_context)

    def test_the_guard_passes_outside_a_model_context(self):
        """The negative control for R4: with no task and no agent in scope the
        guard is silent, so the two refusals above are caused by the contexts
        and not by the guard refusing everything."""
        from app.services.label_service import assert_human_context

        assert assert_human_context() is None

    def test_a_task_context_refuses_even_with_a_perfectly_valid_label(self):
        """The refusal is about WHERE the call came from, never about the
        payload — so a task cannot talk its way past it by passing better
        arguments."""
        from celery import _state

        from app.services.label_service import HumanLabelRefused, record_human_label

        class _FakeTask:
            name = "app.worker.tasks.runtime.bench.promote_trace_to_scenario"

        conn = _RecordingConn()
        _state.push_current_task(_FakeTask())
        try:
            with pytest.raises(HumanLabelRefused):
                record_human_label(
                    conn,
                    scenario_id="11111111-1111-1111-1111-111111111111",
                    reference_answer="Refunds are processed within 14 days.",
                    labelled_by="owner@example.com",
                )
        finally:
            _state.pop_current_task()

        assert conn.cursor_calls == 0
        assert conn.cursor_obj.executed == []


# ---------------------------------------------------------------------------
# The write itself
# ---------------------------------------------------------------------------


class TestRecordHumanLabel:
    def _call(self, conn, **overrides):
        from app.services.label_service import record_human_label

        kwargs = {
            "scenario_id": "11111111-1111-1111-1111-111111111111",
            "reference_answer": "  Refunds are processed within 14 days.  ",
            "labelled_by": "owner@example.com",
        }
        kwargs.update(overrides)
        return record_human_label(conn, **kwargs)

    def test_it_stamps_the_human_tier_and_the_author(self):
        conn = _RecordingConn(rowcount=1)
        result = self._call(conn)

        assert len(conn.cursor_obj.executed) == 1
        sql, params = conn.cursor_obj.executed[0]
        assert "UPDATE eval_scenarios" in sql
        assert params["tier"] == "human_authored"
        assert params["labelled_by"] == "owner@example.com"
        assert params["scenario_id"] == "11111111-1111-1111-1111-111111111111"
        assert result == {
            "scenario_id": "11111111-1111-1111-1111-111111111111",
            "label_trust_tier": "human_authored",
            "labelled_by": "owner@example.com",
            "rows_updated": 1,
        }

    def test_the_answer_is_stripped_before_it_is_stored(self):
        """A label of `"   "` would make the row eligible to a selector that
        filters on `reference_answer != ''` while asserting a human authored
        whitespace."""
        conn = _RecordingConn()
        self._call(conn)
        _sql, params = conn.cursor_obj.executed[0]
        assert params["reference_answer"] == "Refunds are processed within 14 days."

    def test_the_row_that_does_not_exist_is_reported_not_raised(self):
        """rowcount 0 is an outcome the caller counts, not an exception it
        catches — the same shape as select_promotion_candidates' refusals, so a
        labelling rate can never be built without its denominator."""
        conn = _RecordingConn(rowcount=0)
        result = self._call(conn)
        assert result["rows_updated"] == 0

    @pytest.mark.parametrize("answer", ["", "   ", "\n\t ", None])
    def test_an_empty_answer_is_rejected_without_touching_the_database(self, answer):
        from app.services.label_service import LabelRejected

        conn = _RecordingConn()
        with pytest.raises(LabelRejected):
            self._call(conn, reference_answer=answer)
        assert conn.cursor_calls == 0

    @pytest.mark.parametrize("author", ["", "   ", None])
    def test_a_label_with_no_author_is_rejected(self, author):
        """A human tier with no human named behind it is an unsourced claim."""
        from app.services.label_service import LabelRejected

        conn = _RecordingConn()
        with pytest.raises(LabelRejected):
            self._call(conn, labelled_by=author)
        assert conn.cursor_calls == 0

    def test_it_neither_commits_nor_closes_the_callers_connection(self):
        """The caller owns the transaction, matching
        scenario_service.insert_provenance_scenario — so an API handler can wrap
        a read-back or an audit write in the same commit."""
        from app.services import label_service

        source = inspect.getsource(label_service.record_human_label)
        assert "conn.commit()" not in source
        assert "conn.close()" not in source
        assert "psycopg2.connect" not in source

    def test_repeating_the_same_label_produces_the_same_statement(self):
        """Idempotent by construction: applying the UPDATE twice with the same
        arguments leaves the same row state, so a retried request cannot create
        a second label or a duplicate row."""
        conn = _RecordingConn()
        first = self._call(conn)
        second = self._call(conn)
        assert first == second
        sql_a, params_a = conn.cursor_obj.executed[0]
        sql_b, params_b = conn.cursor_obj.executed[1]
        assert sql_a == sql_b and params_a == params_b

    def test_the_module_holds_no_connection_string(self):
        """CLAUDE.md project rule 1, made trivially true: this module never
        sees a connection string, so it cannot leak one into a task arg."""
        with open(LABEL_SERVICE_PATH, encoding="utf-8") as fh:
            source = fh.read()
        assert "conn_str" not in source
        assert "postgresql://" not in source

    def test_the_answer_text_is_never_logged(self):
        """The log line's job is provenance, not content: a reference answer is
        customer-domain text."""
        from app.services import label_service

        source = "\n".join(
            line
            for line in inspect.getsource(label_service.record_human_label).splitlines()
            if not line.strip().startswith("#")
        )
        log_call = source[source.index("log.info(") :]
        log_call = log_call[: log_call.index("\n    )")]
        assert "reference_answer" not in log_call
        assert "answer" not in log_call


# ---------------------------------------------------------------------------
# Absence pins — what P1 must NOT have opened
# ---------------------------------------------------------------------------


class TestP1OpenedNoCustomerFacingDoor:
    def test_a_human_labelled_scenario_is_still_not_promoted_to_verified_qa(self):
        """The settled decision of 2026-08-08 is eval-only: a label improves
        what the eval can measure and reaches no customer.

        `verified_qa` rows are served by `retrieval_service.verified_qa_lookup`
        AHEAD of hybrid search, so one mistyped label would be answered to a
        real customer with no eval between the typo and them. This pins that
        adding the human tier did not, by itself, open that write — a top-scoring
        human-labelled row is still refused.
        """
        from app.services.eval_service import select_promotion_candidates

        scenario = {
            "id": "s1",
            "source": "mined",
            "question": "Do you refund?",
            "reference_answer": "Yes, within 14 days.",
            "label_trust_tier": "human_authored",
        }
        score = {
            "scenario_id": "s1",
            "faithfulness": 1.0,
            "answer_relevancy": 1.0,
        }

        candidates, refusals = select_promotion_candidates([scenario], [score])
        assert candidates == []
        assert sum(refusals.values()) == 1

    def test_the_promotion_decision_is_still_recorded_as_disabled(self):
        from app.services.eval_service import VERIFIED_QA_PROMOTION_DECISION

        assert VERIFIED_QA_PROMOTION_DECISION["enabled"] is False
        assert VERIFIED_QA_PROMOTION_DECISION["reason"]

    @pytest.mark.parametrize("source", SCHEMA_ALLOWED_SOURCES)
    def test_no_source_became_promotable(self, source):
        """0016 added no scenario source, so 0011's list is still the whole
        list and none of it clears the gate."""
        from app.services.eval_service import is_promotable_to_verified_qa

        assert is_promotable_to_verified_qa(source) is False

    def test_the_label_write_does_not_reach_verified_qa(self):
        from app.services import label_service

        with open(LABEL_SERVICE_PATH, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                collapsed = " ".join(node.value.split()).upper()
                assert "INSERT INTO VERIFIED_QA" not in collapsed
                assert "UPDATE VERIFIED_QA" not in collapsed
        assert label_service is not None
