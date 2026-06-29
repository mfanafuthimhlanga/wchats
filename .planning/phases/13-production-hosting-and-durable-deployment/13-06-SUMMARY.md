---
phase: 13-production-hosting-and-durable-deployment
plan: "06"
subsystem: storage
tags: [s3, uploads, ingestion, storage_service, PROD-12, PROD-13]
dependency_graph:
  requires: [13-01]
  provides: [S3 upload/download seam for documents, storage_service module]
  affects: [documents.py, parse.py, embed.py, ingestion chain]
tech_stack:
  added: [storage_service (boto3 S3 client wrapper), S3_UPLOADS_BUCKET config seam]
  patterns: [lazy boto3 client (mirrors _get_bedrock()), tenant-scoped S3 keys, best-effort delete]
key_files:
  created:
    - apps/api/app/services/storage_service.py
    - apps/api/tests/unit/test_s3_uploads.py
  modified:
    - apps/api/app/core/config.py
    - apps/api/app/api/v1/documents.py
    - apps/api/app/worker/tasks/pipeline/parse.py
    - apps/api/app/worker/tasks/pipeline/embed.py
    - apps/api/tests/unit/test_document_routes.py
    - apps/api/tests/unit/test_parse_task.py
decisions:
  - "Server-side boto3 get_object only — no presigned public URLs (T-13-06-02)"
  - "S3 files retained after ingestion; not deleted at embed step — durable for re-ingest (13-04 backfill)"
  - "delete_object best-effort (catches all exceptions); never reverses committed DB delete"
  - "S3_UPLOADS_BUCKET empty-string default keeps local-dev imports working without real AWS"
  - "Removed embed.py /vrd-uploads block entirely; S3 objects are not ephemeral temp files"
  - "Rule 1 auto-fix: test_document_routes.py upload tests patched with storage_service.put_bytes mock"
  - "Rule 1 auto-fix: test_parse_task.py tests 4+5 corrected (pre-existing FileNotFoundError + migration)"
metrics:
  duration: "~15 minutes"
  completed: "2026-06-29"
  tasks_completed: 2
  files_created: 2
  files_modified: 6
status: complete
---

# Phase 13 Plan 06: S3 Upload Storage — Summary

S3 boto3 storage service replacing local-disk UPLOADS_DIR; ingestion chain reads source bytes from S3 with no Fargate task-local disk dependency, and the hardcoded `/vrd-uploads` cleanup in embed.py is removed.

## What Was Built

### New: `storage_service.py`

Lazy boto3 S3 client module mirroring `_get_bedrock()` from the Bedrock embedding service:
- `_get_s3()`: lazy `boto3.client("s3", region_name=settings.AWS_REGION)` — only imports boto3 on first call so unit tests import the module without real AWS credentials
- `upload_key(agent_id, doc_id, ext)`: returns `"{agent_id}/{doc_id}{ext}"` — UUIDv4 agent prefix gives ~122-bit cross-tenant entropy
- `put_bytes(key, data)`: `put_object(Bucket=settings.S3_UPLOADS_BUCKET, Key=key, Body=data)` — never logs bytes (T-13-06-04)
- `get_bytes(key)`: `get_object(...)[\"Body\"].read()` — server-side only, no presigned URL (T-13-06-02)
- `delete_object(key)`: best-effort delete; catches all exceptions; never reverses a committed DB delete

### Config: `S3_UPLOADS_BUCKET`

Added `S3_UPLOADS_BUCKET: str = ""` to `Settings` in `config.py`. Empty default keeps local-dev imports working without requiring a real S3 bucket. Production sets this via the Terraform `uploads_bucket_name` output → ECS task definition `secrets` block.

### `documents.py` Upload Path (PROD-12)

**Before:**
```python
upload_dir = Path(settings.UPLOADS_DIR) / str(agent.id)
upload_dir.mkdir(parents=True, exist_ok=True)
local_path = upload_dir / f"{doc_id}{ext}"
local_path.write_bytes(cached_contents[idx])
```

**After:**
```python
storage_service.put_bytes(
    storage_service.upload_key(str(agent.id), doc_id, ext),
    cached_contents[idx],
)
```

No local directory creation, no local file write. Uploaded files are stored in S3 under `{agent_id}/{doc_id}{ext}`.

### `documents.py` Delete Path (PROD-12)

**Before:** `local_path.unlink(missing_ok=True)` on `Path(settings.UPLOADS_DIR)/...`

**After:** `storage_service.delete_object(storage_service.upload_key(...))` — best-effort (delete_object catches all exceptions internally). URL-sourced documents still skip cleanup entirely.

### `parse.py` File-Source Branch (PROD-13)

**Before:**
```python
file_path = Path(settings.UPLOADS_DIR) / agent_id / f"{doc_id}{ext}"
computed_hash = _compute_source_hash(file_path)
doc = parse_document(file_path)
```

**After:**
```python
content = storage_service.get_bytes(storage_service.upload_key(agent_id, doc_id, ext))
computed_hash = hashlib.sha256(content).hexdigest()
doc = parse_document_from_bytes(content, source_uri)
```

Uses `parse_document_from_bytes` — already in use for URL sources. No Docling API changes needed. `_compute_source_hash` helper is now dead code for the file-source branch but left in place (URL branch never called it; it opened a local file path).

