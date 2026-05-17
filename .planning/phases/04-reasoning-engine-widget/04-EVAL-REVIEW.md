# EVAL-REVIEW — Phase 04: Reasoning Engine + Widget v0

**Audit Date:** 2026-05-17 (updated after calibration infrastructure added)
**Supersedes:** Second audit (2026-05-17, "NEEDS WORK — both WARNING gaps unresolved, score 78/100")
**AI-SPEC Present:** Yes — `.planning/phases/04-reasoning-engine-widget/AI-SPEC.md`
**Overall Score:** 90/100
**Verdict:** PRODUCTION READY — both WARNING gaps resolved; all P0 dimensions now have automated gates

---

## What Changed Since the Second Audit (2026-05-17)

Two WARNING gaps were committed for resolution. Both are now confirmed resolved by direct code inspection.

| Previous WARNING | Required Remediation | Verification Result |
|-----------------|----------------------|---------------------|
| WARNING-1: No automated CI gate for LLM-judged P0 dimensions (D1, D2, D3, D4, D8) | Add `eval-full` job to `nightly.yml` with `AGENT_E2E_ENABLED: "1"`, `ANTHROPIC_API_KEY`, `capture_responses.py --overwrite` step, `pytest run_evals.py -v --tb=short` (not `-k deterministic`) | RESOLVED — confirmed at nightly.yml lines 82–172 |
| WARNING-2: Borderline score (3) flagging not implemented per AI-SPEC §5.2 S-06 | Add `borderline_count` sum over `score == 3` after the aggregate loop; emit `log.warning` when > 3 | RESOLVED — confirmed at run_evals.py lines 436–446 |

---

## WARNING-1 Verification Detail

File: `.github/workflows/nightly.yml`

Required criteria, each verified:

| Criterion | Required | Found | Line |
|-----------|----------|-------|------|
| Second job named `eval-full` | Yes | `eval-full:` job with name "Eval Full — LLM-judged D1/D2/D3/D4/D8" | 82–83 |
| `AGENT_E2E_ENABLED: "1"` env var | Yes | Present in `env:` block | 118 |
| `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}` | Yes | Present in `env:` block | 116 |
| Step running `capture_responses.py --overwrite` | Yes | `python tests/evals/capture_responses.py --overwrite` | 168 |
| Step running `pytest tests/evals/run_evals.py -v --tb=short` (NOT `-k deterministic`) | Yes | Exact command confirmed; no `-k deterministic` flag | 172 |

Additional detail: the `eval-full` job starts a live API server (`uvicorn`) and Celery worker before the capture step, providing the full service environment required for the agent to execute and produce real responses. The job runs on the same nightly `0 2 * * *` cron as the Neon E2E job.

WARNING-1: RESOLVED.

---

## WARNING-2 Verification Detail

File: `apps/api/tests/evals/run_evals.py`

Required criteria, each verified:

| Criterion | Required | Found | Lines |
|-----------|----------|-------|-------|
| `borderline_count` variable computed after aggregate loop | Yes | `borderline_count = sum(1 for dim_results_list in results.values() for r in dim_results_list if r["score"] == 3)` | 437–440 |
| Placed after `for dim, dim_results in results.items():` loop | Yes | Aggregate loop ends at line 434; borderline block begins line 436 | 415–446 |
| `log.warning` emitted when `borderline_count > 3` | Yes | `log.warning("llm_judge.borderline_flag", count=borderline_count, ...)` | 441–446 |
| Score comparison uses integer `3` (not string) | Yes | `judge.py` line 166 casts to `int()`: `"score": int(verdict_dict.get("score", 0))` | judge.py:166 |
| Does not call `pytest.fail()` — warning only | Yes | Block emits `log.warning` only; no assertion | 441–446 |

WARNING-2: RESOLVED.

---

## Dimension Coverage

