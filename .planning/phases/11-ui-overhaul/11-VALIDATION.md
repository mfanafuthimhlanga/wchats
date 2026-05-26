---
phase: 11
slug: ui-overhaul
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-26
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> M11 is a pure visual restyle — no automated UI tests exist in the admin app.
> Primary validation is build-time TypeScript + lint + manual visual audit.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | None (no automated UI tests in `apps/admin`) |
| **Config file** | `apps/admin/package.json` (`build`, `lint` scripts) |
| **Quick run command** | `cd apps/admin && pnpm run build` |
| **Full suite command** | `cd apps/admin && pnpm run build && pnpm run lint` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd apps/admin && pnpm run build`
- **After every plan wave:** Run `cd apps/admin && pnpm run build && pnpm run lint`
- **Before `/gsd-verify-work`:** Full suite must be green + manual visual audit vs `reference/wchats-hillbrow-at-dusk.html`
- **Max feedback latency:** 30 seconds (build) / ~2 min (lint)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 11-01-01 | 01 | 1 | UI-06 | — | N/A | build | `cd apps/admin && pnpm run build` | ✅ | ⬜ pending |
| 11-01-02 | 01 | 1 | UI-03 | — | N/A | build | `cd apps/admin && pnpm run build` | ✅ | ⬜ pending |
| 11-01-03 | 01 | 1 | UI-04 | — | N/A | grep | `grep -r "font-pixelify" apps/admin/app/` | N/A | ⬜ pending |
| 11-01-04 | 01 | 1 | UI-05 | — | N/A | grep | `grep -l "logo-mark.svg" apps/admin/app/` | N/A | ⬜ pending |
| 11-02-01 | 02 | 2 | UI-07 | — | N/A | build+visual | `cd apps/admin && pnpm run build` then visual | ✅ | ⬜ pending |
| 11-03-01 | 03 | 3 | UI-08 | — | N/A | build+visual | `cd apps/admin && pnpm run build` then visual | ✅ | ⬜ pending |
| 11-04-01 | 04 | 4 | UI-11 | — | N/A | build+grep | `cd apps/admin && pnpm run build` | ✅ | ⬜ pending |
| 11-05-01 | 05 | 5 | UI-09 | — | N/A | build+visual | `cd apps/admin && pnpm run build` then visual | ✅ | ⬜ pending |
| 11-06-01 | 06 | 6 | UI-10,UI-12 | — | N/A | build+visual | `cd apps/admin && pnpm run build` then visual | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*
*Task IDs are placeholders — planner will assign real IDs in PLAN.md files.*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements.

- No new test files needed — validation is `pnpm run build` + `pnpm run lint` + manual visual audit
- No test framework installation required
- All Wave 0 needs are satisfied by existing `apps/admin/package.json` scripts

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Skyline PNG visible behind every route | UI-01 | Visual-only — no automated screenshot comparison | Open `http://localhost:3000`, `http://localhost:3000/agents`, `/sign-in`. Confirm cityscape shows through nav and behind content. |
| Fraunces headline + strikethrough renders on landing page | UI-07 | Visual typography — no automated font assertion | Load `/` in browser. Confirm Fraunces headline, coral italic accent, and strikethrough on correct words. |
| AgentCard hover gradient bar | UI-08 | CSS `:hover` requires browser interaction | Hover an agent card. Confirm `translateY(-2px)` lift and top coral gradient bar. |
| Glass stat tiles on eval page | UI-09 | Visual blur effect — no assertion possible | Load an agent's eval page. Confirm stat tiles have visible blur/glass effect against skyline. |
| Skyline behind Clerk sign-in card | UI-10 | Visual — Clerk renders its own DOM | Load `/sign-in`. Confirm cityscape visible behind the centred card. |
| Cross-screen visual audit vs canonical prototype | UI-12 | Subjective visual match | Open `.claude/skills/wchats-design/reference/wchats-hillbrow-at-dusk.html` alongside each route. Confirm colour, type, layout match. |

---

## Token Regression Checklist (Per Wave Gate)

Run these greps after each wave to verify old tokens are gone:

```bash
# Old palette must not appear in :root
grep -n "F0E8E0\|7B1C3A\|FFFCF9\|F7F0EA" apps/admin/app/globals.css | grep -v "^.*#" || echo "CLEAN"

# Old font must be gone
grep -rn "font-pixelify\|FungkyBrowDEMO\|Pixelify" apps/admin/app/ || echo "CLEAN"

# Old orange tokens must be gone (replaced by --accent or --gold)
grep -rn -- "--orange\b\|--orange-dim\|var(--orange)" apps/admin/app/ || echo "CLEAN"

# Old amber-bg token must be replaced
grep -rn "amber-bg\|--amber-bg" apps/admin/app/ || echo "CLEAN"

# Build and lint
cd apps/admin && pnpm run build && pnpm run lint
```

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` build verify
- [ ] Sampling continuity: every task commits triggers `pnpm run build`
- [ ] Wave 0: no new infrastructure needed — confirmed existing setup is sufficient
- [ ] Token regression greps pass after Wave 1
- [ ] `nyquist_compliant: true` set in frontmatter when above checks pass

**Approval:** pending
