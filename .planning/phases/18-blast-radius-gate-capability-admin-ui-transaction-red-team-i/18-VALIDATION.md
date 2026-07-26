---
phase: 18
slug: blast-radius-gate-capability-admin-ui-transaction-red-team-injection-defense-extensions
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-26
---

# Phase 18 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `18-RESEARCH.md` § Validation Architecture, with one correction
> applied — see **Corrected environment claim** below.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (`pytest-asyncio==1.3.0`, `respx==0.23.1`) |
| **Config file** | `apps/api/pyproject.toml` `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, markers `integration` / `e2e` |
| **Quick run command** | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit/<touched_file>.py -x -q` |
| **Full suite command** | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` |
| **Estimated runtime** | ~180 seconds full unit suite; individual module 2–100s |

**Baseline as of 2026-07-26: 970 passed, 0 failed, 7 skipped.** Any red result
during Phase 18 is attributable to Phase 18 — there is no pre-existing-failure
alibi. Establish this by running the full suite before Wave 0.

### Corrected environment claim (verified 2026-07-26)

`18-RESEARCH.md` § Validation Architecture carries forward a "Known environment
gap" from `21-08-SUMMARY.md` stating that any test importing `app.main` fails to
collect via `ragas → langchain_community.chat_models.vertexai`
`ModuleNotFoundError`, and therefore that route-level assertions must be routed
around with service-layer equivalents.

**That gap is closed and the workaround is unnecessary.** Commit `9f50028`
(`fix(deps): pin langchain-community <0.4 to unblock \`import ragas\``) repaired
the dependency pairing. Verified directly:

- `import ragas` → `ragas 0.4.3`, no error.
- `pytest tests/unit/test_deployment_routes.py` → **5 passed** (this is the exact
  module the research named as blocked).
- `tests/unit/test_agent_chat_routes.py` imports `app.main` and passes in the
  full suite.

**Consequence for planning:** BLR-02's "`POST /approve-deployment` returns 422 on
envelope drift" MUST be a real route-level test, not a service-layer stand-in.
The 422 is an HTTP contract; asserting it below the route leaves the status code
itself unverified — which is precisely the class of gap Phase 21's verifier
caught (an implemented task with no caller). Do not accept a service-layer
substitute for any assertion whose subject is a status code or a response shape.

### Genuine environment constraints (these are real)

| Constraint | Effect on validation |
|---|---|
| `tests/unit/test_chunking_service.py` + `test_docling_service.py` cannot collect — `docling` / `docling_core` absent from `apps/api/.venv` | Both are `--ignore`d in the full-suite command above. Untouched by Phase 18. |
| `EMBEDDING_PROVIDER` defaults to `"bedrock"` (`config.py:142`) and there is no AWS/Bedrock access | Any Phase 18 test that reaches an embed path MUST pin the provider (`monkeypatch.setattr(<module>.settings, "EMBEDDING_PROVIDER", "voyage")`) or mock the boundary. An unpinned test issues a real `InvokeModel` call and fails after tenacity retries. This bit three existing tests; see the `_force_voyage_provider` fixture in `tests/unit/retrieval/test_retrieval_service.py` for the pattern. **Relevant to SEC-03's content-injection probe, which ingests a poisoned chunk.** |
| Real `claude_agent_sdk` vs the fake-SDK bootstrap is import-order dependent | Any new test that calls an `@tool`-decorated function must resolve it through a `_fn()`-style helper (`getattr(t, "handler", t)`), not call it directly. See `tests/unit/test_agent_tools.py::_fn`. **Relevant to SEC-02, which extends `test_agent_tools.py`.** |
| 4 GB Windows box, no Docker; cold `import app.main` measured 108–144s | Route-module tests cost ~100s each. Budget for it; do not interpret slowness as a hang. Never propose docker-compose (CLAUDE.md rule 9). |
| No live Neon DB has any v1.2 migration applied (tenant 0009–0012, control 0017–0018) | A new control migration 0019 cannot be roundtrip-verified live without operator action → mark `autonomous:false`. |

---

## Sampling Rate

- **After every task commit:** run the specific unit file(s) that task touched
  (`pytest tests/unit/test_X.py -x -q`). Keep under ~30s where the module allows;
  route modules are exempt (~100s, cold-import bound).
- **After every plan wave:** full unit suite command above, plus any new
  integration tests with `-m integration` when local Postgres + Redis are up.
