#!/usr/bin/env bash
# smoke_vm.sh — W Chats Deployment Smoke Test
#
# Verifies the live deployment via the local stack + Cloudflare Quick Tunnel:
#   (1) TLS health: API reachable over HTTPS with valid cert (D-05)
#   (2) Widget loader: widget.js reachable on Vercel (D-06)
#   (3) Widget JWT: POST /widget/{id}/config mints a JWT token
#   (4) Chat dispatch: POST /widget/{id}/chat returns a job_id (D-09)
#   (5) SSE single-curl: GET /widget/jobs/{job_id}/events holds the connection
#       for up to 95s (buffered-flush — quick tunnel batches events and delivers
#       them all at once when the server closes the stream); agent.response
#       must be present in the flushed stream (D-09)
#   (6) Retrieve cap: count retrieve tool_call events in the SSE stream; assert <= 2 (D-10)
#
# Prerequisites (run by plan 06 against the live host — NOT run locally):
#   - scripts/start_demo.ps1 has been run on the local PC (uvicorn + runtime worker
#     + cloudflared quick tunnel are running)
#   - API_HOST is set to the https://<random>.trycloudflare.com URL from start_demo.ps1
#   - Widget files are deployed to bantuson.vercel.app/wchats/
#
# Required env vars: none (defaults target the live deployment)
# Optional env vars:
#   API_HOST    — API base URL (default: https://wchats-api.duckdns.org)
#   WIDGET_HOST — Vercel host serving the widget (default: https://bantuson.vercel.app)
#   AGENT_ID    — deployed agent UUID (default: fe230a9d-09f0-4043-b2f1-4506a2ef0059)
#
# Usage:
#   bash scripts/smoke_vm.sh
#   API_HOST=https://<tunnel>.trycloudflare.com bash scripts/smoke_vm.sh
#
# Exit codes:
#   0 — all six sections passed
#   1 — any section failed or timed out

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_HOST="${API_HOST:-https://wchats-api.duckdns.org}"
WIDGET_HOST="${WIDGET_HOST:-https://bantuson.vercel.app}"
AGENT_ID="${AGENT_ID:-fe230a9d-09f0-4043-b2f1-4506a2ef0059}"

echo "=== W Chats VM Smoke Test ==="
echo "API host:    $API_HOST"
echo "Widget host: $WIDGET_HOST"
echo "Agent ID:    $AGENT_ID"
echo ""

# ---------------------------------------------------------------------------
# ALL_PASSED accumulator — set to false on any assertion failure; drives exit code
# ---------------------------------------------------------------------------

ALL_PASSED=true

# ---------------------------------------------------------------------------
# Section 1: TLS health — API reachable over HTTPS with a valid certificate (D-05)
# curl validates certs by default; -k is NOT used intentionally.
# ---------------------------------------------------------------------------

echo "=== Section 1: TLS Health ==="

HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    "$API_HOST/health" 2>/dev/null || echo "000")

if [[ "$HEALTH_STATUS" == "200" ]]; then
    echo "[PASS] TLS health: $API_HOST/health returned 200 with valid cert"
else
    echo "[FAIL] TLS health: $API_HOST/health returned $HEALTH_STATUS (expected 200)"
    echo "       (If 000: host unreachable. If cert error: TLS not configured.)"
    ALL_PASSED=false
fi

echo ""

# ---------------------------------------------------------------------------
# Section 2: Widget loader reachable on Vercel (D-06)
# ---------------------------------------------------------------------------

echo "=== Section 2: Widget.js Reachable ==="

WIDGET_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    "$WIDGET_HOST/wchats/widget.js" 2>/dev/null || echo "000")

if [[ "$WIDGET_STATUS" == "200" ]]; then
    echo "[PASS] Widget loader: $WIDGET_HOST/wchats/widget.js returned 200"
else
    echo "[FAIL] Widget loader: $WIDGET_HOST/wchats/widget.js returned $WIDGET_STATUS (expected 200)"
    ALL_PASSED=false
fi

echo ""

# ---------------------------------------------------------------------------
# Section 3: Widget JWT — POST /widget/{agent_id}/config mints a JWT token
# ---------------------------------------------------------------------------

echo "=== Section 3: Widget JWT Mint ==="

CONFIG_RESP=$(curl -s -X GET \
    --max-time 10 \
    -H "Content-Type: application/json" \
    "$API_HOST/widget/$AGENT_ID/config" 2>/dev/null || echo "")

CONFIG_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    -H "Content-Type: application/json" \
    "$API_HOST/widget/$AGENT_ID/config" 2>/dev/null || echo "000")

if [[ "$CONFIG_STATUS" != "200" ]]; then
    echo "[FAIL] Widget config: GET /widget/$AGENT_ID/config returned $CONFIG_STATUS (expected 200)"
    ALL_PASSED=false
    WIDGET_JWT=""
