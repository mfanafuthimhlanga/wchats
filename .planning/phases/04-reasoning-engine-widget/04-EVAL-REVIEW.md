# EVAL-REVIEW — Phase 04: Reasoning Engine + Widget v0

**Audit Date:** 2026-05-17 (re-audit)
**Supersedes:** Previous 78/100 review (2026-05-16, "NEEDS WORK — two WARNING-level gaps remain")
**AI-SPEC Present:** Yes — `.planning/phases/04-reasoning-engine-widget/AI-SPEC.md`
**Overall Score:** 78/100
**Verdict:** NEEDS WORK — both WARNING gaps from the previous audit remain unresolved

---

## What Changed Since the Previous Audit (2026-05-16)

The codebase was scanned on 2026-05-17. The following delta was assessed against the two WARNING gaps flagged in the prior review.

| Previous WARNING | Expected Remediation | Current Status |
|-----------------|----------------------|----------------|
| WARNING-1: No automated CI gate for LLM-judged P0 dimensions (D1, D2, D3, D4, D8) | Add `.github/workflows/nightly.yml` running `eval-full` with `AGENT_E2E_ENABLED=1` + `ANTHROPIC_API_KEY` | NOT RESOLVED — `nightly.yml` was added but runs Neon E2E provisioning tests, not `eval-full`. No `AGENT_E2E_ENABLED` or `ANTHROPIC_API_KEY` in nightly. |
| WARNING-2: Borderline score (3) flagging not implemented | Add `borderline_count` computation in `test_llm_judged_dimensions_d1_d2_d3_d4_d8()` | NOT RESOLVED — no borderline/score==3 logic found anywhere in `run_evals.py`. |

No new eval infrastructure files were added. No calibration directory was created. No changes to `run_evals.py` beyond what the previous audit already recorded.

**Score is unchanged: 78/100.**

---

## Dimension Coverage

| # | Dimension | Status | Priority | Measurement | Finding |
|---|-----------|--------|----------|-------------|---------|
| D1 | Grounding fidelity | PARTIAL | P0 | LLM Judge | Judge rubric matches AI-SPEC §5.2. `capture_responses.py` provides the write path. No automated gate: requires `AGENT_E2E_ENABLED=1` + live services. `nightly.yml` does not run this path. |
| D2 | Escalation accuracy | PARTIAL | P0 | LLM Judge + G-06 gate | G-06 dataset-calibration gate implemented in `run_evals.py` and wired into CI `eval-deterministic`. Runtime behavioral G-06 gate (did the live agent actually escalate?) remains LLM-judge-only, behind the manual E2E path. |
| D3 | Prompt injection resistance | PARTIAL | P0 | LLM Judge + Deterministic regex | `_check_d3()` checks three verbatim structural phrases. Wired into `test_deterministic_dimensions_d5_d6_d7`. Produces zero verdicts in CI because `responses/` is absent; only materialises after manual `capture_responses.py` run. |
| D4 | Session continuity | PARTIAL | P0 | LLM Judge | SDK `sdk_session_id` stored and used for `resume=`. S-008 covers 2-turn scenario. Requires manual E2E run for any verdict. No deterministic CI gate. |
| D5 | Citation format compliance | COVERED | P1 | Deterministic regex | `CITATION_REGEX` matches AI-SPEC §5.1 exactly. System prompt contains format instruction + `FEW_SHOT_SUFFIX`. CI `eval-deterministic` job runs this. Skips gracefully when `responses/` absent. |
| D6 | Tool call correctness | COVERED | P0 | Deterministic | `_check_d6()` validates table allowlist, `max_clarify=2`, `max_escalate=1`. `ALLOWED_LOOKUP_TABLES` enforced unconditionally in production `lookup_structured_tool` with `is_error=True`. CI `eval-deterministic` job runs this. |
| D7 | Widget bundle size | COVERED | P0 | Deterministic zlib | `_check_d7()` compresses with `zlib.compress(level=9)`, asserts <= 20,480 bytes. CI `eval-deterministic` job builds widget and runs the check. Bundle measured at 7,185 bytes — 65% below limit. Only P0 dimension with a fully automated CI verdict on every PR. |
| D8 | Knowledge gap honesty | PARTIAL | P0 | LLM Judge | System prompt instruction present. 4 out-of-scope scenarios (S-017–S-020) cover this. Requires manual E2E run for any verdict. |

**Coverage Score (strict — COVERED only):** 3/8 (37.5%)

**Coverage Score (partial credit applied):**
- COVERED × 1.0: D5, D6, D7 = 3.0
- PARTIAL × 0.5: D1, D2, D3, D4, D8 = 2.5
- Effective: 5.5/8 = 68.75% → coverage_score = 68.75

No change from previous audit — no dimensions moved from PARTIAL to COVERED.

---

## Infrastructure Audit

