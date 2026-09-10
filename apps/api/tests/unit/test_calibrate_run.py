"""`calibrate_run.py`: the owner's labels against a run's stored verdicts (#58).

No database and no judge: samples and verdicts are handed in as the dicts the
two fetchers return, and the sheets live in tmp_path. What is pinned:

  - the sheet carries the four scored strings and an EMPTY verdict, one row per
    (scenario, gated metric), never the Judge's verdict, and never overwrites
  - the judge adapter is a lookup: PASS and FAIL from `binary_verdict`, ERROR
    for an absent row or a NULL verdict, and the row's identity rebuilt
  - `score_run` reaches the same statuses `compute_correlation` does, through
    the same `agreement_result`, and the artifact names the run's Judge
  - a deflected response and a non-gated dimension never enter the matrix
"""

from __future__ import annotations

import csv
import json
import pathlib
from unittest.mock import MagicMock

import pytest

from app.domain.judge_identity import JudgeIdentity
from app.services.calibration_service import load_calibration_status
from tests.evals.calibration import calibrate_run as cr
from tests.evals.calibration import compute_correlation as cc

IDENTITY = {"model": "gpt-5-mini", "reasoning_effort": "none", "prompt_version": "ragas-0.4.1"}


def _samples(n: int) -> list[dict]:
    return [
        {
            "scenario_id": f"S-{i:03d}",
            "dataset": "golden" if i % 2 else "exploratory",
            "question": f"Q{i}?",
            "response": f"A{i}.",
            "retrieved_contexts": [f"c{i}a", f"c{i}b"],
            "reference": f"R{i}.",
        }
        for i in range(1, n + 1)
    ]


class TestTheHarnessReadsWhatTheWriterWrote:
    """`fetch_samples` had no test at all, and its tuple unpack matched the SELECT
    by inspection only. Adding two columns to that SELECT is exactly the edit that
    breaks such an unpack, and on a tenant behind 0028 it would raise where the
    harness used to work."""

    WIDE = (
        "S-001", "golden", "How do I start it?", "pnpm dev.", ["c1"], "Run pnpm dev.",
        [{"role": "user", "content": "Setting up Earth Elements."}],
        "How do I start Earth Elements?",
    )

    def _conn(self, monkeypatch, rows, missing_column=False):
        import psycopg2

        seen: list[str] = []

        class _Cursor:
            def execute(self, sql, params=None):
                seen.append(sql)
                if missing_column and "resolved_question" in sql:
                    raise psycopg2.errors.UndefinedColumn("column turns does not exist")

            def fetchall(self):
                return [r[:6] for r in rows] if missing_column else list(rows)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        conn = MagicMock()
        conn.cursor.return_value = _Cursor()
        monkeypatch.setattr(psycopg2, "connect", lambda *a, **kw: conn)
        return conn, seen

    def test_every_selected_column_lands_on_the_dict(self, monkeypatch):
        self._conn(monkeypatch, [self.WIDE])

        [sample] = cr.fetch_samples("run-1", "postgresql://tenant")

        assert sample["scenario_id"] == "S-001"
        assert sample["question"] == "How do I start it?"
        assert sample["reference"] == "Run pnpm dev."
        assert sample["turns"] == [{"role": "user", "content": "Setting up Earth Elements."}]
        assert sample["resolved_question"] == "How do I start Earth Elements?"

    def test_a_tenant_behind_0028_still_gets_a_sheet(self, monkeypatch):
        """The writer degrades for the same reason; a harness that raised here
        would refuse to label a run it could have labelled."""
        conn, seen = self._conn(monkeypatch, [self.WIDE], missing_column=True)

        [sample] = cr.fetch_samples("run-1", "postgresql://tenant")

        assert sample["turns"] == []
        assert sample["resolved_question"] == ""
        assert sample["question"] == "How do I start it?"
        conn.rollback.assert_called_once()
        assert len(seen) == 2, "the narrow SELECT was never sent"


