---
phase: 19-documentation-v1-1-verification
fixed_at: 2026-07-28T09:00:00+02:00
review_path: .planning/phases/19-documentation-v1-1-verification/19-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 5
documented_not_fixed: 1
skipped: 0
status: partial
partial_reason: >-
  WR-03's underlying coverage defect is documented but NOT repaired — the corpus
  was deliberately not restructured (see the WR-03 entry). 19-VERIFICATION.md
  counts that defect as one of three gaps blocking the phase; it remains open.
---

# Phase 19: Code Review Fix Report

**Fixed at:** 2026-07-28T09:00:00+02:00
**Source review:** .planning/phases/19-documentation-v1-1-verification/19-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (CR-01, CR-02, WR-01, WR-02, WR-03, WR-04)
- Fixed: 5 (CR-01, CR-02, WR-01, WR-02, WR-04)
- Documented but NOT fixed: 1 (WR-03)
- Skipped: 0

> **WR-03 is not resolved.** Its collision is now documented in the harness
> docstring, but the corpus was deliberately left unrestructured, so roughly 43
> of ~119 confused-deputy/injection entries are still pre-empted by
> `rate_denied` before reaching the layer they claim to exercise. The defect is
> unchanged in substance; only its visibility improved. `19-VERIFICATION.md`
> lists it among the three gaps blocking Phase 19 — do not treat this report as
> closing it.

**Scope fence held:** zero files under `apps/api/app/` touched, `apps/api/pyproject.toml`
byte-unchanged, no dependency added. Confirmed by `git diff --stat` against the phase's
starting commit (`fd9bee1`) — only two guide files and two integration-test files (comments
only) changed.

**Test verification:**
- Baseline (before fixes): `1134 passed, 8 skipped, 0 failed`
- Final (after all 6 fixes): `1134 passed, 8 skipped, 0 failed` — identical, no regressions.
- Both gated integration modules (`test_ver01_adversarial_harness.py`,
  `test_aud03_audit_gap.py`) confirmed to **collect** cleanly after edits (2 tests collected,
  0 collection errors). Neither could be *executed* — no PostgreSQL server on this machine.

## Fixed Issues

### CR-01: Owner guide's "Enabled" section describes a control that can never be switched on

**Files modified:** `docs/guides/owner-capability-guide.md`
**Commit:** `0e8230f`
**Applied fix:** Verified against `capability_service.py:307-313` (`validate_tighten_only`'s
`enabled` branch) and `PLATFORM_CAPABILITY_DEFAULTS` (all seven entries ship `enabled: False`)
that a disabled skill cannot be turned on through the deploy screen or any API call, for any
currently-shipped skill. Added an explicit paragraph under "Enabled" stating this plainly:
the platform default is `off` for every skill, "off" is the tightest legal value under
tighten-only, so the checkbox is permanently locked (matching the admin UI's own
`enabledLocked` caption, "Cannot re-enable - the platform default is off for this skill"),
and a skill is enabled today only via a direct database action outside the owner's control.
Took the documentation branch, not the "add a workaround" branch — there is no owner-facing
path to enable a skill today, and the guide now says so instead of implying one exists.

### CR-02: Owner guide attributes a non-existent server-side rate-limit floor to "the server"

