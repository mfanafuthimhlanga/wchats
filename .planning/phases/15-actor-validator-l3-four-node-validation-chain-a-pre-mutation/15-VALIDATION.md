---
phase: 15
slug: actor-validator-l3-four-node-validation-chain-a-pre-mutation-haiku-gate-in-the-agent-sdk-tool-loop
status: approved
nyquist_compliant: true
wave_0_complete: false
created: 2026-06-30
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `15-RESEARCH.md` → `## Validation Architecture` (line 484).
> Paths reconciled to the committed plans (15-01/02/03) after the plan-checker pass.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `apps/api/pyproject.toml` / `apps/api/pytest.ini` (existing) |
| **Quick run command** | `cd apps/api && python -m pytest tests/unit/test_actor_seam.py -q` |
| **Full suite command** | `cd apps/api && python -m pytest tests/unit/ -q` |
| **Estimated runtime** | ~30–60 seconds (unit). Live integration (`tests/integration/`) is env-gated and excluded from the quick run. |

---

## Sampling Rate

- **After every task commit:** Run the quick run command for the touched module
- **After every plan wave:** Run `cd apps/api && python -m pytest tests/unit/ -q`
- **Before `/gsd-verify-work`:** Full unit suite green; live integration run once with a real `ANTHROPIC_API_KEY` + control DB
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 15-01-01 | 01 | 1 | ACT-03 | — | `ACTOR_SKIP_MAX_AMOUNT_CENTS` setting exists (default 500, env-overridable) | source | `python -c "from app.core.config import settings; assert settings.ACTOR_SKIP_MAX_AMOUNT_CENTS == 500"` | ❌ W0 | ⬜ pending |
| 15-01-02 | 01 | 1 | ACT-01, ACT-06 | T-15-01 | `call_actor_gate()` body: forced-tool-use Haiku (`submit_verdict`) → `approve\|block\|require_human` + rationale; Langfuse v4 latency | source | `python -c "import ast; assert 'submit_verdict' in open('app/services/actor_seam.py').read()"` | ❌ W0 | ⬜ pending |
| 15-01-03 | 01 | 1 | ACT-02, ACT-03 | T-15-01 | Skip short-circuit (`requires_confirmation:false` AND `max_amount_cents` < threshold) returns approve WITHOUT calling the Anthropic client | unit | `pytest tests/unit/test_actor_seam.py -q` | ❌ W0 | ⬜ pending |
| 15-02-01 | 02 | 2 | ACT-04 | T-15-02 | `require_human` releases the idempotency reservation FIRST, then writes a `pending_confirmations` row + audit row; adapter NOT called | source | `py_compile + grep 'elif decision == "require_human":'` | ❌ W0 | ⬜ pending |
| 15-02-02 | 02 | 2 | ACT-02, ACT-05 | — | Actor fires on mutating tools, never on `confirm_action` (mutating=False); four-node structural test asserts agent.py async chain unchanged | unit | `pytest tests/unit/test_transactional_tools.py -q` | ❌ W0 | ⬜ pending |
| 15-03-01 | 03 | 3 | ACT-04, ACT-05 | T-15-02 | Live control-DB: require_human → pending row → executes ONLY after `confirm_action` approval; four-node ordering confirmed | integration (live DB) | `ACTOR_E2E_ENABLED=1 pytest tests/integration/test_actor_require_human.py -q` | ❌ W0 | ⬜ pending |
| 15-03-02 | 03 | 3 | ACT-06 | — | Live Haiku: Actor p95 < 1s and total added latency < 1.5s over N≥20 mutating calls; `actor_decision` persisted to `tool_calls_audit` | integration (live API) | `ACTOR_LATENCY_ENABLED=1 pytest tests/integration/test_actor_latency.py -q` | ❌ W0 | ⬜ pending |
| 15-03-03 | 03 | 3 | ACT-04, ACT-06 | — | Human-verify checkpoint: live require_human + latency gate reviewed and approved | manual (checkpoint) | N/A — `checkpoint:human-verify` | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `apps/api/tests/unit/test_actor_seam.py` — ACT-01/02/03 unit (mutating-only gating, skip threshold no-Haiku-call, verdict parsing with `conn_str=""` no-history fallback) — created by 15-01 Task 3
- [ ] `apps/api/tests/unit/test_transactional_tools.py` — require_human dispatcher + four-node structural assertion (extends existing file) — by 15-02 Task 2
- [ ] `apps/api/tests/integration/test_actor_require_human.py` — live require_human → confirm_action e2e (env-gated `ACTOR_E2E_ENABLED`) — by 15-03 Task 1
- [ ] `apps/api/tests/integration/test_actor_latency.py` — live p95 assertion (env-gated `ACTOR_LATENCY_ENABLED`) — by 15-03 Task 2

*Existing pytest infrastructure + `conftest.py` fixtures cover framework install — only the new test files above are needed, and they are created inline by the execution tasks (no separate Wave 0 pass).*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Actor p95 < 1s against the real Haiku API | ACT-06 | Requires a live `ANTHROPIC_API_KEY` and representative load; not deterministic in CI | `ACTOR_LATENCY_ENABLED=1 pytest tests/integration/test_actor_latency.py -q` over N≥20 mutating calls; assert measured p95 < 1000ms and total added latency < 1500ms |
| require_human approval-then-execute against live control DB | ACT-04 | Requires a live control DB + Redis; 15-03 checkpoint is `autonomous: false` | Bring up local Postgres + `redis-server`; `ACTOR_E2E_ENABLED=1 pytest tests/integration/test_actor_require_human.py -q`; confirm the action executes only after `confirm_action` |

*All other phase behaviors have automated unit verification.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or are an explicit manual checkpoint
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 test files mapped to the execution tasks that create them
- [x] No watch-mode flags
- [x] Feedback latency < 60s (unit)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-06-30 (plan-checker VERIFICATION PASSED; Nyquist Dimension 8 a–e PASS)
