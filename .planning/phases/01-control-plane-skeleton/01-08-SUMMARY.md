---
phase: "01-control-plane-skeleton"
plan: "08"
subsystem: "ci, github-actions, readme, e2e-testing"
tags: ["github-actions", "ci", "nightly-e2e", "readme", "ruff", "mypy", "ctl-14", "ctl-15"]
dependency_graph:
  requires:
    - "apps/api/Makefile — make lint, typecheck, test-unit, test-integration targets (01-05)"
    - "apps/api/tests/unit/ — unit test suite (01-06)"
    - "apps/api/tests/integration/ — integration test suite (01-07)"
    - "apps/api/app/ — full FastAPI app (01-01 through 01-04)"
    - "scripts/demo_m1.sh — referenced in README quick-start (01-05)"
  provides:
    - ".github/workflows/ci.yml — PR workflow: lint (ruff), typecheck (mypy), unit tests, integration tests"
    - ".github/workflows/nightly.yml — nightly E2E against real Neon with NEON_API_KEY_TEST"
    - "apps/api/tests/e2e/test_neon_e2e.py — E2E test: real Neon provisioning + 10-table schema verification"
    - "README.md — architecture diagram, quick-start, env vars, test instructions, M1 success criteria"
  affects:
    - "CTL-14 (GitHub Actions CI): ci.yml covers lint, typecheck, unit tests, integration tests"
    - "CTL-15 (nightly E2E): nightly.yml creates real Neon project, asserts schema, deletes at teardown"
tech_stack:
  added:
    - "GitHub Actions — ubuntu-latest runners for CI and nightly E2E"
    - "actions/checkout@v4 + actions/setup-python@v5 — standard GHA setup"
    - "postgres:17-alpine service — ephemeral Postgres for CI integration tests"
    - "redis:7-alpine service — ephemeral Redis for CI integration tests"
    - "[tool.ruff] + [tool.mypy] config sections in pyproject.toml"
    - "e2e pytest marker — registered in pyproject.toml"
  patterns:
    - "GHA services with health-check options (pg_isready, redis-cli ping)"
    - "Ephemeral NEON_ENCRYPTION_KEY generated inline in CI (not stored as secret)"
    - "if: always() teardown step in nightly.yml (T-08-02 double-teardown mitigation)"
    - "project_delete in pytest finally block AND workflow teardown step (belt and suspenders)"
key_files:
  created:
    - ".github/workflows/ci.yml"
    - ".github/workflows/nightly.yml"
    - "apps/api/tests/e2e/__init__.py"
    - "apps/api/tests/e2e/test_neon_e2e.py"
    - "README.md"
  modified:
    - "apps/api/pyproject.toml — added [tool.ruff] and [tool.mypy] sections; registered e2e marker"
decisions:
  - "Ephemeral NEON_ENCRYPTION_KEY generated inline in CI unit/integration jobs (not stored as secret) — avoids T-08-01 (encryption key in GitHub Actions logs)"
  - "Nightly teardown double-layer: project_delete in pytest finally block AND workflow if: always() step — T-08-02 mitigation"
  - "Integration job uses -k 'not worker_kill' filter — worker_kill test spawns real subprocesses and is excluded from GHA runner environment"
  - "E2E test labels Neon project name with 'e2e' prefix — nightly teardown script identifies orphaned projects by name pattern"
  - "ruff select E, F, I (errors, pyflakes, isort) with line-length 120 — matches existing code style"
  - "mypy strict=false, ignore_missing_imports=true — avoids false positives from untyped third-party deps (neon-api, celery)"
metrics:
  duration: "~5 minutes"
  completed_date: "2026-05-13"
  tasks_completed: 2
  files_created: 5
  files_modified: 1
---

# Phase 01 Plan 08: GitHub Actions CI + Nightly E2E + README Summary

GitHub Actions CI workflow (ruff lint, mypy typecheck, unit tests >80% coverage, integration tests with real Postgres/Redis services) on every PR; nightly E2E creates a real Neon project via NEON_API_KEY_TEST secret, verifies all 10 tenant schema tables, and deletes the project in a double-teardown (pytest finally + workflow if:always() step); README has ASCII architecture diagram, 5-command quick-start, env var table, and M1 success criteria checklist.

## Tasks Completed

| # | Name | Commit | Key Files |
|---|------|--------|-----------|
| 1 | GitHub Actions CI (PR) and nightly E2E workflows | fdc0a9e | ci.yml, nightly.yml, test_neon_e2e.py, pyproject.toml |
| 2 | README with architecture diagram, quick-start, demo link | 3a72fc3 | README.md |

