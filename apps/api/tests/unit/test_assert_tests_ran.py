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