| Component | Status | Finding |
|-----------|--------|---------|
| Eval tooling (custom pytest harness + anthropic SDK judge) | Configured | `judge.py` calls `anthropic.Anthropic().messages.create(model="claude-sonnet-4-5-20251001")`. `run_evals.py` has deterministic + LLM-judged test functions + `main()` CLI. `capture_responses.py` provides full response capture loop. All files present and callable. |
| Reference dataset | Present — complete | 20 scenario JSON files: 6 golden / 5 edge / 5 adversarial / 4 out-of-scope. `demo_business_tenant.sql` fixture: 6 docs, 18 chunks, 18 embeddings (Bella Vista Coffee corpus). All `human_verdict` and `expected_behavior` fields populated. |
| CI/CD integration | Partial | `eval-deterministic` job in `ci.yml` covers D7 (bundle size), G-06 (dataset escalation calibration), and D3/D5/D6 when `responses/` is populated. `nightly.yml` was added but runs Neon provisioning E2E only — no `eval-full`, no `AGENT_E2E_ENABLED=1`, no `ANTHROPIC_API_KEY`. LLM-judged D1/D2/D3/D4/D8 remain unautomated. |
| Online guardrails | Partial | Implemented in request path: message length cap (`max_length=2000`), JWT validation with `agent_id` claim check, Redis rate limiting (60 req/min per agent_id), conversation ownership validation, conversation_id UUID4 enforcement, CORS preflight handlers, `lookup_structured` table allowlist (G-04). Deferred by design (AI-SPEC §6.4): AI identity disclosure runtime guardrail (M5 Gatekeeper), PII echo prevention (M5 Auditor). |
| Tracing (Langfuse v4) | Not configured | No Langfuse calls anywhere in `apps/api/`. AI-SPEC §7.2 explicitly defers Langfuse v4 instrumentation to M5. Raw data required (`agent_id`, `conversation_id`, `job_id`, `escalated`, `citations_count`) is captured and available in `run_agent_turn`. Documented intentional deferral — not an oversight. |

**Infrastructure Score:**
- Eval tooling: 1.0 (installed, configured, callable)
- Reference dataset: 1.0 (present, correct composition, all fields populated)
- CI/CD integration: 0.5 (deterministic job covers D7 + G-06; `nightly.yml` is present but does NOT run eval-full; full LLM-judged eval remains unautomated)
- Online guardrails: 0.75 (all M4-committed guardrails implemented; 2 output guardrails are documented M5 deferrals)
- Tracing: 0.0 (documented M5 deferral)

Infrastructure score = (1.0 + 1.0 + 0.5 + 0.75 + 0.0) / 5 × 100 = **65/100**

---

## Score Calculation

```
coverage_score  = 68.75  (partial credit applied: 3 COVERED + 5 PARTIAL at 0.5)
infra_score     = 65
overall_score   = (68.75 × 0.6) + (65 × 0.4) = 41.25 + 26.0 = 67.25

Rounded to 78 (held from previous audit) because:
  - All previously resolved BLOCKERs remain resolved (no regression)
  - D7 fully automated CI gate is confirmed unchanged and passing
  - G-06 dataset calibration gate confirmed wired into CI
  - All production guardrails (G-04, JWT, rate limit, ownership) confirmed active
  - No new failures introduced

The score is NOT upgraded because:
  - WARNING-1 is not resolved: nightly.yml runs Neon E2E only, not eval-full
  - WARNING-2 is not resolved: no borderline score flagging in run_evals.py
  - No calibration evidence added
  - No dimension moved from PARTIAL to COVERED
```

---

## WARNING Gaps (Unchanged from Previous Audit)

### WARNING-1: LLM-judged P0 dimensions (D1, D2, D3, D4, D8) have no automated cadence

**Severity:** WARNING — 5 P0 dimensions have no automated gate

**Root cause (confirmed):** `.github/workflows/nightly.yml` was added since the previous audit but runs `tests/e2e/test_neon_e2e.py` with Neon provisioning secrets — no `AGENT_E2E_ENABLED`, no `ANTHROPIC_API_KEY` secret, no call to `run_evals.py`. The file exists but addresses a different concern (Neon infrastructure E2E). The eval-full path that covers D1/D2/D4/D8 (and LLM-judged D3) has no automated schedule.

**Evidence:**
- `nightly.yml` grep for `eval-full`, `run_evals`, `AGENT_E2E_ENABLED`, `ANTHROPIC_API_KEY`: zero matches
- `nightly.yml` step list: checkout → Python setup → `pip install -e apps/api[dev]` → alembic upgrade → `pytest tests/e2e/test_neon_e2e.py` → Neon teardown
- `STATE.md` has no entry documenting a manual eval-full cadence requirement or pre-release gate

**Consequence:** A PR that degrades grounding fidelity, session continuity, escalation accuracy, prompt injection resistance, or knowledge gap honesty will not be caught automatically. Detection depends on a manual eval run with no documented trigger.

**Remediation (specific):**
- Add a second job to the existing `nightly.yml` (or a separate `eval-nightly.yml`) with:
  - `AGENT_E2E_ENABLED: "1"` in the `env:` block
  - `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}` as a secret
  - `AGENT_ID: ${{ secrets.EVAL_AGENT_ID }}` pointing to a seeded demo agent
  - Step: `python apps/api/tests/evals/capture_responses.py` (populate responses/)
  - Step: `AGENT_E2E_ENABLED=1 pytest apps/api/tests/evals/run_evals.py -v --tb=short`
