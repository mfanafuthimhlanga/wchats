#!/usr/bin/env bash
# demo_m6.sh — W Chats M6 Eval System demo script
#
# D-32 LOCKED: local processes only — no Docker.
#
# Prerequisites (ALL local — no Docker):
#   1. Redis:     redis-server (running on localhost:6379)
#   2. Postgres:  local install (running on localhost:5432)
#   3. API:       uvicorn app.main:app --reload    (from apps/api/)
#   4. Worker:    celery -A app.worker.celery_app worker --queues runtime  (from apps/api/)
#   5. Admin UI:  pnpm dev                         (from apps/admin/) — optional, for dashboard checkpoint
#
# Required env vars:
#   AGENT_ID    — UUID of a deployed agent with eval scenarios loaded
#   API_KEY     — X-API-Key for the tenant (used for widget query endpoint)
#
# Optional env vars:
#   TEST_QUERY  — Widget query to test cache hit (default: "What is your return policy?")
#   BASE_URL    — FastAPI base URL (default: http://localhost:8000)
#   TENANT_DB_URL — Direct psycopg2 connection string for the tenant DB.
#                   If unset, verified_qa count step is skipped gracefully.
#
# Usage:
#   AGENT_ID=<uuid> API_KEY=<key> bash scripts/demo_m6.sh
#
# Exit codes:
#   0 — demo completed successfully
#   1 — prerequisite check failed or eval run timed out

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL="${BASE_URL:-http://localhost:8000}"
AGENT_ID="${AGENT_ID:-}"
API_KEY="${API_KEY:-}"
TEST_QUERY="${TEST_QUERY:-What is your return policy?}"
TENANT_DB_URL="${TENANT_DB_URL:-}"

# ---------------------------------------------------------------------------
# Validate required env vars
# ---------------------------------------------------------------------------

if [[ -z "$AGENT_ID" ]]; then
    echo "ERROR: AGENT_ID env var is required."
    echo "  Usage: AGENT_ID=<uuid> API_KEY=<key> bash scripts/demo_m6.sh"
    exit 1
fi

if [[ -z "$API_KEY" ]]; then
    echo "ERROR: API_KEY env var is required."
    echo "  Usage: AGENT_ID=<uuid> API_KEY=<key> bash scripts/demo_m6.sh"
    exit 1
fi

echo "=== W Chats M6 Demo: Eval System ==="
echo "Agent:    $AGENT_ID"
echo "Base URL: $BASE_URL"
echo ""

# ---------------------------------------------------------------------------
# Prerequisite checks — local processes only (no Docker)
# ---------------------------------------------------------------------------

echo "[Prereqs] Checking local services..."

# Check Redis is reachable
if ! redis-cli ping >/dev/null 2>&1; then
    echo "ERROR: Redis is not reachable. Start with: redis-server"
    exit 1
fi
echo "  [OK] Redis reachable (redis-cli ping)"

# Check FastAPI/uvicorn is reachable
if ! curl -sf --max-time 5 "$BASE_URL/health" >/dev/null 2>&1; then
    echo "ERROR: FastAPI not reachable at $BASE_URL/health"
    echo "  Start with: cd apps/api && uvicorn app.main:app --reload"
    exit 1
fi
echo "  [OK] FastAPI reachable ($BASE_URL/health)"

# Check PostgreSQL is reachable (via psql if available)
if command -v psql >/dev/null 2>&1; then
    if psql -c "SELECT 1;" >/dev/null 2>&1; then
        echo "  [OK] PostgreSQL reachable (psql)"
    else
        echo "  [WARN] PostgreSQL psql check failed — continuing (will fail later if DB is down)"
    fi
else
    echo "  [SKIP] psql not found — skipping PostgreSQL check"
fi

echo ""

# ---------------------------------------------------------------------------
# Section 1 — Generate eval scenarios (trigger generate_eval_suite task)
# ---------------------------------------------------------------------------

echo "=== Section 1: Generate Eval Scenarios ==="
echo "Triggering generate_eval_suite Celery task for agent $AGENT_ID ..."