- **Before `/gsd-verify-work 18`:** full unit suite green at ≥970 passed, and the
  RTX-04 `autonomous:false` live gate either run and recorded, or explicitly
  deferred in `18-UAT.md` with operator acceptance — the pattern Phases 13, 15,
  16, 17 and 21 all used. A silent skip is not acceptable.
- **Max feedback latency:** 180 seconds (full suite).

---

## Per-Task Verification Map

Task IDs are assigned by the planner; this map binds each requirement to its
verifying command and its threat reference. The planner MUST carry every row into
PLAN.md task `<verify>` blocks and fill the Task ID column.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| _tbd_ | _tbd_ | 1 | BLR-01 | T-18-BLR-01 | Blast-radius collector reports **configured limit** and **observed maximum** as separate claims; neither is presented as the other | unit | `pytest tests/unit/test_deployment_service.py::test_fetch_blast_radius_sync -x` | ❌ W0 (extend) | ⬜ pending |
| _tbd_ | _tbd_ | 1 | BLR-02 | T-18-BLR-02 | Envelope hash is deterministic over a canonical field order; a no-op re-save does not change it; a semantic edit does | unit | `pytest tests/unit/test_deployment_service.py::test_envelope_hash_stability -x` | ❌ W0 (extend) | ⬜ pending |
| _tbd_ | _tbd_ | 2 | BLR-02 | T-18-BLR-03 | `POST /approve-deployment` returns **422** when the live envelope hash differs from the hash the checklist run acknowledged | **route-level** integration | `pytest tests/unit/test_deployment_routes.py::test_approve_deployment_envelope_drift_422 -x` | ❌ W0 (extend; module exists and passes) | ⬜ pending |
| _tbd_ | _tbd_ | 2 | CAP-03 | T-18-CAP-01 | PATCH rejects a **loosening** change on every field: `enabled` false→true, rate limit raised, `max_amount_cents` raised, `requires_confirmation` true→false, `requires_identity_verification` true→false, `actor_mode` relaxed | unit | `pytest tests/unit/test_capability_service.py::test_validate_tighten_only -x` | ❌ W0 (new) | ⬜ pending |
| _tbd_ | _tbd_ | 2 | CAP-03 | T-18-CAP-02 | Tighten-only is enforced **server-side in the service**, so a direct API call bypassing the UI is rejected identically | unit | `pytest tests/unit/test_capability_service.py::test_tighten_only_enforced_below_route -x` | ❌ W0 (new) | ⬜ pending |
| _tbd_ | _tbd_ | 2 | CAP-04 | T-18-CAP-03 | An envelope PATCH marks drift such that the latest checklist read reports it | unit | `pytest tests/unit/test_capability_service.py::test_envelope_drift_flag -x` | ❌ W0 (new) | ⬜ pending |
| _tbd_ | _tbd_ | 3 | RTX-01 | T-18-RTX-01 | Confused-deputy probe driving the **real dispatcher** yields an Actor `block`/`require_human`, classified and stored as a finding | integration (`INTEGRATION_TESTS_ENABLED`) | `pytest tests/integration/test_red_team_rtx.py::test_confused_deputy -x -m integration` | ❌ W0 (new) | ⬜ pending |
| _tbd_ | _tbd_ | 3 | RTX-02 | T-18-RTX-02 | Chained small-value sequence trips the rate/constraint layer rather than summing under the per-action cap | integration | `pytest tests/integration/test_red_team_rtx.py::test_value_bound_evasion -x -m integration` | ❌ W0 (new) | ⬜ pending |
| _tbd_ | _tbd_ | 3 | RTX-03 | T-18-RTX-03 | Unverified-identity attempt on a `requires_identity_verification=true` skill is blocked **server-side** (Step 2.5 gate), not by agent prose | integration | `pytest tests/integration/test_red_team_rtx.py::test_identity_bypass -x -m integration` | ❌ W0 (new) | ⬜ pending |
| _tbd_ | _tbd_ | 4 | RTX-04 | T-18-RTX-04 | Full RTX suite on a defined **clean tenant** fixture yields zero high/critical findings | integration, **`autonomous:false`** live gate | `pytest tests/integration/test_red_team_rtx.py::test_clean_tenant_zero_high_severity -x -m integration` | ❌ W0 (new) | ⬜ pending |
| _tbd_ | _tbd_ | 3 | SEC-01 | T-18-SEC-01 | A response carrying PII is transformed per the locked failure mode (redact / block / escalate) and the flag is logged | unit | `pytest tests/unit/test_pii_firewall.py -x` | ❌ W0 (new) | ⬜ pending |
| _tbd_ | _tbd_ | 3 | SEC-01 | T-18-SEC-02 | The firewall **cannot be disabled by prompt content** — a response instructing it to stand down is still filtered | unit | `pytest tests/unit/test_pii_firewall.py::test_firewall_not_prompt_disableable -x` | ❌ W0 (new) | ⬜ pending |
| _tbd_ | _tbd_ | 2 | SEC-02 | T-18-SEC-03 | `retrieve_tool` output wraps chunk text in explicit "data, not instructions" framing; framing survives the truncation path | unit | `pytest tests/unit/test_agent_tools.py::test_retrieve_tool_data_wrapper -x` | ✅ module exists (new case) | ⬜ pending |
| _tbd_ | _tbd_ | 3 | SEC-03 | T-18-SEC-04 | `run_red_team` invokes **both** conversation-injection and content-injection runners; the content variant ingests then queries a poisoned chunk | unit + integration | `pytest tests/unit/test_red_team_service.py::test_conversation_content_split -x` | ✅ module exists (new case) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**Sampling continuity check:** no three consecutive tasks may lack an automated
`<verify>`. The RTX cluster is the risk area — its tests are integration-gated,
so the planner must interleave a unit-verifiable task or provide a mocked-boundary
unit test alongside each integration test.

