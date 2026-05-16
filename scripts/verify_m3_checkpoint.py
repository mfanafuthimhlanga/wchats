#!/usr/bin/env python3
"""
M3 Checkpoint Verification — automated E2E test of the hybrid retrieval pipeline.

Steps:
  1. Apply Alembic migration 0003 (agents.retrieval_strategy column)
  2. Create a fresh tenant + agent via the REST API
  3. Start a Celery worker subprocess (pipeline + runtime queues)
  4. Wait for agent status=ready (Neon provisioned + tenant schema applied)
  5. Seed 20 test chunks + real Voyage embeddings into the Neon tenant DB
  6. Submit query via POST /agents/{id}/query
  7. Poll GET /jobs/{job_id}/events until query.complete
  8. Validate all four result sets are non-empty and vector/BM25 diverge

Exit codes:
  0 — PASSED
  1 — FAILED (see output for details)

Usage (from repo root):
  python scripts/verify_m3_checkpoint.py
  BASE_URL=http://localhost:8000 python scripts/verify_m3_checkpoint.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg2
import requests

# ---------------------------------------------------------------------------
# Bootstrap: path + env
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.resolve()
API_DIR = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_DIR))

from dotenv import load_dotenv  # noqa: E402 — must come after sys.path insert

load_dotenv(REPO_ROOT / ".env")

BASE_URL = os.environ.get("VERIDIAN_BASE_URL", "http://localhost:8000")
ADMIN_KEY = os.environ["ADMIN_KEY"]
VOYAGE_API_KEY = os.environ["VOYAGE_API_KEY"]

# ---------------------------------------------------------------------------
# Seed data: 20 chunks designed for meaningful vector/BM25 divergence
#
# Group A (12): explicit "refund" / "return" language — BM25 hits for
#               "What is the refund policy?"
# Group B (8):  semantically related but lexically different — vector hits
#               ("reimbursement", "money-back", "payment reversal", etc.)
#               that BM25 will not surface for the term "refund policy"
# ---------------------------------------------------------------------------

SEED_CHUNKS = [
    # Group A — BM25 hits
    "Our refund policy allows customers to request a full refund within 30 days of purchase.",
    "To initiate a refund, contact our support team with your order number and reason for return.",
    "Refunds are processed within 5–7 business days after we receive the returned item.",
    "Digital downloads are non-refundable once accessed; physical goods qualify for a full refund.",
    "If your item arrives damaged, you are entitled to a refund or replacement at no extra cost.",
    "Refund requests submitted after 30 days will be reviewed on a case-by-case basis.",
    "Our no-hassle refund policy ensures customer satisfaction is our top priority.",
    "Partial refunds may be issued for items showing signs of use beyond what is necessary for inspection.",
    "Refund eligibility depends on the product category and purchase channel.",
    "Store credit can be issued in lieu of a cash refund if the customer prefers.",
    "International refund policy: shipping costs are non-refundable for orders sent outside the country.",
    "To track your refund status, log in to your account and visit the Order History section.",
    # Group B — Vector hits (semantically close, lexically distant)
    "Customers may seek reimbursement for any unsatisfactory purchase made in the last month.",
    "A money-back guarantee applies to all physical products sold through our online store.",
    "Payment reversal requests are handled by our billing department within two working weeks.",
    "We stand behind every product; if you are dissatisfied, we will make it right financially.",
    "Return merchandise authorisation (RMA) codes are issued within 24 hours of a complaint.",
    "Your satisfaction is guaranteed: any item may be sent back for a full credit to your account.",
    "Compensation for defective goods includes a complete reimbursement of the purchase amount.",
    "Customer-initiated cancellations before dispatch receive an immediate credit note.",
]

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")

def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}", file=sys.stderr)

def _step(n: int, title: str) -> None:
    print(f"\n=== Step {n}: {title} ===")


# ---------------------------------------------------------------------------
# Step 1 — Alembic migration
# ---------------------------------------------------------------------------

def run_migrations() -> None:
    _step(1, "Apply Alembic migrations (control DB)")
    from alembic.config import Config
    from alembic import command as alembic_command

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    alembic_command.upgrade(cfg, "head")
    _ok("Control DB at head (retrieval_strategy column present)")


# ---------------------------------------------------------------------------
# Step 2 — Create tenant + agent
# ---------------------------------------------------------------------------

def create_tenant() -> tuple[str, str]:
    """Returns (tenant_id, api_key)."""
    _step(2, "Create tenant")
    name = f"m3-checkpoint-{uuid.uuid4().hex[:8]}"
    resp = requests.post(
        f"{BASE_URL}/tenants",
        json={"name": name},
        headers={"X-Admin-Key": ADMIN_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _ok(f"Tenant created: id={data['id']} name={name}")
    return str(data["id"]), data["api_key"]


def create_agent(api_key: str) -> tuple[str, str]:
    """Returns (agent_id, job_id)."""
    resp = requests.post(
        f"{BASE_URL}/agents",
        json={
            "name": "m3-checkpoint-agent",
            "role": "support",
            "soul": {
                "voice": "friendly and concise",
                "do": ["answer questions accurately", "cite sources"],
                "do_not": ["make up information", "discuss competitors"],
            },
        },
        headers={"X-API-Key": api_key},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    _ok(f"Agent created: id={data['agent_id']} job={data['job_id']}")
    return str(data["agent_id"]), str(data["job_id"])


# ---------------------------------------------------------------------------
# Step 3 — Start Celery worker subprocess
# ---------------------------------------------------------------------------

def start_worker() -> subprocess.Popen:
    _step(3, "Start Celery worker (pipeline + runtime queues)")
    worker_log = Path(REPO_ROOT / "celery_worker.log")
    env = {**os.environ, "PYTHONPATH": str(API_DIR)}
    log_fh = open(worker_log, "w")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "celery",
            "-A", "app.worker.celery_app",
            "worker",
            "-Q", "pipeline,runtime",
            "--loglevel", "info",
            "--concurrency", "2",
        ],
        cwd=str(API_DIR),
        env=env,
        stdout=log_fh,
        stderr=log_fh,
    )
    # Wait until the worker finishes mingle (meaning it is ready to consume tasks)
    print("  Waiting for worker to connect and finish mingle...", flush=True)
    deadline = time.time() + 60
    connected = False
    while time.time() < deadline:
        if proc.poll() is not None:
            log_fh.flush()
            raise RuntimeError(
                f"Celery worker exited (rc={proc.returncode}) — see {worker_log}"
            )
        try:
            with open(worker_log) as f:
                content = f.read()
            # "mingle: all alone" or "ready." both signal the worker is ready
            if "mingle: all alone" in content or ("Connected to" in content and "ready." in content):
                connected = True
                break
        except OSError:
            pass
        time.sleep(1)

    if not connected:
        if proc.poll() is None:
            print("  (worker readiness inconclusive — proceeding)", flush=True)
        else:
            raise RuntimeError(f"Celery worker failed to start — see {worker_log}")

    _ok(f"Worker started (pid={proc.pid}, log={worker_log})")
    return proc


# ---------------------------------------------------------------------------
# Step 4 — Wait for agent status=ready
# ---------------------------------------------------------------------------

def wait_for_ready(agent_id: str, api_key: str, timeout: int = 180) -> None:
    _step(4, f"Wait for agent {agent_id} status=ready (timeout {timeout}s)")
    deadline = time.time() + timeout
    last_status = "unknown"
    while time.time() < deadline:
        resp = requests.get(
            f"{BASE_URL}/agents/{agent_id}",
            headers={"X-API-Key": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json().get("status", "unknown")
        if status != last_status:
            print(f"  ... status={status}", flush=True)
            last_status = status
        if status == "ready":
            _ok("Agent is ready")
            return
        if status == "failed":
            raise RuntimeError("Agent provisioning failed — check Celery worker logs")
        time.sleep(5)
    raise TimeoutError(f"Agent did not reach ready within {timeout}s (last status={last_status})")


# ---------------------------------------------------------------------------
# Step 5 — Seed test data into tenant Neon DB
# ---------------------------------------------------------------------------

def _get_neon_conn_str(agent_id: str) -> str:
    """Decrypt the pooled Neon connection string from the control DB."""
    # Must import after migrations are applied and env vars are loaded
    from app.core.security import fernet_decrypt
    from app.core.database import SyncSessionFactory
    from app.models.agent import Agent
    from sqlalchemy.orm import Session

    with SyncSessionFactory() as db:
        agent = db.get(Agent, uuid.UUID(agent_id))
        if agent is None or agent.neon_connection_string is None:
            raise RuntimeError(f"Agent {agent_id} has no neon_connection_string")
        return fernet_decrypt(agent.neon_connection_string)


def seed_test_data(conn_str: str) -> str:
    """Insert 20 chunks + embeddings; returns document_id."""
    _step(5, "Seed test chunks + Voyage embeddings into tenant Neon DB")
    import voyageai

    vo = voyageai.Client(api_key=VOYAGE_API_KEY)

    # Embed all chunks in one batch (voyage-3, input_type="document")
    print(f"  Embedding {len(SEED_CHUNKS)} chunks via Voyage voyage-3...", flush=True)
    result = vo.embed(SEED_CHUNKS, model="voyage-3", input_type="document")
    vectors = result.embeddings
    _ok(f"Embeddings ready ({len(vectors)} x {len(vectors[0])} dims)")

    conn = psycopg2.connect(conn_str)
    try:
        with conn.cursor() as cur:
            # Insert a parent document row
            cur.execute(
                """
                INSERT INTO documents (source_type, source_uri, title, parse_status)
                VALUES ('text', 'seed://m3-checkpoint', 'M3 Checkpoint Seed', 'complete')
                RETURNING id
                """,
            )
            doc_id = str(cur.fetchone()[0])

            # Insert chunks + embeddings
            for i, (text, vec) in enumerate(zip(SEED_CHUNKS, vectors)):
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, ordinal, content, token_count)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (doc_id, i, text, len(text.split())),
                )
                chunk_id = str(cur.fetchone()[0])
                cur.execute(
                    """
                    INSERT INTO embeddings (chunk_id, model, vector)
                    VALUES (%s, %s, %s::vector)
                    """,
                    (chunk_id, "voyage-3", str(vec)),
                )

        conn.commit()
    finally:
        conn.close()

    _ok(f"Seeded {len(SEED_CHUNKS)} chunks (doc_id={doc_id})")
    return doc_id


# ---------------------------------------------------------------------------
# Step 6 — Submit query
# ---------------------------------------------------------------------------

def submit_query(agent_id: str, api_key: str) -> str:
    """Returns job_id."""
    _step(6, "Submit query: 'What is the refund policy?'")
    resp = requests.post(
        f"{BASE_URL}/agents/{agent_id}/query",
        json={"query": "What is the refund policy?"},
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    job_id = str(data["job_id"])
    _ok(f"Query dispatched: job_id={job_id}")
    return job_id


# ---------------------------------------------------------------------------
# Step 7 — Poll for query.complete
# ---------------------------------------------------------------------------

def poll_until_complete(job_id: str, api_key: str, timeout: int = 60) -> dict:
    """Returns the query.complete event payload.

    Polls GET /jobs/{job_id} (REST endpoint, not SSE) which returns all
    persisted events as JSON.  The SSE endpoint keeps the connection open
    indefinitely and cannot be polled with a plain requests.get() call.
    """
    _step(7, f"Poll GET /jobs/{job_id} for query.complete event (timeout {timeout}s)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(
            f"{BASE_URL}/jobs/{job_id}",
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        job = resp.json()

        events = job.get("events", [])
        for ev in events:
            if ev.get("event_type") == "query.complete":
                _ok("query.complete received")
                return ev.get("payload", {})
        for ev in events:
            if ev.get("event_type") == "query.failed":
                raise RuntimeError(f"query.failed: {ev.get('payload', {}).get('error')}")

        time.sleep(2)

    raise TimeoutError(f"query.complete not received within {timeout}s")


# ---------------------------------------------------------------------------
# Step 8 — Validate
# ---------------------------------------------------------------------------

def validate(payload: dict) -> bool:
    _step(8, "Validate checkpoint assertions")
    trace = payload.get("trace", {})
    results = payload.get("results", [])

    vector_cands = trace.get("vector_candidates", [])
    bm25_cands = trace.get("bm25_candidates", [])
    fused_cands = trace.get("fused_candidates", [])
    reranked = results  # query.complete payload["results"] = reranked list

    passed = True

    # Assertion 1: all four result sets non-empty
    checks = [
        ("vector_candidates non-empty", len(vector_cands) > 0),
        ("bm25_candidates non-empty", len(bm25_cands) > 0),
        ("fused_candidates non-empty", len(fused_cands) > 0),
        ("reranked results non-empty", len(reranked) > 0),
    ]
    for label, ok in checks:
        if ok:
            _ok(label)
        else:
            _fail(label)
            passed = False

    # Assertion 2: meaningful divergence between vector and BM25 top-5
    vector_ids = {c["chunk_id"] for c in vector_cands[:5]}
    bm25_ids = {c["chunk_id"] for c in bm25_cands[:5]}
    overlap = len(vector_ids & bm25_ids)
    diverges = overlap < len(vector_ids)
    if diverges:
        _ok(f"Vector/BM25 divergence: top-5 overlap={overlap} < {len(vector_ids)} (meaningful)")
    else:
        _fail(f"Vector/BM25 top-5 are identical (overlap={overlap}) — no divergence")
        passed = False

    # Print counts for human review
    print(f"\n  Counts: vector={len(vector_cands)} bm25={len(bm25_cands)} "
          f"fused={len(fused_cands)} reranked={len(reranked)}")
    strategy = payload.get("strategy_used", {})
    print(f"  Strategy: {strategy}")

    return passed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("M3 Checkpoint Verification - Hybrid Retrieval Pipeline")
    print("=" * 60)

    worker_proc: subprocess.Popen | None = None
    overall_pass = False

    try:
        run_migrations()
        tenant_id, api_key = create_tenant()
        # Start worker BEFORE creating agent so provision_neon is dispatched
        # to an already-connected worker (avoids timing race on slow brokers).
        worker_proc = start_worker()
        agent_id, _ = create_agent(api_key)
        wait_for_ready(agent_id, api_key, timeout=180)
        conn_str = _get_neon_conn_str(agent_id)
        seed_test_data(conn_str)
        job_id = submit_query(agent_id, api_key)
        payload = poll_until_complete(job_id, api_key, timeout=90)
        overall_pass = validate(payload)

    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
    except Exception as exc:
        print(f"\n  ERROR: {exc}", file=sys.stderr)
        raise
    finally:
        if worker_proc is not None and worker_proc.poll() is None:
            print("\n=== Stopping Celery worker ===")
            worker_proc.terminate()
            try:
                worker_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker_proc.kill()
            _ok("Worker stopped")

    print("\n" + "=" * 60)
    if overall_pass:
        print("=== M3 Checkpoint: PASSED ===")
    else:
        print("=== M3 Checkpoint: FAILED ===", file=sys.stderr)
    print("=" * 60)
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
