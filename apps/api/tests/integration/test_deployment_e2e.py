"""Guarded E2E integration test for M8 deployment checklist routes.

Only runs when DEP_E2E_ENABLED=1 is set. Requires a live FastAPI server
and Celery worker. Not part of the standard unit test suite.
"""

from __future__ import annotations

import os
import time

import pytest
import requests


# ---------------------------------------------------------------------------
# Guarded E2E test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("DEP_E2E_ENABLED"),
    reason="DEP_E2E_ENABLED not set — skipping live deployment checklist E2E test",
)
def test_deployment_checklist_completes():
    assert False, "E2E stub — replace in Plan 08-07"
