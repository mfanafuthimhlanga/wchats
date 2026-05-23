#!/usr/bin/env bash
# demo_m5.sh — Veridian M5 validation chain smoke test
#
# Prerequisites (ALL local — no Docker):
#   1. Redis:    redis-server
#   2. Postgres: local install (e.g. postgres running on localhost:5432)
#   3. API:      uvicorn app.main:app --port 8000
#   4. Worker:   celery -A app.worker.celery_app worker --pool=solo -Q runtime,pipeline
#
#   Langfuse env vars (required for trace walkthrough — Task 2):
#     export LANGFUSE_PUBLIC_KEY=<your-key>
#     export LANGFUSE_SECRET_KEY=<your-key>
#     export LANGFUSE_HOST=https://cloud.langfuse.com
#
#   jq must be installed: brew install jq / apt-get install jq
#
# Usage:
#   AGENT_ID=<uuid> bash scripts/demo_m5.sh
#   BASE_URL=http://localhost:8000 AGENT_ID=<uuid> bash scripts/demo_m5.sh
#
# Exit codes:
#   0 — demo passed (all four events received within deadline)
#   1 — any step failed or events not received within 90s

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
LANGFUSE_HOST="${LANGFUSE_HOST:-https://cloud.langfuse.com}"

AGENT_ID="${AGENT_ID:-}"
if [[ -z "$AGENT_ID" ]]; then
    echo "ERROR: AGENT_ID env var is required."
    echo "  Usage: AGENT_ID=<uuid> bash scripts/demo_m5.sh"
    exit 1
fi

echo "=== M5 Demo: Validation Chain (adversarial widget query) ==="
echo "Agent:    $AGENT_ID"
echo "Base URL: $BASE_URL"
echo ""

# ---------------------------------------------------------------------------
# Step 1: GET /widget/{agent_id}/config — mint a short-lived Bearer JWT
# ---------------------------------------------------------------------------
echo "[Step 1] Minting widget JWT via GET $BASE_URL/widget/$AGENT_ID/config ..."

CONFIG_RESPONSE=$(curl -sf --max-time 10 \
    "$BASE_URL/widget/$AGENT_ID/config")

JWT=$(echo "$CONFIG_RESPONSE" | jq -r '.jwt')

if [[ -z "$JWT" || "$JWT" == "null" ]]; then
    echo "ERROR: Failed to extract JWT from config response."
    echo "Response: $CONFIG_RESPONSE"
    exit 1
fi

echo "  JWT minted (first 20 chars): ${JWT:0:20}..."
echo ""

# ---------------------------------------------------------------------------
# Step 2: POST /widget/{agent_id}/chat — adversarial (false-premise) question
# The body uses a leading question designed to provoke a non-trivial Auditor
# verdict (partial/ungrounded) — ROADMAP M5 success criterion 2.
# ---------------------------------------------------------------------------
echo "[Step 2] Sending adversarial question to POST $BASE_URL/widget/$AGENT_ID/chat ..."

ADVERSARIAL_QUESTION="Your product guarantees a full refund within 24 hours, right? Can you confirm that policy and tell me how to claim it immediately?"

CHAT_BODY=$(jq -n --arg msg "$ADVERSARIAL_QUESTION" '{"message": $msg}')
CHAT_RESPONSE=$(curl -sf --max-time 15 \
    -X POST "$BASE_URL/widget/$AGENT_ID/chat" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -d "$CHAT_BODY")

JOB_ID=$(echo "$CHAT_RESPONSE" | jq -r '.job_id')

if [[ -z "$JOB_ID" || "$JOB_ID" == "null" ]]; then
    echo "ERROR: Failed to extract job_id from chat response."
    echo "Response: $CHAT_RESPONSE"
    exit 1
fi

echo "  Job dispatched: $JOB_ID"
echo "  Adversarial question: \"${ADVERSARIAL_QUESTION}\""
echo ""

# ---------------------------------------------------------------------------
# Step 3: Poll GET /widget/jobs/{job_id}/events (PUBLIC — no X-API-Key)
# Wait up to 90s for all four events: agent.response, gatekeeper.complete,
# auditor.complete, strategist.complete
# ---------------------------------------------------------------------------
echo "[Step 3] Polling $BASE_URL/widget/jobs/$JOB_ID/events (90s deadline) ..."
echo "  Waiting for: agent.response → gatekeeper.complete → auditor.complete → strategist.complete"
echo ""

DEADLINE=$((SECONDS + 90))

FOUND_AGENT_RESPONSE=false
FOUND_GATEKEEPER=false
FOUND_AUDITOR=false
FOUND_STRATEGIST=false

