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

A tenant DB carries EVERY agent of that tenant, not one (CLAUDE.md rule 9 is
per-tenant Neon projects). The connection therefore scopes to the tenant and
not to the agent, so every findings read here scopes on the run's
`kind = 'm7:{agent_id}'`, which is what run_red_team writes. #162 is what that
cost: the open-finding counts were read across the whole table and one agent's
report carried another's findings.

`red_team_strategies` and `red_team_probes` carry no run and no agent at all, so
`strategies`, `probes` and `probes_tested` below are still tenant-wide. Scoping
them needs a column the schema does not have; the ASR they feed is the ratio of
a scoped numerator to an unscoped denominator, which reads LOW rather than high.

open_findings adds the agent's currently-open findings, each with its real
red_team_findings primary key, so the console has an identifier to call the
contain route with — ordered by an explicit severity rank, most severe
first, never the lexical order of the severity text. Each finding's
description is recovered per request by correlating against its own run's
findings JSONB snapshot in Python, not stored as a column on
red_team_findings — there has never been a description column on that
table — and a correlation miss simply leaves description null rather than
dropping the finding.

latest_run reads the agent's newest run, whatever its status. A complete run
carries its three report counters, each summed over vectors from its coverage
JSON: sequences the attacker closed with nothing landed, findings built on the
attacker's word, and reports the rules dropped. A failed or running run carries
its status and null counters: it never reaches back to an older complete run,
whose counters would then pose as the current reading (eval_service's
_LATEST_RUN_SQL states the same rule). latest_run is null when there is no run,
or when the newest run is complete and its coverage carries none of the three
keys, which is every run written before the counters existed.
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
# findings observed for that strategy across all of THIS AGENT'S runs (#162).
# The findings join reaches red_team_runs for the agent; the probes join cannot,
# because red_team_probes carries neither a run nor an agent.
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
        AND f.run_id IN (SELECT id FROM red_team_runs WHERE kind = %s)
    GROUP BY s.id, s.attack_vector
    ORDER BY s.attack_vector
"""

# Open findings: the agent's currently-open red_team_findings rows, each with
# its real primary key (the identifier the contain route needs), what it stood
# on and the claim kinds that stood (0033), and its own run's findings JSONB
# snapshot (r.findings), joined so the Python-side description correlation
# below is scoped to the finding's OWN run rather than the latest one. The join
# is what carries the agent too (#162): it was a LEFT JOIN with no predicate on
# it, so this listed every agent's open findings and the console offered a
# contain button for each of them. Severity orders by an explicit rank, critical
# then high then medium then low, never a plain descending sort on the severity
# column, which is TEXT and would sort lexically (medium, low, high, critical),
# burying the one severity that shuts the deploy gate at the end of the list.
#
# _OPEN_FINDING_COLUMNS builds the SELECT list and names the fields
# _open_finding reads, so the query and the reader cannot disagree on order.
_OPEN_FINDING_COLUMNS: tuple[str, ...] = (
    "f.id",
    "f.run_id",
    "f.strategy_id",
    "f.severity",
    "f.attack_vector",
    "f.probe_message",
    "f.agent_response",
    "f.turn_count",
    "f.created_at",
    "f.evidence",
    "f.claims",
    "f.retest",
    "r.findings",
)

_OPEN_FINDINGS_SQL = f"""
    SELECT
        {", ".join(_OPEN_FINDING_COLUMNS)}
    FROM red_team_findings f
    JOIN red_team_runs r ON r.id = f.run_id
    WHERE f.status = 'open' AND r.kind = %s
    ORDER BY
        CASE f.severity
            WHEN 'critical' THEN 0
            WHEN 'high' THEN 1
            WHEN 'medium' THEN 2
            WHEN 'low' THEN 3
            ELSE 4
        END,
        f.created_at DESC
