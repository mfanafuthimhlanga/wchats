"""Turn a run's claim reviews into benchmark truth (#290 step 3, owner decision 2026-09-17).

    cd apps/api
    CALIBRATION_TENANT_DSN=... .venv/Scripts/python.exe \
        tests/evals/calibration/benchmark/import_reviews.py --run <eval_run_id> \
        [--benchmark tests/evals/calibration/benchmark/reviewed] [--source <label>]

Writes into the REVIEWED benchmark, a second directory beside the platform set,
never into the platform set itself: the platform set's numbers are pinned per
judge identity by `tests/unit/test_claims_benchmark.py`, and a Tenant's answers
are a different population, labels about real answers rather than sentences
somebody planted. Score the reviewed set with the same scorer:

    score_claims.py --benchmark tests/evals/calibration/benchmark/reviewed \
        --claims tests/evals/calibration/benchmark/reviewed/claims_<identity>.csv

What lands, per answer the Tenant gave:
  - one `truth.csv` row: the claim as the review showed it, `supported` as the
    Tenant answered, `novel` empty (the scorer then matches it exactly),
    `source` as named, default `review`
  - one `rows.csv` row keyed `<run_id>:<scenario_id>`, the id and its source
    and nothing else: no question, no answer, no retrieved text. The decision
    covered the yes or no, and the scorer reads nothing but the id
  - the judge's claims on that scenario under `claims_<identity>.csv`, each
    statement and whether the judge found it, and NOT the judge's reason, which
    is model text over the Tenant's answer that nothing here reads

A second import of the same run replaces its rows rather than adding beside
them. Spends nothing; reads the tenant database once.
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import re
import sys

TRUTH_COLUMNS = ["scenario_id", "claim", "supported", "novel", "source", "added", "note"]
ROW_COLUMNS = ["scenario_id", "source"]
CLAIM_COLUMNS = ["scenario_id", "position", "statement", "supported"]
DEFAULT_BENCH = "tests/evals/calibration/benchmark/reviewed"

_SELECT_SQL = """
    SELECT cr.scenario_id, cr.position, cr.statement, cr.supported, cr.reviewed_at::date,
           res.claims, res.judge_identity
    FROM claim_reviews cr
    LEFT JOIN eval_results res
        ON res.eval_run_id = cr.eval_run_id AND res.scenario_id = cr.scenario_id
       AND res.metric = 'faithfulness'
    WHERE cr.eval_run_id = %(run_id)s::uuid
    ORDER BY cr.scenario_id, cr.position
"""


def _read(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path: pathlib.Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def identity_slug(identity: object) -> str | None:
    """`<model>-<effort>-<prompt_version>` as a file-name part, or None for a partial identity."""
    data = _json(identity)
    if not isinstance(data, dict):
        return None
    parts = [data.get("model"), data.get("reasoning_effort"), data.get("prompt_version")]
    if not all(isinstance(p, str) and p for p in parts):
        return None
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", "-".join(parts)).lstrip(".")
    return slug or None


def merge(bench: pathlib.Path, run_id: str, reviews: list[tuple], *, source: str) -> dict:
    """Fold one run's review rows into the reviewed benchmark's three files.

    `reviews` are the SELECT's tuples. Rows of this run already there are
    replaced. Returns the counts written.
    """
    prefix = f"{run_id}:"
    truth = [t for t in _read(bench / "truth.csv") if not t["scenario_id"].startswith(prefix)]
    rows = [r for r in _read(bench / "rows.csv") if not r["scenario_id"].startswith(prefix)]
    claims_by_identity: dict[str, list[dict]] = {}
    seen_rows: set[str] = set()
    seen_truth: set[tuple[str, int]] = set()
    seen_claims: set[tuple[str, str]] = set()
    unnamed_judge = 0

    for sid, position, statement, supported, when, claims, identity in reviews:
        bench_sid = f"{run_id}:{sid}"
        # The join has no unique key behind it, so a fanned-out review row is
        # folded here rather than written twice.
        if (bench_sid, int(position)) in seen_truth:
            continue
        seen_truth.add((bench_sid, int(position)))
        truth.append({
            "scenario_id": bench_sid,
            "claim": statement,
            "supported": "true" if supported else "false",
            "novel": "",
            "source": source,
            "added": when.isoformat() if hasattr(when, "isoformat") else str(when),
            "note": f"the Tenant's answer on claim {position} of run {run_id[:8]}",
        })
        if bench_sid not in seen_rows:
            seen_rows.add(bench_sid)
            rows.append({"scenario_id": bench_sid, "source": f"{source}:{run_id[:8]}"})
        slug = identity_slug(identity)
        if slug is None:
            unnamed_judge += 1
        elif (slug, bench_sid) not in seen_claims:
            seen_claims.add((slug, bench_sid))
            stored = _json(claims)
            claims_by_identity.setdefault(slug, []).extend(
                {"scenario_id": bench_sid, "position": i, "statement": c["statement"],
                 "supported": str(bool(c["supported"])).lower()}
                for i, c in enumerate(stored if isinstance(stored, list) else [])
                if isinstance(c, dict)
            )

    _write(bench / "truth.csv", TRUTH_COLUMNS, truth)
    _write(bench / "rows.csv", ROW_COLUMNS, rows)
    claims_added = 0
    for slug, new_claims in claims_by_identity.items():
        path = bench / f"claims_{slug}.csv"
        kept = [c for c in _read(path) if not c["scenario_id"].startswith(prefix)]
        _write(path, CLAIM_COLUMNS, kept + new_claims)
        claims_added += len(new_claims)
    return {
        "truth_rows": len(seen_truth),
        "rows_added": len(seen_rows),
        "claims_added": claims_added,
        "answers_with_no_named_judge": unnamed_judge,
    }


def main(argv: list[str]) -> int:
    if len(argv) % 2:
        print(__doc__)
        return 2
    opts = dict(zip(argv[::2], argv[1::2]))
    dsn = os.environ.get("CALIBRATION_TENANT_DSN")
    if "--run" not in opts or not dsn:
        print(__doc__)
        return 2
    import psycopg2  # noqa: PLC0415, only the live path needs it

    bench = pathlib.Path(opts.get("--benchmark", DEFAULT_BENCH))
    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(_SELECT_SQL, {"run_id": opts["--run"]})
            reviews = cur.fetchall()
    finally:
        conn.close()
    if not reviews:
        print(f"run {opts['--run']} has no claim reviews; nothing written")
        return 1
    counts = merge(bench, opts["--run"], reviews, source=opts.get("--source", "review"))
    print(" ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