---

## Wave 0 Requirements

- [ ] `tests/unit/test_capability_service.py` — **new file**, CAP-03 / CAP-04
- [ ] `tests/unit/test_pii_firewall.py` — **new file**, SEC-01
- [ ] `tests/integration/test_red_team_rtx.py` — **new file**, RTX-01..04;
      `INTEGRATION_TESTS_ENABLED`-gated, with RTX-04 as a separate
      `autonomous:false` live-gate case. Mirror the ephemeral-tenant-DB pattern in
      `tests/integration/test_deploy_gate_redteam.py`.
- [ ] `tests/unit/test_deployment_service.py` — **extend** (exists): blast-radius
      collector + envelope-hash cases
- [ ] `tests/unit/test_deployment_routes.py` — **extend** (exists, 5 passing): the
      envelope-drift 422 route case
- [ ] `tests/unit/test_red_team_service.py` — **extend** (exists): conversation /
      content split cases
- [ ] `tests/unit/test_agent_tools.py` — **extend** (exists): SEC-02 wrapper case.
      Use the existing `_fn()` helper to invoke `retrieve_tool`; do not call the
      decorated object directly.
- [ ] Framework install: **none** — pytest / pytest-asyncio / respx already pinned.

A `clean tenant` fixture is a Wave 0 deliverable in its own right: RTX-04's
success criterion is unprovable without a written definition of what "clean"
means (which skills enabled, which envelope limits, verified-identity posture,
provider posture). The planner must name it as a task, not assume it.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Owner acknowledges the envelope hash at deploy | BLR-02 | The acknowledgement is a human act in the M8 checklist UI; the server side is automated but the gesture is not | Open Deploy → Pre-Deploy, confirm the blast-radius panel shows configured limit + observed max, tick the envelope acknowledgement, approve. Then edit any envelope limit and confirm approve now returns 422 / the checklist demands a re-run. |
| Capability UI tighten-only affordance | CAP-03 | Server enforcement is unit-tested; that the UI *presents* loosening as unavailable rather than erroring after submit is a visual/interaction judgement | Load the capability panel, attempt to raise a limit, confirm the control refuses at input time and explains why. Must honour the GOTHAM contract in `DESIGN.md` — verdict-only colour, no decorative hue. |
| Control migration 0019 live roundtrip | BLR-02 / CAP-03 | No live Neon DB currently holds even the v1.2 migrations | Apply 0019 up + down against a real control DB once an operator provisions one; record in `18-UAT.md`. |
| RTX-04 clean-tenant zero-high-severity run | RTX-04 | Needs a real `ANTHROPIC_API_KEY` and a migrated ephemeral tenant DB | Run the gated integration test with credentials present; record max severity and finding count in `18-UAT.md`. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or a named Wave 0 dependency
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all ❌ references above
- [ ] No watch-mode flags
- [ ] Feedback latency < 180s
- [ ] Full unit suite green at ≥970 passed before Wave 0 begins (baseline lock)
- [ ] Every embed-touching test pins `EMBEDDING_PROVIDER` or mocks the boundary
- [ ] BLR-02's 422 is asserted at the route, not below it
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
