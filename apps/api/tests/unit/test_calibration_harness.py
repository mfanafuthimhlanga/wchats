"""Unit tests for the judge calibration harness (audit D7).

`tests/evals/calibration/compute_correlation.py` is the only instrument in this
codebase that can say whether ANY of its LLM verdicts are trustworthy — the
Gatekeeper, the Auditor, the Strategist, classify_severity, and the Actor gate
that runs synchronously before money moves. It was built to the right
specification (Spearman rank correlation, threshold 0.75, AI-SPEC.md §5.2) and
then shipped with two properties that made it unable to say anything:

  1. `human_scores.csv` has never been given a single label, and
  2. an unscored file exited 0, described in the docstring as "informational".

(2) is the defect this module pins. Exit 0 is what a shell, a CI job, a
checklist or a summary reads as success, so an instrument that had never been
calibrated reported the same thing as one that had passed. Missing data is
never passing data.

(1) is not a defect and is not this module's to fix. The `human_score` column
belongs to the owner. An agent-filled calibration set would measure the judge's
agreement with a model — which is the exact tautology the instrument exists to
detect — so the tests here pin that nothing in the harness writes that file,
and never assert anything about what the owner has or has not scored.

No network, no ANTHROPIC_API_KEY, no judge call: `judge_fn` is injected into
compute_correlation() and `readiness()` is local by construction.
"""

import json
import pathlib

import pytest

from tests.evals.calibration import compute_correlation as cc

# ---------------------------------------------------------------------------
# Fixtures — a self-contained calibration tree in tmp_path
# ---------------------------------------------------------------------------


def _verdict_for(score: str) -> str:
    """The binary label a 1-5 score implies, for FIXTURES only.

    BACKLOG 8.2b moved the gate to `human_verdict`. Real labelling is binary and
    the 1-5 score is optional; these fixtures keep both so the reported Spearman
    is still exercised, and derive one from the other so a row stays a 3-tuple.
    """
    if not score:
        return ""  # an unlabelled row: no verdict AND no score
    try:
        return cc.VERDICT_PASS if int(score) >= 3 else cc.VERDICT_FAIL
    except ValueError:
        # A fixture deliberately writing a malformed score keeps a WELL-FORMED
        # verdict, so the row exercises the score parser rather than being
        # rejected one column earlier and never reaching it.
        return cc.VERDICT_PASS


