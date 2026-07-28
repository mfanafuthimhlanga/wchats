---
phase: 19-documentation-v1-1-verification
verified: 2026-07-28T16:30:00Z
status: gaps_found
score: 5/8 must-haves verified
behavior_unverified: 0
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 3/8
  gaps_closed:
    - "docs/guides/owner-capability-guide.md contains no confirmed factual defect about a shipped security-control's behavior (DOC-03) — CR-01, CR-02, WR-01 independently re-verified fixed against source"
    - "VER-01 SC3's adversarial corpus genuinely exercises the Actor-seam and injection-resistance layers its attack_class labels claim to test — WR-03 coverage collision independently re-verified fixed via per-attack-class agent_id isolation"
  gaps_remaining:
    - "VER-01 SC2 — a non-technical tester deploys a refund + Shopify-order agent end-to-end without code"
  regressions: []
gaps:
  - truth: "VER-01 SC2 — a non-technical tester deploys a refund + Shopify-order agent end-to-end without code"
    status: failed
    reason: >
      Independently re-confirmed unchanged since the prior verification. The operator's
      `[failed — blocked]` disposition in 19-UAT.md item 1 (dated 2026-07-28) stands. Two
      structural causes, both re-read directly from current source rather than trusted:
      (1) validate_tighten_only (apps/api/app/services/capability_service.py:298-313)
      still rejects every enabled:False->True transition for every shipped skill — all
      seven PLATFORM_CAPABILITY_DEFAULTS entries still ship enabled=False
      (capability_service.py:107-141), and no code path outside that gated PATCH route
      writes enabled=True anywhere in apps/api/app/; (2) the require_human branch in
      apps/api/app/services/transactional/tools.py still writes a pending_confirmations
      row with no resolving route, task, or script anywhere in the codebase (T-19-04,
      unchanged). Neither is a Phase 19 regression — both are the same pre-existing
      product-capability gaps identified before this re-verification ran. What changed is
      that both now have a named owner: Phase 22 (CAP-05, ACT-07) in ROADMAP.md, added
      2026-07-28, with its own success criterion 4 ("VER-01 SC2 is re-run and its
      [failed — blocked] disposition ... is replaced by an observed result"). Phase 22
      has zero plans executed ("Plans: 0 — run /gsd-plan-phase 22"). Assignment of an
      owner is not closure: the missing capabilities still do not exist in the shipped
      codebase today, so SC2 as worded is still not satisfiable, and this verification
      does not treat the roadmap entry as evidence that the truth now holds. This gap is
      NOT deferred to Phase 22 under Step 9b, because SC2 is Phase 19's own numbered
      success criterion (ROADMAP.md, Phase 19 section) — the phase whose goal explicitly
      includes "prove the milestone's success criteria end-to-end" — not a criterion
      belonging to some separate, already-in-scope-elsewhere concern picked up
      incidentally by a later phase. Deferring it would launder a core, in-scope failure
      of this phase's own goal into an informational footnote.
    artifacts:
      - path: "apps/api/app/services/capability_service.py"
        issue: "validate_tighten_only:298-313 rejects every enabled:False->True transition; every PLATFORM_CAPABILITY_DEFAULTS entry ships enabled=False (unchanged since prior verification)"
      - path: "apps/api/app/services/transactional/tools.py"
        issue: "require_human branch writes a pending_confirmations row with no resolving route/task/script anywhere in apps/api/app/ (unchanged since prior verification)"
    missing:
      - "A code path (outside a direct DB write) for enabling a capability envelope, or an explicit product decision that SC2 as worded is not currently satisfiable and must be re-scoped. (Owned by Phase 22 / CAP-05 — not yet built.)"
      - "A resolution mechanism for pending_confirmations. (Owned by Phase 22 / ACT-07 — not yet built.)"
human_verification:
  - test: "Run `cd apps/api && INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_ver01_adversarial_harness.py -m integration -q -s` against a live local PostgreSQL server, and transcribe the printed attempted count and by_verdict table."
    expected: "invalid: False, at least 100 attempted messages, zero provider_not_configured verdicts, empty unauthorized_mutations list"
    why_human: "Independently reconfirmed: no PostgreSQL binary (psql, pg_ctl) is anywhere on PATH on this machine, and apps/api/.env's CONTROL_DB_URL points at a live Neon production endpoint (ep-falling-glade-...neon.tech), which is not a substitute — this harness targets TEST_ADMIN_DB_URL / CREATE DATABASE against localhost:5432 and AUD-03 seeds backdated rows, neither of which belongs on production. The harness has never executed against a live database; this result remains unobserved, not a pass. A `verification: backstop` truth per 19-04-PLAN.md must abstain until confirmed by explicit evidence. Unchanged since the prior verification."
  - test: "Run `cd apps/api && INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_aud03_audit_gap.py -m integration -q -s` against a live local PostgreSQL server, and transcribe the invocation count, audit-row count, and per-day delta."
    expected: "vacuous: False, 30 days with traffic, zero out-of-window rows, a per-day delta of 0 on every day"
    why_human: "Same missing-PostgreSQL cause as above, independently reconfirmed. Never executed against a live database; unobserved, not a pass. `verification: backstop` truth per 19-03-PLAN.md. Unchanged since the prior verification."
---

# Phase 19: Documentation + v1.1 verification Verification Report

**Phase Goal:** Ship the author/provider/owner guides and prove the milestone's success
criteria end-to-end.
**Verified:** 2026-07-28T16:30:00Z
**Status:** gaps_found
**Re-verification:** Yes — after gap closure

## Goal Achievement

This re-verification independently re-checked all three of the prior verification's gaps
against the current codebase rather than accepting 19-REVIEW-FIX.md's claims at face
value. Two of the three are now genuinely closed, confirmed by direct source reading and
by running the relevant tests myself:

1. **DOC-03 factual defects (CR-01, CR-02, WR-01) — CLOSED.** Re-read
   `docs/guides/owner-capability-guide.md` against `capability_service.py`,
   `enforcement.py`, and `page.tsx` line-by-line. The guide now states plainly that every
   platform default ships `enabled=False`, that `validate_tighten_only` makes "off" the
   tightest legal value so no shipped skill can currently be turned on except by direct
   database action, that the "at least one call" sentence is UI-only copy with no
   server-side floor, and that a raw API call can write and permanently lock a `0/hour`
   rate limit. Every specific claim I independently checked against source now matches.
   No new inaccuracy was introduced by the correction.

2. **WR-03 coverage collision (VER-01 SC3 corpus) — CLOSED.** Re-read the fix
   (`a049da6`) directly in `test_ver01_adversarial_harness.py`: confused-deputy and
   injection entries now run under dedicated `confused_deputy`/`injection` agent_ids,
   isolated from the `primary` track's rate-exhausting chains by Redis key-space
   separation (the rate key already includes `agent_id`,
   `enforcement.py:312`). Confirmed **no rate limit was raised or disabled** — every
   track inserts an unmodified copy of `CLEAN_TENANT_ENVELOPES`
   (`red_team_probe.py:404-461`, untouched by this phase — confirmed via `git log`), and
   the `primary` track's rate-chain groups (`_rate_chain_entries`, unchanged) still
   exhaust exactly as before. Ran the new guard test myself —
   `test_confused_deputy_and_injection_entries_never_hit_an_exhausted_rate_window` —
   and confirmed it derives every limit from `CLEAN_TENANT_ENVELOPES` by import, not a
   hardcoded copy, and passes.

3. **VER-01 SC2 (non-technical deploy end-to-end) — REMAINS FAILED.** Both structural
   blockers (`validate_tighten_only`'s permanent `enabled` lock; the unresolved
   `pending_confirmations` write) are re-confirmed present, unchanged, in current source.
   Ownership was assigned to a new Phase 22 (`CAP-05`, `ACT-07`) with zero plans
   executed. An assigned-but-unbuilt owner is not closure — the capability still does not
   exist in the shipped codebase — so this truth remains FAILED, not deferred.

**Bottom line:** the phase's own most consequential defect — telling a non-technical
owner false things about a money-moving safety control — is genuinely fixed, and the
adversarial harness's coverage claim is now genuinely true rather than merely documented
as false. But the phase's second roadmap success criterion (SC2, a real end-to-end
non-technical deploy) still does not hold against the shipped build, for reasons a
documentation phase cannot fix by itself. This phase's goal — "prove the milestone's
success criteria end-to-end" — remains not fully achieved.

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | DOC-01 tool-author guide published and accurate against source | ✓ VERIFIED | Unchanged since prior verification (only WR-02/IN-01 edit, confirmed accurate below). No regression. |
| 2 | DOC-02 integration-provider guide published and accurate against source | ✓ VERIFIED | File untouched since prior verification (`git log` confirms zero commits since `fd9bee1`). No regression. |
| 3 | DOC-03 owner guide published free of confirmed factual defects | ✓ VERIFIED | Independently re-read `docs/guides/owner-capability-guide.md` against `capability_service.py:298-313,107-141`, `enforcement.py:160-180`, and `apps/admin/app/agents/[id]/deploy/page.tsx:898,1044,1085,1137,1147-1151` line-by-line. CR-01, CR-02, WR-01 all confirmed genuinely fixed — no source-contradicted claim remains. |
| 4 | VER-01 SC2 — non-technical tester deploys refund + Shopify-order agent end-to-end without code | ✗ FAILED | `19-UAT.md` item 1 disposition unchanged: `[failed — blocked]`. Both structural blockers independently re-confirmed present in current source. Phase 22 (CAP-05, ACT-07) assigned as owner, 0 plans executed — assignment is not closure. See Gaps. |
| 5 | VER-01 SC3 — adversarial corpus genuinely exercises the Actor-seam / injection layers its labels claim | ✓ VERIFIED | Independently re-read the `a049da6` fix in `test_ver01_adversarial_harness.py`: per-track agent_id isolation confirmed, `CLEAN_TENANT_ENVELOPES` confirmed unmodified (`git log` shows zero commits to `red_team_probe.py`), guard test run directly and passes. |
| 6 | VER-01 SC3 — a real ≥100-message run against a live migrated tenant reports zero unauthorized mutations and zero `provider_not_configured` | ? UNCERTAIN | `verification: backstop` truth (19-04-PLAN.md). Independently reconfirmed no PostgreSQL binary exists on PATH and `CONTROL_DB_URL` in `apps/api/.env` points at live Neon production, not a local substitute. Never executed against a live database. Routed to human verification. |
| 7 | AUD-03 — a real 30-day synthetic run against a live migrated control DB reports zero gaps | ? UNCERTAIN | `verification: backstop` truth (19-03-PLAN.md). Same independently-reconfirmed missing-PostgreSQL cause. Never executed against a live database. Routed to human verification. |
| 8 | Full unit suite green and strictly above the 1103-passed baseline, no production code touched | ✓ VERIFIED | Ran directly: `1136 passed, 8 skipped, 0 failed` (baseline 1134 + 2 new guard tests = 1136, exact match). `git diff fd9bee1..HEAD --stat -- apps/api/app/ apps/api/pyproject.toml` returns empty — confirmed byte-unchanged / untouched. Four pinned named tests re-run directly and pass: `test_actor_skip_engages_for_demo_refund_envelope`, `test_demo_place_order_envelope_does_not_engage_skip`, `test_result_is_independent_of_input_row_order`, `test_all_probes_inside_red_team_mode`. |