while [ $SECONDS -lt $DEADLINE ]; do
    EVENTS=$(curl -sf --max-time 10 \
        "$BASE_URL/widget/jobs/$JOB_ID/events" 2>/dev/null || true)

    if [[ -z "$EVENTS" ]]; then
        sleep 2
        continue
    fi

    # Check for agent.response
    if [[ "$FOUND_AGENT_RESPONSE" == "false" ]] && echo "$EVENTS" | grep -q '"agent\.response"'; then
        FOUND_AGENT_RESPONSE=true
        AGENT_ANSWER=$(echo "$EVENTS" | grep '"agent\.response"' | jq -r '.payload.response // .payload.text // "(see raw event)"' 2>/dev/null || echo "(parsing skipped)")
        echo "  [RECEIVED] agent.response"
        echo "    Response preview: ${AGENT_ANSWER:0:120}..."
    fi

    # Check for gatekeeper.complete
    if [[ "$FOUND_GATEKEEPER" == "false" ]] && echo "$EVENTS" | grep -q '"gatekeeper\.complete"'; then
        FOUND_GATEKEEPER=true
        GK_VERDICT=$(echo "$EVENTS" | grep '"gatekeeper\.complete"' | jq -r '.payload.verdict // "(n/a)"' 2>/dev/null || echo "(parsing skipped)")
        GK_CONFIDENCE=$(echo "$EVENTS" | grep '"gatekeeper\.complete"' | jq -r '.payload.confidence // "(n/a)"' 2>/dev/null || echo "(parsing skipped)")
        GK_REASON=$(echo "$EVENTS" | grep '"gatekeeper\.complete"' | jq -r '.payload.reason // "(n/a)"' 2>/dev/null || echo "(parsing skipped)")
        echo "  [RECEIVED] gatekeeper.complete"
        echo "    verdict=$GK_VERDICT  confidence=$GK_CONFIDENCE"
        echo "    reason:  $GK_REASON"
    fi

    # Check for auditor.complete
    if [[ "$FOUND_AUDITOR" == "false" ]] && echo "$EVENTS" | grep -q '"auditor\.complete"'; then
        FOUND_AUDITOR=true
        AU_VERDICT=$(echo "$EVENTS" | grep '"auditor\.complete"' | jq -r '.payload.verdict // "(n/a)"' 2>/dev/null || echo "(parsing skipped)")
        AU_CONFIDENCE=$(echo "$EVENTS" | grep '"auditor\.complete"' | jq -r '.payload.confidence // "(n/a)"' 2>/dev/null || echo "(parsing skipped)")
        AU_REASON=$(echo "$EVENTS" | grep '"auditor\.complete"' | jq -r '.payload.reason // "(n/a)"' 2>/dev/null || echo "(parsing skipped)")
        echo "  [RECEIVED] auditor.complete"
        echo "    verdict=$AU_VERDICT  confidence=$AU_CONFIDENCE"
        echo "    reason:  $AU_REASON"
    fi

    # Check for strategist.complete
    if [[ "$FOUND_STRATEGIST" == "false" ]] && echo "$EVENTS" | grep -q '"strategist\.complete"'; then
        FOUND_STRATEGIST=true
        ST_VERDICT=$(echo "$EVENTS" | grep '"strategist\.complete"' | jq -r '.payload.verdict // "(n/a)"' 2>/dev/null || echo "(parsing skipped)")
        ST_CONFIDENCE=$(echo "$EVENTS" | grep '"strategist\.complete"' | jq -r '.payload.confidence // "(n/a)"' 2>/dev/null || echo "(parsing skipped)")
        ST_REASON=$(echo "$EVENTS" | grep '"strategist\.complete"' | jq -r '.payload.reason // "(n/a)"' 2>/dev/null || echo "(parsing skipped)")
        echo "  [RECEIVED] strategist.complete"
        echo "    verdict=$ST_VERDICT  confidence=$ST_CONFIDENCE"
        echo "    reason:  $ST_REASON"
    fi

    # All four events found — exit loop
    if [[ "$FOUND_AGENT_RESPONSE" == "true" && "$FOUND_GATEKEEPER" == "true" && "$FOUND_AUDITOR" == "true" && "$FOUND_STRATEGIST" == "true" ]]; then
        break
    fi

    sleep 2
done

echo ""

# ---------------------------------------------------------------------------
# Evaluate results
# ---------------------------------------------------------------------------
MISSING=()
[[ "$FOUND_AGENT_RESPONSE" == "false" ]] && MISSING+=("agent.response")
[[ "$FOUND_GATEKEEPER"     == "false" ]] && MISSING+=("gatekeeper.complete")
[[ "$FOUND_AUDITOR"        == "false" ]] && MISSING+=("auditor.complete")
[[ "$FOUND_STRATEGIST"     == "false" ]] && MISSING+=("strategist.complete")

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "ERROR: Timed out (90s). Missing events: ${MISSING[*]}"
    echo "  job_id=$JOB_ID"
    exit 1
fi

# ---------------------------------------------------------------------------
# All four events received — print Langfuse trace pointer (VAL-07)
# ---------------------------------------------------------------------------
echo "All four validation events confirmed."
echo ""
echo "View verdict traces at $LANGFUSE_HOST/traces/ for job $JOB_ID"
echo "  Walk the three generation spans: gatekeeper-judge, auditor-judge, strategist-judge"
echo "  Confirm each span shows: structured verdict payload, Haiku model + cost"
echo "  Confirm auditor-judge shows citation spans + partial/ungrounded for the adversarial query"
echo ""
echo "=== M5 Demo: PASSED ==="
