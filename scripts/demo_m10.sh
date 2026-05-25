#!/usr/bin/env bash
# demo_m10.sh — Veridian M10 Maintenance + Observability Demo
#
# Demonstrates OPS-02/OPS-04: alert check Celery task triggers alerts which are
# readable via the alerts endpoint; digest beat tasks are registered in Celery.
#
# Prerequisites (ALL local — all services run natively, no containers):
#   1. Redis:    redis-server (running on localhost:6379)
#   2. Postgres: local install (running on localhost:5432)
#   3. API:      uvicorn app.main:app --reload       (from apps/api/)
#   4. Worker:   celery -A app.worker.celery_app worker --queues pipeline,runtime  (from apps/api/)
#
# Required env vars:
#   ADMIN_KEY   — X-Admin-Key header value for POST /api/v1/agents
#   API_KEY     — X-API-Key for tenant auth on agent routes
#
# Optional env vars:
#   BASE_URL    — FastAPI base URL (default: http://localhost:8000)
#
# Usage:
#   ADMIN_KEY=<key> API_KEY=<key> bash scripts/demo_m10.sh
#
# Exit codes:
#   0 — all assertions passed (OPS-04 alerts endpoint 200, OPS-02/04 beats registered)
#   1 — prerequisite failure, API error, timeout, or assertion failure

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL="${BASE_URL:-http://localhost:8000}"
ADMIN_KEY="${ADMIN_KEY:-}"
API_KEY="${API_KEY:-}"

# ---------------------------------------------------------------------------
# Validate required env vars
# ---------------------------------------------------------------------------

if [[ -z "$ADMIN_KEY" ]]; then
    echo "ERROR: ADMIN_KEY env var is required."
    echo "  Usage: ADMIN_KEY=<admin-key> API_KEY=<tenant-key> bash scripts/demo_m10.sh"
    exit 1
fi

if [[ -z "$API_KEY" ]]; then
    echo "ERROR: API_KEY env var is required."
    echo "  Usage: ADMIN_KEY=<admin-key> API_KEY=<tenant-key> bash scripts/demo_m10.sh"
    exit 1
fi

echo "=== Veridian M10 Demo: Maintenance + Observability ==="
echo "Base URL: $BASE_URL"
echo ""

# ---------------------------------------------------------------------------
# Section 1: Prerequisites
# ---------------------------------------------------------------------------

echo "=== Section 1: Prerequisites ==="

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

echo "[OK] Prerequisites satisfied"
echo ""

# ---------------------------------------------------------------------------
# Section 2: Create + deploy agent
# ---------------------------------------------------------------------------

echo "=== Section 2: Create + Deploy Agent ==="

AGENT_RESPONSE=$(curl -sf -X POST "$BASE_URL/api/v1/agents" \
    -H "X-Admin-Key: $ADMIN_KEY" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "M10 Demo Agent",
      "soul": {
        "voice": "helpful and concise",
        "do": ["answer customer questions clearly"],
        "do_not": ["speculate beyond documentation"]
      },
      "role": "support"
    }' 2>/dev/null || echo "")

if [[ -z "$AGENT_RESPONSE" ]]; then
    echo "ERROR: Failed to create agent (empty response from POST /api/v1/agents)."
    exit 1
fi

AGENT_ID=$(echo "$AGENT_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")

if [[ -z "$AGENT_ID" ]]; then
    echo "ERROR: Could not extract agent ID."
    echo "Response: $AGENT_RESPONSE"
    exit 1
fi

echo "Agent created: $AGENT_ID"

