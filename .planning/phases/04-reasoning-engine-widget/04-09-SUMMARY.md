---
phase: 04-reasoning-engine-widget
plan: "09"
subsystem: cleanup-rebrand
tags: [cleanup, e2e-test, provisioning, rebrand]
dependency_graph:
  requires: [04-07]
  provides: [e2e-test, provision-scripts, generic-fixture]
  affects: [04-10]
tech_stack:
  added: [httpx-async, anyio-asyncio]
  patterns: [AGENT_E2E_ENABLED-guard, asyncio.wait_for-SSE, pytest.mark.anyio]
key_files:
  created:
    - scripts/provision_agent.sh
    - scripts/provision_agent.ps1
  modified:
    - apps/api/tests/evals/fixtures/demo_business_tenant.sql
    - apps/api/tests/integration/test_agent_e2e.py
    - apps/demo/index.html
  deleted:
    - scripts/demo_m4.sh
    - scripts/demo_m4.ps1
commits:
  - ce74b4d
  - a982b8b
status: complete
---

## Summary

Plan 04-09 cleaned up all Bella Vista Coffee demo artifacts and replaced them with
generic, production-ready equivalents.

## Fixture Rename

`apps/api/tests/evals/fixtures/demo_business_tenant.sql` — 18 chunks, 18
chunk_metadata rows, 18 embeddings. Brand string replacements applied:

| Old | New |
|-----|-----|
| Bella Vista Coffee | Acme Consulting |
| bella-vista (S3 URI) | acme-consulting |
| bellavistacoffee.com | acmeconsulting.com |
| 1420 Coffee Lane | 100 Main St |
| 890 Roast Ave | 200 Commerce Ave |

Schema structure, UUIDs, ON CONFLICT clauses, and all content data preserved verbatim.
Header comment updated: `-- Acme Consulting — Generic Eval Fixture`.

## E2E Test

`apps/api/tests/integration/test_agent_e2e.py`
- Guard env var: `AGENT_E2E_ENABLED=1`
- Test function: `async def test_agent_responds_with_citation_against_real_corpus`
- Backend: `pytest.mark.anyio` (asyncio backend from conftest.py)
- SSE timeout: `asyncio.wait_for(_read_agent_response(), timeout=30)`
- Asserts: `payload["text"]` non-empty + `payload["citations"]` list len >= 1
- Skip behavior: 1 skipped, 0 failures when `AGENT_E2E_ENABLED` is unset ✓

## Provisioning Scripts

`scripts/provision_agent.sh` — env vars: `ADMIN_KEY` (required), `AGENT_NAME`,
`SOUL_ROLE`, `SOUL_VOICE`. Steps: POST /tenants → POST /agents → poll /jobs/{id}
until ready → PATCH soul fields. Exits 0 with `PROVISIONED agent_id=<id>`.

`scripts/provision_agent.ps1` — PowerShell equivalent using `Invoke-RestMethod`.
Same orchestration steps; prints `PROVISIONED agent_id=<id>`.

## Deleted Files

- `scripts/demo_m4.sh` — deleted via `git rm`
- `scripts/demo_m4.ps1` — deleted via `git rm`

## Demo Page

`apps/demo/index.html` replaced with a 9-line HTML5 redirect document:
`<meta http-equiv="refresh" content="0; url=/sign-in">` — no widget iframe,
no demo branding.

## Self-Check: PASSED

All acceptance criteria verified:
- `pytest tests/integration/test_agent_e2e.py -q` → 1 skipped ✓
- `bash -n scripts/provision_agent.sh` → exits 0 ✓
- Fixture contains "Acme Consulting", zero "Bella Vista" strings ✓
- demo/index.html has refresh meta, no iframe ✓
- demo_m4.sh + demo_m4.ps1 absent ✓
