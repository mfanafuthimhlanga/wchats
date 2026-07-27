---
phase: 19
slug: documentation-v1-1-verification
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-27
---

# Phase 19 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `19-RESEARCH.md` § Validation Architecture and § Security Domain.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + `pytest-asyncio` (`asyncio_mode = "auto"`), already pinned |
| **Config file** | `apps/api/pyproject.toml` `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, markers `integration` / `e2e` |
| **Quick run command** | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit/<touched_file>.py -x -q` |
| **Full suite command** | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` |
| **Live gate command** | `INTEGRATION_TESTS_ENABLED=1 pytest tests/integration/<file>.py -m integration -q -s` — requires local Postgres + Redis + `ANTHROPIC_API_KEY` |
| **Estimated runtime** | ~180s full unit suite; route modules ~100s each (cold `import app.main`) |

**Baseline as of 2026-07-27 (post 18-09): 1103 passed, 8 skipped, 0 failed.**
Any red result during Phase 19 is attributable to Phase 19 — there is no
pre-existing-failure alibi. Establish this by running the full suite before Wave 0.

### This phase's distinguishing validation problem

Phase 19 is the first phase in this project whose requirements are **majority
non-code**. Three of its five requirements (DOC-01/02/03) produce markdown, and
markdown has no `pytest`. The failure mode this creates is specific and worth
naming: a guide that is fluent, well-organised, and **wrong** passes every
automated check this repo can run, and is discovered only when a reader follows
it and the code does something else.

The mitigation is not to invent a doc-testing framework. It is to make each
guide's correctness claim **anchored to a named source file**, so review is a
diff against source rather than a vibe check. Every DOC task must therefore carry
its source anchors in `<read_first>`, and its `<acceptance_criteria>` must assert
the presence of specific, checkable content (an exact enforcement-order list, an
exact field name, an exact default value) — never "accurately describes" or
"reads clearly". `19-RESEARCH.md` § Shipped Surface (three sections) already
excerpted those anchors; the planner lifts them rather than re-deriving them.

### Genuine environment constraints (these are real)

| Constraint | Effect on validation |
|---|---|
| `tests/unit/test_chunking_service.py` + `test_docling_service.py` cannot collect — `docling` / `docling_core` absent from `apps/api/.venv` | Both stay `--ignore`d in every full-suite command this phase's plans specify, exactly as `18-11-PLAN.md` does. Untouched by Phase 19. |
| No live Neon DB holds the v1.2 or Phase-18 migrations; no live AWS / Stripe / Shopify access in this environment | VER-01's Shopify-order leg and AUD-03's DB-backed window are inherently live-gated → both must be `autonomous:false`, run by the operator, and recorded in `19-UAT.md`. Mirrors the Phases 13/15/16/17/18 deferral pattern. **A silent skip is not acceptable** — deferral requires explicit operator acceptance in writing. |
| `EMBEDDING_PROVIDER` defaults to `"bedrock"` (`config.py:142`) with no AWS access | Only relevant if a VER-01 adversarial message reaches a retrieval path. Any such test must pin the provider (`monkeypatch.setattr(<module>.settings, "EMBEDDING_PROVIDER", "voyage")`) or mock the boundary, per the `_force_voyage_provider` precedent. |
| No clock-injection or time-acceleration mechanism exists anywhere in the codebase; `write_audit_row` has no `created_at` parameter | AUD-03's "30 days" is **only** constructible as seeded backdated rows via direct SQL. Any plan proposing `freezegun`, a fake clock, or a `created_at` kwarg on `write_audit_row` is proposing new production surface for a test — reject it. |
| 4 GB Windows box, no Docker (CLAUDE.md rule 9) | Every command in a guide, a UAT runbook, or a test instruction is a local process (`redis-server`, local PostgreSQL, `uvicorn`, `celery -A app.worker.celery_app worker`). Never `docker-compose`. This applies to the **published guides** as well as the tests — a guide that tells a reader to run a container is a defect. |
| `pending_confirmations` rows are written by two paths and read by **zero** (`19-RESEARCH.md` § Critical Finding) | VER-01 SC2's happy path can non-deterministically dead-end. Whichever disposition the planner locks, the validation consequence is mandatory — see **Open-decision-dependent validation** below. |

### Open-decision-dependent validation

The planner must close `19-RESEARCH.md` § Open Decisions (b) before this map is
complete. The two dispositions imply **different** validation surfaces, and the
plan must carry whichever one it picks:

- **If Option 1 (build a minimal `pending_confirmations` resolve route):** that
  route is new production code in a security-sensitive position, and it inherits
  the full route-level test obligation — IDOR (`_get_owned_agent`, 404 on both
  branches), `extra="forbid"` body validation, and a test proving that approving
  a stale confirmation **re-enters the dispatcher's checks** rather than calling
  the adapter directly. Asserted at the route via `ASGITransport`, not below it.
- **If Option 2 (configure the demo tenant so `require_human` is unreachable):**
  the tenant configuration itself becomes the artifact under test. A test must
  prove the skip short-circuit actually engages for the demo tenant's configured
  values (`requires_confirmation is False` AND `max_amount_cents <
  ACTOR_SKIP_MAX_AMOUNT_CENTS`), because the platform default of 50 000c sits
  100x above the 500c threshold — the configuration is non-obvious and silently
  reverting it re-opens the dead end. The residual gap must be recorded in
  `19-UAT.md` as accepted, not omitted.

---

## Sampling Rate

- **After every task commit:** run the specific unit file(s) that task touched
  (`pytest tests/unit/test_X.py -x -q`). For a DOC task, the equivalent
  fast feedback is the source-anchor check — confirm each cited symbol, default,
  and ordering still exists in the named file (`grep` against the anchor, or the
  existing unit test that pins it).
- **After every plan wave:** full unit suite command above, plus the new
  integration modules collected-but-skipped (`INTEGRATION_TESTS_ENABLED` unset)
  to prove they at least import and collect cleanly.
- **Phase gate, before `/gsd-verify-work 19`:** full unit suite green at ≥1103
  passed, **and** the three `autonomous:false` gates each either run for real by
  the operator and recorded, or explicitly deferred with operator acceptance in
  `19-UAT.md`.
- **Max feedback latency:** 180 seconds (full unit suite).

---

## Per-Task Verification Map

Task IDs are assigned by the planner; this map binds each requirement to its
verifying command and its threat reference. The planner MUST carry every row into
PLAN.md task `<verify>` blocks and fill the Task ID / Plan / Wave columns.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| _planner_ | _planner_ | _planner_ | DOC-01 | — | Guide states the dispatcher's enforcement order as the **eight numbered steps in source order**, and states that a tool author never modifies that order | source-anchored review | `grep` the guide for each of the 8 step names; diff against `apps/api/app/services/transactional/tools.py` | ❌ W0 (new file) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | DOC-01 | — | Guide states that `mutating` / `idempotency_required` / `requires_identity_verification` are literal values set at definition time, **never runtime-inferred** (T-14-02-02) | source-anchored review | diff against `registry.py` `TOOL_METADATA` | ❌ W0 (new file) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | DOC-02 | T-19-02 | Guide documents `get_adapter_for_skill` as the **only** credential-resolving entry point and reproduces its "MUST NOT be called from a route handler or SDK hook" constraint | source-anchored review | diff against `provider_adapter.py` docstring | ❌ W0 (new file) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | DOC-02 | T-19-02 | Guide never instructs a reader to log, print, or persist a resolved credential; `CredentialHandle`'s redacted `__repr__` is documented as load-bearing | source-anchored review | `grep` the guide for the absence of any credential-printing example | ❌ W0 (new file) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | DOC-03 | — | Guide states tighten-only is enforced **server-side before any DB write** (a 422 leaves the row untouched), not merely presented by the UI | source-anchored review | diff against `capability_service.py::validate_tighten_only` + `capability_envelopes.py` | ❌ W0 (new file) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | DOC-03 | — | Guide states the six per-skill controls and the **actual shipped platform defaults** (`enabled:False`, `5/hour`, `actor_mode:always-on`, `place_order` 100 000c / others 50 000c) | source-anchored review | diff against `PLATFORM_CAPABILITY_DEFAULTS` | ❌ W0 (new file) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | VER-01 (happy path) | T-19-01 | A non-technical tester completes refund + Shopify order end-to-end **without writing code**; every step is UI or widget | manual, `checkpoint:human-verify`, **`autonomous:false`** | scripted runbook, result transcribed into `19-UAT.md` | ❌ W0 (new runbook) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | VER-01 (happy path) | T-19-04 | The demo tenant's Actor disposition is **proven**, not assumed — either the resolve route exists, or the skip short-circuit provably engages | unit | `pytest tests/unit/<actor-disposition-test> -x` (exact node id set by the planner once OD (b) is closed) | ❌ W0 (new) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | VER-01 (adversarial) | T-19-03 | 100 synthetic adversarial messages produce **zero** unauthorized state mutations escaping L1–L3, classified against the shipped `verdict_tag` vocabulary | integration, **`autonomous:false`** | `pytest tests/integration/test_ver01_adversarial_harness.py -m integration -q -s` | ❌ W0 (new file) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | VER-01 (adversarial) | T-19-03 | Every probe message runs inside a `red_team_mode()` window, so no real provider side effect can fire | unit | `pytest tests/unit/<harness-unit-file>::test_all_probes_inside_red_team_mode -x` | ❌ W0 (new) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | AUD-03 | T-19-05 | Zero audit gaps across a synthetic 30-day window — dispatcher-invocation count equals `tool_calls_audit` row count, on **rejection branches as well as success** | integration, **`autonomous:false`** | `pytest tests/integration/test_aud03_audit_gap.py -m integration -q -s` | ❌ W0 (new file) | ⬜ pending |
| _planner_ | _planner_ | _planner_ | AUD-03 | T-19-06 | The harness seeds backdated rows only into an **ephemeral** DB and cleans up from a `finally`, never into a DB holding real audit history | integration | same module; assert teardown in the same test | ❌ W0 (new file) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**Sampling continuity check:** no three consecutive tasks may lack an automated
`<verify>`. **This is the acute risk of this phase** — DOC-01/02/03 are three
consecutive manual-review requirements, and the two harness requirements are both
integration-gated. The planner MUST NOT sequence the three DOC tasks
back-to-back-to-back without an automated task interleaved, and must pair each
`autonomous:false` integration gate with a mocked-boundary unit companion that
runs in the normal suite (the pattern 18-06 used: `test_red_team_rtx_runners.py`
unit companion alongside the gated `test_red_team_rtx.py`).

---

## Wave 0 Requirements

- [ ] `docs/guides/tool-author-guide.md` — **new file**, DOC-01
- [ ] `docs/guides/integration-provider-guide.md` — **new file**, DOC-02
      (extends, does not duplicate, `docs/runbooks/integration-credentials.md`)
- [ ] `docs/guides/owner-capability-guide.md` — **new file**, DOC-03
- [ ] `apps/api/tests/integration/test_ver01_adversarial_harness.py` — **new file**,
      VER-01 SC3. `INTEGRATION_TESTS_ENABLED`-gated. Built on the shipped
      `red_team_probe.py` substrate — extend `CLEAN_TENANT_SPEC` and the RTX
      runners, do **not** author a second parallel fixture.
- [ ] `apps/api/tests/integration/test_aud03_audit_gap.py` — **new file**, AUD-03.
      Ephemeral-DB fixture pattern copied from `tests/integration/test_red_team_rtx.py`.
- [ ] A **unit companion module** for each of the two integration harnesses, so
      the phase is not three consecutive unverifiable tasks (see continuity check)
- [ ] `.planning/phases/19-documentation-v1-1-verification/19-UAT.md` — **new file**,
      VER-01 SC2 human-checkpoint transcript + the two deferral records, following
      the `16-UAT.md` / `17-UAT.md` house format
- [ ] Framework install: **none** — pytest / pytest-asyncio / anthropic /
      claude-agent-sdk all already pinned. **No new dependency is in scope for
      this phase** (`19-RESEARCH.md` § Package Legitimacy Audit: zero packages proposed).

**A written definition of the VER-01 demo tenant is a Wave 0 deliverable in its
own right.** SC2 is unprovable without stating which skills are enabled, which
envelope limits are set, the identity-verification posture, and the provider
posture — exactly as RTX-04's `CLEAN_TENANT_SPEC` had to be named as a task
rather than assumed. The planner must name it as a task.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Each guide is *correct*, not merely present | DOC-01/02/03 | No doc-correctness framework exists in this repo, and inventing one is out of scope for a documentation phase | Read each guide side-by-side with its named source anchors (`tools.py`, `registry.py`, `provider_adapter.py`, `capability_service.py`). Every enforcement order, field name, and default value must match source. A guide with a stale default is a defect, not a nit. |
| Each guide is usable by its stated audience | DOC-01/02/03 | Audience fit is a judgement, not an assertion — DOC-03's reader is a business owner, not a developer | DOC-03 specifically: confirm no unexplained jargon, no API paths presented as owner instructions, and that "rate limit" / "blast radius" / "envelope drift" are each explained in business language before use. |
| Non-technical tester end-to-end deploy | VER-01 SC2 | The whole claim is that a human who cannot code succeeds — automating it would assert the opposite of what is being proven | Follow the Wave-0 runbook using only the admin UI and widget. Any step requiring a terminal, a curl, or a code edit **fails the criterion** and must be recorded as such, not worked around. Transcribe verbatim into `19-UAT.md`. |
| Shopify-order leg against real test credentials | VER-01 SC2 | No live Shopify access in this environment | Operator runs with real test-mode credentials, or explicitly defers with acceptance recorded in `19-UAT.md` (mirrors the Phase 16 live-Stripe deferral). |
| 100-message adversarial run | VER-01 SC3 | Needs `ANTHROPIC_API_KEY` and a migrated ephemeral tenant DB | Run the gated integration module with credentials present; record message count, max severity, and finding count in `19-UAT.md`. Zero findings with zero messages attempted is a **vacuous pass** — record the attempted count explicitly. |
| 30-day audit-gap run | AUD-03 | Needs a real migrated DB to hold the seeded window | Run the gated integration module; record invocation count, audit-row count, and the delta (must be 0) in `19-UAT.md`. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or a named Wave 0 dependency
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
      (**explicitly re-check the DOC-01/02/03 run**)
- [ ] Wave 0 covers all ❌ references above
- [ ] No watch-mode flags
- [ ] Feedback latency < 180s
- [ ] Full unit suite green at ≥1103 passed before Wave 0 begins (baseline lock)
- [ ] Every DOC acceptance criterion names a source anchor and asserts specific
      checkable content — no "accurately describes" / "reads clearly"
- [ ] Open Decision (b) is closed in PLAN.md, and its dependent validation surface
      (resolve-route tests **or** skip-short-circuit proof) is present
- [ ] AUD-03 uses seeded backdated rows only — no clock injection, no new
      `created_at` parameter on `write_audit_row`
- [ ] Every adversarial probe runs inside `red_team_mode()`
- [ ] No new dependency added (`pyproject.toml` unchanged)
- [ ] Each `autonomous:false` gate is either run and recorded, or deferred with
      explicit operator acceptance in `19-UAT.md` — never silently skipped
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
