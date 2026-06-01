"""E2E ingestion test against real Anthropic + Voyage APIs.

Gated by INGESTION_E2E_ENABLED=1 environment variable — this test makes
real billed API calls and requires:
  - ANTHROPIC_API_KEY valid and not exhausted
  - VOYAGE_API_KEY valid and not exhausted
  - apps/api/tests/fixtures/demo_business.pdf in place
  - Local Postgres + Redis running (existing integration test infra)
  - docker-compose stack up OR local FastAPI + Celery workers running
  - ADMIN_KEY env var set (for tenant provisioning)

Notes on fixture strategy:
  The existing ``test_agent_and_job`` fixture creates an agent with
  ``status='pending'`` and no ``neon_connection_string``.  E2E tests need a
  *ready* agent whose Neon (or local test) DB has the M2 migration applied.
  Rather than extend the M1 integration fixture (which would couple M1's
  test_chain.py teardown to M2's schema), these E2E tests provision a fresh
  tenant + agent via the live API (HTTP) and poll for readiness — exactly the
  same flow as ``scripts/demo_m2.sh``.  This makes the E2E tests self-contained
  and independent of any SQLAlchemy fixture state.

  If the live API is not reachable (INGESTION_E2E_ENABLED unset), all tests
  in this module are skipped at collection time via ``@pytest.mark.skipif``.
"""

import os
import time
from pathlib import Path

import httpx
import psycopg2
import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Gate: skip the entire module unless INGESTION_E2E_ENABLED=1
# ---------------------------------------------------------------------------

INGESTION_E2E_ENABLED = bool(os.getenv("INGESTION_E2E_ENABLED"))

