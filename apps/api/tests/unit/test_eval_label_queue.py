"""
D6 P2 — the labelling queue: GET the unlabelled rows, POST one human answer.

    GET  /api/v1/agents/{agent_id}/eval-scenarios/unlabelled
    POST /api/v1/agents/{agent_id}/eval-scenarios/{scenario_id}/label

WHAT THIS FILE IS ACTUALLY DEFENDING, in the order the defects would bite:

  1. TENANT ISOLATION. A labelling route that crosses tenants writes one
     business's answer into another business's eval set. The isolation is
     structural — the ownership check 404s before anything is decrypted, and the
     only database a queue route opens is the one behind THAT agent's own
     connection string — and both halves are asserted here, including that a
     cross-tenant request never reaches `fernet_decrypt` or `psycopg2.connect`.

  2. THE AUTHOR IS DERIVED, NEVER SUBMITTED. `labelled_by` comes from the
     authenticated principal and the body forbids extra fields. A caller able to
     name the human is a caller able to name any human, which is exactly the
     argument that removed the tier parameter from the writer in P1.

  3. UNKNOWN IS NOT ZERO. Migration 0016 has not been applied to any database,
     so `label_trust_tier` does not exist anywhere yet. `human_labelled` is
     `null` with `label_provenance_available: false` beside it on that path, not
     `0` — "no way to tell" and "none" are different claims.

  4. THE ORDERING IS NOT WHAT THE PLAN ASKED FOR, AND SAYS SO ON THE WIRE.
     The plan asks for uncertainty ordering. The judge-confidence signal is not
     joinable to a scenario (see `evals.QUEUE_ORDERING`), so the queue is
     ordered by origin trust tier then oldest-first, and the response carries
     `by_uncertainty: false` with the reason. A proxy presented as uncertainty
     would be the defect; an ordering that admits what it is, is not.

WHAT IS NOT PROVEN HERE, PLAINLY. There is no PostgreSQL server on this machine.
No query in this file has been executed by a database. `array_position(...)
NULLS LAST`, the `FILTER (WHERE ...)` counts, and the identity
`unlabelled + labelled == total` are asserted against a recording cursor and at
the SQL-string level; the ordering the database would actually produce has never
been observed. Migration 0016 has never been applied, so the 200 path of the
label write has never touched a real `label_trust_tier` column — what has been
observed is the statement it emits and the parameters it binds.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import psycopg2
import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_current_tenant
from app.api.v1 import evals as evals_module
from app.main import app
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services.eval_service import (
    SCENARIO_SOURCE_TRUST_TIER,
    scenario_trust_tier,
    trust_tier_rank,
)

_TESTS_DIR = os.path.dirname(__file__)
API_ROOT = os.path.normpath(os.path.join(_TESTS_DIR, "../.."))
EVALS_ROUTE_PATH = os.path.normpath(
    os.path.join(API_ROOT, "app", "api", "v1", "evals.py")
)
_MIGRATION_0011 = os.path.normpath(
    os.path.join(API_ROOT, "alembic_tenant/versions/0011_eval_scenarios_provenance.py")
)


def _schema_allowed_scenario_sources() -> list[str]:
    """The eval_scenarios.source values 0011's CHECK permits.

    Parsed, never restated — the same parser test_label_provenance and
    test_eval_service use, and for the same reason: a hardcoded list lets the
    schema and the tier tables drift apart silently, which is the exact failure
    the tier tables exist to prevent.
    """
    with open(_MIGRATION_0011, encoding="utf-8") as fh:
        source = fh.read()
    clauses = re.findall(r"CHECK \(source IN \(([^)]*)\)\)", source)
    assert clauses, (
        "could not find the eval_scenarios.source CHECK in migration 0011 — a "
        "parse failure here is a real failure, not a skip"
    )
    allowed: set[str] = set()
    for clause in clauses:
        allowed.update(re.findall(r"'([^']+)'", clause))
    return sorted(allowed)


# ---------------------------------------------------------------------------
# Fixtures: a tenant, an agent, and a cursor that records instead of connecting
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_ambient_agent_context():
    """Start every test in this module with no agent context in scope.

    NOT boilerplate, and NOT a workaround — it establishes the precondition each
    POST test below claims to exercise, and it was added because 11 of them
    FAILED IN THE FULL SUITE while passing in isolation.

    THE OBSERVED CAUSE, verbatim from the failing run's log line:

        label_eval_scenario.refused_context ...
        reason="a human trust tier may not be stamped from inside an agent tool
        context (agent_id='agent-reset-test'); ..."

    `agent_tools.build_tool_server()` sets `_agent_id_var` and never clears it —
    correctly, since it is setting up a turn — and `tests/unit/test_agent_tools.py:686`
    calls it with `agent_id='agent-reset-test'`. That value is then live for the
    REST OF THE PYTEST PROCESS, so the label route's R4 guard correctly reported
    an agent context and returned 500 where the test expected 200, 404 or 422.
    This is `BACKLOG 4.6`, and `test_label_provenance.py` needed the identical
    fixture for the identical reason — the second module to pay for it.

    The guard was right and the tests were wrong: the direction of the leak is
    fail-CLOSED (it refuses more, never less), which is why it produced 500s and
    not silent forged labels. In a real ASGI process each request runs in its own
    asyncio Task, whose context is a copy, so a `set()` inside one request does
    not propagate to the next — but that is an argument about production, not a
    reason to let a unit test assert against a precondition it never established.
    """
    from app.services.agent_tools import _agent_id_var

    token = _agent_id_var.set("")
    try:
        yield
    finally:
        _agent_id_var.reset(token)


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_ready_agent(tenant: Tenant) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_returning_agent(agent: Agent) -> AsyncMock:
    session = AsyncMock()
    session.get = AsyncMock(return_value=agent)
    return session


def _make_mock_db_returning_none() -> AsyncMock:
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    return session


def _queue_row(
    source: str = "mined",
    question: str = "Where is my order?",
    created_at: datetime | None = None,
) -> tuple:
    """One row in _UNLABELLED_QUEUE_SQL's projection order."""
    return (
        uuid4(),
        source,
        question,
        "production_failure",
        [],
        None,
        None,
        created_at or datetime(2026, 8, 1, 9, 0, 0, tzinfo=timezone.utc),
    )