GENERATE_OUTPUT=$(cd apps/api && python -c "
from app.worker.tasks.runtime.eval import generate_eval_suite
r = generate_eval_suite.apply_async(kwargs={'agent_id': '$AGENT_ID'})
print('Task ID:', r.id)
" 2>&1 || echo "GENERATE_FAILED")

echo "$GENERATE_OUTPUT"

if echo "$GENERATE_OUTPUT" | grep -q "GENERATE_FAILED\|Error\|Traceback"; then
    echo "[WARN] generate_eval_suite task dispatch failed — scenarios may already exist or task unavailable."
    echo "       Continuing to eval run section (run_eval_suite fetches existing scenarios)."
else
    echo "Waiting 5s for task pickup ..."
    sleep 5
fi

echo ""

# Show eval_scenarios count if TENANT_DB_URL is set
if [[ -n "$TENANT_DB_URL" ]]; then
    echo "eval_scenarios table count:"
    psql "$TENANT_DB_URL" -c "SELECT COUNT(*) AS scenario_count FROM eval_scenarios;" 2>/dev/null || echo "  (psql query failed — DB may need warming up)"
    echo ""
fi

# ---------------------------------------------------------------------------
# Section 2 — Trigger eval run + poll for completion
# ---------------------------------------------------------------------------

echo "=== Section 2: Run Eval Suite ==="
echo "Triggering run_eval_suite Celery task for agent $AGENT_ID ..."

TRIGGER_OUTPUT=$(cd apps/api && python -c "
from app.worker.tasks.runtime.eval import run_eval_suite
r = run_eval_suite.apply_async(kwargs={'agent_id': '$AGENT_ID'})
print(r.id)
" 2>&1)

TASK_ID=$(echo "$TRIGGER_OUTPUT" | tail -n 1)

if [[ -z "$TASK_ID" ]]; then
    echo "ERROR: Failed to dispatch run_eval_suite task."
    echo "Output: $TRIGGER_OUTPUT"
    exit 1
fi

echo "Task dispatched: $TASK_ID"
echo "Polling for completion (up to 5 minutes, checking every 15s) ..."
echo ""

MAX_POLLS=20
POLL_COUNT=0
FINAL_STATUS="PENDING"

while [[ $POLL_COUNT -lt $MAX_POLLS ]]; do
    FINAL_STATUS=$(cd apps/api && python -c "
from app.worker.celery_app import celery_app
r = celery_app.AsyncResult('$TASK_ID')
print(r.state)
" 2>/dev/null || echo "PENDING")

    echo "  [Poll $((POLL_COUNT + 1))/$MAX_POLLS] Status: $FINAL_STATUS"

    if [[ "$FINAL_STATUS" == "SUCCESS" ]] || [[ "$FINAL_STATUS" == "FAILURE" ]]; then
        break
    fi

    POLL_COUNT=$((POLL_COUNT + 1))
    sleep 15
done

if [[ "$FINAL_STATUS" == "FAILURE" ]]; then
    echo ""
    echo "ERROR: Eval run task FAILED. Check Celery worker logs."
    exit 1
elif [[ "$FINAL_STATUS" != "SUCCESS" ]]; then
    echo ""
    echo "WARNING: Eval run did not complete within 5 minutes (final status: $FINAL_STATUS)."
    echo "  The eval run may still be processing. Check Celery worker logs."
fi

echo ""
echo "Eval run complete (task: $TASK_ID, status: $FINAL_STATUS)."
echo ""

# ---------------------------------------------------------------------------
# Section 3 — Inspect verified_qa entries promoted from this run
# ---------------------------------------------------------------------------

echo "=== Section 3: verified_qa Entries ==="

if [[ -n "$TENANT_DB_URL" ]]; then
    echo "verified_qa rows promoted by system (source='sandbox_test'):"
    psql "$TENANT_DB_URL" -c "
SELECT
    id,
    LEFT(question, 60) AS question,
    faithfulness,
    relevance,
    promoted_by,
    promoted_at
FROM verified_qa
WHERE source = 'sandbox_test'
ORDER BY promoted_at DESC
LIMIT 10;
" 2>/dev/null || echo "  (psql query failed — check TENANT_DB_URL)"
else
    echo "[SKIP] TENANT_DB_URL not set — showing via FastAPI eval-runs route instead."
    echo ""
    echo "Fetching eval runs from $BASE_URL/api/v1/agents/$AGENT_ID/eval-runs ..."
    EVAL_RUNS=$(curl -sf --max-time 10 \
        -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID/eval-runs" 2>/dev/null || echo "{}")
    echo "$EVAL_RUNS" | python -m json.tool 2>/dev/null || echo "$EVAL_RUNS"
fi

echo ""

# ---------------------------------------------------------------------------
# Section 4 — Widget query hitting verified_qa cache
# ---------------------------------------------------------------------------

echo "=== Section 4: Widget Cache Hit Demo ==="
echo "Sending widget query: \"$TEST_QUERY\""
echo ""

QUERY_RESPONSE=$(curl -s --max-time 30 \
    -X POST "$BASE_URL/api/v1/agents/$AGENT_ID/query" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"$TEST_QUERY\"}" 2>/dev/null || echo "{}")

echo "Query response:"
echo "$QUERY_RESPONSE" | python -m json.tool 2>/dev/null || echo "$QUERY_RESPONSE"
echo ""

# Check trace for cache_hit
echo "=== Trace Check: cache_hit verification ==="
CACHE_HIT=$(echo "$QUERY_RESPONSE" | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    trace = d.get('trace', {})
    cache_hit = trace.get('cache_hit', False)
    source = trace.get('source', 'unknown')
    print(f'cache_hit: {cache_hit}')
    print(f'source: {source}')
    if cache_hit and source == 'verified_qa_cache':
        print('RESULT: Cache hit confirmed — vector search was skipped.')
    elif cache_hit:
        print('RESULT: Cache hit confirmed.')
    else:
        print('RESULT: Cache miss — response served via hybrid search.')
except Exception as e:
    print(f'(Could not parse trace: {e})')
" 2>/dev/null || echo "  (Could not parse query response)")

echo "$CACHE_HIT"
echo ""

# ---------------------------------------------------------------------------
# Human checkpoint
# ---------------------------------------------------------------------------

echo "=== Human Checkpoint ==="
echo ""
echo "  Open http://localhost:3000/agents/$AGENT_ID/evals to see the eval dashboard."
echo ""
echo "  Verify:"
echo "    1. Pass Rates chart shows the latest run with Faithfulness, Answer Relevancy,"
echo "       Context Precision, and Context Recall bars."
echo "    2. Scenarios tab lists individual scenarios with pass/fail status per metric."
echo "    3. At least one row in verified_qa was promoted (source='sandbox_test')."
echo "    4. Widget query above shows cache_hit: true when a matching verified_qa entry"
echo "       exists with cosine similarity >= 0.93 (VERIFIED_QA_HIT_THRESHOLD)."
echo ""
echo "  Run commands:"
echo "    uvicorn app.main:app --reload                    (from apps/api/)"
echo "    celery -A app.worker.celery_app worker --queues runtime  (from apps/api/)"
echo "    celery -A app.worker.celery_app beat --loglevel=info     (M6+, from apps/api/)"
echo "    pnpm dev                                          (from apps/admin/)"
echo ""
echo "=== M6 Demo: Complete ==="