class TestTheSheetShowsWhatBoundTheQuestion:
    """#227 PR 2. A follow-up reaches the labeller with its conversation.

    The owner labels relevancy by reading the question and the answer. For a
    multi-turn scenario the question alone is not what was asked, so the sheet
    carries the conversation it was asked in and the rewrite relevancy was
    actually scored against. Both are empty for the single-turn rows that are the
    whole corpus before #227, so an older run's sheet reads as it did.
    """

    def _row(self, tmp_path, **extra) -> dict:
        sample = {**_samples(1)[0], **extra}
        path = tmp_path / "sheet.csv"
        cr.write_sheet([sample], path)
        with path.open(newline="", encoding="utf-8") as fh:
            return next(iter(csv.DictReader(fh)))

    def test_the_conversation_renders_oldest_first_with_its_roles(self, tmp_path):
        row = self._row(
            tmp_path,
            turns=[
                {"role": "user", "content": "I'm setting up Earth Elements."},
                {"role": "assistant", "content": "Happy to help."},
            ],
            resolved_question="How do I start the dev server for Earth Elements?",
        )

        assert row["turns"] == (
            "USER: I'm setting up Earth Elements.\nASSISTANT: Happy to help."
        )
        assert row["resolved_question"] == (
            "How do I start the dev server for Earth Elements?"
        )

    def test_a_single_turn_row_leaves_both_cells_empty(self, tmp_path):
        row = self._row(tmp_path)

        assert row["turns"] == ""
        assert row["resolved_question"] == ""

    def test_a_turns_value_that_is_not_a_conversation_renders_empty(self, tmp_path):
        assert self._row(tmp_path, turns="not a list")["turns"] == ""
        assert self._row(tmp_path, turns=[7, None])["turns"] == ""


def _verdicts(samples: list[dict], passed_by_row) -> dict:
    """passed_by_row(scenario_id, metric) -> bool | None."""
    out = {}
    for s in samples:
        for metric in cr.GATED_METRICS:
            out[(s["scenario_id"], metric)] = {
                "score": 0.9,
                "binary_verdict": passed_by_row(s["scenario_id"], metric),
                "judge_identity": json.dumps(IDENTITY),
            }
    return out


