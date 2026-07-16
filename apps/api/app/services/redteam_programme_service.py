"""
OPS-13: red-team programme read service — coverage rollup (harm-category x attack-strategy).

Reads red_team_strategies + red_team_probes (first-class rows written by
run_red_team, see app/worker/tasks/runtime/red_team.py Step 7b) and derives
an ASR-per-cell (attack-success-rate) coverage rollup joined against
red_team_findings.

red_team_findings is created by tenant migration 0012 but not yet populated
— that lands in 21-08, which also rewires the deploy gate to read from it.
Until then, the coverage rollup here is an honest empty (0 findings, ASR
0.0) for any strategy with findings_count == 0; the query already supports
non-zero ASR once 21-08 starts writing findings rows.

Each agent has its own dedicated Neon DB (agent.neon_connection_string), so
no agent_id filtering is needed inside these queries — the connection is
already scoped to the correct agent (mirrors red_team_runs / red_team.py's
existing IDOR + conn_str resolution and deployment_service.py's
_fetch_red_team_summary_sync read idiom).
"""

from __future__ import annotations

import psycopg2
import structlog

log = structlog.get_logger(__name__)

_LIST_STRATEGIES_SQL = """
    SELECT id, attack_vector, description, created_at
    FROM red_team_strategies
    ORDER BY attack_vector
"""

_LIST_PROBES_SQL = """
    SELECT id, strategy_id, harm_category, probe_message, created_at
    FROM red_team_probes
    ORDER BY created_at DESC
"""

# Coverage rollup: one row per strategy (attack-strategy axis of the
# harm-category x attack-strategy matrix), aggregating probes tested and
# findings observed for that strategy across all runs.
_COVERAGE_ROLLUP_SQL = """
    SELECT
        s.id AS strategy_id,
        s.attack_vector,
        COUNT(DISTINCT p.id) AS probes_tested,
        COUNT(DISTINCT f.id) AS findings_count,
        COUNT(DISTINCT f.id) FILTER (WHERE f.severity IN ('high', 'critical')) AS high_severity_count
    FROM red_team_strategies s
    LEFT JOIN red_team_probes p ON p.strategy_id = s.id
    LEFT JOIN red_team_findings f ON f.strategy_id = s.id
    GROUP BY s.id, s.attack_vector
    ORDER BY s.attack_vector
"""


def read_programme(conn_str: str, agent_id: str) -> dict:
    """Return {strategies, probes, coverage} for the given agent's tenant DB.

    coverage is the harm-category x attack-strategy rollup: one cell per
    strategy with probes_tested and attack_success_rate (findings_count /
    probes_tested, or 0.0 when no probes have been tested yet — honest
    empty, never a divide-by-zero).
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(_LIST_STRATEGIES_SQL)
            strategy_rows = cur.fetchall()

            cur.execute(_LIST_PROBES_SQL)
            probe_rows = cur.fetchall()

            cur.execute(_COVERAGE_ROLLUP_SQL)
            coverage_rows = cur.fetchall()
    finally:
        conn.close()

    strategies = [
        {
            "id": str(row[0]),
            "attack_vector": row[1],
            "description": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
        }
        for row in strategy_rows
    ]

    probes = [
        {
            "id": str(row[0]),
            "strategy_id": str(row[1]) if row[1] else None,
            "harm_category": row[2],
            "probe_message": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
        }
        for row in probe_rows
    ]

    coverage = []
    for row in coverage_rows:
        strategy_id, attack_vector, probes_tested, findings_count, high_severity_count = row
        probes_tested = probes_tested or 0
        findings_count = findings_count or 0
        attack_success_rate = (findings_count / probes_tested) if probes_tested > 0 else 0.0
        coverage.append(
            {
                "strategy_id": str(strategy_id),
                "attack_vector": attack_vector,
                "probes_tested": probes_tested,
                "findings_count": findings_count,
                "high_severity_count": high_severity_count or 0,
                "attack_success_rate": round(attack_success_rate, 4),
            }
        )

    log.info(
        "redteam_programme.read",
        agent_id=agent_id,
        strategy_count=len(strategies),
        probe_count=len(probes),
        coverage_cells=len(coverage),
    )

    return {"strategies": strategies, "probes": probes, "coverage": coverage}