class _RecordingCursor:
    """A psycopg2 cursor stand-in that records statements instead of running them.

    Routes a statement to one of three canned outcomes by looking at the SQL:
    the counts aggregate, the queue page, or the label write. It is deliberately
    NOT a mock with a fixed return value — the point of these tests is what the
    route asks the database for, so the statement and its bound parameters are
    kept and asserted on.

    `pre_0016` reproduces the state EVERY tenant database is in today: a
    statement naming `label_trust_tier` raises UndefinedColumn, exactly as
    Postgres would before migration 0016 is applied.
    """

    def __init__(self, owner: "_RecordingConn"):
        self.owner = owner
        self.rowcount = -1
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.owner.executed.append((sql, dict(params or {})))
        if self.owner.pre_0016 and "label_trust_tier" in sql:
            raise psycopg2.errors.UndefinedColumn(
                'column "label_trust_tier" does not exist'
            )
        if "COUNT(*)" in sql:
            self._rows = [self.owner.counts_row_pre_0016] if self.owner.pre_0016 else [
                self.owner.counts_row
            ]
            self.rowcount = len(self._rows)
        elif "LIMIT" in sql:
            self._rows = list(self.owner.page_rows)
            self.rowcount = len(self._rows)
        else:
            self._rows = []
            self.rowcount = self.owner.write_rowcount

    def fetchall(self):
        return self._rows


class _RecordingConn:
    def __init__(
        self,
        counts_row: tuple = (10, 4, 6, 2),
        page_rows: list[tuple] | None = None,
        write_rowcount: int = 1,
        pre_0016: bool = False,
    ):
        self.counts_row = counts_row
        self.counts_row_pre_0016 = counts_row[:3]
        self.page_rows = page_rows if page_rows is not None else [_queue_row()]
        self.write_rowcount = write_rowcount
        self.pre_0016 = pre_0016
        self.executed: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self):
        return _RecordingCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


async def _get_queue(conn: _RecordingConn, query: str = "") -> tuple[int, dict]:
    """Drive the GET route against *conn*; return (status_code, body)."""
    tenant = _make_fake_tenant()
    agent = _make_ready_agent(tenant)
    app.dependency_overrides[get_current_tenant] = lambda: tenant
    app.dependency_overrides[get_async_db] = lambda: _make_mock_db_returning_agent(agent)
    try:
        with (
            patch(
                "app.api.v1.evals.fernet_decrypt",
                return_value="postgresql://fake/tenantdb",
            ),
            patch("app.api.v1.evals.psycopg2.connect", return_value=conn),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{agent.id}/eval-scenarios/unlabelled{query}",
                    headers={"X-API-Key": "vrd_live_test"},
                )
    finally:
        app.dependency_overrides.clear()
    return response.status_code, response.json()


