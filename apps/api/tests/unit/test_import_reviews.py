"""A run's claim reviews become truth in the reviewed benchmark (#290 step 3).

`import_reviews.merge` over a temp directory that starts empty, the way the
reviewed benchmark does: every Tenant answer becomes a truth row with `novel`
empty and the source named; the scenario becomes a rows.csv row keyed by run
and scenario carrying the id and nothing of the Tenant's text; the judge's
claims land under the identity that raised them without the judge's reason; a
second import of the same run replaces its rows. Then the scorer reads the
result and precision is a ratio, matched exactly on the judge's own statement
so a neighbouring claim the Tenant never saw inherits nothing.
"""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import pathlib

BENCH = pathlib.Path(__file__).resolve().parents[1] / "evals" / "calibration" / "benchmark"
RUN = "33333333-3333-3333-3333-333333333333"
IDENTITY = {"model": "gpt-5.6-luna", "reasoning_effort": "none", "prompt_version": "ragas-0.4.3"}
CLAIMS = [
    {"statement": "Delivery costs R30 in Tembisa.", "supported": True, "reason": "carried"},
    {"statement": "Orders over R500 ship free.", "supported": False, "reason": "absent"},
    {"statement": "Orders over R500 ship free to Tembisa.", "supported": False, "reason": "absent"},
]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, BENCH / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


imp = _load("import_reviews")
sc = _load("score_claims")


def _review(position: int, supported: bool, scenario: str = "s1", identity: object = IDENTITY) -> tuple:
    return (scenario, position, CLAIMS[position]["statement"], supported, dt.date(2026, 9, 20),
            json.dumps(CLAIMS), json.dumps(identity) if identity is not None else None)


def _rows(path: pathlib.Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class TestMerge:
    def test_an_answer_becomes_a_truth_row_with_novel_empty_and_the_source_named(self, tmp_path):
        counts = imp.merge(tmp_path, RUN, [_review(1, False)], source="review")
        assert counts == {"truth_rows": 1, "rows_added": 1, "claims_added": 3, "answers_with_no_named_judge": 0}
        [row] = _rows(tmp_path / "truth.csv")
        assert row["scenario_id"] == f"{RUN}:s1"
        assert (row["claim"], row["supported"], row["novel"], row["source"]) == ("Orders over R500 ship free.", "false", "", "review")

    def test_a_yes_lands_as_supported_true(self, tmp_path):
        imp.merge(tmp_path, RUN, [_review(1, True)], source="review")
        assert _rows(tmp_path / "truth.csv")[0]["supported"] == "true"

    def test_the_row_carries_the_id_and_nothing_of_the_tenants_text(self, tmp_path):
        imp.merge(tmp_path, RUN, [_review(1, False)], source="review")
        [row] = _rows(tmp_path / "rows.csv")
        assert row == {"scenario_id": f"{RUN}:s1", "source": f"review:{RUN[:8]}"}

    def test_the_judges_claims_land_under_the_identity_that_raised_them_without_the_reason(self, tmp_path):
        imp.merge(tmp_path, RUN, [_review(1, False)], source="review")
        claims = _rows(tmp_path / "claims_gpt-5.6-luna-none-ragas-0.4.3.csv")
        assert [(c["position"], c["supported"]) for c in claims] == [("0", "true"), ("1", "false"), ("2", "false")]
        assert "reason" not in claims[0]

    def test_a_partial_judge_identity_names_no_file_and_is_counted(self, tmp_path):
        counts = imp.merge(tmp_path, RUN, [_review(1, False, identity={"model": "x"})], source="review")
        assert counts["answers_with_no_named_judge"] == 1
        assert not list(tmp_path.glob("claims_*.csv"))

    def test_an_identity_with_path_characters_cannot_leave_the_directory(self):
        assert imp.identity_slug({"model": "../x/y", "reasoning_effort": "none", "prompt_version": "v\\1"}) == "_x_y-none-v_1"

    def test_a_second_import_of_the_same_run_replaces_its_rows(self, tmp_path):
        imp.merge(tmp_path, RUN, [_review(1, False), _review(2, False)], source="review")
        imp.merge(tmp_path, RUN, [_review(1, True)], source="review")
        truth = _rows(tmp_path / "truth.csv")
        assert [(t["claim"], t["supported"]) for t in truth] == [("Orders over R500 ship free.", "true")]
        assert len(_rows(tmp_path / "claims_gpt-5.6-luna-none-ragas-0.4.3.csv")) == 3
        assert len(_rows(tmp_path / "rows.csv")) == 1

    def test_another_runs_rows_survive_an_import(self, tmp_path):
        other = "44444444-4444-4444-4444-444444444444"
        imp.merge(tmp_path, other, [_review(1, False)], source="review")
        imp.merge(tmp_path, RUN, [_review(1, True)], source="review")
        assert sorted(t["scenario_id"] for t in _rows(tmp_path / "truth.csv")) == [f"{RUN}:s1", f"{other}:s1"]

    def test_the_scorer_then_reports_a_precision_ratio_and_credits_no_neighbour(self, tmp_path):
        """The Tenant's yes on claim 1 is a false alarm; claim 2, never shown, stays undecided."""
        imp.merge(tmp_path, RUN, [_review(1, True)], source="review")
        judge = _rows(tmp_path / "claims_gpt-5.6-luna-none-ragas-0.4.3.csv")
        r = sc.score(_rows(tmp_path / "truth.csv"), judge, _rows(tmp_path / "rows.csv"))
        assert (r["supported_truth"], r["true_alarms"], r["false_alarms"], r["undecided"]) == (1, 0, 1, 1)
        assert r["answers_with_planted"] == 0, "a reviewed answer is not a planted one"
        [line] = [line for line in sc.report(r, "j") if line.strip().startswith("precision")]
        assert line.strip().startswith("precision 0 of 1 decided")


def test_a_fanned_out_review_row_is_written_once(tmp_path):
    """The join behind the import has no unique key; a doubled tuple lands as one truth row."""
    counts = imp.merge(tmp_path, RUN, [_review(1, False), _review(1, False)], source="review")
    assert counts["truth_rows"] == 1
    assert len(_rows(tmp_path / "truth.csv")) == 1


def test_a_slug_never_starts_with_a_dot():
    slug = imp.identity_slug({"model": "../x/y", "reasoning_effort": "none", "prompt_version": "v1"})
    assert slug == "_x_y-none-v1"
