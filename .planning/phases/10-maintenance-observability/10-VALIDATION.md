---
phase: 10
slug: maintenance-observability
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-05-25
---

# Phase 10 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Covers plans 10-04 (AlertsBanner UI), 10-05 (de-xfail 9 stubs), 10-06 (demo + E2E).
> Plans 10-01 through 10-03 are already complete — not covered here.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (backend), pnpm build (frontend type check) |
| **Config file** | `apps/api/pyproject.toml` |
| **Quick run command** | `cd apps/api && python -m pytest tests/unit/test_alert_service.py tests/unit/test_digest_service.py tests/unit/test_observability_routes.py -q` |
| **Full suite command** | `cd apps/api && python -m pytest tests/unit/ -q` |
| **Frontend check** | `cd apps/admin && pnpm build 2>&1 \| tail -20` |
| **Estimated runtime** | ~15 seconds (unit), ~60 seconds (full suite + admin build) |

---

## Sampling Rate

- **After every task commit:** Run quick run command
- **After every plan wave:** Run full suite command + admin build
- **Before `/gsd-verify-work`:** Full suite green + admin build zero type errors
- **Max feedback latency:** 15 seconds (unit), 60 seconds (full + frontend)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 10-04-01 | 04 | 4 | OPS-04 | — | AlertsBanner uses Bearer auth (not X-API-Key) | build | `cd apps/admin && pnpm build 2>&1 \| tail -5` | ❌ create | ⬜ pending |
| 10-04-02 | 04 | 4 | OPS-03, OPS-04 | — | AlertsBanner mounted; Langfuse link rendered | build | `cd apps/admin && pnpm build 2>&1 \| tail -5` | ✅ exists | ⬜ pending |
| 10-05-01 | 05 | 5 | OPS-05 | — | 4 digest service tests pass | unit | `cd apps/api && python -m pytest tests/unit/test_digest_service.py -q` | ✅ exists | ⬜ pending |
| 10-05-02 | 05 | 5 | OPS-05 | — | 3 alert service tests pass | unit | `cd apps/api && python -m pytest tests/unit/test_alert_service.py -q` | ✅ exists | ⬜ pending |
| 10-05-03 | 05 | 5 | OPS-04, OPS-05 | T-IDOR | IDOR guard returns 403/404 for wrong tenant | unit | `cd apps/api && python -m pytest tests/unit/test_observability_routes.py -q` | ✅ exists | ⬜ pending |
| 10-06-01 | 06 | 6 | OPS-06 | — | demo_m10.sh passes bash syntax check | syntax | `bash -n scripts/demo_m10.sh && echo "SYNTAX OK"` | ❌ create | ⬜ pending |
| 10-06-02 | 06 | 6 | OPS-05, OPS-06 | — | E2E test skips cleanly when OPS_E2E_ENABLED unset | unit | `cd apps/api && python -m pytest tests/e2e/test_observability_e2e.py -q` | ❌ create | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. Wave 0 stubs were created in plan 10-01 (9 xfail stubs across 3 test files). No new framework installation needed.

---

## Critical Corrections from Research

The following corrections MUST be applied before execution — research found these mismatches between plan text and built code:

### Plan 10-04 corrections
- **WRONG:** `AlertsBanner` receives `apiKey: string` prop and sends `X-API-Key: {apiKey}` header
- **CORRECT:** Use `const { getToken } = useAuth()` from `@clerk/nextjs`; send `Authorization: Bearer ${await getToken()}` — this is how every other client component on the page fetches (confirmed from `page.tsx`)
- **UI-SPEC authority:** `10-UI-SPEC.md` already documents the correct pattern — execute against the UI-SPEC, not the plan's code template

### Plan 10-05 corrections
- **WRONG:** Stubs call `_collect_digest_stats(agent_id, tenant_conn_str)` — 2 args
- **CORRECT:** Actual signature is `_collect_digest_stats(agent_id, tenant_conn_str, db)` — 3 args (injected sync db)
- **WRONG:** Stubs call `send_digest_email(to_email="...", agent_name="...", stats={...})`
- **CORRECT:** Actual signature is `send_digest_email(agent_name, agent_id, stats)` — no `to_email`; email address comes from `settings.SMTP_TO`
- **WRONG:** Alert service stubs patch `app.services.alert_service.get_sync_db` — this import does not exist in the module
- **CORRECT:** `check_and_write_alerts(agent_id, agent_name, db)` — `db` is passed in by the Celery task; no `get_sync_db` call inside the service
- **WRONG:** Digest idempotency stub calls `.scalar_one_or_none()` on cursor result
- **CORRECT:** Actual code calls `.fetchone()` (raw psycopg2 cursor, not SQLAlchemy)
- **PASS (no change needed):** Both observability route stubs (test_observability_routes.py) use correct patterns

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| demo_m10.sh exits 0 with [PASS] lines printed | OPS-06 | Requires running Celery worker + live Redis + FastAPI | Start local services, run `ADMIN_KEY=<key> API_KEY=<key> bash scripts/demo_m10.sh` |
| AlertsBanner renders correctly in browser (no alerts hidden, Langfuse link visible) | OPS-03, OPS-04 | Visual verification cannot be automated | Start admin UI, navigate to `/agents/{id}`, confirm banner absent when 0 alerts, Langfuse link visible |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (created in 10-01)
- [x] No watch-mode flags
- [x] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter — pending plan reassessment

**Approval:** pending