async def _post_label(
    conn: _RecordingConn,
    body: dict | None = None,
    scenario_id=None,
    tenant: Tenant | None = None,
    agent: Agent | None = None,
) -> tuple[int, dict]:
    """Drive the POST route against *conn*; return (status_code, body)."""
    tenant = tenant or _make_fake_tenant()
    agent = agent or _make_ready_agent(tenant)
    app.dependency_overrides[get_current_tenant] = lambda: tenant
    app.dependency_overrides[get_async_db] = lambda: _make_mock_db_returning_agent(agent)
    try:
        with (
            patch(
                "app.api.v1.evals.fernet_decrypt",
                return_value="postgresql://fake/tenantdb",
            ),
            patch("app.api.v1.evals.psycopg2.connect", return_value=conn),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent.id}/eval-scenarios/"
                    f"{scenario_id or uuid4()}/label",
                    json=body if body is not None else {"reference_answer": "Ships in 3 days."},
                    headers={"X-API-Key": "vrd_live_test"},
                )
    finally:
        app.dependency_overrides.clear()
    return response.status_code, response.json()


# ---------------------------------------------------------------------------
# The ordering, and the honesty about what it is not
# ---------------------------------------------------------------------------


class TestQueueOrdering:
    def test_a_customer_negative_origin_outranks_a_model_generated_one(self):
        """The behavioural half of the ordering, exercised in Python.

        Every source that labels a real customer failure — mined production
        failures, owner-filed traces, contained red-team findings — must sort
        ahead of `generated`, whose unlabelled rows are artefacts of a
        generation that came out without an answer. The ranks are read from
        eval_service rather than restated, so this fails if the tier tables move.
        """
        order = evals_module._source_priority_order()
        ranks = [trust_tier_rank(scenario_trust_tier(s)) for s in order]

        assert ranks == sorted(ranks, reverse=True), (
            f"the priority order is not descending by trust tier: "
            f"{list(zip(order, ranks))}"
        )
        assert order.index("mined") < order.index("generated")
        assert order.index("production") < order.index("generated")
        assert order.index("red_team") < order.index("generated")

    def test_the_priority_order_covers_every_source_the_schema_allows(self):
        """A source added to 0011's CHECK without a tier drops out of the order.

        `array_position` returns NULL for a source absent from the array and the
        query sorts NULLS LAST, so the failure direction is 'an unclassified
        origin sorts last' rather than 'sorts first'. That is the safe
        direction, and this test is what makes anyone notice it happened.
        """
        assert sorted(evals_module._source_priority_order()) == (
            _schema_allowed_scenario_sources()
        ), (
            "a source the schema allows is missing from the queue's priority "
            "order, or the order names one the schema does not allow"
        )
        assert sorted(SCENARIO_SOURCE_TRUST_TIER) == _schema_allowed_scenario_sources()

    def test_the_order_is_deterministic_between_sources_sharing_a_tier(self):
        """Three sources share `customer_negative`. Without a secondary key the
        order between them would follow dict iteration and could change under a
        refactor, silently reshuffling the owner's queue."""
        assert evals_module._source_priority_order() == (
            evals_module._source_priority_order()
        )
        assert evals_module._source_priority_order() == [
            "mined",
            "production",
            "red_team",
            "generated",
        ]

    def test_the_queue_is_ordered_oldest_first_and_not_by_recency(self):
        """The plan's one explicit prohibition: not recency.

        Oldest-first is the opposite of recency, not a dressed-up version of it
        — the oldest unlabelled row has been unmeasurable the longest, and
        newest-first starves the tail of the queue permanently.
        """
        sql = evals_module._UNLABELLED_QUEUE_SQL
        assert "created_at ASC" in sql
        assert "created_at DESC" not in sql, (
            "the labelling queue is ordered by recency, which the plan "
            "explicitly rules out"
        )

    def test_the_ordering_is_a_total_order_so_pagination_cannot_skip_a_row(self):
        """`id` is the final key. Without it two rows sharing a source and a
        created_at have no defined relative position, and LIMIT/OFFSET paging
        can then show one row twice and never show another at all."""
        sql = evals_module._UNLABELLED_QUEUE_SQL
        order_by = sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]
        keys = [line.strip().rstrip(",") for line in order_by.strip().splitlines()]
        assert keys[-1] == "id ASC", f"the last ordering key is {keys[-1]!r}, not the row id"

    def test_an_unclassified_source_sorts_last_rather_than_first(self):
        sql = evals_module._UNLABELLED_QUEUE_SQL
        assert "NULLS LAST" in sql, (
            "array_position returns NULL for a source absent from the priority "
            "array; without NULLS LAST an unclassified origin would sort to the "
            "front of the owner's queue"
        )

    def test_the_page_query_binds_the_priority_order_as_a_parameter(self):
        """Not string-interpolated into the SQL. The order is data the query
        binds, so it cannot become an injection surface as the tier tables grow.
        """
        assert "%(source_priority)s::text[]" in evals_module._UNLABELLED_QUEUE_SQL

    async def test_the_response_states_that_this_is_not_an_uncertainty_ordering(self):
        """The honesty is IN THE PAYLOAD, not only in a comment nobody reads.

        The plan asked for uncertainty ordering. This is not one. A console that
        renders this queue as 'the rows the judges were least sure about' would
        be making a claim the data cannot support, so the response says
        by_uncertainty: false and carries the reason.
        """
        status, body = await _get_queue(_RecordingConn())

        assert status == 200
        ordering = body["ordering"]
        assert ordering["by_uncertainty"] is False
        assert ordering["keys"] == [
            "origin_trust_tier DESC",
            "created_at ASC",
            "id ASC",
        ]
        reason = ordering["reason"]
        assert "job_events" in reason and "control-DB" in reason, (
            "the reason must name the actual obstacle — job_events is a "
            "control-DB table and eval_scenarios is not — rather than being a "
            "vague apology"
        )
        assert "confidence" in reason

    def test_the_ordering_record_cannot_be_mutated_through_the_response(self):
        """QUEUE_ORDERING is copied at the use site, matching
        VERIFIED_QA_PROMOTION_DECISION. A caller mutating the returned dict must
        not be able to poison the constant for every later request."""
        source = inspect.getsource(evals_module.list_unlabelled_scenarios)
        assert "dict(QUEUE_ORDERING)" in source


