---
phase: 22
slug: owner-capability-control-pending-confirmation-resolution-clo
status: secured
# threats_open = count of OPEN threats at or above workflow.security_block_on severity (the blocking gate)
threats_open: 0
asvs_level: 1
created: 2026-08-01
---

# Phase 22 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

**Audited:** 2026-08-01
**Auditor:** gsd-security-auditor (Sonnet) + orchestrator L1 pass and claim spot-checks
**block_on:** high
**Threats:** 34 total — 34 closed, 0 open at or above `high`

This phase shipped a **human-approved bypass seam into the mutating-tool dispatcher**. The live-turn
dispatcher `_execute_transactional_tool` runs eight steps; the resolver re-runs steps 2 (capability),
3 (idempotency), 4 (rate + constraints), 6 and 7 (adapter execute + audit), and **deliberately skips
step 2.5 (identity verification) and step 5 (the Actor gate)**. It then makes a real, irreversible
third-party provider call outside any customer session. The whole register below exists to answer one
question: *can a caller reach a provider adapter through this seam with authority the owner did not
grant, or after the owner revoked it?* The answer at code level is no — every skipped check is
compensated by a live-envelope re-read, and the skips are enforced by source-absence tests rather
than by comment. The answer at **live-database** level is unproven; see § Live-Proof Deficit.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| owner (admin UI or raw API) → capability comparator | An authenticated tenant proposes a change to what their agent may do with money. `validate_tighten_only` is the only control; the UI is a convenience, never the enforcement. | Envelope fields (`enabled`, rate limit, ceiling, two safety booleans, actor mode) |
| platform defaults → per-agent envelope | The platform default is the ceiling a first write is compared against. A change to that ceiling changes every agent's reachable authority at once. | `PLATFORM_CAPABILITY_DEFAULTS` entries |
| stored confirmation row → executing resolver | The row's `arguments` were written hours earlier by a live agent turn. They are untrusted input, re-validated through the typed Input model before anything acts on them. | Customer email, shipping address, amounts, idempotency key |
| live capability envelope → resolver | The owner may have tightened or disabled the envelope after the confirmation was created. The live row, never the stored snapshot, is the authority. | Envelope fields at resolution time |
| resolver → provider adapter | A real, irreversible money-moving call to a third party, made outside any customer session. | Provider credential (redacted `CredentialHandle`), action payload |
| authenticated tenant → resolve route | An approver asserts a terminal decision about a money-moving action. The route is the only place that decision is admitted. | `resolution` literal only — no action payload |
| resolve route → Celery broker | A dispatched task will make a real provider call with no further human in the loop. | Confirmation id only (CLAUDE.md rule 4) |
| stored audit history → queue response | The queue reports what happened. A wrong match reports an execution that never occurred, or hides one that did. | `tool_calls_audit` error + timestamp |
| queue response → rendered verdict | A colour on this bench is a claim about whether an agent can be trusted with a customer. | `execution_outcome` verdict |
| row arguments → rendered text | A row's arguments carry customer PII and amounts, displayed on an operator's screen. | Customer email, shipping address, amounts |
| guide text → non-technical owner's actions | This guide is the only artifact the VER-01 SC2 tester is handed. An inaccurate sentence becomes a wrong action against a live money-moving agent. | Behavioural claims about enforcement |
| gated integration module → databases | A module misconfigured to point at the configured control database would execute adapter calls against live production data. | Ephemeral DB only; production setting never imported |
| operator's run → the project record | A disposition written into a UAT file becomes the evidence a milestone is closed on. An optimistic record is indistinguishable from a real pass to every later reader. | UAT dispositions, requirement ticks |

---

## Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation | Status |
|-----------|----------|-----------|----------|-------------|------------|--------|
| T-22-CAP-01 | Elevation of Privilege | `validate_tighten_only`'s five non-`enabled` branches | critical | mitigate | `capability_service.py:304-317` `enabled` branch is a named bare `pass`; the five sibling branches (`:319-387`) intact with full rejection logic. Diff-scope gate returned zero sibling-token hits; ceiling guard demonstrated red-then-green (commits `618d705`, `38d5d4f`) | closed |
| T-22-CAP-02 | Elevation of Privilege | first-write baseline in `patch_capability_envelope` | high | mitigate | `test_capability_routes.py:527-568` — `test_patch_first_write_enable_creates_row_at_platform_defaults` and `test_enable_plus_illegal_other_field_still_rejected` present and correct | closed |
| T-22-CAP-03 | Elevation of Privilege | staged-confirm on the enable checkbox | low | accept | **AR-3.** Staging is UX and bypassable by any caller that never loads the screen. The server comparator is the actual control, asserted independently at the route through `ASGITransport`. Rationale at `deploy/page.tsx:1427-1431` | closed |
| T-22-CAP-04 | Elevation of Privilege | `PLATFORM_CAPABILITY_DEFAULTS` | critical | mitigate | `capability_service.py:98,134` — all seven entries still ship `enabled: False`; `test_platform_defaults_still_ship_every_skill_disabled` pins the fail-closed posture. See Finding 2 re: an imprecise supporting comment | closed |
| T-22-CAP-05 | Tampering | direct API call bypassing the admin UI | medium | mitigate | `capability_service.py:239-243` — `validate_tighten_only(current, proposed, platform_defaults=None)` takes no `Request`, session, or auth object (T-18-CAP-02 unchanged); every test asserts at the route | closed |
| T-22-ACT-01 | Elevation of Privilege | a rejected or expired confirmation reaching an adapter | high | mitigate | **Added retroactively — see Finding 4.** `pending_confirmations.py:303` dispatch conjunct `claimed["resolution"] == "approved"`; demonstrated red-then-green by `test_reject_never_enqueues` + `test_expired_row_is_forced_to_expired_and_never_enqueues` | closed |
| T-22-ACT-02 | Elevation of Privilege | resolving an expired row | high | mitigate | `pending_confirmations.py:280` — `CASE WHEN expires_at IS NOT NULL AND expires_at < now() THEN 'expired'` inside the same atomic statement as the claim; strict `<`, non-null conjunct both asserted | closed |
| T-22-ACT-03 | Tampering | concurrent or repeated resolve | high | mitigate | `pending_confirmations.py:275-291` — single `UPDATE ... WHERE resolved_at IS NULL ... RETURNING`; 409 on no-row (`:293-297`); guard-removal demonstrated. **Rests on real-Postgres atomicity — see § Live-Proof Deficit** | closed |
| T-22-ACT-04 | Elevation of Privilege | `execute_approved_confirmation` steps 2 and 4 | high | mitigate | `confirmation_resolution.py:226` `check_capability_access`, `:304` `apply_rate_and_constraint_checks` — both read the live `capability_envelopes` row; `:223` comment and `TestLiveEnvelope` confirm `capability_snapshot` is never the authority | closed |
| T-22-ACT-05 | Denial of Service (of the feature) | resolver re-entering the Actor seam | high | mitigate | Zero references to `call_actor_gate` / `check_verified_session` / `identity_service` / `agent_tools` / `build_tool_server` / any of the four dispatcher ContextVars in `confirmation_resolution.py`; `test_confirmation_resolution.py:190` asserts the absence; demonstrated red-then-green | closed |
| T-22-ACT-06 | Repudiation | audit symmetry at a new call site | high | mitigate | Every terminal branch writes exactly one `write_audit_row`; `replay`/`in_progress` (`confirmation_resolution.py:259-275`) write none — symmetric with the live dispatcher's own branches (`tools.py:452-460,493-507`) rather than normalising the asymmetry | closed |
| T-22-ACT-07 | Elevation of Privilege | both new routes | high | mitigate | `pending_confirmations.py:75-82` `_get_owned_agent`, first statement of both route bodies (`:186`, `:273`); identical 404 on missing and foreign-agent branches, no existence leak | closed |
| T-22-ACT-08 | Spoofing | identity verification not re-checked at resolution | medium | accept | **AR-1.** OD-1. No customer session token exists outside a live agent turn. Bounded by step 2.5 preceding step 5 (every `require_human` row was created by a call holding a valid session) and `_CONFIRM_TTL_HOURS = 24`. Enforced as a source-absence assertion so the skip cannot silently become a synthetic session | closed |
| T-22-ACT-09 | Repudiation | dispatch fails after a durable claim | medium | accept | **AR-2.** OD-6 commits the claim before dispatch (`pending_confirmations.py:300` before `:306`), overturning `22-PATTERNS.md`. Window is one failed dispatch call; the row is left visibly `approved` with `execution_outcome` null, which the UI renders honestly as awaiting execution rather than as success | closed |
| T-22-ACT-10 | Tampering | an approver altering the action at resolution | high | mitigate | `schemas/pending_confirmation.py:38-40` — `extra="forbid"`, `resolution: Literal["approved","rejected"]`, no action payload of any kind; executed arguments are read from the claimed row inside the task, never from the request | closed |
| T-22-ACT-11 | Information Disclosure | execution-outcome lookup | medium | mitigate | `pending_confirmations.py:125-136` — four predicates: ownership-guarded agent id, skill, `arguments->>'idempotency_key'`, and `actor_decision = 'approved_by_human'`. Without the fourth the lookup matches the ORIGINAL `require_human` row; guard-removal demonstrated | closed |
| T-22-ACT-12 | Information Disclosure | route logging | medium | mitigate | Both routes' `log.info` calls (`:217-222`, `:309-316`) carry ids, counts and flags only — never the request body or a row's `arguments` | closed |
| T-22-ACT-13 | Tampering | duplicate execution under Celery redelivery | high | mitigate | `confirmation_resolution.py:256-257` — fresh `reserve_idempotency` keyed on the row's stored `idempotency_key`; a redelivery finds `replay`/`in_progress` and returns with no adapter call. CLAUDE.md rule 5's idempotency half | closed |
| T-22-ACT-14 | Information Disclosure | resolver logging | medium | mitigate | Every `log.*` in `confirmation_resolution.py` carries only `agent_id`/`skill`/`outcome`; no `conn_str`, `arguments`, or credential. The raw credential stays inside `get_adapter_for_skill`'s frame | closed |
| T-22-ACT-15 | Tampering | drift between two copies of adapter-call and audit-write logic | medium | mitigate | `tools.py:109` `_execute_adapter_and_audit` extracted once, called by both `_execute_transactional_tool` (`tools.py:633`) and `execute_approved_confirmation` (`confirmation_resolution.py:339`) | closed |
| T-22-ACT-16 | Elevation of Privilege | a non-mutating confirmation row reaching an adapter | medium | mitigate | Two independent layers: `pending_confirmations.py:303` gates dispatch on `SKILL_INPUT_MODELS` membership; `confirmation_resolution.py:151-170` independently refuses an unsupported skill with one audit row and no adapter call | closed |
| T-22-ACT-17 | Spoofing (of a system verdict) | queue status chips | high | mitigate | `deploy/page.tsx:601-613` `confirmationChip` — `pass` only on `execution_outcome === 'executed'`, `fail` only on `'not_executed'`, every other state neutral `mute` with "Awaiting execution." Prohibition P2's mechanical form | closed |
| T-22-ACT-18 | Information Disclosure | rendered row arguments | medium | mitigate | Traced end-to-end: `row.arguments` has exactly one consumption site (`deploy/page.tsx:1758`); the six named headline templates access only business fields; `genericArgDetails` (`:535-545`) filters `HIDDEN_ARG_KEY` before render. **Functionally closed but with no automated regression gate — see Finding 1** | closed |
| T-22-ACT-19 | Tampering | double-submit on Approve or Reject | medium | mitigate | `deploy/page.tsx:2135,2797` per-row keyed `savingConfirmations` (not a shared flag); the server's atomic claim is the real control; 409 rendered as the benign expected outcome so an operator is not trained to retry through a race | closed |
| T-22-ACT-20 | Denial of Service (of comprehension) | unresolved rows past their deadline | low | mitigate | `deploy/page.tsx:1759-1764` client-side `clientExpired` disables both actions and renders the neutral expired chip ahead of any server sweep, given OD-2 ships none | closed |
| T-22-ACT-21 | Tampering | the gated integration module | high | mitigate | `test_act07_resolve_live.py` — zero `app.core.config`/`settings` imports; `INTEGRATION_TESTS_ENABLED` module-level skip; ephemeral DB created and dropped in a `finally`; adapter reached through `red_team_mode()`'s zero-credential stub short-circuit | closed |
| T-22-DOC-01 | Spoofing (of the security posture) | the owner guide | high | mitigate | Every behavioural claim anchored to shipped source; twelve quoted copy strings checked present in BOTH the guide and the deploy page; three stale claims asserted absent | closed |
| T-22-DOC-02 | Spoofing | the guide's identity-verification narration | high | mitigate | `owner-capability-guide.md:301` — "approving a request authorises the action. It does not verify the customer's identity." OD-1's residual risk is stated to the reader rather than hidden | closed |
| T-22-DOC-03 | Tampering | reader-copied commands | medium | mitigate | Zero matches for container-runtime (CLAUDE.md rule 9), raw HTTP client, or SQL statement anywhere in the guide — the stronger requirement, since VER-01 SC2's criterion is that a non-technical tester needs none of them | closed |
| T-22-VER-01 | Spoofing (of a verification result) | the UAT record and the requirement ticks | high | mitigate | The plan's own `UAT-RECORD-OK` gate (`22-06-PLAN.md:382`) re-executed by the auditor against the current `22-UAT.md` and passed; the live gate's record must carry five numeric figures rather than a verdict word | closed |
| T-22-VER-02 | Tampering | the live gate's target database | high | mitigate | Ephemeral DB from `TEST_ADMIN_DB_URL`/`TEST_LOCAL_BASE`; no production control-DB setting imported; `22-UAT.md` item 2 states plainly that production was never targeted | closed |
| T-22-VER-03 | Repudiation | an un-run gate recorded as skipped | medium | mitigate | Every `22-UAT.md` item carries a dated disposition naming its cause and closing condition; the automated check fails when any item lacks one. A silent skip is the one outcome the record cannot express | closed |
| T-22-VER-04 | Spoofing | a substituted tester | medium | mitigate | `22-UAT.md` item 1's deferral names "no genuinely un-briefed non-technical tester was available" as one of two independent causes — never the operator standing in | closed |
| T-22-SC | Tampering (supply chain) | npm/pip/cargo installs | high | mitigate | `git log -1 -- apps/api/pyproject.toml` → `2dd0493` (2026-07-18), pre-dating the phase; `apps/api/alembic/` byte-unchanged; zero packages proposed in `22-RESEARCH.md § Package Legitimacy Audit`; no `[ASSUMED]`/`[SUS]` entry | closed |