"""

# The agent's newest run for latest_run, whatever its status. No status filter:
# filtering to complete runs reaches back past a failed latest run and reports an
# older run's counters as current. id breaks a tie on started_at, so two runs
# started in the same instant always resolve to the same one.
_LATEST_RUN_SQL = """
    SELECT id, finished_at, status, coverage
    FROM red_team_runs
    WHERE kind = %s
    ORDER BY started_at DESC, id DESC
    LIMIT 1
"""

#: The per-vector report counters run_coverage writes into
#: red_team_runs.coverage, each a {vector: count} map with zero counts left out.
_REPORT_COUNTERS: tuple[str, ...] = (
    "reports_no_attack",
    "reports_dropped",
    "reports_on_attackers_word",
)


def _summed(coverage: dict, key: str) -> int | None:
    """One counter's integer counts added up over vectors, or None when unreadable.

    None when the key is missing, when its value is not a {vector: count} map, or
    when any count is not an int (a string, a float and a bool all read None).
    A reading with one bad count is not a reading, and a zero there would claim
    one. An empty map is a clean run's zero: run_coverage leaves zero counts out.
    """
    per_vector = coverage.get(key)
    if not isinstance(per_vector, dict):
        return None
    counts = list(per_vector.values())
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in counts):
        return None
    return sum(counts)


def _latest_run(row: tuple | None) -> dict | None:
    """The newest run's id, finish time, status and summed report counters, or None.

    A run that is not complete returns its status with the three counters None:
    it counted nothing, and an older run's counters are not this run's. None when
    there is no run, or when the newest run is complete and its coverage carries
    none of the three counter keys: a run from before the counters has no reading
    to show, and zeros there would claim a reading it never took.
    """
    if row is None:
        return None
    run_id, finished_at, status, coverage = row
    head = {
        "run_id": str(run_id),
        "finished_at": finished_at.isoformat() if finished_at else None,
        "status": status,
    }
    if status != "complete":
        return {**head, **dict.fromkeys(_REPORT_COUNTERS)}
    if not isinstance(coverage, dict) or not any(key in coverage for key in _REPORT_COUNTERS):
        return None
    return {**head, **{key: _summed(coverage, key) for key in _REPORT_COUNTERS}}


def _correlate_entry(
    run_findings: object,
    attack_vector: str | None,
    probe_message: str | None,
    turn_count: int | None,
) -> dict | None:
    """The finding's own entry in its run's findings JSONB snapshot, or None.

    red_team_findings has no description column (0012_red_team_programme.py),
    so the per-run JSONB snapshot on red_team_runs.findings, written once when
    the run completed, is the only place it exists. Matching is scoped to the
    finding's OWN run (via the SQL join in _OPEN_FINDINGS_SQL, never the latest
    run) on the (attack_vector, probe_message, turn_count) triple, the only
    fields both sides carry. The first matching entry wins.

    Never raises. A malformed or absent snapshot (wrong type, missing keys,
    non-dict entries) degrades to "no match" rather than taking the whole
    programme read down with it: the coverage table is served from the same read
    and must not fail because one finding's snapshot is odd.
    """
    try:
        if not isinstance(run_findings, list):
            return None
        for entry in run_findings:
            if not isinstance(entry, dict):
                continue
            if (
                entry.get("attack_vector") == attack_vector
                and entry.get("probe_message") == probe_message
                and entry.get("turn_count") == turn_count
            ):
                return entry
        return None
    except Exception:
        # Defensive: correlation must never sink the programme read. Only
        # attack_vector is logged; probe_message, agent_response and conn_str
        # never belong in a log line (T-23-GB-01).
        log.warning("redteam_programme.correlation_failed", attack_vector=attack_vector)
        return None


def _correlated_text(entry: dict | None, key: str) -> str | None:
    """`entry[key]` when it is a non-empty string, else None."""
    value = entry.get(key) if entry is not None else None
    return value if isinstance(value, str) and value else None


def _open_finding(row: tuple) -> dict:
    """One open red_team_findings row, with the description its run's snapshot adds.

    The row is read by name: each value pairs with the column name
    _OPEN_FINDING_COLUMNS gives it, the alias prefix dropped.
    """
    names = [column.split(".", 1)[1] for column in _OPEN_FINDING_COLUMNS]
    f = dict(zip(names, row, strict=True))
    entry = _correlate_entry(f["findings"], f["attack_vector"], f["probe_message"], f["turn_count"])
    return {
        "id": str(f["id"]),
        "run_id": str(f["run_id"]) if f["run_id"] else None,
        "strategy_id": str(f["strategy_id"]) if f["strategy_id"] else None,
        "severity": f["severity"],
        "attack_vector": f["attack_vector"],
        "probe_message": f["probe_message"],
        "agent_response": f["agent_response"],
        "turn_count": f["turn_count"],
        "created_at": f["created_at"].isoformat() if f["created_at"] else None,
        "description": _correlated_text(entry, "description"),
        # What the finding stood on and the claim kinds that stood, from the row
        # (0033). A row from before 0033 reads attacker_report and [].
        "evidence": f["evidence"],
        "claims": list(f["claims"]),
        # What the latest owner re-test did (0034): its status, outcome and, when it
        # still lands, the reply it stood on. None until a re-test is queued.
        "retest": f["retest"],
    }


def _coverage_cell(row: tuple) -> dict:
    """One strategy's coverage cell; ASR is 0.0 when no probes were tested, never a divide-by-zero."""
    strategy_id, attack_vector, probes_tested, findings_count, high_severity_count = row
    probes_tested = probes_tested or 0
    findings_count = findings_count or 0
    attack_success_rate = (findings_count / probes_tested) if probes_tested > 0 else 0.0
    return {
        "strategy_id": str(strategy_id),
        "attack_vector": attack_vector,
        "probes_tested": probes_tested,
        "findings_count": findings_count,
        "high_severity_count": high_severity_count or 0,
        "attack_success_rate": round(attack_success_rate, 4),
    }


