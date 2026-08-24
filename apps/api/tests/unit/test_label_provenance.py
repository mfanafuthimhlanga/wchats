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
     R2  only the one named API module may
         reference the writer                    (TestR2ImportBoundary)
     R3  model-driven writers cannot write the
         label columns, and cannot NAME them     (TestR3TheModelWritersCannotWrite)
     R4  the writer refuses inside a Celery task
         or an agent tool context                (TestR4RuntimeContextGuard)

   WHAT THE FOUR DO **NOT** ADD UP TO, corrected 2026-08-09 after the P1
   adversarial review demonstrated it. The first version of this file said the
   model-driven producers "physically cannot" populate a label column. They
   can. R1 and R4 bind only callers of `record_human_label`; R2 bans references
   to `label_service`; and R3 was a substring scan over single `ast.Constant`
   nodes, so a Celery task issuing an f-string `UPDATE eval_scenarios SET ...
   label_trust_tier = 'human_authored'` called nothing, imported nothing, and
   passed all 59 tests in this file. R3 is now two complementary scans (a
   composed-SQL reconstruction and a name-level absence pin) with different
   blind spots, and the honest claim is that **the wall notices every forgery
   shape anyone has thought of, not that forgery is impossible**. A statically
   undetectable evasion — composing `"label" + "_trust_tier"` inside
   `eval_service.py`, which the name pin must allowlist because it declares the
   column name — remains possible, and that is the argument for R4 being the
   last line rather than for pretending the first three are exhaustive.

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
SCRIPTS_DIR = os.path.join(API_ROOT, "scripts")
RUNLOGS_DIR = os.path.join(API_ROOT, "_runlogs")
WORKER_DIR = os.path.join(APP_DIR, "worker")
SERVICES_DIR = os.path.join(APP_DIR, "services")
LABEL_SERVICE_PATH = os.path.normpath(os.path.join(SERVICES_DIR, "label_service.py"))
EVAL_SERVICE_PATH = os.path.normpath(os.path.join(SERVICES_DIR, "eval_service.py"))

# R2's allowed region, narrowed 2026-08-09. It used to be the whole of
# `app/api/`, which is not "an authenticated HTTP request": that tree also holds
# `app/api/v1/widget.py`, whose own header records `/widget/{agent_id}/config`
# and `/widget/jobs/{job_id}/events` as **no auth**, plus `agent_chat.py`,
# `query.py`, and `evals.py`'s generic `_query_tenant_db_sync`. Nothing under
# app/api references the writer today, so the tree was clean and the CLAIM was
# what was wrong. The region is now one named module — the one P2 is
# contracted to put the labelling route in.
LABEL_WRITER_CALLER = os.path.normpath(os.path.join(APP_DIR, "api", "v1", "evals.py"))

# The complexity gate names every function over its standard as data.
# `scripts/gates.py` pins ("app/services/label_service.py", "record_human_label")
# in LIZARD_BASELINE, and `tests/unit/test_gates.py` snapshots the same dict, so
# both files hold string constants naming the module and the symbol. Neither
# imports app code, and a path string in a file with no app imports cannot reach
# the writer, so `_writer_hits` excuses the string arm for exactly these two
# files and keeps every other arm watched. An import in either still fails R2.
GATES_SCRIPT_PATH = os.path.normpath(os.path.join(SCRIPTS_DIR, "gates.py"))
GATES_TEST_PATH = os.path.normpath(os.path.join(API_ROOT, "tests", "unit", "test_gates.py"))

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
        # Quoted literals only, matching test_eval_service's parser exactly. A
        # `clause.split(",")` version of this picked up `...` as a source,
        # because 0011's own docstring quotes the shape of the constraint it is
        # replacing (`CHECK (source IN (...))`) and the clause regex matches
        # prose as happily as SQL. The phantom source was harmless — it is not
        # promotable and yields no human tier — which is precisely why it would
        # have sat in five parametrised tests indefinitely, inflating what they
        # looked like they covered.
        allowed.update(re.findall(r"'([^']+)'", clause))
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


def _scanned_source_files() -> list[str]:
    """Every Python file R2 and R3 look at.

    `app/` plus `scripts/` plus `_runlogs/`. The last two were outside every
    restriction until 2026-08-09, which mattered because `_runlogs/` is not
    hypothetical: `_runlogs/run_eval_prod.py:27` already runs
    `FROM eval_scenarios WHERE reference_answer != ''` and its name says where
    it was pointed. A throwaway script that stamps a human tier is a forged
    label in a real tenant DB exactly like a task that does.

    The two alembic trees are excluded **by decision, not by oversight**: a
    migration's whole job is to name the label columns in DDL, so scanning them
    would make the name pin below fire on 0016 itself. What constrains the
    migrations instead is `test_migration_tenant_0016.py`, which bans every
    UPDATE and INSERT in the file outright.
    """
    found = _python_files(APP_DIR)
    for extra in (SCRIPTS_DIR, RUNLOGS_DIR):
        if os.path.isdir(extra):
            found.extend(_python_files(extra))
    return sorted(found)


