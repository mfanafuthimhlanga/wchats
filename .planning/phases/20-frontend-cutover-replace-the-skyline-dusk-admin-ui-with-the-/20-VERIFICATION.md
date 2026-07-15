---
phase: 20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-gotham-console
verified: 2026-07-15T17:32:15Z
status: passed
score: 12/12 must-haves verified
behavior_unverified: 0
overrides_applied: 0
---

# Phase 20: Frontend cutover — Gotham console Verification Report

**Phase Goal:** Retire the dusk-indigo/glass admin frontend and replace it with the Gotham "Bone on Graphite" design system (colour-is-a-verdict, fixed rail, gate shutter, provisioning distinct from operations) as a PURE frontend re-skin + re-IA of `apps/admin` — routed Next.js pages, three.js confined to landing/auth, operations room with honest empty states for Phase-21-backed regions — WITHOUT regressing the provisioning flow or any live endpoint.

**Verified:** 2026-07-15T17:32:15Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths / Success Criteria

| # | Truth (ROADMAP SC) | Status | Evidence |
|---|---|---|---|
| SC1 | `globals.css` exposes Gotham tokens; no dusk/skyline/amber-console residue in the production bundle | ✓ VERIFIED | `node apps/admin/scripts/check-no-dusk-tokens.mjs` → `PASS -- no retired dusk/skyline/amber-console markers found.` (exit 0). `globals.css` contains `--ch-1..4` (lines 67-70) and `:root[data-gate="blocked"]` (line 92). All 5 retired dusk components deleted (`TopNav.tsx`, `HeroPipeline.tsx`, `HeroSteps.tsx`, `StepSubtaskCard.tsx`, `UserAvatar.tsx`) + `skyline-w-chats.png` confirmed absent from disk. |
| SC2 | Landing/agents dashboard/agent-new/operations room are real routed Next.js pages; three.js renders on landing/auth only | ✓ VERIFIED | `pnpm build` produces all 11 real routes (`/`, `/agents`, `/agents/new`, `/agents/[id]` + 5 sub-routes, `/sign-in`, `/sign-up`) as compiled Next.js pages (not static HTML). `SceneMount` (the sole `import('three')` call in the whole bundle) is imported only by `app/page.tsx`; grepped `SceneMount` in sign-in/sign-up is a comment only, not a mount. Playwright `smoke.spec.ts` three.js-confinement assertions (canvas present on `/`, absent on all 10 other routes) pass in isolated re-run across all 3 viewports. |
| SC3 | Provisioning flow (create→provision→ingest→deploy, steps 2–4 locked) intact; every live endpoint preserved | ✓ VERIFIED | `deriveStepState` (JourneyStepper.tsx) drives `data-locked` on stations 2-4, unchanged logic, restyled presentation. Every UI-SPEC §9 endpoint grepped present in its new page owner: `/me/provision`, `GET/POST/DELETE /api/v1/agents[/{id}]`, `GET/POST .../documents[+SSE]`, `GET .../eval-runs[/{id}/results]` + `.../trigger`, `GET .../red-team-runs` + POST trigger (endpoint confirmed server-side at `apps/api/app/api/v1/red_team.py:76`), `GET/PUT .../widget-config`, `GET/POST .../checklist-runs[/{id}/acknowledge]` + `.../approve-deployment`, `GET .../alerts[/{id}/resolve]`. `DELETE /agents/{id}` confirmed real server-side (`apps/api/app/api/v1/agents.py`) and now wired (previously dead from the frontend). |
| SC4 | `prefers-reduced-motion` skips shutter/row-fade motion; no horizontal overflow at 1440/1280/900 | ✓ VERIFIED | `tests/reduced-motion.spec.ts` and `tests/overflow.spec.ts` are filled (no `test.fixme` stubs remain — only a historical comment references the old stub state). Full local re-run: overflow + reduced-motion specs both green in the full-suite run (0 failures in those two files). Human visual-fidelity checkpoint (UI2-08 Task 2) approved by operator 2026-07-15 per commit `485cb3e`. |

**Score:** 4/4 roadmap success criteria verified.

### Requirements Coverage (UI2-01..08)