else
    # Extract JWT from JSON response — never echo the token value
    WIDGET_JWT=$(echo "$CONFIG_RESP" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    token = data.get('jwt', data.get('token', ''))
    print(token)
except Exception:
    print('')
" 2>/dev/null || echo "")

    if [[ -z "$WIDGET_JWT" ]]; then
        echo "[FAIL] Widget config: could not extract JWT from response"
        ALL_PASSED=false
    else
        echo "[PASS] Widget config: JWT token minted (token length: ${#WIDGET_JWT} chars)"
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# Section 4: Chat dispatch — POST /widget/{agent_id}/chat returns a job_id (D-09)
# Uses the JWT from section 3. Expects HTTP 202.
# ---------------------------------------------------------------------------

echo "=== Section 4: Chat Dispatch ==="

JOB_ID=""

if [[ -z "$WIDGET_JWT" ]]; then
    echo "[SKIP] Chat dispatch: skipped (no JWT from section 3)"
    ALL_PASSED=false
else
    CHAT_RESP=$(curl -s -X POST \
        --max-time 15 \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $WIDGET_JWT" \
        -d '{"message": "What is W Chats?"}' \
        "$API_HOST/widget/$AGENT_ID/chat" 2>/dev/null || echo "")

    CHAT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST \
        --max-time 15 \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $WIDGET_JWT" \
        -d '{"message": "What is W Chats?"}' \
        "$API_HOST/widget/$AGENT_ID/chat" 2>/dev/null || echo "000")

    if [[ "$CHAT_STATUS" != "202" && "$CHAT_STATUS" != "200" ]]; then
        echo "[FAIL] Chat dispatch: POST /widget/$AGENT_ID/chat returned $CHAT_STATUS (expected 202)"
        ALL_PASSED=false
    else
        JOB_ID=$(echo "$CHAT_RESP" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('job_id', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

        if [[ -z "$JOB_ID" ]]; then
            echo "[FAIL] Chat dispatch: could not extract job_id from response"
            ALL_PASSED=false
        else
            echo "[PASS] Chat dispatch: job_id extracted (HTTP $CHAT_STATUS)"
        fi
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# Section 5: SSE single-curl (buffered-flush) — hold the SSE connection for
# up to 95s with a single curl call.
#
# Cloudflare Quick Tunnel buffers all SSE events and delivers them all at once
# when the server closes the stream (cloudflare/cloudflared issue #1449).
# The old 18 x --max-time 6 poll loop returned nothing per poll and always
# failed under buffered SSE. The new approach: one --max-time 95 curl holds
# the connection open for the full agent turn duration (D-11 guard = 90s) and
# captures the complete event stream when cloudflared flushes it at stream close.
# Section 6 reads SSE_STREAM from this section unchanged.
# ---------------------------------------------------------------------------

echo "=== Section 5: SSE (buffered-flush — single 95s curl) ==="

AGENT_RESPONSE=false
SSE_STREAM=""

if [[ -z "$JOB_ID" ]]; then
    echo "[SKIP] SSE: skipped (no job_id from section 4)"
    ALL_PASSED=false
else
    echo "  Holding SSE connection on /widget/jobs/$JOB_ID/events (up to 95s)..."

    SSE_STREAM=$(curl -s --max-time 95 -N \
        "$API_HOST/widget/jobs/$JOB_ID/events" 2>/dev/null || echo "")

    if echo "$SSE_STREAM" | grep -q '"event_type":"agent.response"'; then
        AGENT_RESPONSE=true
        echo "[PASS] SSE: agent.response received"
    elif echo "$SSE_STREAM" | grep -q '"event_type":"agent.failed"'; then
        echo "[FAIL] SSE: agent.failed event received"
        ALL_PASSED=false
    else
        echo "[FAIL] SSE: agent.response not received within 95s"
        ALL_PASSED=false
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# Section 6: Retrieve cap — count agent.tool_call events with retrieve tool name
# Assert count <= 2 (D-10 Voyage 3 RPM free tier guard).
# ---------------------------------------------------------------------------

echo "=== Section 6: Retrieve Tool Call Cap (<= 2) ==="

if [[ -z "$JOB_ID" ]] || [[ "$AGENT_RESPONSE" == "false" ]]; then
    echo "[SKIP] Retrieve cap: skipped (no SSE stream captured)"
    if [[ "$ALL_PASSED" == "true" ]]; then
        ALL_PASSED=false
    fi
else
    RETRIEVE_COUNT=$(echo "$SSE_STREAM" | \
        grep '"event_type":"agent.tool_call"' | \
        grep -c '"retrieve"' || echo "0")

    echo "  Retrieve tool calls observed: $RETRIEVE_COUNT"

    if [[ "$RETRIEVE_COUNT" -le 2 ]]; then
        echo "[PASS] Retrieve cap: $RETRIEVE_COUNT retrieve call(s) <= 2 (D-10)"
    else
        echo "[FAIL] Retrieve cap: $RETRIEVE_COUNT retrieve calls (expected <= 2, D-10)"
        ALL_PASSED=false
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# Final status
# ---------------------------------------------------------------------------

echo "=== W Chats VM Smoke Test: Summary ==="
echo ""
echo "  API host:    $API_HOST"
echo "  Widget host: $WIDGET_HOST"
echo "  Agent ID:    $AGENT_ID"
echo ""
echo "  Sections:"
echo "    1. TLS health         (/health, valid cert)"
echo "    2. Widget.js          (Vercel static delivery)"
echo "    3. Widget JWT         (GET /widget/\$AGENT_ID/config)"
echo "    4. Chat dispatch      (POST /widget/\$AGENT_ID/chat -> job_id)"
echo "    5. SSE agent.response (GET /widget/jobs/\$JOB_ID/events)"
echo "    6. Retrieve cap       (tool_call count <= 2)"
echo ""

if [[ "$ALL_PASSED" == "true" ]]; then
    echo "=== Smoke: PASSED ==="
    exit 0
else
    echo "=== Smoke: FAILED (see [FAIL] lines above) ==="
    exit 1
fi
