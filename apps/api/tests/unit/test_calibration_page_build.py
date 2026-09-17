"""The labelling page is verdict-blind and its JSON cannot close the script tag."""

import csv
import pathlib
import sys

import pytest

PAGE = pathlib.Path(__file__).resolve().parents[1] / "evals" / "calibration" / "page"
sys.path.insert(0, str(PAGE))
import build  # noqa: E402

COLUMNS = [
    "scenario_id", "dimension", "human_verdict", "human_score", "notes", "dataset",
    "question", "response", "retrieved_contexts", "reference", "turns", "resolved_question",
]


def _sheet(tmp_path: pathlib.Path, rows: list[dict]) -> pathlib.Path:
    sheet = tmp_path / "human_scores.csv"
    with sheet.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return sheet


def _row(sid: str, **over) -> dict:
    base = {c: "" for c in COLUMNS}
    base.update(scenario_id=sid, dimension="faithfulness", dataset="golden",
                question="q", response="a", retrieved_contexts="c", reference="r")
    base.update(over)
    return base


def test_verdict_score_and_notes_stay_off_the_page(tmp_path):
    sheet = _sheet(tmp_path, [_row("s1", human_verdict="fail", human_score="2", notes="SECRET-NOTE")])
    out = tmp_path / "page.html"
    dims, n = build.build(sheet, out)
    html = out.read_text(encoding="utf-8")
    assert (dims, n) == (["faithfulness"], 1)
    assert "SECRET-NOTE" not in html
    assert "human_verdict" not in html
    assert '"scenario_id": "s1"' in html


def test_a_closing_tag_in_the_text_cannot_end_the_rows_script(tmp_path):
    sheet = _sheet(tmp_path, [_row("s1", response="see </script><b>x</b>")])
    out = tmp_path / "page.html"
    build.build(sheet, out)
    html = out.read_text(encoding="utf-8")
    assert "</script><b>" not in html
    assert "<\\/script><b>" in html


def test_a_repeated_scenario_and_dimension_is_refused(tmp_path):
    sheet = _sheet(tmp_path, [_row("s1"), _row("s1")])
    with pytest.raises(SystemExit, match="repeats"):
        build.build(sheet, tmp_path / "page.html")
