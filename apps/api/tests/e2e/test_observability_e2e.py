"""
Guarded E2E tests for observability endpoints (OPS-04/OPS-05).

Guard: OPS_E2E_ENABLED=1 required. Uses a real tenant with a locally running
Veridian API + Celery worker.

Not run in CI by default — requires:
  - OPS_E2E_ENABLED=1
  - OPS_E2E_AGENT_ID   — UUID of a ready agent
  - OPS_E2E_API_KEY    — raw API key for the agent's tenant
  - OPS_E2E_BASE_URL   — base URL of the API (default: http://localhost:8000)

Run with:
  OPS_E2E_ENABLED=1 \\
  OPS_E2E_AGENT_ID=<uuid> \\
  OPS_E2E_API_KEY=<key> \\
  python -m pytest tests/e2e/test_observability_e2e.py -m e2e --tb=short
"""

import os

import httpx
import pytest

# ---------------------------------------------------------------------------
# Guard: skip unless OPS_E2E_ENABLED=1
# ---------------------------------------------------------------------------

OPS_E2E_ENABLED = os.environ.get("OPS_E2E_ENABLED", "0") == "1"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not OPS_E2E_ENABLED,
        reason="OPS_E2E_ENABLED=1 required",
    ),
]

# ---------------------------------------------------------------------------
# Module-level constants (read from env at import time)
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("OPS_E2E_BASE_URL", "http://localhost:8000") + "/api/v1"
AGENT_ID = os.environ.get("OPS_E2E_AGENT_ID", "")
API_KEY = os.environ.get("OPS_E2E_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}

# ---------------------------------------------------------------------------
# OPS-04: Alerts endpoint returns a list
# ---------------------------------------------------------------------------


def test_ops04_alerts_endpoint_returns_list():
    """OPS-04: GET /api/v1/agents/{id}/alerts → 200, body is a list.

    Verifies:
    - Endpoint is reachable and returns HTTP 200
    - Response body is a JSON list (may be empty if no alerts triggered)
    """
    resp = httpx.get(
        f"{BASE_URL}/agents/{AGENT_ID}/alerts",
        headers=HEADERS,
        timeout=15,
    )
    assert resp.status_code == 200, (
        f"Expected 200 from GET /agents/{AGENT_ID}/alerts, got {resp.status_code}: {resp.text}"
    )

    body = resp.json()
    assert isinstance(body, list), (
        f"Expected list from /alerts endpoint, got {type(body)}: {body!r}"
    )


# ---------------------------------------------------------------------------
# OPS-04: Alert resolve roundtrip
# ---------------------------------------------------------------------------


def test_ops04_alert_resolve_roundtrip():
    """OPS-04: GET alerts → resolve first alert → GET again → resolved alert absent.

    Verifies:
    - An unresolved alert can be resolved via POST /alerts/{id}/resolve
    - The resolved alert no longer appears in the GET /alerts list
    """
    # Fetch current unresolved alerts
    resp = httpx.get(
        f"{BASE_URL}/agents/{AGENT_ID}/alerts",
        headers=HEADERS,
        timeout=15,
    )
    assert resp.status_code == 200, (
        f"Expected 200 from GET /agents/{AGENT_ID}/alerts, got {resp.status_code}: {resp.text}"
    )

    alerts = resp.json()
    assert isinstance(alerts, list), (
        f"Expected list from /alerts endpoint, got {type(alerts)}: {alerts!r}"
    )

    if not alerts:
        pytest.skip("No active alerts — cannot test resolve roundtrip")

    # Resolve the first alert
    first_alert_id = alerts[0]["id"]
    resolve_resp = httpx.post(
        f"{BASE_URL}/agents/{AGENT_ID}/alerts/{first_alert_id}/resolve",
        headers=HEADERS,
        timeout=15,
    )
    assert resolve_resp.status_code == 200, (
        f"Expected 200 from POST /alerts/{first_alert_id}/resolve, "
        f"got {resolve_resp.status_code}: {resolve_resp.text}"
    )

    resolve_body = resolve_resp.json()
    assert resolve_body.get("resolved") is True, (
        f"Expected resolved=True in response, got: {resolve_body!r}"
    )

    # Fetch alerts again — resolved alert should no longer appear
    resp2 = httpx.get(
        f"{BASE_URL}/agents/{AGENT_ID}/alerts",
        headers=HEADERS,
        timeout=15,
    )
    assert resp2.status_code == 200, (
        f"Expected 200 from second GET /agents/{AGENT_ID}/alerts, "
        f"got {resp2.status_code}: {resp2.text}"
    )

    alerts_after = resp2.json()
    assert isinstance(alerts_after, list), (
        f"Expected list from second /alerts call, got {type(alerts_after)}: {alerts_after!r}"
    )

    resolved_ids = [a["id"] for a in alerts_after]
    assert first_alert_id not in resolved_ids, (
        f"Resolved alert {first_alert_id} still appears in /alerts response after resolve"
    )
