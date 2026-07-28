---
phase: 19-documentation-v1-1-verification
reviewed: 2026-07-28T08:02:54+02:00
depth: standard
files_reviewed: 8
files_reviewed_list:
  - apps/api/tests/integration/test_aud03_audit_gap.py
  - apps/api/tests/integration/test_ver01_adversarial_harness.py
  - apps/api/tests/unit/test_audit_gap_arithmetic.py
  - apps/api/tests/unit/test_ver01_demo_tenant.py
  - apps/api/tests/unit/test_ver01_harness_probes.py
  - docs/guides/integration-provider-guide.md
  - docs/guides/owner-capability-guide.md
  - docs/guides/tool-author-guide.md
findings:
  critical: 2
  warning: 4
  info: 1
  total: 7
status: issues_found
---

# Phase 19: Code Review Report

**Reviewed:** 2026-07-28T08:02:54+02:00
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

Phase 19 shipped no production code — every file in scope is either a prose guide
narrating already-shipped Phase 14/16/17/18 behaviour, or a gated (never yet
executed) test harness. I read the guides against their load-bearing source files
(`capability_service.py`, `capability_envelopes.py`, `tools.py`, `registry.py`,
`schemas.py`, `provider_adapter.py`, `credential_service.py`,
`app/services/actor_seam.py`, `app/services/red_team_probe.py`, the deploy admin
page) rather than judging the prose on its own terms, and traced the two gated
harnesses' behaviour by hand against the dispatcher and enforcement code they
drive.

`docs/guides/integration-provider-guide.md` is clean: every specific claim I
checked — the six `ProviderAdapter` abstract methods, `get_adapter_for_skill`'s
"MUST NOT be called from a route handler or SDK hook" constraint quoted verbatim,
`CredentialHandle`'s redacted `__repr__`/`__str__`, the HKDF-per-call
single-use requirement, the red-team-mode `ContextVar` short-circuit and its
placement before any credential fetch — matches the source exactly. It correctly
defers to `docs/runbooks/integration-credentials.md` for deploy-time
provisioning without duplicating it.

`docs/guides/tool-author-guide.md` is largely accurate (the eight-step
enforcement order, the `confirm_action` exception, the T-14-02-01/02 schema and
registry rules all check out against `tools.py`/`registry.py`/`schemas.py`), but
undercounts the audit-writing branches inside the Step 2.5 IDV gate — see WR-02.

`docs/guides/owner-capability-guide.md` — written for a non-technical business
owner — contains the two most serious findings in this review. Both are
"confident claim that is factually wrong about the source" defects, and both are
about what the shipped system actually lets an owner (or a raw API caller) do:
the "Enabled" control describes a switch that, per `validate_tighten_only`, can
never actually be flipped on for any shipped skill; and the "Rate limit"
control's "at least one call" floor is attributed to the server when it is
in fact enforced only by client-side JavaScript, with no server-side floor at
all.

The two gated test harnesses (`test_aud03_audit_gap.py`,
`test_ver01_adversarial_harness.py`) and their three unit companions are careful,
well-reasoned work, and their core correctness properties hold up under hand
tracing against the dispatcher. I did find one real coverage defect in the
VER-01 adversarial corpus (WR-03) and one unstated environmental assumption in
the AUD-03 harness (WR-04). Neither invalidates the harnesses' central
assertions, but WR-03 materially weakens what the corpus's `attack_class`
labels and printed `by_verdict` breakdown actually prove for an operator
transcribing results into `19-UAT.md`.

## Critical Issues

### CR-01: Owner guide's "Enabled" section describes a control that can never be switched on

**File:** `docs/guides/owner-capability-guide.md:35-43`
**Issue:** The guide states: "Nothing acts on a customer's behalf until you
deliberately turn a skill on. There is no skill that starts active by default —
this is true for every tenant, on day one, with no exception." This reads as a
description of a normal on/off control the owner operates. It is not one.

