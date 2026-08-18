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

    def build(rows, *, capture: set[str] | None = None, with_scores: bool = True):
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

    def test_a_judge_below_the_kappa_floor_is_not_calibrated(self, calibration_tree):
        """0.6 is the floor, and it is a choice rather than a measurement.

        The practice's bands: below 0.4 the judge is not tracking the human, 0.6
        to 0.8 substantial, above 0.8 strong. Nothing in this repo has produced a
        kappa yet, so there is no observed distribution to set the floor against.
        """
        rows = [(f"S-4{i:02d}", "grounding_fidelity", s)
                for i, s in enumerate(["5", "5", "5", "5", "1", "1"])]
        calibration_tree(rows)
        # Four of six agree; the judge flips one pass and one fail.
        result = cc.compute_correlation(
            _judge_returning({"S-400": 5, "S-401": 5, "S-402": 5, "S-403": 1,
                              "S-404": 5, "S-405": 1})
        )

        assert cc.KAPPA_THRESHOLD == 0.6
        assert 0 < result["kappa"] < cc.KAPPA_THRESHOLD
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
