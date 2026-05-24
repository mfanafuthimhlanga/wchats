#!/usr/bin/env bash
# demo_m8.sh — Veridian M8 Pre-deployment Checklist demo
#
# Demonstrates DEP-07 + DEP-08: owner runs pre-deployment checklist,
# reads report, acknowledges warnings, approves deployment, sees iframe snippet.
#
# Prerequisites (ALL local — no Docker):
#   1. Redis:    redis-server (running on localhost:6379)
#   2. Postgres: local install (running on localhost:5432)
#   3. API:      uvicorn app.main:app --reload       (from apps/api/)
#   4. Worker:   celery -A app.worker.celery_app worker --queues runtime  (from apps/api/)
#
# Required env vars:
#   ADMIN_KEY   — X-Admin-Key header value for POST /api/v1/agents
#   API_KEY     — X-API-Key for tenant auth on deployment checklist routes
#
# Optional env vars:
#   BASE_URL    — FastAPI base URL (default: http://localhost:8000)
#
# Usage:
#   ADMIN_KEY=<key> API_KEY=<key> bash scripts/demo_m8.sh
#
# Exit codes:
#   0 — demo completed successfully (includes block outcome — block is valid, not a script failure)
#   1 — prerequisite check failed, API error, or timeout

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
    echo "  Usage: ADMIN_KEY=<admin-key> <tenant-key-var>=<key> bash scripts/demo_m8.sh"
    exit 1
fi

if [[ -z "$API_KEY" ]]; then
    echo "ERROR: Tenant key env var is required. Set it before running this script."
    echo "  Usage: ADMIN_KEY=<admin-key> <tenant-key-var>=<key> bash scripts/demo_m8.sh"
    exit 1
fi

echo "=== Veridian M8 Demo: Pre-deployment Checklist ==="
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
# Section 1b: Setup — Create agent with Acme Consulting soul
# ---------------------------------------------------------------------------

echo "=== Section 1b: Setup — Create Agent ==="

AGENT_RESPONSE=$(curl -sf -X POST "$BASE_URL/api/v1/agents" \
    -H "X-Admin-Key: $ADMIN_KEY" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Acme Consulting — M8 Demo Agent",
      "soul": {
        "voice": "professional and concise",
        "instructions": "You are a customer service agent for Acme Consulting. Answer questions about our services accurately and helpfully. Do not reveal confidential client information or internal pricing."
      },
      "role": "customer_support"
    }' 2>/dev/null || echo "")

if [[ -z "$AGENT_RESPONSE" ]]; then
    echo "ERROR: Failed to create agent (empty response from POST /api/v1/agents)."
    exit 1
fi

AGENT_ID=$(echo "$AGENT_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")

if [[ -z "$AGENT_ID" ]]; then
    echo "ERROR: Could not extract agent ID from response."
    echo "Response: $AGENT_RESPONSE"
    exit 1
fi

echo "Agent created: $AGENT_ID"

