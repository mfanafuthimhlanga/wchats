---
phase: 5
slug: validation-chain
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-23
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3.0 |
| **Config file** | `apps/api/pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `pytest apps/api/tests/unit/test_validators.py -x` |
| **Full suite command** | `pytest apps/api/tests/unit/ -x` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest apps/api/tests/unit/ -x -q`
- **After every plan wave:** Run `pytest apps/api/tests/unit/ -x`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 05-01 | migrations | 1 | VAL-01–06 | T-prompt-injection | Pydantic model_validate before any DB write | unit | `pytest apps/api/tests/unit/test_validators.py -x` | ❌ W0 | ⬜ pending |
| 05-02 | validation_service | 1 | VAL-01–03 | T-unvalidated-output | Haiku output always model_validate'd; never written raw | unit | `pytest apps/api/tests/unit/test_validators.py -x` | ❌ W0 | ⬜ pending |
| 05-03 | validators tasks | 2 | VAL-04 | T-message-size | retrieved_context truncated to top-3 chunks ≤600 chars | unit | `pytest apps/api/tests/unit/test_validators.py::test_run_gatekeeper_task -x` | ❌ W0 | ⬜ pending |
| 05-04 | chain dispatch | 2 | VAL-04 | — | Chain dispatched from run_agent_turn | unit | `pytest apps/api/tests/unit/test_agent_task.py::test_validators_dispatched -x` | ❌ W0 | ⬜ pending |
| 05-05 | Langfuse logging | 3 | VAL-05 | T-cred-leak | LANGFUSE keys from env vars only; never in task args | unit | `pytest apps/api/tests/unit/test_validators.py::test_langfuse_logged -x` | ❌ W0 | ⬜ pending |
| 05-06 | resynthesis flag | 3 | VAL-06 | — | Flag set after 3 consecutive ungrounded verdicts | unit | `pytest apps/api/tests/unit/test_validators.py::test_resynthesis_flag -x` | ❌ W0 | ⬜ pending |
| 05-07 | verified_qa_candidates | 3 | VAL-02 | T-false-positive-vqa | confidence ≥ 0.90 threshold; row contains auditor_confidence | unit | `pytest apps/api/tests/unit/test_validators.py::test_auditor_inserts_candidate -x` | ❌ W0 | ⬜ pending |
| 05-08 | demo script | 4 | VAL-07 | — | N/A | manual | N/A — human walks Langfuse UI | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `apps/api/tests/unit/test_validators.py` — stubs for VAL-01 through VAL-06 (all test functions declared, body `pass` or `pytest.skip`)
- [ ] `apps/api/app/services/validation_service.py` — Pydantic verdict models + Haiku call stubs
- [ ] `apps/api/app/worker/tasks/runtime/validators.py` — three Celery task stubs (`run_gatekeeper`, `run_auditor`, `run_strategist`)
- [ ] Add `LANGFUSE_PUBLIC_KEY="test_lf_pk"`, `LANGFUSE_SECRET_KEY="test_lf_sk"`, `LANGFUSE_HOST="https://cloud.langfuse.com"` to `apps/api/tests/conftest.py` env setup block
- [ ] Add `langfuse==3.12.1` to `apps/api/pyproject.toml` (and `pip install` in dev env)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Adversarial query in widget shows all three validator verdicts in Langfuse trace | VAL-07 | Langfuse UI walkthrough; no automated assertion on cloud trace | Run `scripts/demo_m5.sh`; open Langfuse trace; verify Gatekeeper + Auditor + Strategist spans each have a structured verdict payload |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
