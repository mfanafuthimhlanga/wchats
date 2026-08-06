"""Guarded E2E integration test for M8 deployment checklist routes.

Only runs when DEP_E2E_ENABLED=1 is set. Requires a live FastAPI server
and Celery worker. Not part of the standard unit test suite.

Required env vars when DEP_E2E_ENABLED=1:
  E2E_AGENT_ID  — UUID of a pre-provisioned agent (test agent, not production)
  API_KEY       — X-API-Key for tenant auth

Optional env vars:
  BASE_URL      — FastAPI base URL (default: http://localhost:8000)
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
    """E2E: trigger checklist run, poll for completion, assert schema + approval flow.

    Covers DEP-07 (owner journey) and DEP-08 (is_deployed flip + iframe snippet).
    Uses X-API-Key auth (dual-auth fallback active from M4.1 — RESEARCH.md Pitfall 6).
    """
    BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
    API_KEY = os.environ.get("API_KEY", "")
    AGENT_ID = os.environ.get("E2E_AGENT_ID", "")

    if not AGENT_ID:
        pytest.skip("E2E_AGENT_ID env var required for E2E test — use a pre-provisioned test agent")
    if not API_KEY:
        pytest.skip("API_KEY env var required for E2E test")

    headers = {"X-API-Key": API_KEY}

    # 1. Trigger checklist run (202 Accepted)
    trigger_resp = requests.post(
        f"{BASE_URL}/api/v1/agents/{AGENT_ID}/checklist-runs",
        headers=headers,
        timeout=30,
    )
    assert trigger_resp.status_code == 202, (
        f"Expected 202 from POST /checklist-runs, got {trigger_resp.status_code}: {trigger_resp.text}"
    )
    checklist_run_id = trigger_resp.json().get("checklist_run_id")
    assert checklist_run_id, "checklist_run_id must be non-empty in trigger response"

    # 2. Poll GET /checklist-runs/{run_id} for completion (300s deadline, 3s interval)
    deadline = time.time() + 300
    complete_run = None

    while time.time() < deadline:
        poll_resp = requests.get(
            f"{BASE_URL}/api/v1/agents/{AGENT_ID}/checklist-runs/{checklist_run_id}",
            headers=headers,
            timeout=30,
        )
        assert poll_resp.status_code == 200, (
            f"Expected 200 from GET /checklist-runs/{checklist_run_id}, got {poll_resp.status_code}"
        )
        data = poll_resp.json()
        run = data.get("run", data)
        if run.get("status") in ("complete", "failed"):
            complete_run = run
            break
        time.sleep(3)

    assert complete_run is not None, "Checklist run did not complete within 300s"
    assert complete_run["status"] == "complete", (
        f"Run status: {complete_run['status']} — check Celery worker logs"
    )

    # 3. Assert schema
    assert complete_run["recommendation"] in ["ship", "ship_with_warnings", "block"], (
        f"Unexpected recommendation: {complete_run['recommendation']}"
    )
    assert isinstance(complete_run.get("warnings", []), list), "warnings must be a list"
    assert isinstance(complete_run.get("all_warnings_acknowledged", False), bool), (
        "all_warnings_acknowledged must be bool"
    )

    # 4. If approvable, test full approval flow
    if complete_run["recommendation"] == "block":
        # block is a valid outcome — skip approval assertions
        return

    # 4a. Acknowledge warnings if ship_with_warnings
    if complete_run["recommendation"] == "ship_with_warnings":
        warning_ids = [w["warning_id"] for w in complete_run.get("warnings", []) if isinstance(w, dict)]
        if warning_ids:
            ack_resp = requests.post(
                f"{BASE_URL}/api/v1/agents/{AGENT_ID}/checklist-runs/{complete_run['id']}/acknowledge",
                json={"warning_ids": warning_ids},
                headers=headers,
                timeout=30,
            )
            assert ack_resp.status_code == 200, (
                f"Expected 200 from POST /acknowledge, got {ack_resp.status_code}: {ack_resp.text}"
            )

    # 4b. Approve deployment
    approve_resp = requests.post(
        f"{BASE_URL}/api/v1/agents/{AGENT_ID}/approve-deployment",
        json={"checklist_run_id": complete_run["id"]},
        headers=headers,
        timeout=30,
    )
    assert approve_resp.status_code == 200, (
        f"Expected 200 from POST /approve-deployment, got {approve_resp.status_code}: {approve_resp.text}"
    )
    data = approve_resp.json()
    assert data["deployed"] is True, "deployed must be True after approval"
    assert "iframe_snippet" in data, "iframe_snippet must be present in approval response"
    assert "widget.wchats.app" in data["iframe_snippet"], (
        f"iframe_snippet must contain 'widget.wchats.app', got: {data['iframe_snippet']}"
    )

    # 4c. Confirm is_deployed flag flipped on the agent
    agent_resp = requests.get(
        f"{BASE_URL}/api/v1/agents/{AGENT_ID}",
        headers=headers,
        timeout=30,
    )
    assert agent_resp.status_code == 200
    agent_data = agent_resp.json()
    agent = agent_data.get("agent", agent_data)
    assert agent.get("is_deployed") is True, (
        "agents.is_deployed must be True after approve-deployment"
    )
