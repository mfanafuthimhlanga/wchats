---
phase: 20
slug: frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-gotham-console
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-15
---

# Phase 20 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Source: `20-RESEARCH.md` § Validation Architecture. This is a frontend re-skin phase —
> validation leans on build success, static grep assertions, route smoke, and Playwright/axe.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Playwright (`@playwright/test`, Node) + `@axe-core/playwright`; Next.js `pnpm build` for compile/type gate. (Python Playwright launcher is broken in the main shell — run browser checks from a subagent or Node.) |
| **Config file** | none yet — Wave 0 installs `@playwright/test` + config |
| **Quick run command** | `pnpm --dir apps/admin build` + token-grep assertions (see below) |
| **Full suite command** | `pnpm --dir apps/admin exec playwright test` (route smoke + overflow + reduced-motion + axe) |
| **Estimated runtime** | build ~60–120 s on 4 GB; playwright suite ~30–60 s |

---

## Sampling Rate

- **After every task commit:** `pnpm --dir apps/admin build` (must compile) + relevant grep assertion
- **After every plan wave:** full Playwright/axe suite
- **Before `/gsd-verify-work`:** full suite green + all grep assertions pass
- **Max feedback latency:** ~120 s

---

## Success-Criterion → Validation Map (from ROADMAP Phase 20 SC + RESEARCH)

| SC | Requirement | How validated | Automated command / assertion |
|----|-------------|---------------|-------------------------------|
| SC1 | `globals.css` exposes Gotham tokens (`--ch-1..4`, `data-gate`, Bone-on-Graphite) AND no `dusk-*`/skyline/`amber-console`/`--brass-*` token remains in the `apps/admin` bundle | static grep | `grep -rniE 'dusk-|skyline|amber-console|--brass-|--bg-deep|--glass-bg|Fraunces|Hillbrow' apps/admin/app` returns nothing; `grep -E '\-\-ch-1|data-gate' apps/admin/app/globals.css` matches |
| SC2 | Landing, agents dashboard, agent-new, operations room are real routed Next.js pages; three.js specimen renders on landing/auth only | build + route smoke + grep | `pnpm build` passes; Playwright loads `/`, `/agents`, `/agents/new`, `/agents/[id]`; three.js import appears only in the landing/auth client component (grep `import('three')` scoped) |
| SC3 | Provisioning flow (create → provision → ingest → deploy) unchanged, steps 2–4 locked until step 1; no live-endpoint regression | endpoint-preservation map + route smoke | every `fetch()`/query in `20-UI-SPEC.md` §9 endpoint map still present post-port (grep each endpoint path); Playwright drives the create→lock→next path |
| SC4 | `prefers-reduced-motion` skips shutter repaint + row fades; no horizontal overflow at 1440/1280/900 | Playwright | axe + `document.scrollingElement.scrollWidth <= clientWidth` at each viewport; reduced-motion emulation asserts no shutter animation frames |

---

## Wave 0 Requirements

- [ ] `pnpm --dir apps/admin add -D @playwright/test @axe-core/playwright` + minimal `playwright.config.ts` (base URL, 3 viewport projects)
- [ ] `apps/admin/tests/smoke.spec.ts` — route-load + overflow + reduced-motion stubs for SC2/SC4
- [ ] token-grep assertion script (SC1) runnable in CI/local

*Rebuild phase: existing infra covers none of this — Wave 0 installs the browser-test harness.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Visual fidelity to `prototypes/gotham/` (palette, rail, gate shutter feel) | UI2-01..06 | Pixel/aesthetic judgment not reducible to an assertion | Screenshot each ported route (subagent Playwright) and compare side-by-side with the matching `prototypes/gotham/*.html` |

*All structural behaviors above have automated verification; only visual fidelity is manual.*

---

## Validation Sign-Off

- [ ] All tasks have an `<automated>` verify or a Wave 0 dependency
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (Playwright/axe harness)
- [ ] No watch-mode flags
- [ ] Feedback latency < 120 s
- [ ] `nyquist_compliant: true` set in frontmatter once the planner fills the per-task map

**Approval:** pending
