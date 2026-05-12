#!/usr/bin/env bash
# demo_m1.sh — Veridian M1 end-to-end demo
#
# Prerequisites:
#   - docker-compose services running (docker compose up -d)
#   - ADMIN_KEY env var set (from .env or exported in shell)
#   - jq installed (brew install jq / apt-get install jq)
#
# Exit codes:
#   0 — demo passed (agent status=ready, neon_project_id set)
#   1 — any step failed
#
# Usage:
#   ADMIN_KEY=vrd_admin_... bash scripts/demo_m1.sh
#   API_BASE=http://localhost:8000 ADMIN_KEY=... bash scripts/demo_m1.sh

set -euo pipefail

API="${API_BASE:-http://localhost:8000}"
ADMIN_KEY="${ADMIN_KEY:?ADMIN_KEY env var required — see .env.example}"

echo "=== Veridian M1 Demo ==="
echo "API: $API"

# ------------------------------------------------------------------------------
# Step 1: Bootstrap a tenant
# ------------------------------------------------------------------------------
echo ""
echo "[1/3] Creating tenant..."
TENANT_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API/tenants" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -d '{"name": "Demo Coffee Roasters"}')
HTTP_CODE=$(echo "$TENANT_RESP" | tail -1)
TENANT_BODY=$(echo "$TENANT_RESP" | head -1)
[[ "$HTTP_CODE" == "201" ]] || { echo "ERROR: POST /tenants returned $HTTP_CODE: $TENANT_BODY"; exit 1; }

API_KEY=$(echo "$TENANT_BODY" | jq -r .api_key)
TENANT_ID=$(echo "$TENANT_BODY" | jq -r .id)
echo "  Tenant ID:  $TENANT_ID"
echo "  API Key:    $API_KEY"

# ------------------------------------------------------------------------------
# Step 2: Create an agent
# ------------------------------------------------------------------------------
echo ""
echo "[2/3] Creating agent..."
AGENT_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API/agents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d @scripts/fixtures/demo_agent.json)
HTTP_CODE=$(echo "$AGENT_RESP" | tail -1)
AGENT_BODY=$(echo "$AGENT_RESP" | head -1)
[[ "$HTTP_CODE" == "202" ]] || { echo "ERROR: POST /agents returned $HTTP_CODE: $AGENT_BODY"; exit 1; }

JOB_ID=$(echo "$AGENT_BODY" | jq -r .job_id)
AGENT_ID=$(echo "$AGENT_BODY" | jq -r .agent_id)
EVENTS_URL=$(echo "$AGENT_BODY" | jq -r .events_url)
echo "  Agent ID:   $AGENT_ID"
echo "  Job ID:     $JOB_ID"
echo "  Events URL: $EVENTS_URL"

# ------------------------------------------------------------------------------
# Step 3: Stream SSE events until job.complete or job.failed
# ------------------------------------------------------------------------------
echo ""
echo "[3/3] Streaming SSE events for job $JOB_ID..."
echo "  (waiting up to 120s for provisioning to complete)"

EVENTS_SEEN=()
while IFS= read -r line; do
  if [[ "$line" == event:* ]]; then
    EVENT_TYPE="${line#event: }"
    EVENTS_SEEN+=("$EVENT_TYPE")
    echo "  event: $EVENT_TYPE"
  fi
  if [[ "${EVENTS_SEEN[*]:-}" == *"job.complete"* ]] || [[ "${EVENTS_SEEN[*]:-}" == *"job.failed"* ]]; then
    break
  fi
done < <(timeout 120 curl -N -s -H "X-API-Key: $API_KEY" "$API$EVENTS_URL" 2>&1 || true)

# ------------------------------------------------------------------------------
# Step 4: Fetch and verify final agent state
# ------------------------------------------------------------------------------
echo ""
echo "Final agent state:"
FINAL=$(curl -s -H "X-API-Key: $API_KEY" "$API/agents/$AGENT_ID")
echo "$FINAL" | jq .

STATUS=$(echo "$FINAL" | jq -r .status)
NEON_PROJECT=$(echo "$FINAL" | jq -r .neon_project_id)
SCHEMA_VERSION=$(echo "$FINAL" | jq -r .schema_version)

echo ""
echo "=== Demo Results ==="
echo "  Tenant ID:      $TENANT_ID"
echo "  Agent ID:       $AGENT_ID"
echo "  Neon Project:   $NEON_PROJECT"
echo "  Schema Version: $SCHEMA_VERSION"
echo "  Final Status:   $STATUS"
echo "  Events seen:    ${EVENTS_SEEN[*]:-none}"

# Validation
[[ "$STATUS" == "ready" ]] || { echo "FAIL: agent status is '$STATUS', expected 'ready'"; exit 1; }
[[ "$NEON_PROJECT" != "null" ]] || { echo "FAIL: neon_project_id is null — provisioning did not complete"; exit 1; }

echo ""
echo "=== DEMO PASSED ==="