**Score:** 5/8 truths verified (2 present-but-unconfirmed as backstop truths, routed to
human verification; 1 failed).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `docs/guides/tool-author-guide.md` | DOC-01, ≥120 lines, 8-step order | ✓ VERIFIED | Unchanged except WR-02/IN-01 edit (line 77-83, independently re-read: "the three IDV block branches" now correctly names all three, plus "every other early return" closes IN-01) |
| `docs/guides/integration-provider-guide.md` | DOC-02, ≥120 lines | ✓ VERIFIED | Untouched since prior verification |
| `docs/guides/owner-capability-guide.md` | DOC-03, ≥120 lines, no source-contradicted claims | ✓ VERIFIED | 256+ lines, every re-checked claim now matches source — CR-01/CR-02/WR-01 confirmed closed |
| `apps/api/tests/unit/test_ver01_demo_tenant.py` | VER01_DEMO_TENANT_ENVELOPES + skip-boundary proof | ✓ VERIFIED | Unchanged; both pinned named tests re-run and pass |
| `apps/api/tests/integration/test_aud03_audit_gap.py` | `compute_audit_gap` + gated harness | ✓ VERIFIED (present + unit-proven; live run unconfirmed) | Unchanged; collects cleanly; never run live |
| `apps/api/tests/unit/test_audit_gap_arithmetic.py` | DB-free proof of parity arithmetic | ✓ VERIFIED | Unchanged; named test re-run and passes |
| `apps/api/tests/integration/test_ver01_adversarial_harness.py` | `ADVERSARIAL_MESSAGE_CORPUS`, driver, summariser | ✓ VERIFIED | Restructured by `a049da6` (per-track agent_id isolation); collects cleanly; coverage claim now genuinely true, confirmed by direct code reading and by running the new guard test |
| `apps/api/tests/unit/test_ver01_harness_probes.py` | mocked-boundary proof | ✓ VERIFIED | Extended with the new guard test (`test_confused_deputy_and_injection_entries_never_hit_an_exhausted_rate_window`), run directly and passes |
| `.planning/phases/19-documentation-v1-1-verification/19-UAT.md` | live-gate dispositions, no silent pass | ✓ VERIFIED | Item 4 now carries the Phase 22 ownership assignment; item 1's `[failed — blocked]` disposition unchanged; no `[pending]` remains |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `docs/guides/owner-capability-guide.md` | `apps/admin/app/agents/[id]/deploy/page.tsx` | verbatim copy quotation | ✓ WIRED | Every quoted sentence independently re-verified verbatim against `page.tsx:898,1044,1085,1137,1147-1151`, and the surrounding claims now correctly attribute UI-only copy to the UI, not the server |
| `apps/api/tests/integration/test_ver01_adversarial_harness.py` | `app/services/red_team_probe.py` (`CLEAN_TENANT_ENVELOPES`) | per-track agent envelope insertion, unmodified | ✓ WIRED | `_insert_clean_agent` confirmed to insert `CLEAN_TENANT_ENVELOPES` rows unmodified for all three tracks; source file confirmed untouched by `git log` |
| `apps/api/tests/unit/test_ver01_harness_probes.py` | `apps/api/tests/integration/test_ver01_adversarial_harness.py` | imports corpus + `rate_track` partitioning | ✓ WIRED | New guard test imports `ADVERSARIAL_MESSAGE_CORPUS` directly and derives limits from `CLEAN_TENANT_ENVELOPES`; confirmed by running it |
| `.planning/ROADMAP.md` (Phase 22) | `.planning/phases/19-documentation-v1-1-verification/19-UAT.md` (item 1) | success criterion 4 references the disposition to replace | ⚠️ WIRED, NOT YET ACTED ON | Link is real (Phase 22 SC4 names item 1's disposition explicitly) but Phase 22 has 0 plans executed — the link is a commitment, not evidence the truth now holds |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| New WR-03 guard test passes | `pytest tests/unit/test_ver01_harness_probes.py::test_confused_deputy_and_injection_entries_never_hit_an_exhausted_rate_window -q` | 1 passed | ✓ PASS |
| Actor skip engages for demo refund envelope (regression check) | `pytest tests/unit/test_ver01_demo_tenant.py::test_actor_skip_engages_for_demo_refund_envelope -q` | 1 passed | ✓ PASS |
| Actor skip does NOT engage for demo place_order envelope (regression check) | `pytest tests/unit/test_ver01_demo_tenant.py::test_demo_place_order_envelope_does_not_engage_skip -q` | 1 passed | ✓ PASS |
| Audit-gap arithmetic order-independent (regression check) | `pytest tests/unit/test_audit_gap_arithmetic.py::test_result_is_independent_of_input_row_order -q` | 1 passed | ✓ PASS |
| Every adversarial probe runs inside `red_team_mode()` (regression check) | `pytest tests/unit/test_ver01_harness_probes.py::test_all_probes_inside_red_team_mode -q` | 1 passed | ✓ PASS |
| Both live-gated integration modules import/collect cleanly | `pytest tests/integration/test_aud03_audit_gap.py tests/integration/test_ver01_adversarial_harness.py --collect-only -q` | 2 tests collected, 0 errors | ✓ PASS |
| Full unit suite at claimed count | `pytest tests/unit -q --ignore=...chunking... --ignore=...docling...` | 1136 passed, 8 skipped, 0 failed (verifier-run directly, not orchestrator-reported) | ✓ PASS |
| No PostgreSQL binary reachable (independent re-confirmation of the live-gate blocker) | `where psql`, `where pg_ctl` | both exit 1, not found | ✓ CONFIRMED (blocker still real) |
| `CONTROL_DB_URL` points at live Neon production, not local | inspected `apps/api/.env` | `postgresql+asyncpg://neondb_owner:***@ep-falling-glade-...neon.tech/neondb` | ✓ CONFIRMED (not a valid substitute for the live gates) |

### Probe Execution

No `scripts/*/tests/probe-*.sh` convention exists in this repository; this phase's
"probes" are the pytest-based gated harnesses covered under Behavioral Spot-Checks and
Human Verification above. Step 7c: SKIPPED (no shell-script probe convention in this
project).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| DOC-01 | 19-01 | Tool-author guide | ✓ SATISFIED | Unchanged, accurate |
| DOC-02 | 19-01 | Integration-provider guide | ✓ SATISFIED | Unchanged, accurate |
| DOC-03 | 19-02 | Owner-facing capability-configuration guide | ✓ SATISFIED | CR-01/CR-02/WR-01 confirmed genuinely fixed — no remaining source-contradicted claim |
| VER-01 | 19-02, 19-04, 19-05 | v1.1 success-criteria gate (SC2 + SC3) | ✗ BLOCKED | SC2 still recorded failed-blocked (structural, not environmental — unbuilt Phase 22 owns it); SC3's coverage defect is fixed but its live run remains unconfirmed |
| AUD-03 | 19-03 | Zero audit gaps across 30 synthetic days | ✗ BLOCKED | Harness unit-proven; live run never executed (no PostgreSQL, `CONTROL_DB_URL` is live production) |

No orphaned requirements — `REQUIREMENTS.md` line 400 maps exactly DOC-01, DOC-02, DOC-03,
VER-01, AUD-03 to Phase 19. `REQUIREMENTS.md`'s own state (`[x]` DOC-01/02/03, `[ ]`
VER-01/AUD-03) now matches this verification's findings **without** the prior nuance flag
— DOC-03's tick is no longer contradicted by its content, since the content is now
genuinely accurate. `ACT-04` and `CAP-03`'s corrected notes (reverted `[x]`→`[ ]`;
"18-10 has run" correction) independently confirmed present and accurate in
`REQUIREMENTS.md` lines 310, 318.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `apps/api/tests/integration/test_ver01_adversarial_harness.py` | 84-141 | Long module-docstring narration of a now-fixed defect (WR-03) | ℹ️ Info | Historically accurate (the docstring still correctly describes the fix and its rationale); not misleading, left as-is |

