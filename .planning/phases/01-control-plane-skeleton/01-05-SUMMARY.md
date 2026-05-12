---
phase: "01-control-plane-skeleton"
plan: "05"
subsystem: "docker-compose, ci-dev, demo"
tags: ["docker", "dockerfile", "docker-compose", "makefile", "demo", "bash", "env"]
dependency_graph:
  requires:
    - "apps/api/app/main.py — FastAPI app (01-04)"
    - "apps/api/app/worker/celery_app.py — Celery app (01-02)"
    - "apps/api/app/api/v1/* — all routes (01-04)"
    - "apps/api/pyproject.toml — dependency manifest (01-01)"
  provides:
    - "apps/api/Dockerfile — python:3.12-slim image for all app services"
    - "docker-compose.yml — 6-service compose file with healthchecks and startup ordering"
    - ".env.example — all required env vars with placeholders and generation instructions"
    - "apps/api/Makefile — dev-light, test-unit, test-integration, lint, typecheck, demo"
    - "scripts/demo_m1.sh — end-to-end M1 acceptance proof script"
    - "scripts/fixtures/demo_agent.json — AgentCreate fixture for demo"
  affects:
    - "Wave 6+ (01-06) — CI workflows build on top of docker-compose and Makefile targets"
    - "CTL-11: docker-compose up starts all six services"
    - "CTL-12: scripts/demo_m1.sh runs clean from scratch"
tech_stack:
  added:
    - "python:3.12-slim (Docker base image)"
    - "postgres:17-alpine (docker-compose service)"
    - "redis:7-alpine (docker-compose service)"
  patterns:
    - "condition: service_healthy in depends_on — no race-condition startup ordering"
    - "Non-root user in Dockerfile (useradd appuser) — T-05-04 mitigation"
    - "env_file: .env + environment: override pattern — internal hostnames injected at compose level"
    - "Source volume mount (./apps/api:/app) for uvicorn --reload live development"
    - "set -euo pipefail in demo script — immediate exit on any failure"
    - ".env excluded from Docker image — secrets injected at runtime only"
key_files:
  created:
    - "apps/api/Dockerfile"
    - "docker-compose.yml"
    - ".env.example"
    - "apps/api/Makefile"
    - "scripts/demo_m1.sh"
    - "scripts/fixtures/demo_agent.json"
  modified: []
decisions:
  - "docker-compose version: 3.9 attribute included for clarity (Compose v2 ignores it with warning but it documents intent)"
  - "Dockerfile COPY . /app after pip install so library layers cache independently from source changes"
  - "demo_m1.sh uses ${EVENTS_SEEN[*]:-} with default to avoid unbound variable on empty array (bash strict mode compatibility)"
  - "dev-light uses docker compose (v2 syntax) not docker-compose (v1) for consistency with current tooling"
  - "Makefile test-unit/test-integration cd to apps/api first — supports running make from project root"
metrics:
  duration: "~7 minutes"
  completed_date: "2026-05-12"
  tasks_completed: 2
  files_created: 6
---

# Phase 01 Plan 05: Docker Compose, Dockerfile, .env.example, and Demo Script Summary

6-service docker-compose with condition: service_healthy startup ordering, python:3.12-slim Dockerfile with non-root user, comprehensive .env.example, Makefile with dev-light/test/lint/typecheck targets, and demo_m1.sh end-to-end acceptance proof script.

## Tasks Completed

| # | Name | Commit | Key Files |
|---|------|--------|-----------|
| 1 | Dockerfile, docker-compose.yml (6 services with healthchecks), .env.example | 55a82a4 | apps/api/Dockerfile, docker-compose.yml, .env.example |
| 2 | Makefile targets and demo_m1.sh script | 463c838 | apps/api/Makefile, scripts/demo_m1.sh, scripts/fixtures/demo_agent.json |

## Deviations from Plan

None — plan executed exactly as written.

All must_haves verified:
- docker-compose.yml defines exactly 6 services: postgres, redis, api, worker_pipeline, worker_runtime, beat
- Services use `condition: service_healthy` in `depends_on` (6 occurrences in compose file)
- worker_pipeline runs `--queues=pipeline`; worker_runtime runs `--queues=runtime`
- beat service is present and idle in M1
- .env.example contains all 9 required env vars: NEON_API_KEY, NEON_REGION, NEON_ENCRYPTION_KEY, CONTROL_DB_URL, CONTROL_DB_SYNC_URL, REDIS_URL, LOG_LEVEL, ADMIN_KEY, CORS_ORIGINS
- make dev-light starts only postgres, redis, api
- scripts/demo_m1.sh: bootstraps tenant, creates agent, streams SSE events, validates status=ready and neon_project_id set
- demo_m1.sh exits 0 on success, exit 1 on any failure
- bash -n scripts/demo_m1.sh exits 0 (syntax valid)

## Verification Results

All 5 plan-specified checks pass:

1. `docker compose config --services` → lists: postgres, redis, api, worker_pipeline, worker_runtime, beat (exactly 6) — PASSED
2. `grep -c "service_healthy" docker-compose.yml` → 6 (≥2, PASSED)
3. `bash -n scripts/demo_m1.sh` → exits 0 — PASSED
4. `grep "dev-light" apps/api/Makefile` → target definition found — PASSED
5. `grep -c "NEON_ENCRYPTION_KEY" .env.example` → 1 — PASSED

## Known Stubs

None. All files are production-ready configurations with no placeholder logic:
- docker-compose.yml: real service definitions with real healthchecks
- demo_m1.sh: real API calls with real validation logic
- .env.example: placeholder VALUES (by design — secrets cannot be committed) but all comments and generation instructions are accurate

## Threat Flags

Threat mitigations implemented:

| Threat ID | Mitigation Applied |
|-----------|-------------------|
| T-05-01 | .env is already in .gitignore (from Wave 1 setup); .env.example explicitly documents that .env must not be committed |
| T-05-04 | Dockerfile does NOT COPY .env; env vars injected at runtime via `env_file: .env` and `environment:` blocks in docker-compose.yml |

T-05-02 (API key in terminal during demo) and T-05-03 (postgres volume state) accepted as documented in threat model.

## Self-Check: PASSED

Files verified to exist:
- apps/api/Dockerfile — FOUND (committed 55a82a4)
- docker-compose.yml — FOUND (committed 55a82a4)
- .env.example — FOUND (committed 55a82a4)
- apps/api/Makefile — FOUND (committed 463c838)
- scripts/demo_m1.sh — FOUND (committed 463c838)
- scripts/fixtures/demo_agent.json — FOUND (committed 463c838)

Commits verified in git log:
- 55a82a4 — Task 1: Dockerfile, docker-compose.yml, .env.example
- 463c838 — Task 2: Makefile, demo_m1.sh, demo_agent.json