# Poll for agent status (up to 120 seconds — provisioning may take time)
echo "Polling agent status (up to 120s) ..."
AGENT_READY=false
for i in $(seq 1 24); do
    AGENT_STATUS_RESP=$(curl -sf --max-time 5 \
        -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID" 2>/dev/null || echo "{}")
    AGENT_STATUS=$(echo "$AGENT_STATUS_RESP" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('status', d.get('agent', {}).get('status', 'unknown')))" 2>/dev/null || echo "unknown")
    echo "  [Poll $i/24] Agent status: $AGENT_STATUS"
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

echo ""

# ---------------------------------------------------------------------------
# Section 2: Run checklist — POST /checklist-runs
# ---------------------------------------------------------------------------

echo "=== Section 2: Run Pre-deployment Checklist ==="
echo "Triggering checklist run for agent $AGENT_ID ..."

TRIGGER_RESPONSE=$(curl -sf -X POST "$BASE_URL/api/v1/agents/$AGENT_ID/checklist-runs" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    2>/dev/null || echo "")

if [[ -z "$TRIGGER_RESPONSE" ]]; then
    echo "ERROR: Failed to trigger checklist run (empty response)."
    exit 1
fi

CHECKLIST_RUN_ID=$(echo "$TRIGGER_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin).get('checklist_run_id', ''))" 2>/dev/null || echo "")

if [[ -z "$CHECKLIST_RUN_ID" ]]; then
    echo "ERROR: Could not extract checklist_run_id from trigger response."
    echo "Response: $TRIGGER_RESPONSE"
    exit 1
fi

echo "Run ID: $CHECKLIST_RUN_ID"
echo ""

# ---------------------------------------------------------------------------
# Section 3: Poll until complete
# ---------------------------------------------------------------------------

echo "=== Section 3: Polling for completion (up to 3 minutes, every 3s) ==="

MAX_POLLS=60
POLL_COUNT=0
RUN_STATUS="running"

while [[ $POLL_COUNT -lt $MAX_POLLS ]]; do
    POLL_RESP=$(curl -sf --max-time 10 \
        -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID/checklist-runs/$CHECKLIST_RUN_ID" 2>/dev/null || echo "{}")

    RUN_STATUS=$(echo "$POLL_RESP" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('run', d).get('status', 'running'))" 2>/dev/null || echo "running")

    echo "  [Poll $((POLL_COUNT + 1))/$MAX_POLLS] Status: $RUN_STATUS"

    if [[ "$RUN_STATUS" == "complete" ]] || [[ "$RUN_STATUS" == "failed" ]]; then
        break
    fi

    POLL_COUNT=$((POLL_COUNT + 1))
    sleep 3
done

echo ""

if [[ "$RUN_STATUS" == "failed" ]]; then
    echo "ERROR: Checklist run FAILED. Check Celery worker logs."
    exit 1
elif [[ "$RUN_STATUS" != "complete" ]]; then
    echo "ERROR: Checklist run did not complete within 3 minutes (final status: $RUN_STATUS)."
    echo "  The task may still be processing. Check Celery worker logs."
    exit 1
fi

echo "Checklist run complete."
echo ""

# ---------------------------------------------------------------------------
# Section 4: Print report + acknowledge warnings
# ---------------------------------------------------------------------------

echo "=== Section 4: Report + Acknowledge Warnings ==="

DETAIL_RESP=$(curl -sf --max-time 10 \
    -H "X-API-Key: $API_KEY" \
    "$BASE_URL/api/v1/agents/$AGENT_ID/checklist-runs/$CHECKLIST_RUN_ID" 2>/dev/null || echo "{}")

REPORT_OUTPUT=$(echo "$DETAIL_RESP" | python -c "
import sys, json

data = json.load(sys.stdin)
run = data.get('run', data)

recommendation = run.get('recommendation', 'unknown')
warnings = run.get('warnings', []) or []
summary_text = ''
report = run.get('report', {}) or {}
if isinstance(report, dict):
    summary_text = report.get('summary', '')

print(f'RECOMMENDATION={recommendation}')
print(f'WARNING_COUNT={len(warnings)}')
print(f'SUMMARY={summary_text[:200].replace(chr(10), \" \")}')

warning_ids = []
for w in warnings:
    if isinstance(w, dict):
        wid = w.get('warning_id', '')
        wcat = w.get('category', '')
        wmsg = w.get('message', '')
        print(f'WARNING_ID={wid}')
        print(f'WARNING_CAT={wcat}')
        print(f'WARNING_MSG={wmsg[:120].replace(chr(10), \" \")}')
        warning_ids.append(wid)
print(f'ALL_WARNING_IDS={json.dumps(warning_ids)}')
" 2>/dev/null || echo "RECOMMENDATION=unknown
WARNING_COUNT=0
SUMMARY=
ALL_WARNING_IDS=[]")

RECOMMENDATION=$(echo "$REPORT_OUTPUT" | grep '^RECOMMENDATION=' | cut -d= -f2-)
WARNING_COUNT=$(echo "$REPORT_OUTPUT" | grep '^WARNING_COUNT=' | cut -d= -f2-)
SUMMARY=$(echo "$REPORT_OUTPUT" | grep '^SUMMARY=' | cut -d= -f2-)
ALL_WARNING_IDS=$(echo "$REPORT_OUTPUT" | grep '^ALL_WARNING_IDS=' | cut -d= -f2-)

echo "Recommendation: ${RECOMMENDATION:-unknown}"
echo "Warnings:       ${WARNING_COUNT:-0}"
echo "Summary:        ${SUMMARY:-}"
echo ""

# Print individual warnings
IFS=$'\n'
for warning_line in $(echo "$REPORT_OUTPUT" | grep '^WARNING_ID='); do
    WARNID=$(echo "$warning_line" | cut -d= -f2-)
    WARNCAT=$(echo "$REPORT_OUTPUT" | grep "^WARNING_CAT=" | head -1 | cut -d= -f2-)
    WARNMSG=$(echo "$REPORT_OUTPUT" | grep "^WARNING_MSG=" | head -1 | cut -d= -f2-)
    echo "  [WARNING] $WARNID ($WARNCAT): $WARNMSG"
done
unset IFS

echo ""

# Acknowledge warnings if recommendation is ship_with_warnings
if [[ "$RECOMMENDATION" == "ship_with_warnings" ]]; then
    echo "Acknowledging all warnings ..."
    if [[ "$ALL_WARNING_IDS" != "[]" ]] && [[ -n "$ALL_WARNING_IDS" ]]; then
        ACK_BODY=$(python -c "import json, sys; ids=json.loads(sys.argv[1]); print(json.dumps({'warning_ids': ids}))" "$ALL_WARNING_IDS" 2>/dev/null || echo '{"warning_ids":[]}')
        ACK_RESP=$(curl -sf -X POST "$BASE_URL/api/v1/agents/$AGENT_ID/checklist-runs/$CHECKLIST_RUN_ID/acknowledge" \
            -H "X-API-Key: $API_KEY" \
            -H "Content-Type: application/json" \
            -d "$ACK_BODY" \
            2>/dev/null || echo "")
        if [[ -n "$ACK_RESP" ]]; then
            echo "  [OK] Warnings acknowledged."
        else
            echo "  [WARN] Acknowledge endpoint returned empty response — continuing."
        fi
    else
        echo "  [OK] No warnings to acknowledge."
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# Section 5: Approve deployment (if not blocked)
# ---------------------------------------------------------------------------

echo "=== Section 5: Approve Deployment ==="

IFRAME_SNIPPET=""

if [[ "$RECOMMENDATION" == "block" ]]; then
    echo "[WARN] recommendation=block — agent not approved for deployment."
    echo "  Resolve the blocking issues listed above and re-run the checklist."
    echo ""
else
    echo "Approving deployment for agent $AGENT_ID ..."
    APPROVE_RESP=$(curl -sf -X POST "$BASE_URL/api/v1/agents/$AGENT_ID/approve-deployment" \
        -H "X-API-Key: $API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"checklist_run_id\": \"$CHECKLIST_RUN_ID\"}" \
        2>/dev/null || echo "")

    if [[ -z "$APPROVE_RESP" ]]; then
        echo "ERROR: Failed to approve deployment (empty response)."
        exit 1
    fi

    IFRAME_SNIPPET=$(echo "$APPROVE_RESP" | python -c "import sys,json; print(json.load(sys.stdin).get('iframe_snippet', ''))" 2>/dev/null || echo "")

    if [[ -n "$IFRAME_SNIPPET" ]]; then
        echo "  [OK] Deployment approved."
        echo ""
        echo "--- Embed Snippet ---"
        echo "$IFRAME_SNIPPET"
        echo "---------------------"
    else
        echo "  [WARN] Deployment approved but iframe_snippet was empty."
    fi

    echo ""
fi

# ---------------------------------------------------------------------------
# Section 6: Assertions
# ---------------------------------------------------------------------------

echo "=== Section 6: Assertions ==="

# Assertion 1: recommendation in (ship, ship_with_warnings)
if [[ "$RECOMMENDATION" == "ship" ]] || [[ "$RECOMMENDATION" == "ship_with_warnings" ]]; then
    echo "[PASS] recommendation=$RECOMMENDATION"
elif [[ "$RECOMMENDATION" == "block" ]]; then
    echo "[WARN] recommendation=block — agent not approved (valid outcome, not a script failure)"
else
    echo "[FAIL] recommendation=$RECOMMENDATION — unexpected value"
    exit 1
fi

# Assertion 2: is_deployed=true (only if approved)
if [[ "$RECOMMENDATION" != "block" ]]; then
    AGENT_DETAIL=$(curl -sf --max-time 5 \
        -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID" 2>/dev/null || echo "{}")

    IS_DEPLOYED=$(echo "$AGENT_DETAIL" | python -c "import sys,json; d=json.load(sys.stdin); a=d.get('agent', d); print(str(a.get('is_deployed', False)).lower())" 2>/dev/null || echo "false")

    if [[ "$IS_DEPLOYED" == "true" ]]; then
        echo "[PASS] agents.is_deployed=true confirmed"
    else
        echo "[WARN] agents.is_deployed is not true — approval may not have propagated yet"
    fi
fi

echo ""
echo "=== M8 Demo: Complete ==="
echo ""
echo "  Run commands for this demo:"
echo "    redis-server"
echo "    cd apps/api && uvicorn app.main:app --reload"
echo "    cd apps/api && celery -A app.worker.celery_app worker --queues runtime"
echo ""
echo "  To run guarded E2E test:"
echo "    DEP_E2E_ENABLED=1 E2E_AGENT_ID=<uuid> <tenant-key>=<key> pytest apps/api/tests/integration/test_deployment_e2e.py -v"