def _parse(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=path)


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """`id()` of every string constant that is a bare expression statement.

    That is every docstring, and nothing else — a bare string expression is
    bound to `__doc__` (or discarded) and can never be handed to
    `cur.execute` or `import_module`. Both detectors below skip these, for the
    reason both were AST walks in the first place: **prose is not
    reachability.** A module that must explain why it does NOT name a label
    column, or why it does NOT call the writer, has to be able to say the
    words — and a detector that punishes the explanation teaches the next
    author to delete the explanation.
    """
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


class _RecordingCursor:
    """Minimal psycopg2 cursor stand-in that records what was executed.

    `scenario_exists` answers the existence probe `record_human_label` runs when
    the scoped UPDATE matches nothing — the probe that tells "no such row" apart
    from "already answered". It is consulted only on that path.
    """

    def __init__(self, rowcount: int = 1, scenario_exists: bool = False):
        self.executed: list[tuple[str, dict]] = []
        self.rowcount = rowcount
        self.scenario_exists = scenario_exists
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params or {}))
        if "SELECT 1" in sql:
            self._rows = [(1,)] if self.scenario_exists else []

    def fetchall(self):
        return self._rows


class _RecordingConn:
    def __init__(self, rowcount: int = 1, scenario_exists: bool = False):
        self.cursor_obj = _RecordingCursor(
            rowcount=rowcount, scenario_exists=scenario_exists
        )
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

    def test_a_human_tier_over_an_empty_answer_fails_closed(self):
        """A human claim about a string that is not there resolves to `unknown`.

        `record_human_label` refuses an empty answer and 0016's CHECK refuses to
        store the pair, so no shipped path creates this row — but a downgrade
        and re-upgrade, a partial restore, or a direct write can, and the
        resolver must not then assert that a human authored an empty string.
        Such a row is also excluded from the eval by `WHERE reference_answer !=
        ''`, so the claim would hang on a row nothing ever scores.
        """
        from app.services.eval_service import (
            is_human_labelled,
            label_trust_tier,
            trust_tier_rank,
        )

        for answer in ("", "   ", None):
            row = {
                "source": "mined",
                "reference_answer": answer,
                "label_trust_tier": "human_authored",
            }
            assert label_trust_tier(row) == "unknown"
            assert is_human_labelled(row) is False
            assert trust_tier_rank(label_trust_tier(row)) < trust_tier_rank(
                "model_generated"
            )

    def test_a_projection_without_the_answer_column_is_not_downgraded(self):
        """The check above applies to a PRESENT-and-empty answer only.

        A caller that selected `id, source, label_trust_tier` and no
        `reference_answer` has not told us the answer is empty — it has told us
        nothing about the answer. Downgrading there would make the resolver's
        verdict depend on the caller's SELECT list, which is the kind of
        action-at-a-distance that gets discovered in production.
        """
        from app.services.eval_service import is_human_labelled, label_trust_tier

        row = {"source": "mined", "label_trust_tier": "human_authored"}
        assert label_trust_tier(row) == "human_authored"
        assert is_human_labelled(row) is True

    def test_a_mapping_that_is_not_a_scenario_never_reads_as_human_labelled(self):
        """The decision-eval namespace collision, pinned from both sides.

        `decision_eval_service` published `label_trust_tier: 'human_authored'`
        on every `DecisionFixture` and on its run report, meaning "these
        fixtures were hand-written". Handed to `label_trust_tier()` — which
        reads its key off any mapping — all 23 of them resolved as
        `is_human_labelled() is True`: a human-authorship claim about a
        `reference_answer` those objects do not have, and about a `source` they
        do not have either. Observed by execution during the P1 review; no
        caller did it, and nothing structural stopped one.

        Two fixes, and this asserts the one that does not depend on every other
        module choosing a different spelling: a mapping with neither `source`
        nor `reference_answer` is not an eval scenario and gets `unknown`.
        """
        from app.services.eval_service import is_human_labelled, label_trust_tier

        not_a_scenario = {
            "case_id": "confirm-order-01",
            "label_trust_tier": "human_authored",
        }
        assert label_trust_tier(not_a_scenario) == "unknown"
        assert is_human_labelled(not_a_scenario) is False

    def test_the_decision_eval_no_longer_spells_the_column_name(self):
        """The other half of the same fix, asserted against the real module."""
        import dataclasses

        from app.services import decision_eval_service as des
        from app.services.eval_service import is_human_labelled

        assert not hasattr(des, "FIXTURE_LABEL_TRUST_TIER")
        assert des.FIXTURE_LABEL_PROVENANCE == "human_authored"

        fixtures = des.build_decision_fixtures()
        assert fixtures, "the decision fixture set is empty"
        for fixture in fixtures:
            row = dataclasses.asdict(fixture)
            assert "label_trust_tier" not in row
            assert is_human_labelled(row) is False


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
    attribute access, and string constants that mention the module or the
    symbol anywhere in them — the importlib / getattr / sys.modules back doors.

    WHAT IT CANNOT SEE, stated because the previous version of this docstring
    claimed it saw "every route". A name assembled from fragments —
    `getattr(s, "label" + "_service")`, `import_module("app.services." +
    "label" + "_service")` — leaves no constant containing `label_service` and
    is invisible here. It is invisible to any static check of this shape, which
    is the argument for R4 (the runtime context guard) being the last line, not
    an argument for weakening this one. `test_the_detector_is_blind_to_a_name_
    composed_from_fragments` records that limit so it cannot be forgotten.
    """
    hits: list[str] = []
    watched = {"label_service", "record_human_label"}
    tree = _parse(path)
    docstrings = _docstring_constant_ids(tree)
    for node in ast.walk(tree):
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
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            # Any constant that MENTIONS the module or the symbol, not only one
            # that spells the full dotted path. `import_module("app.services."
            # + "label_service")`, `"app.services.%s" % "label_service"` and
            # `sys.modules["app.services." + "label_service"]` were all
            # invisible to the full-path version of this arm.
            for name in sorted(watched):
                if name in node.value:
                    hits.append(f"string containing {name!r}")
    return hits


def _writer_hits(path: str) -> list[str]:
    """`_references_label_writer`, with the string arm excused for the two
    gate-baseline files. See the comment above GATES_SCRIPT_PATH."""
    hits = _references_label_writer(path)
    if os.path.normpath(path) in (GATES_SCRIPT_PATH, GATES_TEST_PATH):
        hits = [hit for hit in hits if not hit.startswith("string containing")]
    return hits


def _imports_the_api_layer(path: str) -> list[str]:
    """Imports of `app.api` (or a submodule of it) in *path*."""
    hits: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            hits.extend(
                a.name
                for a in node.names
                if a.name == "app.api" or a.name.startswith("app.api.")
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "app.api" or node.module.startswith("app.api."):
                hits.append(node.module)
    return hits


class TestR2ImportBoundary:
    def test_only_the_one_named_api_module_may_reference_the_writer(self):
        """The writer is reachable from `app/api/v1/evals.py` and from nowhere
        else in the tree — including nowhere else under `app/api/`.

        The claim used to be "reachable from an authenticated HTTP request",
        with `app/api/` as the allowed region. That region contains the
        anonymous end-customer surface: `widget.py`'s own header documents
        `/widget/{agent_id}/config` and `/widget/jobs/{job_id}/events` as **no
        auth**, and its chat routes sit behind a short-lived JWT issued to an
        anonymous website visitor. No authentication property was ever asserted
        here and none is asserted now — what is asserted is a module name, so
        that is what the test says.

        `app/worker/` is every Celery task. The rest of `app/services/` is every
        agent tool, every judge, every scenario producer and the eval service
        itself — and the eval service is imported BY the tasks, so a single
        import there would hand the writer to every task in the system through
        the back door.
        """
        offenders: dict[str, list[str]] = {}
        for path in _scanned_source_files():
            if path in (LABEL_SERVICE_PATH, LABEL_WRITER_CALLER):
                continue
            hits = _writer_hits(path)
            if hits:
                offenders[os.path.relpath(path, API_ROOT)] = hits

        assert offenders == {}, (
            "a human trust tier must be unreachable from any agent, task or "
            f"judge — these modules reference the writer: {offenders}"
        )

    def test_no_worker_or_service_module_imports_the_api_layer(self):
        """The companion pin, without which R2's boundary is a module path with
        a hole in it.

        R2 permits `app/api/v1/evals.py` to hold the writer. If any module under
        `app/worker/` or `app/services/` imported `app.api`, a task could reach
        the writer transitively through whatever that module re-exports, and
        every assertion above would still be green. Today no such import exists
        — verified, and now pinned, because it was holding by accident.
        """
        offenders: dict[str, list[str]] = {}
        for root in (WORKER_DIR, SERVICES_DIR):
            for path in _python_files(root):
                hits = _imports_the_api_layer(path)
                if hits:
                    offenders[os.path.relpath(path, API_ROOT)] = hits

        assert offenders == {}, (
            "a worker or service module imports the API layer, which is the "
            f"one region permitted to hold the human-label writer: {offenders}"
        )

    def test_no_test_module_outside_this_one_may_reference_the_writer(self):
        """Every file under `tests/`, not only the two conftests.

        The previous version filtered on `basename == "conftest.py"`, which is
        2 files out of 159; the other 157 include 16 that already define
        `@pytest.fixture`, and R4 is silent in a unit test because there is no
        Celery task and no agent ContextVar in scope. So a fixture in any of
        those 157 could have called `record_human_label` successfully while
        this class stayed green. The allowlist is exactly one module: this one,
        which must reference the writer in order to test it.
        """
        allowed = os.path.normpath(os.path.abspath(__file__))
        offenders: dict[str, list[str]] = {}
        for path in _python_files(TESTS_DIR):
            if os.path.normpath(os.path.abspath(path)) == allowed:
                continue
            hits = _writer_hits(path)
            if hits:
                offenders[os.path.relpath(path, API_ROOT)] = hits

        assert offenders == {}, (
            "a test module outside test_label_provenance.py references the "
            f"human-label writer: {offenders}"
        )

    @pytest.mark.parametrize(
        "route,snippet",
        [
            ("plain import", "import app.services.label_service\n"),
            (
                "from-import of the symbol",
                "from app.services.label_service import record_human_label\n",
            ),
            ("from-import of the module", "from app.services import label_service\n"),
            (
                "attribute call with no import in the file",
                "def f(conn):\n    return label_service.record_human_label(conn)\n",
            ),
            (
                "importlib back door",
                "import importlib\n"
                "m = importlib.import_module('app.services.label_service')\n",
            ),
            (
                "importlib with a concatenated path",
                "import importlib\n"
                "m = importlib.import_module('app.services.' + 'label_service')\n",
            ),
            (
                "importlib with a %-formatted path",
                "import importlib\n"
                "m = importlib.import_module('app.services.%s' % 'label_service')\n",
            ),
            (
                "sys.modules with a concatenated key",
                "import sys\n"
                "m = sys.modules['app.services.' + 'label_service']\n",
            ),
            (
                "getattr with the symbol named as a string",
                "def f(mod):\n    return getattr(mod, 'record_human_label')\n",
            ),
        ],
    )
    def test_the_boundary_detector_sees_each_route_it_claims_to_see(
        self, tmp_path, route, snippet
    ):
        """Each arm of the detector is exercised separately.

        A boundary test is worth what its detector catches, and a detector with
        one arm doing all the work reports a clean tree the moment somebody
        reaches the writer by one of the others. Found by mutation: misspelling
        the watched-name set left the earlier version of this class entirely
        green, because every reference it had ever been shown arrived through
        the module-path arm.

        The last four arms were added 2026-08-09. The five before them were all
        HONEST spellings, so the vacuity check itself lived inside the
        detector's non-blind region — the same structural defect as the one it
        was written to catch, one level up.
        """
        path = tmp_path / "candidate.py"
        path.write_text(snippet, encoding="utf-8")
        assert _references_label_writer(str(path)), (
            f"the detector is blind to the {route} route — every boundary "
            "assertion in this class is vacuous for that route"
        )

    @pytest.mark.parametrize(
        "route,snippet",
        [
            (
                "getattr on a composed module name",
                "def f(s):\n"
                "    return getattr(getattr(s, 'label' + '_service'), "
                "'record' + '_human_label')\n",
            ),
            (
                "import_module on a fragment-assembled path",
                "import importlib\n"
                "m = importlib.import_module('app.services.' + 'label' + '_service')\n",
            ),
        ],
    )
    def test_the_detector_is_blind_to_a_name_composed_from_fragments(
        self, tmp_path, route, snippet
    ):
        """A DOCUMENTED LIMIT, asserted so it cannot quietly be forgotten.

        No static check of this shape can see a name assembled at runtime from
        fragments that individually spell nothing. Writing that down as a
        passing test rather than as a sentence in a docstring means the claim
        "R2 sees every route" can never be restored by accident: it is false,
        here is the counter-example, and here is why R4 exists.

        IF YOU CLOSE THIS GAP, DELETE THIS TEST. It will go red, and that is the
        correct signal — not a reason to weaken the detector.
        """
        path = tmp_path / "evasive.py"
        path.write_text(snippet, encoding="utf-8")
        assert _references_label_writer(str(path)) == [], (
            f"the detector now sees the {route} route — that is an improvement; "
            "delete this test and correct the module docstring's claim about "
            "what R2 cannot see"
        )

    def test_the_boundary_detector_does_not_fire_on_unrelated_label_code(
        self, tmp_path
    ):
        """The negative control. Reading and ranking a label tier is what most
        of the codebase legitimately does; only WRITING one is walled off, so a
        detector that fired on `label_trust_tier` would push the next author
        into weakening it rather than obeying it."""
        path = tmp_path / "innocent.py"
        path.write_text(
            "from app.services.eval_service import label_trust_tier, "
            "is_human_labelled\n"
            "def f(row):\n"
            "    return label_trust_tier(row), is_human_labelled(row)\n",
            encoding="utf-8",
        )
        assert _references_label_writer(str(path)) == []

    def test_the_boundary_detector_does_not_fire_on_prose(self, tmp_path):
        """A docstring that names the writer is not a route to the writer.

        `eval_service.label_trust_tier`'s docstring explains that
        `record_human_label` refuses an empty answer — which is exactly the kind
        of sentence the read path should contain, and exactly the sentence the
        strengthened string arm flagged as a boundary violation on its first
        run. Prose is not reachability; a bare string expression is bound to
        `__doc__` and cannot be imported through.
        """
        path = tmp_path / "prose.py"
        path.write_text(
            '"""The writer lives in label_service and this module never calls it."""\n'
            "def f():\n"
            '    """record_human_label refuses an empty answer."""\n'
            "    return 1\n",
            encoding="utf-8",
        )
        assert _references_label_writer(str(path)) == []

    def test_the_boundary_check_can_actually_see_a_reference(self):
        """The detector run against a real file that references the writer —
        this one — so the scan cannot pass by matching nothing at all."""
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


def _constant_text(node: ast.AST) -> str:
    """Every string constant reachable from *node*, in source order, joined.

    This is what makes the scan below see COMPOSED SQL. `ast.iter_child_nodes`
    visits fields in source order, so an f-string's literal parts, the two sides
    of a `+`, the left operand of a `%`, the receiver of `.format(...)` and the
    separator/elements of `"".join([...])` all reassemble into one string
    without the helper needing a case for each of them.
    """
    parts: list[str] = []

    def walk(current: ast.AST) -> None:
        if isinstance(current, ast.Constant):
            if isinstance(current.value, str):
                parts.append(current.value)
            return
        for child in ast.iter_child_nodes(current):
            walk(child)

    walk(node)
    return "".join(parts)


def _normalised_sql(text: str) -> str:
    """Whitespace-collapsed, upper-cased, schema- and quote-normalised.

    `public.eval_scenarios` and `"eval_scenarios"` are the same table as
    `eval_scenarios`, and both were invisible to the first version of this scan.
    """
    collapsed = " ".join(text.split()).upper()
    return collapsed.replace('"', "").replace("PUBLIC.", "")


_WRITE_MARKERS = ("INSERT INTO EVAL_SCENARIOS", "UPDATE EVAL_SCENARIOS")


def _set_clause_columns(statement: str) -> list[str]:
    """The column names an UPDATE's SET clause assigns to, lower-cased."""
    collapsed = " ".join(statement.split())
    match = re.search(r"\bSET\b(.*?)(?:\bWHERE\b|$)", collapsed, re.IGNORECASE)
    if not match:
        return []
    return [name.lower() for name in re.findall(r"(\w+)\s*=", match.group(1))]


