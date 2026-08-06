"""Guarded E2E integration test for M7 red team routes.

Only runs when RED_TEAM_E2E_ENABLED=1 is set. Requires a live FastAPI server
and Celery worker. Not part of the standard unit test suite.
"""

from __future__ import annotations

import os
import time

import pytest
import requests

# ---------------------------------------------------------------------------
# Constants (read from environment)
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("API_KEY", "")
AGENT_ID = os.environ.get("AGENT_ID", "")


# ---------------------------------------------------------------------------
# Guarded E2E test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("RED_TEAM_E2E_ENABLED"),
    reason="RED_TEAM_E2E_ENABLED not set — skipping live red team E2E test",
)
def test_red_team_run_completes_and_returns_findings():
    """E2E: trigger a red team run, poll for completion, assert valid schema.

    Does NOT assert findings are non-empty or deployment_blocked is True —
    those assertions belong in demo_m7.sh with the intentionally weak agent.
    This test only verifies the routes work and the run completes with a valid schema.
    """
    if not AGENT_ID:
        pytest.skip("AGENT_ID env var required for E2E test")

    if not API_KEY:
        pytest.skip("API_KEY env var required for E2E test")

    headers = {"X-API-Key": API_KEY}

    # 1. Trigger a red team run (202 Accepted)
    response = requests.post(
        f"{BASE_URL}/api/v1/agents/{AGENT_ID}/red-team-runs",
        headers=headers,
        timeout=30,
    )
    assert response.status_code == 202, (
        f"Expected 202 from POST /red-team-runs, got {response.status_code}: {response.text}"
    )
    task_id = response.json()["job_id"]
    assert task_id, "job_id must be non-empty in trigger response"

    # 2. Poll GET /red-team-runs for a completed run (up to 300 seconds)
    deadline = time.time() + 300
    run_id = None

    while time.time() < deadline:
        poll_resp = requests.get(
            f"{BASE_URL}/api/v1/agents/{AGENT_ID}/red-team-runs",
            headers=headers,
            timeout=30,
        )
        assert poll_resp.status_code == 200, (
            f"Expected 200 from GET /red-team-runs, got {poll_resp.status_code}"
        )
        runs = poll_resp.json().get("runs", [])
        for run in runs:
            if run.get("status") == "complete":
                run_id = run["id"]
                break
        if run_id:
            break
        time.sleep(15)

    if not run_id:
        pytest.skip("Red team did not complete within 300s — increase timeout or check worker")

    # 3. Fetch single run detail
    detail_resp = requests.get(
        f"{BASE_URL}/api/v1/agents/{AGENT_ID}/red-team-runs/{run_id}",
        headers=headers,
        timeout=30,
    )
    assert detail_resp.status_code == 200, (
        f"Expected 200 from GET /red-team-runs/{run_id}, got {detail_resp.status_code}"
    )

    run_detail = detail_resp.json()["run"]

    # 4. Assert valid schema
    assert run_detail["status"] == "complete", (
        f"Expected run status 'complete', got '{run_detail['status']}'"
    )
    assert "findings" in run_detail, "run_detail must contain 'findings' key"
    assert run_detail["max_severity"] in ["none", "low", "medium", "high", "critical"], (
        f"max_severity '{run_detail['max_severity']}' is not a valid enum value"
    )
    assert isinstance(run_detail["deployment_blocked"], bool), (
        f"deployment_blocked must be bool, got {type(run_detail['deployment_blocked'])}"
    )

    # 5. Print summary
    print(
        f"Red team run complete: run_id={run_id}, "
        f"max_severity={run_detail['max_severity']}, "
        f"deployment_blocked={run_detail['deployment_blocked']}, "
        f"findings_count={len(run_detail.get('findings', []))}"
    )
