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
     would be the defect; an ordering that admits what it is, is not. The key
     list in that payload is now PARSED OUT OF THE STATEMENT, so it can no
     longer describe an ordering the database is not performing.

  5. ONLY A HUMAN'S CREDENTIAL MAY PRODUCE A HUMAN TIER. `label_service`'s four
     restrictions are all in-process facts and none of them can see a caller in
     another process, while `get_current_tenant` accepts `X-API-Key`. The route
     therefore refuses any credential but a Clerk JWT — and refuses
     CREDENTIAL_UNKNOWN too, so "cannot tell" never resolves to "human".

  6. THE WRITE REACHES ONLY WHAT THE QUEUE OFFERED. The UPDATE is scoped by the
     negation of the selector predicate, so an already-answered scenario is a
     409 rather than a silent overwrite of a curated golden-set answer.

WHAT THE 2026-08-09 REVIEW CHANGED HERE, because several of these tests were
demonstrated to be passing inside their own blind spots: the module-write scan is
now two AST scans borrowed from test_label_provenance rather than an uppercase
substring search; the ORDER BY is compared as a parsed key list rather than by
three independent substring checks that left the priority key's DIRECTION
unpinned; the counts FILTERs are counted rather than merely present; and the
empty-answer test's name is true, because the check moved to the request model
and a whitespace body no longer opens a connection on its way to a 422.

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
from starlette.requests import Request

# conftest.py sets required env vars before any app import
from app.api import deps as deps_module
from app.api.deps import (
    CREDENTIAL_API_KEY,
    CREDENTIAL_CLERK_JWT,
    CREDENTIAL_UNKNOWN,
    get_async_db,
    get_credential_kind,
    get_current_tenant,
)
from app.api.v1 import evals as evals_module
from app.main import app
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services.eval_service import (
    SCENARIO_SOURCE_TRUST_TIER,
    scenario_trust_tier,
    trust_tier_rank,
)
# NOTE: this module does NOT import `app.services.label_service`, and must not.
# R2 in test_label_provenance pins that the writer is referenced by exactly one
# API module and by exactly one test module, and a convenience import here would
# widen that wall for the sake of reading a SQL string. Everything this file
# needs to assert about the write it asserts against the statement the RECORDING
# CURSOR captured from a real request, which is stronger evidence anyway.

# THE COMPOSED-SQL DETECTOR, BORROWED RATHER THAN REIMPLEMENTED.
# `test_this_module_issues_no_write_of_its_own_to_eval_scenarios` below used to
# be an uppercase substring scan over the raw file, and the 2026-08-09
# adversarial review showed it blind to three of four spellings of the very write
# it exists to catch (schema-qualified, quoted-identifier, and composed from
# fragments — each appended to evals.py, each leaving all 141 tests of this
# module and test_label_provenance green). That module's `_scenario_write_
# statements` already models f-strings, `+`, `%`, `.format`, `.join`, `public.`
# and quoted identifiers, and its blind spots are pinned there by eight forgery
# fixtures. Importing it means one detector with one set of known limits, rather
# than two that have to be kept in step by hand.
from tests.unit.test_label_provenance import (
    _constant_text,
    _docstring_constant_ids,
    _normalised_sql,
    _scenario_write_statements,
)

# The write verbs. `DELETE FROM` rather than a bare `DELETE` so the scan does not
# fire on the word in ordinary prose that happens to sit in a runtime string.
_SQL_WRITE_VERBS = ("UPDATE ", "INSERT INTO ", "DELETE FROM ")


