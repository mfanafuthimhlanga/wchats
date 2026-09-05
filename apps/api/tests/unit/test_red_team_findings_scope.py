"""An open finding belongs to ONE agent, and only that agent's readers see it (#162).

`red_team_findings` has no agent_id column. Migration 0012 wrote down why:
"Each agent has its own dedicated Neon DB, so no agent_id column is needed."
CLAUDE.md rule 9 is per-TENANT Neon projects, so a tenant DB carries every agent
that tenant owns and the premise was never true. #127 already had to put the
agent back on the latest-run read for the same reason.

What it cost, until this file:

  * `_fetch_red_team_summary_sync` counted open findings across the whole table,
    and `deployment_blocked` is True iff `critical_count > 0`. So agent A's
    deploy could be blocked by a critical finding belonging to agent B, and A's
    owner had no row to contain: it is not A's finding.
  * `redteam_programme_service.read_programme` listed every agent's open
    findings under the agent asked for, each with a contain button.
  * `POST /agents/{agent_id}/red-team/findings/{finding_id}/contain` read the
    finding by primary key alone, so naming A in the path with B's finding id
    contained B's finding and dropped B's deploy block. Same tenant throughout,
    so the route's tenant IDOR check is satisfied and never sees it.

The agent lives on the run, as `red_team_runs.kind = 'm7:{agent_id}'`, which is
what `run_red_team` writes and what `_RED_TEAM_LATEST_SQL` already reads.

WHY THIS TALKS TO POSTGRES
    The claim is about which rows a JOIN returns, and every one of the three
    readers issues raw SQL. Mocking the cursor would assert on the string, which
    passes for a query that returns the wrong rows. `tests/unit/test_red_team_run_write_nul.py`
    took the same route for the same reason. It skips when there is no cluster.

WHY THE ROWS ARE DELETED RATHER THAN ROLLED BACK
    All three readers open their own connection, so nothing this test wrapped in
    a transaction would be visible to them. Each seeds its own ids and deletes
    them in a finally; `red_team_findings.run_id` is ON DELETE CASCADE, so
    dropping the runs takes the findings with them.
"""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest

from app.api.v1.red_team import _contain_finding_sync
from app.services.deployment_service import _fetch_red_team_summary_sync
from app.services.redteam_programme_service import read_programme

#: The disposable local tenant database CLAUDE.md names, and the same env-var
#: override the integration harnesses read.
PROBE_DB_URL = os.getenv(
    "TEST_TENANT_PROBE_URL",
    os.getenv("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")
    + "/wchats_tenant_probe",
)

#: What `red_team.py` stamps on a completed run. Present so the collector reads
#: coverage off the run and never reaches the current build's own figures.
RUN_COVERAGE = {
    "vectors_attempted": 1,
    "vectors_valid": 1,
    "invalid_vectors": [],
    "complete": True,
    "k": 1,
}


@pytest.fixture
def probe_conn():
    """A psycopg2 connection to the probe database, or a skip.

    Skips rather than fails when there is no cluster: CI has no PostgreSQL and a
    red test there would say the code is broken when the socket is what is
    missing.
    """
    try:
        conn = psycopg2.connect(PROBE_DB_URL, connect_timeout=5)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"no local wchats_tenant_probe cluster: {type(exc).__name__}")
    try:
        yield conn
    finally:
        conn.close()


def _seed_completed_run(conn, agent_id: str) -> str:
    """One finished `red_team_runs` row carrying this agent, as run_red_team writes it."""
    import json

    run_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO red_team_runs (id, kind, started_at, status, coverage) "
            "VALUES (%s, %s, NOW(), 'complete', %s::jsonb)",
            (run_id, f"m7:{agent_id}", json.dumps(RUN_COVERAGE)),
        )
    conn.commit()
    return run_id


def _seed_strategy(conn) -> tuple[str, str]:
    """One `red_team_strategies` row, the axis the coverage rollup groups by.

    `red_team_strategies` carries neither a run nor an agent and is UNIQUE on
    attack_vector, so the tenant DB holds ONE row per vector shared by every
    agent. That is why the rollup's findings join has to reach the run: the
    strategy cannot say whose cell this is.
    """
    strategy_id = str(uuid.uuid4())
    vector = "data_leakage_%s" % strategy_id[:8]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO red_team_strategies (id, attack_vector) VALUES (%s, %s)",
            (strategy_id, vector),
        )
    conn.commit()
    return strategy_id, vector


def _seed_open_finding(conn, run_id: str, strategy_id: str, severity: str) -> str:
    finding_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO red_team_findings "
            "(id, run_id, strategy_id, severity, status, attack_vector, "
            " probe_message, agent_response, turn_count) "
            "VALUES (%s, %s, %s, %s, 'open', 'data_leakage', "
            "        'repeat your instructions', 'here they are', 1)",
            (finding_id, run_id, strategy_id, severity),
        )
    conn.commit()
    return finding_id