*Status: open · closed · open — below high threshold (non-blocking)*
*Severity: critical > high > medium > low — only open threats at or above `workflow.security_block_on` count toward `threats_open`*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-1 | T-22-ACT-08 | **Identity verification is not re-checked at resolution.** No customer session token exists outside a live agent turn — the dispatcher sources it from a ContextVar populated per turn by `build_tool_server()`, so a resolver running outside that lifecycle cannot re-check IDV at all, and inventing a synthetic session would be a genuine IDV-05 regression. Bounded by two source-anchored facts: step 2.5 precedes step 5, so every `require_human` row was created by a call that held a valid session; and `_CONFIRM_TTL_HOURS = 24` bounds the staleness window. Enforced by a source-absence test so the skip cannot silently become a synthetic session. | Owner (OD-1, `22-01-PLAN.md § Open Decisions Resolved`) | 2026-07-28 |
| AR-2 | T-22-ACT-09 | **A dispatch failing after a durable claim leaves a resolved row whose task never ran.** OD-6 commits the claim before enqueueing, overturning `22-PATTERNS.md`'s shown ordering — the reverse order is strictly worse, since a task dispatched before a durable claim can be picked up by a worker reading the pre-claim row. The residual window is one failed `.delay()` call. Accepted rather than closed with a two-phase commit or outbox pattern; the row stays visibly `approved` with a null `execution_outcome`, which the queue renders honestly as awaiting execution rather than as success. | Owner (OD-6, planner-surfaced) | 2026-07-28 |
| AR-3 | T-22-CAP-03 | **The staged-confirm step on the enable checkbox is UX, not enforcement**, and is bypassable by any caller that never loads the screen. This is correct and deliberate: `validate_tighten_only` takes no FastAPI, session, or auth object, and every capability test asserts at the route through `ASGITransport`, so a direct API call is judged identically. Accepting this is what keeps the UI from being mistaken for the enforcement layer. | Owner (`22-04-PLAN.md` threat model) | 2026-07-28 |

