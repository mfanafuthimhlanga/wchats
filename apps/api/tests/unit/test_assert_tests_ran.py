"""scripts/assert_tests_ran.py turns "nothing ran" into a red step (#102).

scripts/ is not a package, so this loads the script by path, the way
tests/unit/test_gates.py does.

The reports below are the shapes CI actually produced. The one that matters is
`ONE_SKIPPED`: it is the report behind the green check on run 33150722736, where
"Eval (deterministic checks)" reported success over one skipped test in 0.08s.
"""

import importlib.util
import pathlib

import pytest

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_PATH = API_DIR / "scripts" / "assert_tests_ran.py"


def load_script():
    spec = importlib.util.spec_from_file_location("assert_tests_ran_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assert_tests_ran = load_script()


ONE_SKIPPED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="1" tests="1" time="0.08">
<testcase classname="tests.evals.run_evals" name="test_deterministic_dimensions_d5_d6_d7">
<skipped type="pytest.skip" message="no scenario has a recorded response"/></testcase>
</testsuite></testsuites>
"""

ONE_PASSED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="0" tests="1" time="0.41">
<testcase classname="tests.evals.run_evals" name="test_deterministic_dimension_d7"/>
</testsuite></testsuites>
"""

ONE_PASSED_ONE_SKIPPED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="1" tests="2" time="0.44">
<testcase classname="tests.evals.run_evals" name="test_deterministic_dimension_d7"/>
<testcase classname="tests.evals.run_evals" name="test_deterministic_dimensions_d5_d6">
<skipped type="pytest.skip" message="no scenario has a recorded response"/></testcase>
</testsuite></testsuites>
"""

NOTHING_COLLECTED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="0" tests="0" time="0.01"/>
</testsuites>
"""


def write(tmp_path, xml: str) -> str:
    report = tmp_path / "junit.xml"
    report.write_text(xml, encoding="utf-8")
    return str(report)


@pytest.mark.parametrize(
    "xml, why",
    [
        (ONE_SKIPPED, "the report behind the green check on run 33150722736"),
        (NOTHING_COLLECTED, "a selector that matched no test at all"),
    ],
)
def test_a_run_that_asserted_nothing_fails(tmp_path, xml: str, why: str) -> None:
    assert assert_tests_ran.main(["assert_tests_ran.py", write(tmp_path, xml)]) == 1, why


@pytest.mark.parametrize("xml", [ONE_PASSED, ONE_PASSED_ONE_SKIPPED])
def test_one_real_assertion_is_enough_to_pass(tmp_path, xml: str) -> None:
    """A skip alongside a pass is honest reporting, not an empty run."""
    assert assert_tests_ran.main(["assert_tests_ran.py", write(tmp_path, xml)]) == 0


def test_a_missing_report_fails(tmp_path) -> None:
    """pytest wrote nothing, so nothing is known about what it ran."""
    assert assert_tests_ran.main(["assert_tests_ran.py", str(tmp_path / "absent.xml")]) == 1


def test_the_reason_names_the_counts(tmp_path, capsys) -> None:
    """A red step that does not say what it saw sends the reader back to the log."""
    assert_tests_ran.main(["assert_tests_ran.py", write(tmp_path, ONE_SKIPPED)])
    err = capsys.readouterr().err
    assert "NOTHING PASSED" in err
    assert "1 skipped" in err


# ---------------------------------------------------------------------------
# A report the parser cannot read
# ---------------------------------------------------------------------------

#: Three shapes a CI step really produces where the JUnit XML should be. Each one
#: used to exit 1 with a raw xml.etree.ElementTree.ParseError traceback, under a
#: docstring promising the script "names what it saw".
UNPARSEABLE = {
    "empty": "",
    "truncated": '<?xml version="1.0" encoding="utf-8"?>\n<testsuites><testsuite tests="1"',
    "not xml at all": "ERROR: file or directory not found: tests/evals/run_evals.py\n",
}


@pytest.mark.parametrize("label, text", sorted(UNPARSEABLE.items()))
def test_an_unreadable_report_fails_closed(tmp_path, label: str, text: str) -> None:
    """pytest wrote something, and nothing can be read from it."""
    assert assert_tests_ran.main(["assert_tests_ran.py", write(tmp_path, text)]) == 1, label


@pytest.mark.parametrize("label, text", sorted(UNPARSEABLE.items()))
def test_an_unreadable_report_says_what_was_there(tmp_path, capsys, label: str, text: str) -> None:
    """A traceback tells the reader the script broke, not the report."""
    assert_tests_ran.main(["assert_tests_ran.py", write(tmp_path, text)])
    err = capsys.readouterr().err

    assert "ParseError" not in err, f"{label} still reports the exception class"
    assert "Traceback" not in err
    assert "not parseable XML" in err
    # The size ON DISK, not of the source string. `write_text` translates the
    # newlines, so a two-line fixture is two bytes longer on Windows than on CI.
    on_disk = pathlib.Path(write(tmp_path, text)).stat().st_size
    assert f"{on_disk} bytes" in err, f"{label} does not say how much was in the file"
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first:
        assert first[:40] in err, f"{label} does not quote what was there instead"


# ---------------------------------------------------------------------------
# The derived count
# ---------------------------------------------------------------------------

#: One test that passed and then errored in teardown. pytest counts it once under
#: `tests` and once under `errors`, so the derivation subtracts it twice.
PASSED_THEN_ERRORED_IN_TEARDOWN = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1" time="0.12">
<testcase classname="tests.evals.run_evals" name="test_deterministic_dimension_d7">
<error message="failed on teardown">RuntimeError</error></testcase>
</testsuite></testsuites>
"""

#: The same double-subtraction taken one step further: a test that failed AND
#: errored in teardown drives the derived count below zero.
FAILED_AND_ERRORED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="1" skipped="0" tests="1" time="0.12">
<testcase classname="tests.evals.run_evals" name="test_deterministic_dimension_d7">
<failure message="assert 0">assert 0</failure>
<error message="failed on teardown">RuntimeError</error></testcase>
</testsuite></testsuites>
"""


@pytest.mark.parametrize(
    "tests, failures, errors, skipped, why",
    [
        (1, 0, 1, 0, "one test that passed and then errored in teardown"),
        (1, 1, 1, 0, "one test that failed and then errored in teardown"),
        (2, 2, 2, 2, "every count double-attributed"),
    ],
)
def test_the_derived_count_never_goes_negative(
    tests: int, failures: int, errors: int, skipped: int, why: str
) -> None:
    """A test can be counted under `tests` and under `errors` at the same time.

    The double subtraction undercounts, which is the safe direction: it turns a
    real run red and a reader investigates. The floor is about the message, since
    "-1 passed" reads as a broken script rather than a finding about the run.
    """
    assert assert_tests_ran.passed_count(tests, failures, errors, skipped) == 0, why


@pytest.mark.parametrize("xml", [PASSED_THEN_ERRORED_IN_TEARDOWN, FAILED_AND_ERRORED])
def test_a_teardown_error_is_not_a_pass(tmp_path, capsys, xml: str) -> None:
    """The same shape end to end, through the report a run actually writes."""
    assert assert_tests_ran.main(["assert_tests_ran.py", write(tmp_path, xml)]) == 1
    assert "NOTHING PASSED" in capsys.readouterr().err
