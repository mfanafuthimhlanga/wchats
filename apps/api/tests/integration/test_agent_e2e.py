import os
import pytest
import uuid

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.getenv("AGENT_E2E_ENABLED"),
        reason="Set AGENT_E2E_ENABLED=1 to run E2E tests against real Claude API + local services"
    )
]


def test_agent_responds_with_citation_against_real_corpus():
    """Full E2E: POST /agents/{id}/chat -> SDK call -> SSE agent.response with citations.

    Requires:
    - AGENT_E2E_ENABLED=1
    - ANTHROPIC_API_KEY set
    - AGENT_ID env var (UUID of a ready agent with ingested corpus)
    - API_KEY env var (X-API-Key for that tenant)
    - Local services: Redis, Postgres, uvicorn on :8000, Celery worker
    """
    import httpx
    import json
    import time

    agent_id = os.getenv("AGENT_ID", "")
    api_key = os.getenv("API_KEY", "")
    api_base = os.getenv("API_BASE", "http://localhost:8000")

    if not agent_id or not api_key:
        pytest.skip("Set AGENT_ID and API_KEY env vars to run E2E test")

    # POST chat
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{api_base}/agents/{agent_id}/chat",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"message": "What are your business hours?", "conversation_id": None},
        )
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    data = resp.json()
    job_id = data["job_id"]

    # Stream SSE events until agent.response
    events_url = f"{api_base}/widget/jobs/{job_id}/events"
    payload = None
    deadline = time.time() + 60

    with httpx.Client(timeout=65) as client:
        with client.stream("GET", events_url) as stream:
            buffer = ""
            for line in stream.iter_lines():
                if time.time() > deadline:
                    break
                buffer += line + "\n"
                if "agent.response" in buffer and "data:" in buffer:
                    for ln in buffer.split("\n"):
                        if ln.startswith("data:"):
                            try:
                                payload = json.loads(ln[5:].strip())
                                break
                            except json.JSONDecodeError:
                                pass
                    if payload:
                        break

    assert payload is not None, "No agent.response event received within 60s"
    assert payload.get("text"), "agent.response payload has no text"
    assert isinstance(payload.get("citations"), list), "citations must be a list"
    assert len(payload["citations"]) >= 1, "Expected at least 1 citation"