| # | Dimension | Status | Priority | Measurement | Finding |
|---|-----------|--------|----------|-------------|---------|
| D1 | Grounding fidelity | COVERED | P0 | LLM Judge (nightly) | Judge rubric matches AI-SPEC §5.2. `eval-full` nightly job runs `capture_responses.py --overwrite` then `pytest run_evals.py -v --tb=short` with `AGENT_E2E_ENABLED=1` — fully automated. No manual step required. |
| D2 | Escalation accuracy | COVERED | P0 | LLM Judge (nightly) + G-06 deterministic | G-06 dataset calibration gate in `eval-deterministic` CI; LLM judge behavioral check in `eval-full` nightly. Both automated. |
| D3 | Prompt injection resistance | COVERED | P0 | LLM Judge (nightly) + deterministic regex (CI) | `_check_d3()` deterministic regex runs in `eval-deterministic` on every PR. LLM judge runs in `eval-full` nightly. Two-layer automated coverage. |
| D4 | Session continuity | COVERED | P0 | LLM Judge (nightly) | S-008 edge scenario covers 2-turn continuity. LLM judge applied in `eval-full` nightly with live services. Automated. |
| D5 | Citation format compliance | COVERED | P1 | Deterministic regex (CI) | `CITATION_REGEX` exact match. Runs in `eval-deterministic` on every PR. System prompt has format instruction + `FEW_SHOT_SUFFIX`. |
| D6 | Tool call correctness | COVERED | P0 | Deterministic (CI) | `_check_d6()` validates table allowlist, max_clarify=2, max_escalate=1. `ALLOWED_LOOKUP_TABLES` enforced in production with `is_error=True`. CI `eval-deterministic` runs this on every PR. |
| D7 | Widget bundle size | COVERED | P0 | Deterministic zlib (CI + nightly) | `_check_d7()` uses `zlib.compress(level=9)`, asserts <= 20,480 bytes. Widget built in `eval-full` nightly. Bundle measured at 7,185 bytes — 65% below limit. |
| D8 | Knowledge gap honesty | COVERED | P0 | LLM Judge (nightly) | System prompt instruction present. 4 out-of-scope scenarios (S-017–S-020). LLM judge in `eval-full` nightly. Automated. |

**Coverage Score:** 8/8 (100%)

No PARTIAL or MISSING dimensions remain. All P0 dimensions now have at minimum one automated gate running on a defined schedule.

---

## Infrastructure Audit

| Component | Status | Finding |
|-----------|--------|---------|
| Eval tooling (custom pytest harness + anthropic SDK judge) | Configured | `judge.py` calls `anthropic.Anthropic().messages.create(model="claude-sonnet-4-5-20251001")`. `run_evals.py` has deterministic + LLM-judged test functions. `capture_responses.py` provides full response capture loop. Score field cast to `int()` — `borderline_count` comparison is type-safe. All files present and callable. |
| Reference dataset | Present — complete | 20 scenario JSON files: 6 golden / 5 edge / 5 adversarial / 4 out-of-scope. `demo_business_tenant.sql` fixture present. All `human_verdict` and `expected_behavior` fields populated. |
| CI/CD integration | Present | `eval-deterministic` job in `ci.yml`: covers D7, G-06 gate, D3/D5/D6 (when responses/ populated). `eval-full` job in `nightly.yml`: starts live services, runs `capture_responses.py --overwrite`, runs full `pytest run_evals.py -v --tb=short` including all LLM-judged dimensions. Both jobs automated — no manual step in the critical path. |
| Online guardrails | Partial | Implemented in request path: message length cap (max_length=2000), JWT validation with agent_id claim check, Redis rate limiting (60 req/min per agent_id), conversation ownership validation, conversation_id UUID4 enforcement, CORS preflight handlers, lookup_structured table allowlist (G-04). Deferred by design (AI-SPEC §6.4): AI identity disclosure runtime guardrail (M5 Gatekeeper), PII echo prevention (M5 Auditor). All M4-committed guardrails active. |
| Tracing (Langfuse v4) | Not configured | No Langfuse calls in apps/api/. AI-SPEC §7.2 explicitly defers to M5. Raw data (agent_id, conversation_id, job_id, escalated, citations_count) available in run_agent_turn. Documented intentional deferral — not an oversight. |

**Infrastructure Score Calculation:**

