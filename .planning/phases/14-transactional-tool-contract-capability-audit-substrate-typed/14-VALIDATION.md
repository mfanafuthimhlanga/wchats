---
phase: 14
slug: transactional-tool-contract-capability-audit-substrate-typed
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-29
---

# Phase 14 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `14-RESEARCH.md` § Validation Architecture. The planner fills the per-task map.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x/8.x (existing — `apps/api/tests/`) |
| **Config file** | `apps/api/pyproject.toml` / existing pytest config |
| **Quick run command** | `pytest apps/api/tests/unit -q` |
| **Full suite command** | `pytest apps/api/tests -q` |
| **Estimated runtime** | unit fast; no live providers (StubProviderAdapter) so the whole phase is offline-testable |

---

## Sampling Rate

- **After every task commit:** `pytest apps/api/tests/unit -q`
- **After every plan wave:** `pytest apps/api/tests -q`
- **Before `/gsd-verify-work`:** full suite green
- **Max feedback latency:** ~120s (unit)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Created By | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-----------------|--------|
| 14-01-01 | 01 | 1 | CAP-01, AUD-01, AUD-02 | T-14-01 | 4 control-DB tables; no tenant PII; fail-closed default | unit/migration | `pytest tests/unit/test_migration_0014.py -x -q` | task | ⬜ pending |
| 14-02-01 | 02 | 1 | TXN-01, TXN-03 | T-14-02 | typed schemas reject string-blob/SQL/URL; mutating flag present | unit | `pytest tests/unit/test_transactional_tools.py -x -q` | task | ⬜ pending |
| 14-03-01 | 03 | 2 | TXN-02 | T-14-03 | replay w/ same idempotency key returns stored result, no re-execute | unit | `pytest tests/unit/test_tool_idempotency.py -x -q` | task | ⬜ pending |
| 14-04-01 | 04 | 2 | CAP-02 | T-14-04 | disabled/over-limit/constraint-violation → capability.denial; fail-closed | unit | `pytest tests/unit/test_capability_enforcement.py -x -q` | task | ⬜ pending |

*Planner replaces these seed rows with the full per-task map.*

---

## Wave 0 Requirements (created in-task, tdd-style)

- [ ] `apps/api/tests/unit/test_migration_0014.py` — 4 tables present, correct columns, control-DB
- [ ] `apps/api/tests/unit/test_transactional_tools.py` — typed-schema rejection + `mutating` flag + `TransactionalToolDef` metadata
- [ ] `apps/api/tests/unit/test_tool_idempotency.py` — replay returns stored result; no double-mutation under simulated retry
- [ ] `apps/api/tests/unit/test_capability_enforcement.py` — denial paths + fail-closed + ordering (capability → seam → idempotency → execute → audit)
- [ ] `StubProviderAdapter` makes the full path exercisable offline

*Key validation landmines (from research): (1) the Actor pre-execution **seam** must be a real call-site inside the tool dispatch (Phase 15 fills it) — verify it's invoked for `mutating:true` tools; (2) idempotency must survive `acks_late` retries — test the double-execute guard at the DB `UNIQUE` layer, not Redis; (3) audit row written for 100% of mutating calls incl. `capability_snapshot`.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| (none expected) | — | StubProviderAdapter makes the phase fully offline-testable | — |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
</content>
