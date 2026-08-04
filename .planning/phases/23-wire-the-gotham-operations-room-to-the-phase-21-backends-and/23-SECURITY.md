---
phase: 23
slug: wire-the-gotham-operations-room-to-the-phase-21-backends-and
status: draft
# threats_open = count of OPEN threats at or above workflow.security_block_on severity (the blocking gate)
threats_open: 0
asvs_level: 1
created: 2026-08-04
---

# Phase 23 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

**Register origin:** authored at plan time. All nine PLAN.md files carry a `<threat_model>` block,
so this audit ran in **verify-mitigations** mode — confirming each stated mitigation exists in the
implementation — not in retroactive-STRIDE mode.

**What this phase actually changed.** Phase 23 wired the six-region Gotham operations room to
backends Phase 21 had already shipped, and added feedback capture to the customer widget. It
introduced **no migration and no dependency**: `apps/api/pyproject.toml` and both alembic trees are
byte-unchanged across the phase, and the only `package.json` delta anywhere is two new entries in
`apps/admin`'s `scripts` block. Backend change is confined to two read-path additions
(`agent.py`'s assistant `message_id` on the terminal SSE emit; `redteam_programme_service.py`'s
`open_findings` with real primary keys). Server-side authorization for every endpoint the console
now calls was closed in Phase 21 and is inherited, not re-implemented here — see `21-SECURITY.md`
(33/33).

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| customer widget → widget feedback route | Anonymous end user submits a rating against a message id | `message_id`, `conversation_id`, `rating`, optional `csat_score` |
| admin console → Phase 21 agent endpoints | Authenticated operator reads metrics, retrieval health, traces, prompt versions, red-team programme | Per-agent operational telemetry, adversarial finding text |
| admin console → mutating operator actions | Contain a finding, grade a trace, canary/rollback a prompt version | Agent id, finding id, trace id, version id, canary percent |
| backend response → rendered console value | A rendered figure asserts something about the agent's real state | Numeric readings, verdict chips, severity counts, deploy-gate state |
| a design/security review's verdict → what ships | Work reaches the developer only after an adversary has been through it | Review findings, gate results |

The fourth boundary is the one this phase exists because of. The v1.2 milestone audit found the
console asserting things the backend had not said — three "ships in a future release" claims for
capabilities that had shipped, and `not tracked yet` rendered over counts already present in the
fetched payload. A false reading is a security-relevant defect here because the deploy gate is one
of the readings.

---

## Threat Register

57 unique threats. `T-23-SC` is the same supply-chain threat repeated verbatim in all nine plans
and is verified once for the whole phase rather than nine times.