# ---------------------------------------------------------------------------
# The selector: this route must not change it, and must be its exact complement
# ---------------------------------------------------------------------------


class TestTheSelectorIsUntouched:
    def test_the_queue_selects_exactly_what_the_eval_selector_excludes(self):
        """The cross-module pin. The two halves of this guarantee live in
        different modules, so the predicate is read out of the task's own source
        rather than restated here.

        If `run_eval_suite` ever stops filtering on `reference_answer != ''`,
        this queue's definition of 'unlabelled' silently stops meaning 'will
        never be scored', and that is the moment to find out.
        """
        from app.worker.tasks.runtime import eval as eval_task

        selector = inspect.getsource(eval_task.run_eval_suite)
        predicate = evals_module.SELECTOR_ELIGIBILITY_PREDICATE

        assert predicate in selector, (
            f"run_eval_suite no longer filters on {predicate!r} — the labelling "
            "queue's 'unlabelled' and the eval's 'will never be scored' have "
            "come apart"
        )
        assert f"NOT ({predicate})" in evals_module._UNLABELLED_QUEUE_SQL
        assert f"NOT ({predicate})" in evals_module._QUEUE_COUNTS_SQL
        assert f"NOT ({predicate})" in evals_module._QUEUE_COUNTS_PRE_0016_SQL

    def test_this_module_issues_no_write_of_its_own_to_eval_scenarios(self):
        """The route must not grow its own INSERT or UPDATE.

        R3 in test_label_provenance scans this file too, but it only fires on a
        write that also NAMES a label column — a bare
        `SET reference_answer = ...` here would pass it and would put a second
        write path beside the one the four restrictions were built around.
        """
        with open(EVALS_ROUTE_PATH, encoding="utf-8") as fh:
            body = " ".join(fh.read().split()).upper()

        for marker in ("UPDATE EVAL_SCENARIOS", "INSERT INTO EVAL_SCENARIOS"):
            assert marker not in body, (
                f"{EVALS_ROUTE_PATH} contains {marker!r} — the human label has a "
                "second write path that bypasses the service layer"
            )


# ---------------------------------------------------------------------------
# The counts, and their denominator
# ---------------------------------------------------------------------------


class TestQueueCounts:
    async def test_every_count_travels_with_its_denominator(self):
        """A rate without its denominator must not be constructible from this
        response. `total` is that denominator and it is always present."""
        status, body = await _get_queue(_RecordingConn(counts_row=(10, 4, 6, 2)))

        assert status == 200
        counts = body["counts"]
        assert counts["total"] == 10
        assert counts["unlabelled"] == 4
        assert counts["labelled"] == 6
        assert counts["unlabelled"] + counts["labelled"] == counts["total"]

    async def test_eligible_is_labelled_because_the_selector_needs_no_change(self):
        """The P2 claim, made readable from the payload instead of asserted in
        prose: writing an answer is the whole of what makes a row eligible."""
        status, body = await _get_queue(_RecordingConn(counts_row=(10, 4, 6, 2)))

        assert status == 200
        assert body["counts"]["eligible"] == body["counts"]["labelled"] == 6

    async def test_human_labelled_is_unknown_not_zero_before_migration_0016(self):
        """THE STATE EVERY TENANT DATABASE IS IN TODAY.

        0016 has not been applied anywhere, so `label_trust_tier` does not
        exist and the count cannot be taken. Reporting 0 would assert 'no human
        has labelled anything', which is a measurement this route did not make.
        Null plus `label_provenance_available: false` says which of the two
        happened — the same shape `datasets.available` already uses for the
        pre-0014 `dataset` column.
        """
        status, body = await _get_queue(_RecordingConn(pre_0016=True))

        assert status == 200
        counts = body["counts"]
        assert counts["human_labelled"] is None, (
            "an unmeasurable count was reported as zero, which claims a "
            "measurement that was never taken"
        )
        assert counts["label_provenance_available"] is False
        # The rest of the queue still works — this is a degradation, not an outage
        assert counts["total"] == 10
        assert counts["unlabelled"] == 4

    async def test_human_labelled_is_a_number_once_the_column_exists(self):
        """The negative control for the test above: when the column IS there the
        count is reported, so `None` means 'could not tell' and never 'always'."""
        status, body = await _get_queue(_RecordingConn(counts_row=(10, 4, 6, 2)))

        assert status == 200
        assert body["counts"]["human_labelled"] == 2
        assert body["counts"]["label_provenance_available"] is True

    async def test_the_counts_query_binds_the_human_tiers_rather_than_spelling_them(
        self,
    ):
        status, _ = await _get_queue(_RecordingConn())
        assert status == 200
        assert "%(human_tiers)s::text[]" in evals_module._QUEUE_COUNTS_SQL

    async def test_an_empty_tenant_database_reports_zeroes_and_not_an_error(self):
        conn = _RecordingConn(counts_row=(0, 0, 0, 0), page_rows=[])
        status, body = await _get_queue(conn)

        assert status == 200
        assert body["scenarios"] == []
        assert body["counts"]["total"] == 0
        assert body["page"]["returned"] == 0