| Requirement | Description | Status | Evidence |
|---|---|---|---|
| UI2-01 | Gotham tokens ported to globals.css | ✓ SATISFIED | Full `:root` token block + `data-gate` override + `.tint` transition present; dusk `:root` fully removed (grep gate green). |
| UI2-02 | Landing rebuilt as routed page + three.js confined | ✓ SATISFIED | `app/page.tsx` full rebuild; `SceneMount.tsx` client-only, dynamic `import('three')`, confinement verified by grep + Playwright. |
| UI2-03 | Agents dashboard rebuilt, `GET /agents` preserved | ✓ SATISFIED | `agents/page.tsx` rebuilt on `.zone.card`; fake command-strip cut (grep clean); `GET /api/v1/agents` + `/me/provision` present. |
| UI2-04 | Agent-new provisioning rebuilt, steps 2-4 locked | ✓ SATISFIED | `.stepper`/`.station` restyle, `deriveStepState` unchanged logic; soul/ingest pages also under this requirement — soul three.js dropped for CSS-only fallback (confirmed, zero three.js refs), ingest SSE-driven swarm colour-fixed (`#C79A3C` absent). |
| UI2-05 | Operations room, 6 regions, honest empty states | ✓ SATISFIED | All 6 `aria-labelledby` regions present in fixed order (live-h/rag-h/bench-h/judge-h/adv-h/prompt-h); 4 honest-`EmptyState` regions + 2 real-data regions (Judgement/Adversary) wired to real endpoints; empty-state copy matches UI-SPEC §12 verbatim. |
| UI2-06 | Widget preview retained (<20kb Preact, out of admin scope for size) | ✓ SATISFIED | Deploy page retains sticky widget preview using scoped `--widget-accent` exception token (`.preview`/`.stage` only, confirmed via grep — does not leak into console chrome or repaint on `data-gate`). |
| UI2-07 | Delete dusk pages/styles from production bundle | ✓ SATISFIED | 5 dusk components + skyline PNG deleted; `check:no-dusk-tokens` exits 0 across the whole bundle (public/wchats — a separate, out-of-scope published customer-widget package — explicitly excluded from the scan with documented rationale). |
| UI2-08 | Accessibility + reduced-motion parity | ✓ SATISFIED | Playwright a11y/overflow/reduced-motion specs filled and passing (see spot-check detail below); human visual-fidelity checkpoint approved. |

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `apps/admin/app/globals.css` | Gotham token system, dusk removed | ✓ VERIFIED | Confirmed `--ch-1..4`, `data-gate`, `.tint`; grep gate green |
| `apps/admin/app/components/gotham/*` | Rail/PageChrome/Zone/Chip/Ledger/Btn/EmptyState/icons/SceneMount/GateProvider | ✓ VERIFIED | All present, wired into layouts/pages |
| `apps/admin/app/page.tsx` | Routed landing w/ three.js | ✓ VERIFIED | Real route, SceneMount mounted, builds |
| `apps/admin/app/agents/page.tsx`, `agents/new/page.tsx` | Dashboard + provisioning | ✓ VERIFIED | Real routes, endpoints preserved |
| `apps/admin/app/agents/[id]/page.tsx` | 6-region ops room | ✓ VERIFIED | 6 regions present, real+honest-empty mix per UI-SPEC §6.4 |
| `apps/admin/app/agents/[id]/{soul,ingest,eval,deploy,settings}/page.tsx` | Sub-route rebuilds | ✓ VERIFIED | All rebuilt, endpoints preserved, five must-fixes applied |
| `apps/admin/scripts/check-no-dusk-tokens.mjs` | SC1 grep gate | ✓ VERIFIED | Runs, exits 0 |
| `apps/admin/tests/{smoke,overflow,reduced-motion,a11y}.spec.ts` | Wave-5 parity suite | ✓ VERIFIED | 4 spec files, 135 tests enumerated, real assertions (no fixme) |

### Key Link Verification (endpoint preservation, UI-SPEC §9)

| Endpoint | Owner Page | Status |
|---|---|---|
| `POST /me/provision` | `/agents`, `/agents/new` | WIRED |
| `GET /api/v1/agents` | `/agents` | WIRED |
| `GET/PATCH /api/v1/agents/{id}` | `/agents/[id]`, `/agents/[id]/soul`, layout, `/agents/new` | WIRED |
| `DELETE /api/v1/agents/{id}` | `/agents/[id]/settings` | WIRED (newly connected, server endpoint pre-existing) |
| `GET/POST /api/v1/agents/{id}/documents` (+ detail, + SSE) | `/agents/[id]`, `/agents/[id]/ingest` | WIRED |
| `GET .../eval-runs`, `.../results`, `.../trigger` | `/agents/[id]/eval`, `/agents/[id]` | WIRED |
| `GET/POST .../red-team-runs` | `/agents/[id]` | WIRED (server endpoint confirmed at `red_team.py:76`) |
| `GET/PUT .../widget-config` | `/agents/[id]/deploy` | WIRED |
| `GET/POST .../checklist-runs` (+ acknowledge), `.../approve-deployment` | `/agents/[id]/deploy`, `/agents/[id]` | WIRED (checklist polling bug fixed: LIST endpoint instead of id-mismatched GET-by-id) |
| `GET .../alerts` (+ resolve) | `AlertsBanner` in `/agents/[id]` | WIRED |

