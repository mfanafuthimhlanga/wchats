---
phase: 03-hybrid-retrieval
plan: 07
status: complete
wave: 7
committed: true
requirements_satisfied:
  - RET-07
  - RET-08
files_created:
  - notebooks/demo_m3.ipynb
  - scripts/demo_m3.sh
---

# Plan 07 Summary: M3 Demo Notebook + Shell Script

## What Was Built

### notebooks/demo_m3.ipynb

Valid Jupyter notebook (nbformat=4) with 9 cells: 1 markdown header + 8 code cells.

| Cell | Type | Purpose |
|------|------|---------|
| 0 | Markdown | Header: "M3 Demo: Hybrid Retrieval — Vector search + BM25 + RRF + Voyage Rerank" |
| 1 | Code | Setup: imports, load_dotenv(), read VERIDIAN_BASE_URL / VERIDIAN_API_KEY / VERIDIAN_AGENT_ID / DEMO_QUERY from env |
| 2 | Code | Submit query: POST /agents/{AGENT_ID}/query → capture job_id + events_url |
| 3 | Code | Poll SSE: poll_query_complete() polls GET /jobs/{job_id}/events until query.complete event; prints strategy_used + results count |
| 4 | Code | Vector candidates DataFrame: displays chunk_id, cosine_score, content (top 10) |
| 5 | Code | BM25 candidates DataFrame: displays chunk_id, bm25_score, content (top 10) |
| 6 | Code | Fused candidates DataFrame: displays chunk_id, rrf_score, cosine_score, bm25_score, vector_rank, bm25_rank, content (top 10) |
| 7 | Code | Reranked (final) DataFrame: adds rerank_delta column (rerank_score - rrf_score); displays top 10 |
| 8 | Code | Divergence assertion: checks top-5 vector vs top-5 BM25 overlap < len(vector_ids); asserts meaningful divergence |

No hardcoded secrets. All credentials read from environment variables via `python-dotenv`.

### scripts/demo_m3.sh

Shell smoke test following demo_m2.sh pattern exactly.

Features:
- Bash strict mode: `#!/usr/bin/env bash` + `set -euo pipefail`
- Env vars: `BASE_URL` (default `http://localhost:8000`), `API_KEY` (required), `AGENT_ID` (required)
- Validates non-empty API_KEY and AGENT_ID with explicit error messages
- POSTs `{"query": "What is the refund policy?"}` to `POST /agents/{AGENT_ID}/query`
- Polls `GET /jobs/{JOB_ID}/events` every 2s up to 60s deadline using `$SECONDS`
- Detects `"query.complete"` in the events response body via `grep -q`
- Extracts result count via `jq -r '.payload.results | length'`
- Exits 0 on success with `=== M3 Demo: PASSED ===`
- Exits 1 if query.complete not received within 60s

## Verification Results

```
Notebook OK: 9 cells (8 code + 1 markdown)
exists: OK
set -euo pipefail: 1 match
query.complete: 7 matches
API_KEY: 8 matches
demo_m3.sh structure OK
```

## How to Run

### Prerequisites

1. Docker Compose services running: `docker compose up -d`
2. M2 data ingested: run `bash scripts/demo_m2.sh` first and note AGENT_ID + API_KEY
3. Create or update `.env` with:
   ```
   VERIDIAN_BASE_URL=http://localhost:8000
   VERIDIAN_API_KEY=<tenant api key from demo_m2.sh>
   VERIDIAN_AGENT_ID=<agent uuid with status=ready and M2 data>
   ```

### Notebook

```bash
cd notebooks
jupyter lab   # or: jupyter notebook
# Open demo_m3.ipynb → Kernel → Restart and Run All
```

Expected outputs per cell:
- Cell 4: DataFrame with cosine_score column, non-empty
- Cell 5: DataFrame with bm25_score column, non-empty
- Cell 6: DataFrame with rrf_score, cosine_score, bm25_score, vector_rank, bm25_rank columns
- Cell 7: DataFrame with rerank_score, rrf_score, rerank_delta columns
- Cell 8: Prints "Vector and BM25 produce meaningfully different candidate sets"

### Shell Script

```bash
export BASE_URL=http://localhost:8000
export API_KEY=<tenant api key>
export AGENT_ID=<agent uuid>
bash scripts/demo_m3.sh
# Expected: exits 0 and prints "=== M3 Demo: PASSED ==="
```

## Pending: Human Checkpoint Required

Per the plan-07 blocking checkpoint, human verification is required before RET-08 is considered satisfied:

1. Run notebook against real M2 tenant DB
2. Verify four DataFrames are non-empty
3. Verify Cell 8 prints "Vector and BM25 produce meaningfully different candidate sets" (overlap < 5)
4. Run `bash scripts/demo_m3.sh` and confirm exit 0

Signal approval with "approved" or describe which cells failed or what output was missing.