No `TBD`/`FIXME`/`XXX` debt markers found in any file touched by this gap-closure pass
(`docs/guides/owner-capability-guide.md`, `docs/guides/tool-author-guide.md`,
`apps/api/tests/integration/test_ver01_adversarial_harness.py`,
`apps/api/tests/unit/test_ver01_harness_probes.py`,
`apps/api/tests/integration/test_aud03_audit_gap.py`). No blocker-severity findings
remain from the prior review — CR-01 and CR-02 (both previously 🛑 Blocker) are
independently confirmed closed.

### Human Verification Required

1. **VER-01 SC3 live adversarial run**
   **Test:** `cd apps/api && INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_ver01_adversarial_harness.py -m integration -q -s`, against a real local PostgreSQL server.
   **Expected:** `invalid: False`, ≥100 attempted, zero `provider_not_configured`, empty `unauthorized_mutations`.
   **Why human:** No PostgreSQL server is installed on this machine (independently reconfirmed — no `psql`/`pg_ctl` on PATH), and `CONTROL_DB_URL` is live Neon production, not a usable substitute. This harness has never been executed against a live database. Note: unlike the prior verification, a clean pass here would now genuinely mean the Actor-seam/injection layers were exercised for all affected skills — the WR-03 coverage collision this note previously warned about is fixed.