# Poll for agent status (up to 120 seconds)
echo "Polling agent status (up to 120s) ..."
AGENT_READY=false
for i in $(seq 1 24); do
    AGENT_STATUS_RESP=$(curl -sf --max-time 5 \
        -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID" 2>/dev/null || echo "{}")
    AGENT_STATUS=$(echo "$AGENT_STATUS_RESP" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('status', d.get('agent', {}).get('status', 'unknown')))" 2>/dev/null || echo "unknown")
    echo "  [Poll $i/24] status: $AGENT_STATUS"
    if [[ "$AGENT_STATUS" == "ready" ]]; then
        AGENT_READY=true
        break
    fi
    sleep 5
done

if [[ "$AGENT_READY" == "false" ]]; then
    echo "  [WARN] Agent not ready within 120s — continuing anyway."
    echo "  (Provisioning infrastructure may be missing or still starting.)"
fi

# Trigger deployment
echo "Triggering deployment ..."
DEPLOY_RESP=$(curl -sf -X POST "$BASE_URL/api/v1/deployments/trigger" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"agent_id\": \"$AGENT_ID\"}" 2>/dev/null || echo "")

if [[ -n "$DEPLOY_RESP" ]]; then
    DEPLOY_STATUS=$(echo "$DEPLOY_RESP" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('status', 'unknown'))" 2>/dev/null || echo "unknown")
    echo "  Deployment triggered (status: $DEPLOY_STATUS)"
else
    echo "  [WARN] Deployment trigger returned empty response — continuing."
fi

# Poll deployment until approved (up to 60 seconds)
echo "Polling deployment status (up to 60s) ..."
DEPLOY_APPROVED=false
for i in $(seq 1 12); do
    DEPLOY_STATUS_RESP=$(curl -sf --max-time 5 \
        -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID" 2>/dev/null || echo "{}")
    IS_DEPLOYED=$(echo "$DEPLOY_STATUS_RESP" | python -c "import sys,json; d=json.load(sys.stdin); a=d.get('agent',d); print(str(a.get('is_deployed', False)).lower())" 2>/dev/null || echo "false")
    echo "  [Poll $i/12] is_deployed: $IS_DEPLOYED"
    if [[ "$IS_DEPLOYED" == "true" ]]; then
        DEPLOY_APPROVED=true
        break
    fi
    sleep 5
done

if [[ "$DEPLOY_APPROVED" == "false" ]]; then
    echo "  [WARN] Agent not deployed within 60s — continuing anyway."
fi

echo ""

# ---------------------------------------------------------------------------
# Section 3: Trigger alert check
#
# NOTE: requires Celery worker running (celery -A app.worker.celery_app worker --queues runtime)
# ---------------------------------------------------------------------------

echo "=== Section 3: Trigger Alert Check ==="
echo "  Dispatching run_alert_check task via apply_async ..."

# NOTE: requires Celery worker running (celery -A app.worker.celery_app worker --queues runtime)
(cd apps/api && python -c "
from app.worker.tasks.runtime.alert import run_alert_check
run_alert_check.apply_async(kwargs={'agent_id': '$AGENT_ID'})
" 2>/dev/null || true)

echo "  Alert check task dispatched (or skipped if worker not available)."
echo "  Waiting 5s for task to process ..."
sleep 5

# Fetch and print alerts
echo "  Fetching alerts for agent $AGENT_ID ..."
ALERTS_RESP=$(curl -sf --max-time 10 \
    -H "X-API-Key: $API_KEY" \
    "$BASE_URL/api/v1/agents/$AGENT_ID/alerts" 2>/dev/null || echo "")

if [[ -n "$ALERTS_RESP" ]]; then
    echo "  Alerts response:"
    echo "$ALERTS_RESP" | python -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if isinstance(data, list):
        print(f'  Count: {len(data)}')
        for alert in data[:3]:
            print(f'    - [{alert.get(\"alert_type\",\"?\")}] {alert.get(\"message\",\"\")} (severity: {alert.get(\"severity\",\"?\")})')
    else:
        print(f'  {data}')
except Exception:
    pass
" 2>/dev/null || true
fi

echo ""

# ---------------------------------------------------------------------------
# Section 4: Show alerts output — verify endpoint returns HTTP 200
# ---------------------------------------------------------------------------

echo "=== Section 4: Alerts Endpoint Health Check (OPS-04) ==="

ALL_PASSED=true

ALERTS_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    -H "X-API-Key: $API_KEY" \
    "$BASE_URL/api/v1/agents/$AGENT_ID/alerts" 2>/dev/null || echo "000")

if [[ "$ALERTS_STATUS" == "200" ]]; then
    echo "[PASS] OPS-04: alerts endpoint returns 200"
else
    echo "[FAIL] OPS-04: alerts endpoint returned $ALERTS_STATUS (expected 200)"
    ALL_PASSED=false
fi

echo ""

# ---------------------------------------------------------------------------
# Section 5: Verify Celery beats registered (OPS-02/OPS-04)
# ---------------------------------------------------------------------------

echo "=== Section 5: Verify Celery Beat Registration (OPS-02/OPS-04) ==="

BEATS_OUTPUT=$(cd apps/api && celery -A app.worker.celery_app inspect registered 2>/dev/null || echo "")

if echo "$BEATS_OUTPUT" | grep -q "run_weekly_digest_beat" && echo "$BEATS_OUTPUT" | grep -q "run_alert_check_beat"; then
    echo "[PASS] OPS-02/OPS-04: beats registered"
else
    echo "[FAIL] OPS-02/OPS-04: beats not found in celery inspect"
    echo "  (celery inspect registered lists task function names, not beat schedule keys)"
    echo "  (Ensure Celery worker is running: cd apps/api && celery -A app.worker.celery_app worker --queues pipeline,runtime)"
    if [[ -n "$BEATS_OUTPUT" ]]; then
        echo "  Registered tasks output (first 10 lines):"
        echo "$BEATS_OUTPUT" | head -10 | sed 's/^/    /'
    fi
    ALL_PASSED=false
fi

echo ""

# ---------------------------------------------------------------------------
# Final status
# ---------------------------------------------------------------------------

echo "=== M10 Demo: Summary ==="
echo ""
echo "  Agent ID: $AGENT_ID"
echo ""
echo "  Run commands for this demo:"
echo "    redis-server"
echo "    cd apps/api && uvicorn app.main:app --reload"
echo "    cd apps/api && celery -A app.worker.celery_app worker --queues pipeline,runtime"
echo ""
echo "  To run guarded E2E test:"
echo "    OPS_E2E_ENABLED=1 OPS_E2E_AGENT_ID=<uuid> OPS_E2E_API_KEY=<key> \\"
echo "      python -m pytest apps/api/tests/e2e/test_observability_e2e.py -v"
echo ""

if [[ "$ALL_PASSED" == "true" ]]; then
    echo "=== M10 Demo: PASSED ==="
    exit 0
else
    echo "=== M10 Demo: FAILED (one or more assertions — see [FAIL] lines above) ==="
    exit 1
fi
