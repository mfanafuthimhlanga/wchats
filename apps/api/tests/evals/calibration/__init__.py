"""Judge calibration harness (AI-SPEC.md §5.2, audit D7).

A package only so `tests.evals.calibration.compute_correlation` is importable
from a unit test — the module is a CLI, not a test, and pytest does not collect
it.
"""
