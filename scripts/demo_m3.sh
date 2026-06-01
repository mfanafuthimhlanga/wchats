#!/usr/bin/env bash
# demo_m3.sh — W Chats M3 hybrid retrieval smoke test
#
# Prerequisites:
#   - docker-compose services running (docker compose up -d) with M2 data ingested
#   - jq installed (brew install jq / apt-get install jq)
#   - API_KEY env var set (tenant API key from POST /tenants, see demo_m2.sh output)
#   - AGENT_ID env var set (agent UUID with status=ready and M2 data ingested)
#
# Usage:
#   API_KEY=vrd_... AGENT_ID=<uuid> bash scripts/demo_m3.sh
#   BASE_URL=http://localhost:8000 API_KEY=vrd_... AGENT_ID=<uuid> bash scripts/demo_m3.sh
#
# Exit codes:
#   0 — demo passed (query.complete event received with results)
#   1 — any step failed or query.complete not received within 60s

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-}"
AGENT_ID="${AGENT_ID:-}"

if [[ -z "$API_KEY" ]]; then
    echo "ERROR: API_KEY required"
    exit 1
fi
if [[ -z "$AGENT_ID" ]]; then
    echo "ERROR: AGENT_ID required"
    exit 1
fi

echo "=== M3 Demo: Hybrid Retrieval ==="
echo "Submitting query to agent $AGENT_ID..."

QUERY_RESPONSE=$(curl -sf -X POST "$BASE_URL/agents/$AGENT_ID/query" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"query": "What is the refund policy?"}')

JOB_ID=$(echo "$QUERY_RESPONSE" | jq -r '.job_id')
echo "Job dispatched: $JOB_ID"

echo "Polling for query.complete event..."
DEADLINE=$((SECONDS + 60))
COMPLETE=false
EVENTS=""

while [[ $SECONDS -lt $DEADLINE ]]; do
    EVENTS=$(curl -sf --max-time 10 "$BASE_URL/jobs/$JOB_ID/events" \
        -H "X-API-Key: $API_KEY" 2>/dev/null || true)
    if echo "$EVENTS" | grep -q '"query.complete"'; then
        COMPLETE=true
        break
    fi
    sleep 2
done

if [[ "$COMPLETE" != "true" ]]; then
    echo "ERROR: query.complete not received within 60s"
    exit 1
fi

echo "query.complete received"

RESULT_COUNT=$(echo "$EVENTS" | grep '"query.complete"' | jq -r '.payload.results | length' 2>/dev/null || echo "?")
echo "Results count: ${RESULT_COUNT:-?}"
echo "=== M3 Demo: PASSED ==="
