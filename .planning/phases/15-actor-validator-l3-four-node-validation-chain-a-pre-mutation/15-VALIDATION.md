---
phase: 15
slug: actor-validator-l3-four-node-validation-chain-a-pre-mutation-haiku-gate-in-the-agent-sdk-tool-loop
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-30
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `15-RESEARCH.md` → `## Validation Architecture` (line 484).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `apps/api/pyproject.toml` / `apps/api/pytest.ini` (existing) |
| **Quick run command** | `cd apps/api && python -m pytest tests/test_actor_seam.py -q` |
| **Full suite command** | `cd apps/api && python -m pytest -q` |
| **Estimated runtime** | ~30–60 seconds (unit); live-DB / live-API integration excluded from quick run |

---

## Sampling Rate

- **After every task commit:** Run the quick run command for the touched module
- **After every plan wave:** Run the full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

> The planner populates concrete Task IDs / commands from PLAN.md tasks. Rows below
> are the requirement→test backbone the planner MUST cover (no silent drops).

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 15-xx-xx | xx | 1 | ACT-02 | T-15-01 | Actor gate fires only for `mutating:true` tools; never for read-only tools | unit | `pytest tests/test_actor_seam.py -k mutating_only` | ❌ W0 | ⬜ pending |
| 15-xx-xx | xx | 1 | ACT-03 | — | Envelope `requires_confirmation:false` AND `max_amount_cents` < skip threshold → short-circuit, no Haiku call | unit | `pytest tests/test_actor_seam.py -k skip_threshold` | ❌ W0 | ⬜ pending |
| 15-xx-xx | xx | 1 | ACT-01 | T-15-02 | Single Haiku call → structured `approve\|block\|require_human` + rationale; injection in history does not flip to approve | unit | `pytest tests/test_actor_seam.py -k verdict` | ❌ W0 | ⬜ pending |
| 15-xx-xx | xx | 2 | ACT-04 | T-15-03 | `require_human` writes a `pending_confirmations` row, releases the idempotency reservation, and the action executes ONLY after `confirm_action` approval | integration (live control-DB) | `pytest tests/test_actor_require_human.py` | ❌ W0 | ⬜ pending |
| 15-xx-xx | xx | 2 | ACT-05 | — | Actor runs synchronously pre-mutation; Gatekeeper/Auditor/Strategist still dispatched async post-response (`agent.py` unchanged) | integration | `pytest tests/test_validation_chain_four_node.py` | ❌ W0 | ⬜ pending |
| 15-xx-xx | xx | 2 | ACT-06 | — | `actor_decision` persisted to `tool_calls_audit`; Actor latency recorded to Langfuse v4; p95 < 1s asserted | integration (live API) | `pytest tests/test_actor_latency.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `apps/api/tests/test_actor_seam.py` — unit stubs for ACT-01/02/03 (mutating-only gating, skip threshold, verdict parsing with `conn_str=""` no-history fallback)
- [ ] `apps/api/tests/test_actor_require_human.py` — require_human → pending_confirmations → confirm_action integration (ACT-04)
- [ ] `apps/api/tests/conftest.py` — reuse existing fixtures (Anthropic client stub / Langfuse no-op / control-DB fixture)

*Existing pytest infrastructure covers framework install — only new test files are needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Actor p95 < 1s against the real Haiku API | ACT-06 | Requires a live `ANTHROPIC_API_KEY` and representative load; not deterministic in CI | Run `pytest tests/test_actor_latency.py` with a real key over N≥20 mutating calls; assert measured p95 < 1000ms and total added latency < 1500ms |

*All other phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