def _write_verbs_in(path: str) -> list[str]:
    """Every SQL write verb *path* builds into a string, docstrings excluded.

    THE COMPANION SCAN, with the opposite blind spot to
    `_scenario_write_statements`. That one reconstructs a statement and then has
    to RECOGNISE THE TABLE, so a table name composed from fragments
    (`"eval_" + "scenarios"` bound to a name, then interpolated) reassembles as
    `UPDATE  SET ...` and escapes it. This one asks only whether a write verb is
    being built at all, which needs no model of the table name — and it can ask
    that because `app/api/v1/evals.py` is a read module: the single write it is
    responsible for is delegated to `label_service`.

    Docstrings are exempt, exactly as in `_label_column_mentions`: a bare string
    expression is bound to `__doc__` and can never reach `cur.execute`, and a
    module that must explain why it does not write has to be able to say so.
    Comments are not in the AST at all.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    docstrings = _docstring_constant_ids(tree)

    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp, ast.Call)):
            continue
        if isinstance(node, ast.Constant) and id(node) in docstrings:
            continue
        text = _normalised_sql(_constant_text(node))
        for verb in _SQL_WRITE_VERBS:
            if verb in text:
                hits.append(f"{verb.strip()} ({type(node).__name__})")
    return sorted(set(hits))

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


def _make_mock_db(resolved: Agent | None) -> AsyncMock:
    """A control-DB session whose agent SELECT resolves to *resolved*.

    `_resolve_agent_tenant_db` issues `select(Agent).where(id == ...,
    deleted_at IS NULL)` rather than `db.get(Agent, id)`, because `db.get()`
    cannot express the soft-delete filter and a soft-deleted agent was therefore
    still labellable. Every statement is kept on `session.statements` so a test
    can assert WHAT WAS ASKED FOR — against a mock that is the only way to check
    a WHERE clause, since the mock will return whatever it is told to regardless.
    """
    session = AsyncMock()
    session.statements = []

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=resolved)

    async def _execute(statement, *args, **kwargs):
        session.statements.append(statement)
        return result

    session.execute = AsyncMock(side_effect=_execute)
    # Kept so a regression to db.get() fails loudly rather than returning a
    # MagicMock that passes every attribute access.
    session.get = AsyncMock(return_value=None)
    return session


def _make_mock_db_returning_agent(agent: Agent) -> AsyncMock:
    return _make_mock_db(agent)


def _make_mock_db_returning_none() -> AsyncMock:
    return _make_mock_db(None)


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
        if "SELECT 1" in sql:
            # `label_service._SCENARIO_EXISTS_SQL`, run only when the scoped
            # UPDATE matched nothing, to tell "no such row" (404) apart from
            # "already answered" (409).
            self._rows = [(1,)] if self.owner.scenario_exists else []
            self.rowcount = len(self._rows)
        elif "COUNT(*)" in sql:
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
        scenario_exists: bool = False,
    ):
        self.counts_row = counts_row
        self.counts_row_pre_0016 = counts_row[:3]
        self.page_rows = page_rows if page_rows is not None else [_queue_row()]
        self.write_rowcount = write_rowcount
        self.pre_0016 = pre_0016
        # What the existence probe finds. Only consulted when write_rowcount is
        # 0: `scenario_exists=True` is the relabel case (the row is here, the
        # scoped UPDATE skipped it), `False` is the absent-row case.
        self.scenario_exists = scenario_exists
        self.executed: list[tuple[str, dict]] = []
        # How many times psycopg2.connect handed this connection out. THE
        # PROPERTY "a refused request never reaches the database" IS ABOUT THIS,
        # not about `executed`: `record_human_label` raises before it opens a
        # cursor, so a request that connects and is then rejected leaves
        # `executed` empty and looks identical to one that never connected.
        self.connects = 0
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


def _counting_connect(conn: _RecordingConn):
    """A psycopg2.connect stand-in that counts how often it was called.

    `return_value=conn` cannot show whether a connection was opened at all, and
    that is the property the refusal paths advertise: a request refused for its
    CONTEXT, its CREDENTIAL or its CONTENT must not reach the database. Since
    `record_human_label` raises before it opens a cursor, `conn.executed` stays
    empty either way — so counting connects is the only way to tell "never
    connected" from "connected, then rejected".
    """

    def _connect(*args, **kwargs):
        conn.connects += 1
        return conn

    return _connect


def _writes(conn: _RecordingConn) -> list[tuple[str, dict]]:
    """The statements that are neither a counts aggregate, a page, nor a probe.

    i.e. the label UPDATE and nothing else. `SELECT 1` is excluded because
    `record_human_label` runs the existence probe on the error path only, and a
    probe is not a write — a test asserting "an empty label reached the
    database" must not be satisfied by one.
    """
    return [
        (sql, params)
        for sql, params in conn.executed
        if "COUNT(*)" not in sql and "LIMIT" not in sql and "SELECT 1" not in sql
    ]


def _override_auth(tenant: Tenant, agent: Agent | None, credential: str) -> None:
    """Install the three dependency overrides both routes resolve.

    `get_credential_kind` is overridden EXPLICITLY, never left to default,
    because the real dependency reads `request.state.credential_kind` — which
    `get_current_tenant` sets and which an override of `get_current_tenant`
    therefore never sets. Its honest answer in that state is CREDENTIAL_UNKNOWN,
    and the label route fails closed on unknown, so a test that does not say
    which credential it is simulating is a test asserting against a precondition
    it never established. That is the same mistake the `_agent_id_var` fixture at
    the top of this module was added to stop making.
    """
    app.dependency_overrides[get_current_tenant] = lambda: tenant
    app.dependency_overrides[get_async_db] = lambda: _make_mock_db(agent)
    app.dependency_overrides[get_credential_kind] = lambda: credential


async def _get_queue(conn: _RecordingConn, query: str = "") -> tuple[int, dict]:
    """Drive the GET route against *conn*; return (status_code, body)."""
    tenant = _make_fake_tenant()
    agent = _make_ready_agent(tenant)
    _override_auth(tenant, agent, CREDENTIAL_CLERK_JWT)
    try:
        with (
            patch(
                "app.api.v1.evals.fernet_decrypt",
                return_value="postgresql://fake/tenantdb",
            ),
            patch("app.api.v1.evals.psycopg2.connect", new=_counting_connect(conn)),
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
    credential: str = CREDENTIAL_CLERK_JWT,
) -> tuple[int, dict]:
    """Drive the POST route against *conn*; return (status_code, body)."""
    tenant = tenant or _make_fake_tenant()
    agent = agent or _make_ready_agent(tenant)
    _override_auth(tenant, agent, credential)
    try:
        with (
            patch(
                "app.api.v1.evals.fernet_decrypt",
                return_value="postgresql://fake/tenantdb",
            ),
            patch("app.api.v1.evals.psycopg2.connect", new=_counting_connect(conn)),
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


# The ORDER BY the queue statement is supposed to have, spelled out once.
# EVERY KEY, INCLUDING ITS DIRECTION, so that a mutation of any one of them turns
# a test red. This list is the fix for the review's second finding: the priority
# key's direction was pinned by nothing at all, and reversing it to
# `DESC NULLS LAST` — which puts `generated` first and `mined` last, the exact
# opposite of what the module comment, the reference doc and QUEUE_ORDERING all
# claim — left all 54 tests passing.
EXPECTED_ORDER_BY_KEYS = [
    "array_position(%(source_priority)s::text[], source) ASC NULLS LAST",
    "created_at ASC",
    "id ASC",
]


class TestQueueOrdering:
    def test_the_ordering_is_exactly_these_keys_in_this_direction(self):
        """The whole ORDER BY, parsed once and compared as a list.

        Three separate substring checks (`created_at ASC` present, `NULLS LAST`
        present, last key is `id ASC`) between them left the FIRST key's
        direction unpinned — the key the entire ordering section is about. A
        single equality over the parsed list cannot have that gap: any key
        added, removed, reordered or reversed changes the list.
        """
        assert evals_module._order_by_keys(evals_module._UNLABELLED_QUEUE_SQL) == (
            EXPECTED_ORDER_BY_KEYS
        )

    def test_the_priority_key_sorts_the_best_origin_first_not_last(self):
        """The behavioural meaning of that first key, stated in its own right.

        `_source_priority_order()` returns the array best-first, so
        `array_position(...)` is 1 for `mined` and 4 for `generated` and the
        direction must be ASC. `DESC` inverts the owner's queue silently: the
        rows worth least are offered first and the mined customer failures sink
        to the last page.
        """
        priority_key = evals_module._order_by_keys(
            evals_module._UNLABELLED_QUEUE_SQL
        )[0]

        assert "array_position(" in priority_key
        assert priority_key.endswith("ASC NULLS LAST"), (
            f"the queue's priority key is {priority_key!r} — reversing it puts "
            "the model_generated rows at the front of the owner's queue"
        )
        assert "DESC" not in priority_key
        order = evals_module._source_priority_order()
        assert order.index("mined") < order.index("generated"), (
            "the bound priority array is not best-first, so ASC is the wrong "
            "direction for it"
        )

    def test_the_page_takes_its_limit_and_offset_in_that_order(self):
        """`LIMIT %(offset)s OFFSET %(limit)s` swaps the two bounds while the
        response goes on reporting the caller's requested ones: ?limit=5&offset=10
        would return 10 rows starting at row 5 under a page object that says
        {"limit": 5, "offset": 10}. Nothing caught it, because
        test_the_page_reports_its_own_bounds asserts the contents of the bound
        params dict and never their ROLE in the statement."""
        assert (
            "LIMIT %(limit)s OFFSET %(offset)s" in evals_module._UNLABELLED_QUEUE_SQL
        ), (
            "the queue statement does not bind limit to LIMIT and offset to "
            "OFFSET — the two can be swapped with the response still reporting "
            "the caller's requested bounds"
        )

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
        # DERIVED FROM THE STATEMENT, not hand-written beside it. The old list
        # said `origin_trust_tier DESC` — a column that is not in the schema and
        # a direction the query does not use — so the payload described a query
        # that did not exist and could drift from it freely.
        assert ordering["keys"] == EXPECTED_ORDER_BY_KEYS
        reason = ordering["reason"]
        assert "job_events" in reason and "control-DB" in reason, (
            "the reason must name the actual obstacle — job_events is a "
            "control-DB table and eval_scenarios is not — rather than being a "
            "vague apology"
        )
        assert "confidence" in reason

    async def test_the_ordering_record_cannot_be_mutated_through_the_response(self):
        """A caller mutating the returned dict must not poison the constant.

        ASSERTED BEHAVIOURALLY, and it had to be. This used to check that the
        string `dict(QUEUE_ORDERING)` appeared in the handler's source — and
        `dict()` is a SHALLOW copy while `keys` is a list, so the returned
        "copy" still shared the constant's list and appending to it changed
        QUEUE_ORDERING for every later request in the process. The docstring's
        comparison to `eval_service.VERIFIED_QA_PROMOTION_DECISION` did not hold
        either: that constant is all scalars and has no nested mutable to share.

        The handler is awaited directly rather than driven over HTTP because
        FastAPI serialises the response, which would hide exactly the aliasing
        this is about.
        """
        before = list(evals_module.QUEUE_ORDERING["keys"])
        tenant = _make_fake_tenant()
        agent = _make_ready_agent(tenant)
        with (
            patch(
                "app.api.v1.evals.fernet_decrypt",
                return_value="postgresql://fake/tenantdb",
            ),
            patch(
                "app.api.v1.evals.psycopg2.connect", return_value=_RecordingConn()
            ),
        ):
            body = await evals_module.list_unlabelled_scenarios(
                agent_id=agent.id,
                limit=20,
                offset=0,
                db=_make_mock_db(agent),
                tenant=tenant,
            )

        body["ordering"]["keys"].append("injected DESC")
        body["ordering"]["by_uncertainty"] = True

        assert evals_module.QUEUE_ORDERING["keys"] == before, (
            "the response shares QUEUE_ORDERING's nested list — one caller's "
            "mutation now describes the ordering for every later request"
        )
        assert evals_module.QUEUE_ORDERING["by_uncertainty"] is False


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

    def test_the_two_count_filters_are_the_predicate_and_its_exact_negation(self):
        """`unlabelled + labelled == total` is an identity of the SQL — PINNED.

        The claim was true by reading the source and guarded by nothing:
        replacing the `labelled` FILTER with `WHERE question != ''`, which makes
        the identity FALSE in Postgres (a labelled row with an empty question
        falls into neither bucket), left all 54 tests green. The one test that
        asserts the identity does so over `counts_row=(10, 4, 6, 2)` — numbers
        the test itself supplies — so it passes whatever the SQL says.

        Counting occurrences rather than checking presence is the point: a bare
        `in` for the un-negated form is satisfied by the negated form as a
        substring, so presence cannot distinguish the two.
        """
        predicate = evals_module.SELECTOR_ELIGIBILITY_PREDICATE
        negated = f"FILTER (WHERE NOT ({predicate}))"
        plain = f"FILTER (WHERE {predicate})"

        for name, sql in (
            ("_QUEUE_COUNTS_SQL", evals_module._QUEUE_COUNTS_SQL),
            ("_QUEUE_COUNTS_PRE_0016_SQL", evals_module._QUEUE_COUNTS_PRE_0016_SQL),
        ):
            collapsed = " ".join(sql.split())
            assert collapsed.count(negated) == 1, (
                f"{name} does not count `unlabelled` as exactly one negation of "
                f"the selector predicate: {collapsed.count(negated)} occurrences"
            )
            # The negated form contains no occurrence of the plain form (the
            # `NOT (` sits between `WHERE` and the predicate), so this count is
            # the un-negated FILTER alone.
            assert collapsed.count(plain) == 1, (
                f"{name} does not count `labelled` as exactly one un-negated "
                f"selector predicate: {collapsed.count(plain)} occurrences — "
                "the two buckets are no longer complements and "
                "unlabelled + labelled == total is no longer an identity"
            )

    def test_this_module_issues_no_write_of_its_own_to_eval_scenarios(self):
        """The route must not grow its own INSERT or UPDATE.

        R3 in test_label_provenance scans this file too, but it only fires on a
        write that also NAMES a label column — a bare
        `SET reference_answer = ...` here would pass it and would put a second
        write path beside the one the four restrictions were built around.

        THE DETECTOR IS `test_label_provenance._scenario_write_statements`, NOT A
        TEXT SCAN. This test used to read the file, collapse whitespace, upper-case
        it and look for two literal markers. The 2026-08-09 adversarial review
        appended a bare `eval_scenarios` write to this very module in four
        spellings: the plain one went red, and `UPDATE public.eval_scenarios`,
        `UPDATE "eval_scenarios"` and `"eval_" + "scenarios"` all passed 141
        tests. The scan was demonstrating itself inside the complement of its own
        blind spot. The AST reconstruction reassembles a statement from its
        literal parts however it was composed and normalises `public.` and quoted
        identifiers away, and its own limits are pinned by the eight forgery
        fixtures in test_label_provenance.
        """
        statements = _scenario_write_statements(EVALS_ROUTE_PATH)

        assert statements == [], (
            f"{EVALS_ROUTE_PATH} issues an eval_scenarios write of its own — the "
            "human label has a second write path that bypasses the service "
            f"layer: {statements}"
        )

    def test_this_module_issues_no_write_statement_of_any_kind(self):
        """The second scan, with a different blind spot — and it is needed.

        `_scenario_write_statements` still has to RECOGNISE THE TABLE NAME, so
        `_TBL = "eval_" + "scenarios"` followed by `f"UPDATE {_TBL} SET ..."`
        reconstructs as `UPDATE  SET ...` and slips past it. Verified, not
        assumed — `test_the_table_aware_scan_has_this_exact_blind_spot` below
        runs the borrowed detector against that spelling and asserts it finds
        nothing, so this pairing is documented by a test rather than by a hope.

        This scan needs no model of how the table name was assembled, because it
        does not look for one: `app/api/v1/evals.py` is a READ module plus one
        write DELEGATED to `label_service`, so it should contain no write verb in
        any SQL string at all, against any table. That is a stronger claim than
        the first scan makes and a much easier one to keep true.

        Docstrings are exempt for the reason R3 exempts them: prose is not
        reachability, and a module that must explain why it does not write has to
        be able to say the words. Comments never enter the AST at all.
        """
        offenders = _write_verbs_in(EVALS_ROUTE_PATH)

        assert offenders == [], (
            f"{EVALS_ROUTE_PATH} builds a SQL write statement: {offenders}. This "
            "module reads; the one write it is responsible for is delegated to "
            "the service layer, which is the only place the four restrictions "
            "apply."
        )

    @pytest.mark.parametrize(
        "spelling",
        [
            '_ = "UPDATE public.eval_scenarios SET reference_answer = %(a)s"\n',
            "_ = 'UPDATE \"eval_scenarios\" SET reference_answer = %(a)s'\n",
            '_TBL = "eval_" + "scenarios"\n'
            '_ = f"UPDATE {_TBL} SET reference_answer = %(a)s"\n',
        ],
    )
    def test_the_two_scans_between_them_see_a_forged_write(self, tmp_path, spelling):
        """The vacuity check, and the reason there are two scans.

        Asserting that a list is empty passes just as happily when the detector
        is broken as when the module is clean. These three spellings are the
        three the ORIGINAL uppercase text scan was demonstrated blind to on
        2026-08-09 — each appended to evals.py, each leaving all 141 tests of
        this module plus test_label_provenance green.
        """
        path = tmp_path / "forged.py"
        path.write_text(spelling, encoding="utf-8")

        seen = bool(_scenario_write_statements(str(path))) or bool(
            _write_verbs_in(str(path))
        )
        assert seen, (
            f"neither scan sees {spelling!r} — a second write path can be added "
            "to the one module allowlisted to hold the human-label writer and no "
            "test goes red"
        )

    def test_the_table_aware_scan_has_this_exact_blind_spot(self, tmp_path):
        """Names the first scan's limit instead of implying it has none.

        A composed table name reconstructs to `UPDATE  SET ...`, which carries no
        table, so the statement scan cannot see it. Recorded as a test so the
        pairing above is a documented division of labour and not an accident that
        could be undone by someone deleting the second scan as redundant.
        """
        path = tmp_path / "composed.py"
        path.write_text(
            '_TBL = "eval_" + "scenarios"\n'
            '_ = f"UPDATE {_TBL} SET reference_answer = %(a)s"\n',
            encoding="utf-8",
        )
        assert _scenario_write_statements(str(path)) == []
        assert _write_verbs_in(str(path)), (
            "the verb scan does not cover the statement scan's blind spot, so "
            "the composed spelling is seen by neither"
        )

    def test_the_verb_scan_does_not_fire_on_a_read_or_on_prose(self, tmp_path):
        """The negative control. A scan that fired on `SELECT` or on a docstring
        would teach the next author to weaken it rather than obey it."""
        path = tmp_path / "reader.py"
        path.write_text(
            '"""This module never issues an UPDATE or an INSERT INTO."""\n'
            "def read(cur):\n"
            "    cur.execute('SELECT id FROM eval_scenarios ORDER BY created_at')\n",
            encoding="utf-8",
        )
        assert _write_verbs_in(str(path)) == []


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

        writes = _writes(conn)
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
        assert _writes(conn)[0][1]["labelled_by"] == f"tenant:{tenant.id}"

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

    @pytest.mark.parametrize(
        "answer",
        [
            "",
            "   ",
            "\n\t ",
            "\u00a0",  # NBSP — Zs, which str.strip() does remove
            "\u200b",  # ZERO WIDTH SPACE — Cf, which it does NOT
            "\ufeff",  # BOM / zero-width no-break space
            "\u200c",  # zero-width non-joiner
            "\u200b\u200c\ufeff",  # a run of them, as a paste would produce
        ],
    )
    async def test_an_empty_answer_is_rejected_without_touching_the_database(
        self, answer
    ):
        """An empty label is the state the row is already in, and a human tier
        over an empty answer claims a person authored nothing on a row the
        selector would then start scoring.

        THE ZERO-WIDTH CASES ARE NOT PEDANTRY. `str.strip()` removes Cc and Zs
        and leaves Cf alone, so `reference_answer="\\u200b"` used to return 200,
        bind `tier='human_authored'`, and store a value that satisfies BOTH
        `run_eval_suite`'s `WHERE reference_answer != ''` and 0016's
        `COALESCE(reference_answer,'') <> ''` CHECK — re-inerting the row while
        marking it labelled, which is precisely the state the guard exists to
        prevent. Observed through the real route on 2026-08-09 for U+200B, U+FEFF
        and U+200C. The realistic origin is a rich-text paste, not an attacker.

        AND THE NAME OF THIS TEST IS NOW TRUE. It used to be false for the
        whitespace cases: Pydantic's `min_length=1` passed `"   "`, so the
        handler decrypted a connection string and `psycopg2.connect`ed before
        `record_human_label` stripped and raised. The check is now a
        `field_validator` on the request model, so a refused CONTENT gets the
        same "never reaches the database" treatment as a refused CONTEXT.
        """
        conn = _RecordingConn()
        status, _ = await _post_label(conn, body={"reference_answer": answer})

        assert status == 422
        assert conn.connects == 0, (
            "an empty label opened a tenant connection before being refused — "
            "the emptiness check is not at the boundary, so the property this "
            "test's name claims does not hold"
        )
        assert conn.executed == []

    async def test_an_answer_that_is_merely_padded_with_zero_width_is_kept(self):
        """The negative control for the test above.

        A guard that rejected any answer CONTAINING a zero-width character would
        throw away real labels pasted out of a rich-text editor. The rule is
        "carries at least one visible character", not "is free of invisible
        ones".
        """
        conn = _RecordingConn()
        status, body = await _post_label(
            conn, body={"reference_answer": "\u200bShips in 3 days.\u200b"}
        )

        assert status == 200, body
        assert _writes(conn)[0][1]["reference_answer"] == "\u200bShips in 3 days.\u200b"

    async def test_an_oversized_answer_is_rejected_at_the_boundary(self):
        """The stored value is fed to a paid judge on every nightly run.

        `run_eval_suite` interpolates `reference_answer` into Ragas' prompts for
        as long as the row lives, so an unbounded label is a recurring cost and
        not a one-off write. The bound is generous — several pages of prose —
        and being at the boundary means the cost is refused before a connection
        is opened.
        """
        conn = _RecordingConn()
        over = "a" * (evals_module.MAX_REFERENCE_ANSWER_CHARS + 1)
        status, _ = await _post_label(conn, body={"reference_answer": over})

        assert status == 422
        assert conn.connects == 0
        assert conn.executed == []

        at_limit = "a" * evals_module.MAX_REFERENCE_ANSWER_CHARS
        conn_ok = _RecordingConn()
        status_ok, _ = await _post_label(
            conn_ok, body={"reference_answer": at_limit}
        )
        assert status_ok == 200, "the bound itself is rejected, so it is off by one"

    async def test_a_scenario_that_already_has_an_answer_is_a_409_not_an_overwrite(
        self,
    ):
        """THE WRITE'S REACH IS THE QUEUE'S OWN POPULATION, AND NOTHING WIDER.

        `_LABEL_SQL`'s WHERE used to be `id = %(scenario_id)s::uuid` alone, so one
        POST silently replaced any existing `reference_answer` in the agent's
        database and re-stamped its provenance with no record of what had been
        there. On a `dataset='golden'` row that is worse than losing an answer:
        `eval.py` runs the golden half in full every night so consecutive runs
        are a PAIRED per-item comparison, and moving one item's reference answer
        breaks the comparison while the run report has no way to say so.

        The row exists, the scoped UPDATE matches nothing, and the existence
        probe is what tells this apart from "no such row".
        """
        conn = _RecordingConn(write_rowcount=0, scenario_exists=True)
        status, body = await _post_label(conn)

        assert status == 409, body
        assert "already has a reference answer" in body["detail"]

        # Exactly one write was ATTEMPTED and it matched nothing, because the
        # statement itself excluded the row. The transaction is still committed —
        # it changed nothing, so there is nothing to roll back — and asserting
        # `commits == 0` would be asserting a bookkeeping detail rather than the
        # property, which is that the existing answer was never assigned.
        writes = _writes(conn)
        assert len(writes) == 1, f"expected one attempted write, got {len(writes)}"
        assert f"NOT ({evals_module.SELECTOR_ELIGIBILITY_PREDICATE})" in writes[0][0]
        assert any("SELECT 1" in sql for sql, _ in conn.executed), (
            "the 409 was reported without asking whether the row exists, so it "
            "cannot be distinguishing a relabel from a missing row"
        )

    async def test_the_label_write_is_scoped_to_an_unlabelled_row(self):
        """The statement-level half of the test above, so the property survives
        a change to how the route reports the outcome.

        Read off the statement the REAL writer emitted through the recording
        cursor, not by importing `_LABEL_SQL`: this module may not reference
        `label_service` (R2), and the emitted statement is better evidence than
        the constant anyway.
        """
        conn = _RecordingConn()
        status, _ = await _post_label(conn)
        assert status == 200

        collapsed = " ".join(_writes(conn)[0][0].split())
        predicate = evals_module.SELECTOR_ELIGIBILITY_PREDICATE

        assert f"NOT ({predicate})" in collapsed, (
            "the human-label UPDATE is not scoped to an unlabelled row — it can "
            f"overwrite any scenario in the agent's database: {collapsed}"
        )
        where = collapsed.split("WHERE", 1)[1]
        assert "id = %(scenario_id)s::uuid" in where
        assert where.index("id = %(scenario_id)s::uuid") < where.index("NOT (")

    async def test_a_missing_row_is_still_a_404_and_not_the_409(self):
        """The two zero-row outcomes must not be confused: a `scenario_id` from
        another tenant is not a row in this connection at all, and it must stay
        indistinguishable from an id that never existed."""
        conn = _RecordingConn(write_rowcount=0, scenario_exists=False)
        status, body = await _post_label(conn)

        assert status == 404
        assert body["detail"] == "Scenario not found"

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
        _override_auth(tenant, agent, CREDENTIAL_CLERK_JWT)
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
        _override_auth(tenant, agent, CREDENTIAL_CLERK_JWT)
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
        _override_auth(tenant, agent, CREDENTIAL_CLERK_JWT)
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

        _override_auth(caller, foreign_agent, CREDENTIAL_CLERK_JWT)
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
        _override_auth(tenant, None, CREDENTIAL_CLERK_JWT)
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
        _override_auth(tenant, agent, CREDENTIAL_CLERK_JWT)
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
    async def test_a_soft_deleted_agent_is_gone_from_both_routes(self, method):
        """`agents.py:226` states the invariant: "all read routes already filter
        on deleted_at IS NULL, so a soft-deleted agent disappears from the API
        surface". `_resolve_agent_tenant_db` used `db.get()`, which cannot
        express that filter, so DELETE /agents/{id} left the agent both listable
        HERE and — new in P2 — LABELLABLE: a `human_authored` row written into a
        deleted agent's tenant database.

        Two halves, and the second is the one that matters against a mock: the
        route 404s when the filtered SELECT resolves to nothing, AND the SELECT
        it issued really does carry the filter. A mock returns whatever it is
        told to, so without the second assertion this would pass over a query
        with no WHERE clause at all.
        """
        tenant = _make_fake_tenant()
        session = _make_mock_db(None)  # what the filtered SELECT returns
        app.dependency_overrides[get_current_tenant] = lambda: tenant
        app.dependency_overrides[get_async_db] = lambda: session
        app.dependency_overrides[get_credential_kind] = lambda: CREDENTIAL_CLERK_JWT
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
        decrypt.assert_not_called()
        connect.assert_not_called()

        assert session.statements, "no agent lookup was issued at all"
        rendered = str(session.statements[0])
        assert "deleted_at IS NULL" in rendered, (
            "the agent lookup does not filter soft-deleted agents, so a deleted "
            f"agent is still labellable: {rendered}"
        )

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
# The credential that may stamp a human tier — the out-of-process hole
# ---------------------------------------------------------------------------


class TestOnlyAHumansCredentialMayStampAHumanTier:
    """`label_service`'s R1-R4 are all IN-PROCESS facts.

    A parameter list, an import graph, Celery's thread-local task stack and an
    `agent_tools` ContextVar. None of the four can see a caller in a DIFFERENT
    process, and `get_current_tenant` accepts `X-API-Key` — a machine credential
    a script, a scheduler or a model-driven pipeline can hold. Until 2026-08-09
    any of them could POST model prose here and have it stored as
    `label_trust_tier='human_authored'`, the tier `VERIFIED_QA_MIN_TRUST_TIER` is
    defined over. The hierarchy was then worth the secrecy of an API key rather
    than any human-in-the-loop property, and the phase's central claim — "a human
    authored this" — was not enforceable anywhere.

    The credential is the only evidence about the caller that survives the
    process boundary, so this is where the check has to live.
    """

    async def test_an_api_key_may_not_record_a_human_label(self):
        conn = _RecordingConn()
        status, body = await _post_label(conn, credential=CREDENTIAL_API_KEY)

        assert status == 403, body
        assert conn.connects == 0, (
            "a machine credential opened the tenant database on the way to "
            "stamping human_authored"
        )
        assert conn.executed == []
        assert "API key" in body["detail"]

    async def test_an_unrecorded_credential_is_refused_too(self):
        """Fail CLOSED on "cannot tell".

        CREDENTIAL_UNKNOWN means no credential resolver ran — a replaced
        dependency, a future refactor, a route wired up without auth. Treating it
        as "probably a human" would make the guard removable by accident, in the
        one place where the failure is a forged authorship claim nobody notices.
        """
        conn = _RecordingConn()
        status, _ = await _post_label(conn, credential=CREDENTIAL_UNKNOWN)

        assert status == 403
        assert conn.connects == 0
        assert conn.executed == []

    async def test_a_clerk_session_may(self):
        """The negative control: the refusal above is about the credential and
        not about the route being broken."""
        conn = _RecordingConn()
        status, body = await _post_label(conn, credential=CREDENTIAL_CLERK_JWT)

        assert status == 200, body
        assert body["label_trust_tier"] == "human_authored"

    async def test_the_queue_itself_is_readable_with_an_api_key(self):
        """The gate is on the WRITE, not on the feature. Reading the queue
        asserts nothing about who is reading, so a machine credential listing
        unlabelled rows is not a provenance claim and is not refused — narrowing
        the read as well would be a change nobody asked for, and this pins that
        it was not made by accident."""
        tenant = _make_fake_tenant()
        agent = _make_ready_agent(tenant)
        _override_auth(tenant, agent, CREDENTIAL_API_KEY)
        try:
            with (
                patch(
                    "app.api.v1.evals.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.evals.psycopg2.connect", return_value=_RecordingConn()
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{agent.id}/eval-scenarios/unlabelled",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200

    def test_the_route_declares_the_credential_dependency(self):
        """The gate is a dependency of the handler, not a check somebody can
        drop by editing one `if`. Pinned as a statement about the signature so
        that removing the parameter fails here as well as behaviourally."""
        signature = inspect.signature(evals_module.label_eval_scenario)
        assert "credential_kind" in signature.parameters
        source = inspect.getsource(evals_module.label_eval_scenario)
        assert "CREDENTIAL_CLERK_JWT" in source

    @staticmethod
    def _bare_request() -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [],
                "query_string": b"",
                "state": {},
            }
        )

    async def test_the_dependency_reports_unknown_when_nothing_recorded_one(self):
        request = self._bare_request()
        kind = await deps_module.get_credential_kind(
            request, tenant=_make_fake_tenant()
        )
        assert kind == CREDENTIAL_UNKNOWN

    async def test_get_current_tenant_records_which_path_authenticated(self):
        """The mechanism itself, driven through the REAL dependency for both
        credential paths — otherwise `get_credential_kind` could be reading a
        value nothing ever writes, and every test above would be exercising an
        override of an override."""
        tenant = _make_fake_tenant()
        tenant.api_key_hash = "argon2-hash"
        tenant.api_key_prefix = "prefix"

        result = MagicMock()
        result.scalars.return_value.first.return_value = tenant
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)

        jwt_request = self._bare_request()
        bearer = MagicMock()
        bearer.credentials = "fake.clerk.token"
        with patch(
            "app.api.deps.verify_clerk_jwt", return_value={"sub": "user_test"}
        ):
            got = await deps_module.get_current_tenant(
                jwt_request, bearer=bearer, api_key=None, db=db
            )
        assert got is tenant
        assert jwt_request.state.credential_kind == CREDENTIAL_CLERK_JWT

        key_request = self._bare_request()
        with patch("app.api.deps.verify_api_key", return_value=True):
            got = await deps_module.get_current_tenant(
                key_request, bearer=None, api_key="vrd_live_test", db=db
            )
        assert got is tenant
        assert key_request.state.credential_kind == CREDENTIAL_API_KEY

        # And the dependency that reads it agrees with what was written.
        assert (
            await deps_module.get_credential_kind(key_request, tenant=tenant)
        ) == CREDENTIAL_API_KEY


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
