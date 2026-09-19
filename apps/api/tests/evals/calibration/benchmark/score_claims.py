"""Score a judge's per-claim verdicts against a benchmark's truth per claim.

    python score_claims.py --benchmark <dir> --claims <claims.csv>

<dir>/rows.csv holds the answers. <dir>/truth.csv holds `scenario_id, claim,
supported, novel`, one row per claim whose truth is known: a sentence planted
into an answer (supported false, by construction) or a claim a reviewer
answered yes or no on. `novel` is the planted sentence's fabricated detail as
words, the words neither the retrieved text nor the original answer carry,
space separated and empty for a reviewed claim. claims.csv holds
`scenario_id, statement, supported`, one row per claim the judge decided, as
`eval_results.claims` stores them. The judge identity is whatever the file is
named after `claims_`; nothing here checks it, so name the file honestly.

A judge claim MATCHES a truth claim when at least JUDGE_IN_TRUTH of the judge
claim's words are in the truth claim AND the judge claim carries at least one
of the truth claim's `novel` words. The judge splits a sentence into atomic
statements, so the first test is on the judge claim's side. The second is what
separates the fabricated detail from a neighbouring claim about the same
subject: "the migration command migrates the control database" shares four
words with a planted rollback sentence and none of its fabricated ones, and
"pnpm preview serves the bundle locally" is a claim the original answer already
made and the judge already flagged before anything was planted. A truth claim
with no novel word skips the second test. Numbers count as words whatever
their length, because the fabricated detail is often a number. Every match is
printed, because the rule is word overlap and a reader has to be able to check
it.

Recall:    unsupported truth claims that at least one matching judge claim also
           marks unsupported, over all unsupported truth claims, with a Wilson
           95% interval. A truth claim with no matching judge claim at all is a
           miss of its own kind, "not extracted", and stays in the denominator.
           Zero unsupported truth claims is `unknown`, not a ratio.
Precision: judge-unsupported claims matching an unsupported truth claim (true
           alarms), over judge-unsupported claims matching any truth claim. A
           false alarm can only exist when truth.csv holds a SUPPORTED claim,
           so until it does the line says `unknown` and reports the confirmed
           count alone. A judge-unsupported claim matching no truth claim is
           UNDECIDED, never a false alarm. A judge claim matching truth claims
           that disagree is AMBIGUOUS and leaves the decided set.

Answer level, for the gate rule step 3 of #290 proposes: how many answers carry
at least one judge-unsupported claim, split by whether the answer holds a
planted claim. An answer the judge wrote no claim for is UNSCORED, excluded
from those counts and reported, because an absent judgement is not a clean
answer.

Exit 0 on a report, 2 on usage, 1 when a file is refused.
"""

from __future__ import annotations

import csv
import math
import pathlib
import re
import sys
from collections import defaultdict

JUDGE_IN_TRUTH = 0.5


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 or w.isdigit()}


def novel_words(truth: dict) -> set[str]:
    """The fabricated detail, as truth.csv states it. Empty skips the test."""
    return set((truth.get("novel") or "").lower().split())


def matches(judge_claim: str, truth_claim: str, novel: set[str]) -> bool:
    j, t = words(judge_claim), words(truth_claim)
    if not j or not t:
        return False
    if len(j & t) / len(j) < JUDGE_IN_TRUTH:
        return False
    return not novel or bool(j & novel)


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _bool(value: str | None) -> bool:
    v = (value or "").strip().lower()
    if v not in ("true", "false"):
        raise SystemExit(f"supported must be true or false, got {value!r}")
    return v == "true"


def read_csv(path: pathlib.Path, need: set[str]) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        header = set(reader.fieldnames or [])
    if not need <= header:
        raise SystemExit(f"{path} needs columns {sorted(need)}")
    return rows