2. **AUD-03 live 30-day audit-gap run**
   **Test:** `cd apps/api && INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_aud03_audit_gap.py -m integration -q -s`, against a real local PostgreSQL server.
   **Expected:** `vacuous: False`, 30 days with traffic, zero out-of-window rows, per-day delta 0.
   **Why human:** Same missing-PostgreSQL cause, independently reconfirmed. Never executed against a live database.

### Gaps Summary

One confirmed gap remains, down from three:

1. **VER-01 SC2 is still not achieved.** The operator's `[failed — blocked]` disposition
   from `19-UAT.md` is independently reconfirmed accurate: `validate_tighten_only` still
   makes capability `enabled=True` unreachable through any shipped API for any skill, and
   the `require_human` branch's `pending_confirmations` row still has no resolution path
   anywhere in the codebase. Both are now assigned to a named owner (Phase 22, `CAP-05` +
   `ACT-07`), which is real, honest planning progress — but Phase 22 has zero plans
   executed, so the capability still does not exist. This verification does not treat
   assignment as closure. SC2 is Phase 19's own second numbered success criterion in
   ROADMAP.md, not a concern incidentally picked up by a later phase's unrelated scope,
   so it is reported as a live gap rather than filtered into the deferred list under
   Step 9b.

Two of the prior verification's three gaps are genuinely closed, confirmed independently
rather than trusted from `19-REVIEW-FIX.md`'s own counters:

