# S3 endpoint seam — unblock E2E-2 without touching the cloud

**Row:** `1.24 · ingest-requires-s3`. **Owner decision, 2026-08-13:** add an `S3_ENDPOINT_URL` seam
and run a local S3-compatible store as a plain process. **Not** real AWS.

## Why this needs a plan and not a patch

`storage_service` is the boundary that decides **where customer documents are written and read**. An
env-driven override on it is not a dev convenience; it is a redirect primitive. The design below is
shaped by that, and the fail-closed rule is the part that matters most.

## Design

### 1. `S3_ENDPOINT_URL: str | None = None`

New `Settings` field, default `None` → boto3 resolves real AWS exactly as today. No behaviour change
for any existing deployment, and nothing new is required in `.env.example`'s REQUIRED block (it has a
default, so E2E-0's test is unaffected).

### 2. `_get_s3()` passes it only when set

```python
kwargs = {"region_name": settings.AWS_REGION}
if settings.S3_ENDPOINT_URL:
    kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
_s3 = boto3.client("s3", **kwargs)
```

Credentials come from boto3's normal chain, so MinIO works by exporting `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` for the local processes. **No credential fields are added to `Settings`** —
adding them would create a second, weaker home for secrets that the IAM-role path deliberately avoids.

### 3. THE FAIL-CLOSED RULE: the override cannot exist in production

If `ENVIRONMENT == "production"` and `S3_ENDPOINT_URL` is set → **raise**, loudly, at client
construction. Not "ignore it", not "warn": a production process configured to send customer documents
to a non-AWS endpoint must refuse to start that path rather than quietly serve it.

This is what converts the seam from "a redirect primitive anyone with env access can aim" into "a
dev-only affordance that is structurally absent in production". It is the whole justification for
being allowed to add it, and it gets a mutation proof.

### 4. `StorageNotConfigured` — the bare-500 finding

`S3_UPLOADS_BUCKET` defaults to `""`, which today reaches botocore and surfaces as
`Invalid bucket name ""` inside a 500. An operator cannot tell that from a bug — I could not, and I
wrote the trace. Add a module-level exception raised by a `_bucket()` accessor, and have
`upload_documents` translate it to **503 with a message naming the setting**. A missing configuration
is unavailable-service, not internal-error.

## Files

- `app/core/config.py` — one field
- `app/services/storage_service.py` — `_get_s3()`, `_bucket()`, `StorageNotConfigured`
- `app/api/v1/documents.py` — catch → 503
- `tests/unit/test_storage_endpoint_seam.py` — new
- `.env.example` × 2 — document the new optional setting
- `.dev/BACKLOG.md` — `1.24` closed, `1.10` corrected

## Tests, each with a mutation proof

1. default → boto3 called **without** `endpoint_url` (the production path is the default)
2. set → boto3 called **with** it
3. `ENVIRONMENT=production` + endpoint set → raises; message names the variable
4. `ENVIRONMENT=production` + endpoint **unset** → constructs normally (the guard is not a blanket ban)
5. empty bucket → `StorageNotConfigured`, and the route returns **503**, not 500
6. bucket set → no raise

The client is memoised in a module global (`_s3`), so every test must reset it or it will assert
against a client built by a previous test. That is a real trap: it would make test 2 pass while
reading test 1's client.

## Risks

- **The seam is the risk.** Mitigated by §3, which is the only reason to accept it.
- **`transformers 5.13.1` is far newer than the `>=4.47.0` the pyproject comment reasoned about**
  (it warns that docling-ibm-models needs RT-DETRv2 support added in 4.47). A 5.x major may break
  docling's layout model at *runtime*, which import-time checks will not reveal. E2E-2 is where that
  shows up; if it does, it is a finding about the pin, not about this seam.
- **MinIO is not S3.** A green E2E-2 against MinIO proves the ingestion chain, not AWS compatibility.
  Say so in the trace rather than letting "ingest works" stand unqualified.

## Definition of done

Six tests green with six mutation proofs, the full unit gate re-run, and `POST /agents/{id}/documents`
observed returning 202 against MinIO with document rows in the tenant DB.
