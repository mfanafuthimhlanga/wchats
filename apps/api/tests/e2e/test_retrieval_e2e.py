"""
E2E test for full hybrid retrieval chain against real Voyage API.

Guard: RETRIEVAL_E2E_ENABLED=1 required. Uses a real M2 tenant DB with
ingested data and a deployed (or locally running) W Chats API service.

Not run in CI by default — requires:
  - RETRIEVAL_E2E_ENABLED=1
  - RETRIEVAL_E2E_AGENT_ID  — UUID of a ready agent with ingested data
  - RETRIEVAL_E2E_API_KEY   — raw API key for the agent's tenant
  - RETRIEVAL_E2E_BASE_URL  — base URL of the API (default: http://localhost:8000)
  - VOYAGE_API_KEY          — real Voyage AI key (used by the worker)

Run with:
  RETRIEVAL_E2E_ENABLED=1 \\
  RETRIEVAL_E2E_AGENT_ID=<uuid> \\
  RETRIEVAL_E2E_API_KEY=<key> \\
  pytest tests/e2e/test_retrieval_e2e.py -m e2e --tb=short
"""

import json
import os
import time

import httpx
import pytest

# ---------------------------------------------------------------------------
# Guard: skip unless RETRIEVAL_E2E_ENABLED=1
# ---------------------------------------------------------------------------
RETRIEVAL_E2E_ENABLED = os.environ.get("RETRIEVAL_E2E_ENABLED", "0") == "1"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not RETRIEVAL_E2E_ENABLED,
        reason="RETRIEVAL_E2E_ENABLED=1 required for real Voyage E2E test",
    ),
]


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------


def test_full_retrieval_chain_real_voyage():
    """Full E2E: POST /agents/{id}/query → poll job events → assert query.complete.

    Verifies:
    - POST /agents/{id}/query returns 202 with job_id and events_url.
    - Polling GET /jobs/{job_id}/events within 60s yields a 'query.complete' event.
    - query.complete payload contains:
        - results (list)
        - trace with vector_candidates, bm25_candidates, fused_candidates,
          reranked_candidates
        - strategy_used

    This test makes real Voyage API calls (embed_query + rerank) against
    a real tenant DB with previously ingested data.
    """
    agent_id = os.environ["RETRIEVAL_E2E_AGENT_ID"]
    api_key = os.environ["RETRIEVAL_E2E_API_KEY"]
    base_url = os.environ.get("RETRIEVAL_E2E_BASE_URL", "http://localhost:8000")

    # ------------------------------------------------------------------
    # 1. POST query — dispatch retrieve_and_rank job
    # ------------------------------------------------------------------
    resp = httpx.post(
        f"{base_url}/agents/{agent_id}/query",
        headers={"X-API-Key": api_key},
        json={"query": "What is the refund policy?"},
        timeout=30,
    )
    assert resp.status_code == 202, (
        f"Expected 202 from POST /query, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "job_id" in body, f"Missing job_id in POST response: {body}"
    assert "events_url" in body, f"Missing events_url in POST response: {body}"
    assert body["status"] == "pending", f"Expected status=pending, got {body['status']}"

    job_id = body["job_id"]
    events_url = body["events_url"].lstrip("/")  # strip leading slash for URL join

    # ------------------------------------------------------------------
    # 2. Poll GET /jobs/{job_id}/events until query.complete is received
    # ------------------------------------------------------------------
    deadline = time.time() + 60
    result = None

    while time.time() < deadline:
        try:
            events_resp = httpx.get(
                f"{base_url}/{events_url}",
                headers={"X-API-Key": api_key},
                timeout=30,
            )
            assert events_resp.status_code == 200, (
                f"Events endpoint returned {events_resp.status_code}: {events_resp.text}"
            )

            for line in events_resp.text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event_type") == "query.complete":
                        result = event.get("payload", {})
                        break

        except httpx.RequestError:
            pass  # transient — retry

        if result is not None:
            break

        time.sleep(1)

    # ------------------------------------------------------------------
    # 3. Assert query.complete payload shape
    # ------------------------------------------------------------------
    assert result is not None, (
        f"query.complete event not received within 60s for job_id={job_id}"
    )
    assert "results" in result, f"Missing 'results' in query.complete payload: {result.keys()}"
    assert isinstance(result["results"], list), (
        f"'results' should be a list, got {type(result['results'])}"
    )

    assert "trace" in result, f"Missing 'trace' in query.complete payload: {result.keys()}"
    trace = result["trace"]
    assert "vector_candidates" in trace, f"Missing vector_candidates in trace: {trace.keys()}"
    assert "bm25_candidates" in trace, f"Missing bm25_candidates in trace: {trace.keys()}"
    assert "fused_candidates" in trace, f"Missing fused_candidates in trace: {trace.keys()}"
    assert "reranked_candidates" in trace, (
        f"Missing reranked_candidates in trace: {trace.keys()}"
    )

    assert "strategy_used" in result, (
        f"Missing 'strategy_used' in query.complete payload: {result.keys()}"
    )
