# Phase 19: Documentation + v1.1 verification - Pattern Map

**Mapped:** 2026-07-27
**Files analyzed:** 7 (3 prose guides, 1 UAT transcript, 2 gated integration tests, N unit companions)
**Analogs found:** 7 / 7

**Read note:** this phase is majority prose. Three of seven artifacts (DOC-01/02/03)
are markdown, and their analogs are *house-style precedents*, not code. Do not force
them into a code-pattern frame — what follows extracts structural conventions
(heading depth, prerequisites-first-or-not, command rendering, source citation style,
warning-block idiom), not code excerpts.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `docs/guides/tool-author-guide.md` | doc/guide | n/a (prose) | `docs/runbooks/integration-credentials.md` (structure); `docs/adr/0002-...md` (citation style) | house-style precedent |
| `docs/guides/integration-provider-guide.md` | doc/guide | n/a (prose) | `docs/runbooks/integration-credentials.md` (**direct extension target** — DOC-02 must not duplicate it) | exact precedent, delta-scoped |
| `docs/guides/owner-capability-guide.md` | doc/guide | n/a (prose) | `apps/admin/app/agents/[id]/deploy/page.tsx` (locked owner-facing copy, 18-10) + `docs/runbooks/integration-credentials.md` (structure only) | house-style + copy-source precedent |
| `apps/api/tests/integration/test_ver01_adversarial_harness.py` | test, integration | event-driven / adversarial-probe | `apps/api/tests/integration/test_red_team_rtx.py` | exact — direct structural parent |
| `apps/api/tests/integration/test_aud03_audit_gap.py` | test, integration | batch / coverage-check | `apps/api/tests/integration/test_red_team_rtx.py` (ephemeral-DB fixture); `test_deploy_gate_redteam.py` (older precedent it mirrors) | exact — fixture pattern; no query-shape precedent exists (report below) |
| unit companion for VER-01 harness | test, unit | mocked-boundary | `apps/api/tests/unit/test_red_team_rtx_runners.py` | exact — direct structural parent |
| unit companion for AUD-03 harness | test, unit | mocked-boundary | `apps/api/tests/unit/test_red_team_rtx_runners.py` (mocking idiom only — no audit-row precedent exists) | role-match |
| `.planning/phases/19-.../19-UAT.md` | doc, verification transcript | n/a (prose, house format) | `16-UAT.md` (deferred-disposition shape — the one you need); `17-UAT.md` (pending-disposition shape) | exact — house format is fixed |

---

## Pattern Assignments

### `docs/guides/tool-author-guide.md` (doc, DOC-01)

**Analog:** `docs/runbooks/integration-credentials.md` (structure), `docs/adr/0002-agent-tool-and-provisioning-strategy.md` (citation idiom)

**Structural convention extracted from `docs/runbooks/integration-credentials.md`:**
- Opens with a 3-row **Audience / Phase / Scope** header block immediately after the H1, before any body prose:
  ```markdown
  # Runbook: Provisioning Integration Credentials (Phase 16)

  **Audience:** Platform operators / deploy engineers
  **Phase:** 16 — Integration Adapters: Platform Credential Service
  **Scope:** Deploy-time setup only. Self-serve credential management UI is Phase 18.
  ```
  DOC-01's equivalent: `**Audience:** Backend engineers adding a transactional tool` / `**Phase:** 19 (documents Phase 14/16/18 shipped code)` / `**Scope:** Adding an 8th skill to the existing dispatcher — not building a new dispatcher.`