def _scenario_write_statements(path: str) -> list[str]:
    """Every expression in *path* that INSERTs into or UPDATEs eval_scenarios.

    REWRITTEN 2026-08-09 — the previous version collected a single
    `ast.Constant` only, and was demonstrated blind by the P1 adversarial
    review: an f-string `UPDATE` in a real Celery task module stamping
    `label_trust_tier = 'human_authored'` produced no red anywhere. Now the
    candidate nodes are Constant / JoinedStr / BinOp / Call and each is
    flattened by `_constant_text`, so the statement is reconstructed from all of
    its literal parts however it was assembled.

    Deliberately over-collects: the same text can be reported at three
    granularities (Call ⊃ JoinedStr ⊃ Constant), and a Call whose arguments
    happen to hold both a write and a label column name is flagged. Both
    directions fail CLOSED — they make the wall louder, never quieter — which
    is the only acceptable direction for a detector whose failure mode is a
    forged provenance nobody notices.
    """
    statements: list[str] = []
    for node in ast.walk(_parse(path)):
        if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp, ast.Call)):
            continue
        text = _constant_text(node)
        if not text:
            continue
        normalised = _normalised_sql(text)
        if any(marker in normalised for marker in _WRITE_MARKERS):
            statements.append(text)
    return statements


def _label_column_mentions(path: str) -> list[str]:
    """Every AST node in *path* that NAMES a label-provenance column.

    The second, independent half of R3, and the one with no SQL-shape blind
    spot at all: it does not care whether the write is a constant, an f-string,
    a `+` chain, a `.format`, an `ON CONFLICT DO UPDATE`, or a psycopg2
    parameter dict. If a module that may not label a row so much as spells
    `label_trust_tier`, `labelled_by` or `labelled_at` — in a string, an
    identifier, an attribute, a keyword argument or an import alias — it is an
    offender.

    DOCSTRINGS ARE EXEMPT, and for the same reason `_references_label_writer`
    is an AST walk rather than a text search: prose is not reachability. A bare
    string expression statement is bound to `__doc__` and cannot be handed to
    `cur.execute`, and a module that must explain why it does NOT name a label
    column has to be able to say the words. `decision_eval_service` is exactly
    that module.

    Its own blind spot is the mirror image of the statement scan's: a column
    name assembled from fragments (`"label" + "_trust_tier"`) spells nothing
    here. Between the two scans every forgery shape the review probed is seen;
    neither claims forgery is impossible.
    """
    tree = _parse(path)
    docstrings = _docstring_constant_ids(tree)

    mentions: list[str] = []
    for node in ast.walk(tree):
        texts: list[str] = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                texts.append(node.value)
        for attribute in ("id", "attr", "arg", "name", "module"):
            value = getattr(node, attribute, None)
            if isinstance(value, str):
                texts.append(value)
        for text in texts:
            lowered = text.lower()
            for column in LABEL_COLUMNS:
                if column in lowered:
                    mentions.append(f"{column} ({type(node).__name__})")
    return sorted(set(mentions))