def _label(path: pathlib.Path, rows: list[tuple[str, str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario_id", "dimension", "human_verdict"])
        for row in rows:
            w.writerow(row)


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------


def test_the_sheet_has_one_row_per_gated_metric_with_the_text_and_no_verdict(tmp_path):
    sheet = tmp_path / "runs" / "r1" / "human_scores.csv"
    samples = _samples(3)

    code, messages = cr.write_sheet(samples, sheet)

    assert code == cc.EXIT_SECOND_PASS_EMITTED
    with sheet.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3 * len(cr.GATED_METRICS)
    assert {r["dimension"] for r in rows} == set(cr.GATED_METRICS)
    first = rows[0]
    assert first["question"] == "Q1?" and first["response"] == "A1." and first["reference"] == "R1."
    assert first["retrieved_contexts"] == "c1a\n\nc1b"
    assert all(r["human_verdict"] == "" for r in rows)
    assert "judge" not in "".join(rows[0].keys()).lower(), "the Judge's verdict must not be on the sheet"
    # It is the sheet compute_correlation's reader accepts.
    parsed = cc.read_human_score_rows(sheet)
    assert parsed["attempted"] == 6 and parsed["valid"] == 0


def test_the_sheet_refuses_to_overwrite_and_refuses_an_empty_run(tmp_path):
    sheet = tmp_path / "human_scores.csv"
    sheet.write_text("scenario_id,dimension,human_verdict\nS-001,faithfulness,pass\n", encoding="utf-8")

    code, messages = cr.write_sheet(_samples(2), sheet)

    assert code == cc.EXIT_SETUP_ERROR
    assert "NOT overwritten" in messages[0]
    assert sheet.read_text(encoding="utf-8").count("pass") == 1

    code, messages = cr.write_sheet([], tmp_path / "fresh.csv")
    assert code == cc.EXIT_SETUP_ERROR
    assert "no eval_samples rows" in messages[0]
    assert not (tmp_path / "fresh.csv").exists()


# ---------------------------------------------------------------------------
# The judge adapter
# ---------------------------------------------------------------------------


def test_the_stored_judge_is_a_lookup_over_the_rows():
    verdicts = {
        ("S-001", "faithfulness"): {"score": 0.95, "binary_verdict": True, "judge_identity": json.dumps(IDENTITY)},
        ("S-001", "answer_relevancy"): {"score": 0.42, "binary_verdict": False, "judge_identity": IDENTITY},
        ("S-002", "faithfulness"): {"score": None, "binary_verdict": None, "judge_identity": None},
    }
    judge = cr.stored_judge(verdicts)

    passed = judge("S-001", "faithfulness")
    assert (passed["verdict"], passed["score"]) == ("PASS", cr.STORED_PASS_SCORE)
    assert passed["judge_identity"] == JudgeIdentity(**IDENTITY)

    failed = judge("S-001", "answer_relevancy")
    assert (failed["verdict"], failed["score"]) == ("FAIL", cr.STORED_FAIL_SCORE)
    assert failed["judge_identity"] == JudgeIdentity(**IDENTITY), "a dict identity is read like a JSON one"

    null = judge("S-002", "faithfulness")
    assert (null["verdict"], null["score"]) == ("ERROR", 0)
    assert null["judge_identity"] is None

    absent = judge("S-999", "faithfulness")
    assert (absent["verdict"], absent["score"]) == ("ERROR", 0)


# ---------------------------------------------------------------------------
# Scoring a run
# ---------------------------------------------------------------------------


@pytest.fixture
def run_tree(tmp_path, monkeypatch):
    """A labelled run in tmp_path. build(passed_by_row, human, second) -> (samples, verdicts, sheet, pass2)."""
    monkeypatch.setattr(cc, "CALIBRATION_ARTIFACT_JSON", tmp_path / "calibration.json")

    def build(n, judge_passed, human_passed, second_passed=None):
        samples = _samples(n)
        verdicts = _verdicts(samples, judge_passed)
        sheet = tmp_path / "human_scores.csv"
        pass2 = tmp_path / "human_scores_pass2.csv"
        rows = [
            (s["scenario_id"], m, "pass" if human_passed(s["scenario_id"], m) else "fail")
            for s in samples
            for m in cr.GATED_METRICS
        ]
        _label(sheet, rows)
        if second_passed is not None:
            _label(
                pass2,
                [(sid, m, "pass" if second_passed(sid, m) else "fail") for sid, m, _ in rows],
            )
        return samples, verdicts, sheet, pass2

    return build


def _mostly_pass(sid: str, metric: str) -> bool:
    """A balanced-enough labelling: two scenarios fail on each metric."""
    return sid not in ("S-002", "S-005")


def test_a_judge_that_agrees_with_a_consistent_owner_is_calibrated(run_tree):
    samples, verdicts, sheet, pass2 = run_tree(12, _mostly_pass, _mostly_pass, _mostly_pass)

    result = cr.score_run(samples, verdicts, sheet, pass2)

    assert result["status"] == cc.STATUS_CALIBRATED, result["errors"]
    assert result["pairs"] == 24
    assert result["cells"]["judge_too_lenient"] == 0 and result["cells"]["judge_too_harsh"] == 0
    assert all(e["judge_identity"] == JudgeIdentity(**IDENTITY) for e in result["table"])


def test_a_judge_that_passes_what_the_owner_fails_is_not_calibrated(run_tree):
    def judge_passes_everything(sid, metric):
        return True

    samples, verdicts, sheet, pass2 = run_tree(12, judge_passes_everything, _mostly_pass, _mostly_pass)

    result = cr.score_run(samples, verdicts, sheet, pass2)

    assert result["status"] != cc.STATUS_CALIBRATED
    assert result["cells"]["judge_too_lenient"] == 4
    assert result["kappa"] is None or result["kappa"] < 0.01


def test_without_a_second_pass_the_ceiling_is_withheld_and_nothing_is_calibrated(run_tree):
    samples, verdicts, sheet, pass2 = run_tree(12, _mostly_pass, _mostly_pass, None)

    result = cr.score_run(samples, verdicts, sheet, pass2)

    assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET
    assert result["ceiling_interval"] is None
    assert any("no blind second verdict" in e for e in result["errors"])


def test_the_artifact_names_the_judge_the_run_stamped(run_tree):
    samples, verdicts, sheet, pass2 = run_tree(12, _mostly_pass, _mostly_pass, _mostly_pass)
    result = cr.score_run(samples, verdicts, sheet, pass2)

    path = cc.write_calibration_artifact(result, cc.CALIBRATION_ARTIFACT_JSON, sheet=sheet)
    status = load_calibration_status(path, JudgeIdentity(**IDENTITY))

    assert status.calibrated, status
    assert status.judge_identity == JudgeIdentity(**IDENTITY)
    assert status.labels_made_at is not None


def test_a_deflection_a_missing_sample_and_a_non_gated_dimension_never_enter_the_matrix(run_tree):
    from app.domain.pii_firewall import PII_DEFLECTION

    samples, verdicts, sheet, pass2 = run_tree(12, _mostly_pass, _mostly_pass, _mostly_pass)
    samples[0]["response"] = PII_DEFLECTION
    del samples[1]  # S-002 labelled, no sample
    with sheet.open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(["S-003", "context_recall", "pass"])

    result = cr.score_run(samples, verdicts, sheet, pass2)

    assert result["pairs"] == 24 - 2 - 2
    reasons = "\n".join(result["errors"])
    assert "PII firewall" in reasons
    assert "no eval_samples row" in reasons
    assert "not a gated metric" in reasons


def test_an_unlabelled_sheet_and_a_missing_sheet_are_not_measurements(tmp_path):
    sheet = tmp_path / "human_scores.csv"
    pass2 = tmp_path / "human_scores_pass2.csv"
    samples = _samples(2)
    verdicts = _verdicts(samples, lambda s, m: True)

    missing = cr.score_run(samples, verdicts, sheet, pass2)
    assert missing["status"] == cc.STATUS_SETUP_ERROR

    cr.write_sheet(samples, sheet)
    unlabelled = cr.score_run(samples, verdicts, sheet, pass2)
    assert unlabelled["status"] == cc.STATUS_NOT_CALIBRATED_YET
    assert unlabelled["pairs"] == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_the_cli_refuses_anything_but_one_flag_and_a_run_id(capsys):
    assert cr.main([]) == cc.EXIT_SETUP_ERROR
    assert cr.main(["--score"]) == cc.EXIT_SETUP_ERROR
    assert cr.main(["--judge", "r1"]) == cc.EXIT_SETUP_ERROR
    assert "usage" in capsys.readouterr().out


def test_the_cli_names_the_dsn_it_needs_before_touching_anything(monkeypatch, tmp_path):
    monkeypatch.delenv(cr.TENANT_DSN_ENV, raising=False)
    monkeypatch.setattr(cr, "RUNS_DIR", tmp_path)

    with pytest.raises(SystemExit) as exc:
        cr.main(["--sheet", "r1"])

    assert cr.TENANT_DSN_ENV in str(exc.value)
    assert not (tmp_path / "r1").exists()


def test_the_second_pass_is_emitted_from_the_run_sheet(monkeypatch, tmp_path):
    monkeypatch.setattr(cr, "RUNS_DIR", tmp_path)
    sheet = tmp_path / "r1" / cr.SHEET_NAME
    sheet.parent.mkdir()
    _label(sheet, [("S-001", "faithfulness", "pass"), ("S-001", "answer_relevancy", "fail")])

    assert cr.main(["--second-pass", "r1"]) == cc.EXIT_SECOND_PASS_EMITTED

    pass2 = tmp_path / "r1" / cr.PASS2_NAME
    with pass2.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {(r["scenario_id"], r["dimension"]) for r in rows} == {
        ("S-001", "faithfulness"), ("S-001", "answer_relevancy")
    }
    assert all(r["human_verdict"] == "" for r in rows)
