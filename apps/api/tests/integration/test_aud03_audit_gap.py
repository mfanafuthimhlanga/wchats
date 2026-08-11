"""
Integration harness for AUD-03 (Phase 19, plan 19-03) — the zero-audit-gap
proof across a synthetic 30-day window of mutating traffic.

Why this file is shaped the way it is:
    write_audit_row (app.services.transactional.audit) has exactly ten
    keyword-only parameters and none of them is `created_at` — the column is
    `server_default=now()` only (app.models.tool_calls_audit). No clock
    abstraction, fake-time dependency, or accelerated-clock mechanism exists
    anywhere in this codebase, and this file MUST NOT add one — that would be
    new production surface on a security-audited write path for the benefit
    of a test (19-01-PLAN.md § OD-3). The only buildable 30-day window is
    therefore seeded backdated rows: run real dispatcher invocations through
    the real enforcement stack, then rewrite `created_at` with a direct SQL
    UPDATE immediately after each batch.

    AUD-01 already proves the per-call "one invocation, one audit row"
    guarantee at unit level. This harness integrates that guarantee across
    realistic volume and a 30-day time spread — the failure class it is
    built to catch is a row silently dropped by a retry, a worker restart,
    or a Redis blip, which a single-call unit test cannot see. Per-day
    parity is asserted as `invocations attempted == audit rows`, not
    `successes == rows`, because a harness that only exercised the success
    path would report zero gaps while proving almost nothing — the success
    path is the one nobody doubts. Each synthetic day therefore drives one
    deterministic success and two deterministic rejections (a disabled
    skill's capability denial, and an over-ceiling refund's rate/constraint
    denial) so per-day parity proves AUD-01 symmetry under volume, not only
    on the happy path.

Ephemeral DB only (T-19-05): the harness creates its own uniquely-named
control DB, migrates it, and drops it with pg_terminate_backend from a
finally block — backdated rows must never be able to land in a database that
also holds real audit history, which would corrupt the audit log's own
integrity (the thing being measured). Mirrors the tenant_db_url/control_db_url
ephemeral-DB fixture pattern in tests/integration/test_red_team_rtx.py
exactly (create -> migrate -> yield -> terminate backends -> drop).

CLAUDE.md rule 9 (NO DOCKER): every fixture here uses local Postgres
(TEST_ADMIN_DB_URL / TEST_LOCAL_BASE) and local redis-server — no
docker-compose step, no container runtime is ever started.

T-19-11 guard (load-bearing): the invocation tally this harness asserts
against is the harness's OWN in-memory counter, recorded at the moment each
call is attempted. It is NEVER re-derived from a second query against
tool_calls_audit — a parity check that queried the audit table for both
sides of the comparison could never fail, which is a vacuous gate wearing
the costume of a real one.

Module-level import discipline: standard library and pytest only (no
route module, no app.main). app.services.red_team_probe symbols are
imported lazily inside the fixtures/test that need them — importing that
module executes app.core.config's module-level `Settings()` validation,
which requires environment variables (e.g. PLATFORM_CREDENTIAL_KEY) that
are only guaranteed to be set once tests/conftest.py has run its
`os.environ.setdefault(...)` calls. Keeping the module level free of that
dependency is what lets `python -c "import
tests.integration.test_aud03_audit_gap"` succeed outside pytest, and is
also why tests/integration/test_red_team_rtx.py never imports
red_team_probe at module scope either. This is also what lets the unit
companion (tests/unit/test_audit_gap_arithmetic.py) import
`compute_audit_gap` from this module cheaply and without a live DB.

Guards:
    - INTEGRATION_TESTS_ENABLED=1 gates the whole module (skip otherwise).
    - The gated test additionally requires a reachable local Redis (the
      rate layer is Redis INCR+EXPIRE) — skips with a clear reason when
      unreachable.
    - The harness's "success" batch call is deliberately configured so the
      Actor's ACT-03 skip short-circuit engages (envelope max_amount_cents
      below settings.ACTOR_SKIP_MAX_AMOUNT_CENTS, requires_confirmation
      False) — no live ANTHROPIC_API_KEY is required for this gate, matching
      the skip reason below, which names only Postgres and Redis.

      That no-model-call property covers all THREE calls per batch, and it
      only holds because each one returns before step 5:
        * place_order   — reaches step 5 but the ACT-03 skip fires
                          (max_env 499 < ACTOR_SKIP_MAX_AMOUNT_CENTS 500).
        * book_slot     — capability denial at step 2 (enabled=False).
        * issue_refund  — max_amount_cents denial at step 4 (6000 > the
                          CLEAN_TENANT_ENVELOPES ceiling of 5000). Its
                          envelope ceiling is 5000, which is NOT below the
                          ACT-03 threshold, so if step 4 ever stopped denying
                          it this call would fall through to a live Haiku
                          judge — thirty of them per run. It did exactly that
                          until 2026-08-11: the dispatcher passed raw_args (a
                          dict) to apply_rate_and_constraint_checks, which
                          reads the amount with getattr, so the ceiling never
                          fired at all. Fixed in tools.py step 4; pinned by
                          tests/unit/test_transactional_tools.py::
                          TestMaxAmountCentsIsEnforcedByTheDispatcher.

Deferred to plan 19-05 (autonomous:false): the operator's live run of this
gate, transcribed into 19-UAT.md per the verification:backstop truth this
plan's frontmatter records.
"""

