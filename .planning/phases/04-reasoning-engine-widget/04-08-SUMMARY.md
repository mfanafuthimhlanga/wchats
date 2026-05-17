---
phase: 04-reasoning-engine-widget
plan: "08"
subsystem: demo-page
tags: [demo, superseded]
dependency_graph:
  requires: [04-05, 04-06, 04-07]
  provides: [demo-page, demo-scripts, e2e-test]
  affects: [04-09]
tech_stack:
  added: []
  patterns: [AGENT_E2E_ENABLED-guard]
key_files:
  created:
    - apps/demo/index.html
    - apps/demo/.gitignore
    - scripts/demo_m4.sh
    - scripts/demo_m4.ps1
    - apps/api/tests/integration/test_agent_e2e.py
  modified: []
commit: b7edae5
status: superseded
---

## Summary

Plan 04-08 was executed in commit `b7edae5` and created the following artifacts:

- `apps/demo/index.html` — Bella Vista Coffee demo page with Parchment & Wine Design G layout and embedded widget iframe
- `apps/demo/.gitignore` — excludes `demo_m4_runtime.html` runtime variant
- `scripts/demo_m4.sh` — Bash demo orchestrator (provision → ingest → PATCH soul → print URL)
- `scripts/demo_m4.ps1` — PowerShell equivalent (Windows-native)
- `apps/api/tests/integration/test_agent_e2e.py` — guarded E2E test (AGENT_E2E_ENABLED=1 required)

## Supersession Note

This plan was subsequently superseded per decision recorded in STATE.md:

> [04-08] SUPERSEDED — demo page no longer needed (production system, no demo); replaced by 04-09 (cleanup) + 04-10 (Clerk auth)

Plan 04-09 (Wave 8) replaces these artifacts:
- Renames eval fixture brand (Bella Vista Coffee → Acme Consulting)
- Replaces `apps/demo/index.html` with a minimal sign-in redirect placeholder
- Replaces `scripts/demo_m4.*` with generic `scripts/provision_agent.*` scripts (no hardcoded brand)
- Retains `apps/api/tests/integration/test_agent_e2e.py` with minor adjustments

The human checkpoint (Task 2 — run demo end-to-end) was intentionally skipped because the demo page artifact itself was superseded before that checkpoint was reached.

## Human Checkpoint Outcome

Skipped — plan superseded before human verification was due. The live widget was already verified in Plan 04-06 (Soul Editor PATCH + widget iframe rendering) and Plan 04-07 (eval harness integration tests against live Claude API).

## Self-Check: PASSED (close-out)

Commits exist for all five artifacts created. Human checkpoint skipped per supersession decision. Wave 8 (04-09) will clean up brand-specific demo artifacts.