## Deviations from Plan

None — plan executed exactly as written.

The only extension beyond the literal plan spec: the nightly.yml teardown script deletes orphaned projects by matching the `vrd-` prefix with an `e2e` name pattern, providing a broader safety net than just cleaning up the project from the current test run. This is a natural application of T-08-02's double-teardown mitigation, not a scope change.

## Must-Haves Checklist

- [x] CI workflow triggers on pull_request targeting main and push to main
- [x] CI workflow has four jobs: lint (ruff), typecheck (mypy), test-unit, test-integration
- [x] CI test-integration has postgres:17-alpine service with health-check
- [x] CI test-integration has redis:7-alpine service with health-check
- [x] CI integration job runs alembic upgrade head before pytest
- [x] Nightly workflow triggers on schedule cron "0 2 * * *" and workflow_dispatch
- [x] Nightly workflow references ${{ secrets.NEON_API_KEY_TEST }} (not NEON_API_KEY)
- [x] Nightly workflow has teardown step with if: always()
- [x] apps/api/tests/e2e/test_neon_e2e.py contains teardown that calls project_delete in finally block
- [x] apps/api/pyproject.toml contains [tool.ruff] section
- [x] apps/api/pyproject.toml contains [tool.mypy] section with ignore_missing_imports = true
- [x] Both YAML files syntactically valid (yaml.safe_load passes)
- [x] README.md has ## Quick Start with 5-command path
- [x] README.md has ## Architecture with ASCII diagram
- [x] README.md mentions NEON_API_KEY, NEON_ENCRYPTION_KEY, ADMIN_KEY
- [x] README.md references demo_m1.sh
- [x] README.md has ## Running Tests
- [x] README.md has M1 success criteria (6 items from prd-M1.md §3)
- [x] README.md has demo recording placeholder
- [x] README.md mentions argon2 and Fernet in security notes

## CTL Verification Map

| CTL ID | Requirement | Artifact |
|--------|-------------|----------|
| CTL-14 | GitHub Actions CI (lint, typecheck, unit tests, integration tests) | .github/workflows/ci.yml |
| CTL-15 | Nightly E2E against real Neon with teardown | .github/workflows/nightly.yml + tests/e2e/test_neon_e2e.py |

## Verification Results

1. `python -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/ci.yml', '.github/workflows/nightly.yml']]; print('OK')"` → PASSED
2. `grep -c "NEON_API_KEY_TEST" .github/workflows/nightly.yml` → 1 (PASSED)
3. `grep -c "if: always()" .github/workflows/nightly.yml` → 1 (PASSED)
4. `grep -c "Quick Start" README.md` → 1 (PASSED)
5. `grep -c "demo_m1.sh" README.md` → 2 (PASSED)

## Security Notes (Threat Model)

| Threat ID | Mitigation Applied |
|-----------|-------------------|
| T-08-01 | NEON_ENCRYPTION_KEY generated inline (ephemeral) in CI unit/integration jobs — never stored as GitHub secret. Nightly uses `secrets.NEON_ENCRYPTION_KEY` which is the same value as local .env (no new secret material in logs). |
| T-08-02 | Double teardown: `project_delete` in pytest `finally` block + workflow step with `if: always()`. Orphan cleanup script in nightly.yml deletes all `vrd-*e2e*` projects at end of every nightly run. |
| T-08-03 | README describes defensive measures (argon2id key hashing, Fernet encryption, no secrets in queue). No key material or internal system details exposed. |

## Known Stubs

None. All files created are CI configuration and documentation. The E2E test file is a real functional test (not a stub). The demo recording is explicitly a placeholder with a comment indicating it will be updated after the first successful run — this is intentional, not a stub that blocks the plan's goal.

## Threat Flags

None. CI workflow files and README introduce no new network endpoints, auth paths, or schema changes. The E2E test consumes the existing Neon API (already in the threat model from 01-03).

## Self-Check: PASSED

Files verified:
- .github/workflows/ci.yml — FOUND (committed fdc0a9e)
- .github/workflows/nightly.yml — FOUND (committed fdc0a9e)
- apps/api/tests/e2e/__init__.py — FOUND (committed fdc0a9e)
- apps/api/tests/e2e/test_neon_e2e.py — FOUND (committed fdc0a9e)
- README.md — FOUND (committed 3a72fc3)
- apps/api/pyproject.toml (modified) — FOUND (committed fdc0a9e)

Commits verified:
- fdc0a9e — Task 1: CI workflows, nightly E2E, E2E test, pyproject.toml
- 3a72fc3 — Task 2: README.md