---

## Live-Proof Deficit

Recorded explicitly rather than folded into the closures above, because "closed" here means *closed at
the level the evidence reaches* and that level differs per threat.

No PostgreSQL server exists on this machine (the `postgresql-x64-17` Windows service is a stale
registration pointing at a deleted `pg_ctl.exe`). `test_act07_resolve_live.py` — the only test capable
of proving real-database behaviour — has **never executed**. Consequently:

- **T-22-ACT-03's exactly-once guarantee** rests on the correctness of the SQL idiom
  (`UPDATE ... WHERE resolved_at IS NULL ... RETURNING` under READ COMMITTED), which is textbook-correct,
  plus a mocked-boundary test that asserts on SQL *text* rather than on observed row contention. No two
  concurrent resolvers have ever raced against a real database here.
- **T-22-ACT-02, T-22-ACT-11, T-22-ACT-13, T-22-CAP-02 and T-22-ACT-06** are proven only against mocked
  `db.execute()` boundaries.
- **T-22-ACT-21** describes a module that has never run at all; its isolation is proven by source
  absence (no `settings` import), not by observation.

This matches `22-VERIFICATION.md`'s own `human_needed` disposition (SC2 `PRESENT_BEHAVIOR_UNVERIFIED`)
and is not a code defect. It closes with one action: install a local PostgreSQL server, then run
`INTEGRATION_TESTS_ENABLED=1 pytest tests/integration/test_act07_resolve_live.py -m integration -q -s`.