| Threat ID | Category | Component | Severity | Disposition | Mitigation | Status |
|-----------|----------|-----------|----------|-------------|------------|--------|
| T-23-GA-01 | Information Disclosure | terminal SSE emit | low | mitigate | Only the assistant row's id is emitted; `_persist_messages` returns `assistant_msg_id` alone (`agent.py:347`, emit at `:983-992`) | closed |
| T-23-GA-02 | Tampering | widget feedback route | medium | accept | `message_id` is not bound to the caller's session; residual confined to `message_feedback` rows, no read or mutation elsewhere (`widget.py:765-840`) | closed |
| T-23-GA-03 | Repudiation | mock sites asserting a real id | high | mitigate | All nine `_persist_messages` patch sites supply `return_value`; `test_agent_response_carries_assistant_message_id` asserts a `str` | closed |
| T-23-GA-04 | Tampering | id regenerated between persist and emit | medium | mitigate | Single call site, single local reused at the emit; no `uuid4()` between them (`agent.py:952-991`) | closed |
| T-23-GB-01 | Information Disclosure | programme read | medium | mitigate | 404-not-403 IDOR check (`red_team.py:308-314`); only `attack_vector` logged on correlation failure | closed |
| T-23-GB-02 | Denial of Service | description correlation | high | mitigate | `_correlate_description` wrapped in `try/except Exception`, returns `None`; finding still returned with id + severity | closed |
| T-23-GB-03 | Tampering | contained findings resurfacing | critical | mitigate | `WHERE f.status = 'open'` (`redteam_programme_service.py:90`); guard-removal demonstrated red-then-green | closed |
| T-23-GB-04 | Tampering | correlation bound to latest run | medium | mitigate | `LEFT JOIN red_team_runs r ON r.id = f.run_id` scopes to the finding's own run | closed |
| T-23-GB-05 | Tampering | lexical severity sort burying criticals | high | mitigate | Explicit `CASE f.severity WHEN 'critical' THEN 0 …` rank, `created_at DESC` tiebreak (`:91-99`) | closed |
| T-23-GB-06 | Elevation of Privilege | cross-agent finding read | critical | accept | No `agent_id` filter exists because each agent has its own Neon DB; scoping is structural, per CLAUDE.md's per-tenant-project rule | closed |
| T-23-VAL-01 | Spoofing | gate fooled by its own comments | high | mitigate | `stripComments()` mirrors the token gate; all literal matching runs on stripped text (`check-ops-room-wiring.mjs:65-78,118-129`) | closed |
| T-23-VAL-02 | Spoofing | gate scanning itself | medium | mitigate | Walk root is `apps/admin/app`, a sibling of `apps/admin/scripts` where the gate lives | closed |
| T-23-VAL-03 | Tampering | sentinel rendered as `NaN` | high | mitigate | Formatters take `number` only; sentinel predicates type-narrow first; spec asserts no `"NaN"` output | closed |
| T-23-VAL-04 | Tampering | a real zero rendered as an absence | high | mitigate | Zero-input assertions for all three cell renderers plus canary percent (`ops-format.spec.ts:183-195,375`) | closed |
| T-23-VAL-05 | Elevation of Privilege | auth bypass smuggled into a test config | high | mitigate | Zero `demo`/`bypass`/`skipAuth`/token literals; `playwright.unit.config.ts` has no `webServer` and no session config | closed |
| T-23-VAL-06 | Repudiation | pure layer mistaken for render coverage | medium | accept | Spec never requests the `page` fixture; the calling half is covered by the wiring gate's six per-region checks | closed |
| T-23-WF-01 | Tampering | forged `message_id` in feedback | medium | accept | One module-level token for chat and feedback alike; server holds authority, residual is a stray feedback row | closed |
| T-23-WF-02 | Denial of Service | unbounded feedback submissions | medium | mitigate | `MAX_SUBMISSIONS = 2` checked before any send (`FeedbackRow.jsx:5,14,26-28`) | closed |
| T-23-WF-03 | Tampering | out-of-range rating | low | accept | Client sends `'up'`/`'down'` and `1..5`; server `Literal` + `ge=1,le=5` is the authority | closed |
| T-23-WF-04 | Tampering | duplicate row skewing CSAT | medium | mitigate | Same `MAX_SUBMISSIONS` bound; skew capped at one extra row per message (OD-7) | closed |
| T-23-WF-05 | Information Disclosure | message text in feedback body | low | mitigate | Body carries ids, rating and score only; INSERT has no text column (`widget.py:740-748`) | closed |
| T-23-WF-06 | Spoofing | control rendered without an id | medium | mitigate | `if (!messageId) return null` as the first statement after mandatory hooks | closed |
| T-23-UI-01 | Tampering | raw numeric bypass of the sentinel layer | high | mitigate | Number-or-sentinel unions; zero raw `toFixed`/`Number()`/`parseFloat` in either panel | closed |
| T-23-UI-02 | Tampering | predicate applied to the wrong field family | high | mitigate | Underscore predicate on staleness fields only, spaced predicate on `avg_*` only; no cross-application | closed |
| T-23-UI-03 | Tampering | `not tracked yet` over real counts | high | mitigate | Counts rendered raw and unconditionally (`page.tsx:561,566`); `.chan-untracked` applied to zero elements | closed |
| T-23-UI-04 | Elevation of Privilege | cross-tenant metrics read | critical | mitigate | Both routes 404-not-403 on tenant mismatch (`metrics.py:78-88,142-150`); `agentId` sourced only from the route param | closed |
| T-23-UI-05 | Information Disclosure | stale document ids leaked | low | accept | Backend caps at `[:20]`; the field is typed but never rendered — stricter than the accepted bound | closed |
| T-23-UI-06 | Repudiation | region failures scattered across surfaces | medium | mitigate | Panels call `onError` exclusively; `page.tsx` folds them into one `role="alert"` banner (see Observation 1) | closed |
| T-23-UI-07 | Spoofing | a new chip class inventing a verdict | medium | mitigate | Exactly one `<Chip>`, reusing the existing closed union; no new class defined | closed |
| T-23-ADV-01 | Tampering | stale frozen deploy-gate verdict | critical | mitigate | `redTeamBlocked = isGateBlocked(openFindings)` derived fresh every render (`page.tsx:384`); no `deployment_blocked` input | closed |
| T-23-ADV-02 | Elevation of Privilege | accidental irreversible contain | high | mitigate | Resting button only stages; confirm is `autoFocus` + `aria-describedby`; both disabled in flight | closed |
| T-23-ADV-03 | Elevation of Privilege | cross-tenant contain | critical | accept | 404-not-403 ownership check (`red_team.py:438-440`); lookup runs on the per-agent `conn_str` | closed |
| T-23-ADV-04 | Tampering | adversarial text rendered as markup | medium | mitigate | Zero `dangerouslySetInnerHTML`; probe text is not rendered at all (see Observation 2) | closed |
| T-23-ADV-05 | Information Disclosure | probe transcript logged | medium | mitigate | Zero `console.*` calls; `agent_response` never read by the component | closed |
| T-23-ADV-06 | Denial of Service | shared in-flight flag | medium | mitigate | Per-finding-id keyed `Record<string, boolean>` (`AdversaryPanel.tsx:110`) | closed |
| T-23-ADV-07 | Repudiation | optimistic removal asserting success | medium | mitigate | `onSuccess` only invalidates queries; no optimistic removal, no toast | closed |
| T-23-PRM-01 | Elevation of Privilege | accidental canary | high | mitigate | Staged confirm with interpolated share and version, `autoFocus`, both actions disabled while busy | closed |
| T-23-PRM-02 | Elevation of Privilege | accidental rollback | high | mitigate | Same staged shape; copy states "Nothing is deleted." verbatim | closed |
| T-23-PRM-03 | Repudiation | rollback destroying history | medium | mitigate | `rollback()` contains only SELECT/UPDATE(label)/INSERT — no DELETE in the function body | closed |
| T-23-PRM-04 | Tampering | out-of-range canary percent | low | mitigate | Client `min/max` + `clampPercent`; server `Field(ge=0, le=100)` is the independent authority | closed |
| T-23-PRM-05 | Elevation of Privilege | version from another agent | critical | accept | `_get_owned_agent` plus an independent `version.agent_id != agent_id` check (`prompt_version_service.py:232-241`) | closed |
| T-23-PRM-06 | Information Disclosure | soul content rendered as markup | low | accept | Zero `dangerouslySetInnerHTML`; renders as plain text children | closed |
| T-23-PRM-07 | Tampering | unchanged fields hidden from a diff | medium | mitigate | All four fields listed unconditionally, each labelled changed/unchanged, never hidden | closed |
| T-23-BCH-01 | Elevation of Privilege | keyboard shortcut firing a one-way grade | high | mitigate | Four guards present: modifier keys, form-control focus, confirmation open, already filed (`BenchPane.tsx:285-298`) | closed |
| T-23-BCH-02 | Information Disclosure | trace payload logged or injected | medium | mitigate | Zero `console.*`/`dangerouslySetInnerHTML`; route re-checks the trace's own `payload->>'agent_id'` | closed |
| T-23-BCH-03 | Tampering | 409 surfaced as a global failure | medium | mitigate | 409 caught per row, inline note + invalidate; no toast, no global surface | closed |
| T-23-BCH-04 | Repudiation | client-side tally drift | medium | mitigate | No local increment; tally read only from query data, refreshed by invalidation | closed |
| T-23-BCH-05 | Elevation of Privilege | grading another agent's trace | critical | accept | Tenant 404 plus an independent trace-ownership check ordered before the already-filed branch and any write | closed |
| T-23-BCH-06 | Denial of Service | focus lost from the roving listbox | medium | mitigate | Selection state and DOM focus moved together; genuine visually-hidden `aria-live` region fires on every grade | closed |
| T-23-BCH-07 | Tampering | an operator grade rendered as a machine verdict | medium | mitigate | `gradeToChip()` unconditionally returns `'mute'`; used only for `graded_status`, never `verdict` | closed |
| T-23-GATE-01 | Repudiation | a gate recorded as passing that was not run | high | mitigate | Sweep numbers are transcripts; auditor independently re-ran the wiring gate (exit 0) and both touched backend modules (30 passed) | closed |
| T-23-GATE-02 | Spoofing | a soft review presented as adversarial | high | mitigate | No severity floor in the brief; 37 findings, 26 fixed / 3 split / 8 declined with individually cited reasons; output corroborates | closed |
| T-23-GATE-03 | Spoofing | a code review described as a visual one | medium | mitigate | `23-09-SUMMARY.md` attributes UI-1..06 to a prior rendered session and all 31 reviewer findings to code; none mis-attributed | closed |
| T-23-GATE-04 | Repudiation | a validation row that cannot catch its own defect | high | mitigate | Six real verify-script defects documented across five plans; WIRE-02 split across two rows for its two halves | closed |
| T-23-GATE-05 | Tampering | a design fix silently breaking a gate | medium | mitigate | Full gate set re-run after fixes; auditor independently reproduced two of them green post-fix | closed |
| T-23-GATE-06 | Repudiation | a follow-up dropped rather than deferred | medium | mitigate | Five follow-ups tabled with reason and closing condition (the original four plus the `eval/page.tsx` twin) | closed |
| T-23-SC | Tampering | npm/pip installs | high | mitigate | Verified once phase-wide: only `apps/admin`'s `scripts` block changed; widget and API manifests and both alembic trees byte-unchanged | closed |

