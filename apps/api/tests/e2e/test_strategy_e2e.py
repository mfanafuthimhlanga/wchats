"""
Guarded E2E tests for retrieval strategy synthesis (STR-01/STR-02/STR-03).

Guard: STRATEGY_E2E_ENABLED=1 required. Uses a real tenant with an ingested
corpus and a locally running W Chats API + Celery worker.

Not run in CI by default — requires:
  - STRATEGY_E2E_ENABLED=1
  - STRATEGY_E2E_AGENT_ID  — UUID of a ready agent with synthesized retrieval_strategy
  - STRATEGY_E2E_API_KEY   — raw API key for the agent's tenant
  - STRATEGY_E2E_BASE_URL  — base URL of the API (default: http://localhost:8000)

Run with:
  STRATEGY_E2E_ENABLED=1 \\
  STRATEGY_E2E_AGENT_ID=<uuid> \\
  STRATEGY_E2E_API_KEY=<key> \\
  python -m pytest tests/e2e/test_strategy_e2e.py -m e2e --tb=short
"""

import os
import time

import httpx
import pytest

# ---------------------------------------------------------------------------
# Guard: skip unless STRATEGY_E2E_ENABLED=1
# ---------------------------------------------------------------------------

STRATEGY_E2E_ENABLED = os.environ.get("STRATEGY_E2E_ENABLED", "0") == "1"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not STRATEGY_E2E_ENABLED,
        reason="STRATEGY_E2E_ENABLED=1 required for real strategist E2E test",
    ),
]

# ---------------------------------------------------------------------------
# STR-01: Auto-generated strategy is non-empty after ingestion
# ---------------------------------------------------------------------------


def test_str01_strategy_is_auto_generated():
    """STR-01: GET /api/v1/agents/{id} → retrieval_strategy is a non-empty dict.

    The synthesize_retrieval_strategy Celery task writes the strategy to
    agents.retrieval_strategy after embed_and_migrate completes. If a corpus
    was just ingested, poll up to 60s for the strategy to appear.

    Verifies:
    - retrieval_strategy is present in the agent response
    - retrieval_strategy is a non-empty dict (not {})
    """
    agent_id = os.environ["STRATEGY_E2E_AGENT_ID"]
    api_key = os.environ["STRATEGY_E2E_API_KEY"]
    base_url = os.environ.get("STRATEGY_E2E_BASE_URL", "http://localhost:8000")

    strategy = {}
    deadline = time.time() + 60

    while time.time() < deadline:
        resp = httpx.get(
            f"{base_url}/api/v1/agents/{agent_id}",
            headers={"X-API-Key": api_key},
            timeout=15,
        )
        assert resp.status_code == 200, (
            f"Expected 200 from GET /agents/{agent_id}, got {resp.status_code}: {resp.text}"
        )

        body = resp.json()
        agent = body.get("agent", body)
        strategy = agent.get("retrieval_strategy", {})

        if strategy:
            break

        time.sleep(3)

    assert strategy, (
        f"retrieval_strategy is empty ({strategy!r}) after 60s — "
        "ensure the agent has an ingested corpus and the Celery worker is running"
    )
    assert isinstance(strategy, dict), (
        f"retrieval_strategy must be a dict, got {type(strategy)}: {strategy!r}"
    )


# ---------------------------------------------------------------------------
# STR-02: Synthesized strategy contains valid RetrievalStrategy fields
# ---------------------------------------------------------------------------


