---
phase: 08
slug: pre-deployment-checklist
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-05-23
---

# Phase 08 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing, apps/api/pyproject.toml) |
| **Config file** | apps/api/pyproject.toml `[tool.pytest]` section |
| **Quick run command** | `cd apps/api && python -m pytest tests/unit/test_deployment_service.py tests/unit/test_deployment_task.py -x -q` |
| **Full suite command** | `cd apps/api && python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~45 seconds (unit only), ~90 seconds (full suite) |

---

## Sampling Rate

- **After every task commit:** Run `cd apps/api && python -m pytest tests/unit/test_deployment_service.py tests/unit/test_deployment_task.py -x -q`
- **After every plan wave:** Run `cd apps/api && python -m pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 08-01-01 | 01 | 1 | DEP-03, DEP-06 | — | migration idempotent (IF NOT EXISTS) | source | `alembic history` shows 0011 | ❌ W0 | ⬜ pending |
| 08-02-01 | 02 | 2 | DEP-01, DEP-02, DEP-03 | T-08-01 | submit_report is side-effect only; signals treated as data | unit | `pytest tests/unit/test_deployment_service.py -x -q` | ❌ W0 | ⬜ pending |
| 08-03-01 | 03 | 3 | DEP-01, DEP-02, DEP-03 | T-08-02, T-08-04 | conn_str never in task args; idempotency skip on running row | unit | `pytest tests/unit/test_deployment_task.py -x -q` | ❌ W0 | ⬜ pending |
| 08-04-01 | 04 | 3 | DEP-04, DEP-05, DEP-06 | T-08-03, T-08-05 | IDOR check on every route; approve blocked when recommendation=block | unit | `pytest tests/unit/test_deployment_routes.py -x -q` | ❌ W0 | ⬜ pending |
| 08-05-01 | 05 | 4 | DEP-04, DEP-05, DEP-06 | — | Pre-Deploy tab is default; approve button disabled until all warnings acked | manual | Visual browser check | N/A | ⬜ pending |
| 08-06-01 | 06 | 5 | DEP-01–DEP-07 | — | All xfail stubs de-xfailed; full unit suite green | unit | `pytest tests/unit/ -x -q` | ❌ W0 | ⬜ pending |
| 08-07-01 | 07 | 6 | DEP-07, DEP-08 | — | demo_m8.sh exits 0; is_deployed=true assertion passes | e2e | `DEP_E2E_ENABLED=1 pytest tests/integration/test_deployment_e2e.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_deployment_service.py` — xfail stubs for DEP-01, DEP-02, DEP-03 (de-xfailed in Plan 08-06)
- [ ] `tests/unit/test_deployment_task.py` — xfail stubs for run_deployment_checklist idempotency, happy path, failure path (de-xfailed in Plan 08-06)
- [ ] `tests/unit/test_deployment_routes.py` — xfail stubs for DEP-04, DEP-05, DEP-06 route behavior (de-xfailed in Plan 08-06)
- [ ] `tests/integration/test_deployment_e2e.py` — guarded E2E stub with DEP_E2E_ENABLED guard (de-xfailed in Plan 08-07)

*All stubs planted in Plan 08-01 (Wave 0 task). De-xfailed in Plans 08-06 and 08-07.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Pre-Deploy tab renders as default tab on /agents/[id]/deploy | DEP-04 | Next.js UI — no Playwright setup in this phase | Open browser, navigate to deploy page, verify Pre-Deploy is selected by default |
| Running state shows spinner + polling | DEP-04 | Animated UI state requires visual inspection | Trigger checklist run, observe 3s poll interval and spinner text |
| ship_with_warnings shows per-warning checkboxes | DEP-05 | Checkbox interaction requires browser | Trigger checklist with warning-producing agent, verify each checkbox must be checked before Approve enables |
| Approved state shows green Live badge | DEP-06 | Visual state | Approve a ship agent, verify green "Live" badge + redirect hint to Embed Code tab |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify — NOTE: 08-05 (Task 08-05-01) is manual-only; 08-04 (automated) and 08-06 (automated) bracket it, so no 3-consecutive gap exists. Borderline but acceptable.
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 90s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending execution