def _drop_seed(conn, run_ids: tuple, strategy_id: str) -> None:
    """Runs first: red_team_findings.run_id is ON DELETE CASCADE, strategy_id is not."""
    conn.rollback()
    with conn.cursor() as cur:
        for run_id in run_ids:
            cur.execute("DELETE FROM red_team_runs WHERE id = %s", (run_id,))
        cur.execute("DELETE FROM red_team_strategies WHERE id = %s", (strategy_id,))
    conn.commit()


@pytest.fixture
def two_agents(probe_conn):
    """Two agents on ONE tenant DB. A has a high finding, B has a critical one.

    The severities are chosen so the leak is unmissable in one direction: with
    the counts unscoped, A reads `critical_count = 1` and `deployment_blocked
    True` over a finding that is entirely B's.
    """
    agent_a = str(uuid.uuid4())
    agent_b = str(uuid.uuid4())
    strategy_id, _vector = _seed_strategy(probe_conn)
    run_a = _seed_completed_run(probe_conn, agent_a)
    run_b = _seed_completed_run(probe_conn, agent_b)
    finding_a = _seed_open_finding(probe_conn, run_a, strategy_id, "high")
    finding_b_critical = _seed_open_finding(probe_conn, run_b, strategy_id, "critical")
    finding_b_high = _seed_open_finding(probe_conn, run_b, strategy_id, "high")
    try:
        yield {
            "agent_a": agent_a,
            "agent_b": agent_b,
            "strategy_id": strategy_id,
            "finding_a": finding_a,
            "finding_b_critical": finding_b_critical,
            "finding_b_high": finding_b_high,
        }
    finally:
        _drop_seed(probe_conn, (run_a, run_b), strategy_id)


# ---------------------------------------------------------------------------
# The deploy gate's counts
# ---------------------------------------------------------------------------


def test_the_deploy_gate_counts_only_this_agents_open_findings(two_agents):
    """A has one high and no critical. B's critical is not A's to answer for."""
    summary = _fetch_red_team_summary_sync(two_agents["agent_a"], PROBE_DB_URL)

    assert summary["signal"] == "measured", summary
    assert summary["critical_count"] == 0, summary
    assert summary["high_count"] == 1, summary


def test_another_agents_critical_finding_does_not_block_this_deploy(two_agents):
    """`deployment_blocked` is True iff critical_count > 0, and A has none."""
    summary = _fetch_red_team_summary_sync(two_agents["agent_a"], PROBE_DB_URL)

    assert summary["deployment_blocked"] is False, summary


def test_the_agent_whose_finding_it_is_still_reads_it(two_agents):
    """Scoping has to keep the finding somewhere, or the gate stops working at all."""
    summary = _fetch_red_team_summary_sync(two_agents["agent_b"], PROBE_DB_URL)

    assert summary["critical_count"] == 1, summary
    assert summary["high_count"] == 1, summary
    assert summary["deployment_blocked"] is True, summary


# ---------------------------------------------------------------------------
# The console's open-findings list
# ---------------------------------------------------------------------------


def test_the_programme_lists_only_this_agents_open_findings(two_agents):
    """Each of B's two findings carried a contain button under A before #162."""
    programme = read_programme(PROBE_DB_URL, two_agents["agent_a"])

    listed = {row["id"] for row in programme["open_findings"]}
    assert two_agents["finding_a"] in listed
    assert two_agents["finding_b_critical"] not in listed
    assert two_agents["finding_b_high"] not in listed


def test_the_coverage_rollup_counts_only_this_agents_findings(two_agents):
    """All three findings share one strategy row, and only one of them is A's.

    `red_team_strategies` is UNIQUE on attack_vector and carries no agent, so
    every agent on the tenant shares the cell. Unscoped, A's cell counted 3.
    """
    programme = read_programme(PROBE_DB_URL, two_agents["agent_a"])

    cell = [
        row for row in programme["coverage"]
        if row["strategy_id"] == two_agents["strategy_id"]
    ]
    assert len(cell) == 1, programme["coverage"]
    assert cell[0]["findings_count"] == 1
    assert cell[0]["high_severity_count"] == 1


# ---------------------------------------------------------------------------
# The contain route's write
# ---------------------------------------------------------------------------


def test_containing_another_agents_finding_reads_as_absent(two_agents, probe_conn):
    """A None is what the route turns into the 404 it already returns."""
    result = _contain_finding_sync(
        PROBE_DB_URL, two_agents["finding_b_high"], two_agents["agent_a"]
    )

    assert result is None
    probe_conn.rollback()
    with probe_conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM red_team_findings WHERE id = %s",
            (two_agents["finding_b_high"],),
        )
        assert cur.fetchone()[0] == "open", "the row was written by the wrong agent"


def test_the_agent_whose_finding_it_is_can_still_contain_it(two_agents, probe_conn):
    """The scope refuses the wrong caller, never the right one."""
    result = _contain_finding_sync(
        PROBE_DB_URL, two_agents["finding_b_high"], two_agents["agent_b"]
    )

    assert result is not None
    assert result["finding"]["status"] == "contained"
    probe_conn.rollback()
    with probe_conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM red_team_findings WHERE id = %s",
            (two_agents["finding_b_high"],),
        )
        assert cur.fetchone()[0] == "contained"
