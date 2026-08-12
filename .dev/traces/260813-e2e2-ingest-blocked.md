# TRACE — E2E-2 · ingest: BLOCKED, and the plan's premise was wrong

**Attempted 2026-08-13** against the E2E-1 agent (`c14d13a1-…`, Neon project `mute-dream-53534177`).
**Result: `POST /agents/{id}/documents` → 500.** Nothing ingested. No document, chunk or embedding row
exists.

## The observation

```
POST /api/v1/agents/c14d13a1-7401-4ff2-b563-c9c0a72e3fcb/documents
  files=[demo_business.pdf, 8459 bytes]        -> 500 Internal Server Error

  File "app/api/v1/documents.py", line 189, in upload_documents
    storage_service.put_bytes(
  File "app/services/storage_service.py", line 99, in put_bytes
    _get_s3().put_object(
botocore.exceptions.ParamValidationError: Parameter validation failed:
Invalid bucket name "": Bucket name must match the regex "^[a-zA-Z0-9.\-_]{1,255}$" ...
```

## Three findings

### 1. `1.10`'s location is wrong by one step — this fails in the ROUTE, not the worker

`1.10` records `ParamValidationError('Invalid bucket name "")` as a failure inside
`parse_documents` (`parse.py:264`, `storage_service.get_bytes`). That is true of the *test fixtures*,
which write bytes to a temp dir and then let the worker try to read them from S3. But in the real
path the **upload route itself** calls `storage_service.put_bytes` (`documents.py:189`) and dies
there, so the Celery chain is never dispatched. The worker-side gap is real but unreachable — you
cannot get to it.

### 2. The route returns a bare 500 for a pure configuration problem

`S3_UPLOADS_BUCKET` defaults to `""` (`config.py:166`, deliberately, so local dev imports work). An
operator who has not set it gets `Internal Server Error` with no indication that storage is
unconfigured — indistinguishable from a bug. There is no startup check and no guard on the route.

### 3. There is NO `endpoint_url` seam anywhere

`grep -rn "endpoint_url|S3_ENDPOINT" apps/api/app/ infra/` returns **nothing**.
`storage_service._get_s3()` builds `boto3.client("s3", region_name=settings.AWS_REGION)` with no
override, so the bucket cannot be pointed at a local S3-compatible store without a code change to the
seam that decides where customer documents are written.

## What is actually required for E2E-2, counted

| Blocker | State |
|---|---|
| **S3 bucket + credentials** | none. `S3_UPLOADS_BUCKET=""`, `~/.aws` absent. Blocks upload **and** parse. |
| **`docling==2.93.0` + `transformers>=4.47.0`** (the `pipeline` extra) | not installed. `chunking_service.py:64` imports `docling.chunking.HybridChunker` lazily. Pulls torch — a multi-GB install on a 4 GB box. |
| **Embeddings** | `EMBEDDING_PROVIDER` defaults to **`bedrock`** → also AWS. `voyage` is a supported fallback and `VOYAGE_API_KEY` is real, so this one is an env flip, not a blocker. |

`chonkie` is **not in `pyproject.toml` at all**, despite CLAUDE.md listing "Chonkie ≥1.6.5" in the
stack. Chunking goes through docling's `HybridChunker`. Recorded as a docs/stack mismatch, not acted
on.

## The plan's premise was wrong, and this is the correction

`PRODUCTION-READINESS.md` §4 said:

> **Nothing here needs AWS**; E2E-1 to E2E-5 run against the local PostgreSQL + Redis that already
> exist.

**That is false from E2E-2 onward.** Ingestion has a hard S3 dependency on both the write and read
sides, and the default embedding provider is Bedrock. E2E-1 needed no AWS and passed; E2E-2 cannot
start. Same failure mode as the two claims already recorded in §5 — a confident sentence that one
command disproved.

## Nothing was left behind

Checked after the failure: `jobs` holds only the completed `create_agent`; the tenant DB has
`documents 0 / chunks 0 / embeddings 0`. The S3 put precedes the DB write, so a storage failure
leaves no orphan document row — that ordering is correct and worth keeping.

## Not done, and why

No code was changed. Unblocking this needs one of two decisions that are the owner's, both filed in
`1.24`: add an `S3_ENDPOINT_URL` seam and run a local S3-compatible store, or supply real AWS. Adding
an env-driven override to the boundary that decides where customer documents are written is a
security-relevant change, not a dev convenience, and it should be a planned one.