def test_str02_strategy_fields_in_bounds():
    """STR-02: Synthesized strategy contains all 6 RetrievalStrategy keys with
    values within documented bounds.

    Expected schema (RetrievalStrategy Pydantic model in retrieval_service.py):
      vector_k:          int, expected 10–50
      bm25_k:            int, expected 10–50
      final_k:           int, expected 3–10
      rerank_threshold:  float, expected 0.0–1.0
      query_expansion:   bool
      metadata_filters:  list

    Verifies:
    - All 6 keys are present
    - vector_k is an int in range [10, 50]
    - final_k is an int in range [3, 10]
    - query_expansion is a bool
    - metadata_filters is a list
    """
    agent_id = os.environ["STRATEGY_E2E_AGENT_ID"]
    api_key = os.environ["STRATEGY_E2E_API_KEY"]
    base_url = os.environ.get("STRATEGY_E2E_BASE_URL", "http://localhost:8000")

    resp = httpx.get(
        f"{base_url}/api/v1/agents/{agent_id}",
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text}"
    )

    body = resp.json()
    agent = body.get("agent", body)
    strategy = agent.get("retrieval_strategy", {})

    assert strategy, (
        "retrieval_strategy is empty — run test_str01 first or ensure corpus is ingested"
    )

    # All six keys must be present
    expected_keys = {
        "vector_k",
        "bm25_k",
        "final_k",
        "rerank_threshold",
        "query_expansion",
        "metadata_filters",
    }
    present_keys = set(strategy.keys())
    missing_keys = expected_keys - present_keys
    assert not missing_keys, (
        f"Missing RetrievalStrategy keys: {missing_keys}. Got: {present_keys}"
    )

    # vector_k: int in [10, 50]
    vector_k = strategy["vector_k"]
    assert isinstance(vector_k, int), (
        f"vector_k must be int, got {type(vector_k)}: {vector_k!r}"
    )
    assert 10 <= vector_k <= 50, (
        f"vector_k={vector_k} out of expected range [10, 50]"
    )

    # final_k: int in [3, 10]
    final_k = strategy["final_k"]
    assert isinstance(final_k, int), (
        f"final_k must be int, got {type(final_k)}: {final_k!r}"
    )
    assert 3 <= final_k <= 10, (
        f"final_k={final_k} out of expected range [3, 10]"
    )

    # query_expansion: bool
    assert isinstance(strategy["query_expansion"], bool), (
        f"query_expansion must be bool, got {type(strategy['query_expansion'])}: "
        f"{strategy['query_expansion']!r}"
    )

    # metadata_filters: list
    assert isinstance(strategy["metadata_filters"], list), (
        f"metadata_filters must be list, got {type(strategy['metadata_filters'])}: "
        f"{strategy['metadata_filters']!r}"
    )


# ---------------------------------------------------------------------------
# STR-03: Eval machinery works — faithfulness metric returned and numeric
#         (tolerant: asserts metric is returned, not a hard threshold)
# ---------------------------------------------------------------------------


def test_str03_eval_run_returns_faithfulness():
    """STR-03: POST /eval-runs/trigger → poll to completion → faithfulness is present and numeric.

    This test is intentionally tolerant — it verifies the comparison machinery
    works (eval runs, metrics are returned) without enforcing a hard faithfulness
    threshold, since real Ragas scores vary run to run.

    Verifies:
    - POST /eval-runs/trigger returns 202 with task_id
    - GET /eval-runs eventually shows a completed run
    - The completed run has a numeric faithfulness aggregate_score
    """
    agent_id = os.environ["STRATEGY_E2E_AGENT_ID"]
    api_key = os.environ["STRATEGY_E2E_API_KEY"]
    base_url = os.environ.get("STRATEGY_E2E_BASE_URL", "http://localhost:8000")

    # 1. Trigger eval run
    trigger_resp = httpx.post(
        f"{base_url}/api/v1/agents/{agent_id}/eval-runs/trigger",
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    assert trigger_resp.status_code == 202, (
        f"Expected 202 from POST /eval-runs/trigger, got {trigger_resp.status_code}: "
        f"{trigger_resp.text}"
    )

    trigger_body = trigger_resp.json()
    assert "task_id" in trigger_body, (
        f"Missing task_id in trigger response: {trigger_body}"
    )
    assert trigger_body.get("status") == "queued", (
        f"Expected status=queued, got {trigger_body.get('status')!r}"
    )

    # 2. Poll GET /eval-runs until a run is complete (up to 3 minutes)
    deadline = time.time() + 180
    completed_run = None

    while time.time() < deadline:
        runs_resp = httpx.get(
            f"{base_url}/api/v1/agents/{agent_id}/eval-runs",
            headers={"X-API-Key": api_key},
            timeout=15,
        )
        assert runs_resp.status_code == 200, (
            f"Expected 200 from GET /eval-runs, got {runs_resp.status_code}: {runs_resp.text}"
        )

        runs_body = runs_resp.json()
        eval_runs = runs_body.get("eval_runs", [])

        for run in eval_runs:
            if run.get("status") == "complete":
                completed_run = run
                break

        if completed_run is not None:
            break

        time.sleep(3)

    assert completed_run is not None, (
        "No eval run reached 'complete' status within 3 minutes. "
        "Ensure the Celery worker is running with --queues runtime "
        "and the agent has eval scenarios."
    )

    # 3. Assert faithfulness aggregate is present and numeric
    aggregate_scores = completed_run.get("aggregate_scores", {})
    assert "faithfulness" in aggregate_scores, (
        f"Missing 'faithfulness' in aggregate_scores: {aggregate_scores}"
    )

    faithfulness = aggregate_scores["faithfulness"]
    assert isinstance(faithfulness, (int, float)), (
        f"faithfulness must be numeric, got {type(faithfulness)}: {faithfulness!r}"
    )

    # Soft bounds check: faithfulness is a Ragas metric, should be 0.0–1.0
    assert 0.0 <= faithfulness <= 1.0, (
        f"faithfulness={faithfulness} outside expected Ragas range [0.0, 1.0]"
    )
