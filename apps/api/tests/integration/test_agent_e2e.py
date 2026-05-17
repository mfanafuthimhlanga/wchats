import asyncio
import json
import os
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.getenv("AGENT_E2E_ENABLED"),
        reason="Set AGENT_E2E_ENABLED=1 to run E2E tests (requires real Claude API + local services)"
    ),
]


@pytest.mark.anyio
async def test_agent_responds_with_citation_against_real_corpus():
    """Full E2E: POST /agents/{id}/chat -> Celery SDK task -> SSE agent.response + citations.

    Requires:
    - AGENT_E2E_ENABLED=1
    - ANTHROPIC_API_KEY set
    - AGENT_ID env var (UUID of a ready agent with ingested corpus)
    - API_KEY env var (X-API-Key for that tenant)
    - Local services: Redis, Postgres, uvicorn :8000, Celery worker
    """
    import httpx

    agent_id = os.environ["AGENT_ID"]
    api_key = os.environ["API_KEY"]
    api_base = os.getenv("API_BASE", "http://localhost:8000")

    async with httpx.AsyncClient(base_url=api_base, timeout=35) as client:
        # POST /agents/{id}/chat → 202 + job_id
        resp = await client.post(
            f"/agents/{agent_id}/chat",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"message": "What are your business hours?", "conversation_id": None},
        )
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    job_id = resp.json()["job_id"]

    # Stream SSE until agent.response arrives
    payload = None

    async def _read_agent_response() -> dict:
        async with httpx.AsyncClient(base_url=api_base, timeout=35) as stream_client:
            async with stream_client.stream("GET", f"/widget/jobs/{job_id}/events") as stream:
                async for line in stream.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "agent.response":
                            return event.get("payload", {})
        return {}

    payload = await asyncio.wait_for(_read_agent_response(), timeout=30)

    assert payload, "No agent.response event received within 30s"
    assert payload.get("text"), "agent.response payload has no text"
    assert isinstance(payload.get("citations"), list), "citations must be a list"
    assert len(payload["citations"]) >= 1, "Expected at least 1 citation"