**Files modified:** `docs/guides/owner-capability-guide.md`
**Commit:** `30d14d9`
**Applied fix:** Verified against `_parse_rate_limit` (`apps/api/app/services/transactional/
enforcement.py:160-180`, no lower bound) and `validate_tighten_only`'s rate-limit branch
(`capability_service.py:316-333`, only rejects a proposed rate *greater* than current) that
`0/hour` passes the server's tighten-only check unconditionally. Corrected the guide to
attribute the "at least one call" sentence to the deploy screen's own pre-flight check
(`page.tsx`'s `requestRate()`, before any API call), stated plainly that the server has no
lower bound at all, and warned that a raw API call bypassing the screen can write (and, under
tighten-only, permanently lock in) a `0/hour` rate limit.

**Per the hard scope fence, only the documentation branch of the review's suggested fix was
taken.** `_parse_rate_limit` in `apps/api/app/services/transactional/enforcement.py` was
**not** modified — adding a `> 0` floor there would be an unreviewed production behavior
change, out of bounds for a phase that shipped zero production code.

**Follow-up requiring its own phase (recorded here, not fixed):** `_parse_rate_limit` has no
lower bound. A raw `PATCH .../capability-envelopes/{skill}` call with `{"rate_limit":
"0/hour"}` succeeds unconditionally against any current non-zero rate, and once written,
`validate_tighten_only`'s tighten-only rule makes it permanent — every future proposal to
raise the rate back above zero is a loosening and is rejected. This is a genuine
self-inflicted, permanent denial-of-service footgun on that skill, reachable by any caller
with API access to the capability-envelope route. Needs a dedicated phase to add and test a
server-side floor (`> 0`) in `validate_tighten_only`'s rate-limit branch.

### WR-01: Owner guide's quoted ceiling-refusal sentence is UI copy, not server output

**Files modified:** `docs/guides/owner-capability-guide.md`
**Commit:** `dd0cb89`
**Applied fix:** Confirmed the quoted sentence "That amount is higher than the current
ceiling. Nothing was changed." is client-side copy in `apps/admin/app/agents/[id]/deploy/
page.tsx:1085` (`requestMaxAmount()`, pre-flight `draftMaxCents > currentMaxCents` check),
and that a raw API call instead receives the server's actual 422 body,
`"Capability envelope change rejected: loosen_max_amount_cents"`
(`apps/api/app/api/v1/capability_envelopes.py:232`, confirmed by reading the route). Updated
both citations (the "Ceiling (maximum amount)" subsection and the "Tighten-only, and what
happens at the edge" section) to attribute the quoted sentence to the screen, note that the
refusal genuinely is server-enforced (unlike CR-02's true gap) but with different wording on
a direct API call, and scoped the "refused in exactly the same way" claim specifically to the
ceiling case rather than leaving it read as a blanket rule that CR-02 shows is false for
rate limits.

### WR-02: Tool-author guide undercounts the IDV gate's audit-writing branches

**Files modified:** `docs/guides/tool-author-guide.md`
**Commit:** `6dddb63`
**Applied fix:** Counted the audit-writing branches inside `_execute_transactional_tool`'s
Step 2.5 myself against `apps/api/app/services/transactional/tools.py:208-301` rather than
trusting either the guide's "two" or the review's "three" — confirmed **three** distinct
`write_audit_row` call sites: `identity_verification.required` (no token, lines 218-229),
the fail-closed `identity_verification.check_failed` (when `check_verified_session` itself
raises, lines 255-266), and `identity_verification.invalid_or_expired` (lines 278-289).
Changed "the two IDV block branches" to "the **three** IDV block branches" and named all
three explicitly, calling out the fail-closed branch's importance.

**Also resolves IN-01 as a byproduct, as anticipated in the task brief.** Rather than
hand-enumerating the remaining unnamed branches (which, on inspection, is actually larger
than IN-01's stated scope — I found a further unnamed `actor_require_human` branch at
`tools.py:460` that neither WR-02 nor IN-01 mentioned, in addition to IN-01's two named Step 6
branches, `provider.not_configured` and the bare adapter exception), I replaced the tail of
the enumerated list with "...and every other early return in `_execute_transactional_tool`"
— the same "pointer" fix IN-01's own Fix section offered as an alternative to hand-naming all
seven-plus branches. This closes the sentence permanently against future branch-count drift
(including the previously-unflagged `actor_require_human` branch) rather than leaving a
half-corrected enumerated list, so the same sentence WR-02 and IN-01 both cite is now fully
accurate for both findings in one edit.

### WR-03: VER-01 adversarial corpus's confused-deputy/injection entries are pre-empted by rate-limit exhaustion

**Files modified:** `apps/api/tests/integration/test_ver01_adversarial_harness.py`
**Commit:** `372f845`
**Applied fix:** Verified the collision by hand: `_value_bound_ceiling_entries()` (12
`place_order` calls) and the four `_rate_chain_entries(...)` groups (8 calls each to
`cancel_order`, `update_subscription`, `book_slot`, `update_customer_record`) run before
`_confused_deputy_actor_entries()` and `_injection_entries()` in `ADVERSARIAL_MESSAGE_CORPUS`,
all against one `agent_id`; all five of those skills carry `rate_limit: "5/hour"` on
`CLEAN_TENANT_ENVELOPES` (confirmed in `app/services/red_team_probe.py:406-461`, including
`place_order` which the module's own inline comment at line 174 does not mention but the
`CLEAN_TENANT_ENVELOPES` list itself does carry). Added a "KNOWN COVERAGE GAP" paragraph to
the module's "Session posture" docstring, documenting the collision explicitly (this was the
review's own stated minimum acceptable fix) — it explains why the harness's core
`unauthorized_mutations` assertion remains sound while the printed `by_verdict` table's
`rate_denied` count should not be read as Actor-gate or injection-resistance evidence for the
five affected skills.

**Deliberately did not restructure the corpus itself** (raising rate limits via an
AUD-03-style envelope override, or reordering entries) — the per-finding guidance explicitly
authorized skipping a fix "larger than a fix pass should carry" for this exact case, and this
harness is an integration test I cannot execute here (no PostgreSQL server on this machine),
so I could not validate a runtime restructuring actually produces the intended verdict
distribution. Confirmed instead that: the docstring-only change does not alter runtime
behavior (syntax-checked, and the module still collects as exactly 1 test); the 12
corpus-shape unit tests in `tests/unit/test_ver01_harness_probes.py` still pass unchanged
(they assert count, uniqueness, keys, and idempotency-key freshness — none assert
attack-class-to-verdict mapping, so they were never at risk from this docstring-only change).
This structural gap is real and left for a follow-up phase with access to a running
Postgres instance to fix and verify end-to-end.

### WR-04: AUD-03 harness's day-bucketing correctness silently assumes host clock and Postgres server clock are the same clock

**Files modified:** `apps/api/tests/integration/test_aud03_audit_gap.py`
**Commit:** `454e096`
**Applied fix:** Confirmed the same-clock dependency by reading `batch_started_at =
datetime.now(timezone.utc)` (Python process clock) compared against `tool_calls_audit.
created_at` (Postgres `server_default=now()`) via `WHERE created_at >= :since`
(lines 653-694). Added an explicit multi-line comment directly above `batch_started_at`
naming the assumption, why it holds today (CLAUDE.md rule 9's local-Postgres requirement
means same machine, same clock), and what would need revisiting (a negative skew tolerance
on `:since`) if this harness is ever pointed at a non-local Postgres instance. Chose the
comment over an executable assertion: an assertion comparing Python-clock vs. Postgres-clock
skew would itself be new runtime logic in a harness I cannot execute here to validate, and the
per-finding guidance explicitly offered "an explicit assertion or a clearly-worded comment"
as equally defensible — the comment carries zero execution risk while fully documenting the
assumption for a future reader or maintainer.

## Skipped Issues

None — all 6 in-scope findings were fixed. (WR-03's full structural corpus fix was
deliberately deferred rather than applied, per its own per-finding guidance authorizing a
documented partial fix; see WR-03 above. It is recorded as fixed, not skipped, because the
review's own "at minimum" bar — documenting the collision — was met.)

---

_Fixed: 2026-07-28T09:00:00+02:00_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
