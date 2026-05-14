---
slug: parse-cv2-libxcb-import
status: verifying
trigger: "M2 ingestion pipeline parse task fails with ImportError: libxcb.so.1 — ingestion chain stalls after parsing.started, never reaches parsing.complete or beyond"
created: "2026-05-14"
updated: "2026-05-14"
---

# Debug Session: parse-cv2-libxcb-import

## Symptoms

- **Expected:** All 11 SSE events stream: ingestion.started → parsing.started → parsing.complete → chunking.started → chunking.complete → metadata.started → metadata.complete → embedding.started → embedding.complete → ingestion.complete → job.complete
- **Actual:** Only ingestion.started + parsing.started fire, repeated 4x (Celery retries), then WARNING: missing expected events
- **Error:** `ImportError: libxcb.so.1: cannot open shared object file: No such file or directory` from `cv2` imported by `docling_ibm_models.tableformer.data_management.tf_predictor` inside the parse Celery task
- **Timeline:** Introduced when worker_pipeline image was built — Dockerfile.pipeline has the correct apt-get install (libxcb1, libgl1, libglib2.0-0, libsm6, libxext6) but Docker BuildKit reused the cached apt-get layer and did NOT install the packages
- **Reproduction:** `bash scripts/demo_m2.sh` against live docker-compose stack

## Current State

- docker-compose stack: UP (api, worker_pipeline, worker_runtime, beat, redis, postgres)
- libxcb1 + libgl1 + libglib2.0-0 + libsm6 + libxext6 installed into running container via `docker exec apt-get install` — NOT yet in image
- `docker exec python -c "import cv2"` → `cv2 OK: 4.13.0` (libraries ARE present)
- worker_pipeline restarted (docker stop + docker start) to pick up code fixes and allow fresh ForkPoolWorker spawns
- API container recreated via `docker compose up -d --no-deps api` — uvicorn running WITHOUT --reload
- All code fixes committed
- Demo run underway: job_id=1af51f70, document_id=8e2f25b5, parse task aaecbae1 received at 20:33:02
- ingestion.started + parsing.started confirmed in SSE stream
- Docling parse in progress on ForkPoolWorker-1 (~1310s expected from cold models)

## Current Focus

- hypothesis: "All bugs fixed; pipeline will complete; demo will pass all 11 events"
- test: "Awaiting job.complete event in running demo_m2.sh (job 1af51f70)"
- expecting: "All 11 M2 events observed, tenant DB inspection shows chunks/embeddings"
- next_action: "wait for demo completion (~21:55 UTC based on 20:33 task start + 1310s parse)"

## Evidence

- timestamp: 2026-05-14T18:02-18:07
  what: ImportError libxcb.so.1 from cv2 in parse_documents task on ForkPoolWorker-1
  file: docker logs veridian-worker_pipeline-1

- timestamp: 2026-05-14T19:15
  what: Worker container stopped cleanly (exit 0 on SIGTERM), then docker start preserved writable layer with apt-get installs. cv2 import OK after restart.
  file: docker inspect veridian-worker_pipeline-1

- timestamp: 2026-05-14T19:31-19:53
  what: SSE stream opened by demo_m2.sh with 600s timeout. API was running --reload (WatchFiles). parse_documents succeeded after 1310s. SSE stream killed before events arrived — demo reported WARNING missing events.
  notes: Root cause 2 identified: uvicorn --reload (WatchFiles restart killed SSE). Fix: `docker compose up -d --no-deps api` removed --reload.

- timestamp: 2026-05-14T19:15
  what: FileNotFoundError in chunk_documents: path was /tmp/vrd-uploads/... instead of /vrd-uploads/... (tempfile.gettempdir() vs Docker volume mount)
  file: apps/api/app/worker/tasks/pipeline/chunk.py
  fix: Changed _resolve_local_path to use Path("/vrd-uploads") / agent_id / ...

- timestamp: 2026-05-14T20:09
  what: OperationalError in chunk_documents: SSL connection has been closed unexpectedly after 954s of docling inference. Neon serverless SSL timed out while tenant_conn held idle.
  file: apps/api/app/worker/tasks/pipeline/chunk.py
  fix: Close tenant_conn before docling call, reopen after (mirrors parse.py pattern)

- timestamp: 2026-05-14T20:33
  what: New demo run started. ingestion.started + parsing.started confirmed. parse task aaecbae1 received by ForkPoolWorker-1. SSE stream active, API verified running without --reload.
  file: scripts/demo_m2.sh (SSE_TIMEOUT=3600 fix active)

## Eliminated Hypotheses

- H1 (prefork workers cannot import cv2 even after apt-get): Eliminated. Restarting container with docker stop + docker start preserved writable layer (apt-get installs intact). Fresh ForkPoolWorker-2 spawned and successfully imported cv2 and completed docling parse in 1310s.
- H2 (demo timeout too short): Eliminated. Changed SSE_TIMEOUT from 600s default to 3600s env var with 3600s default.
- H3 (uvicorn --reload causing SSE disconnect): Eliminated. API was running with --reload due to prior manual docker run command. Recreated via docker compose up -d --no-deps api.
- H4 (wrong vrd-uploads path in chunk.py and embed.py): Eliminated. Fixed: /tmp/vrd-uploads → /vrd-uploads.
- H5 (Neon SSL timeout in chunk.py): Eliminated. Fixed: close tenant_conn before docling re-parse, reopen after.

## Resolution

- root_cause: "Multiple compounding bugs: (1) libxcb1/cv2 system libraries missing from worker image due to BuildKit cache; (2) uvicorn running with --reload which restarted SSE connections; (3) chunk.py/embed.py used wrong vrd-uploads path; (4) chunk.py held Neon SSL connection open during 1310s docling parse causing SSL timeout; (5) demo SSE timeout was 600s < 1310s parse time"
- fix: "(1) apt-get install libxcb1 libgl1 libglib2.0-0 libsm6 libxext6 in running container; (2) docker compose up -d --no-deps api to remove --reload; (3) fixed _resolve_local_path in chunk.py and cleanup path in embed.py; (4) close tenant_conn before docling call in chunk.py; (5) SSE_TIMEOUT env var defaulting to 3600s in demo_m2.sh"
- verification: "demo_m2.sh running with job 1af51f70 — awaiting all 11 events"
- files_changed:
  - apps/api/app/worker/tasks/pipeline/chunk.py
  - apps/api/app/worker/tasks/pipeline/embed.py
  - scripts/demo_m2.sh