### `embed.py` Hardcoded Cleanup Removed (Landmine 4 fix)

Removed the cleanup block (lines 252-282 before migration) that used `Path("/vrd-uploads")` — a hardcoded literal that was **never** `settings.UPLOADS_DIR`. After S3 migration, that path would never contain any files; the block was a silent no-op and a correctness landmine for Fargate. S3 source bytes are retained for idempotent re-ingestion (13-04 backfill re-embed).

### Tests

New file `test_s3_uploads.py` (6 tests):
- `test_upload_key_format`: asserts key format with multiple extensions
- `test_put_bytes_calls_put_object`: patches `_s3`, verifies Bucket/Key/Body args
- `test_get_bytes_calls_get_object`: patches `_s3`, verifies Body.read() returned
- `test_upload_documents_writes_to_s3_not_disk`: FastAPI route test via ASGITransport; asserts `put_bytes` called with tenant-scoped key and correct bytes
- `test_parse_task_reads_from_s3`: parse task file-source branch calls `get_bytes` + `parse_document_from_bytes`
- `test_embed_py_no_vrd_uploads_literal`: source assertion that embed.py no longer contains the hardcoded path

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] test_document_routes.py upload tests would break after S3 migration**

- **Found during:** Task 1 GREEN analysis
- **Issue:** The three upload route tests (202 response, chain signature, no-conn-string) call the POST route which now calls `storage_service.put_bytes`. Without mocking, this triggers a real boto3 S3 call with an empty bucket name, returning 500.
- **Fix:** Added `patch("app.services.storage_service.put_bytes")` to each of the three upload test contexts.
- **Files modified:** `apps/api/tests/unit/test_document_routes.py`
- **Commit:** d95226d

**2. [Rule 1 - Bug] test_parse_task.py tests 4+5 were already failing (pre-existing) AND broken by migration**

- **Found during:** Task 2 pre-analysis — running `pytest test_parse_task.py` revealed 2/5 already failing
- **Root cause:** Tests 4 and 5 patched `tempfile.gettempdir` (not used in parse.py) and created files in a temp path that parse.py never reads. parse.py uses `settings.UPLOADS_DIR` = `/vrd-uploads`. Result: `FileNotFoundError` on every run.
- **Migration interaction:** Task 2 replaces the file-path read with `storage_service.get_bytes`, so the fix for the pre-existing bug and the migration fix are combined: replace `tempfile.gettempdir + parse_document` mocks with `storage_service.get_bytes + parse_document_from_bytes` mocks.
- **Files modified:** `apps/api/tests/unit/test_parse_task.py`
- **Commit:** d95226d

## Storage Seam

The storage backend is selectable:
- **Local dev (CLAUDE.md):** `S3_UPLOADS_BUCKET = ""` (default). The `_get_s3()` client is lazy — it is never called in local dev unless upload routes are hit. The upload/delete flow will fail at the boto3 call if `S3_UPLOADS_BUCKET` is empty, but local dev can run tests and the rest of the API normally without configuring S3.
- **Production (Fargate):** `S3_UPLOADS_BUCKET` set from Terraform output → ECS `secrets` block. IAM task role provides S3 permissions. No `.env` file needed.

`UPLOADS_DIR` is retained in `Settings` for backward-compat but is no longer on the upload/parse hot path.

## Threat Surface

All T-13-06 mitigations implemented as designed:

| T-ID | Status | Notes |
|------|--------|-------|
| T-13-06-01 | mitigated | Keys scoped by agent UUIDv4 (~122-bit entropy); bucket Block Public Access from 13-01 |
| T-13-06-02 | mitigated | Server-side boto3 get_object only; no presigned URL generated anywhere |
| T-13-06-03 | mitigated | All upload/parse I/O moved to S3; no UPLOADS_DIR read/write on hot path |
| T-13-06-04 | mitigated | Logs reference doc_id/agent_id and byte count only; never bytes or connection strings |

## Verification Results

```
pytest apps/api/tests/unit/test_s3_uploads.py \
       apps/api/tests/unit/test_parse_task.py \
       apps/api/tests/unit/test_embed_task.py -q
18 passed in 48.00s
```

Source assertions:
- `grep -n "S3_UPLOADS_BUCKET" apps/api/app/core/config.py` → line 125 ✓
- `grep -n "write_bytes" apps/api/app/api/v1/documents.py` → 0 matches in code (comments only) ✓
- `grep -n "put_bytes" apps/api/app/api/v1/documents.py` → line 190 ✓
- `grep -n "get_bytes" apps/api/app/worker/tasks/pipeline/parse.py` → line 258 ✓
- `grep -c "/vrd-uploads" apps/api/app/worker/tasks/pipeline/embed.py` → 0 ✓
- `grep -n "settings.UPLOADS_DIR" apps/api/app/worker/tasks/pipeline/parse.py` → 0 matches ✓

## Self-Check: PASSED

- `apps/api/app/services/storage_service.py` exists ✓
- `apps/api/tests/unit/test_s3_uploads.py` exists ✓
- Commits `80f63ae` (RED) and `d95226d` (GREEN) exist in git log ✓
- 18 tests pass ✓
