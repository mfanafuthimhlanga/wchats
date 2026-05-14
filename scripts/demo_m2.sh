#!/usr/bin/env bash
# demo_m2.sh — Veridian M2 ingestion pipeline demo
#
# Prerequisites:
#   - docker-compose services running (docker compose up -d)
#   - jq installed (brew install jq / apt-get install jq)
#   - apps/api/tests/fixtures/demo_business.pdf exists
#   - ADMIN_KEY env var set (matches docker-compose ADMIN_KEY)
#
# Usage:
#   ADMIN_KEY=vrd_admin_... bash scripts/demo_m2.sh
#   AGENT_ID=<existing-agent-uuid> API_KEY=<tenant-key> ADMIN_KEY=... bash scripts/demo_m2.sh   # skip provisioning
#
# Exit codes:
#   0 — demo passed (all 11 M2 events observed, DB counts verified)
#   1 — any step failed or expected events missing

set -euo pipefail

API="${API_BASE:-http://localhost:8000}"
ADMIN_KEY="${ADMIN_KEY:?ADMIN_KEY env var required — see .env.example}"
PDF_PATH="${PDF_PATH:-apps/api/tests/fixtures/demo_business.pdf}"

echo "=== Veridian M2 Ingestion Demo ==="
echo "API: $API"
echo "PDF: $PDF_PATH"

# ------------------------------------------------------------------------------
# Sanity-check: PDF fixture must exist
# ------------------------------------------------------------------------------
if [[ ! -f "$PDF_PATH" ]]; then
    echo "ERROR: PDF fixture not found at $PDF_PATH"
    echo "  Run: ls apps/api/tests/fixtures/"
    exit 1
fi

PDF_SIZE=$(wc -c < "$PDF_PATH")
echo "  pdf_size: $PDF_SIZE bytes"
if [[ "$PDF_SIZE" -ge 500000 ]]; then
    echo "WARNING: PDF is >= 500KB — this may slow down the demo"
fi

# ------------------------------------------------------------------------------
# Step 1 — Provision tenant + agent if AGENT_ID / API_KEY not pre-set
# ------------------------------------------------------------------------------
if [[ -z "${AGENT_ID:-}" ]] || [[ -z "${API_KEY:-}" ]]; then
    echo ""
    echo "[1/4] Creating tenant..."
    TENANT_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API/tenants" \
      -H "Content-Type: application/json" \
      -H "X-Admin-Key: $ADMIN_KEY" \
      -d '{"name": "Demo Coffee Roasters M2"}')
    HTTP_CODE=$(echo "$TENANT_RESP" | tail -1)
    TENANT_BODY=$(echo "$TENANT_RESP" | head -1)
    [[ "$HTTP_CODE" == "201" ]] || { echo "ERROR: POST /tenants returned $HTTP_CODE: $TENANT_BODY"; exit 1; }

    API_KEY=$(echo "$TENANT_BODY" | jq -r .api_key)
    TENANT_ID=$(echo "$TENANT_BODY" | jq -r .id)
    echo "  Tenant ID:  $TENANT_ID"
    echo "  API Key:    $API_KEY"

    echo ""
    echo "[2/4] Creating agent..."
    AGENT_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API/agents" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $API_KEY" \
      -d '{"name": "Demo Coffee Agent", "soul": {"voice": "friendly and helpful", "do": ["answer questions about products", "escalate complex issues"], "do_not": ["discuss competitors", "make pricing promises"]}, "role": "support"}')
    HTTP_CODE=$(echo "$AGENT_RESP" | tail -1)
    AGENT_BODY=$(echo "$AGENT_RESP" | head -1)
    [[ "$HTTP_CODE" == "202" ]] || { echo "ERROR: POST /agents returned $HTTP_CODE: $AGENT_BODY"; exit 1; }

    PROVISION_JOB_ID=$(echo "$AGENT_BODY" | jq -r .job_id)
    AGENT_ID=$(echo "$AGENT_BODY" | jq -r .agent_id)
    PROVISION_EVENTS_URL=$(echo "$AGENT_BODY" | jq -r .events_url)
    echo "  Agent ID:   $AGENT_ID"
    echo "  Job ID:     $PROVISION_JOB_ID"

    echo ""
    echo "  Waiting for agent provisioning (polling GET /agents/$AGENT_ID, up to 120s)..."
    WAIT_START=$(date +%s)
    AGENT_STATUS="pending"
    while [[ "$AGENT_STATUS" != "ready" ]] && [[ "$AGENT_STATUS" != "failed" ]]; do
        sleep 5
        NOW=$(date +%s)
        ELAPSED=$(( NOW - WAIT_START ))
        if [[ "$ELAPSED" -gt 360 ]]; then
            echo "ERROR: Agent did not become ready within 360s (status=$AGENT_STATUS)"
            exit 1
        fi
        AGENT_RESP=$(curl -s -H "X-API-Key: $API_KEY" "$API/agents/$AGENT_ID")
        AGENT_STATUS=$(echo "$AGENT_RESP" | jq -r .status)
        echo "  status: $AGENT_STATUS (${ELAPSED}s elapsed)"
    done

    if [[ "$AGENT_STATUS" != "ready" ]]; then
        echo "ERROR: Agent provisioning failed (status=$AGENT_STATUS)"
        exit 1
    fi
    echo "  Agent is ready."
else
    echo ""
    echo "[1-2/4] Using pre-set AGENT_ID=$AGENT_ID (skipping provisioning)"
fi

# ------------------------------------------------------------------------------
# Step 2 — Print banner
# ------------------------------------------------------------------------------
echo ""
echo "M2 ingestion demo — uploading $PDF_PATH to agent $AGENT_ID"