def read_programme(conn_str: str, agent_id: str) -> dict:
    """Return {strategies, probes, coverage, open_findings, latest_run} for the agent's tenant DB.

    coverage is the harm-category x attack-strategy rollup: one cell per strategy
    with probes_tested and attack_success_rate (findings_count / probes_tested,
    or 0.0 when no probes have been tested yet, an honest empty rather than a
    divide-by-zero). open_findings is the agent's currently-open findings
    (contained/closed never appear), ordered by real severity rank, each carrying
    its real primary key and a description recovered from its own run's findings
    snapshot (null on a miss). "The agent's" is true of both since #162.
    latest_run is the newest run's status and report counters, or null (_latest_run).
    """
    kind = (f"m7:{agent_id}",)  # the agent, as red_team_runs spells it (#162)
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(_LIST_STRATEGIES_SQL)
            strategy_rows = cur.fetchall()

            cur.execute(_LIST_PROBES_SQL)
            probe_rows = cur.fetchall()

            cur.execute(_COVERAGE_ROLLUP_SQL, kind)
            coverage_rows = cur.fetchall()

            cur.execute(_OPEN_FINDINGS_SQL, kind)
            open_finding_rows = cur.fetchall()

            cur.execute(_LATEST_RUN_SQL, kind)
            latest_run_row = cur.fetchone()
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

    coverage = [_coverage_cell(row) for row in coverage_rows]

    open_findings = [_open_finding(row) for row in open_finding_rows]
    latest_run = _latest_run(latest_run_row)

    log.info(
        "redteam_programme.read",
        agent_id=agent_id,
        strategy_count=len(strategies),
        probe_count=len(probes),
        coverage_cells=len(coverage),
        open_finding_count=len(open_findings),
        latest_run_counted=latest_run is not None,
    )

    return {
        "strategies": strategies,
        "probes": probes,
        "coverage": coverage,
        "open_findings": open_findings,
        "latest_run": latest_run,
    }