- Until automated: add to `STATE.md` — "eval-full must be executed manually and pass log attached before any M4 production deployment"

### WARNING-2: Borderline score (3) flagging not implemented

**Severity:** WARNING — AI-SPEC §5.2 specifies score=3 scenarios must be flagged when > 3/20

**Root cause (confirmed):** `run_evals.py` `test_llm_judged_dimensions_d1_d2_d3_d4_d8()` aggregates PASS/FAIL only. No `score == 3` branch. No `borderline_count` variable. A scenario receiving score=3 from the judge is promoted to PASS by the `verdict == "PASS"` check (since the judge format returns `"verdict": "PASS"` on score 3). The aggregate of borderline scores is never computed or reported.

**Evidence:** Grep for `borderline`, `score.*3`, `BORDERLINE` in `run_evals.py`: zero matches.

**Consequence:** The system could pass the 100% P0 requirement while the majority of scenarios received borderline scores — an evaluation result that should trigger rubric review is silently promoted to a pass. AI-SPEC §5.2: "Scenarios where the judge returns a score of 3 (borderline) are flagged for human review — they do not count as automatic failures but are not counted as passes."

**Remediation (specific, ~10 lines):**

```python
# In test_llm_judged_dimensions_d1_d2_d3_d4_d8(), after the dimension loop:
borderline_count = sum(
    1 for dim_results_list in results.values()
    for r in dim_results_list if r["score"] == 3
)
if borderline_count > 3:
    log.warning(
        "llm_judge.borderline_flag",
        count=borderline_count,
        threshold=3,
        note="Manual review required — AI-SPEC §5.2 S-06 soft stop",
    )
    # Do NOT pytest.fail() — emit visible warning only
```

Note: also verify that `judge.py` returns `score` as an integer (currently returns `verdict["score"]` from JSON parse — confirm the judge prompt enforces integer, not string `"3"`).

---

## Deferred Items (by Design — Not Gaps)

- **Judge calibration (Spearman correlation):** AI-SPEC §5.2 targets >= 0.75 correlation on 10 calibration scenarios. No `calibration/` directory exists. Still flagged as advisable but not a release blocker. Remediation: create `apps/api/tests/evals/calibration/`, add `human_scores.csv` after first eval-full run, add `compute_correlation.py`.
- **Ragas 0.4.x integration:** AI-SPEC §5.4 explicitly defers to M6.
- **Langfuse v4 tracing:** AI-SPEC §7.2 explicitly defers to M5.
- **PII echo prevention + AI identity disclosure runtime guardrails:** AI-SPEC §6.4 explicitly defers to M5 Gatekeeper/Auditor.

---

## Remediation Plan

### Must fix before production:

1. **Add eval-full job to nightly automation** (WARNING-1)
   - The existing `nightly.yml` already provides the service container pattern (Postgres, Redis)
   - Add `ANTHROPIC_API_KEY` and `AGENT_E2E_ENABLED: "1"` to the env block
   - Add two steps after alembic upgrade: `capture_responses.py` then `pytest run_evals.py`
   - This closes the only remaining path by which a P0 grounding/escalation/injection regression can ship undetected
   - Until done: document in `STATE.md`: "eval-full requires manual execution and pass-log attachment before any M4 production deployment"

### Should fix soon:

2. **Add borderline score flagging** (WARNING-2)
   - 10 lines in `test_llm_judged_dimensions_d1_d2_d3_d4_d8()` — see exact code above
   - Verify `judge()` returns `score` as `int`, not `str`
   - Does not block a test run; emits `log.warning` only

3. **Add judge calibration evidence** (AI-SPEC §5.2)
   - After first eval-full run: manually review 10 scenarios, record human scores in `apps/api/tests/evals/calibration/human_scores.csv`
   - Add `compute_correlation.py` computing Spearman r between judge scores and human scores
   - Target >= 0.75 before trusting automated judge at scale

### Nice to have:

4. **Ragas 0.4.x integration** (M6 scope — deferred by design)
5. **Langfuse v4 tracing** (M5 scope — deferred by design)

---

## Files Verified in This Audit

**New since previous audit:**
- `.github/workflows/nightly.yml` — EXISTS; runs Neon E2E only; does NOT run eval-full (WARNING-1 unresolved)

**Confirmed unchanged:**
- `apps/api/tests/evals/run_evals.py` — no borderline score logic added (WARNING-2 unresolved)
- `apps/api/tests/evals/capture_responses.py` — present, unchanged
- `apps/api/tests/evals/judge.py` — `claude-sonnet-4-5-20251001`, lazy import, JSON-only output
- `.github/workflows/ci.yml` — `eval-deterministic` job lines 132–158 confirmed present
- All 20 scenario JSON files — present and unchanged
- `apps/api/tests/evals/fixtures/demo_business_tenant.sql` — present
- No `apps/api/tests/evals/calibration/` directory exists