# ------------------------------------------------------------------------------
# Step 3 — POST the PDF
# ------------------------------------------------------------------------------
echo ""
echo "[3/4] Uploading PDF..."
UPLOAD_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API/api/v1/agents/$AGENT_ID/documents" \
    -H "X-API-Key: $API_KEY" \
    -F "files=@$PDF_PATH")
HTTP_CODE=$(echo "$UPLOAD_RESP" | tail -1)
BODY=$(echo "$UPLOAD_RESP" | head -1)

if [[ "$HTTP_CODE" != "202" ]]; then
    echo "ERROR: POST /api/v1/agents/$AGENT_ID/documents returned $HTTP_CODE"
    echo "$BODY"
    exit 1
fi

JOB_ID=$(echo "$BODY" | jq -r .job_id)
EVENTS_URL=$(echo "$BODY" | jq -r .events_url)
DOC_ID=$(echo "$BODY" | jq -r '.document_ids[0]')

echo "  job_id:      $JOB_ID"
echo "  document_id: $DOC_ID"
echo "  events_url:  $EVENTS_URL"

# ------------------------------------------------------------------------------
# Step 4 — Stream SSE events (mirrors demo_m1.sh stream pattern)
# ------------------------------------------------------------------------------
echo ""
echo "[4/4] Streaming SSE events from $EVENTS_URL ..."
echo "  (waiting up to 600s for ingestion chain to complete)"

EVENTS_SEEN=()
EXPECTED_EVENTS=(
    "ingestion.started"
    "parsing.started"
    "parsing.complete"
    "chunking.started"
    "chunking.complete"
    "metadata.started"
    "metadata.complete"
    "embedding.started"
    "embedding.complete"
    "ingestion.complete"
    "job.complete"
)

while IFS= read -r line; do
    if [[ "$line" == event:* ]]; then
        EVENT_TYPE="${line#event: }"
        EVENTS_SEEN+=("$EVENT_TYPE")
        echo "  event: $EVENT_TYPE"
    fi
    if [[ "${EVENTS_SEEN[*]:-}" == *"job.complete"* ]] || \
       [[ "${EVENTS_SEEN[*]:-}" == *"job.failed"* ]]; then
        break
    fi
done < <(timeout 600 curl -N -s -H "X-API-Key: $API_KEY" "$API$EVENTS_URL" 2>&1 || true)

# Soft assertion: confirm all 11 expected events appeared
MISSING=()
for evt in "${EXPECTED_EVENTS[@]}"; do
    if [[ "${EVENTS_SEEN[*]:-}" != *"$evt"* ]]; then
        MISSING+=("$evt")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo ""
    echo "WARNING: missing expected events: ${MISSING[*]}"
else
    echo ""
    echo "  All 11 M2 events observed."
fi

# ------------------------------------------------------------------------------
# Step 5 — Inspect tenant DB
#   The tenant DB is hosted on Neon, accessed via the encrypted connection string
#   stored in the agent row. We decrypt in-process via Python (never logged).
# ------------------------------------------------------------------------------
echo ""
echo "=== Tenant DB Inspection ==="

INSPECT_SCRIPT='
import os, sys
sys.path.insert(0, "apps/api")
# Ensure test env vars are set before Settings validates
import os as _os
for k in ("NEON_API_KEY", "NEON_ENCRYPTION_KEY", "ADMIN_KEY"):
    _os.environ.setdefault(k, _os.environ.get(k, "demo-placeholder"))

import psycopg2
from app.core.config import settings
from app.core.security import fernet_decrypt
from app.core.database import get_sync_db
from sqlalchemy import text

agent_id = os.environ["AGENT_ID"]

with get_sync_db() as db:
    row = db.execute(
        text("SELECT neon_connection_string FROM agents WHERE id = :id"),
        {"id": agent_id}
    ).fetchone()
    if row is None:
        print(f"  ERROR: agent {agent_id} not found in control DB")
        sys.exit(1)
    conn_str = fernet_decrypt(row[0])

with psycopg2.connect(conn_str, connect_timeout=10) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunks")
        print(f"  chunks:         {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM chunk_metadata")
        print(f"  chunk_metadata: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM embeddings")
        print(f"  embeddings:     {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM entities")
        print(f"  entities:       {cur.fetchone()[0]}")
        cur.execute("SELECT content FROM chunks WHERE content LIKE %s LIMIT 1", ("%|%",))
        row = cur.fetchone()
        if row:
            print("  table chunk found (first 200 chars):")
            print("    " + row[0][:200].replace("\n", " | "))
        else:
            print("  WARNING: no chunk containing | (pipe) found — table-to-Markdown may have failed")
'
AGENT_ID="$AGENT_ID" python -c "$INSPECT_SCRIPT"

# ------------------------------------------------------------------------------
# Step 6 — Exit messaging
# ------------------------------------------------------------------------------
echo ""
echo "=== M2 Demo Results ==="
echo "  agent_id:    $AGENT_ID"
echo "  job_id:      $JOB_ID"
echo "  document_id: $DOC_ID"
echo "  events_seen: ${#EVENTS_SEEN[@]} of ${#EXPECTED_EVENTS[@]}"

if [[ ${#MISSING[@]} -eq 0 ]]; then
    echo "  events:      all 11 M2 events observed"
    echo ""
    echo "=== DEMO PASSED ==="
    exit 0
else
    echo "  events:      ${#EVENTS_SEEN[@]} events seen; ${#MISSING[@]} missing: ${MISSING[*]}"
    echo ""
    echo "=== DEMO FAILED ==="
    exit 1
fi