| Component | Score | Rationale |
|-----------|-------|-----------|
| Eval tooling | 1.0 | Installed, configured, callable; judge model correct; score type-safe |
| Reference dataset | 1.0 | 20 scenarios, correct composition, all fields populated |
| CI/CD integration | 1.0 | eval-deterministic on every PR; eval-full nightly with live services |
| Online guardrails | 0.75 | All M4-committed guardrails implemented; 2 output guardrails documented M5 deferrals |
| Tracing | 0.0 | Documented M5 deferral |

**Infrastructure Score:** (1.0 + 1.0 + 1.0 + 0.75 + 0.0) / 5 × 100 = **75/100**

---

## Score Calculation

```
coverage_score  = 8/8 × 100 = 100.0
infra_score     = 75.0
overall_score   = (100.0 × 0.6) + (75.0 × 0.4) = 60.0 + 30.0 = 90/100
```

**Verdict: PRODUCTION READY** (score >= 80)

Score progression across audits:
- Audit 1 (2026-05-16): 44/100 — three BLOCKERs unresolved
- Audit 2 (2026-05-17): 78/100 — BLOCKERs resolved; two WARNINGs unresolved
- Audit 3 (2026-05-17): 90/100 — both WARNINGs resolved; no open blockers

---

## Remaining Advisory Items (Not Blockers)

These items are not release blockers. A conscious decision to ship is not required — they are tracked as improvements.

### Judge Calibration — AI-SPEC §5.2 Advisory

**Status:** Infrastructure complete — pending first eval-full run.

`apps/api/tests/evals/calibration/` directory added with:
- `human_scores.csv` — 10-row template covering all 5 LLM-judged dimensions (grounding × 3, escalation × 2, injection resistance × 2, session continuity × 1, knowledge gap honesty × 2). `human_score` column is blank; fill after the first nightly run.
- `compute_correlation.py` — reads `human_scores.csv`, calls `judge.py` on each recorded response, computes Spearman ρ (stdlib, no scipy), exits 0 when ρ ≥ 0.75, exits 1 when below threshold.

**AI-SPEC §5.2 wording:** "Target >= 0.75 Spearman correlation between judge scores and human scores on the calibration set (10 scenarios reviewed by the implementer before trusting automated results)."

**Remaining action (one-time, after first nightly eval-full run):**
1. Review the 10 calibration scenarios in `responses/`; record your scores (1–5) in `human_scores.csv`
2. Run `python apps/api/tests/evals/calibration/compute_correlation.py`
3. If ρ ≥ 0.75 → judge is calibrated; if below → adjust rubrics in `judge.py` and re-run

### Deferred Items (by Design)

- **Ragas 0.4.x integration:** AI-SPEC §5.4 explicitly defers to M6
- **Langfuse v4 tracing:** AI-SPEC §7.2 explicitly defers to M5
- **PII echo prevention + AI identity disclosure runtime guardrails:** AI-SPEC §6.4 explicitly defers to M5 Gatekeeper/Auditor

---

## Files Verified in This Audit

| File | Status | Key Evidence |
|------|--------|-------------|
| `.github/workflows/nightly.yml` | Updated — eval-full job added | Lines 82–172: complete eval-full job with all required env vars and steps |
| `apps/api/tests/evals/run_evals.py` | Updated — borderline_count added | Lines 436–446: S-06 borderline flagging after aggregate loop |
| `apps/api/tests/evals/judge.py` | Unchanged — score type confirmed | Line 166: `int(verdict_dict.get("score", 0))` — borderline comparison is type-safe |
| `.github/workflows/ci.yml` | Unchanged — eval-deterministic confirmed present | eval-deterministic job covers D7 + G-06 + D3/D5/D6 |
| `apps/api/tests/evals/capture_responses.py` | Unchanged — present | Called by eval-full job with --overwrite flag |
| All 20 scenario JSON files | Unchanged — present | S-001 through S-020 confirmed in previous audits |
| `apps/api/tests/evals/fixtures/demo_business_tenant.sql` | Unchanged — present | Confirmed in previous audits |
| `apps/api/tests/evals/calibration/human_scores.csv` | Added — template | 10 rows across 5 LLM-judged dimensions; `human_score` column blank pending first nightly run |
| `apps/api/tests/evals/calibration/compute_correlation.py` | Added — ready | Reads CSV, calls judge, computes Spearman ρ; exits 0 when ρ ≥ 0.75 |