def _write_csv(
    path: pathlib.Path,
    rows: list[tuple[str, str, str]],
    *,
    with_scores: bool = True,
) -> pathlib.Path:
    """The sheet. `with_scores=False` writes the shape the owner is ASKED for.

    F13, adversarial review 2026-08-18. Verdict and score were always both
    present or both absent here, because both were derived from one tuple field.
    So no test ever fed a verdict-only row - the row the harness's own printed
    guidance asks the owner to produce - and F1, a TypeError in `main()` on
    exactly that shape, sat undetected behind the gap.
    """
    lines = ["scenario_id,dimension,human_verdict,human_score,notes"]
    lines += [
        f"{sid},{dim},{_verdict_for(score)},{score if with_scores else ''},note"
        for sid, dim, score in rows
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_second_pass(
    path: pathlib.Path,
    rows: list[tuple[str, str, str]],
    mode: str = "match",
) -> pathlib.Path:
    """The blind re-label sheet (BACKLOG 8.2c). Same header, no notes, no score.

    `mode` is what KIND of labeller wrote it:

        "match"    perfectly self-consistent - the ceiling is 1.0
        "sloppy"   flips one row - a real labeller, and a ceiling below 1.0
        "partial"  omits one row - no ceiling at all, because the rows the
                   labeller happened to finish are not a random sample

    Default "match" so that every test about the JUDGE keeps being about the
    judge. The ceiling's own behaviour is pinned by TestTheCeilingIsMeasuredNotChosen.
    """
    labelled = [(sid, dim, _verdict_for(score)) for sid, dim, score in rows]
    labelled = [row for row in labelled if row[2]]
    if mode == "partial" and labelled:
        labelled = labelled[:-1]
    if mode == "sloppy" and labelled:
        sid, dim, verdict = labelled[0]
        flipped = cc.VERDICT_FAIL if verdict == cc.VERDICT_PASS else cc.VERDICT_PASS
        labelled[0] = (sid, dim, flipped)

    lines = ["scenario_id,dimension,human_verdict"]
    lines += [f"{sid},{dim},{verdict}" for sid, dim, verdict in labelled]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def calibration_tree(tmp_path, monkeypatch):
    """Point the harness at a tmp scenarios/responses/csv triple.

    Returns a callable: build(rows) -> the csv path, having also written a
    scenario file and a response file for every scenario id referenced.
    """
    scenarios = tmp_path / "scenarios"
    responses = tmp_path / "responses"
    scenarios.mkdir()
    responses.mkdir()

    monkeypatch.setattr(cc, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(cc, "RESPONSES_DIR", responses)

    def build(rows, *, capture: set[str] | None = None, with_scores: bool = True,
              second_pass: str | None = "match"):
        for sid, _dim, _score in rows:
            (scenarios / f"{sid}_fixture.json").write_text(
                json.dumps({"id": sid, "turns": [{"role": "user", "message": f"q for {sid}"}]}),
                encoding="utf-8",
            )
            if capture is None or sid in capture:
                (responses / f"{sid}.json").write_text(
                    json.dumps({"scenario_id": sid, "response_text": f"a for {sid}",
                                "tool_calls_log": []}),
                    encoding="utf-8",
                )
        csv_path = _write_csv(tmp_path / "human_scores.csv", rows, with_scores=with_scores)
        monkeypatch.setattr(cc, "HUMAN_SCORES_CSV", csv_path)

        # BACKLOG 8.2c. Pointed at tmp ALWAYS, so no test can read the real
        # sheet, and written unless a test is about its absence.
        pass2_path = tmp_path / "human_scores_pass2.csv"
        monkeypatch.setattr(cc, "HUMAN_SCORES_PASS2_CSV", pass2_path)
        if second_pass is not None:
            _write_second_pass(pass2_path, rows, mode=second_pass)
        return csv_path

    return build


def _judge_returning(scores_by_scenario: dict[str, int]):
    """A judge_fn stand-in. Never touches anthropic."""

    def _judge(dimension, transcript, tool_calls_log):
        sid = transcript.split("q for ")[1].split("\n")[0]
        score = scores_by_scenario[sid]
        return {
            "dimension": dimension,
            "verdict": "PASS" if score >= 3 else "FAIL",
            "score": score,
            "reason": f"judge says {score}",
        }

    return _judge


_FOUR_ROWS = [
    ("S-101", "grounding_fidelity", "1"),
    ("S-102", "grounding_fidelity", "2"),
    ("S-103", "grounding_fidelity", "4"),
    ("S-104", "grounding_fidelity", "5"),
]


# ---------------------------------------------------------------------------
# The defect: an unscored file is not a pass
# ---------------------------------------------------------------------------


class TestUnscoredIsNotAPass:
    """Missing data is never passing data."""

    def test_an_unscored_file_is_neither_pass_nor_fail(self, calibration_tree):
        calibration_tree([(sid, dim, "") for sid, dim, _ in _FOUR_ROWS])

        result = cc.compute_correlation(_judge_returning({}))

        assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET
        assert result["rho"] is None, (
            "a run that made no measurement must not hand a number to a reader"
        )
        assert result["valid"] == 0
        assert result["attempted"] == 4, "the denominator travels with the rows"
        assert cc.EXIT_CODE_FOR_STATUS[result["status"]] != cc.EXIT_CALIBRATED, (
            "the shipped script exited 0 here and called it 'informational'; in a "
            "shell, exit 0 is success"
        )

    def test_the_four_outcomes_have_four_distinct_exit_codes(self):
        codes = list(cc.EXIT_CODE_FOR_STATUS.values())
        assert len(set(codes)) == len(codes) == 4
        assert cc.EXIT_CODE_FOR_STATUS[cc.STATUS_CALIBRATED] == 0
        assert cc.TRUSTWORTHY_STATUS == cc.STATUS_CALIBRATED, (
            "exactly one status may be read as 'this judge may be trusted at scale'"
        )

    def test_too_few_pairs_is_not_calibrated_yet(self, calibration_tree):
        """Two rows cannot produce a correlation; they must not produce a pass."""
        rows = _FOUR_ROWS[:2]
        calibration_tree(rows)

        result = cc.compute_correlation(_judge_returning({"S-101": 1, "S-102": 2}))

        assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET
        assert result["rho"] is None
        assert result["pairs"] == 2 < cc.MIN_PAIRS

    def test_a_judge_error_is_not_a_score_of_zero(self, calibration_tree):
        """judge() returns score=0 with verdict ERROR on any exception.

        Zero is the absence of a score, not a low one. Correlating it would make
        rho move with the Anthropic API's failure rate, and four API errors plus
        one real pair would report a correlation.
        """
        calibration_tree(_FOUR_ROWS)

        def _erroring_judge(dimension, transcript, tool_calls_log):
            return {"dimension": dimension, "verdict": "ERROR", "score": 0,
                    "reason": "connection reset"}

        result = cc.compute_correlation(_erroring_judge)

        assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET
        assert result["rho"] is None
        assert result["pairs"] == 0
        assert result["valid"] == 4, (
            "the human labels were valid; it was the judge that produced nothing"
        )
        assert len(result["errors"]) == 4

    def test_total_agreement_on_one_label_is_unknown_not_pass(self, calibration_tree):
        """Both raters said PASS to everything, so chance agreement is certain.

        BACKLOG 8.2b. Raw agreement here is 100%, and the shipped harness would
        have reported that as a calibrated judge. Cohen's kappa is UNDEFINED
        instead: with one label on both sides, a coin agrees just as often, so
        this set cannot distinguish a good judge from a coin. Undefined is
        reported as NOT CALIBRATED YET, which is neither a pass nor a judge
        failure.
        """
        rows = [(sid, dim, "5") for sid, dim, _ in _FOUR_ROWS]
        calibration_tree(rows)

        result = cc.compute_correlation(
            _judge_returning({"S-101": 5, "S-102": 5, "S-103": 5, "S-104": 5})
        )

        assert result["cells"]["both_pass"] == 4
        assert result["kappa"] is None, "undefined, and never rendered as 0.0"
        assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET
        assert any("undefined" in e for e in result["errors"])

    def test_chance_level_agreement_is_a_measurement_and_it_fails(self, calibration_tree):
        """The other side of the same coin, and it must NOT be 'yet'.

        The human passed everything; the judge passed half. Raw agreement is 50%
        and kappa is 0.0 exactly: no better than chance. That IS a measurement,
        so it is a failure rather than an absence, and the distinction is what
        tells the owner whether to label more rows or fix the judge.
        """
        rows = [(sid, dim, "5") for sid, dim, _ in _FOUR_ROWS]
        calibration_tree(rows)

        result = cc.compute_correlation(
            _judge_returning({"S-101": 1, "S-102": 2, "S-103": 4, "S-104": 5})
        )

        assert result["kappa"] == pytest.approx(0.0)
        assert result["status"] == cc.STATUS_NOT_CALIBRATED
        assert result["cells"] == {
            "both_pass": 2, "judge_too_harsh": 2, "judge_too_lenient": 0, "both_fail": 0
        }

    def test_a_judge_that_failed_most_of_its_calls_is_not_calibrated(
        self, calibration_tree
    ):
        """P4 review: MIN_PAIRS alone let a 30%-success judge report PASS.

        Ten rows scored by the owner; the judge returns verdict='ERROR' on seven
        (529s, JSON parse failures — judge.py returns score 0 on any exception).
        The three survivors happen to rank in agreement, rho = 1.000, and the
        machine-readable status — the thing a caller branches on — said
        'calibrated'. pairs/valid was computed, carried in the return dict, and
        never consulted. The three survivors are not a random sample either:
        whatever made the other seven fail selected them.
        """
        rows = [(f"S-2{i:02d}", "grounding_fidelity", str(score))
                for i, score in enumerate([1, 2, 3, 4, 5, 1, 2, 3, 4, 5])]
        calibration_tree(rows)
        agreeing = {"S-200": 1, "S-204": 5, "S-209": 5}

        def _flaky_judge(dimension, transcript, tool_calls_log):
            sid = transcript.split("q for ")[1].split("\n")[0]
            if sid in agreeing:
                return {"dimension": dimension, "verdict": "PASS",
                        "score": agreeing[sid], "reason": "ok"}
            return {"dimension": dimension, "verdict": "ERROR", "score": 0,
                    "reason": "529 overloaded"}

        result = cc.compute_correlation(_flaky_judge)

        assert result["pairs"] == 3 >= cc.MIN_PAIRS, (
            "the pair floor is satisfied — that is the point"
        )
        assert result["valid"] == 10
        assert result["pair_rate"] == pytest.approx(0.3)
        assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET
        assert result["rho"] is None, (
            "a caller must not be able to read a number out of a run that "
            "measured three tenths of the set"
        )
        assert any("30%" in e for e in result["errors"])

    def test_the_pair_rate_floor_is_a_floor_and_not_a_ban(self, calibration_tree):
        """One judge error out of ten is 90% — above the floor, still a PASS.

        Without this the test above would pass for the wrong reason: a gate that
        rejected every run with a single error would make the instrument
        useless rather than honest.
        """
        rows = [(f"S-3{i:02d}", "grounding_fidelity", str(score))
                for i, score in enumerate([1, 2, 3, 4, 5, 1, 2, 3, 4, 5])]
        calibration_tree(rows)
        scores = dict(zip([f"S-3{i:02d}" for i in range(10)],
                          [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]))

        def _one_error_judge(dimension, transcript, tool_calls_log):
            sid = transcript.split("q for ")[1].split("\n")[0]
            if sid == "S-303":
                return {"dimension": dimension, "verdict": "ERROR", "score": 0,
                        "reason": "529 overloaded"}
            # The verdict follows the score. BACKLOG 8.2b: this stub used to
            # hardcode "PASS", and under Spearman that still reported rho = 1.0
            # and CALIBRATED, because a judge that passes everything ranks in
            # perfect agreement with any human whose scores happen to rise. That
            # is the defect kappa exists to catch, so the stub can no longer be
            # written that way without failing a different test in this module.
            return {"dimension": dimension,
                    "verdict": "PASS" if scores[sid] >= 3 else "FAIL",
                    "score": scores[sid], "reason": "ok"}

        result = cc.compute_correlation(_one_error_judge)

        assert result["pairs"] == 9
        assert result["pair_rate"] == pytest.approx(0.9)
        assert result["pair_rate"] >= cc.MIN_PAIR_RATE
        assert result["status"] == cc.STATUS_CALIBRATED
        assert result["kappa"] == pytest.approx(1.0), "the gate"
        assert result["rho"] == pytest.approx(1.0), "still reported beside it"

    def test_a_missing_csv_is_a_setup_error_not_a_pass(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc, "HUMAN_SCORES_CSV", tmp_path / "nope.csv")

        result = cc.compute_correlation(_judge_returning({}))

        assert result["status"] == cc.STATUS_SETUP_ERROR
        assert cc.EXIT_CODE_FOR_STATUS[result["status"]] == cc.EXIT_SETUP_ERROR
        assert result["rho"] is None

    def test_a_missing_response_file_never_becomes_a_pair(self, calibration_tree):
        """capture_responses.py has never been run against a live agent.

        A row whose recorded response is absent is an error, not a zero and not
        a skip that quietly shrinks the denominator without saying so.
        """
        calibration_tree(_FOUR_ROWS, capture={"S-101", "S-102"})

        result = cc.compute_correlation(
            _judge_returning({"S-101": 1, "S-102": 2, "S-103": 4, "S-104": 5})
        )

        assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET
        assert result["pairs"] == 2
        assert len(result["errors"]) == 2
        assert all("No recorded response" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# ...and a calibrated run still passes, and an uncalibrated one still fails
# ---------------------------------------------------------------------------


class TestCalibratedAndUncalibrated:
    """The fix must not have turned the instrument into a permanent 'unknown'."""

    def test_a_judge_that_agrees_with_the_human_is_calibrated(self, calibration_tree):
        calibration_tree(_FOUR_ROWS)

        result = cc.compute_correlation(
            _judge_returning({"S-101": 1, "S-102": 2, "S-103": 4, "S-104": 5})
        )

        assert result["status"] == cc.STATUS_CALIBRATED
        assert result["rho"] == pytest.approx(1.0)
        assert result["pairs"] == 4
        assert cc.EXIT_CODE_FOR_STATUS[result["status"]] == cc.EXIT_CALIBRATED

    def test_a_judge_that_ranks_backwards_is_not_calibrated(self, calibration_tree):
        calibration_tree(_FOUR_ROWS)

        result = cc.compute_correlation(
            _judge_returning({"S-101": 5, "S-102": 4, "S-103": 2, "S-104": 1})
        )

        assert result["status"] == cc.STATUS_NOT_CALIBRATED
        assert result["rho"] == pytest.approx(-1.0)
        assert cc.EXIT_CODE_FOR_STATUS[result["status"]] == cc.EXIT_NOT_CALIBRATED

    def test_the_gate_is_kappa_and_spearman_is_only_reported(self, calibration_tree):
        """BACKLOG 8.2b, owner decision 2026-08-18. Both numbers, one gate.

        The judge here ranks within each half differently from the human but
        agrees on every pass/fail call. Spearman sees the rank disagreement and
        drops to 0.6; kappa sees perfect agreement on the question that decides
        anything. The gate follows kappa, and rho is still printed beside it so
        the AI-SPEC number is not silently abandoned.
        """
        calibration_tree(_FOUR_ROWS)
        # human 1,2,4,5 -> fail,fail,pass,pass ; judge 2,1,5,4 -> same verdicts
        result = cc.compute_correlation(
            _judge_returning({"S-101": 2, "S-102": 1, "S-103": 5, "S-104": 4})
        )

        assert cc.THRESHOLD == 0.75, "AI-SPEC 5.2's number, still reported"
        assert 0 < result["rho"] < cc.THRESHOLD, "the ranks disagree"
        assert result["kappa"] == pytest.approx(1.0), "the verdicts do not"
        assert result["status"] == cc.STATUS_CALIBRATED

    def test_a_judge_whose_interval_includes_zero_is_not_calibrated(self, calibration_tree):
        """BACKLOG 8.2c replaced the 0.6 floor this test used to assert.

        Four of six rows agree, so the point estimate is 0.25 - which under the
        old gate meant "below 0.6" and under no gate at all would have read as
        "some agreement". Six rows cannot support either reading: the interval
        straddles zero, so this corpus does not show the judge doing better than
        chance, and that is a MEASUREMENT and therefore a failure rather than an
        absence.
        """
        rows = [(f"S-4{i:02d}", "grounding_fidelity", s)
                for i, s in enumerate(["5", "5", "5", "5", "1", "1"])]
        calibration_tree(rows)
        # Four of six agree; the judge flips one pass and one fail.
        result = cc.compute_correlation(
            _judge_returning({"S-400": 5, "S-401": 5, "S-402": 5, "S-403": 1,
                              "S-404": 5, "S-405": 1})
        )

        assert result["kappa"] == pytest.approx(0.25, abs=0.01), "the point estimate"
        assert result["judge_interval"]["low"] <= 0, "and six rows cannot rule out chance"
        assert result["gate"]["beats_chance"] is False
        assert result["status"] == cc.STATUS_NOT_CALIBRATED

    def test_a_judge_that_passes_everything_is_refused(self, calibration_tree):
        """The defect the old gate could not see, and the reason it moved.

        A judge that returns PASS to every input ranks in perfect agreement with
        any human whose scores happen to rise, so Spearman reported rho = 1.0 and
        'safe to trust automated results' over a judge that is not reading the
        response at all. Kappa subtracts the chance rate and refuses it.
        """
        calibration_tree(_FOUR_ROWS)
        result = cc.compute_correlation(
            _judge_returning({"S-101": 3, "S-102": 4, "S-103": 4, "S-104": 5})
        )

        assert result["cells"]["judge_too_lenient"] == 2, "it passed two the human failed"
        assert result["status"] != cc.STATUS_CALIBRATED, (
            "a judge that passes everything must never be reported as calibrated"
        )


# ---------------------------------------------------------------------------
# The human_score column is the owner's
# ---------------------------------------------------------------------------


class TestTheHumanColumnIsNeverWritten:
    """An agent-filled calibration set destroys the instrument silently.

    A judge calibrated against model-written labels measures its agreement with
    a model. It would report a high rho and mean nothing, and nobody downstream
    could tell — which is strictly worse than the current honest 'uncalibrated'.
    """

    def test_reading_the_calibration_set_never_modifies_it(self):
        """Runtime proof over the SHIPPED file: bytes in, bytes out.

        Deliberately not an assertion about what is IN the column — filling it
        is the owner's step and a test that went red when they did it would be
        this discipline sabotaging the very person it exists for.
        """
        before = cc.HUMAN_SCORES_CSV.read_bytes()

        cc.read_human_score_rows()
        cc.load_human_scores()
        cc.readiness()

        assert cc.HUMAN_SCORES_CSV.read_bytes() == before

    def test_no_module_in_the_repo_opens_the_calibration_set_for_writing(self):
        """Source-level proof, closing the class rather than the one instance.

        A future 'seed the calibration set' helper anywhere under apps/api would
        trip this, not just a regression inside compute_correlation.py.
        """
        api_root = pathlib.Path(cc.__file__).parents[3]
        assert (api_root / "app").is_dir(), f"unexpected repo layout at {api_root}"

        referencing: list[pathlib.Path] = []
        for path in api_root.rglob("*.py"):
            if ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "human_scores" in text:
                referencing.append(path)

        # Only the harness and this test may name the file at all.
        names = sorted(p.name for p in referencing)
        assert names == ["compute_correlation.py", "test_calibration_harness.py"], (
            f"a new module references human_scores.csv: {names}"
        )

        source = (api_root / "tests/evals/calibration/compute_correlation.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ('HUMAN_SCORES_CSV.write', 'HUMAN_SCORES_CSV.open("w"',
                          "HUMAN_SCORES_CSV.open('w'", 'csv.writer', 'csv.DictWriter'):
            assert forbidden not in source, (
                f"the calibration harness writes the owner's column: {forbidden}"
            )


# ---------------------------------------------------------------------------
# Readiness — every input except the owner's
# ---------------------------------------------------------------------------


class TestReadiness:
    """`--check` answers 'what is stopping anyone from finding out'."""

    def test_the_shipped_tree_is_honest_about_being_unready(self, capsys):
        """The tree is unready, and the report must SAY WHY rather than just say no.

        RED FROM 2026-08-17 TO 2026-08-18, undetected. It asserted `blocking` was
        non-empty, which was true only while `responses/` had never been captured.
        The E2E-6 capture filled that directory, every machine-fixable input became
        present, and `blocking` correctly emptied — leaving the assertion pinned to
        a state the project had moved past. The last full battery ran 2026-08-16,
        the day before, and `fast` is collect-only, so nothing ran this until now.

        The rewrite keeps the intent and stops pinning one spelling of it: whatever
        is missing, the report must name it, and the caller must not read exit 0.
        """
        report = cc.readiness()

        assert report["scenarios_present"] == 20
        assert report["human_scores_attempted"] >= 10
        assert report["ready_to_calibrate"] is False

        named = report["blocking"] or report["awaiting_owner_scores"]
        assert named, "an unready harness must name what is missing"

        assert cc.print_readiness(report) == cc.EXIT_NOT_CALIBRATED_YET
        printed = capsys.readouterr().out
        assert "Awaiting the owner" in printed or "Blocking" in printed, (
            "the exit code alone does not tell a reader what to do next"
        )

    def test_readiness_reports_valid_beside_attempted(self, calibration_tree):
        calibration_tree([
            ("S-101", "grounding_fidelity", "4"),
            ("S-102", "grounding_fidelity", ""),
            ("S-103", "grounding_fidelity", "9"),
            ("S-104", "grounding_fidelity", "not-a-number"),
        ])

        report = cc.readiness()

        assert report["human_scores_attempted"] == 4
        assert report["human_scores_valid"] == 1
        assert len(report["human_scores_unusable"]) == 3, (
            "every rejected row is named with its reason, never silently dropped"
        )
        assert any("outside 1-5" in u for u in report["human_scores_unusable"])
        assert any("non-integer" in u for u in report["human_scores_unusable"])

    def test_readiness_names_the_missing_ceiling(self, calibration_tree, capsys):
        """BACKLOG 8.2c. The ceiling is an OWNER input, like the verdicts are.

        Reported here rather than discovered after a paid judge run whose
        numbers nobody may read. Observed by mutation 2026-08-18: dropping
        `awaiting_ceiling` from `awaiting_owner` left every other test green,
        so --check would have said READY over a tree that cannot produce a
        readable result.
        """
        calibration_tree(_FOUR_ROWS, second_pass=None)

        report = cc.readiness()

        assert report["blocking"] == [], "nothing machine-fixable is missing"
        assert report["second_pass_valid"] == 0
        assert len(report["rows_without_second_verdict"]) == 4
        assert report["awaiting_ceiling"] is True
        assert report["awaiting_owner_scores"] is True
        assert report["ready_to_calibrate"] is False

        assert cc.print_readiness(report) == cc.EXIT_NOT_CALIBRATED_YET
        assert "HUMAN CEILING" in capsys.readouterr().out, (
            "the exit code alone does not tell the owner which sheet is missing"
        )

    def test_an_unlabelled_sheet_names_BOTH_missing_owner_inputs(
        self, calibration_tree, capsys
    ):
        """The state the shipped tree is actually in, and both asks are the owner's.

        Naming only the nearer one produces a second evening of work nobody
        planned for: the owner labels the sheet, comes back, and is told there
        is a whole second pass to do.
        """
        calibration_tree([(sid, dim, "") for sid, dim, _ in _FOUR_ROWS], second_pass=None)

        cc.print_readiness(cc.readiness())
        printed = capsys.readouterr().out

        assert "human verdicts needed are filled in" in printed
        assert "HUMAN CEILING" in printed

    def test_readiness_flags_a_dimension_the_judge_does_not_have(self, calibration_tree):
        """A typo in an unscored row is worth catching before the owner spends an
        evening grading against it — the judge would return ERROR on every one."""
        calibration_tree([("S-101", "grounding_fidelty", "")])

        report = cc.readiness()

        assert report["unknown_dimensions"] == ["grounding_fidelty"]
        assert any("unknown judge dimension" in b for b in report["blocking"])

    def test_readiness_flags_a_scenario_that_does_not_exist(self, calibration_tree, tmp_path):
        calibration_tree([("S-101", "grounding_fidelity", "")])
        # Reference a scenario with no file on disk.
        _write_csv(cc.HUMAN_SCORES_CSV, [("S-999", "grounding_fidelity", "")])

        report = cc.readiness()

        assert report["unknown_scenarios"] == ["S-999"]
        assert report["responses_missing"] == ["S-999"]
        assert report["ready_to_calibrate"] is False

    def test_readiness_calls_no_judge(self, calibration_tree, monkeypatch):
        """Safe to run on a machine with no services and no API key."""
        import tests.evals.judge as judge_mod

        called = []
        monkeypatch.setattr(judge_mod, "judge", lambda *a, **k: called.append(a))

        calibration_tree(_FOUR_ROWS)
        report = cc.readiness()

        assert called == []
        assert report["ready_to_calibrate"] is True, (
            "a complete tmp tree with four scored rows IS ready — otherwise this "
            "test would pass for the wrong reason"
        )

    def test_a_deflected_response_is_named_and_does_not_count_as_scorable(
        self, calibration_tree, tmp_path
    ):
        """BACKLOG 7.29: the corpus check that ran could not see a deflection.

        It looked for empties, short answers and provider-error text. The PII
        deflection is a well-formed sentence of ordinary length, so four of the
        twenty E2E-6 responses passed as clean and were only found while someone
        read them. Scoring one measures the firewall rather than the judge.
        """
        from app.utils.pii_firewall import PII_DEFLECTION

        calibration_tree(_FOUR_ROWS)
        (tmp_path / "responses" / "S-101.json").write_text(
            json.dumps({
                "scenario_id": "S-101",
                "response_text": PII_DEFLECTION,
                "tool_calls_log": [],
            }),
            encoding="utf-8",
        )
        report = cc.readiness()

        assert report["deflected_responses"] == ["S-101"]
        assert report["scorable_rows"] == 3
        assert report["blocking"] == [], (
            "three scorable rows still meet the minimum, so naming the deflection "
            "must not stop the owner scoring the rest"
        )

    def test_deflections_below_the_minimum_block(self, calibration_tree, tmp_path):
        from app.utils.pii_firewall import PII_DEFLECTION

        calibration_tree(_FOUR_ROWS)
        for sid in ("S-101", "S-102"):
            (tmp_path / "responses" / f"{sid}.json").write_text(
                json.dumps({
                    "scenario_id": sid,
                    "response_text": PII_DEFLECTION,
                    "tool_calls_log": [],
                }),
                encoding="utf-8",
            )
        report = cc.readiness()

        assert report["deflected_responses"] == ["S-101", "S-102"]
        assert report["scorable_rows"] == 2
        assert any("PII deflection" in b for b in report["blocking"])
        assert report["ready_to_calibrate"] is False

    def test_a_scenario_scored_on_two_dimensions_is_named_once(
        self, calibration_tree, tmp_path
    ):
        """`scorable_rows` counts rows; `deflected_responses` names scenarios."""
        from app.utils.pii_firewall import PII_DEFLECTION

        calibration_tree([
            ("S-101", "grounding_fidelity", "4"),
            ("S-101", "knowledge_gap_honesty", "4"),
            ("S-102", "grounding_fidelity", "2"),
            ("S-103", "grounding_fidelity", "5"),
        ])
        (tmp_path / "responses" / "S-101.json").write_text(
            json.dumps({
                "scenario_id": "S-101",
                "response_text": PII_DEFLECTION,
                "tool_calls_log": [],
            }),
            encoding="utf-8",
        )
        report = cc.readiness()

        assert report["deflected_responses"] == ["S-101"]
        assert report["scorable_rows"] == 2, (
            "two rows referenced S-101, so both are unscorable"
        )

    def test_an_ordinary_answer_is_never_called_a_deflection(
        self, calibration_tree, tmp_path
    ):
        """The control: a real answer that mentions contacting the team is scorable."""
        calibration_tree(_FOUR_ROWS)
        (tmp_path / "responses" / "S-101.json").write_text(
            json.dumps({
                "scenario_id": "S-101",
                "response_text": (
                    "For anything involving your order, please contact our team "
                    "directly at hello@acmecoffee.example and we'll help you."
                ),
                "tool_calls_log": [],
            }),
            encoding="utf-8",
        )
        report = cc.readiness()

        assert report["deflected_responses"] == []
        assert report["scorable_rows"] == 4

    def test_a_ready_tree_says_ready_and_does_not_say_calibrated(self, calibration_tree):
        """P4 review: this pinned `== EXIT_CALIBRATED`, i.e. exit 0.

        That is audit D7's own defect — "exit 0 is success to every reader" —
        reintroduced on the new code path and then guarded by a test. --check
        makes zero judge calls by design, so a shell, Makefile or CI step keyed
        on the documented exit code (`0 == calibrated`) would have recorded the
        judge as calibrated while rho had never been computed and could have
        been -1.0. Ready is a statement about the inputs, never about the judge.
        """
        calibration_tree(_FOUR_ROWS)
        report = cc.readiness()

        assert report["blocking"] == []
        assert report["awaiting_owner_scores"] is False
        assert report["ready_to_calibrate"] is True
        assert cc.print_readiness(report) == cc.EXIT_READY_TO_CALIBRATE
        assert cc.EXIT_READY_TO_CALIBRATE != cc.EXIT_CALIBRATED

    def test_no_check_outcome_can_report_the_calibrated_exit_code(self, calibration_tree):
        """Both branches of --check, closing the class rather than one instance."""
        calibration_tree(_FOUR_ROWS, capture={"S-101"})  # not ready
        assert cc.main(["--check"]) != cc.EXIT_CALIBRATED

        calibration_tree(_FOUR_ROWS)  # ready
        assert cc.main(["--check"]) != cc.EXIT_CALIBRATED

        assert cc.EXIT_READY_TO_CALIBRATE not in cc.EXIT_CODE_FOR_STATUS.values(), (
            "a readiness answer must not share an exit code with a run that "
            "actually computed a correlation"
        )

    def test_main_check_returns_the_readiness_code(self, calibration_tree):
        calibration_tree([(sid, dim, "") for sid, dim, _ in _FOUR_ROWS])

        assert cc.main(["--check"]) == cc.EXIT_NOT_CALIBRATED_YET


# ---------------------------------------------------------------------------
# BACKLOG 8.1 — calibration reads run 0, because run 0 is the row the human scored
# ---------------------------------------------------------------------------


class TestCalibrationReadsRunZero:
    """A record holds k runs now, and only one of them carries a human label.

    The owner scores one row per (scenario, dimension). Scoring every run would
    multiply the only human step in the system by k, so the sequence is: capture
    once at k > 1, the human labels run 0, the judge is calibrated against those
    labels here, and the calibrated judge scores the rest for reliable@k.

    Reading any other run makes the correlation a comparison between a judge and
    a human who never saw that text.
    """

    def _multi_run(self, tmp_path, sid, texts):
        (tmp_path / "responses" / f"{sid}.json").write_text(
            json.dumps({
                "scenario_id": sid,
                "runs": [{"response_text": t, "tool_calls_log": []} for t in texts],
            }),
            encoding="utf-8",
        )

    def test_the_judge_is_shown_run_zero_not_the_last_run(self, calibration_tree, tmp_path):
        calibration_tree(_FOUR_ROWS)
        self._multi_run(tmp_path, "S-101", ["a for S-101", "SECOND RUN", "THIRD RUN"])

        seen: list[str] = []

        def _recording_judge(dimension, transcript, tool_calls_log):
            seen.append(transcript)
            return {"dimension": dimension, "verdict": "PASS", "score": 3, "reason": "r"}

        cc.compute_correlation(_recording_judge)

        joined = "\n".join(seen)
        assert "a for S-101" in joined, "run 0 is the row the human labelled"
        assert "SECOND RUN" not in joined and "THIRD RUN" not in joined, (
            "a later run would be correlated against a human score for text nobody saw"
        )

    def test_a_deflection_in_a_later_run_does_not_block_the_labelled_row(
        self, calibration_tree, tmp_path
    ):
        """Run 0 is scorable, so calibration proceeds.

        A deflection in run 2 is a real finding and validate_corpus.py reports
        it. It is not a reason to withhold the row the human can actually score.
        """
        from app.utils.pii_firewall import PII_DEFLECTION

        calibration_tree(_FOUR_ROWS)
        self._multi_run(tmp_path, "S-101", ["a for S-101", PII_DEFLECTION])

        assert cc.readiness()["deflected_responses"] == []

    def test_a_deflected_run_zero_is_still_caught(self, calibration_tree, tmp_path):
        from app.utils.pii_firewall import PII_DEFLECTION

        calibration_tree(_FOUR_ROWS)
        self._multi_run(tmp_path, "S-101", [PII_DEFLECTION, "a for S-101"])

        assert cc.readiness()["deflected_responses"] == ["S-101"]

    def test_a_pre_8_1_record_still_calibrates(self, calibration_tree):
        """The files on disk today are single-run and must keep working."""
        calibration_tree(_FOUR_ROWS)
        result = cc.compute_correlation(
            _judge_returning({"S-101": 1, "S-102": 2, "S-103": 4, "S-104": 5})
        )
        assert result["status"] == cc.STATUS_CALIBRATED
        assert result["pairs"] == 4


# ---------------------------------------------------------------------------
# Adversarial review 2026-08-18. The BLOCK findings and the gap that hid them.
# ---------------------------------------------------------------------------


class TestTheShapeTheOwnerIsActuallyAskedFor:
    """A verdict and NO score. The harness prints "a 1-5 score is optional".

    F1 was a TypeError in `main()` on exactly this row: the table formatter did
    `judge_score - human_score` and `f"{human_score:>6}"` while 8.2b had made
    `human_score` optional. The gate computed kappa correctly and the CLI died on
    the way to printing it, so a shell read the non-zero exit as NOT CALIBRATED
    over a judge that was.
    """

    def test_a_verdict_without_a_score_is_a_usable_row(self, calibration_tree):
        calibration_tree(_FOUR_ROWS, with_scores=False)
        parsed = cc.read_human_score_rows()

        assert parsed["valid"] == 4
        assert all(row["human_score"] is None for row in parsed["rows"])
        assert all(row["human_verdict"] in (cc.VERDICT_PASS, cc.VERDICT_FAIL)
                   for row in parsed["rows"])

    def test_the_gate_still_decides_without_any_scores(self, calibration_tree):
        calibration_tree(_FOUR_ROWS, with_scores=False)
        result = cc.compute_correlation(
            _judge_returning({"S-101": 1, "S-102": 2, "S-103": 4, "S-104": 5})
        )
        assert result["status"] == cc.STATUS_CALIBRATED
        assert result["kappa"] == pytest.approx(1.0)
        assert result["rho"] is None, "Spearman needs scores; the gate does not"

    def test_main_does_not_crash_on_a_verdict_only_sheet(self, calibration_tree, monkeypatch, capsys):
        """The regression test for F1, driving the CLI the way a person runs it."""
        calibration_tree(_FOUR_ROWS, with_scores=False)
        import tests.evals.judge as judge_module

        monkeypatch.setattr(
            judge_module, "judge",
            _judge_returning({"S-101": 1, "S-102": 2, "S-103": 4, "S-104": 5}),
        )

        exit_code = cc.main([])

        out = capsys.readouterr().out
        assert exit_code == cc.EXIT_CALIBRATED, out
        assert "Cohen's kappa" in out, "the gate's number must reach the reader"
        assert "human PASS" in out, "the matrix is the report card"

    def test_a_verdict_and_a_score_that_disagree_are_both_kept(self, calibration_tree, tmp_path):
        """The human is allowed to contradict themself, and nothing silently fixes it.

        The fixtures derived the verdict FROM the score, so a `fail` beside a `5`
        was inexpressible. The gate reads the verdict; Spearman reads the score.
        """
        (tmp_path / "human_scores.csv").write_text(
            "scenario_id,dimension,human_verdict,human_score,notes\n"
            "S-101,grounding_fidelity,fail,5,contradicts on purpose\n",
            encoding="utf-8",
        )
        rows = cc.read_human_score_rows(tmp_path / "human_scores.csv")["rows"]
        assert rows[0]["human_verdict"] == cc.VERDICT_FAIL
        assert rows[0]["human_passed"] is False
        assert rows[0]["human_score"] == 5


class TestASpreadsheetRoundTripDoesNotReadAsUnlabelled:
    """F2 and F3. Both produced "nobody has labelled anything" over a full sheet."""

    def test_an_excel_utf8_bom_does_not_empty_every_scenario_id(self, tmp_path):
        """Excel's "CSV UTF-8" writes a BOM. The first header became \ufeffscenario_id,
        so every row parsed as VALID with an empty scenario_id and readiness
        reported READY over a file that produced zero pairs.
        """
        path = tmp_path / "human_scores.csv"
        path.write_bytes(
            b"\xef\xbb\xbf"
            b"scenario_id,dimension,human_verdict,human_score,notes\n"
            b"S-101,grounding_fidelity,pass,,note\n"
        )
        rows = cc.read_human_score_rows(path)["rows"]
        assert rows[0]["scenario_id"] == "S-101", "the BOM must not become part of the header"

    @pytest.mark.parametrize("header", ["Human_Verdict", "verdict", "human verdict"])
    def test_a_renamed_or_padded_gate_column_is_reported_as_a_header_problem(
        self, tmp_path, header
    ):
        """Not as ten unlabelled rows. They need different actions from the owner."""
        path = tmp_path / "human_scores.csv"
        path.write_text(
            f"scenario_id,dimension,{header},human_score,notes\n"
            "S-101,grounding_fidelity,pass,,note\n",
            encoding="utf-8",
        )
        parsed = cc.read_human_score_rows(path)

        assert parsed["valid"] == 0
        assert any("no `human_verdict` column" in u for u in parsed["unusable"]), parsed["unusable"]
        assert not any("not filled in yet" in u for u in parsed["unusable"]), (
            "a missing column must not read as an unlabelled sheet"
        )

    def test_a_padded_header_that_strips_to_the_right_name_is_still_detected(self, tmp_path):
        """` human_verdict` strips to the right name, so the column IS present."""
        path = tmp_path / "human_scores.csv"
        path.write_text(
            "scenario_id,dimension, human_verdict,human_score,notes\n"
            "S-101,grounding_fidelity,pass,,note\n",
            encoding="utf-8",
        )
        parsed = cc.read_human_score_rows(path)
        assert not any("no `human_verdict` column" in u for u in parsed["unusable"])


class TestADeflectionNeverEntersTheGate:
    """F11. `deflected_response_ids` was advisory: readiness called it, the gate did not."""

    def test_a_labelled_deflection_is_excluded_from_the_matrix(self, calibration_tree, tmp_path):
        from app.utils.pii_firewall import PII_DEFLECTION

        calibration_tree(_FOUR_ROWS)
        (tmp_path / "responses" / "S-101.json").write_text(
            json.dumps({"scenario_id": "S-101", "response_text": PII_DEFLECTION,
                        "tool_calls_log": []}),
            encoding="utf-8",
        )

        result = cc.compute_correlation(
            _judge_returning({"S-101": 1, "S-102": 2, "S-103": 4, "S-104": 5})
        )

        assert sum(result["cells"].values()) == 3, (
            "grading a deflection measures the firewall, so it may not decide the gate"
        )
        assert any("deflection" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# BACKLOG 8.2c — the threshold is derived from the labels, and there is none
# ---------------------------------------------------------------------------


#: 20 rows, balanced 10 pass / 10 fail. Twenty is the smallest size used in this
#: file at which a judge can be WRONG about several rows and still have its
#: interval clear zero — which is the only configuration where half (b) can be
#: the half that decides anything. At six rows every imperfect judge already
#: fails (a), so (b) would never be reached and could not be tested.
_TWENTY_BALANCED = [
    (f"S-5{i:02d}", "grounding_fidelity", "5" if i < 10 else "1")
    for i in range(20)
]


def _judge_wrong_about(n: int):
    """Agrees with the human on every row of _TWENTY_BALANCED except `n` of them.

    The flips are split across both labels so the judge's marginals stay near
    the human's. A judge that only ever errs one way moves the chance rate as
    well as the agreement rate, and then kappa is measuring two things at once.
    """
    wrong = {f"S-5{i:02d}" for i in range(n // 2)}
    wrong |= {f"S-5{10 + i:02d}" for i in range(n - n // 2)}
    scores = {}
    for i, (sid, _dim, _score) in enumerate(_TWENTY_BALANCED):
        judge_passes = (i < 10) != (sid in wrong)
        scores[sid] = 5 if judge_passes else 1
    return _judge_returning(scores)


class TestTheCeilingIsMeasuredNotChosen:
    """The gate is two intervals from these labels, and no constant at all.

    `KAPPA_THRESHOLD = 0.6` was a Landis-Koch band boundary: a 1977 rule of
    thumb published with no empirical basis, so it was not merely unmeasured
    here, it was never measured anywhere. The owner refused it on 2026-08-18.

    What replaced it needs BOTH halves, and half (b) is why the second sheet
    exists: a judge cannot be expected to agree with a human more than that
    human agrees with THEMSELF, so the labeller's own test-retest kappa is the
    scale. Two tests below are the pair that proves the scale is real — the SAME
    judge passes against one labeller and fails against another, with nothing in
    the code different between them.
    """

    PERFECT = {"S-101": 1, "S-102": 2, "S-103": 4, "S-104": 5}

    def test_no_second_pass_is_never_a_pass(self, calibration_tree):
        """The judge is perfect. It is still not calibrated, and that is right."""
        calibration_tree(_FOUR_ROWS, second_pass=None)

        result = cc.compute_correlation(_judge_returning(self.PERFECT))

        assert result["kappa"] == pytest.approx(1.0), "the judge agreed on every row"
        assert result["gate"]["beats_chance"] is True
        assert result["gate"]["reaches_ceiling"] is None, "None, never False"
        assert result["ceiling_interval"] is None
        assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET, (
            "an unmeasured ceiling is an absence, not a judge failure"
        )
        assert any("HUMAN CEILING" in e for e in result["errors"])

    def test_a_partial_second_pass_is_refused_rather_than_used(self, calibration_tree):
        """Three of four rows re-labelled is not a ceiling over four rows.

        The finished subset is not a random sample of the sheet — it is whichever
        rows the labeller got to before stopping — and it would also be measured
        at a smaller n than the judge, which makes the comparison a statement
        about sample size rather than about agreement.
        """
        calibration_tree(_FOUR_ROWS, second_pass="partial")

        result = cc.compute_correlation(_judge_returning(self.PERFECT))

        assert result["ceiling_interval"] is None
        assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET
        assert any("no blind second verdict" in e for e in result["errors"])
        assert any("S-104" in e for e in result["errors"]), "the missing row is named"

    def test_a_judge_that_reaches_the_ceiling_is_calibrated(self, calibration_tree):
        calibration_tree(_FOUR_ROWS)

        result = cc.compute_correlation(_judge_returning(self.PERFECT))

        assert result["ceiling_interval"]["usable"] is True
        assert result["gate"]["beats_chance"] is True
        assert result["gate"]["reaches_ceiling"] is True
        assert result["status"] == cc.STATUS_CALIBRATED

    def test_a_judge_below_a_perfect_labeller_beats_chance_and_still_fails(
        self, calibration_tree
    ):
        """Half (b) deciding on its own, which half (a) alone cannot do.

        The judge is wrong about four of twenty rows: enough that its interval
        clears zero comfortably, and enough that it cannot reach the interval of
        a labeller who reproduced every one of their own verdicts.
        """
        calibration_tree(_TWENTY_BALANCED, second_pass="match")

        result = cc.compute_correlation(_judge_wrong_about(4))

        assert result["gate"]["beats_chance"] is True, "it is clearly not a coin"
        assert result["gate"]["reaches_ceiling"] is False
        assert result["status"] == cc.STATUS_NOT_CALIBRATED
        assert any("below the human" in e for e in result["errors"])

    def test_the_same_judge_passes_against_a_labeller_who_is_not_perfect(
        self, calibration_tree
    ):
        """The scale is the labeller, and this is the test that proves it.

        Identical judge, identical rows, identical code. The only thing that
        changed is that the human contradicted one of their own verdicts on the
        second pass — so the ceiling came down, and a judge that was
        distinguishably worse than a perfect labeller is not distinguishably
        worse than this one.

        If this test and the one above ever agree, the ceiling has stopped being
        read from the data.
        """
        calibration_tree(_TWENTY_BALANCED, second_pass="sloppy")

        result = cc.compute_correlation(_judge_wrong_about(4))

        assert result["ceiling_interval"]["usable"] is True
        assert result["ceiling_interval"]["point"] < 1.0, "the labeller contradicted a row"
        assert result["gate"]["reaches_ceiling"] is True
        assert result["status"] == cc.STATUS_CALIBRATED

    def test_a_one_sided_second_pass_is_no_ceiling_at_all(self, calibration_tree):
        """A human who labelled everything the same way has set no ceiling.

        Their self-agreement is undefined for the same reason a judge's would be:
        chance agreement is already certain. Reporting it as a LOW ceiling would
        let any judge through, which is the failure mode this whole file exists
        to prevent.
        """
        rows = [(f"S-6{i:02d}", "grounding_fidelity", "5") for i in range(6)]
        calibration_tree(rows)

        result = cc.compute_correlation(
            _judge_returning({f"S-6{i:02d}": (5 if i < 3 else 1) for i in range(6)})
        )

        assert result["status"] != cc.STATUS_CALIBRATED

    def test_the_module_holds_no_threshold_constant(self):
        """The property the row exists for, asserted rather than described.

        Re-adding the constant fails here even if nothing reads it yet, because
        a number sitting in this module is a number someone gates on later.
        """
        assert not hasattr(cc, "KAPPA_THRESHOLD")

        import inspect

        source = inspect.getsource(cc.compute_correlation)
        assert "0.6" not in source
        assert 'gate["calibrated"]' in source, (
            "the status must come from the two measured intervals and nothing else"
        )


class TestTheSecondPassSheetIsWrittenEmptyAndBlind:
    """`--emit-second-pass`. The one thing in this harness that writes a sheet.

    It writes the QUESTION, never an answer: scenario, dimension, and an empty
    verdict column. It carries no notes, because pass one's notes are the
    owner's own reasoning about the row and reading them back is reading back
    the answer.
    """

    def _emit(self, calibration_tree, rows=None):
        calibration_tree(rows or _FOUR_ROWS, second_pass=None)
        return cc.emit_second_pass()

    def test_it_writes_every_row_with_an_empty_verdict(self, calibration_tree):
        code, _messages = self._emit(calibration_tree)

        assert code == cc.EXIT_SECOND_PASS_EMITTED
        text = cc.HUMAN_SCORES_PASS2_CSV.read_text(encoding="utf-8")
        header, *body = [line for line in text.splitlines() if line]

        assert header == "scenario_id,dimension,human_verdict"
        assert len(body) == 4
        assert all(line.endswith(",") for line in body), "no verdict may be pre-filled"
        assert "note" not in text, "pass one's notes would leak the first answer"
        assert cc.read_second_pass()["valid"] == 0, "and it reads as unlabelled"

    def test_it_never_shares_an_exit_code_with_a_measurement(self):
        assert cc.EXIT_SECOND_PASS_EMITTED != cc.EXIT_CALIBRATED
        assert cc.EXIT_SECOND_PASS_EMITTED not in cc.EXIT_CODE_FOR_STATUS.values()
        assert cc.EXIT_SECOND_PASS_EMITTED != cc.EXIT_READY_TO_CALIBRATE

    def test_it_refuses_to_overwrite_an_existing_sheet(self, calibration_tree):
        """The one file in this harness holding labels that cost an evening."""
        calibration_tree(_FOUR_ROWS, second_pass="match")
        before = cc.HUMAN_SCORES_PASS2_CSV.read_bytes()

        code, messages = cc.emit_second_pass()

        assert code == cc.EXIT_SETUP_ERROR
        assert cc.HUMAN_SCORES_PASS2_CSV.read_bytes() == before
        assert any("NOT overwritten" in m for m in messages)

    def test_it_refuses_while_the_first_pass_is_unfinished(self, calibration_tree):
        """Both sheets labelled in one sitting is one pass copied, not a retest."""
        rows = [("S-101", "grounding_fidelity", "5"),
                ("S-102", "grounding_fidelity", "")]
        calibration_tree(rows, second_pass=None)

        code, messages = cc.emit_second_pass()

        assert code == cc.EXIT_SETUP_ERROR
        assert not cc.HUMAN_SCORES_PASS2_CSV.exists()
        assert any("test-retest" in m for m in messages)

    def test_it_never_writes_the_first_sheet(self, calibration_tree):
        calibration_tree(_FOUR_ROWS, second_pass=None)
        before = cc.HUMAN_SCORES_CSV.read_bytes()

        cc.emit_second_pass()

        assert cc.HUMAN_SCORES_CSV.read_bytes() == before

    def test_the_rows_come_back_in_a_different_order(self, calibration_tree):
        """Shuffled, so the sheet cannot be re-labelled from muscle memory."""
        rows = [(f"S-7{i:02d}", "grounding_fidelity", "5" if i % 2 else "1")
                for i in range(10)]
        self._emit(calibration_tree, rows)

        emitted = [line.split(",")[0] for line in
                   cc.HUMAN_SCORES_PASS2_CSV.read_text(encoding="utf-8").splitlines()[1:]
                   if line]

        assert sorted(emitted) == sorted(sid for sid, _d, _s in rows), "same rows"
        assert emitted != [sid for sid, _d, _s in rows], "different order"

    def test_main_routes_the_flag(self, calibration_tree, capsys):
        calibration_tree(_FOUR_ROWS, second_pass=None)

        code = cc.main(["--emit-second-pass"])

        assert code == cc.EXIT_SECOND_PASS_EMITTED
        assert "WITHOUT opening the first sheet" in capsys.readouterr().out