*Status: open · closed · open — below high threshold (non-blocking)*
*Severity: critical > high > medium > low — only open threats at or above `workflow.security_block_on` (high) count toward `threats_open`*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

Every accepted risk below was checked for whether its **stated bound actually holds in the code**.
An accepted risk whose bound is not true would be OPEN, not accepted.

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-23-01 | T-23-GA-02 | `message_id` is not bound to the caller's session. Residual is a stray `message_feedback` row; no read path and no mutation elsewhere depends on it. | Phase 23 planner | 2026-08-04 |
| AR-23-02 | T-23-GB-06 | No cross-agent finding filter exists because each agent has its own Neon project. Isolation is structural, per CLAUDE.md's per-tenant-project rule, not a query predicate that could be forgotten. | Phase 23 planner | 2026-08-04 |
| AR-23-03 | T-23-VAL-06 | The browserless spec proves the pure layer only. The calling half is covered by the wiring gate's six per-region reachability checks, not by this spec. | Phase 23 planner | 2026-08-04 |
| AR-23-04 | T-23-WF-01 | A forged `message_id` yields a stray feedback row and nothing more; the server remains the authority on rating shape and rate. | Phase 23 planner | 2026-08-04 |
| AR-23-05 | T-23-WF-03 | Client-side range limits are convenience. `Literal["up","down"]` and `ge=1,le=5` on the server are the real constraint. | Phase 23 planner | 2026-08-04 |
| AR-23-06 | T-23-UI-05 | Backend caps `stale_document_ids` at 20. In practice the field is typed but never rendered, so the shipped outcome is stricter than the accepted bound. | Phase 23 planner | 2026-08-04 |
| AR-23-07 | T-23-ADV-03 | Contain is 404-not-403 on ownership and resolves the finding against the per-agent connection string, so cross-tenant containment is structurally impossible. | Phase 23 planner | 2026-08-04 |
| AR-23-08 | T-23-PRM-05 | Beyond agent ownership, `_get_owned_version` independently re-checks `version.agent_id != agent_id` before any read or write. Confirmed a real second check, not a restatement. | Phase 23 planner | 2026-08-04 |
| AR-23-09 | T-23-PRM-06 | Soul content renders as plain text children; there is no `dangerouslySetInnerHTML` anywhere in the panel. | Phase 23 planner | 2026-08-04 |
| AR-23-10 | T-23-BCH-05 | Tenant 404 plus an independent trace-ownership check, ordered before the already-filed branch and before any write. | Phase 23 planner | 2026-08-04 |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-08-04 | 57 | 57 | 0 | gsd-security-auditor (verify-mitigations mode), orchestrator-spot-checked |

