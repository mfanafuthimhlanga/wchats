"""Build the labelling page for a run from its sheet.

    python tests/evals/calibration/page/build.py runs/<run_id>/human_scores.csv out.html

The page shows the question, the agent's answer sentence by sentence, and the
retrieved text, and saves PASS or FAIL per row to the artifact's `labels`
collection. Only the columns named in FIELDS reach the page: the verdict, score
and notes columns stay behind, so the label is blind to any earlier pass.
"""

import csv
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FIELDS = ("scenario_id", "dimension", "dataset", "question", "response",
          "retrieved_contexts", "reference", "turns", "resolved_question")


def build(sheet: pathlib.Path, out: pathlib.Path) -> tuple[list[str], int]:
    with sheet.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in FIELDS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{sheet} is missing columns {missing}")
        rows = [{k: r[k] for k in FIELDS} for r in reader]

    seen = set()
    for r in rows:
        k = (r["scenario_id"], r["dimension"])
        if k in seen:
            raise SystemExit(f"{sheet} repeats {k}")
        seen.add(k)

    template = (HERE / "template.html").read_text(encoding="utf-8")
    if template.count("__ROWS__") != 1:
        raise SystemExit("template.html needs exactly one __ROWS__")

    blob = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    out.write_text(template.replace("__ROWS__", blob), encoding="utf-8")
    return sorted({r["dimension"] for r in rows}), len(rows)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    sheet, out = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    dims, n = build(sheet, out)
    print(f"rows: {n}  dimensions: {', '.join(dims)}  bytes: {out.stat().st_size}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