from __future__ import annotations

import os
import uuid
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from unittest.mock import patch
from uuid import uuid4

import pytest

INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not INTEGRATION_TESTS,
        reason=(
            "INTEGRATION_TESTS_ENABLED=1 required for the AUD-03 30-day "
            "audit-gap gate (real local Postgres: an ephemeral control DB "
            "migrated to alembic head '0019'; real local Redis for the rate "
            "layer)"
        ),
    ),
]

_TESTS_DIR = os.path.dirname(__file__)

AUDIT_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# compute_audit_gap — pure, DB-free coverage-parity helper. Imported directly
# by tests/unit/test_audit_gap_arithmetic.py so the two files can never
# silently drift apart.
# ---------------------------------------------------------------------------


def _bucket_utc_date(row: Mapping[str, Any], field: str) -> date_cls:
    """Return `row[field]`'s UTC calendar date, or raise ValueError.

    Every timestamp is converted with `.astimezone(timezone.utc)` first and
    then reduced to `.date()` — both sides of the invocations/audit_rows
    comparison use this identical rule, so a row written at 23:59 UTC and a
    row written at 00:01 UTC fall on adjacent days and never on both.
    """
    value = row.get(field)
    if not isinstance(value, datetime):
        raise ValueError(
            f"{field!r} must be a timezone-aware datetime, got "
            f"{type(value).__name__}"
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{field!r} must be a timezone-aware datetime; got a naive "
            f"datetime ({value!r}). A naive datetime is a programming error "
            "— this function never guesses a timezone."
        )
    return value.astimezone(timezone.utc).date()


