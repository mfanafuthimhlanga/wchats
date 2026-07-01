---
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
plan: "02"
subsystem: dependency-pinning
tags: [twilio, supply-chain, sms-otp, pyproject]
dependency_graph:
  requires: []
  provides: [twilio==9.10.9 pinned in pyproject.toml]
  affects: [17-03 TwilioSmsProvider import]
tech_stack:
  added: ["twilio==9.10.9"]
  patterns: [exact-pin convention, supply-chain gate, provenance comment]
key_files:
  created: []
  modified:
    - path: apps/api/pyproject.toml
      change: "Added twilio==9.10.9 with provenance comment after requests-oauthlib entry"
decisions:
  - "twilio==9.10.9 selected as exact pin — matches 9.10.0 already installed on dev machine, 9.10.9 is latest published release on PyPI (2026-05-07)"
  - "africastalking NOT pinned — optional SMS alternative, deferred per OD-2"
  - "Exact == pin convention matches stripe/ShopifyAPI pattern established in P16-02"
metrics:
  duration: "9m"
  completed: "2026-07-01"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 1
status: complete
---

# Phase 17 Plan 02: Twilio Supply-Chain Gate and pyproject.toml Pin Summary

**One-liner:** twilio==9.10.9 exact-pinned in pyproject.toml after blocking human supply-chain checkpoint (T-17-SC) cleared.

## What Was Built

Pinned `twilio==9.10.9` in `apps/api/pyproject.toml` with a provenance comment, immediately following the `requests-oauthlib>=2.0` entry, using the exact-pin convention established by stripe/ShopifyAPI in P16-02. The supply-chain gate (T-17-SC) was cleared by human verification before the pin was written.

## Tasks

| Task | Name | Type | Commit | Status |
|------|------|------|--------|--------|
| 1 | Supply-chain legitimacy gate for twilio (blocking-human) | checkpoint:human-verify | — (gate only) | APPROVED |
| 2 | Pin twilio==9.10.9 in pyproject.toml | auto | 94b8fdf | COMPLETE |

## Task 1: Gate Resolution

**Gate type:** blocking-human (T-17-SC — supply-chain, never auto-approvable)

**Legitimacy evidence verified by operator:**
- PyPI https://pypi.org/project/twilio/ confirmed: official Twilio Python SDK, author "Twilio", 367 published releases.
- Source repo: https://github.com/twilio/twilio-python (official Twilio org).
- Version 9.10.9 published on PyPI (uploaded 2026-05-07, current latest release).
- No typosquat — package name is exactly `twilio`.
- Local dev environment already has twilio 9.10.0 installed and verified (`import twilio` succeeds).

**Outcome:** APPROVED. Pinning proceeded.

## Task 2: Pin Applied

Added to `apps/api/pyproject.toml` after `requests-oauthlib>=2.0`:

```toml
# P17-02: SMS OTP — supply-chain gate cleared (human checkpoint T-17-SC approved)
# twilio 9.10.9: official Twilio SDK (github.com/twilio/twilio-python) — SMS OTP default (OD-2), verified 17-02 human gate
"twilio==9.10.9",
```

**Verification results:**
- `grep twilio==9.10.9 pyproject.toml` → matched at line 51
- `python -c "import twilio; print('twilio', twilio.__version__)"` → `twilio 9.10.0` (dev install, import succeeds)
- `africastalking` NOT present in pyproject.toml
- No Twilio credential literals in pyproject.toml

## Deviations from Plan

None — plan executed exactly as written.

## Decisions Made

1. **Exact == pin:** `twilio==9.10.9` uses the same exact-pin convention as `stripe==15.3.0` and `ShopifyAPI==12.7.0` (P16-02 pattern). Reproducible CI installs.
2. **africastalking excluded:** Optional Africa's Talking alternative (OD-2) is not pinned this phase. Will be added behind the `SmsProvider` seam when the swap is needed.
3. **Provenance comment:** Comment references the human gate, source repo, and plan, matching the P16-02 style for auditability.

## Known Stubs

None — this plan only pins a dependency; no application code was written.

## Threat Flags

No new threat surface introduced beyond what the threat model already covers.

## Self-Check: PASSED

- `apps/api/pyproject.toml` modified: FOUND (line 51 contains `twilio==9.10.9`)
- Commit 94b8fdf: FOUND
- No africastalking in pyproject.toml: CONFIRMED
- No credential literals: CONFIRMED
- import twilio: CONFIRMED (9.10.0 installed on dev machine)