def score(truth: list[dict], judge: list[dict], rows: list[dict]) -> dict:
    """The counts. Pure, so a test can hand it three lists."""
    judge = list(judge)
    answers = [r["scenario_id"] for r in rows]
    strays = sorted({c["scenario_id"] for c in judge} - set(answers))
    if strays:
        raise SystemExit(f"claims name {len(strays)} scenario(s) rows.csv does not hold: {strays[:3]}")
    judge_by: dict[str, list[dict]] = defaultdict(list)
    for c in judge:
        judge_by[c["scenario_id"]].append(c)

    truth_hits: list[dict] = []
    verdicts_for: dict[int, set[bool]] = defaultdict(set)  # id(judge claim) -> truth values it matched
    for t in truth:
        novel = novel_words(t)
        found = [c for c in judge_by[t["scenario_id"]] if matches(c["statement"], t["claim"], novel)]
        for c in found:
            verdicts_for[id(c)].add(_bool(t["supported"]))
        flagged = [c for c in found if not _bool(c["supported"])]
        truth_hits.append({"truth": t, "novel": novel, "matched": found, "flagged": flagged})

    unsupported_truth = [h for h in truth_hits if not _bool(h["truth"]["supported"])]
    recall_hits = [h for h in unsupported_truth if h["flagged"]]
    not_extracted = [h for h in unsupported_truth if not h["matched"]]

    judge_unsupported = [c for c in judge if not _bool(c["supported"])]
    ambiguous = [c for c in judge_unsupported if len(verdicts_for.get(id(c), ())) > 1]
    decided = [c for c in judge_unsupported if len(verdicts_for.get(id(c), ())) == 1]
    true_alarms = [c for c in decided if False in verdicts_for[id(c)]]
    false_alarms = [c for c in decided if True in verdicts_for[id(c)]]
    undecided = [c for c in judge_unsupported if id(c) not in verdicts_for]

    planted = {t["scenario_id"] for t in truth if not _bool(t["supported"])}
    flagged_answers = {c["scenario_id"] for c in judge_unsupported}
    scored = [a for a in answers if judge_by.get(a)]
    unscored = [a for a in answers if not judge_by.get(a)]
    return {
        "truth_hits": truth_hits,
        "unsupported_truth": len(unsupported_truth),
        "recall_hits": len(recall_hits),
        "not_extracted": not_extracted,
        "supported_truth": sum(1 for t in truth if _bool(t["supported"])),
        "judge_unsupported": len(judge_unsupported),
        "true_alarms": len(true_alarms),
        "false_alarms": len(false_alarms),
        "ambiguous": len(ambiguous),
        "undecided": len(undecided),
        "answers": len(answers),
        "unscored": unscored,
        "answers_with_planted": sum(1 for a in scored if a in planted),
        "flagged_with_planted": sum(1 for a in scored if a in planted and a in flagged_answers),
        "answers_without_planted": sum(1 for a in scored if a not in planted),
        "flagged_without_planted": sum(1 for a in scored if a not in planted and a in flagged_answers),
        "per_answer_unsupported": sorted(
            len([c for c in judge_by[a] if not _bool(c["supported"])]) for a in scored
        ),
    }


def _state(h: dict) -> str:
    supported = _bool(h["truth"]["supported"])
    if not h["matched"]:
        return "not extracted"
    if supported:
        return "FALSE ALARM, judge flagged a carried claim" if h["flagged"] else "clear"
    return "FOUND" if h["flagged"] else "MISSED, judge marked it supported"


def report(result: dict, identity: str) -> list[str]:
    r = result
    lines = [f"=== judge {identity} (as the claims file is named)"]
    for h in r["truth_hits"]:
        t = h["truth"]
        lines.append(f"  {t['scenario_id'][:8]} {_state(h):<42} {t['claim'][:70]}")
        for c in h["matched"]:
            verdict = "unsupported" if not _bool(c["supported"]) else "supported  "
            lines.append(f"           judge {verdict} {c['statement'][:70]}")
    if r["unsupported_truth"]:
        low, high = wilson(r["recall_hits"], r["unsupported_truth"])
        lines.append(f"  recall    {r['recall_hits']} of {r['unsupported_truth']} unsupported truth claims flagged, "
                     f"95% interval [{low:.2f}, {high:.2f}]"
                     + (f", {len(r['not_extracted'])} not extracted" if r["not_extracted"] else ""))
    else:
        lines.append("  recall    unknown: no unsupported truth claim to find")
    decided = r["true_alarms"] + r["false_alarms"]
    tail = f"{r['undecided']} undecided, no truth yet" + (f", {r['ambiguous']} ambiguous" if r["ambiguous"] else "")
    if r["supported_truth"] == 0:
        lines.append(f"  precision unknown: truth.csv holds no supported claim, so a false alarm cannot occur; "
                     f"{r['true_alarms']} flags confirmed against planted sentences; {tail}")
    elif decided == 0:
        lines.append(f"  precision unknown: no judge-unsupported claim matched a truth claim; {tail}")
    else:
        lines.append(f"  precision {r['true_alarms']} of {decided} decided judge-unsupported claims are true alarms, "
                     f"{r['false_alarms']} false alarms; {tail}")
    lines.append(f"  answers flagged (any unsupported claim): {r['flagged_with_planted']} of {r['answers_with_planted']} "
                 f"with a planted claim, {r['flagged_without_planted']} of {r['answers_without_planted']} without"
                 + (f"; {len(r['unscored'])} unscored, no judge claim at all" if r["unscored"] else ""))
    p = r["per_answer_unsupported"]
    if p:
        lines.append(f"  unsupported claims per scored answer ({len(p)}): min {p[0]}, upper median {p[len(p) // 2]}, max {p[-1]}")
    return lines


def main(argv: list[str]) -> int:
    opts = dict(zip(argv[::2], argv[1::2]))
    if len(argv) % 2 or "--benchmark" not in opts or "--claims" not in opts:
        print(__doc__)
        return 2
    bench = pathlib.Path(opts["--benchmark"])
    truth = read_csv(bench / "truth.csv", {"scenario_id", "claim", "supported", "novel"})
    rows = read_csv(bench / "rows.csv", {"scenario_id"})
    claims_path = pathlib.Path(opts["--claims"])
    judge = read_csv(claims_path, {"scenario_id", "statement", "supported"})
    known = {r["scenario_id"] for r in rows}
    for t in truth:
        if t["scenario_id"] not in known:
            raise SystemExit(f"truth.csv names {t['scenario_id']}, which rows.csv does not hold")
    identity = claims_path.stem.removeprefix("claims_")
    print("\n".join(report(score(truth, judge, rows), identity)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
