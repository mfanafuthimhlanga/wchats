---
phase: 13-production-hosting-and-durable-deployment
plan: "02"
subsystem: embeddings
tags: [bedrock, titan-v2, embeddings, provider-seam, PROD-06]
dependency_graph:
  requires: []
  provides: [bedrock-embedding-client, provider-seam, 1024-dim-guard]
  affects: [embedding_service, retrieval_service, embed_chunks, embed_query, rrf_fuse_with_expansion]
tech_stack:
  added: [boto3>=1.34]
  patterns: [lazy-import, provider-seam, tenacity-retry, dimension-guard]
key_files:
  created:
    - apps/api/app/services/bedrock_embedding_service.py
    - apps/api/tests/unit/test_embedding_bedrock.py
  modified:
    - apps/api/pyproject.toml
    - apps/api/app/core/config.py
    - apps/api/app/services/embedding_service.py
    - apps/api/app/services/retrieval_service.py
    - apps/api/tests/unit/test_embedding_service.py
decisions:
  - "Titan v2 1024-dim for both document and query paths via EMBEDDING_PROVIDER env seam"
  - "Dimension guard in embed_texts (outside retry loop) — dim mismatch is config error, not transient"
  - "Lazy import of bedrock_embedding_service inside function bodies to keep modules importable offline"
  - "voyage_provider autouse fixture in test_embedding_service.py to preserve regression tests (Rule 1)"
metrics:
  duration: "~25 minutes"
  completed: "2026-06-29"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 5
status: complete
requirements: [PROD-06]
---

# Phase 13 Plan 02: Bedrock Titan v2 Embedding Provider Seam Summary

Both the document-embedding path (`embed_chunks`) and the query-embedding path (`embed_query` + `rrf_fuse_with_expansion`) now resolve the embedder via `settings.EMBEDDING_PROVIDER`: when set to `"bedrock"` (the new default), calls lazily import and dispatch to `bedrock_embedding_service.embed_texts()` backed by Amazon Bedrock Titan Text Embeddings v2 at 1024 dimensions; when set to `"voyage"`, the original Voyage AI path is taken unchanged.

## What Was Built

### New module: `bedrock_embedding_service.py`

Provides the Bedrock side of the provider seam:

- `_get_bedrock()`: lazy boto3 `bedrock-runtime` client (mirrors `_get_vo()` pattern; never imports boto3 at module load time)
- `EMBED_DIM = 1024`: hard constant matching `embeddings.vector VECTOR(1024)` — the schema is unchanged
- `_invoke_one(text)`: tenacity-retried single-text Titan v2 call (`modelId=amazon.titan-embed-text-v2:0`, `dimensions=1024`, `normalize=True`)
- `embed_texts(texts, input_type)`: loops `_invoke_one` per text; asserts `len(vector) == EMBED_DIM` after each call — raises `RuntimeError("bedrock embedding dim mismatch: got N, expected 1024")` on mismatch
- `active_embedding_model()`: returns `settings.BEDROCK_EMBED_MODEL_ID` when provider=bedrock, else falls back via lazy import to `embedding_service.EMBEDDING_MODEL`

### Provider seam: three dispatch points

| Function | File | Bedrock path | Voyage fallback |
|----------|------|-------------|-----------------|
| `embed_chunks` | embedding_service.py | `bedrock_svc.embed_texts(texts, "document")` | `_embed_batch` loop (128-item batching unchanged) |
| `embed_query` | retrieval_service.py | `bedrock_svc.embed_texts([q], "query")[0]` | `_get_vo().embed([q], model="voyage-3", input_type="query").embeddings[0]` |
| Expansion batch in `rrf_fuse_with_expansion` | retrieval_service.py | `bedrock_svc.embed_texts(variants, "query")` | `_get_vo().embed(variants, model="voyage-3", input_type="query").embeddings` |

### New Settings fields (D-14 env seam)

```
EMBEDDING_PROVIDER: str = "bedrock"          # "bedrock" | "voyage"
AWS_REGION: str = "us-east-1"               # Bedrock endpoint region
BEDROCK_EMBED_MODEL_ID: str = "amazon.titan-embed-text-v2:0"
```

### New dependency