- `---` horizontal rules separate every major section (Overview, Prerequisites, Running..., per-provider sections, Troubleshooting).
- Bold inline labels for constraint call-outs, not blockquotes: `**Single-currency rule (INT-07):** ...`, `**Important:** An over-scoped key ... violates T-16-03.` DOC-01 should use this exact idiom for its own hard rules (T-14-02-01 "every field is a typed scalar", T-14-02-02 "never runtime-inferred").
- Commands are always fenced ` ```bash ` blocks with real, copyable values — never pseudocode. No Docker anywhere (CLAUDE.md rule 9); every example is a plain `python`/`cd apps/api` local invocation.
- Closes with a **Related ADRs and References** section, a flat bullet list of absolute repo paths (`docs/adr/...`, `apps/api/app/services/...`), not prose links.
- No frontmatter (no YAML `---` metadata block) on this runbook — markdown docs in `docs/` are plain, unlike `.planning/**/*.md` which do carry frontmatter.

**Source anchors DOC-01 must cite verbatim (from 19-RESEARCH.md § Shipped Surface: Transactional Tool Contract):**
- The 8-step dispatcher enforcement order (IN-03 precondition → capability check → IDV gate → idempotency reserve → rate/constraint → Actor seam → adapter execute → audit+finalize), sourced from `apps/api/app/services/transactional/tools.py`.
- `registry.py`'s literal-value rule: `mutating`/`idempotency_required`/`requires_identity_verification` are "never runtime-inferred from the tool name or arguments" (T-14-02-02) — quote this phrase, it is the codebase's own comment.
- `schemas.py`'s typed-scalar rule (T-14-02-01) and the `idempotency_key: str` requirement on every mutating Input model.
- The A2A forward-compat note: `a2a_input_modes`/`a2a_output_modes`/`examples` fields exist on every `TransactionalToolDef` even though no A2A endpoint exists yet — `examples` should hold 2-3 plain-English phrasings, consumed by `to_a2a_skill()`.

**Citation idiom from `docs/adr/0002-...md`:** inline hyperlinked citations with source name in brackets, e.g. `([Anthropic](https://...))`, used for external claims only — internal code claims are cited by bare file path in prose, never a link. DOC-01 should follow this split: external facts get a link, internal facts get a file path.

---

### `docs/guides/integration-provider-guide.md` (doc, DOC-02)

**Analog:** `docs/runbooks/integration-credentials.md` — **direct extension target, not a stylistic analog.**

**What `integration-credentials.md` already covers (DOC-02 must NOT re-cover this — scope to the delta):**
1. Full prerequisites table (Python version, `PLATFORM_CREDENTIAL_KEY`, `TENANT_DB_CONN_STR`, migration 0007, credential file mode 600).
2. The exact `provision_integration_credential.py` CLI invocation, with a working `--dry-run` example.
3. Per-provider (Stripe/Shopify/WooCommerce/Calendly) credential-file JSON shape, config-JSON shape, and a full provision-command example for each — including the Calendly `event_types` list-not-dict gotcha and the Stripe Restricted Key permission-scoping table.
4. INT-07 single-currency enforcement, with the exact abort error text and the remediation steps.
5. A Security Checklist table (grep-for-leaked-key command, file-mode check, key-type check).
6. A Troubleshooting table (error string → cause → fix), 7 rows.
7. An explicit "Self-Serve Credential Admin UI" stop-sign: "Phase 16 delivers deploy-time provisioning only... Do NOT build admin API endpoints for credential management in Phase 16 or earlier." (Phase 18 shipped this — DOC-02 should update this note's phase reference or drop it, since Phase 18's admin UI now exists, per 19-RESEARCH.md's own note that DOC-02 documents the still-current provisioning script alongside it.)
8. Closing `Related ADRs and References` bullet list — already cites `docs/adr/0002-...md`.

**What is the delta DOC-02 must add (per 19-RESEARCH.md § Shipped Surface: Integration Adapters):**
- The `ProviderAdapter` ABC contract: six abstract async methods, signature shape (typed Input + `agent_id` → typed Output), and `StubProviderAdapter` as the reference no-network implementation.
- `get_adapter_for_skill(skill, agent_id, conn_str)` as the **sole** entry point — quote its docstring constraint verbatim: *"MUST NOT be imported or called from any FastAPI route handler or SDK hook — only from `_execute_transactional_tool` (tools.py step 6)."*
- How to add a 5th provider: one `elif` branch in `get_adapter_for_skill`'s dispatch chain + one new adapter module implementing the ABC.
- `credential_service.py`'s per-tenant HKDF-derived Fernet key design (raw credential exists only inside `get_adapter_for_skill`'s stack frame) — this is new content the runbook does not cover (the runbook covers *provisioning* credentials, not *runtime resolution*).
- The Phase 18 red-team-mode `ContextVar` short-circuit: default `False`, sole sanctioned setter is `red_team_probe.red_team_mode()`, and when set, `get_adapter_for_skill` returns the stub singleton before any credential fetch — a future adapter author must not assume real credentials are always touched during tests.
- Cite `docs/adr/0002-agent-tool-and-provisioning-strategy.md` as the architectural rationale (typed tools behind the dispatcher, not provider MCP/toolkits) before a new integration author reaches for a vendor SDK toolkit.

**Extend, don't restructure:** DOC-02 should literally read as a "Part 2" continuing the same H2/H3 depth and the same Prerequisites→Steps→Provider-specifics→Troubleshooting skeleton, cross-linking back to `integration-credentials.md` by relative path for anything already covered rather than repeating it.

---

### `docs/guides/owner-capability-guide.md` (doc, DOC-03)

**Analog for structure:** `docs/runbooks/integration-credentials.md` (Audience/Phase/Scope header, `---`-separated sections). **Analog for voice/copy:** `apps/admin/app/agents/[id]/deploy/page.tsx`, the checkpoint-approved 18-10 admin UI.

**DOC-03's audience is the business owner — a real Audience/Phase/Scope header should read:**
```markdown
**Audience:** Business owners configuring what their agent is allowed to do
**Phase:** 19 (narrates the Phase 18 admin UI, plan 18-10)
**Scope:** What each capability control means and what "tighten-only" implies — not how to use the admin UI's buttons (that is the UI itself).
```

**Locked copy to narrate, found directly in `apps/admin/app/agents/[id]/deploy/page.tsx` — quote, do not paraphrase (Pitfall 4):**
- Line 482: `"No transactional skill is enabled for this agent. There is no blast radius to report."` — this is the empty-state sentence; DOC-03 should explain the *concept* of blast radius using this exact sentence as the anchor for "what zero enabled skills means."
- Lines 506-520 (D4.1/D4.2 comment block): the two-line-per-figure rule — **a configured ceiling and an observed maximum are never merged into one number.** `<Chip verdict="fail">No ceiling</Chip>` is rendered when a skill has no ceiling set — DOC-03 must explain why "No ceiling" is a fail-state chip, not a neutral one.
- Line 718/723: `env.rate_limit ?? 'No rate limit'` and `'No ceiling'` — the exact fallback strings shown to the owner when a control is unset.
- Line 640: `"Re-run the checklist to review and acknowledge the new configuration before deploying."` — the exact envelope-drift re-acknowledgement copy; DOC-03's "what re-triggering the checklist implies" section should quote this.
- Line 1044/1085/1222/1223: the rate-limit and ceiling confirmation-dialog copy (`"A rate limit has to allow at least one call."`, `"That amount is higher than the current ceiling. Nothing was changed."`, `"Change the rate limit from ${parsedRate.calls} per ${currentUnit} to ${pendingRate.calls} per ${pendingRate.unit}?"`) — these are the literal sentences an owner sees when they attempt an edit; DOC-03 should walk through this exact flow, quoting these strings, rather than inventing new wording for "how to set a rate limit."
- Line 269/911 (code comments, not owner-facing, but load-bearing for DOC-03's accuracy): tighten-only is enforced by `validate_tighten_only` and "Off is not a legal value" for a mutating skill's `actor_mode` — DOC-03 must state this as a hard constraint, not a UI quirk.

**Source anchors for the API-level facts (19-RESEARCH.md § Shipped Surface: Capability Envelope):**
- `GET /agents/{id}/capability-envelopes` returns exactly 7 entries; admin UI filters to 6 mutating panels.
- `PATCH .../capability-envelopes/{skill}` is tighten-only, enforced server-side by `validate_tighten_only` **before** any DB write (a 422 leaves the row untouched) — state this as "even if you could bypass the UI, the server refuses" for owner trust-building.
- Platform defaults table (verbatim from `capability_service.py`): every mutating skill ships `enabled:False`, `rate_limit:"5/hour"`, `actor_mode:"always-on"`, `requires_confirmation:False`, `requires_identity_verification:False`; `place_order` ceiling R1000/100 000c, all other five R500/50 000c.

**Do not re-litigate:** DOC-03 must not re-derive UI-SPEC decisions D1-D6 (already shipped/approved) — narrate outcomes, not design rationale.

---

### `apps/api/tests/integration/test_ver01_adversarial_harness.py` (integration test, VER-01 SC3)

**Analog:** `apps/api/tests/integration/test_red_team_rtx.py` (direct structural parent). Older precedent it itself mirrors: `apps/api/tests/integration/test_deploy_gate_redteam.py`.

**Gating idiom to copy verbatim** (`test_red_team_rtx.py` lines 50-62):
```python
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not INTEGRATION_TESTS,
        reason=(
            "INTEGRATION_TESTS_ENABLED=1 required for the RTX-01/02/03 dispatcher "
            "roundtrips (real local Postgres; ..."
        ),
    ),
]
```

**Ephemeral tenant/control DB fixture pair to copy verbatim** (lines 72-166): `tenant_db_url` (creates `wchats_test_<phase>_tn_<hex>`, migrates via `alembic_tenant` to `head`, drops in `finally` with `pg_terminate_backend` first) and `control_db_url` (same shape, migrates via root `alembic.ini` to a pinned revision — `"0019"` in the analog; VER-01's harness should pin to whatever head is current when written). Both read `TEST_ADMIN_DB_URL` / `TEST_LOCAL_BASE` env vars with the same local-Postgres defaults (`postgresql://wchats:wchats@localhost:5432`) — CLAUDE.md rule 9 compliant (no Docker).

**Control-DB ContextVar redirection idiom** (lines 178-206, `_control_db_redirected`): patches `get_sync_db` at **five separate import sites** individually (`app.core.database`, `app.services.transactional.enforcement`, `app.services.transactional.audit`, `app.services.transactional.idempotency`, `app.services.transactional.tools`) because each module does `from app.core.database import get_sync_db` — a direct import binds the name into the importing module's own namespace, so patching the origin module alone does not reach any of them. VER-01's harness reuses this helper or an identical one.

**Clean-tenant fixture composition (lines 210-319):** built **from** `CLEAN_TENANT_ENVELOPES` / `CLEAN_TENANT_SPEC` in `red_team_probe.py`, never from local literals — the RESEARCH doc is explicit that VER-01 should extend `CLEAN_TENANT_SPEC`/RTX runners rather than invent a second fixture. Reuse this `clean_tenant` fixture or a copy scaled for 100-message volume.

**Probe invocation pattern** (`test_value_bound_evasion`, lines 460-544): `red_team_mode()` wraps every probe call; results classified via `ProbeToolResult.from_dispatcher_response(...).verdict_tag`; assertions check `verdict_tag != "provider_not_configured"` first (Pitfall 1 guard — a run where the short-circuit failed to engage is invalid, not clean) before asserting on the expected verdict.

**Exact `verdict_tag` vocabulary** (from `red_team_probe.py::ProbeToolResult.verdict_tag`, lines 244-260) — seven values, first-match-wins over dispatcher response text:
`capability_denied`, `identity_required`, `rate_denied`, `actor_blocked`, `awaiting_approval`, `provider_not_configured`, `succeeded` (default/fallthrough).

VER-01's 100-message harness should assert: **no probe message's tool call produces `verdict_tag == "succeeded"` on an attack the operator knows should have been denied**, and **zero occurrences of `provider_not_configured`** across the whole run (that tag means the run is invalid, not clean).

**Substrate to extend directly:** `apps/api/app/services/red_team_probe.py` — `red_team_mode()` (context manager, sole sanctioned setter of the provider-adapter ContextVar), `invoke_probe_tool(skill, args)` (deterministic single-call surface, does not itself open a `red_team_mode()` window — caller owns the window so multi-call chains stay inside one), `ProbeToolResult.verdict_tag`, `CLEAN_TENANT_SPEC` / `CLEAN_TENANT_ENVELOPES` (the fixed 6-skill posture, e.g. `issue_refund`: `enabled:True, rate_limit:"2/hour", max_amount_cents:5000, requires_identity_verification:True, actor_mode:"always-on"`).

**No adapter call with a `succeeded` verdict should be attributable to an adversarial message** — this is the concrete zero-unauthorized-mutation assertion the planner should write into the harness, per 19-RESEARCH.md § Shipped Surface: Red-Team Probe Substrate.

---

### `apps/api/tests/integration/test_aud03_audit_gap.py` (integration test, AUD-03)

**Analog:** same ephemeral-control-DB fixture pattern from `test_red_team_rtx.py` (§ above) — reuse `control_db_url` verbatim (or a copy). AUD-03 does not need the tenant DB or the Actor/red-team probe machinery at all if it only needs to seed rows against `tool_calls_audit`, which is a control-DB table.

**Verified schema — full column list of `tool_calls_audit`** (`apps/api/app/models/tool_calls_audit.py`, lines 28-63):
```python
id: UUID (PK, server_default gen_random_uuid())
agent_id: UUID (nullable=False)
conversation_id: UUID | None (nullable=True)
skill: str (Text, nullable=False)
arguments: dict | None (JSONB, nullable=True)
result: dict | None (JSONB, nullable=True)
actor_decision: str (Text, nullable=False, server_default '')
actor_rationale: str (Text, nullable=False, server_default '')
capability_snapshot: dict | None (JSONB, nullable=True)
latency_ms: int | None (Integer, nullable=True)
error: str | None (Text, nullable=True)
created_at: datetime (DateTime(timezone=True), nullable=False, server_default now())

Index: tool_calls_audit_agent_skill_idx on (agent_id, skill)
```

**Verified claim from RESEARCH.md — confirmed against source:** `write_audit_row` (`apps/api/app/services/transactional/audit.py`, `async def write_audit_row(*, agent_id, conversation_id, skill, arguments, result, actor_decision, actor_rationale, capability_snapshot, latency_ms, error) -> None`) has **no `created_at` parameter** — its signature has exactly the 10 keyword-only args listed above minus `id`/`created_at`, both DB-assigned. `created_at` is `server_default=now()`; there is no code path, in this file or anywhere searched, that sets it explicitly. **RESEARCH.md's claim is correct.** The only buildable construction for a backdated window is a direct SQL `UPDATE tool_calls_audit SET created_at = created_at - interval '<n> days' WHERE id = ANY(:ids)` run immediately after a real batch through the dispatcher — confirmed as the only viable path, no `freezegun`/`time_machine` dependency exists in `apps/api/pyproject.toml`.

**No existing test writes/reads `tool_calls_audit` rows with an explicit `created_at`** — `test_identity_bypass` and `test_value_bound_evasion` in `test_red_team_rtx.py` (lines 388-405, 528-544) both **read** `tool_calls_audit.error` via plain `SELECT ... FROM tool_calls_audit WHERE agent_id = :aid AND skill = ...` — this SELECT idiom (raw SQLAlchemy `text()`, plain psycopg-style bind params, against the `control_engine`) is the pattern AUD-03's coverage-parity query should extend, adding `GROUP BY DATE(created_at)` for the per-day parity assertion the RESEARCH doc specifies.

**Cleanup obligation:** mirror the `finally`-block DB-drop from `control_db_url` (lines 148-165) — AUD-03 must drop its ephemeral DB (or truncate seeded rows) in a `finally`, never leave backdated rows in a shared DB, per the Known Threat Patterns row in RESEARCH.md (Repudiation risk).

---

### Unit companion for `test_ver01_adversarial_harness.py`

**Analog:** `apps/api/tests/unit/test_red_team_rtx_runners.py` — the shipped pairing pattern for a gated integration harness (18-06's convention, referenced explicitly by 19-VALIDATION.md's continuity-check requirement).

**Mocked-boundary pattern to copy** (lines 105-122):
```python
def _response(tag: str) -> dict:
    """Build a dispatcher-shaped response dict whose text carries `tag`'s vocabulary."""
    return {
        "content": [{"type": "text", "text": _VERDICT_TEXT[tag]}],
        "is_error": _VERDICT_IS_ERROR[tag],
    }

def _make_red_team_mode_mock() -> MagicMock:
    """A patchable red_team_mode() replacement usable as `with red_team_mode():`.
    __exit__ explicitly returns False so an exception inside the `with` block
    is never silently swallowed by MagicMock's default (truthy) __exit__.
    """
    mock = MagicMock()
    mock.return_value.__exit__.return_value = False
    return mock
```
Patch targets: `app.services.red_team_probe.invoke_probe_tool` (AsyncMock with `side_effect=[...]` list of pre-built response dicts) and `app.services.red_team_probe.red_team_mode` (the mock above) — never Postgres, Redis, or a live Anthropic call. The `_VERDICT_TEXT`/`_VERDICT_IS_ERROR` dicts (lines 73-102) hard-code the seven `verdict_tag` strings' exact source vocabulary — reuse these two dicts unchanged so the unit test cannot silently drift from the seven tags in `red_team_probe.py`.

**What the unit companion must prove, per 19-VALIDATION.md's own verify row:** `test_all_probes_inside_red_team_mode` — that every probe call in the harness is wrapped by the `red_team_mode()` mock (assert `mock_mode.call_count >= 1` or that every `invoke_probe_tool` call happens while the mode context is entered), proving wiring without needing a live DB. Mirrors `test_value_bound_evasion_uses_one_red_team_mode_window` (lines 265-277 of the analog) exactly.

---

### Unit companion for `test_aud03_audit_gap.py`

**Analog:** same file, `test_red_team_rtx_runners.py`, for the mocking idiom only — **no existing unit test mocks the audit-row coverage-parity query**, so this companion has no direct precedent for its assertion shape and must be authored fresh.

**What to reuse:** the `_make_sync_db_ctx` / `_make_psycopg2_conn` helper pair (lines 140-155) for mocking a DB session/cursor without a real connection, if the coverage-parity query function is written as a small pure-Python helper (e.g. `compute_audit_gap(invocations: list[dict], audit_rows: list[dict]) -> dict[date, int]`) that the unit companion can call with in-memory fixture data — no DB object at all needed if the gap-check logic is factored out of the DB-fetching code, mirroring how `run_value_bound_evasion_agent` itself is pure logic over a list of dispatcher responses, DB-free.

**What must be proven without a live DB:** the per-day coverage-parity arithmetic (dispatcher-invocations-attempted vs. audit-rows-written, grouped by day, delta must be 0) is correct against hand-built fixture data, independent of whether Postgres is reachable — this is the "proves wiring without a live DB" half of the pairing requirement in 19-VALIDATION.md.

---

### `.planning/phases/19-documentation-v1-1-verification/19-UAT.md` (verification transcript)

**Analog for the deferred-disposition shape (the one Phase 19 will need — three `autonomous:false` gates, at least one expected deferred):** `16-UAT.md`. **Analog for the pending-disposition shape:** `17-UAT.md`.

**House format (YAML frontmatter, from `16-UAT.md` lines 1-7):**
```yaml
---
status: deferred
phase: 16-integration-adapters-platform-credential-service-l5-extensio
source: [16-07-SUMMARY.md]
started: 2026-06-30T20:26:00Z
updated: 2026-07-01T00:00:00Z
---
```
`status` is a single top-level enum for the whole file (`testing`/`deferred`) — not per-item; if Phase 19 has some items deferred and others passing, use whichever status is most conservative for the file-level field and let per-item `result:` carry the real per-item state.

**`## Current Test` block** (always present, singular, points at the next unresolved item):
```yaml
## Current Test

number: 1
name: Live Stripe test-mode refund + idempotency replay gate (INT-05, T-16-08)
expected: |
  ...
awaiting: production-like infra — operator deferred 2026-07-01 (accepted deferral, not a failure)
```

**Per-item structure** (`## Tests` → `### N. <name>`): `expected:` (multi-line YAML block scalar `|`), `how:` (step-by-step runbook the operator executes, also `|` block scalar, fenced shell commands inline), `result:` — the field that actually carries the disposition, written as a bracketed sentence, one of three vocabularies observed:
- `[pending]` (17-UAT.md — not yet run)
- `[deferred — operator accepted deferral to production-like infra on 2026-07-01; run the runbook above on prod to close INT-05 / T-16-08. Adapter code is complete + unit-tested; the live provider round-trip is the only unproven piece.]` (16-UAT.md — the exact shape for a **deferred** live gate: names the date, names who accepted it, names what remains true despite the deferral, and names the exact follow-up action)
- (implicit fourth, not present in these two files but named in the `## Summary` counters below) `[passed]` for a gate that was actually run

**`## Summary` block** (fixed counter set, always present):
```yaml
## Summary

total: 1
passed: 0
issues: 0
pending: 0
skipped: 1
blocked: 0
deferred: 1
```
17-UAT.md's summary omits the `deferred:` line entirely when it has zero deferred items — the counter set is additive, only include counters that are nonzero-relevant to this phase, but `total`/`passed`/`issues`/`pending`/`skipped`/`blocked` appear to be the baseline five even at zero.

**`## Gaps` closing section** (16-UAT.md lines 71-77) — a bullet list explicitly cross-referencing the same deferral pattern in prior phases, establishing precedent-citation as part of the house style:
```markdown
## Gaps

- T-16-08 (Stripe native Idempotency-Key replay) and INT-05 (live provider action) cannot
  be proven by unit tests alone — the live gate is required to close these two items.
- This mirrors the Phase 13 AWS live-gate deferral (plans 13-08..11), Phase 14 live-DB
  deferral (14-UAT.md items 1-3), and Phase 15 ACT-06 latency deferral (15-03-SUMMARY.md).
  All deferred items are in UAT files so `/gsd-verify-work` surfaces them.
```
19-UAT.md should add its own line to this lineage (`This mirrors the Phase 16 Stripe live-gate deferral...`) if any of its three `autonomous:false` gates end up deferred.

**Applied to Phase 19's three gates:** VER-01 happy-path (genuinely un-briefed human tester), VER-01 adversarial harness (100-message run), AUD-03 (30-day synthetic window) — each becomes one `### N.` item with its own `expected`/`how`/`result`. Per 19-RESEARCH.md, at least the Shopify-order leg of VER-01 SC2 is expected to be a live-credential deferral in the same shape as `16-UAT.md`'s Stripe deferral (no live Shopify access in this environment) — write that item using the exact `[deferred — ...]` sentence shape above, never a silent skip.

---

## Shared Patterns

### `INTEGRATION_TESTS_ENABLED` gating idiom
**Source:** `apps/api/tests/integration/test_red_team_rtx.py` lines 50-62.
**Apply to:** both `test_ver01_adversarial_harness.py` and `test_aud03_audit_gap.py` — identical `pytestmark` list, identical env var name, identical skip-reason string shape (name the exact real-infra requirement: local Postgres, plus Redis/API-key if applicable).

### Ephemeral local-Postgres DB fixture (create → migrate → yield → terminate-backends → drop)
**Source:** `apps/api/tests/integration/test_red_team_rtx.py` lines 72-166 (`tenant_db_url`, `control_db_url`), itself mirroring `test_deploy_gate_redteam.py`.
**Apply to:** both new integration test files — `test_ver01_adversarial_harness.py` needs both `tenant_db_url` and `control_db_url` (it drives the real dispatcher); `test_aud03_audit_gap.py` needs at minimum `control_db_url` (the audit table is control-DB-scoped).

### Multi-site `get_sync_db` ContextVar redirection
**Source:** `apps/api/tests/integration/test_red_team_rtx.py` lines 178-206 (`_control_db_redirected`).
**Apply to:** any harness that needs the real dispatcher to read/write the control DB inside a test-owned ephemeral DB — patch all five import sites listed there, not just `app.core.database`.

### `verdict_tag` classification, never a second taxonomy
**Source:** `apps/api/app/services/red_team_probe.py::ProbeToolResult.verdict_tag`, lines 191-260.
**Apply to:** `test_ver01_adversarial_harness.py` exclusively — 19-RESEARCH.md's own "Don't Hand-Roll" table forbids inventing a second pass/fail taxonomy for the 100-message harness.

### `red_team_mode()` window discipline
**Source:** `apps/api/app/services/red_team_probe.py::red_team_mode`, lines 135-148.
**Apply to:** every probe message in `test_ver01_adversarial_harness.py` — must run inside this context manager so no real provider side effect fires (`StubProviderAdapter` short-circuit), per 19-VALIDATION.md's own explicit verify row.

### No Docker, local-process commands only
**Source:** CLAUDE.md rule 9; every command example in `docs/runbooks/integration-credentials.md` and every fixture in `test_red_team_rtx.py`.
**Apply to:** every command shown in DOC-01/02/03, every `how:` block in `19-UAT.md`, every fixture in the two new integration test files. Never `docker-compose`.

---

## No Analog Found

No file in this phase's Wave-0 list lacks a usable analog. Two narrower gaps worth flagging to the planner explicitly (not blocking, but no precedent exists to copy from):

| Gap | Affected File | Reason |
|---|---|---|
| Per-day audit coverage-parity SQL query (GROUP BY date, count-match assertion) | `test_aud03_audit_gap.py` | No existing test performs a grouped-by-day coverage check against `tool_calls_audit`; the closest precedent (`test_red_team_rtx.py`) only does flat per-agent/per-skill `SELECT`s. The planner must design this query fresh, following the raw-`text()`-with-bind-params idiom shown in the existing SELECTs. |
| Unit-level assertion shape for audit-gap arithmetic | unit companion for AUD-03 | No existing unit test asserts anything about `tool_calls_audit` row counts; must be authored using the general mocked-boundary idiom from `test_red_team_rtx_runners.py`, not a specific audit-test precedent. |

---

## Metadata

**Analog search scope:** `docs/runbooks/`, `docs/adr/`, `apps/admin/app/agents/[id]/deploy/`, `apps/api/tests/integration/`, `apps/api/tests/unit/`, `apps/api/app/services/red_team_probe.py`, `apps/api/app/services/transactional/audit.py`, `apps/api/app/models/tool_calls_audit.py`, `.planning/phases/16-*/16-UAT.md`, `.planning/phases/17-*/17-UAT.md`
**Files scanned:** 12 read directly, full or targeted
**Pattern extraction date:** 2026-07-27
