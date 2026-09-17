"""Build the labelling page for a run from its sheet.

    python build.py <sheet.csv> <out.html> [--names "acme,widgetco"] [--rubrics rubrics.json]

The page shows the question, the agent's answer sentence by sentence, and the
retrieved text, and saves PASS or FAIL per row to the artifact's `labels`
collection. Only the columns named in FIELDS reach the page: the verdict, score
and notes columns stay behind, so the label is blind to any earlier pass.

--names    words that appear in every passage of this project (the product name,
           the company), so their overlap between answer and passage says nothing.
--rubrics  JSON of {dimension: {title, short, text, anchors, notes}} to add to or
           override the built-in faithfulness and answer_relevancy rubrics. A
           dimension with no rubric gets a plain PASS/FAIL one.
"""

import csv
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FIELDS = ("scenario_id", "dimension", "dataset", "question", "response",
          "retrieved_contexts", "reference", "turns", "resolved_question")


def _slot(html: str, slot: str, value) -> str:
    if html.count(slot) != 1:
        raise SystemExit(f"template.html needs exactly one {slot}")
    return html.replace(slot, json.dumps(value, ensure_ascii=False).replace("</", "<\\/"))


def build(sheet: pathlib.Path, out: pathlib.Path, names: list[str] = (), rubrics: dict | None = None) -> tuple[list[str], int]:
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

    html = (HERE / "template.html").read_text(encoding="utf-8")
    html = _slot(html, "__NAMES__", list(names))
    html = _slot(html, "__RUBRICS__", rubrics or {})
    html = _slot(html, "__ROWS__", rows)
    out.write_text(html, encoding="utf-8")
    return sorted({r["dimension"] for r in rows}), len(rows)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    sheet, out = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    opts = dict(zip(argv[2::2], argv[3::2]))
    names = [w.strip() for w in opts.get("--names", "").split(",") if w.strip()]
    rubrics = json.loads(pathlib.Path(opts["--rubrics"]).read_text(encoding="utf-8")) if "--rubrics" in opts else None
    dims, n = build(sheet, out, names, rubrics)
    print(f"rows: {n}  dimensions: {', '.join(dims)}  names: {len(names)}  bytes: {out.stat().st_size}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
