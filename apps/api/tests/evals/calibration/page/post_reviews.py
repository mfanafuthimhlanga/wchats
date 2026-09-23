"""Post the claims bench's answers to the review route, one sitting per POST.

    python post_reviews.py <reviews.json> <api_base> <agent_id> [--dry-run]

`reviews.json` is the bench artifact's `reviews` collection as a JSON list (or a
dict of id to doc): each doc carries run_id, scenario_id, position, statement
and supported. The key comes from the `WCHATS_API_KEY` environment variable and
goes in the `X-API-Key` header; it is never printed. Answers are grouped per run
and sent in sittings of at most 100. A refused sitting (422) is retried one
answer at a time so the good answers land and the refused ones are listed.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

SITTING = 100


def _post(url: str, key: str, answers: list[dict]) -> tuple[int, str]:
    req = urllib.request.Request(
        url, method="POST", data=json.dumps({"answers": answers}).encode(),
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def load(path: pathlib.Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    docs = list(raw.values()) if isinstance(raw, dict) else list(raw)
    out = []
    for d in docs:
        d = d.get("data", d) if isinstance(d, dict) else d
        if not all(k in d for k in ("run_id", "scenario_id", "position", "statement", "supported")):
            continue
        out.append({k: d[k] for k in ("run_id", "scenario_id", "position", "statement", "supported")})
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    reviews, base, agent = pathlib.Path(argv[0]), argv[1].rstrip("/"), argv[2]
    dry = "--dry-run" in argv
    key = os.environ.get("WCHATS_API_KEY", "")
    if not key and not dry:
        print("WCHATS_API_KEY is not set")
        return 2
    docs = load(reviews)
    by_run: dict[str, list[dict]] = {}
    for d in docs:
        by_run.setdefault(d["run_id"], []).append({k: d[k] for k in ("scenario_id", "position", "statement", "supported")})
    for run, answers in by_run.items():
        url = f"{base}/api/v1/agents/{agent}/eval-runs/{run}/claims/review"
        stored = 0
        refused: list[tuple[str, int, str]] = []
        for i in range(0, len(answers), SITTING):
            batch = answers[i:i + SITTING]
            if dry:
                print(f"run {run}: would post {len(batch)} answers")
                continue
            st, body = _post(url, key, batch)
            if st == 200:
                stored += json.loads(body)["stored"]
                continue
            if st != 422:
                print(f"run {run}: HTTP {st} {body}")
                return 1
            for a in batch:
                st1, body1 = _post(url, key, [a])
                if st1 == 200:
                    stored += 1
                else:
                    refused.append((a["scenario_id"], a["position"], body1))
        print(f"run {run}: answers {len(answers)}  stored {stored}  refused {len(refused)}")
        for sid, pos, why in refused:
            print(f"  refused {sid} {pos}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