- **DOC-03's owner guide is now factually accurate.** Both Critical findings (CR-01,
  CR-02) and the related Warning (WR-01) are closed. A non-technical owner reading this
  guide today will not be told a control exists that cannot be reached, nor that a
  client-side safeguard is server-enforced when it is not.
- **VER-01 SC3's adversarial corpus now genuinely exercises the layers its labels
  claim.** The rate-window collision (WR-03) that previously caused ~43 of ~119
  confused-deputy/injection entries to be pre-empted by `rate_denied` before reaching
  the Actor gate is fixed via per-attack-class agent_id isolation, with no rate limit
  raised or weakened anywhere. A new, DB-free guard test proves the isolation holds and
  will fail loudly if it ever regresses.

The two live-gate items (VER-01 SC3's actual ≥100-message run, and AUD-03's 30-day run)
remain unproven for an environmental reason outside this phase's control — no PostgreSQL
server exists on this machine, and the only reachable Postgres (`CONTROL_DB_URL`) is live
production, which neither harness should be pointed at. These remain honestly recorded as
`? UNCERTAIN` / human-verification items, not silently passed.

**Overall:** this phase's own two most consequential fixable defects are now closed. The
remaining gap (SC2) is a missing product capability that a documentation phase cannot
close by itself, and its ownership has been correctly handed to a new phase rather than
papered over. The phase goal — "prove the milestone's success criteria end-to-end" — is
still not fully achieved, because SC2 does not hold and SC3's live proof remains
unexecuted, but the phase is materially closer to its goal than at the prior
verification (3/8 → 5/8 verified truths, zero Blocker-severity findings remaining).

---

_Verified: 2026-07-28T16:30:00Z_
_Verifier: Claude (gsd-verifier)_
