---
phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
plan: 02
subsystem: security
tags: [pii, regex, luhn, prompt-injection, retrieval, agent-tools, celery]

# Dependency graph
requires: []
provides:
  - "app.utils.pii_firewall.scan_response — SEC-01 synchronous regex PII output firewall"
  - "app.worker.tasks.runtime.agent.py single unconditional scan_response call site covering citations/persistence/SSE emit/validator chord"
  - "app.services.agent_tools.py RETRIEVED_CONTEXT_HEADER/FOOTER + _frame_retrieved_context — SEC-02 data-not-instructions framing on retrieve_tool"
affects: [18-09 (SEC-03 content-injection probe builds on this framing), any future plan touching run_agent_turn's response_text local or retrieve_tool's final return]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Sibling-module shape to app.utils.sanitize.py: module docstring naming the threat, module-level compiled re.Pattern, small public surface, no I/O/config reads"
    - "Single-rebind-covers-all-consumers: response_text rebound once immediately after unpack so citations, persistence, SSE emit, and the async validator chord can never diverge"
    - "Labeled-delimiter data-not-instructions framing (actor_seam.py idiom) reused at a second trust boundary (retrieved chunk text)"

key-files:
  created:
    - apps/api/app/utils/pii_firewall.py
    - apps/api/tests/unit/test_pii_firewall.py
  modified:
    - apps/api/app/worker/tasks/runtime/agent.py
    - apps/api/app/services/agent_tools.py
    - apps/api/tests/unit/test_agent_tools.py

key-decisions:
  - "SEC-01 detector order is email -> sa_id -> card (not card -> sa_id) because a 13-digit SA ID also satisfies the card regex's 13-19-digit range and would pass Luhn under the card check too; checking the more specific sa_id pattern first prevents misclassifying an SA ID as a card"
  - "RETRIEVED_CONTEXT_HEADER text refers to 'the closing marker below' rather than repeating the literal footer string, so header+footer concatenation in _frame_retrieved_context produces exactly one occurrence of each constant (verified by test_frame_retrieved_context_idempotent_safe)"

patterns-established:
  - "L4 output firewall pattern: pure, single-positional-arg, no config/flag read, called unconditionally at exactly one synchronous call site on the customer-facing response path"

requirements-completed: [SEC-01, SEC-02]

coverage:
  - id: D1
    description: "A response carrying a Luhn-valid card number, an email address, or a plausible-YYMMDD+Luhn-valid SA ID number is replaced with a generic deflection (PII_DEFLECTION) before persistence, the SSE emit, and the validator chord; a clean response and a legitimate business phone number pass through byte-identical"
    requirement: "SEC-01"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_pii_firewall.py::test_detect_email, test_detect_card_luhn_valid, test_detect_sa_id_number, test_flagged_response_is_replaced_with_generic_deflection, test_business_phone_number_is_not_flagged, test_clean_response_passes_through_byte_identical, test_card_shaped_but_luhn_invalid_is_not_pii, test_thirteen_digits_with_impossible_date_is_not_pii"
        status: pass
    human_judgment: false
  - id: D2
    description: "The firewall cannot be disabled by prompt content instructing it to stand down; scan_response has exactly one positional parameter and no disable flag"
    requirement: "SEC-01"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_pii_firewall.py::test_firewall_not_prompt_disableable"
        status: pass
    human_judgment: false
  - id: D3
    description: "scan_response is wired into run_agent_turn at exactly one unconditional call site between the response_text unpack and citation extraction, so citations, persistence, the SSE emit, and the async validator chord all see the same filtered text; a flag is logged by detector name only, never the matched value"
    requirement: "SEC-01"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_agent_task.py, test_agent_turn_metrics.py, test_agent_turn_langfuse.py, test_agent_turn_connection_batch.py (22 tests, all pass with the new call site in place)"
        status: pass
    human_judgment: false
  - id: D4
    description: "retrieve_tool's tool-result text is enclosed by an explicit data-not-instructions header/footer applied after the per-chunk truncation loop, so a truncated chunk stays fully enclosed; citations and lookup_structured_tool are unaffected; sanitize_chunk_text remains untouched"
    requirement: "SEC-02"
    verification:
      - kind: unit
        ref: "apps/api/tests/unit/test_agent_tools.py::test_retrieve_tool_data_wrapper, test_frame_retrieved_context_idempotent_safe, test_retrieve_truncates_to_max_chunks"
        status: pass
    human_judgment: false

duration: 13min
completed: 2026-07-26
status: complete
---

# Phase 18 Plan 02: SEC-01 PII output firewall + SEC-02 retrieval data-not-instructions framing Summary

**Regex-only synchronous PII output firewall (email/Luhn-card/SA-ID) rebinding `response_text` at one call site in `run_agent_turn`, plus a labeled-delimiter data-not-instructions wrapper on `retrieve_tool`'s tool-result text that survives per-chunk truncation.**

## Performance

- **Duration:** 13 min
- **Started:** 2026-07-26T22:40:31+02:00
- **Completed:** 2026-07-26T22:52:55+02:00
- **Tasks:** 3
- **Files modified:** 5 (2 created, 3 modified)