def compute_audit_gap(
    invocations: Sequence[Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
    *,
    window_start: datetime,
    window_days: int = AUDIT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Compare dispatcher-invocations-attempted to audit-rows-written, per UTC day.

    Pure function: no DB access, no I/O, no app.* dependency — unit-testable
    against hand-built fixture data (tests/unit/test_audit_gap_arithmetic.py).

    Bucketing rule (the correctness crux): every timestamp is converted with
    `.astimezone(timezone.utc)` first, then reduced to `.date()`. A
    timestamp at exactly UTC midnight belongs to the day that begins at that
    instant, and to no other day. A naive datetime is a programming error —
    ValueError is raised naming the offending field rather than guessing a
    timezone.

    Args:
        invocations: dispatcher-invocation attempts, each a mapping carrying
            a timezone-aware `created_at` datetime and a `skill` key. This
            tally MUST come from the harness's own in-memory counter
            recorded at attempt time — never from a second query against
            tool_calls_audit, or the parity check compares the table to
            itself and can never fail (T-19-11).
        audit_rows: rows read back from tool_calls_audit, each a mapping
            carrying a timezone-aware `created_at` datetime.
        window_start: timezone-aware datetime; its UTC calendar date is day
            0 of the window.
        window_days: number of calendar days in the window (default
            AUDIT_WINDOW_DAYS = 30).

    Returns:
        A mapping with:
          - "per_day": an ordered mapping keyed by datetime.date, one entry
            for every day from window_start's UTC date through
            window_days - 1 days later inclusive. Days with no traffic are
            present with zeroes — a missing key is never how a zero day is
            represented. Each value carries "invocations", "audit_rows",
            and "delta" (audit_rows - invocations).
          - "total_invocations", "total_audit_rows", "max_abs_delta",
            "days_with_traffic".
          - "out_of_window": counts of invocations/audit_rows whose
            timestamp fell outside [window_start_utc_date, +window_days).
            Reported, never dropped.
          - "vacuous": True when total_invocations == 0 — a zero-delta
            result over a zero-traffic window is a vacuous pass, not a
            clean one.

    Raises:
        ValueError: window_start or any row's `created_at` is missing, not
            a datetime, or timezone-naive.
    """
    if not isinstance(window_start, datetime):
        raise ValueError(
            "window_start must be a timezone-aware datetime, got "
            f"{type(window_start).__name__}"
        )
    if window_start.tzinfo is None or window_start.tzinfo.utcoffset(window_start) is None:
        raise ValueError(
            "window_start must be a timezone-aware datetime; got a naive "
            f"datetime ({window_start!r})."
        )

    window_start_date = window_start.astimezone(timezone.utc).date()

    per_day: "OrderedDict[date_cls, dict[str, int]]" = OrderedDict(
        (
            window_start_date + timedelta(days=offset),
            {"invocations": 0, "audit_rows": 0, "delta": 0},
        )
        for offset in range(window_days)
    )

    out_of_window = {"invocations": 0, "audit_rows": 0}

    def _tally(rows: Sequence[Mapping[str, Any]], field_name: str) -> None:
        for row in rows:
            day = _bucket_utc_date(row, "created_at")
            offset = (day - window_start_date).days
            if 0 <= offset < window_days:
                per_day[day][field_name] += 1
            else:
                out_of_window[field_name] += 1

    _tally(invocations, "invocations")
    _tally(audit_rows, "audit_rows")

    total_invocations = 0
    total_audit_rows = 0
    max_abs_delta = 0
    days_with_traffic = 0
    for counts in per_day.values():
        counts["delta"] = counts["audit_rows"] - counts["invocations"]
        total_invocations += counts["invocations"]
        total_audit_rows += counts["audit_rows"]
        max_abs_delta = max(max_abs_delta, abs(counts["delta"]))
        if counts["invocations"] or counts["audit_rows"]:
            days_with_traffic += 1

    return {
        "per_day": per_day,
        "total_invocations": total_invocations,
        "total_audit_rows": total_audit_rows,
        "max_abs_delta": max_abs_delta,
        "days_with_traffic": days_with_traffic,
        "out_of_window": out_of_window,
        "vacuous": total_invocations == 0,
    }


# ---------------------------------------------------------------------------
# Fixture: ephemeral tenant Postgres DB migrated to alembic_tenant head —
# needed only so a real customer_identities row can back a verified session
# for the issue_refund rejection branch (its envelope requires identity
# verification, matching CLEAN_TENANT_ENVELOPES).
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_db_url():
    from alembic.config import Config
    from sqlalchemy import create_engine, pool
    from sqlalchemy import text as sa_text

    from alembic import command

    admin_url = os.environ.get(
        "TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres"
    )
    local_base = os.environ.get("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")
    db_name = f"wchats_test_1903_tn_{uuid.uuid4().hex[:12]}"

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool)
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    conn_url = f"{local_base}/{db_name}"
    script_location = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic_tenant"))

    cfg = Config()
    cfg.set_main_option("script_location", script_location)
    cfg.set_main_option("sqlalchemy.url", conn_url)
    command.upgrade(cfg, "head")

    try:
        yield conn_url
    finally:
        admin_engine = create_engine(
            admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
        )
        try:
            with admin_engine.connect() as conn:
                conn.execute(
                    sa_text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :dbname AND pid <> pg_backend_pid()"
                    ),
                    {"dbname": db_name},
                )
                conn.execute(sa_text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        finally:
            admin_engine.dispose()


# ---------------------------------------------------------------------------
# Fixture: ephemeral control Postgres DB migrated to "0019" — tool_calls_audit,
# capability_envelopes, agents, and tenants all live here. This is the only
# database this harness ever backdates rows in, and it is dropped in finally
# below regardless of test outcome (T-19-05 / T-19-06).
# ---------------------------------------------------------------------------


@pytest.fixture
def control_db_url():
    from alembic.config import Config
    from sqlalchemy import create_engine, pool
    from sqlalchemy import text as sa_text

    from alembic import command

    admin_url = os.environ.get(
        "TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres"
    )
    local_base = os.environ.get("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")
    db_name = f"wchats_test_1903_ctl_{uuid.uuid4().hex[:12]}"

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool)
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    conn_url = f"{local_base}/{db_name}"
    alembic_ini = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic.ini"))
    cfg = Config(alembic_ini)
    cfg.set_main_option("sqlalchemy.url", conn_url)
    command.upgrade(cfg, "0019")

    try:
        yield conn_url
    finally:
        admin_engine = create_engine(
            admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
        )
        try:
            with admin_engine.connect() as conn:
                conn.execute(
                    sa_text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :dbname AND pid <> pg_backend_pid()"
                    ),
                    {"dbname": db_name},
                )
                conn.execute(sa_text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        finally:
            admin_engine.dispose()


# ---------------------------------------------------------------------------
# Control-DB ContextVar redirection — every transactional module that
# touches the control DB imports get_sync_db DIRECTLY (`from
# app.core.database import get_sync_db`), so patching
# app.core.database.get_sync_db alone would not reach any of them (a direct
# `from X import Y` binds Y into the importing module's own namespace at
# import time). Each module-level alias the enforcement path binds must be
# patched individually.
# ---------------------------------------------------------------------------


@contextmanager
def _control_db_redirected(control_conn_url: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(control_conn_url)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def _fake_get_sync_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    with ExitStack() as stack:
        for target in (
            "app.core.database.get_sync_db",
            "app.services.transactional.enforcement.get_sync_db",
            "app.services.transactional.audit.get_sync_db",
            "app.services.transactional.idempotency.get_sync_db",
            "app.services.transactional.tools.get_sync_db",
        ):
            stack.enter_context(patch(target, _fake_get_sync_db))
        try:
            yield
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Fixture: require_redis — the rate layer is Redis INCR+EXPIRE.
# ---------------------------------------------------------------------------


@pytest.fixture
def require_redis():
    from app.services.transactional.enforcement import _get_redis

    try:
        _get_redis().ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local redis-server required for the AUD-03 rate layer: {exc}")


# ---------------------------------------------------------------------------
# Fixture: aud03_tenant — envelopes seeded from CLEAN_TENANT_ENVELOPES
# exactly as the clean_tenant fixture in test_red_team_rtx.py does, with
# three deliberate overrides so every rejection this harness needs is
# reachable from configuration alone, never from live Actor/model behaviour.
# ---------------------------------------------------------------------------


@dataclass
class Aud03Tenant:
    agent_id: str
    tenant_id: str
    control_db_url: str
    tenant_db_url: str


def _aud03_envelope_rows(actor_skip_ceiling_cents: int) -> list[dict[str, Any]]:
    """Build this harness's envelope rows, derived from CLEAN_TENANT_ENVELOPES.

    Three deliberate overrides, all config-driven (never model-driven):
      - book_slot: enabled forced False — the capability-denial rejection
        branch each synthetic day's batch exercises.
      - place_order: max_amount_cents tightened below
        settings.ACTOR_SKIP_MAX_AMOUNT_CENTS so the Actor's ACT-03 skip
        short-circuit engages deterministically on the harness's success
        call — mirrors the VER-01 demo tenant's own R4.99 construction
        (19-02) rather than requiring a live ANTHROPIC_API_KEY here.
      - place_order / issue_refund: rate_limit raised well above what
        AUDIT_WINDOW_DAYS batches running in real wall-clock minutes would
        otherwise trip inside the same Redis rate window, so the
        max_amount_cents constraint — not an incidental rate-limit
        collision — is what denies the over-ceiling refund.
    """
    from app.services.red_team_probe import CLEAN_TENANT_ENVELOPES

    rows: list[dict[str, Any]] = []
    for source_row in CLEAN_TENANT_ENVELOPES:
        row = dict(source_row)
        row["constraints"] = dict(row["constraints"])
        if row["skill"] == "book_slot":
            row["enabled"] = False
        elif row["skill"] == "place_order":
            row["constraints"]["max_amount_cents"] = actor_skip_ceiling_cents - 1
            row["rate_limit"] = "1000/hour"
        elif row["skill"] == "issue_refund":
            row["rate_limit"] = "1000/hour"
        rows.append(row)
    return rows


@pytest.fixture
def aud03_tenant(control_db_url, tenant_db_url):
    import json as json_lib

    from sqlalchemy import create_engine
    from sqlalchemy import text as sa_text

    from app.core.config import settings
    from app.services.red_team_probe import CLEAN_TENANT_SPEC

    assert CLEAN_TENANT_SPEC["integration_credentials_rows"] == 0

    tenant_id = str(uuid4())
    agent_id = str(uuid4())

    envelope_rows = _aud03_envelope_rows(settings.ACTOR_SKIP_MAX_AMOUNT_CENTS)

    control_engine = create_engine(control_db_url)
    try:
        with control_engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO tenants (id, name, api_key_hash) "
                    "VALUES (:id, 'AUD-03 Synthetic Tenant', :hash)"
                ),
                {"id": tenant_id, "hash": f"aud03-tenant-hash-{tenant_id}"},
            )
            conn.execute(
                sa_text(
                    "INSERT INTO agents (id, tenant_id, name, soul, role) "
                    "VALUES (:id, :tenant_id, 'AUD-03 Synthetic Agent', "
                    "CAST('{}' AS JSONB), 'customer_service')"
                ),
                {"id": agent_id, "tenant_id": tenant_id},
            )
            for row in envelope_rows:
                conn.execute(
                    sa_text(
                        "INSERT INTO capability_envelopes "
                        "(agent_id, skill, enabled, rate_limit, constraints, "
                        "requires_confirmation, requires_identity_verification, actor_mode) "
                        "VALUES (:agent_id, :skill, :enabled, :rate_limit, "
                        "CAST(:constraints AS JSONB), :requires_confirmation, "
                        ":requires_identity_verification, :actor_mode)"
                    ),
                    {
                        "agent_id": agent_id,
                        "skill": row["skill"],
                        "enabled": row["enabled"],
                        "rate_limit": row["rate_limit"],
                        "constraints": json_lib.dumps(row["constraints"]),
                        "requires_confirmation": row["requires_confirmation"],
                        "requires_identity_verification": row["requires_identity_verification"],
                        "actor_mode": row["actor_mode"],
                    },
                )
    finally:
        control_engine.dispose()

    # A verified session backs issue_refund's requires_identity_verification=True
    # row so its over-ceiling calls are denied by Step 4 (max_amount_cents), not
    # blocked earlier by Step 2.5 (identity_verification.required) — matching
    # test_value_bound_evasion's own session setup in test_red_team_rtx.py.
    from app.services.identity_service import hash_session_token

    session_token = f"aud03-verified-session-{agent_id}"
    tenant_engine = create_engine(tenant_db_url)
    try:
        with tenant_engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO customer_identities "
                    "(external_id, verification_method, session_token_hash, "
                    "session_expires_at) "
                    "VALUES (:external_id, 'email', :token_hash, "
                    "NOW() + INTERVAL '1 hour')"
                ),
                {
                    "external_id": f"aud03-{uuid4()}@example.com",
                    "token_hash": hash_session_token(session_token),
                },
            )
    finally:
        tenant_engine.dispose()

    from app.models.agent import Agent

    with _control_db_redirected(control_db_url):
        # Bind get_sync_db INSIDE the patch context. `from X import Y` binds Y
        # into this frame at the moment it runs, so importing it above the
        # `with` captured the UNPATCHED function -- this fixture then seeded the
        # ephemeral control DB and read back through the real session, which the
        # integration conftest points at the shared wchats_control. `db.get`
        # returned None and `db.expunge(None)` raised UnmappedInstanceError
        # before a single batch ran. Identical defect (and fix) to
        # test_ver01_adversarial_harness.py's clean_tenant fixture.
        from app.core.database import get_sync_db  # noqa: PLC0415

        with get_sync_db() as db:
            agent = db.get(Agent, agent_id)
            assert agent is not None, (
                "aud03_tenant seeded the ephemeral control DB but read back None -- "
                "get_sync_db was not redirected, so this is reading the wrong database"
            )
            db.expunge(agent)  # detach so it stays usable after the session closes

        from app.services.agent_tools import RetrievalStrategy, build_tool_server

        build_tool_server(
            conn_str=tenant_db_url,
            agent_id=agent_id,
            agent_name=agent.name,
            strategy=RetrievalStrategy.model_validate(agent.retrieval_strategy or {}),
            conversation_id=str(uuid4()),
            notify_fn=lambda reason, context: None,  # never send a real escalation email
            tenant_id=tenant_id,
            verified_session_token=session_token,
            job_id="",
        )

        yield Aud03Tenant(
            agent_id=agent_id,
            tenant_id=tenant_id,
            control_db_url=control_db_url,
            tenant_db_url=tenant_db_url,
        )


# ---------------------------------------------------------------------------
# test_zero_audit_gaps_across_synthetic_30_day_window — the gated gate.
# ---------------------------------------------------------------------------


def test_zero_audit_gaps_across_synthetic_30_day_window(aud03_tenant, require_redis):
    """AUD-03: every dispatcher invocation attempted over a synthetic 30-day
    window produces exactly one tool_calls_audit row, including on rejection
    branches — never just on the happy path.

    Run with -s so a failure's per-day table reaches the operator's terminal
    for transcription into 19-UAT.md rather than a bare assertion error.
    """
    import asyncio

    from sqlalchemy import create_engine
    from sqlalchemy import text as sa_text

    from app.core.config import settings
    from app.services.red_team_probe import invoke_probe_tool, red_team_mode

    actor_skip_ceiling_cents = settings.ACTOR_SKIP_MAX_AMOUNT_CENTS

    async def _run_batch() -> list[dict]:
        """Run one synthetic day's worth of traffic inside a single
        red_team_mode() window: one deterministic success (place_order,
        Actor-skip engaged), one deterministic capability denial (book_slot,
        left disabled), one deterministic rate/constraint denial
        (issue_refund above its envelope's max_amount_cents ceiling).
        """
        attempts: list[dict] = []

        async def _attempt(skill: str, args: dict) -> None:
            # Recorded at attempt time, from the harness's own counter —
            # never re-derived from tool_calls_audit itself (T-19-11).
            attempts.append({"created_at": datetime.now(timezone.utc), "skill": skill})
            await invoke_probe_tool(skill, args)

        with red_team_mode():
            await _attempt(
                "place_order",
                {
                    "idempotency_key": str(uuid4()),
                    "product_id": "aud03-synthetic-product",
                    "quantity": 1,
                    "customer_email": "aud03-synthetic@example.com",
                    "shipping_address": "1 Synthetic Street, Test City",
                    "amount_cents": actor_skip_ceiling_cents - 1,
                },
            )
            await _attempt(
                "book_slot",
                {
                    "idempotency_key": str(uuid4()),
                    "service_type": "consultation",
                    "preferred_date": "2026-01-01",
                    "preferred_time": "10:00",
                    "customer_name": "AUD-03 Synthetic Customer",
                },
            )
            await _attempt(
                "issue_refund",
                {
                    "idempotency_key": str(uuid4()),
                    "order_id": "aud03-synthetic-order",
                    "refund_amount_cents": 6000,  # above the 5000-cent ceiling
                    "reason": "AUD-03 synthetic over-ceiling probe",
                },
            )
        return attempts

    control_engine = create_engine(aud03_tenant.control_db_url)
    invocation_records: list[dict] = []
    run_start = datetime.now(timezone.utc)
    window_start = run_start - timedelta(days=AUDIT_WINDOW_DAYS - 1)

    try:
        with _control_db_redirected(aud03_tenant.control_db_url):
            for batch_index in range(AUDIT_WINDOW_DAYS):
                # 19-REVIEW.md WR-04: batch_started_at is captured on THIS
                # (Python test-runner) clock, but the WHERE created_at >= :since
                # comparison below is evaluated against Postgres's own
                # server_default=now() timestamps on tool_calls_audit. This is
                # only correct if the two clocks agree to well under the
                # sub-second gap between capturing batch_started_at and the
                # batch's rows landing in the DB. CLAUDE.md rule 9 (NO DOCKER)
                # requires a local Postgres install for this suite -- same
                # machine, same clock -- which is what makes that true here.
                # If this harness is ever pointed at a non-local Postgres
                # instance, this same-clock assumption would need revisiting
                # (e.g. a negative skew tolerance on :since) before the
                # per-day backdating below can be trusted.
                #
                # Residual, accepted: every batch's UTC calendar day is read
                # off the wall clock, so a run that STARTS within its own
                # runtime (~1-2 min) of UTC midnight straddles two dates and
                # lands two batches on one day, leaving another empty --
                # days_with_traffic < 30, with max_abs_delta still 0. That is
                # a scheduling artifact, not an audit gap; re-run it.
                batch_started_at = datetime.now(timezone.utc)
                batch_attempts = asyncio.run(_run_batch())

                offset_days = AUDIT_WINDOW_DAYS - 1 - batch_index
                with control_engine.begin() as conn:
                    written_rows = conn.execute(
                        sa_text(
                            "SELECT id FROM tool_calls_audit "
                            "WHERE agent_id = :aid AND created_at >= :since"
                        ),
                        {"aid": aud03_tenant.agent_id, "since": batch_started_at},
                    ).fetchall()
                    written_ids = [row[0] for row in written_rows]
                    if written_ids and offset_days > 0:
                        # `hours`, not `days`. Postgres interval arithmetic on a
                        # timestamptz is calendar-aware for day/month/year units
                        # (it converts to the SESSION TimeZone, subtracts calendar
                        # days, converts back) and exact for hour/minute/second
                        # units. The Python side below shifts by
                        # timedelta(days=offset_days), which is always exactly
                        # 24*offset_days hours. Across a DST transition inside the
                        # 30-day window those two rules differ by an hour, and an
                        # audit row within an hour of UTC midnight would then
                        # bucket one day away from the invocation that produced it
                        # -- a fabricated +1/-1 delta pair on two days, reported as
                        # an audit gap. Africa/Johannesburg (this server's
                        # TimeZone, verified 2026-08-11) has no DST, so the two
                        # forms are equal here; expressing it in exact-duration
                        # units means the gate does not silently depend on that.
                        conn.execute(
                            sa_text(
                                "UPDATE tool_calls_audit "
                                "SET created_at = created_at - make_interval(hours => :h) "
                                "WHERE id = ANY(:ids)"
                            ),
                            {"h": offset_days * 24, "ids": written_ids},
                        )

                shift = timedelta(days=offset_days)
                for attempt in batch_attempts:
                    attempt["created_at"] = attempt["created_at"] - shift
                invocation_records.extend(batch_attempts)

            with control_engine.connect() as conn:
                audit_rows = conn.execute(
                    sa_text(
                        "SELECT created_at FROM tool_calls_audit WHERE agent_id = :aid"
                    ),
                    {"aid": aud03_tenant.agent_id},
                ).fetchall()
    finally:
        control_engine.dispose()

    audit_row_records = [{"created_at": row[0]} for row in audit_rows]

    result = compute_audit_gap(
        invocation_records, audit_row_records, window_start=window_start
    )

    # The print condition must cover EVERY assertion below, not just two of
    # them. It previously named `vacuous` and `max_abs_delta` only, so a
    # days_with_traffic or out_of_window failure -- the two most likely, since
    # both are sensitive to when the run starts relative to UTC midnight --
    # printed nothing at all and left the operator a bare assertion error on a
    # gate that runs once and is transcribed by hand.
    if (
        result["vacuous"]
        or result["days_with_traffic"] != AUDIT_WINDOW_DAYS
        or result["out_of_window"]["invocations"]
        or result["out_of_window"]["audit_rows"]
        or result["max_abs_delta"] != 0
    ):
        print("\nAUD-03 per-day coverage table (transcribe into 19-UAT.md):")
        for day, counts in result["per_day"].items():
            print(
                f"  {day} invocations={counts['invocations']} "
                f"audit_rows={counts['audit_rows']} delta={counts['delta']}"
            )
        print(f"  out_of_window={result['out_of_window']}")

    # Order matters: a zero-delta result over a zero-traffic window is a
    # vacuous pass, not a clean one — asserted first, and loudly.
    assert result["vacuous"] is False
    assert result["days_with_traffic"] == AUDIT_WINDOW_DAYS
    assert result["out_of_window"]["invocations"] == 0
    assert result["out_of_window"]["audit_rows"] == 0
    assert result["max_abs_delta"] == 0