**Verification depth.** ASVS L1 (mitigation presence). The auditor went past grep depth on several
items and independently re-ran two of the phase's own gates rather than trusting the recorded
transcript: `check-ops-room-wiring.mjs` (exit 0, PASS) and
`pytest tests/unit/test_agent_task.py tests/unit/test_redteam_programme.py` (30 passed). Both
reproduced the recorded results.

**Orchestrator spot-check.** Three of the highest-stakes evidence citations were re-verified
against source independently of the auditor: the `CASE f.severity` rank
(`redteam_programme_service.py:91-99`, confirmed — critical ranks 0, not a lexical sort), the
`WHERE f.status = 'open'` filter (`:90`, confirmed), and the live gate derivation
(`page.tsx:384 redTeamBlocked = isGateBlocked(openFindings)`, confirmed). All three matched.

### Claims recorded as unverified

Stated plainly rather than assumed, per T-23-GATE-01's own discipline:

1. **The prior session's adversarial reviewer conversation is not transcript-accessible.** Its
   *output* is verified — six files carry comments citing specific finding numbers and the defect
   each fixed — but no one in this session watched it being briefed. The plan forbids the soft
   framing and the output is consistent with a genuine pass; that is corroboration, not proof.
2. **The full sweep was not re-run in this audit.** The wiring gate and the two touched backend
   modules were reproduced exactly. The 1199-test backend suite, the 113-test e2e suite and the
   widget size gate are taken from `23-VALIDATION.md`'s recorded sweep, cross-checked only by the
   two gates that did reproduce.