## Accomplishments
- `app/utils/pii_firewall.py` — three structurally-validated detectors (email, Luhn-valid card, SA ID with date-plausible YYMMDD + Luhn check digit), stdlib `re` only, no phone-number detector by design (avoids deflecting a tenant's own published support line)
- `scan_response(text) -> (text_or_deflection, detector_or_None)` is a pure single-arg function with no disable flag — proven prompt-undisableable by `test_firewall_not_prompt_disableable`, which feeds it a response containing both an override instruction AND a Luhn-valid card number and confirms it is still deflected
- Wired into `run_agent_turn` (`apps/api/app/worker/tasks/runtime/agent.py`) at exactly one unconditional call site, immediately after `response_text` is unpacked and before citation extraction — a single rebind covers citations, persistence, the terminal SSE emit, and the async Gatekeeper/Auditor/Strategist validator chord, so none of those four consumers can ever see a different text than the others
- `retrieve_tool`'s tool-result text (`apps/api/app/services/agent_tools.py`) is now enclosed by `RETRIEVED_CONTEXT_HEADER`/`RETRIEVED_CONTEXT_FOOTER` via `_frame_retrieved_context`, applied after the `_CONTENT_CHAR_LIMIT` truncation loop so a truncated chunk stays fully enclosed — proven by `test_retrieve_tool_data_wrapper` reusing the 5000-char-chunk truncation fixture and asserting the footer is still the text's final segment
- Full unit suite: 982 (18-01 baseline) → 994 (after Task 2) → 996 (after Task 3) passed, 8 skipped, 0 failed

## Task Commits

Each task was committed atomically:

1. **Task 1: pii_firewall.py — three structurally-validated detectors + generic deflection** - `1b35074` (feat)
2. **Task 2: Wire the firewall into the customer turn at one unconditional call site** - `c6a0309` (feat)
3. **Task 3: SEC-02 — frame retrieve_tool's tool-result text as data, not instructions** - `572b3dd` (feat)

_No TDD tasks in this plan — all three tasks are `type="auto"` without `tdd="true"`._

## Files Created/Modified
- `apps/api/app/utils/pii_firewall.py` - New sibling module to `sanitize.py`: `PII_DEFLECTION`, `detect_pii`, `scan_response`, `_luhn_ok`, `_sa_id_date_plausible`, `_EMAIL_RE`, `_CARD_RE`, `_SA_ID_RE`
- `apps/api/tests/unit/test_pii_firewall.py` - 12 tests covering Luhn correctness, each detector's positive/negative case, the phone-number exclusion, byte-identical pass-through, and the prompt-undisableable case
- `apps/api/app/worker/tasks/runtime/agent.py` - Added `from app.utils.pii_firewall import scan_response` import; inserted the unconditional `scan_response(response_text)` call + conditional `pii_firewall.response_deflected` warning log + unconditional rebind, between the `response_text` unpack (line 894 pre-edit) and `_extract_citations` call
- `apps/api/app/services/agent_tools.py` - Added `RETRIEVED_CONTEXT_HEADER`, `RETRIEVED_CONTEXT_FOOTER` constants and `_frame_retrieved_context` helper; changed `retrieve_tool`'s final return to wrap `str(chunks)` with the framing
- `apps/api/tests/unit/test_agent_tools.py` - Added `test_retrieve_tool_data_wrapper` (resolved via the existing `_fn()` helper, reusing `test_retrieve_truncates_to_max_chunks`' exact patch set) and `test_frame_retrieved_context_idempotent_safe`

## Decisions Made
- **Detector check order (email → sa_id → card):** a 13-digit SA ID candidate that passes its date-plausibility + Luhn check would ALSO satisfy the card regex's 13-19-digit range and pass Luhn as a card. Checking the more specific `sa_id` pattern before the broader `card` pattern prevents `detect_pii` from misreporting a valid SA ID as `"card"`.
- **Header wording avoids repeating the footer's literal text:** the first drafted `RETRIEVED_CONTEXT_HEADER` named the exact footer string (`"...closing END RETRIEVED CONTEXT marker below..."`), which made `framed.count(RETRIEVED_CONTEXT_FOOTER)` equal 2 instead of 1 in `test_frame_retrieved_context_idempotent_safe` (the header mentioning the footer text counted as a second occurrence). Reworded to "the closing marker below" — functionally identical instruction, structurally distinct string, test now passes with `count() == 1` for both header and footer.

## Deviations from Plan

None - plan executed exactly as written. Both detector-ordering and header-wording decisions above were implementation choices made while writing Task 1/3, not deviations from any specified behavior — the plan's `must_haves` did not prescribe a detector check order or exact header wording beyond the required phrase fragment ("not as instructions"), both of which are satisfied.

## Issues Encountered
- Initial `test_frame_retrieved_context_idempotent_safe` failed once (`count(RETRIEVED_CONTEXT_FOOTER) == 2`, expected `1`) because the header's prose literally quoted the footer string. Fixed by rewording the header (see Decisions Made above); no test assertions were weakened to work around it.

## User Setup Required

None - no external service configuration required. No new dependency was installed (`apps/api/pyproject.toml` unchanged — verified by `git diff --exit-code apps/api/pyproject.toml` after every task).

## Next Phase Readiness
- SEC-01 and SEC-02 are both closed and independent of the BLR/CAP cluster (18-01/03..11); no other plan in this phase reads or writes `pii_firewall.py`, the new `agent.py` call site, or the `retrieve_tool` framing constants.
- Plan 18-09 (SEC-03, content-injection red-team probe) is expected to exercise the `_frame_retrieved_context` boundary as part of its injection defense proof — the framing is in place and stable for that plan to build on.
- Full unit suite at 996 passed / 8 skipped / 0 failed, above the 982-baseline floor this plan started from.

---
*Phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i*
*Completed: 2026-07-26*

## Self-Check: PASSED

All created/modified files confirmed present on disk; all four task/summary commit hashes (`1b35074`, `c6a0309`, `572b3dd`, `0f570b7`) confirmed present in `git log --oneline --all`.