# The two modules permitted to name a label column. `label_service` writes them;
# `eval_service` declares `LABEL_TIER_COLUMN` and resolves a row's tier from it,
# which is the read path the whole system uses. The allowlist is the name pin's
# weak point and it is named rather than buried: a forgery composed inside
# `eval_service.py` would evade it, and is caught by the statement scan above
# only if it spells the table name.
_MAY_NAME_A_LABEL_COLUMN = (LABEL_SERVICE_PATH, EVAL_SERVICE_PATH)


class TestR3TheModelWritersCannotWrite:
    def test_only_the_label_writer_writes_the_label_columns(self):
        """Every model-driven producer routes through
        `scenario_service.store_scenarios` or
        `scenario_service.insert_provenance_scenario` — generated suites, mined
        production failures, promoted traces, contained red-team findings.
        Neither statement names a label-provenance column.

        THE CLAIM THIS TEST NO LONGER MAKES: that those producers "physically
        cannot" populate one. They can — by writing raw SQL that never touches
        `label_service` at all, which is precisely what the P1 review did to a
        real Celery task module, in an f-string, with no test going red. What
        this test asserts is that no eval_scenarios write anywhere in the
        scanned tree NAMES a label column, however that write is spelled.
        """
        offenders: dict[str, list[str]] = {}
        for path in _scanned_source_files():
            if path == LABEL_SERVICE_PATH:
                continue
            for statement in _scenario_write_statements(path):
                lowered = statement.lower()
                named = [c for c in LABEL_COLUMNS if c in lowered]
                if named:
                    offenders.setdefault(
                        os.path.relpath(path, API_ROOT), []
                    ).extend(named)

        assert offenders == {}, (
            "an eval_scenarios write outside label_service names a label "
            f"provenance column: {offenders}"
        )

    def test_no_model_driven_module_names_a_label_column_at_all(self):
        """The name-level absence pin — R3's half with no SQL-shape blind spot.

        `app/worker/` (every Celery task), the rest of `app/services/` (every
        agent tool, judge and scenario producer), `scripts/` and `_runlogs/` may
        not so much as mention `label_trust_tier`, `labelled_by` or
        `labelled_at`. A module with no reason to name the column has no reason
        to name it in any syntax, so this needs no model of how SQL is
        assembled — which is exactly the property the statement scan lacked.
        """
        offenders: dict[str, list[str]] = {}
        for root in (WORKER_DIR, SERVICES_DIR, SCRIPTS_DIR, RUNLOGS_DIR):
            if not os.path.isdir(root):
                continue
            for path in _python_files(root):
                if path in _MAY_NAME_A_LABEL_COLUMN:
                    continue
                mentions = _label_column_mentions(path)
                if mentions:
                    offenders[os.path.relpath(path, API_ROOT)] = mentions

        assert offenders == {}, (
            "a module that may not label a row names a label-provenance "
            f"column: {offenders}"
        )

    def test_the_two_allowlisted_readers_issue_no_eval_scenarios_write(self):
        """The allowlist is bounded by a second assertion, not by trust.

        `eval_service.py` is permitted to name the columns because it declares
        and resolves them. It is not permitted to WRITE them, and the statement
        scan is what says so — so the allowlist above cannot become the hole the
        name pin was added to close.
        """
        statements = _scenario_write_statements(EVAL_SERVICE_PATH)
        assert statements == [], (
            "eval_service.py is allowlisted for NAMING label columns because it "
            "is the read path; it must issue no eval_scenarios write at all, "
            f"and it issues: {statements}"
        )

    @pytest.mark.parametrize(
        "shape,snippet",
        [
            (
                "f-string UPDATE with the column name composed from a constant",
                '_ADV_TIER_COL = "label_trust_tier"\n'
                "def forge(conn, scenario_id, answer):\n"
                "    with conn.cursor() as cur:\n"
                "        cur.execute(\n"
                '            f"UPDATE eval_scenarios SET reference_answer = %s, '
                '{_ADV_TIER_COL} = "\n'
                "            f\"'human_authored', labelled_by = 'run_eval_suite', "
                'labelled_at = NOW() "\n'
                '            f"WHERE id = %s::uuid", (answer, scenario_id))\n',
            ),
            (
                "explicit + concatenation",
                "def forge(cur, sid):\n"
                "    cur.execute('UPDATE eval_scenarios SET label_trust_tier = ' +\n"
                "                \"'human_authored' WHERE id = %s\", (sid,))\n",
            ),
            (
                "schema-qualified table name",
                "def forge(cur, sid):\n"
                "    cur.execute(\"UPDATE public.eval_scenarios SET labelled_by = "
                "'x' WHERE id = %s\", (sid,))\n",
            ),
            (
                "quoted identifier",
                "def forge(cur, sid):\n"
                '    cur.execute(\'UPDATE "eval_scenarios" SET labelled_at = NOW() '
                "WHERE id = %s', (sid,))\n",
            ),
            (
                "ON CONFLICT DO UPDATE inside an f-string",
                "_T = 'human_authored'\n"
                "def forge(cur, sid):\n"
                '    cur.execute(f"INSERT INTO eval_scenarios (id, source, question, '
                'reference_answer, label_trust_tier) VALUES (%s, \'mined\', %s, %s, '
                "'{_T}') ON CONFLICT (id) DO UPDATE SET label_trust_tier = "
                "EXCLUDED.label_trust_tier\", (sid, 'q', 'a'))\n",
            ),
            (
                ".format()",
                "def forge(cur, sid, tier):\n"
                "    cur.execute('UPDATE eval_scenarios SET label_trust_tier = "
                "{!r}'.format(tier))\n",
            ),
            (
                "%-formatting",
                "def forge(cur, tier):\n"
                "    cur.execute('UPDATE eval_scenarios SET labelled_by = %s' % tier)\n",
            ),
            (
                "str.join of fragments",
                "def forge(cur):\n"
                "    cur.execute(' '.join(['UPDATE eval_scenarios',\n"
                "                          \"SET labelled_at = NOW()\"]))\n",
            ),
        ],
    )
    def test_the_write_scan_sees_a_forged_label_write_however_it_is_spelled(
        self, tmp_path, shape, snippet
    ):
        """The permanent negative-control fixtures.

        THE F-STRING SHAPE IS THE ONE THAT WAS ACTUALLY OBSERVED TO PASS. The
        P1 adversarial review appended it verbatim to
        `app/worker/tasks/runtime/eval.py` and ran this file: 59 passed at
        baseline, 59 passed with the forgery in place, no red. The semantically
        identical forgery written as one plain string constant went red. So the
        guard caught a spelling, not a capability — and the implementer's own
        mutation proof used exactly the spelling the detector could see.

        Every shape below is now a fixture rather than a probe somebody ran
        once, so the blind spot cannot silently return.
        """
        path = tmp_path / "forgery.py"
        path.write_text(snippet, encoding="utf-8")

        statements = _scenario_write_statements(str(path))
        named = [
            column
            for statement in statements
            for column in LABEL_COLUMNS
            if column in statement.lower()
        ]
        mentions = _label_column_mentions(str(path))

        assert named or mentions, (
            f"R3 is blind to the {shape!r} forgery — a module that never "
            "imports label_service can stamp a human tier and no test goes red"
        )

    def test_the_write_scan_does_not_fire_on_a_read(self, tmp_path):
        """The negative control for the statement scan.

        Selecting the label columns is what P2's queue and P3's consumers are
        supposed to do. A scan that fired on a SELECT would push the next author
        into weakening it rather than obeying it — the same failure mode as a
        detector that fires on `label_trust_tier` in a comment.
        """
        path = tmp_path / "reader.py"
        path.write_text(
            "def read(cur):\n"
            "    cur.execute('SELECT id, label_trust_tier, labelled_by, "
            "labelled_at FROM eval_scenarios')\n"
            "    return cur.fetchall()\n",
            encoding="utf-8",
        )
        assert _scenario_write_statements(str(path)) == []

    def test_the_name_pin_fires_on_code_and_not_on_prose(self, tmp_path):
        """The name pin's own vacuity check, both directions.

        Without the first half, `test_no_model_driven_module_names_a_label_
        column_at_all` could pass by detecting nothing at all. Without the
        second, the pin would fire on any module that explains why it does not
        name a label column — which is how a wall teaches the next author to
        delete the wall.
        """
        named = tmp_path / "named.py"
        named.write_text(
            "def forge(cur, sid):\n"
            "    column = 'label_trust_tier'\n"
            "    cur.execute('UPDATE t SET ' + column + \" = 'human_authored'\")\n",
            encoding="utf-8",
        )
        assert _label_column_mentions(str(named))

        prose = tmp_path / "prose.py"
        prose.write_text(
            '"""This module deliberately never writes label_trust_tier."""\n'
            "def f():\n"
            '    """Not labelled_by anyone, not labelled_at any time."""\n'
            "    return 1\n",
            encoding="utf-8",
        )
        assert _label_column_mentions(str(prose)) == []

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
        label.

        Asserted on the SET clause's column NAMES, not as `"source" not in
        joined` over the raw SQL. The substring form passed only because no
        column in the statement happened to contain those six letters, and it
        would have fired on `source_document_id`, `datasource` or a CTE alias —
        a failure with nothing to do with touching `eval_scenarios.source`,
        which teaches the next author to edit the test rather than obey it.
        """
        from app.services import label_service

        statements = _scenario_write_statements(LABEL_SERVICE_PATH)
        assert statements, "label_service contains no eval_scenarios write"

        written: set[str] = set()
        for statement in statements:
            written.update(_set_clause_columns(statement))

        assert written == {
            "reference_answer",
            "label_trust_tier",
            "labelled_by",
            "labelled_at",
        }, f"the human-label UPDATE writes an unexpected column set: {written}"
        assert "source" not in written, (
            "the human-label UPDATE touches eval_scenarios.source"
        )
        assert "dataset" not in written, (
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

    def test_a_broken_celery_detector_refuses_rather_than_proceeding(
        self, monkeypatch
    ):
        """The one function whose entire job is to fail closed used to be the
        only place in the module that failed open.

        Both detectors wrapped everything in `except Exception: return None`,
        and both except-branches carried `# pragma: no cover` — excluded from
        coverage by declaration and exercised by nothing. Any malfunction in
        DETECTING the context made `assert_human_context()` silent, and
        `record_human_label` went on to stamp `human_authored`. A detector that
        could not answer must refuse; "I could not tell" is not "no model is
        driving this".
        """
        from celery import _state

        from app.services.label_service import HumanLabelRefused, record_human_label

        def _explode():
            raise RuntimeError("current-task stack is wedged")

        monkeypatch.setattr(_state, "get_current_task", _explode)

        conn = _RecordingConn()
        with pytest.raises(HumanLabelRefused) as excinfo:
            record_human_label(
                conn,
                scenario_id="11111111-1111-1111-1111-111111111111",
                reference_answer="Refunds are processed within 14 days.",
                labelled_by="owner@example.com",
            )

        assert "could not determine" in str(excinfo.value)
        assert conn.cursor_calls == 0

    def test_a_broken_agent_detector_refuses_rather_than_proceeding(
        self, monkeypatch
    ):
        """The agent-context arm of the same split."""
        from app.services import agent_tools
        from app.services.label_service import HumanLabelRefused, record_human_label

        class _WedgedVar:
            def get(self):
                raise LookupError("contextvar lookup failed")

        monkeypatch.setattr(agent_tools, "_agent_id_var", _WedgedVar())

        conn = _RecordingConn()
        with pytest.raises(HumanLabelRefused) as excinfo:
            record_human_label(
                conn,
                scenario_id="11111111-1111-1111-1111-111111111111",
                reference_answer="Refunds are processed within 14 days.",
                labelled_by="owner@example.com",
            )

        assert "could not determine" in str(excinfo.value)
        assert conn.cursor_calls == 0

    @pytest.mark.parametrize(
        "absent_module", ["celery", "app.services.agent_tools"]
    )
    def test_an_absent_dependency_is_not_a_malfunction(
        self, monkeypatch, absent_module
    ):
        """The OTHER half of the split, and the reason it is a split.

        The lazy imports exist so this module stays importable in a process with
        no Celery wiring and no agent stack — "a guard that raises on import is
        a guard that gets deleted". An absent dependency means there is no such
        context to be inside, so `None`/`''` is the TRUE answer and the guard
        stays silent. Only a dependency that is present and then misbehaves is a
        malfunction. Setting the module to None in sys.modules is what makes an
        import of it raise ImportError, which is the real shape of "not
        installed here".
        """
        import sys

        from app.services.label_service import assert_human_context

        monkeypatch.setitem(sys.modules, absent_module, None)
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
            # False on every successful write: the existence probe is an
            # error-path question and is not asked when a row was labelled.
            "already_labelled": False,
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
        labelling rate can never be built without its denominator.

        And zero rows now has TWO causes, because the UPDATE is scoped to an
        unlabelled row: the id is absent from this database, or it is present and
        already answered. Both are reported; neither is raised.
        """
        conn = _RecordingConn(rowcount=0, scenario_exists=False)
        result = self._call(conn)
        assert result["rows_updated"] == 0
        assert result["already_labelled"] is False

        relabel = _RecordingConn(rowcount=0, scenario_exists=True)
        result = self._call(relabel)
        assert result["rows_updated"] == 0
        assert result["already_labelled"] is True, (
            "a POST against an already-answered scenario is indistinguishable "
            "from one against a row that does not exist"
        )

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

        THE ASSERTION IS THE REFUSAL REASON, NOT THE COUNT (D6 P3 review,
        finding 6). It used to read `sum(refusals.values()) == 1`, which after
        P3 added the decision gate was satisfied by EITHER lock. The review
        deleted the resolver gate outright and this test stayed green with the
        exact lock its docstring claims to pin entirely removed. `customer_negative`
        is `source='mined'` resolved — the QUESTION's origin — so the assertion
        now fails if the gate is swapped to the label's tier, which is the
        one-line change it exists to catch.
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
        assert refusals == {"trust_tier:customer_negative": 1}, (
            "a top-scoring human-labelled row was not refused on the tier of "
            f"its QUESTION's origin: {refusals}"
        )

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


