---
phase: 19-documentation-v1-1-verification
verified: 2026-07-28T14:00:00Z
status: gaps_found
score: 3/8 must-haves verified
behavior_unverified: 0
overrides_applied: 0
gaps:
  - truth: "docs/guides/owner-capability-guide.md contains no confirmed factual defect about a shipped security-control's behavior (DOC-03 prohibition: MUST NOT describe behaviour the shipped code does not exhibit)"
    status: failed
    reason: >
      Independently re-verified against the current file (not merely trusted from
      19-REVIEW.md): lines 40-43 tell the reader "Nothing acts on a customer's behalf
      until you deliberately turn a skill on" with no disclosure that, per
      validate_tighten_only (apps/api/app/services/capability_service.py:307-313),
      every enabled:False->True transition is rejected for every shipped skill (all
      seven PLATFORM_CAPABILITY_DEFAULTS entries ship enabled=False, and no other code
      path in apps/api/app/ ever sets enabled=True). The admin UI itself already knows
      this and permanently greys out the checkbox
      (apps/admin/app/agents/[id]/deploy/page.tsx:1137,1147-1151) — the guide does not.
      Separately, lines 51-58 state "you see the exact sentence the server returns: 'A
      rate limit has to allow at least one call.'" This sentence is client-side-only
      copy (page.tsx:1044) and the server has no such floor at all — _parse_rate_limit
      accepts "0/hour" and validate_tighten_only's tighten-only rule then makes a
      written 0/hour permanent. Both defects (CR-01, CR-02 in 19-REVIEW.md) remain
      present in the shipped file; neither has been corrected since the review ran.
    artifacts:
      - path: "docs/guides/owner-capability-guide.md"
        issue: "Lines 35-43 and 45-61 make two confident, source-contradicted claims about a money-moving safety control to a non-technical audience"
    missing:
      - "State plainly under 'Enabled' that every platform default ships enabled=False and validate_tighten_only makes 'off' the tightest legal value, so no shipped skill can currently be turned on through the deploy screen or any API call — and name how a skill actually gets enabled today (direct DB seeding, outside owner control)."
      - "Correct the rate-limit-floor paragraph to state the 'at least one call' sentence is UI-only copy, not a server-enforced floor, and that a raw API call can currently write (and permanently lock in, via tighten-only) a 0/hour rate limit."
  - truth: "VER-01 SC2 — a non-technical tester deploys a refund + Shopify-order agent end-to-end without code"
    status: failed
    reason: >
      Recorded `[failed — blocked]` by direct operator disposition in 19-UAT.md item 1,
      dated 2026-07-28, after a real, non-worked-around attempt. Two independently
      confirmed structural causes, neither environmental: (1) capability enabled=True
      is unreachable through any shipped API — validate_tighten_only rejects every
      enabled:False->True transition, so enabling issue_refund/place_order for a real
      tenant requires direct database seeding, which a non-technical tester cannot
      perform unaided, directly contradicting SC2's own "without code" wording; (2)
      threat T-19-04 — the Actor's require_human branch writes a PendingConfirmation
      row that no route, task, or script in the codebase resolves, so the place_order
      leg can dead-end with no way to complete the transaction. Both are capabilities
      the product does not currently have, not infrastructure that closes with
      provisioning.
    artifacts:
      - path: "apps/api/app/services/capability_service.py"
        issue: "validate_tighten_only:307-313 rejects every enabled:False->True transition; every PLATFORM_CAPABILITY_DEFAULTS entry ships enabled=False"
      - path: "apps/api/app/services/transactional/tools.py"
        issue: "require_human branch writes a pending_confirmations row with no resolving route/task/script anywhere in apps/api/app/"
    missing:
      - "A code path (outside a direct DB write) for enabling a capability envelope, or an explicit product decision that SC2 as worded is not currently satisfiable and must be re-scoped."
      - "A resolution mechanism for pending_confirmations (a human-approved bypass seam inside _execute_transactional_tool per OD-2's own analysis), deferred to v1.2 per 19-UAT.md item 4."
  - truth: "VER-01 SC3's adversarial corpus genuinely exercises the Actor-seam and injection-resistance layers its attack_class labels claim to test"
    status: failed
    reason: >
      Independently reconfirmed against apps/api/tests/integration/test_ver01_adversarial_harness.py:492-502
      — ADVERSARIAL_MESSAGE_CORPUS runs _value_bound_ceiling_entries() and four
      _rate_chain_entries(...) groups (5/hour rate limit on every affected skill, per
      red_team_probe.py:419-455's CLEAN_TENANT_ENVELOPES) BEFORE
      _confused_deputy_actor_entries() and _injection_entries(), all against the same
      agent_id inside one run. apply_rate_and_constraint_checks increments the same
      Redis rate counter on every call regardless of outcome and checks it before the
      Actor seam is ever reached. As a result ~43 of the corpus's ~119 entries — the
      confused-deputy and injection entries for place_order, update_subscription,
      book_slot, update_customer_record, and cancel_order — are denied by rate_denied
      before ever reaching the Actor gate or any injection-resistance mechanism, every
      time the corpus runs. The pass/fail assertion (unauthorized_mutations empty)
      remains sound regardless of which layer catches an attack, but the printed
      by_verdict table an operator is instructed to transcribe into 19-UAT.md
      materially overstates per-layer coverage for these five skills.
    artifacts:
      - path: "apps/api/tests/integration/test_ver01_adversarial_harness.py"
        issue: "Corpus ordering (rate-chain entries before confused-deputy/injection entries on the same skill) causes rate-window exhaustion to pre-empt the Actor-seam and injection-resistance coverage the entries claim to exercise (WR-03, 19-REVIEW.md)"
    missing:
      - "Raise the rate_limit for the five affected skills in this harness's clean-tenant override (mirroring AUD-03's own _aud03_envelope_rows precedent), or reorder the corpus so confused-deputy/injection entries run before their skill's rate-chain entries, or split into per-skill sub-runs with fresh rate windows."
      - "At minimum, name this collision explicitly in the module docstring so a reader of the by_verdict table is not misled."
human_verification:
  - test: "Run `cd apps/api && INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_ver01_adversarial_harness.py -m integration -q -s` against a live local PostgreSQL server, and transcribe the printed attempted count and by_verdict table."
    expected: "invalid: False, at least 100 attempted messages, zero provider_not_configured verdicts, empty unauthorized_mutations list"
    why_human: "No PostgreSQL server is installed on this machine (confirmed by the operator 2026-07-28 — stale service registration, orphaned data directory, nothing on PATH or listening on 5432-5435). The harness has never executed against a live database; this result is unobserved, not a pass. A `verification: backstop` truth per 19-04-PLAN.md must abstain until confirmed by explicit evidence."
  - test: "Run `cd apps/api && INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_aud03_audit_gap.py -m integration -q -s` against a live local PostgreSQL server, and transcribe the invocation count, audit-row count, and per-day delta."
    expected: "vacuous: False, 30 days with traffic, zero out-of-window rows, a per-day delta of 0 on every day"
    why_human: "Same missing-PostgreSQL cause as above, confirmed by the operator 2026-07-28. Never executed against a live database; unobserved, not a pass. `verification: backstop` truth per 19-03-PLAN.md."
---

# Phase 19: Documentation + v1.1 verification Verification Report

**Phase Goal:** Ship the author/provider/owner guides and prove the milestone's success
criteria end-to-end.
**Verified:** 2026-07-28T14:00:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

This phase's own artifacts (19-UAT.md, 19-REVIEW.md, REQUIREMENTS.md, STATE.md) already
carry an unusually honest self-report: the operator recorded VER-01 SC2 as
`[failed — blocked]` and VER-01 SC3 / AUD-03 as `[deferred]` rather than papering over
them, and REQUIREMENTS.md was corrected in the same plan to un-tick VER-01 and AUD-03.
This verification independently re-confirmed those claims against the codebase rather
than accepting them at face value, and additionally found one confirmed defect
(WR-03's rate-window coverage collision) reconfirmed by direct code reading, and
re-confirmed both Critical findings in 19-REVIEW.md remain present, unfixed, in the
shipped owner guide.

**Bottom line: two of three roadmap success criteria did not achieve their goal.** SC1
(guides published) is compromised by two confirmed factual defects in the owner guide,
the guide with the highest stakes (a non-technical owner making decisions about
money-moving controls). SC2 is recorded failed by the operator on structural product
grounds, not environment grounds. SC3's two proof harnesses are real, careful,
unit-proven engineering — but neither has ever run against a live database, and one of
them (the adversarial harness) has a confirmed coverage gap independent of whether it
runs. This phase produced real, valuable work (three developer/business guides, two
non-trivial test harnesses, an honest paper trail); it did not achieve "prove the
milestone's success criteria end-to-end."

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | DOC-01 tool-author guide published and accurate against source | ✓ VERIFIED | `docs/guides/tool-author-guide.md` (196 lines), all 20 anchor literals present, 8 enforcement steps in ascending source order (mechanically re-verified), `pyproject.toml` unchanged. 19-REVIEW.md: clean, no findings. |
| 2 | DOC-02 integration-provider guide published and accurate against source | ✓ VERIFIED | `docs/guides/integration-provider-guide.md` (206 lines), all 17 anchor literals present, no stdout/log-emitting example, no CLI-duplication, no container command. 19-REVIEW.md: clean, no findings. |
| 3 | DOC-03 owner guide published free of confirmed factual defects | ✗ FAILED | `docs/guides/owner-capability-guide.md` (256 lines) exists, all anchor literals present — but lines 35-43 and 45-61 independently re-confirmed to still carry CR-01 and CR-02 (owner-capability-guide.md tells a non-technical reader a control exists that `validate_tighten_only` makes permanently unreachable, and attributes client-only UI copy to a non-existent server-side floor). See Gaps. |
| 4 | VER-01 SC2 — non-technical tester deploys refund + Shopify-order agent end-to-end without code | ✗ FAILED | `19-UAT.md` item 1: `[failed — blocked]`, operator disposition 2026-07-28. Two structural causes independently re-confirmed against `apps/api/app/services/capability_service.py:307-313` and the unresolved `pending_confirmations` write path. See Gaps. |
| 5 | VER-01 SC3 — adversarial corpus genuinely exercises the Actor-seam / injection layers its labels claim | ✗ FAILED | Independently re-confirmed: `test_ver01_adversarial_harness.py:492-502` runs rate-exhausting chains before confused-deputy/injection entries on the same skill+agent_id; ~43/~119 entries never reach the layer their `attack_class` claims. See Gaps (WR-03). |
| 6 | VER-01 SC3 — a real ≥100-message run against a live migrated tenant reports zero unauthorized mutations and zero `provider_not_configured` | ? UNCERTAIN | `verification: backstop` truth (19-04-PLAN.md). Harness authored, collects cleanly, its 12-test unit companion passes. Never executed against a live database — no PostgreSQL installed on this machine (confirmed by operator 2026-07-28). Routed to human verification. |
| 7 | AUD-03 — a real 30-day synthetic run against a live migrated control DB reports zero gaps | ? UNCERTAIN | `verification: backstop` truth (19-03-PLAN.md). Harness authored, collects cleanly, its 11-test unit companion passes. Never executed against a live database — same missing-PostgreSQL cause. Routed to human verification. |
| 8 | Full unit suite green and strictly above the 1103-passed baseline, no production code touched | ✓ VERIFIED | 1134 passed, 8 skipped, 0 failed (docling modules ignored, as specified). Baseline 1103 + 31 new tests (8+11+12) = 1134, exact match. `apps/api/pyproject.toml` byte-unchanged; nothing under `apps/api/app/` touched. Four pinned named tests re-run directly by this verification and pass: `test_actor_skip_engages_for_demo_refund_envelope`, `test_demo_place_order_envelope_does_not_engage_skip`, `test_result_is_independent_of_input_row_order`, `test_all_probes_inside_red_team_mode`. |

**Score:** 3/8 truths verified (2 present-but-unconfirmed as backstop truths, routed to
human verification; 3 failed).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `docs/guides/tool-author-guide.md` | DOC-01, ≥120 lines, 8-step order | ✓ VERIFIED | 196 lines, all anchors present, order confirmed ascending |
| `docs/guides/integration-provider-guide.md` | DOC-02, ≥120 lines | ✓ VERIFIED | 206 lines, all anchors present |
| `docs/guides/owner-capability-guide.md` | DOC-03, ≥120 lines, no source-contradicted claims | ⚠️ STUB-EQUIVALENT (present, substantively wrong) | 256 lines, anchors present, but CR-01/CR-02 confirmed unfixed — the artifact exists and is well-formed but two of its central safety claims are false against source |
| `apps/api/tests/unit/test_ver01_demo_tenant.py` | VER01_DEMO_TENANT_ENVELOPES + skip-boundary proof | ✓ VERIFIED | Present, contains `VER01_DEMO_TENANT_ENVELOPES`, both pinned named tests pass |
| `apps/api/tests/integration/test_aud03_audit_gap.py` | `compute_audit_gap` + gated harness | ✓ VERIFIED (present + unit-proven; live run unconfirmed) | Present, `compute_audit_gap` present, collects cleanly, never run live |
| `apps/api/tests/unit/test_audit_gap_arithmetic.py` | DB-free proof of parity arithmetic | ✓ VERIFIED | Present, imports `compute_audit_gap`, named test passes |
| `apps/api/tests/integration/test_ver01_adversarial_harness.py` | `ADVERSARIAL_MESSAGE_CORPUS`, driver, summariser | ⚠️ PRESENT + WIRED, coverage claim overstated | Present, ≥100 entries, collects cleanly, but WR-03 coverage collision confirmed |
| `apps/api/tests/unit/test_ver01_harness_probes.py` | mocked-boundary proof | ✓ VERIFIED | Present, named test passes |
| `.planning/phases/19-documentation-v1-1-verification/19-UAT.md` | live-gate dispositions, no silent pass | ✓ VERIFIED | Exists, every item carries an explicit, dated disposition (1 failed, 2 deferred, 1 recorded); no `[pending]` remains |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `docs/guides/tool-author-guide.md` | `apps/api/app/services/transactional/tools.py` | 8-step enforcement order in source order | ✓ WIRED | Ascending line-number check re-run, passes |
| `docs/guides/integration-provider-guide.md` | `docs/runbooks/integration-credentials.md` | relative cross-link | ✓ WIRED | Literal present, no duplication of provisioning CLI |
| `docs/guides/owner-capability-guide.md` | `apps/admin/app/agents/[id]/deploy/page.tsx` | verbatim copy quotation | ⚠️ WIRED BUT MISLEADING | Quotes are verbatim, but two of the surrounding factual claims about what those quotes mean contradict the same source file (CR-01/CR-02) |
| `apps/api/tests/unit/test_audit_gap_arithmetic.py` | `apps/api/tests/integration/test_aud03_audit_gap.py` | imports `compute_audit_gap` | ✓ WIRED | Confirmed by passing named test |
| `apps/api/tests/unit/test_ver01_harness_probes.py` | `apps/api/tests/integration/test_ver01_adversarial_harness.py` | imports corpus/driver/summariser | ✓ WIRED | Confirmed by passing named test |
| `.planning/phases/19-documentation-v1-1-verification/19-UAT.md` | `docs/guides/owner-capability-guide.md` | only material handed to SC2 tester | ✓ WIRED | Referenced by path in the UAT runbook |
| `.planning/STATE.md` | `18-10-SUMMARY.md` | stale-record correction | ✓ WIRED | STATE.md corrected: "18-10 is executed and committed" |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Actor skip engages for demo refund envelope | `pytest tests/unit/test_ver01_demo_tenant.py::test_actor_skip_engages_for_demo_refund_envelope -q` | 1 passed | ✓ PASS |
| Actor skip does NOT engage for demo place_order envelope (T-19-04 made a tested fact) | `pytest tests/unit/test_ver01_demo_tenant.py::test_demo_place_order_envelope_does_not_engage_skip -q` | 1 passed | ✓ PASS |
| Audit-gap arithmetic is order-independent | `pytest tests/unit/test_audit_gap_arithmetic.py::test_result_is_independent_of_input_row_order -q` | 1 passed | ✓ PASS |
| Every adversarial probe runs inside `red_team_mode()` | `pytest tests/unit/test_ver01_harness_probes.py::test_all_probes_inside_red_team_mode -q` | 1 passed | ✓ PASS |
| Both live-gated integration modules import/collect cleanly | `pytest tests/integration/test_aud03_audit_gap.py tests/integration/test_ver01_adversarial_harness.py --collect-only -q` | 2 tests collected, 0 errors | ✓ PASS |
| Full unit suite at claimed count | `pytest tests/unit -q --ignore=...chunking... --ignore=...docling...` | 1134 passed, 8 skipped, 0 failed (orchestrator-run baseline, cross-checked against 1103+31 arithmetic) | ✓ PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` convention exists in this repository; this phase's
"probes" are the pytest-based gated harnesses covered under Behavioral Spot-Checks and
Human Verification above. Step 7c: SKIPPED (no shell-script probe convention in this
project).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| DOC-01 | 19-01 | Tool-author guide | ✓ SATISFIED | Guide published, accurate, clean review |
| DOC-02 | 19-01 | Integration-provider guide | ✓ SATISFIED | Guide published, accurate, clean review |
| DOC-03 | 19-02 | Owner-facing capability-configuration guide | ✗ BLOCKED | Guide published but contains two confirmed factual defects about a shipped safety control (CR-01, CR-02) |
| VER-01 | 19-02, 19-04, 19-05 | v1.1 success-criteria gate (SC2 + SC3) | ✗ BLOCKED | SC2 recorded failed-blocked by operator (structural, not environmental); SC3 harness has a confirmed coverage defect and its live run is unconfirmed |
| AUD-03 | 19-03 | Zero audit gaps across 30 synthetic days | ✗ BLOCKED | Harness authored and unit-proven; live run never executed (no PostgreSQL on this machine) |

No orphaned requirements — REQUIREMENTS.md maps exactly DOC-01, DOC-02, DOC-03, VER-01,
AUD-03 to Phase 19, matching the five requirement IDs claimed across the five plans.
REQUIREMENTS.md's own state (`[x]` for DOC-01/02/03, `[ ]` for VER-01/AUD-03) matches
this verification's independent findings, including the DOC-03 nuance: REQUIREMENTS.md
ticks DOC-03 as delivered while this verification finds its content factually
compromised — REQUIREMENTS.md tracks "guide published," not "guide correct," and does
not currently distinguish the two. See Gaps.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `docs/guides/owner-capability-guide.md` | 35-43 | Confident claim contradicted by source (`validate_tighten_only`) | 🛑 Blocker | A non-technical owner is told a safety control is a normal switch they operate; it cannot currently be switched on for any shipped skill |
| `docs/guides/owner-capability-guide.md` | 50-61 | Attributes client-side-only copy to "the server," while the actual server has no equivalent floor | 🛑 Blocker | Reader believes a rate-limit-zero safeguard is server-enforced; a raw API call can currently write a permanent 0/hour lock |
| `docs/guides/owner-capability-guide.md` | 68-72, 133-138 | Quoted refusal sentence presented as what a raw API call also returns (WR-01, `19-REVIEW.md`) | ⚠️ Warning | Outcome (422, nothing written) is correct; exact wording is not — minor relative to CR-01/CR-02 |
| `docs/guides/tool-author-guide.md` | 76-82 | Undercounts IDV gate audit-writing branches (two stated, three actual) (WR-02) | ⚠️ Warning | New skill author could miss the fail-closed `check_failed` branch |
| `apps/api/tests/integration/test_ver01_adversarial_harness.py` | 173-502 | Rate-window exhaustion pre-empts confused-deputy/injection coverage on 5 skills (WR-03, independently reconfirmed) | ⚠️ Warning | `by_verdict` table overstates per-layer coverage for an operator transcribing results |
| `apps/api/tests/integration/test_aud03_audit_gap.py` | 653-694 | Undocumented same-clock assumption between host and Postgres server (WR-04) | ⚠️ Warning | Only correct because local Postgres is required by CLAUDE.md rule 9; not guarded or commented |
| `docs/guides/tool-author-guide.md` | 76-82 | Omits Step 6 adapter-failure audit branches from the enumerated list (IN-01) | ℹ️ Info | Incomplete worked example, general symmetry statement still correct |

No `TBD`/`FIXME`/`XXX` debt markers found in any file this phase modified.

### Human Verification Required

1. **VER-01 SC3 live adversarial run**
   **Test:** `cd apps/api && INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_ver01_adversarial_harness.py -m integration -q -s`, against a real local PostgreSQL server.
   **Expected:** `invalid: False`, ≥100 attempted, zero `provider_not_configured`, empty `unauthorized_mutations`.
   **Why human:** No PostgreSQL server is installed on this machine; this harness has never been executed against a live database, and its result cannot be inferred from the passing unit companion alone. Note independently of the run's outcome: even a clean pass here should be read alongside the confirmed WR-03 coverage collision — a clean `by_verdict` table does not mean the Actor-seam/injection layers were exercised for the five affected skills.

2. **AUD-03 live 30-day audit-gap run**
   **Test:** `cd apps/api && INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_aud03_audit_gap.py -m integration -q -s`, against a real local PostgreSQL server.
   **Expected:** `vacuous: False`, 30 days with traffic, zero out-of-window rows, per-day delta 0.
   **Why human:** Same missing-PostgreSQL cause. Never executed against a live database.

### Gaps Summary

Three confirmed gaps prevent this phase's goal — "prove the milestone's success
criteria end-to-end" — from being achieved:

1. **DOC-03's owner guide is not merely unproven, it is factually wrong** about a
   money-moving safety control (CR-01) and misattributes a client-only safeguard to
   the server (CR-02). This was flagged by `19-REVIEW.md` before this verification ran,
   and remains unfixed in the shipped file as of this check. "Published" was achieved;
   "accurate," which the phase's own DOC-03 prohibition requires, was not.

2. **VER-01 SC2 is recorded failed by the operator**, for structural product reasons
   (capability `enabled=True` unreachable through any shipped API; the `require_human`
   dead end) that provisioning more infrastructure cannot close. This is the correct,
   honest disposition — not a verification failure of this phase's own process — but it
   means the roadmap's second success criterion did not happen.

3. **VER-01 SC3's adversarial harness has a confirmed, code-level coverage defect**
   (WR-03) independent of whether it is ever run: rate-window exhaustion from earlier
   corpus entries means ~43 of ~119 confused-deputy/injection-labeled entries never
   reach the layers their `attack_class` claims to test. Additionally, the harness's
   central pass/fail claim has never been executed against a live database — both are
   recorded honestly in `19-UAT.md` as `[deferred]`, and this verification confirms
   that disposition is accurate (not softened, not silently passed).

This phase's own paper trail (`19-UAT.md`, `19-REVIEW.md`, the corrected
`REQUIREMENTS.md`/`STATE.md`) is unusually candid and made this verification easier,
not harder — every finding above was either already surfaced by the phase's own review
step or directly confirmed by re-reading the named source files. The work that did ship
cleanly (DOC-01, DOC-02, the AUD-03/VER-01 unit-proven arithmetic and probe-window
discipline, the honest UAT record itself) is real and should not be discounted. But
three of five requirements shipping cleanly while the phase's two headline proofs went
unachieved is the accurate characterization, and the roadmap's second and third success
criteria are not met by this build.

---

_Verified: 2026-07-28T14:00:00Z_
_Verifier: Claude (gsd-verifier)_
