---
phase: 19-documentation-v1-1-verification
plan: 01
subsystem: docs
tags: [markdown, transactional-tools, provider-adapters, dispatcher, credential-service]

# Dependency graph
requires:
  - phase: 14-transactional-dispatcher-idempotency-actor-hooks
    provides: "_execute_transactional_tool, TOOL_REGISTRY, transactional schemas"
  - phase: 16-integration-adapters-platform-credential-service-l5-extensio
    provides: "ProviderAdapter ABC, credential_service.py, docs/runbooks/integration-credentials.md"
  - phase: 17-identity-verification
    provides: "Step 2.5 IDV gate ordering (T-17-21)"
  - phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
    provides: "red_team_probe.py's ContextVar short-circuit in get_adapter_for_skill"
provides:
  - "docs/guides/tool-author-guide.md — DOC-01, the transactional tool-author contract"
  - "docs/guides/integration-provider-guide.md — DOC-02, the fifth-provider adapter contract"
affects: [19-02, future-phases-adding-transactional-skills-or-providers]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "docs/guides/ as a third markdown location alongside docs/adr/ and docs/runbooks/ (OD-1)"
    - "Delta-scoped guide extending an existing runbook by relative cross-link instead of duplicating it"

key-files:
  created:
    - docs/guides/tool-author-guide.md
    - docs/guides/integration-provider-guide.md
  modified: []

key-decisions:
  - "Both guides verified sentence-by-sentence against apps/api/app/services/transactional/tools.py, registry.py, schemas.py, provider_adapter.py, and credential_service.py before writing — no claim taken from the plan's own summary."
  - "DOC-02 documents that Phase 18's capability admin UI does NOT lift the runbook's credential-management stop-sign (it configures envelopes, not credentials) — corrected inline rather than silently dropping the note."

requirements-completed: [DOC-01, DOC-02]

coverage:
  - id: D1
    description: "docs/guides/tool-author-guide.md documents the 8-step dispatcher enforcement order in source order, the Step 2.5 IDV-before-idempotency ordering (T-17-21), the registry literal-value rule (T-14-02-02), schema typed-scalar rule (T-14-02-01), the examples/to_a2a_skill forward-compat note, and the confirm_action dead end"
    requirement: "DOC-01"
    verification:
      - kind: other
        ref: "grep -F anchor loop over 20 literals in docs/guides/tool-author-guide.md (task <verify>, automated)"
        status: pass
      - kind: other
        ref: "python step-order assertion over the 8 dispatcher step banners (task <verify>, automated)"
        status: pass
    human_judgment: false
  - id: D2
    description: "docs/guides/integration-provider-guide.md documents the ProviderAdapter ABC's six methods, get_adapter_for_skill's route/hook prohibition (quoted verbatim), runtime HKDF/CredentialHandle resolution, and the red-team-mode ContextVar short-circuit, delta-scoped over docs/runbooks/integration-credentials.md"
    requirement: "DOC-02"
    verification:
      - kind: other
        ref: "grep -F anchor loop over 18 literals + duplication/log-emission/container negative gates in docs/guides/integration-provider-guide.md (task <verify>, automated)"
        status: pass
    human_judgment: false

# Metrics
duration: ~20min
completed: 2026-07-27
status: complete
---

# Phase 19 Plan 01: Tool-author and integration-provider guides Summary

**Two source-verified developer guides — the transactional dispatcher's 8-step enforcement order and the fifth-provider adapter contract — published under `docs/guides/`, with every behavioural claim traced to a named file.**

## Performance

- **Duration:** ~20 min
- **Completed:** 2026-07-27T22:09:01Z
- **Tasks:** 2
- **Files modified:** 2 (both new)

## Accomplishments
- `docs/guides/tool-author-guide.md`: narrates `_execute_transactional_tool`'s 8-step enforcement order (`IN-03` precondition → capability check → IDV gate → idempotency reserve → rate/constraint checks → Actor seam → adapter execute → audit+finalize) in the exact order and step names the source banners use, states the Step 2.5-before-Step-3 IDV ordering that protects idempotency slots (T-17-21), quotes `registry.py`'s own "never runtime-inferred from the tool name or arguments" sentence (T-14-02-02), documents the `T-14-02-01` typed-scalar schema rule, and states plainly that `confirm_action`'s `pending_confirmations` rows have no resolver in the codebase today.
- `docs/guides/integration-provider-guide.md`: delta-scoped extension of `docs/runbooks/integration-credentials.md` — names all six `ProviderAdapter` abstract methods, quotes `get_adapter_for_skill`'s docstring constraint verbatim ("MUST NOT be imported or called from any FastAPI route handler or SDK hook"), documents the per-tenant HKDF/`CredentialHandle` runtime resolution the runbook doesn't cover, and explains the red-team-mode `ContextVar` short-circuit (default `False`, sole sanctioned setter `red_team_probe.red_team_mode()`) — with no example that prints, logs, or persists a resolved credential.
- Both guides corrected/updated one factual note each: DOC-01 states the `confirm_action`/`require_human` approval path is unbuilt (not a hypothetical); DOC-02 corrects the runbook's Phase-18 forward-reference — the shipped capability admin UI configures envelopes, not credentials, so the credential-management stop-sign still stands.

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the tool-author guide (DOC-01)** - `e8ce72e` (docs)
2. **Task 2: Write the integration-provider guide (DOC-02)** - `c1bdb14` (docs)

_Note: this is a pure-prose plan — no test/feat/refactor cycle applies._

## Files Created/Modified
- `docs/guides/tool-author-guide.md` - DOC-01: transactional tool-author contract (196 lines)
- `docs/guides/integration-provider-guide.md` - DOC-02: fifth-provider adapter contract, delta-scoped over the credential runbook (206 lines)

## Decisions Made
- Both guides use the house structure from `docs/runbooks/integration-credentials.md` (H1, `**Audience:**/**Phase:**/**Scope:**` header, `---`-separated sections, bold inline constraint call-outs, no YAML frontmatter) per `19-PATTERNS.md`.
- DOC-02's "What the runbook already covers" section lists the seven areas by name with no restated content — the runbook is the extension target, not a stylistic analog only.
- Every command shown in both guides is a local-process invocation (`cd apps/api`, plain `python`); no container-runtime example appears anywhere (CLAUDE.md rule 9).

## Deviations from Plan

None — plan executed exactly as written. Every literal, ordering, and prohibition named in the plan's `<action>` blocks was verified directly against `apps/api/app/services/transactional/tools.py`, `registry.py`, `schemas.py`, `provider_adapter.py`, `credential_service.py`, and `apps/api/app/services/red_team_probe.py` before being written, and no discrepancy between the plan and the shipped source was found.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
Both DOC-01 and DOC-02 anchor gates pass; the full unit suite (`apps/api/tests/unit`, excluding the two docling-dependent files that cannot collect in this environment) still reports 1103 passed / 8 skipped / 0 failed, matching the plan's stated baseline, and `apps/api/pyproject.toml` is byte-identical (this plan added no dependency, per the OD block's hard-stop policy). Plan 19-02 (DOC-03, VER-01 demo-tenant tests) can proceed independently — no file overlap with this plan.

---
*Phase: 19-documentation-v1-1-verification*
*Completed: 2026-07-27*

## Self-Check: PASSED

- FOUND: docs/guides/tool-author-guide.md
- FOUND: docs/guides/integration-provider-guide.md
- FOUND: .planning/phases/19-documentation-v1-1-verification/19-01-SUMMARY.md
- FOUND commit: e8ce72e
- FOUND commit: c1bdb14
- FOUND commit: dcf4fc0
