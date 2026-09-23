"""Build the claims bench for one eval run from the claims route's payload.

    python build_claims.py <run_id> <claims.json> <out.html>

`claims.json` is the body of `GET /api/v1/agents/{agent}/eval-runs/{run}/claims`:
one entry per answer that carries a flagged claim, with the question, the
response, the retrieved passages and the flagged claims. The page shows one
answer per screen, the flagged claims beside it, and saves each yes or no to
the artifact's `reviews` collection keyed run__scenario__position. Answers with
no flagged claim are dropped: there is nothing to ask.

`post_reviews.py` turns the collection back into one POST per sitting.
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _slot(html: str, slot: str, value) -> str:
    if html.count(slot) != 1:
        raise SystemExit(f"claims_template.html needs exactly one {slot}")
    return html.replace(slot, json.dumps(value, ensure_ascii=False).replace("</", "<\\/"))


def build(run_id: str, claims: pathlib.Path, out: pathlib.Path) -> tuple[int, int]:
    payload = json.loads(claims.read_text(encoding="utf-8"))
    scenarios = [
        {
            "scenario_id": s["scenario_id"],
            "question": s["question"],
            "response": s["response"],
            "retrieved_contexts": list(s.get("retrieved_contexts") or []),
            "claims": [{"position": c["position"], "statement": c["statement"]} for c in s["claims"]],
        }
        for s in payload["scenarios"]
        if s["claims"]
    ]
    html = (HERE / "claims_template.html").read_text(encoding="utf-8")
    html = _slot(html, "__RUN__", run_id)
    html = _slot(html, "__SCENARIOS__", scenarios)
    out.write_text(html, encoding="utf-8")
    return len(scenarios), sum(len(s["claims"]) for s in scenarios)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    run_id, claims, out = argv[0], pathlib.Path(argv[1]), pathlib.Path(argv[2])
    answers, flagged = build(run_id, claims, out)
    print(f"answers: {answers}  flagged claims: {flagged}  bytes: {out.stat().st_size}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