_API_BASE = os.getenv("API_BASE", "http://localhost:8000")
_ADMIN_KEY = os.getenv("ADMIN_KEY", "")
_FIXTURE_PDF = Path("apps/api/tests/fixtures/demo_business.pdf")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provision_agent(client: httpx.Client) -> tuple[str, str, str]:
    """Create a tenant + agent via the live API and wait until agent.status=='ready'.

    Returns:
        (api_key, agent_id, tenant_id)
    """
    # Create tenant
    resp = client.post(
        f"{_API_BASE}/tenants",
        json={"name": "E2E-Test Tenant"},
        headers={"X-Admin-Key": _ADMIN_KEY},
        timeout=15,
    )
    assert resp.status_code == 201, f"POST /tenants failed: {resp.status_code} {resp.text}"
    tenant_data = resp.json()
    api_key = tenant_data["api_key"]
    tenant_id = tenant_data["id"]

    # Create agent
    resp = client.post(
        f"{_API_BASE}/agents",
        json={
            "name": "E2E Test Agent",
            "soul": {"tone": "professional", "language": "en"},
            "role": "support",
        },
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    assert resp.status_code == 202, f"POST /agents failed: {resp.status_code} {resp.text}"
    agent_data = resp.json()
    agent_id = agent_data["agent_id"]

    # Poll until ready (max 120 seconds)
    deadline = time.time() + 120
    while time.time() < deadline:
        r = client.get(
            f"{_API_BASE}/agents/{agent_id}",
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            status = r.json().get("status", "")
            if status == "ready":
                return api_key, agent_id, tenant_id
            if status == "failed":
                pytest.fail(f"Agent provisioning failed: {r.json()}")
        time.sleep(2)

    pytest.fail(f"Agent {agent_id} did not become ready within 120s")


def _wait_for_job(db_url: str, job_id: str, timeout: int = 300) -> str:
    """Poll the control DB jobs table until job.status is terminal.

    Returns the final status string.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        conn = psycopg2.connect(db_url, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
                row = cur.fetchone()
        finally:
            conn.close()
        if row and row[0] in ("complete", "failed"):
            return row[0]
        time.sleep(2)
    return "timeout"


def _get_control_db_url() -> str:
    """Return the control DB URL for direct psycopg2 queries in E2E tests."""
    return os.getenv(
        "INTEGRATION_DB_URL",
        "postgresql://wchats:wchats@localhost:5432/wchats_control",
    )


# ---------------------------------------------------------------------------
# Test 1: Real PDF ingestion end-to-end (ING-10)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INGESTION_E2E_ENABLED,
    reason="Set INGESTION_E2E_ENABLED=1 to run E2E ingestion against real APIs",
)
def test_real_pdf_ingestion_end_to_end():
    """ING-10: upload demo_business.pdf, verify chunks/metadata/embeddings/entities populate.

    Provisions a fresh tenant + agent via the live API, uploads the demo PDF,
    waits for the ingestion chain to complete, then inspects the tenant DB for:
      - chunk_count > 5
      - metadata_count == chunk_count
      - embeddings_count == chunk_count
      - vector dimension == 1024 (voyage-3)
      - entity_count >= 1
      - at least one chunk contains | (Markdown table marker — ING-03 proof)
    """
    assert _ADMIN_KEY, "ADMIN_KEY env var must be set for E2E tests"
    assert _FIXTURE_PDF.exists(), f"Demo PDF not found at {_FIXTURE_PDF}"
    assert _FIXTURE_PDF.stat().st_size < 500_000, "Demo PDF must be < 500KB"

    control_db_url = _get_control_db_url()

    with httpx.Client() as client:
        api_key, agent_id, _tenant_id = _provision_agent(client)

        # Upload PDF
        with open(_FIXTURE_PDF, "rb") as fh:
            upload_resp = client.post(
                f"{_API_BASE}/api/v1/agents/{agent_id}/documents",
                headers={"X-API-Key": api_key},
                files={"files": (_FIXTURE_PDF.name, fh, "application/pdf")},
                timeout=30,
            )
        assert upload_resp.status_code == 202, (
            f"POST /documents failed: {upload_resp.status_code} {upload_resp.text}"
        )
        body = upload_resp.json()
        document_id = body["document_ids"][0]
        job_id = body["job_id"]

    # Wait for ingestion chain to complete (max 300s)
    final_status = _wait_for_job(control_db_url, job_id, timeout=300)
    assert final_status == "complete", (
        f"Ingestion job did not complete (final_status={final_status}). "
        "Check Celery worker logs for errors."
    )

    # Inspect tenant DB via the encrypted connection string
    from app.core.security import fernet_decrypt

    conn_row = None
    ctrl_conn = psycopg2.connect(control_db_url, connect_timeout=5)
    try:
        with ctrl_conn.cursor() as cur:
            cur.execute(
                "SELECT neon_connection_string FROM agents WHERE id = %s",
                (agent_id,),
            )
            conn_row = cur.fetchone()
    finally:
        ctrl_conn.close()

    assert conn_row is not None, f"Agent {agent_id} not found in control DB"
    tenant_conn_str = fernet_decrypt(conn_row[0])

    with psycopg2.connect(tenant_conn_str, connect_timeout=10) as tconn:
        with tconn.cursor() as cur:
            # Chunk count
            cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE document_id = %s",
                (document_id,),
            )
            chunk_count = cur.fetchone()[0]
            assert chunk_count > 5, (
                f"Expected >5 chunks for a real PDF, got {chunk_count}"
            )

            # Metadata count must equal chunk count
            cur.execute(
                """
                SELECT COUNT(*) FROM chunk_metadata cm
                JOIN chunks c ON c.id = cm.chunk_id
                WHERE c.document_id = %s
                """,
                (document_id,),
            )
            metadata_count = cur.fetchone()[0]
            assert metadata_count == chunk_count, (
                f"chunk_metadata count {metadata_count} != chunk count {chunk_count}"
            )

            # Embeddings count must equal chunk count
            cur.execute(
                """
                SELECT COUNT(*) FROM embeddings e
                JOIN chunks c ON c.id = e.chunk_id
                WHERE c.document_id = %s
                """,
                (document_id,),
            )
            embedding_count = cur.fetchone()[0]
            assert embedding_count == chunk_count, (
                f"embeddings count {embedding_count} != chunk count {chunk_count}"
            )

            # Embedding dimension must be 1024 (voyage-3)
            cur.execute(
                """
                SELECT vector_dims(e.vector)
                FROM embeddings e
                JOIN chunks c ON c.id = e.chunk_id
                WHERE c.document_id = %s
                LIMIT 1
                """,
                (document_id,),
            )
            dim_row = cur.fetchone()
            assert dim_row is not None, "No embedding found for dimension check"
            assert dim_row[0] == 1024, (
                f"Embedding dimension {dim_row[0]} != 1024 (expected voyage-3)"
            )

            # Entity count >= 1 (a real business PDF must yield at least one entity)
            cur.execute("SELECT COUNT(*) FROM entities")
            entity_count = cur.fetchone()[0]
            assert entity_count >= 1, (
                "Expected at least 1 entity extracted from a real business PDF"
            )

            # ING-03: at least one chunk contains pipe characters (Markdown table marker)
            cur.execute(
                "SELECT content FROM chunks WHERE document_id = %s AND content LIKE %s LIMIT 1",
                (document_id, "%|%"),
            )
            table_chunk = cur.fetchone()
            assert table_chunk is not None, (
                "No table chunk found — chunks with '|' (Markdown table rows) were expected "
                "from a PDF with at least one structured table (ING-03)"
            )


# ---------------------------------------------------------------------------
# Test 2: Idempotent rerun — same document ingested twice produces equal counts
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INGESTION_E2E_ENABLED,
    reason="Set INGESTION_E2E_ENABLED=1 to run E2E ingestion against real APIs",
)
def test_real_pdf_idempotent_rerun():
    """ING-09 + ING-10: dispatching the chain twice for the same document produces identical row counts.

    Run 1: upload the demo PDF via the route — wait for completion — capture counts.
    Run 2: re-dispatch the same Celery chain for the same document_id via a second
           POST of the same file (the source_hash guard in parse_documents should
           detect the duplicate and skip re-parsing; ON CONFLICT upserts on chunks
           and embeddings handle the rest) — capture counts again.

    Asserts:
      chunk_count_before == chunk_count_after
      embedding_count_before == embedding_count_after
    """
    assert _ADMIN_KEY, "ADMIN_KEY env var must be set for E2E tests"
    assert _FIXTURE_PDF.exists(), f"Demo PDF not found at {_FIXTURE_PDF}"

    control_db_url = _get_control_db_url()

    with httpx.Client() as client:
        api_key, agent_id, _tenant_id = _provision_agent(client)

        # ------------------------------------------------------------------
        # Run 1: initial upload
        # ------------------------------------------------------------------
        with open(_FIXTURE_PDF, "rb") as fh:
            resp1 = client.post(
                f"{_API_BASE}/api/v1/agents/{agent_id}/documents",
                headers={"X-API-Key": api_key},
                files={"files": (_FIXTURE_PDF.name, fh, "application/pdf")},
                timeout=30,
            )
        assert resp1.status_code == 202, (
            f"Run 1 POST /documents failed: {resp1.status_code} {resp1.text}"
        )
        body1 = resp1.json()
        document_id = body1["document_ids"][0]
        job_id_1 = body1["job_id"]

    # Wait for run 1 to complete
    status1 = _wait_for_job(control_db_url, job_id_1, timeout=300)
    assert status1 == "complete", (
        f"Run 1 ingestion job did not complete (status={status1})"
    )

    # Capture counts after run 1
    from app.core.security import fernet_decrypt

    ctrl_conn = psycopg2.connect(control_db_url, connect_timeout=5)
    try:
        with ctrl_conn.cursor() as cur:
            cur.execute(
                "SELECT neon_connection_string FROM agents WHERE id = %s",
                (agent_id,),
            )
            conn_row = cur.fetchone()
    finally:
        ctrl_conn.close()

    assert conn_row is not None
    tenant_conn_str = fernet_decrypt(conn_row[0])

    with psycopg2.connect(tenant_conn_str, connect_timeout=10) as tconn:
        with tconn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE document_id = %s",
                (document_id,),
            )
            chunk_count_before = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*) FROM embeddings e
                JOIN chunks c ON c.id = e.chunk_id
                WHERE c.document_id = %s
                """,
                (document_id,),
            )
            embedding_count_before = cur.fetchone()[0]

    assert chunk_count_before > 0, "Run 1 produced no chunks — cannot test idempotency"

    # ------------------------------------------------------------------
    # Run 2: re-dispatch the ingestion chain for the same document_id.
    #   We dispatch via the Celery task functions directly to avoid
    #   creating a second document row in the tenant DB.
    # ------------------------------------------------------------------
    from celery import chain as celery_chain
    from app.worker.tasks.pipeline.parse import parse_documents
    from app.worker.tasks.pipeline.chunk import chunk_documents
    from app.worker.tasks.pipeline.metadata import generate_metadata
    from app.worker.tasks.pipeline.embed import embed_and_migrate
    from app.models.job import Job

    # Get tenant_id for the second job row
    ctrl_conn = psycopg2.connect(control_db_url, connect_timeout=5)
    try:
        with ctrl_conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id FROM agents WHERE id = %s",
                (agent_id,),
            )
            tenant_row = cur.fetchone()
    finally:
        ctrl_conn.close()
    tenant_id = tenant_row[0]

    # Insert a second job row for the re-dispatch (tasks emit events keyed by job_id)
    from sqlalchemy import create_engine, text as sa_text
    from sqlalchemy.orm import sessionmaker

    sync_engine = create_engine(
        control_db_url.replace("postgresql://", "postgresql://"),
        pool_pre_ping=True,
    )
    SyncSession = sessionmaker(sync_engine)

    with SyncSession() as db:
        db.execute(
            sa_text(
                """
                INSERT INTO jobs (id, tenant_id, agent_id, kind, status, created_at)
                VALUES (gen_random_uuid(), :tenant_id, :agent_id, 'ingest_documents', 'pending', now())
                RETURNING id
                """
            ),
            {"tenant_id": str(tenant_id), "agent_id": str(agent_id)},
        )
        second_job_id_row = db.execute(
            sa_text(
                """
                SELECT id FROM jobs
                WHERE agent_id = :agent_id AND kind = 'ingest_documents'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"agent_id": str(agent_id)},
        ).fetchone()
        db.commit()
    second_job_id = str(second_job_id_row[0])

    celery_chain(
        parse_documents.s(str(tenant_id), str(agent_id), second_job_id, [document_id]),
        chunk_documents.s(),
        generate_metadata.s(),
        embed_and_migrate.s(),
    ).apply_async(queue="pipeline")

    # Wait for run 2
    status2 = _wait_for_job(control_db_url, second_job_id, timeout=300)
    assert status2 == "complete", (
        f"Run 2 ingestion job did not complete (status={status2})"
    )

    # Capture counts after run 2
    with psycopg2.connect(tenant_conn_str, connect_timeout=10) as tconn:
        with tconn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE document_id = %s",
                (document_id,),
            )
            chunk_count_after = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*) FROM embeddings e
                JOIN chunks c ON c.id = e.chunk_id
                WHERE c.document_id = %s
                """,
                (document_id,),
            )
            embedding_count_after = cur.fetchone()[0]

    # Idempotency assertions
    assert chunk_count_before == chunk_count_after, (
        f"Idempotency violation: chunk count changed from {chunk_count_before} "
        f"to {chunk_count_after} on second run"
    )
    assert embedding_count_before == embedding_count_after, (
        f"Idempotency violation: embedding count changed from {embedding_count_before} "
        f"to {embedding_count_after} on second run"
    )