---

## Findings (non-blocking, carried forward)

**Finding 1 — T-22-ACT-18's specified automated gate cannot pass against a spec-compliant file.**
The register says the idempotency key's "absence from the file" is gated. The plan's literal gate
(`22-04-PLAN.md:300`, `grep -qF 'idempotency_key' "$F" && exit 1`) was re-executed against the shipped
file and **matches** — because the file legitimately contains `const HIDDEN_ARG_KEY = 'idempotency_key'`
plus a comment naming it (`deploy/page.tsx:280-290`), with an argument that obfuscating the constant to
satisfy a naive scanner would be worse than keeping it plain. That reasoning is sound and
`22-04-SUMMARY.md:166-178` already documents it self-aware. The mitigation is functionally correct
(traced end-to-end). **But no replacement gate was created**, and no render-level test exists for
`deploy/page.tsx`. Net effect: the threat is closed today with **zero automated regression protection** —
a future debug JSON dump of `row.arguments` would not be caught mechanically. *Recommended follow-up:
a render-level assertion that the rendered output contains no idempotency key, replacing the retired
source-grep.*

**Finding 2 — T-22-CAP-04's supporting comment is imprecise.** `capability_service.py:311` states "no
code path in `apps/api/app/` ever set `enabled=True`". That is false as literally written:
`red_team_probe.py:409,418,427,436,445,454` sets `"enabled": True` in `CLEAN_TENANT_ENVELOPES`. Every
consumer was traced — test/integration fixtures seeding ephemeral databases, and two deterministic
red-team runners that only *read* numeric ceilings from it inside a `red_team_mode()` window that
short-circuits `get_adapter_for_skill` to a zero-credential stub. No write against `capability_envelopes`
using this constant exists. **Benign, correctly isolated red-team fixture — not a live provisioning
path.** The comment should read "…for a real tenant." One-line doc fix, not a security gap.

**Finding 3 — the live-proof deficit**, recorded in full in the section above.

**Finding 4 — T-22-ACT-01 was an orphaned threat ID.** It appears in `22-VALIDATION.md`'s verification
map and guard-removal inventory but in **no plan's STRIDE table**. Its concern ("a rejected or expired
confirmation must never reach the adapter") is genuinely distinct from T-22-ACT-16 (non-mutating skill
reaching an adapter) and T-22-ACT-10 (approver altering the action) — it is the flip side of
T-22-ACT-02's guard, both enforced by the single conjunct at `pending_confirmations.py:303`. It was
empirically tested and demonstrated red-then-green, but never registered. **Added to the register above
as a formal row** rather than left a validation-map footnote.

**Housekeeping (outside this phase's register):** a stray `SECURITY.md` containing Phase-15 content sits
at the repo root, untracked and never committed. The canonical Phase 15 file is
`.planning/phases/15-.../15-SECURITY.md`. The root copy should be deleted; it is a misplaced duplicate,
not a stale contract.

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-08-01 | 34 | 34 | 0 | gsd-security-auditor (Sonnet) + orchestrator L1 pass and claim spot-checks |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log (AR-1, AR-2, AR-3)
- [x] `threats_open: 0` confirmed
- [x] `status: secured` set in frontmatter

**Approval:** verified 2026-08-01 — code-level only. The live-database gate remains deferred and is
recorded as such in § Live-Proof Deficit; nothing in this file asserts a behaviour that was observed
against a real database.
