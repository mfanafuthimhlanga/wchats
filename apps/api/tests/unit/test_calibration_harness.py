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


def _write_csv(path: pathlib.Path, rows: list[tuple[str, str, str]]) -> pathlib.Path:
    lines = ["scenario_id,dimension,human_score,notes"]
    lines += [f"{sid},{dim},{score},note" for sid, dim, score in rows]
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

    def build(rows, *, capture: set[str] | None = None):
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
        csv_path = _write_csv(tmp_path / "human_scores.csv", rows)
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

    def test_zero_variance_is_unknown_not_pass(self, calibration_tree):
        """Every human score identical means there is no ranking to correlate.

        spearman() returns nan; nan >= 0.75 is False, so the shipped code would
        have called this FAIL. It is neither: nothing was measured.
        """
        rows = [(sid, dim, "3") for sid, dim, _ in _FOUR_ROWS]
        calibration_tree(rows)

        result = cc.compute_correlation(
            _judge_returning({"S-101": 1, "S-102": 2, "S-103": 4, "S-104": 5})
        )

        assert result["status"] == cc.STATUS_NOT_CALIBRATED_YET
        assert result["rho"] is None
        assert any("undefined" in e for e in result["errors"])

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
            return {"dimension": dimension, "verdict": "PASS",
                    "score": scores[sid], "reason": "ok"}

        result = cc.compute_correlation(_one_error_judge)

        assert result["pairs"] == 9
        assert result["pair_rate"] == pytest.approx(0.9)
        assert result["pair_rate"] >= cc.MIN_PAIR_RATE
        assert result["status"] == cc.STATUS_CALIBRATED
        assert result["rho"] == pytest.approx(1.0)

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

    def test_the_threshold_is_the_one_ai_spec_names(self, calibration_tree):
        """A rho just under 0.75 must fail; the gate is 0.75, not 'positive'."""
        calibration_tree(_FOUR_ROWS)
        # human ranks 1,2,3,4 vs judge ranks 2,1,4,3 -> sum d^2 = 4 -> rho = 0.6,
        # a judge that agrees on the broad ordering and disagrees on every
        # adjacent pair. Positive, useless, and below the gate.
        result = cc.compute_correlation(
            _judge_returning({"S-101": 2, "S-102": 1, "S-103": 5, "S-104": 4})
        )

        assert cc.THRESHOLD == 0.75
        assert 0 < result["rho"] < cc.THRESHOLD
        assert result["status"] == cc.STATUS_NOT_CALIBRATED


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
