---
slug: delete-document-noop
status: resolved
trigger: manual
created: 2026-05-22
goal: find_and_fix
---

# Debug: DELETE document is a no-op (doc stays in KB list)

## Symptoms

- DELETE /api/v1/agents/{agent_id}/documents/{document_id} from the admin UI
  appears to succeed but the document remains in the KB list.
- Frontend (apps/admin/app/agents/[id]/ingest/page.tsx handleDeleteDoc) treats
  HTTP 404 as success and only throws on other non-OK statuses.

## Current Focus

**hypothesis:** CONFIRMED — `chunk_id = ANY(%s)` is passed a list of Python str
(psycopg2 returns `uuid` columns as str), rendered as a `text[]` array. Postgres
raises `operator does not exist: uuid = text`, the whole tenant-DB transaction
rolls back, and the handler returns HTTP 500. The row is never deleted.
**next_action:** Cast the array params to `uuid[]` (`ANY(%s::uuid[])`).

## Evidence

- timestamp: 2026-05-22T00:00:00Z
  finding: |
    Live OpenAPI (GET /openapi.json) shows the DELETE route IS registered:
    /api/v1/agents/{agent_id}/documents/{document_id} -> delete, get.
    So this is NOT the stale-uvicorn / missing-route case (cf. delete-agent-405).

- timestamp: 2026-05-22T00:00:10Z
  finding: |
    Auth is correct: both GET list and DELETE use get_current_tenant, which is
    dual-auth (Clerk JWT first, X-API-Key fallback). No get_current_user exists.
    No auth dependency mismatch. CORS allow_methods includes DELETE.

- timestamp: 2026-05-22T00:00:20Z
  finding: |
    All 13 unit tests in tests/unit/test_document_routes.py pass, but they fully
    mock psycopg2 (cursor.execute is a MagicMock) so no real SQL ever runs.
    They cannot catch a type-mismatch in the DELETE SQL.

- timestamp: 2026-05-22T00:00:30Z
  finding: |
    Read-only diag (scripts/_diag_delete_doc.py) against the live 'portfolio
    assistant' tenant DB: documents exist and the handler's existence check
    `SELECT source_type FROM documents WHERE id = %s` FINDS them
    (handler-lookup-found=True). So the 404 path is NOT the cause for real docs.

- timestamp: 2026-05-22T00:00:40Z
  finding: |
    Reproduction (scripts/_diag_delete_sql.py) running the EXACT handler delete
    sequence inside a rolled-back transaction against the real tenant DB:
      step1 existence -> ('md',)
      step2 chunk_ids -> 47 chunks
      step3 DELETE FROM chunk_entities WHERE chunk_id = ANY(%s)
        -> psycopg2.errors.UndefinedFunction:
           operator does not exist: uuid = text
           LINE 1: ... WHERE chunk_id = ANY(ARRAY['bf9d8...
    psycopg2 returns chunks.id (uuid) as Python str; the list is bound as a
    text[] array; uuid = text has no operator. The handler's broad
    `except Exception` rolls back and returns HTTP 500.

  conclusion: |
    Root cause: untyped UUID array parameters in the three
    `WHERE chunk_id = ANY(%s)` deletes. Any document that HAS chunks (every
    successfully-ingested doc) hits this and the delete transaction always
    rolls back. The document is never removed -> "doc stays".

## Resolution

- root_cause: |
    In delete_document (apps/api/app/api/v1/documents.py), the three deletes
    that match by chunk_id used `ANY(%s)` with a list of UUID *strings*
    (psycopg2 returns uuid columns as str). Postgres rejects `uuid = text`,
    so the transaction always fails for any document that has chunks, rolls
    back, and returns 500 — the row is never deleted.
- fix: |
    Cast the bound array to uuid[] in all three deletes: `ANY(%s::uuid[])`.
    This makes the comparison `uuid = uuid`. (Single-id deletes that use
    `WHERE document_id = %s` / `WHERE id = %s` were already fine because a bare
    string literal is implicitly cast to uuid in that simple equality form.)