# ---------------------------------------------------------------------------
# The queue page itself
# ---------------------------------------------------------------------------


class TestUnlabelledQueuePage:
    async def test_a_row_carries_its_origin_tier_named_as_an_origin_tier(self):
        """`origin_trust_tier` is the tier the QUESTION's source earns. It is not
        the tier a label would carry, and the wire keeps the two apart the same
        way the schema does — fusing them is the defect D6 P1 exists to prevent.
        """
        conn = _RecordingConn(page_rows=[_queue_row(source="mined")])
        status, body = await _get_queue(conn)

        assert status == 200
        row = body["scenarios"][0]
        assert row["source"] == "mined"
        assert row["origin_trust_tier"] == "customer_negative"
        assert "label_trust_tier" not in row, (
            "the queue reports a label tier for a row that has no label"
        )

    async def test_the_page_reports_its_own_bounds(self):
        conn = _RecordingConn(page_rows=[_queue_row(), _queue_row()])
        status, body = await _get_queue(conn, query="?limit=5&offset=10")

        assert status == 200
        assert body["page"] == {"limit": 5, "offset": 10, "returned": 2}
        page_sql = [s for s, _ in conn.executed if "LIMIT" in s]
        assert page_sql, "no page query was issued"
        params = [p for s, p in conn.executed if "LIMIT" in s][0]
        assert params["limit"] == 5
        assert params["offset"] == 10
        assert params["source_priority"] == evals_module._source_priority_order()

    @pytest.mark.parametrize("query", ["?limit=0", "?limit=101", "?offset=-1"])
    async def test_out_of_range_paging_is_rejected(self, query):
        status, _ = await _get_queue(_RecordingConn(), query=query)
        assert status == 422

    async def test_the_queue_returns_the_context_the_owner_needs_to_answer(self):
        conn = _RecordingConn(page_rows=[_queue_row()])
        status, body = await _get_queue(conn)

        assert status == 200
        row = body["scenarios"][0]
        assert row["question"] == "Where is my order?"
        assert row["retrieved_contexts"] == []
        assert row["created_at"] is not None


# ---------------------------------------------------------------------------
# The write: what it stamps, and what it refuses to be told
# ---------------------------------------------------------------------------


