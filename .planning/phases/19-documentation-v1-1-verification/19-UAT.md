---
status: testing
phase: 19-documentation-v1-1-verification
source: [19-02-SUMMARY.md, 19-03-SUMMARY.md, 19-04-SUMMARY.md]
started: 2026-07-27T23:16:00Z
updated: 2026-07-27T23:16:00Z
---

## Current Test

number: 1
name: VER-01 SC2 — non-technical tester deploys a refund + Shopify-order agent end to end
expected: |
  A person who cannot code completes signup through deployment and drives a
  successful refund and a successful Shopify order using only the admin UI
  and the widget, with no terminal, no curl, and no code edit.
awaiting: operator run or explicit deferral

## Tests

### 1. VER-01 SC2 — non-technical tester deploys a refund + Shopify-order agent end to end

expected: |
  A person who cannot code completes signup through deployment and drives a
  successful refund and a successful Shopify order using only the admin UI
  and the widget. The tester is given no briefing beyond
  `docs/guides/owner-capability-guide.md` and receives no narration or
  assistance from the operator during the run.

how: |
  **Preconditions (operator sets these up beforehand — never part of the
  tester's own steps):**
  1. Start local PostgreSQL.
  2. Start local Redis: `redis-server`
  3. Start the API: `cd apps/api && uvicorn app.main:app --reload`
  4. Start the worker: `celery -A app.worker.celery_app worker`
  5. Provision a live Shopify test-mode credential per
     `docs/runbooks/integration-credentials.md`.
  6. Establish the tenant configuration, quoting `VER01_DEMO_TENANT_ENVELOPES`
     (`apps/api/tests/unit/test_ver01_demo_tenant.py`) field by field:
     `issue_refund` enabled at a `499`-cent ceiling with a `5/hour` rate
     limit, requires_confirmation `False`, requires_identity_verification
     `False`, actor_mode `always-on`; `place_order` enabled at a `20000`-cent
     ceiling with a `5/hour` rate limit, same posture on the other four
     fields; the other four mutating skills (`cancel_order`,
     `update_subscription`, `book_slot`, `update_customer_record`) left at
     the shipped platform default (disabled).
  7. No container runtime anywhere in this precondition set (CLAUDE.md
     rule 9) — local processes only.

  **The only document the tester is handed:** `docs/guides/owner-capability-guide.md`.
  The tester receives no other briefing and the operator does not narrate or
  assist during the run.

  **Numbered tester steps, with the expected outcome stated after each:**
  1. Tester signs up and reaches the agent-configuration screen using only
     the admin UI. Expected: signup completes with no terminal step.
  2. Tester reviews the deploy screen's capability panels against
     `docs/guides/owner-capability-guide.md` and confirms the pre-seeded
     `issue_refund` / `place_order` posture. Expected: the tester can explain
     in their own words what each control means, using only the guide.
  3. Tester deploys the agent through the admin UI's deployment checklist.
     Expected: the checklist passes and the agent goes live with no code
     edit.
  4. Tester opens the widget and drives a refund conversation ending at or
     under R4.99 (499 cents). Expected: the refund completes deterministically
     (the Actor's low-value skip engages — no human-in-the-loop wait).
  5. Tester opens the widget and drives a Shopify order conversation.
     Expected: the order either completes end to end, or the conversation
     reaches the `require_human`/awaiting-approval state described below.

  **Three explicit failure rules the operator must apply, not work around:**
  - Any step requiring a terminal, a curl, or a code edit **fails the
    criterion** and is recorded as a failure, not routed around.
  - If no genuinely un-briefed non-technical tester is available, the item
    is **deferred** — never satisfied by the operator standing in for the
    tester.
  - If the `place_order` leg returns the awaiting-approval response, that is
    the accepted `T-19-04` gap firing (see item 4) — record it verbatim and
    stop that leg rather than reporting a pass.

  **Configuration note:** the `499`-cent refund ceiling is imposed by the
  Actor skip threshold (`ACTOR_SKIP_MAX_AMOUNT_CENTS`, default `500`,
  compared strictly less-than in `apps/api/app/services/actor_seam.py`) and
  is **not** an owner-chosen business limit. SC2's "refunds up to a
  configured limit" is therefore demonstrated at R4.99, and this record says
  so rather than presenting R4.99 as a deliberate business ceiling.

  **Structural note the operator should read before running this item:**
  see item 4's second entry — the demo tenant's `enabled: True` rows above
  cannot be reached by the tester through the shipped admin UI at all; they
  must be seeded directly. This bears on whether SC2 is satisfiable as
  written, independent of how the tester's own run goes.

result: [pending]

### 2. VER-01 SC3 — 100 synthetic adversarial messages, zero unauthorized mutations

expected: |
  The gated harness reports `invalid: False`, at least 100 attempted
  messages, zero `provider_not_configured` verdicts, and an empty
  `unauthorized_mutations` list.

how: |
  Preconditions: local PostgreSQL reachable at the `TEST_ADMIN_DB_URL` /
  `TEST_LOCAL_BASE` defaults, local `redis-server` running, `ANTHROPIC_API_KEY`
  set. No container runtime.

  ```
  cd apps/api
  INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_ver01_adversarial_harness.py -m integration -q -s
  ```

  Transcribe the printed **attempted** count and the full per-verdict
  (`by_verdict`) table into `result:`. A zero-finding result recorded
  **without** the attempted count is not an acceptable record — the
  attempted count is what distinguishes a clean run from a vacuous one
  (`19-VALIDATION.md § Manual-Only Verifications`).

result: [pending]

### 3. AUD-03 — zero audit gaps across a synthetic 30-day window

expected: |
  `vacuous: False`, 30 days with traffic, zero out-of-window rows, and a
  per-day delta of 0 on every day.

how: |
  Preconditions: local PostgreSQL, local `redis-server`. No container
  runtime.

  ```
  cd apps/api
  INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_aud03_audit_gap.py -m integration -q -s
  ```

  Transcribe the invocation count, the audit-row count, and the delta
  (must be 0) into `result:`.

result: [pending]

### 4. Accepted gaps and open items (record only, no run)

expected: |
  This item is a written record, not a test. `result:` is `[recorded]`.

how: |
  No run — this item documents structural facts surfaced during Phase 19
  planning and execution that must not be silently dropped.

result: [recorded]

**T-19-04 — accepted.** The `require_human` branch writes a
`pending_confirmations` row that nothing in the codebase reads or resolves;
there is no admin route, no Celery task, and no CLI script that sets
`resolved_at` or `resolution`. Phase 19 accepted this per
`19-01-PLAN.md § OD-2` rather than closing it, because closing it requires a
human-approved bypass seam inside `_execute_transactional_tool` — re-entering
the dispatcher without one re-runs the Actor and loops. Uncovered by any
Phase 19 requirement ID. Made observable by
`tests/unit/test_ver01_demo_tenant.py::test_demo_place_order_envelope_does_not_engage_skip`.
Follow-up: v1.2.

**Capability `enabled=True` is unreachable through any shipped API — a
second, distinct SC2 blocker.** `validate_tighten_only`
(`apps/api/app/services/capability_service.py:307-313`) rejects every
`enabled: False -> True` transition unless the skill's own platform default
already ships `enabled: True` — and every entry in
`PLATFORM_CAPABILITY_DEFAULTS` ships `enabled: False`. The function's own
docstring (lines 256-261) states this outright: "Every platform default
ships enabled=False, so in practice re-enabling a disabled skill is not
reachable through this route — a chosen consequence, not a surprise." No
other code path in `apps/api/app/` sets `enabled=True` on a capability
envelope; the only writes are that PATCH route and read-time synthesis of
`enabled: False`. Both `VER01_DEMO_TENANT_ENVELOPES` (this phase) and
Phase 18's `CLEAN_TENANT_ENVELOPES` seed their `enabled: True` rows directly
into the database, not through the admin UI.

This bears directly on VER-01 SC2, whose criterion is a non-technical tester
deploying an agent that issues refunds and places Shopify orders **without
code**. If enabling `issue_refund` and `place_order` requires direct database
seeding, a non-technical tester cannot complete the deploy unaided — SC2 as
written is not satisfiable against the current build, independent of how
item 1's tester run itself goes. This finding is recorded here as a distinct,
named item; it is not resolved, not worked around, and not soft-pedaled in
item 1's wording above. Its disposition (deferral or failure) is the
operator's to record against item 1, not this executor's to pre-judge.
Uncovered by any Phase 19 requirement ID. Not claimed as fixed by this plan
— no file under `apps/api/app/` is touched by Phase 19.

**RTX-04 — not claimed, not closed.** Phase 19's adversarial harness reuses
the Phase 18 probe substrate but asserts VER-01 SC3's own `verdict_tag`
claim, not RTX-04's severity aggregate. `test_clean_tenant_zero_high_severity`
does not exist; plan `18-11` is unexecuted and remains RTX-04's owner. Phase
19 passing does not make RTX-04 proven.

**DOC-01/02/03 correctness is human-reviewed, not machine-proven.** The
anchor gates prove specific literals are present and, for the tool-author
guide, that the eight enforcement steps appear in source order. They do not
prove the surrounding prose is accurate.
`19-VALIDATION.md § Manual-Only Verifications` names the review each guide
still needs against its source anchors.

## Summary

total: 4
passed: 0
issues: 0
pending: 4
skipped: 0
blocked: 0

## Gaps

- Items 1-3 above (VER-01 SC2, VER-01 SC3, AUD-03) each require real local
  infrastructure (Postgres, Redis, and for item 1 a live Shopify test-mode
  credential and an un-briefed non-technical tester) that this execution
  environment does not provide. This mirrors the Phase 13 AWS live-gate
  deferral (`13-08..11`), the Phase 14 live-DB deferral (`14-UAT.md`
  items 1-3), the Phase 15 ACT-06 latency deferral (`15-03-SUMMARY.md`), and
  the Phase 16 live-Stripe deferral (`16-UAT.md`). All deferred items are
  recorded in UAT files so `/gsd-verify-work` surfaces them.
- Item 1 (VER-01 SC2) additionally carries a structural blocker independent
  of environment availability: capability `enabled=True` cannot be reached
  through any shipped API, so the demo tenant's own precondition setup
  requires a step no non-technical owner could perform unaided. See item 4.