`"boto3>=1.34,<2.0"` in `pyproject.toml` — AWS first-party SDK, approved in Phase 13 Package Legitimacy Audit (>100M downloads/month, 10+ year PyPI history).

## Test Results

```
pytest tests/unit/test_embedding_bedrock.py tests/unit/test_embedding_service.py -x -q
15 passed in 0.78s
```

**Task 1 tests (5):**
- `test_embed_texts_returns_1024_dim_vector` — 1 call → 1024-dim vector PASS
- `test_embed_texts_calls_invoke_model_correctly` — modelId + body shape PASS
- `test_embed_texts_loops_per_text` — 3 texts → 3 invoke_model calls PASS
- `test_embed_texts_raises_on_dim_mismatch` — 512-dim → RuntimeError PASS
- `test_active_embedding_model_returns_bedrock_id` — returns BEDROCK_EMBED_MODEL_ID PASS

**Task 2 tests (4):**
- `test_embed_chunks_routes_to_bedrock` — bedrock path taken PASS
- `test_embed_chunks_routes_to_voyage` — voyage fallback taken PASS
- `test_embed_query_routes_to_bedrock` — bedrock path taken PASS
- `test_embed_query_routes_to_voyage` — voyage fallback taken PASS

**Regression (6):**
- All existing `test_embedding_service.py` Voyage-path tests PASS (with voyage_provider fixture)

## Commits

| Hash | Type | Description |
|------|------|-------------|
| `09680cb` | test | Add failing tests for bedrock titan v2 embedder + provider seam (RED) |
| `af6192b` | feat | Implement bedrock titan v2 embedding client + provider seam (GREEN Task 1) |
| `0e1f8e5` | feat | Swap embed_chunks and embed_query onto bedrock/voyage provider seam (GREEN Task 2) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Voyage regression tests broke after seam introduction**

- **Found during:** Task 2 GREEN verification
- **Issue:** After adding `EMBEDDING_PROVIDER=bedrock` as the new default in Settings, the existing `test_embedding_service.py` tests (which mock Voyage's `_vo` but never mock `_bedrock`) began routing `embed_chunks` to Bedrock without any mock, causing `NoCredentialsError` (5 retries × exponential backoff = ~50s timeout per failing test)
- **Root cause:** `settings` is a module-level singleton created at first import; all tests share it; the new default "bedrock" was picked up by tests designed for the voyage path
- **Fix:** Added `voyage_provider` autouse fixture to `test_embedding_service.py` that patches `app.services.embedding_service.settings` (EMBEDDING_PROVIDER="voyage") for every test in that module. Bedrock-path tests remain in `test_embedding_bedrock.py`
- **Files modified:** `apps/api/tests/unit/test_embedding_service.py`
- **Commit:** `0e1f8e5` (bundled with Task 2)

## Invariants Preserved

- `embeddings.vector VECTOR(1024)` schema: **unchanged** — Titan v2 configured at `dimensions=1024` matches exactly; no migration needed
- Agent turns, validators, red-team calls: **untouched** — only the embedding client moved to Bedrock
- `boto3` is the only new dependency — no `[SUS]` or `[SLOP]` packages
- Count-mismatch guard in `embed_chunks` still fires for both providers
- All imports are lazy (inside function bodies) — the module is importable in unit tests with no AWS credentials and no live Bedrock access

## Threat Surface Scan

No new network endpoints introduced. The Bedrock call goes out via IAM task role (no static credentials in code). The three dispatch points (embed_chunks, embed_query, rrf_fuse_with_expansion) all touch the same Bedrock endpoint — no new trust boundaries beyond what T-13-02-01/T-13-02-02 in the plan's threat model already covers.

## Known Stubs

None — no UI-facing data paths or placeholder text introduced. The provider seam is purely internal to the embedding/retrieval layer.

## Self-Check: PASSED

- `bedrock_embedding_service.py` FOUND
- `test_embedding_bedrock.py` FOUND
- Commit `09680cb` (RED test) FOUND
- Commit `af6192b` (Task 1 GREEN) FOUND
- Commit `0e1f8e5` (Task 2 GREEN) FOUND
- `pytest tests/unit/test_embedding_bedrock.py tests/unit/test_embedding_service.py -x -q` → 15 passed