class TestTheWriteChangesNothingElse:
    """D6 P3 — the downstream half, for the one fact only this module may read.

    Everything else P3 asserts lives in tests/unit/test_label_downstream.py.
    This one cannot: R2 above forbids every test module but this one from so
    much as naming the writer, and this is a statement ABOUT the writer. The
    rest of P3 reaches the same tier through
    `eval_service.VERIFIED_QA_PROMOTION_DECISION["producible_label_tier"]`,
    which the test below pins equal to the writer's own constant — so the
    indirection is a pin rather than a hope.

    THIS CLASS HELD A SECOND TEST AND IT WAS A DUPLICATE (D6 P3 review, finding
    2). `test_the_label_write_assigns_exactly_four_columns_and_never_dataset`
    parsed the SET clause off the writer's SQL constant and asserted the
    four-column set by equality. P2 already asserts exactly that, by equality,
    with a more robust regex parse, driven through the real route and the real
    writer rather than off a module constant:
    `test_eval_label_queue.py::TestTheLabelWrite::
    test_a_label_is_recorded_at_the_human_authored_tier`. The review's mutation
    (adding `dataset = COALESCE(dataset, 'golden')` to the UPDATE) turned that
    P2 test red along with it. Deleted, so the ignored-new-files control carries
    one `--deselect` instead of two — and the control is the one instrument that
    can see a pre-existing test silently changing status, so every hand-
    maintained node id in it is a cost.
    """

    def test_the_tier_the_writer_stamps_is_the_tier_the_run_record_names(self):
        """One spelling, pinned across the boundary the import wall creates.

        `eval_service` cannot import the writer to state this tier — the writer
        imports `eval_service`, so the dependency only runs one way — and no
        other test module may name the writer. So the run record spells the tier
        as a literal and this is the single place the two are compared. Without
        it, editing HUMAN_AUTHORED_TIER would leave every run recording a
        promotion decision about a tier nothing produces.
        """
        from app.services.eval_service import VERIFIED_QA_PROMOTION_DECISION
        from app.services.label_service import HUMAN_AUTHORED_TIER

        assert (
            VERIFIED_QA_PROMOTION_DECISION["producible_label_tier"]
            == HUMAN_AUTHORED_TIER
        )