### Behavioral Spot-Checks (independently re-run, not just SUMMARY claims)

| Behavior | Command | Result | Status |
|---|---|---|---|
| SC1 token gate | `node apps/admin/scripts/check-no-dusk-tokens.mjs` | exit 0, "no retired dusk/skyline/amber-console markers found" | ✓ PASS |
| Production build | `corepack pnpm --dir apps/admin build` | Compiled successfully, TypeScript clean, all 11 routes generated | ✓ PASS |
| three.js confinement (module scope) | `grep -rn "from 'three'\|import('three')" app` | Only in `SceneMount.tsx`; used only by `app/page.tsx` | ✓ PASS |
| Full Playwright parity suite (first run, under concurrent build load in this verification session) | `playwright test` | 129 passed / 6 failed (3 a11y timeouts, 2 three.js-canvas 5s-timeout misses on desktop/laptop, all on `/` and `/sign-in`) | ⚠ FLAKY under load |
| Targeted re-run of the 6 failing tests in isolation (no concurrent load) | `playwright test -g "..."` per failing test | 3.js canvas mount: 3/3 pass; a11y: 22/22 pass | ✓ PASS |
| Isolated WebGL/canvas probe | ad-hoc probe spec, `about:blank` WebGL context + landing-page canvas count | WebGL supported, canvas count = 1, no console errors beyond expected THREE.Clock deprecation notice + benign GPU driver perf warnings | ✓ PASS |

**Note on the flaky first run:** the phase's own `20-15-SUMMARY.md` documents this exact class of resource-contention risk on a 4GB dev machine ("Turbopack's on-demand per-route first-compile took up to ~30s/route cold"). My first full-suite run coincided with a concurrent `pnpm build` + TypeScript check I had just run in the same session, which plausibly starved the dev server / browser workers of memory/CPU. Re-running the 6 failing tests in isolation (no concurrent load) reproduced a clean pass every time, and an ad-hoc WebGL probe confirmed the three.js specimen genuinely renders with no functional defect. This is a **test-infrastructure fragility** (the `expect(locator).toHaveCount(1)` on the three.js canvas uses Playwright's default 5000ms assertion timeout, not the elevated 90s test-level timeout, so it doesn't get the same cold-compile headroom the a11y timeouts were given) — not a product regression. Flagged as a non-blocking improvement suggestion, not a gap.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `apps/admin/app/agents/[id]/deploy/page.tsx` | 845, 730 | `.rig` CSS class name retained from prototype comment/history | ℹ️ Info | Not a stub — `.rig` div now renders a real "Run checklist again" button (`triggerChecklist.mutate()`), not the dropped fake "Test the gate" buttons. Confirmed by reading surrounding JSX. |
| `apps/admin/public/logo-mark.svg` | n/a | Unreferenced dead asset still hardcodes retired coral gradient | ℹ️ Info | Confirmed unreferenced anywhere in `app/`/`public/`; does not affect the SC1 gate or build; already logged in `deferred-items.md` by the executor as a known, explicitly-deferred cleanup item. |

No blocker-level anti-patterns found. No `TBD`/`FIXME`/`XXX` debt markers in any `app/` file.

### Human Verification Required

None outstanding — the one item that would normally require human judgment (visual fidelity of the Gotham port against `prototypes/gotham/*.html`) was already run as a blocking human checkpoint and approved by the operator on 2026-07-15 (commit `485cb3e`, `20-15-SUMMARY.md` frontmatter `requirements-completed: [UI2-08] # visual-fidelity checkpoint APPROVED by operator 2026-07-15`).

### Gaps Summary

None. All four ROADMAP success criteria and all eight requirements (UI2-01..08) are verified against the actual codebase, not just SUMMARY claims. The token gate runs clean, the production build compiles, three.js confinement holds under both grep and independent Playwright re-verification, every endpoint in the UI-SPEC §9 preservation map is wired in its new page owner (with one real pre-existing bug — checklist-run polling by the wrong id — found and fixed during 20-12), and the operations room's honest-empty-state regions (Live/Retrieval health/Bench/Prompt) render zero fabricated data while Judgement/Adversary wire real endpoints. The one discrepancy found during independent re-verification (6/135 Playwright failures on first run) was isolated to test-infrastructure timing sensitivity under concurrent machine load in this verification session, not a functional defect — confirmed by clean isolated re-runs and an independent WebGL/canvas probe.

---

_Verified: 2026-07-15T17:32:15Z_
_Verifier: Claude (gsd-verifier)_