3. **The three manual-only verifications remain unrun** — the containment round trip, the customer
   feedback interaction, and populated-table overflow. Blocked by the absence of a local PostgreSQL
   server, recorded as deferred in `23-VALIDATION.md`. They are not closed and must not be read as
   closed.

### Non-blocking observations

Disclosed rather than filed as threats, because neither is a data-exposure or privilege issue:

1. **`AlertsBanner.tsx` renders its own `role="alert"` per active alert**, independent of the
   consolidated region-error banner. Taken literally, the runtime DOM can carry more than one
   `role="alert"`, which contradicts one plan's "exactly one error surface" acceptance criterion.
   T-23-UI-06's actual mitigation text holds for every region this phase wires; `AlertsBanner` is a
   pre-existing feature, byte-unchanged in this phase's diff. An ARIA duplication nit inherited from
   before the phase.
2. **T-23-ADV-04 and T-23-ADV-05 are trivially true because the fields are never rendered.**
   `probe_message` and `agent_response` are typed on `OpenFinding` but the Adversary panel surfaces
   neither. The mitigations hold, but only because the operator currently has no visibility into
   what the adversarial probe actually said — which one plan's design intent arguably expected. A
   product follow-up, not a security finding.

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer) — 47 mitigate, 10 accept, 0 transfer
- [x] Accepted risks documented in Accepted Risks Log — 10 entries, each with its bound checked against source
- [x] `threats_open: 0` confirmed — 0 open at any severity, 0 at or above the `high` block threshold