`validate_tighten_only` (`apps/api/app/services/capability_service.py:307-313`)
rejects every `enabled: False -> True` transition unless the skill's
`PLATFORM_CAPABILITY_DEFAULTS` entry already has `enabled=True`:
```python
if proposed_enabled and not current_enabled:
    if not default_entry.get("enabled", False):
        return _reject("loosen_enabled", "enabled")
```
Every one of the seven `PLATFORM_CAPABILITY_DEFAULTS` entries ships
`enabled: False` (`capability_service.py:97-141`), and no code anywhere in
`apps/api/app/` ever constructs a `CapabilityEnvelope` (or issues an `INSERT
INTO capability_envelopes`) with `enabled=True` outside the tighten-only PATCH
route itself (verified by grepping every `CapabilityEnvelope(` /
`INSERT INTO capability_envelopes` call site in `apps/api/app/`: only
`capability_envelopes.py`'s own PATCH handler, which is gated by this exact
comparator). There is no agent-creation seeding path, no onboarding template,
and no other route that ever writes `enabled=True`.

The shipped admin UI already knows this and hides the control accordingly —
`apps/admin/app/agents/[id]/deploy/page.tsx:1137,1147-1151`:
```tsx
const enabledLocked = envelope.enabled === false && envelope.platform_default.enabled === false
...
<input ... disabled={enabledLocked} .../>
...
{enabledLocked ? 'Cannot re-enable - the platform default is off for this skill.' : ...}
```
Since every platform default is `enabled=false`, `enabledLocked` is `true` for
every skill on every fresh envelope — the checkbox is permanently greyed out in
production. The owner guide never surfaces this; it instead tells the reader
the opposite: that turning a skill on is a thing they do. This is exactly the
"known-true fact" this review was asked to probe for, and it is confirmed: a
non-technical owner who follows only this guide will believe their
money-moving skills can eventually be switched on through the deploy screen,
when in the shipped product they cannot be, for any tenant, ever, without a
direct database write.
**Fix:** Add an explicit statement under "Enabled" (mirroring what the UI
caption already says): every skill's platform default ships `enabled=False`,
and `validate_tighten_only` treats "off" as the tightest legal value for
`enabled` — so a disabled skill cannot be turned on through this screen or any
API call for any currently-shipped skill. Either document this as an
intentional v1.1 platform limitation and point to how a skill actually gets
enabled today (a direct seed/DB action outside the owner's control), or correct
the guide's framing so it does not promise a reachable control.

### CR-02: Owner guide attributes a non-existent server-side rate-limit floor to "the server"

**File:** `docs/guides/owner-capability-guide.md:50-61`
**Issue:** The guide states: "If you try to set a rate limit of zero calls, you
see the exact sentence **the server returns**: 'A rate limit has to allow at
least one call.' Nothing was written when you see that sentence."

This is doubly wrong against the source:

1. That sentence is not returned by the server anywhere. It exists only as a
   client-side string in `apps/admin/app/agents/[id]/deploy/page.tsx:1044`,
   set by `requestRate()` **before** any API call is made:
   ```tsx
   if (draftRate.calls < 1) {
     ...
     setRateNote('A rate limit has to allow at least one call.')
     ...
     return
   }
   ```
2. More importantly, the server has **no such floor at all**.
   `_parse_rate_limit` (`apps/api/app/services/transactional/enforcement.py:160-180`)
   parses `"0/hour"` successfully (`max_calls = 0`), and
   `validate_tighten_only`'s rate-limit branch
   (`capability_service.py:316-333`) only rejects a proposed rate whose
   calls-per-second is **greater** than the current one — `0/hour` is
   tighter than anything and is accepted unconditionally. The page.tsx source
   itself documents this gap in its own comment
   (`apps/admin/app/agents/[id]/deploy/page.tsx:1038-1041`): "no code path in
   this component may stage a non-positive rate, because a zero written here
   cannot be raised again from this screen" — i.e. the floor is a **client-only**
   safety net around a real absence of server-side validation, and once a
   `0/hour` row is written, `validate_tighten_only`'s tighten-only rule makes
   it permanent (0 is the tightest reachable value, so every future proposal
   is a loosen and is rejected).

The guide elsewhere states, as a general principle, "Even a request that
somehow bypassed the screen entirely — a raw API call instead of a click —
would be refused in exactly the same way" (line 143-144). For a zero-call rate
limit, this is false: a raw `PATCH .../capability-envelopes/{skill}` with
`{"rate_limit": "0/hour"}` on a skill whose current rate limit is anything
non-zero would **succeed** (0 rps ≤ current rps) and permanently disable the
skill by rate-limiting it to zero calls forever, with no server-side rejection
at any point. A reader relying on this guide's assurance would not know this
footgun exists.
**Fix:** Either close the server-side gap (add a `> 0` floor to
`validate_tighten_only`'s rate-limit branch, matching what the UI already
enforces client-side), or correct the guide to state plainly that the "at
least one call" floor is a UI-only safeguard, not a server-enforced invariant,
and that a raw API call bypassing the screen can currently write (and
permanently lock in) a zero-call rate limit.

## Warnings

### WR-01: Owner guide's quoted ceiling-refusal sentence is UI copy, not server output

**File:** `docs/guides/owner-capability-guide.md:68-72,133-138`
**Issue:** The guide quotes "That amount is higher than the current ceiling.
Nothing was changed." as "the exact sentence" the reader sees on refusal, and
separately claims a raw API call bypassing the screen "would be refused in
exactly the same way." The refusal itself genuinely is server-enforced (unlike
CR-02) — `validate_tighten_only` does reject `loosen_max_amount_cents` before
any write — but the quoted sentence is client-only text
(`apps/admin/app/agents/[id]/deploy/page.tsx:1085`,
`setMaxNote('That amount is higher than the current ceiling. Nothing was
changed.')`), generated by a pre-flight client-side check
(`draftMaxCents > currentMaxCents`) before the API is even called. A raw API
call that hits the actual PATCH route instead receives
`HTTPException(422, detail="Capability envelope change rejected:
loosen_max_amount_cents")` (`apps/api/app/api/v1/capability_envelopes.py:232`)
— a different, machine-oriented string. "Refused in exactly the same way" is
true for the outcome (422, nothing written) but not for the message, and the
guide's own References section elsewhere correctly attributes these quoted
sentences to `page.tsx` ("every sentence quoted above is copied verbatim from
this file, not paraphrased") — which sits in tension with the body text's
"the server returns" framing for the sibling rate-limit sentence (CR-02).
**Fix:** Clarify that the quoted sentences are the admin UI's copy, and that a
direct API call receives a different (reason-code-style) 422 body with the
same refusal outcome, not the same wording.

### WR-02: Tool-author guide undercounts the IDV gate's audit-writing branches

**File:** `docs/guides/tool-author-guide.md:76-82`
**Issue:** The guide's audit-symmetry paragraph says: "Every rejection branch
— capability denial, **the two IDV block branches**, the `args_mismatch`
branch, the rate/constraint denial, and the Actor `block` decision — writes
exactly one `tool_calls_audit` row before returning (AUD-01 symmetry)."

`_execute_transactional_tool`'s Step 2.5 (`apps/api/app/services/transactional/tools.py:208-301`)
actually contains **three** distinct `write_audit_row` call sites, not two:
- `error="identity_verification.required"` (no verified-session token present,
  lines 218-229)
- `error="identity_verification.check_failed"` (the fail-closed branch when
  `check_verified_session` itself raises, lines 255-266)
- `error="identity_verification.invalid_or_expired"` (token present but
  invalid/expired, lines 278-289)

The general AUD-01 symmetry claim is still correct in spirit, but the specific
count is wrong, and the fail-closed `check_failed` branch — arguably the most
important one for a new skill author to know exists, since it is what makes a
transient DB error at the IDV layer fail closed rather than silently skip
verification — is the one left uncounted.
**Fix:** Change "the two IDV block branches" to "the three IDV block branches"
(or name the DB-error fail-closed branch explicitly) so a reader building an
8th skill has the complete Step 2.5 picture.

### WR-03: VER-01 adversarial corpus's confused-deputy/injection entries are pre-empted by rate-limit exhaustion from earlier entries on the same skill

**File:** `apps/api/tests/integration/test_ver01_adversarial_harness.py:173-502`
**Issue:** `ADVERSARIAL_MESSAGE_CORPUS` runs `_value_bound_ceiling_entries()`
(12 `place_order` calls) and the four `_rate_chain_entries(...)` groups (8
calls each to `cancel_order`, `update_subscription`, `book_slot`,
`update_customer_record`) **before** `_confused_deputy_actor_entries()` and
`_injection_entries()`, all against the **same** `agent_id` inside a single
test run. `apply_rate_and_constraint_checks`
(`apps/api/app/services/transactional/enforcement.py:304-333`) keys its Redis
counter as `f"ratelimit:{agent_id}:{skill}:{window_key}"` and increments it on
every call regardless of outcome, checking the count **before** the
`max_amount_cents` constraint check runs. Every one of these five skills has a
`rate_limit` of `"5/hour"` on the clean tenant
(`CLEAN_TENANT_ENVELOPES`), so:

- After the 12 `place_order` calls in `_value_bound_ceiling_entries()`, the
  `place_order` rate counter is already at 12 (only the first 5 are actually
  denied by `max_amount_cents`; calls 6-12 are denied by `rate_limit`
  instead — already a partial mislabel of `value_bound_evasion`).
- The 5 subsequent `place_order` entries in
  `_confused_deputy_actor_entries()` (amount 4999, well inside the ceiling,
  intended to reach the Actor gate) all run with the counter already far
  past 5, so **every one of them is denied by `rate_denied`, and none of
  them ever reaches Step 5 (the Actor seam)** — the layer the entry's
  `attack_class: "confused_deputy"` claims to exercise.
- The same pattern repeats for `update_subscription` (4 confused-deputy
  entries after an 8-call rate chain on the same skill), `book_slot` (3
  after 8), and `update_customer_record` (3 confused-deputy + 5 injection
  entries after 8), and for `cancel_order` (5 injection entries after 8).

The module docstring's "Session posture" note explicitly anticipates *part* of
this (the IDV-vs-rate-layer split for `issue_refund`) and argues that
"`unauthorized_mutations` does not care WHICH layer catches it" — true for the
pass/fail assertion, which remains sound. But it does not acknowledge that the
rate-window collision described above means the confused-deputy and
injection-labeled entries for `place_order`, `update_subscription`,
`book_slot`, `update_customer_record`, and `cancel_order` (43 of the corpus's
~119 entries) are, in practice, **never** actually adjudicated by the Actor
gate or any injection-resistance mechanism at all — they are caught by the
rate limiter first, every time, because the corpus deliberately runs a
rate-exhausting chain against each of those skills earlier in the same run.
The printed `by_verdict` table (meant for the operator to transcribe into
`19-UAT.md`, per the module's own `-s` framing) will show a large `rate_denied`
count where a reader would reasonably expect to see `actor_blocked` entries
proving Actor-gate confused-deputy resistance specifically. The harness's own
claim that it has "every enforcement layer attacked" is not false — the
capability and identity layers genuinely are attacked by their intended
mechanism — but the Actor-seam and injection-resistance coverage this
corpus claims is largely illusory for the skills affected.

Notably, `test_aud03_audit_gap.py`'s author was aware of and explicitly
guarded against this exact class of problem elsewhere in the same phase
(`_aud03_envelope_rows` deliberately raises `place_order`/`issue_refund`'s
`rate_limit` to `"1000/hour"` specifically "so the max_amount_cents
constraint — not an incidental rate-limit collision — is what denies the
over-ceiling refund"), which makes the omission of the same defense in the
VER-01 corpus look like an oversight rather than a deliberate, documented
trade-off.
**Fix:** Either raise the `rate_limit` for the five affected skills in this
harness's clean-tenant override (mirroring AUD-03's approach), or restructure
the corpus so confused-deputy/injection entries for a skill run *before* that
skill's own rate-chain entries, or split the corpus into per-skill sub-runs
each with a fresh rate window. At minimum, update the module docstring to
name this collision explicitly (the same way the identity-layer/rate-chain
interaction already is) so a reader of the printed `by_verdict` table is not
misled about what was actually proven.

### WR-04: AUD-03 harness's day-bucketing correctness silently assumes host clock and Postgres server clock are the same clock

**File:** `apps/api/tests/integration/test_aud03_audit_gap.py:653-694`
**Issue:** Each batch captures `batch_started_at = datetime.now(timezone.utc)`
in the Python process, then later selects
`WHERE agent_id = :aid AND created_at >= :since` against
`tool_calls_audit.created_at`, a column populated by Postgres's own
`server_default=now()` at INSERT time. The two timestamps are compared
directly, which is only correct if the Python process's clock and the
Postgres server's clock agree closely (well under the sub-second gap between
`batch_started_at` being captured and the batch's DB writes landing). This
holds today only because CLAUDE.md rule 9 requires a **local** Postgres
install (same machine, same clock) — but nothing in the fixture asserts or
documents this dependency, and there is no guard against clock skew the way
the module elsewhere is careful to document its other environmental
assumptions (Redis reachability via `require_redis`, ephemeral-DB hygiene,
etc.). If this harness is ever pointed at a non-local (network-attached)
Postgres instance, clock drift could cause `written_ids` to miss rows from
the current batch (if the DB clock lags) or capture rows from a
still-in-flight previous batch (if the DB clock leads), silently corrupting
the per-day backdating and producing a false gap or a false clean result.
**Fix:** Add a one-line comment noting the same-clock assumption explicitly
next to `batch_started_at`, or defensively widen the `:since` bound by a
small negative skew tolerance (e.g. a few hundred milliseconds) to make the
assumption robust rather than merely true-by-deployment-constraint.

## Info

### IN-01: Tool-author guide's audit-branch list omits the Step 6 adapter-failure branches

**File:** `docs/guides/tool-author-guide.md:76-82`
**Issue:** In addition to undercounting the IDV branches (WR-02), the same
enumerated list — "capability denial, the two IDV block branches, the
`args_mismatch` branch, the rate/constraint denial, and the Actor `block`
decision" — never mentions the two additional `write_audit_row` call sites at
Step 6 in `tools.py`: the `error=f"provider.not_configured:{exc}"` branch
(`ProviderNotConfiguredError`/`CredentialDecryptionError`, lines 493-512) and
the bare adapter-exception branch (`error=error_str`, lines 519-549). The
general AUD-01 symmetry statement made just before and after this list ("every
entry ... that is NOT a replay or benign `in_progress` produces exactly one
row") remains accurate and covers these branches implicitly, so this is not a
factual error, only an incomplete worked example — a new skill author
benchmarking their own adapter's error-handling against "the list" in this
paragraph could reasonably miss that adapter-resolution failures also need to
follow the release-idempotency-then-audit pattern shown at Step 6.
**Fix:** Either broaden the enumerated list to name all seven audit-writing
branches, or replace the itemized list with an explicit pointer to
"every early-return in `_execute_transactional_tool` except `replay` and
`in_progress`" so the count can never drift out of sync with the source again.

---

_Reviewed: 2026-07-28T08:02:54+02:00_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