class TestTheLabelWrite:
    async def test_a_label_is_recorded_at_the_human_authored_tier(self):
        """The happy path, driven through the REAL service write against a
        recording cursor — not a mocked writer, because the statement it emits
        is the thing worth asserting."""
        conn = _RecordingConn()
        scenario_id = uuid4()
        status, body = await _post_label(conn, scenario_id=scenario_id)

        assert status == 200, body
        assert body["scenario_id"] == str(scenario_id)
        assert body["label_trust_tier"] == "human_authored"

        writes = [
            (sql, params)
            for sql, params in conn.executed
            if "COUNT(*)" not in sql and "LIMIT" not in sql
        ]
        assert len(writes) == 1, f"expected exactly one write, got {len(writes)}"
        sql, params = writes[0]
        assert params["tier"] == "human_authored"
        assert params["reference_answer"] == "Ships in 3 days."
        assert params["scenario_id"] == str(scenario_id)

        # The question's ORIGIN is not touched. `source` says where the question
        # came from and stays true after someone else writes the answer; a mined
        # failure the owner answers is `source='mined'` AND
        # `label_trust_tier='human_authored'` at the same time, and fusing the
        # two is the defect D6 P1 exists to prevent.
        set_clause = re.search(
            r"\bSET\b(.*?)\bWHERE\b", " ".join(sql.split()), re.IGNORECASE
        )
        assert set_clause, f"the write has no SET clause: {sql!r}"
        assigned = {
            name.lower() for name in re.findall(r"(\w+)\s*=", set_clause.group(1))
        }
        assert assigned == {
            "reference_answer",
            "label_trust_tier",
            "labelled_by",
            "labelled_at",
        }, f"the label write assigns {sorted(assigned)}"
        assert "source" not in assigned, (
            "the label write changes the row's source — the question's origin "
            "and the answer's provenance have been fused into one column"
        )

    async def test_the_counts_are_recomputed_after_the_write(self):
        """The labelled -> eligible transition is observable in the same
        response that caused it, rather than requiring a second round trip the
        console might not make."""
        conn = _RecordingConn(counts_row=(10, 3, 7, 1))
        status, body = await _post_label(conn)

        assert status == 200
        assert body["counts"]["total"] == 10
        assert body["counts"]["eligible"] == body["counts"]["labelled"] == 7

    async def test_the_transaction_is_committed_and_the_connection_closed(self):
        """`record_human_label` neither commits nor closes — the caller owns the
        transaction. This route is that caller, and an uncommitted label is a
        label that never happened."""
        conn = _RecordingConn()
        status, _ = await _post_label(conn)

        assert status == 200
        assert conn.commits >= 1, "the label was never committed"
        assert conn.closes >= 1, "the tenant connection was leaked"

    async def test_a_scenario_that_is_not_in_this_database_is_a_404(self):
        """Zero rows matched. This is also the cross-tenant outcome: a scenario
        id belonging to another tenant is simply not a row here, and it must be
        indistinguishable from an id that never existed."""
        conn = _RecordingConn(write_rowcount=0)
        status, body = await _post_label(conn)

        assert status == 404
        assert body["detail"] == "Scenario not found"

    async def test_the_body_may_not_name_the_author(self):
        """P1's settled decision, enforced structurally rather than by comment.

        A caller able to name the human is a caller able to name any human —
        the same argument that removed the tier parameter from the writer. With
        `extra` at its default this body would succeed with the field silently
        dropped, and the caller would have every reason to think it had been
        honoured.
        """
        conn = _RecordingConn()
        status, _ = await _post_label(
            conn,
            body={"reference_answer": "Ships in 3 days.", "labelled_by": "someone@else"},
        )

        assert status == 422, (
            "a request body naming the label's author was accepted"
        )
        assert conn.executed == [], "a rejected body still reached the database"

    @pytest.mark.parametrize(
        "field", ["label_trust_tier", "labelled_at", "tier", "source"]
    )
    async def test_no_other_provenance_field_may_be_submitted_either(self, field):
        conn = _RecordingConn()
        status, _ = await _post_label(
            conn, body={"reference_answer": "An answer.", field: "human_authored"}
        )
        assert status == 422
        assert conn.executed == []

    async def test_the_author_is_derived_from_the_authenticated_principal(self):
        """Not read from the request, and not invented: it is the account the
        credential resolved to."""
        tenant = _make_fake_tenant()
        agent = _make_ready_agent(tenant)
        conn = _RecordingConn()

        status, body = await _post_label(conn, tenant=tenant, agent=agent)

        assert status == 200
        assert body["labelled_by"] == f"tenant:{tenant.id}"
        writes = [p for s, p in conn.executed if "COUNT(*)" not in s and "LIMIT" not in s]
        assert writes[0]["labelled_by"] == f"tenant:{tenant.id}"

    def test_the_principal_names_an_account_and_says_so(self):
        """It is NOT a person. `get_current_tenant` has two credential paths and
        does not report which was used, so `tenant.clerk_user_id` would
        attribute an API-key write to a Clerk user who may not have made it.
        The prefix stops a bare UUID from reading as a user id beside a human
        trust tier."""
        tenant = _make_fake_tenant()
        principal = evals_module._label_principal(tenant)

        assert principal == f"tenant:{tenant.id}"
        assert principal.startswith("tenant:"), (
            "the label's author is stored as a bare identifier, which reads as a "
            "person beside label_trust_tier = 'human_authored'"
        )

    @pytest.mark.parametrize("answer", ["", "   ", "\n\t "])
    async def test_an_empty_answer_is_rejected_without_touching_the_database(
        self, answer
    ):
        """An empty label is the state the row is already in, and a human tier
        over an empty answer claims a person authored nothing on a row the
        selector would then start scoring."""
        conn = _RecordingConn()
        status, _ = await _post_label(conn, body={"reference_answer": answer})

        assert status == 422
        writes = [s for s, _ in conn.executed if "COUNT(*)" not in s and "LIMIT" not in s]
        assert writes == [], "an empty label reached the database"

    async def test_a_tenant_database_without_0016_says_which_migration_is_missing(self):
        """0016 has never been applied to any database. Until it is, this is the
        response every label attempt gets, and it must name the migration rather
        than surfacing a psycopg2 traceback as a 500."""
        conn = _RecordingConn(pre_0016=True)
        status, body = await _post_label(conn)

        assert status == 503
        assert "0016" in body["detail"]
        assert conn.commits == 0, "a failed write was committed"
        assert conn.rollbacks >= 1, "a failed write was not rolled back"

    async def test_a_model_driven_context_is_refused_before_a_connection_opens(self):
        """R4's early-out at the route layer.

        `record_human_label` re-asserts this itself, but by then
        `_record_label_sync` has already opened a tenant connection — so the
        route runs the check first to keep P1's property (a refused context
        never reaches the database) true across the thread hop.
        """
        tenant = _make_fake_tenant()
        agent = _make_ready_agent(tenant)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_async_db] = lambda: _make_mock_db_returning_agent(
            agent
        )
        connect = MagicMock()
        decrypt = MagicMock(return_value="postgresql://fake/tenantdb")
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", new=decrypt),
                patch("app.api.v1.evals.psycopg2.connect", new=connect),
                patch(
                    "app.api.v1.evals.assert_human_context",
                    side_effect=evals_module.HumanLabelRefused("a task is driving this"),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{agent.id}/eval-scenarios/{uuid4()}/label",
                        json={"reference_answer": "An answer."},
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 500
        connect.assert_not_called()
        decrypt.assert_not_called()

    async def test_a_REAL_agent_context_refuses_the_label_and_opens_nothing(self):
        """The same refusal, driven by real ContextVar state instead of a patch.

        The test above patches `assert_human_context` and therefore proves only
        that the route handles the exception. This one sets `_agent_id_var` the
        way `agent_tools.build_tool_server()` does and lets the genuine guard
        decide — which is the arm that actually stands between a model-driven
        call and a `human_authored` row.

        It is also the behaviour the autouse fixture at the top of this module
        suppresses, pinned here so that suppressing it for the other tests does
        not leave it untested anywhere.
        """
        from app.services.agent_tools import _agent_id_var

        tenant = _make_fake_tenant()
        agent = _make_ready_agent(tenant)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_async_db] = lambda: _make_mock_db_returning_agent(
            agent
        )
        connect = MagicMock()
        decrypt = MagicMock(return_value="postgresql://fake/tenantdb")
        token = _agent_id_var.set("agent-driving-this-call")
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", new=decrypt),
                patch("app.api.v1.evals.psycopg2.connect", new=connect),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{agent.id}/eval-scenarios/{uuid4()}/label",
                        json={"reference_answer": "Model-written prose."},
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            _agent_id_var.reset(token)
            app.dependency_overrides.clear()

        assert response.status_code == 500, (
            "an agent tool context was allowed to stamp a human trust tier"
        )
        connect.assert_not_called()
        decrypt.assert_not_called()

    async def test_the_refusal_message_does_not_leak_the_internal_reason(self):
        """The refusal names a context a caller cannot influence; echoing the
        internal detail back tells an attacker about the process's state."""
        tenant = _make_fake_tenant()
        agent = _make_ready_agent(tenant)
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_async_db] = lambda: _make_mock_db_returning_agent(
            agent
        )
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://x"),
                patch(
                    "app.api.v1.evals.assert_human_context",
                    side_effect=evals_module.HumanLabelRefused("task=run_eval_suite"),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{agent.id}/eval-scenarios/{uuid4()}/label",
                        json={"reference_answer": "An answer."},
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 500
        assert "run_eval_suite" not in response.json()["detail"]


# ---------------------------------------------------------------------------
# Tenant isolation — the critical one
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    async def _drive_cross_tenant(self, method: str):
        """Authenticate as one tenant, address an agent owned by another."""
        caller = _make_fake_tenant()
        owner = _make_fake_tenant()
        foreign_agent = _make_ready_agent(owner)

        app.dependency_overrides[get_current_tenant] = lambda: caller
        app.dependency_overrides[get_async_db] = lambda: _make_mock_db_returning_agent(
            foreign_agent
        )
        connect = MagicMock()
        decrypt = MagicMock(return_value="postgresql://fake/tenantdb")
        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", new=decrypt),
                patch("app.api.v1.evals.psycopg2.connect", new=connect),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    if method == "GET":
                        response = await client.get(
                            f"/api/v1/agents/{foreign_agent.id}/eval-scenarios/unlabelled",
                            headers={"X-API-Key": "vrd_live_test"},
                        )
                    else:
                        response = await client.post(
                            f"/api/v1/agents/{foreign_agent.id}/eval-scenarios/"
                            f"{uuid4()}/label",
                            json={"reference_answer": "An answer."},
                            headers={"X-API-Key": "vrd_live_test"},
                        )
        finally:
            app.dependency_overrides.clear()
        return response, connect, decrypt

    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_a_cross_tenant_request_is_404_and_opens_no_database(self, method):
        """A labelling route that crosses tenants writes one business's answer
        into another business's eval set. 404 rather than 403 so the response
        cannot be used to enumerate agents, and — the part a status code alone
        would not prove — nothing is decrypted and no connection is opened."""
        response, connect, decrypt = await self._drive_cross_tenant(method)

        assert response.status_code == 404
        assert response.json()["detail"] == "Agent not found"
        decrypt.assert_not_called()
        connect.assert_not_called()

    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_an_unknown_agent_is_404(self, method):
        tenant = _make_fake_tenant()
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_async_db] = _make_mock_db_returning_none
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                if method == "GET":
                    response = await client.get(
                        f"/api/v1/agents/{uuid4()}/eval-scenarios/unlabelled",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
                else:
                    response = await client.post(
                        f"/api/v1/agents/{uuid4()}/eval-scenarios/{uuid4()}/label",
                        json={"reference_answer": "An answer."},
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_an_unprovisioned_agent_database_is_404(self, method):
        tenant = _make_fake_tenant()
        agent = _make_ready_agent(tenant)
        agent.neon_connection_string = None
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_async_db] = lambda: _make_mock_db_returning_agent(
            agent
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                if method == "GET":
                    response = await client.get(
                        f"/api/v1/agents/{agent.id}/eval-scenarios/unlabelled",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
                else:
                    response = await client.post(
                        f"/api/v1/agents/{agent.id}/eval-scenarios/{uuid4()}/label",
                        json={"reference_answer": "An answer."},
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404
        assert response.json()["detail"] == "Agent database not provisioned"

    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_both_routes_require_a_credential(self, method):
        """No dependency overrides — the real auth dependency runs."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            if method == "GET":
                response = await client.get(
                    f"/api/v1/agents/{uuid4()}/eval-scenarios/unlabelled"
                )
            else:
                response = await client.post(
                    f"/api/v1/agents/{uuid4()}/eval-scenarios/{uuid4()}/label",
                    json={"reference_answer": "An answer."},
                )

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Absence pins on the route's shape
# ---------------------------------------------------------------------------


class TestTheRouteShape:
    def test_the_request_model_forbids_extra_fields(self):
        """The declaration behind `test_the_body_may_not_name_the_author`,
        pinned separately so that removing it fails as a statement about the
        model and not only as one behavioural symptom."""
        assert (
            evals_module.ScenarioLabelRequest.model_config.get("extra") == "forbid"
        )

    def test_the_request_model_carries_exactly_one_field(self):
        fields = set(evals_module.ScenarioLabelRequest.model_fields)
        assert fields == {"reference_answer"}, (
            f"the labelling request accepts {sorted(fields)} — every field "
            "beyond the answer itself is something the caller gets to assert "
            "about provenance"
        )

    def test_no_route_in_this_module_accepts_a_connection_string(self):
        """A tenant connection string is fetched and decrypted at runtime and is
        never a parameter of anything a caller can reach (CTL-08)."""
        tree = ast.parse(open(EVALS_ROUTE_PATH, encoding="utf-8").read())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = " ".join(
                ast.unparse(d) for d in node.decorator_list
            )
            if "router." not in decorators:
                continue
            args = node.args
            names = [a.arg for a in args.args + args.kwonlyargs]
            offenders.extend(
                f"{node.name}({name})"
                for name in names
                if "conn" in name.lower() or "dsn" in name.lower()
            )
        assert offenders == [], (
            f"a route accepts something connection-string shaped: {offenders}"
        )

    def test_the_two_queue_routes_use_the_one_ownership_check(self):
        """One check, not two that can drift. Both handlers call
        `_resolve_agent_tenant_db` and neither re-implements the comparison."""
        for handler in (
            evals_module.list_unlabelled_scenarios,
            evals_module.label_eval_scenario,
        ):
            source = inspect.getsource(handler)
            assert "_resolve_agent_tenant_db" in source, (
                f"{handler.__name__} does not use the shared ownership check"
            )
            assert "tenant_id !=" not in source, (
                f"{handler.__name__} re-implements the ownership comparison"
            )

    def test_the_ownership_check_still_compares_the_tenant(self):
        """The companion to the test above: having the two handlers delegate is
        worth nothing if the thing they delegate to stopped checking."""
        source = inspect.getsource(evals_module._resolve_agent_tenant_db)
        assert "agent.tenant_id != tenant.id" in source
        assert "status_code=404" in source
