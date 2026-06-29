---
phase: 14
slug: transactional-tool-contract-capability-audit-substrate-typed
status: planned
nyquist_compliant: true
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
| 14-01-01 | 01 | 1 | CAP-01, AUD-01, AUD-02, TXN-02 | T-14-01-01/02/03/04 | migration 0014: 4 control-DB tables; enabled server_default false (fail-closed); UNIQUE(agent_id,skill) + UNIQUE(agent_id,skill,idempotency_key); down_revision 0013 | unit/migration | `cd apps/api && pytest tests/unit/test_migration_0014.py -x -q` | task | ⬜ pending |
| 14-01-02 | 01 | 1 | CAP-01, AUD-01, AUD-02 | T-14-01-01/02 | 4 ORM models mirror migration constraints + fail-closed defaults; registered in app.models | unit | `cd apps/api && pytest tests/unit/test_migration_0014.py -x -q` | task | ⬜ pending |
| 14-02-01 | 02 | 1 | TXN-01 | T-14-02-01 | 14 typed Pydantic models; idempotency_key required on all 6 mutating inputs; no blob/SQL/URL/open-dict fields | unit | `cd apps/api && pytest tests/unit/test_transactional_contract.py -x -q` | task | ⬜ pending |
| 14-02-02 | 02 | 1 | TXN-03, TXN-05 | T-14-02-02 | definition-time mutating flags in TOOL_REGISTRY (6 True, confirm_action False); A2A metadata captured | unit | `cd apps/api && pytest tests/unit/test_transactional_contract.py -x -q` | task | ⬜ pending |
| 14-02-03 | 02 | 1 | TXN-01 | T-14-02-03 | StubProviderAdapter offline ([STUB] outputs) behind ProviderAdapter ABC; call_actor_gate pass-through approve stub | unit | `cd apps/api && pytest tests/unit/test_transactional_contract.py -x -q` | task | ⬜ pending |
| 14-03-01 | 03 | 2 | CAP-02 | T-14-03-01/05 | fail-closed denial on missing/disabled/over-limit/constraint-violation; capability.denial logged; Redis only for rate counter | unit | `cd apps/api && pytest tests/unit/test_capability_enforcement.py -x -q` | task | ⬜ pending |
| 14-03-02 | 03 | 2 | TXN-02 | T-14-03-02 | replay served from control-DB tool_idempotency_keys (UNIQUE), ON CONFLICT DO NOTHING; not Redis; survives acks_late | unit | `cd apps/api && pytest tests/unit/test_tool_idempotency.py -x -q` | task | ⬜ pending |
| 14-03-03 | 03 | 2 | AUD-01 | T-14-03-03/04 | one tool_calls_audit row per execution incl. error path; capability_snapshot plain dict; actor cols empty in P14 | unit | `cd apps/api && pytest tests/unit/test_capability_enforcement.py -x -q` | task | ⬜ pending |
| 14-04-01 | 04 | 3 | TXN-01, TXN-02, TXN-03, AUD-01 | T-14-04-01/02 | dispatcher order capability→idempotency(short-circuit)→actor seam→execute(stub)→audit→store; seam unbypassable; replay no re-execute/no 2nd audit | unit | `cd apps/api && pytest tests/unit/test_transactional_tools.py -x -q` | task | ⬜ pending |
| 14-04-02 | 04 | 3 | TXN-04, AUD-02 | T-14-04-05 | confirm_action writes pending_confirmations, no adapter, no idempotency key (mutating=False); registry sdk_tool attached | unit | `cd apps/api && pytest tests/unit/test_transactional_tools.py -x -q` | task | ⬜ pending |
| 14-04-03 | 04 | 3 | TXN-01, TXN-04 | T-14-04-03/04 | 7 tools registered in build_tool_server + allowed_tools; existing 4 (incl. escalate_to_human) retained; envelope still gates | unit | `cd apps/api && pytest tests/unit/test_transactional_tools.py -x -q` | task | ⬜ pending |

**Open-question resolutions baked into the plans:**
- (a) `confirm_action` is `mutating=False` — writes a pending_confirmations row, no provider action, no idempotency key. Duplicate-confirm dedup deferred to Phase 18; PRD §4.3 DDL unchanged.
- (b) The idempotency lookup is hoisted above the Actor seam so replays short-circuit to the stored result before Actor+execute (no redundant Haiku call). Capability check still runs first on every call (fail-closed on replays); `call_actor_gate` still gates 100% of fresh executions.

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
