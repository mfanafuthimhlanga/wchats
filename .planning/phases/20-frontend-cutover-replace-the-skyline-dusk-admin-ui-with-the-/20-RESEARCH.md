# Phase 20: Frontend Cutover — Gotham Console - Research

**Researched:** 2026-07-15
**Domain:** Next.js App Router frontend re-skin/re-IA + three.js embedding
**Confidence:** HIGH (all findings grounded in direct file reads of this codebase; no external-library API uncertainty beyond three.js version drift)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Canonical design contract = `prototypes/gotham/` (11 HTML pages + `tokens.css` + `app.css` + `scene.js` + `MESH.md`). Port these; do not redesign.
- Palette name: "Bone on Graphite" (graphite base, bone chrome). The four `--ch-1..4` channel luminances + the `data-gate` shutter mechanism live in `tokens.css` — port them into `apps/admin/app/globals.css`.
- "Colour is a verdict": green = pass, red = fail/gate-shut are the ONLY hues. Eval channels are values of bone by luminance; red appears only on failure. Colour is never decoration.
- Routed Next.js pages, NOT a single-surface `console.html` fold. `console.html` was an exploration; the shipped IA is routed pages.
- Provisioning and operations are DIFFERENT interfaces. `agent-new` = provisioning wizard (steps 2–4 locked until step 1 done). The agent operations room = a flywheel with no end.
- three.js confined to landing + auth only (design law).
- Nested `<a>` inside a card `<a>` wrapper: the browser ejects the inner anchor. Use `<span>`/`<button>` for card actions.
- After ANY token rename, grep the old token repo-wide. CSS fails SILENTLY on undefined vars (white-on-white). OUTSTANDING: a repo-wide `--brass-*` audit was never done, and "brass armature" prose remains in `gotham/MESH.md:46` — check before/at cutover.
- Never put decoration in an artifact's functional slot (e.g. a decorative orb in the live-prompt slot).
- Never write UI copy that explains the design's own metaphor to the user.
- Product copy voice is neutral/literal (verified / check), not a themed metaphor.

### Claude's Discretion
(None explicitly separated in CONTEXT.md — the entire `<decisions>` block is locked. Discretion areas identified during research are called out inline below and in Open Questions.)

### Deferred Ideas (OUT OF SCOPE)
- `console.html` single-surface fold (explored, NOT chosen for the cutover — routed pages win).
- All Phase 21 backends (turn_metrics, retrieval_metrics, traces, red-team programme tables, prompt_versions, new endpoints).
- Deleting the sibling `../design` firn template #11 (housekeeping, not wchats).
- Removing the empty locked `prototypes/assay/` dir (cosmetic).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| UI2-01 | Port Gotham tokens into `globals.css` (Bone-on-Graphite, `--ch-1..4`, `data-gate`), replacing the dusk-indigo/glass token set | See "Token Cutover" — full token diff table + deletion list |
| UI2-02 | Rebuild landing as routed page; wire three.js specimen (landing/auth only) | See "three.js in Next.js" + "Routed Pages" §Landing |
| UI2-03 | Rebuild agents dashboard reading `GET /agents` | See "Routed Pages" §Agents dashboard |
| UI2-04 | Rebuild agent-new provisioning (steps 2–4 locked); create→provision→ingest→deploy unchanged | See "Routed Pages" §Provisioning + endpoint inventory |
| UI2-05 | Operations room, six regions, real endpoints where they exist + honest empty states | See "Operations Room" region-by-region backing table |
| UI2-06 | Keep widget preview (Preact <20KB) embedded | See "Routed Pages" §Widget preview — confirmed decorative mock pattern, both dusk and gotham use the same non-functional preview approach |
| UI2-07 | Delete dusk-*/skyline/amber-console styles + pages from production bundle | See "Deletion/Parity" — full enumeration of every dusk reference found |
| UI2-08 | a11y + reduced-motion + no horizontal overflow at 1440/1280/900 | See "Validation Architecture" + "Common Pitfalls" |
</phase_requirements>

## Summary

Phase 20 is **not a token-value swap under stable variable names** — it is a full visual and structural rebuild. The current `apps/admin` styling system is CSS custom properties defined once in `globals.css` `:root` (dusk-indigo/glass palette: `--bg-deep`, `--accent`, `--text-1`, `--glass-bg`, etc.) consumed almost entirely through **inline `style={{ ... var(--token) }}` objects** in TSX, not Tailwind utility classes (Tailwind is imported but used in exactly one place: `className="ml-4 shrink-0"` in `AlertsBanner.tsx`; everything else is `className="glass"` / `className="on-photo"` referencing three custom classes, plus raw inline styles). The Gotham prototypes use a **completely different token vocabulary** (`--bg`, `--ink`, `--live`, `--pass`, `--fail`, `--ch-1..4`, `--surface`) and a **completely different component-class system** (`app.css`: `.rail`, `.zone`, `.chip`, `.ledger`, `.btn-primary`, `.section`) with a fixed 56px left nav rail instead of the current sticky top `TopNav`. Because token *names* differ and the component-class philosophy differs (engraved hairline zones vs. blurred glass panels), each dusk page/component must be rebuilt from the Gotham HTML source, not patched. `globals.css` gets Gotham's tokens installed wholesale (Wave 1); every subsequent wave replaces a dusk route's JSX with a Gotham-derived one, keeping the exact same `fetch`/`useQuery` data-fetching calls to the FastAPI backend (these must not change — that is the non-regression contract).

three.js is not yet a project dependency; the prototype loads it from a CDN via a dynamic `import()` inside `scene.js`. For the Next.js port, install `three` as a real npm dependency (pin the working version; do not silently take `latest`) and mount it from a `'use client'` component using a `useEffect`-scoped dynamic `import('three')` (equivalent to `next/dynamic(..., { ssr: false })` but simpler for a canvas-only widget with no React-facing render tree). This keeps the ~600KB three.js chunk out of every route except landing and the auth routes, and fully out of the Preact widget bundle (a completely separate build under `apps/admin/public/wchats/` — untouched by this phase).

The operations room's six Gotham regions (Live, Retrieval health, The bench, Judgement, Adversary, The prompt) map unevenly onto real backend support per `AGENT-MGMT-GAPS.md`: **Judgement** (eval runs) and **Adversary** (red-team runs) have real, working endpoints and should render live data; **Live**, **Retrieval health**, **The bench**, and **The prompt** have no aggregate backend today and must render honest, clearly-labeled empty states, deferring to Phase 21 (OPS-01..16). The existing `AlertsBanner` (`GET /agents/{id}/alerts`) has no Gotham region — the design's `data-gate="blocked"` room-wide repaint is the natural home for a `red_team_critical` alert; `eval_regression` alerts should surface as a chip inside Judgement.

**Primary recommendation:** Treat Wave 1 as a pure token/asset swap in `globals.css` (verifiable by grep + build), then rebuild each route wave-by-wave directly from the corresponding Gotham HTML file, preserving every existing `fetch`/`useQuery` call verbatim and translating Gotham's `app.css` classes into either global classes (imported once, like dusk's `.glass`) or scoped component styles — never re-deriving new class names from scratch.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Token/palette definition (Bone on Graphite) | Frontend Server (SSR) — `globals.css` loaded at root layout | — | CSS custom properties are static, defined once, inherited by every client component; no runtime computation needed |
| Landing page + three.js specimen | Browser / Client | Frontend Server (SSR) shell | The specimen is WebGL/canvas — inherently client-only; the surrounding page chrome (headline, copy, nav) can server-render, with the canvas mounted client-side inside it |
| Auth pages (sign-in/sign-up) | Browser / Client | — | Clerk's `<SignIn>`/`<SignUp>` are already client components (`'use client'`); any three.js background behind them is the same client-only pattern as landing |
| Agents dashboard, agent-new, operations room data | API / Backend (FastAPI) | Frontend Server (SSR) for the page shell | All real data is fetched client-side via `useQuery` against `NEXT_PUBLIC_API_BASE` — the Next.js server only serves the React shell; it does not proxy or SSR-fetch backend data (Clerk `getToken()` is browser-only) |
| Operations-room empty-state regions (Live, Retrieval health, bench, prompt) | Browser / Client | — | No backend exists yet (Phase 21) — these are pure static/conditional UI states, not data-fetching concerns |
| Widget bundle (Preact, <20KB) | CDN / Static | — | Built and served from `apps/admin/public/wchats/` independently of the Next.js app bundle; must never import `three` or any admin-only code |
| Deploy-time checklist/approve gate | API / Backend | Frontend (client mutation) | `POST /agents/{id}/checklist-runs`, `POST .../approve-deployment` — existing `deployment.py` endpoints; UI only triggers + displays |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `three` | 0.185.1 (verified via `npm view three version`, 2026-07-15) [VERIFIED: npm registry] | WebGL specimen + gate shutter rendering | Already the library the Gotham prototype's `scene.js` is written against (CDN-imported at r161); npm-installing it removes the CDN/offline dependency and gives version pinning |
| `next` | 16.2.6 (already installed) | App Router, routing, dynamic import | Already the project's framework — no change |
| `@tanstack/react-query` | ^5.100.11 (already installed) | Data fetching for all routed pages | Already used by every existing `agents/*` page; keep the exact same query keys/fetch calls when porting |
| `@clerk/nextjs` | ^7.3.5 (already installed) | Auth + session token for API calls | Unchanged; only the `clerkAppearance` object in `layout.tsx` needs re-theming to Gotham colors |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `@playwright/test` | 1.61.1 (verified via `npm view @playwright/test version`) [VERIFIED: npm registry] | Node-based screenshot/route-smoke/viewport-overflow checks | Wave 4 parity gate — the existing Python Playwright launcher (`scripts/verify_new_page.py`) is broken in the main shell per project constraint; a Node-based Playwright install run from a subagent avoids that blocker entirely |
| `@axe-core/playwright` | 4.12.1 (verified via `npm view @axe-core/playwright version`) [VERIFIED: npm registry] | Automated a11y assertions (UI2-08) | Combine with Playwright for `prefers-reduced-motion` + contrast + landmark checks per route |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `three` npm install | Keep CDN `import('https://unpkg.com/three@0.161.0/...')` as in the prototype | CDN import works offline-hostile (breaks with no network, breaks any CSP `script-src`), can't be version-locked in `package.json`/lockfile, and silently updates behavior if unpkg serves a different cached version. Reject — install as a real dependency. |
| `useEffect` + dynamic `import('three')` | `next/dynamic(() => import('./SceneMount'), { ssr: false })` | Both work; `next/dynamic` is the documented Next.js pattern and is preferred when the component itself needs to be conditionally rendered in JSX (e.g., swapped out entirely on `prefers-reduced-motion`). Use `next/dynamic` for the top-level mount component; the inner `import('three')` inside `scene.js`'s logic can stay as-is since it's already coded that way. |
| Custom empty-state components per operations-room region | A single reusable `EmptyRegion` component parameterized by copy + Phase-21 req IDs | Prefer the reusable component — four of six regions need the same "not yet instrumented" pattern; one component avoids four near-duplicate implementations |

**Installation:**
```bash
cd apps/admin
pnpm add three
pnpm add -D @types/three @playwright/test @axe-core/playwright
```

**Version verification:** `three@0.185.1` and `@types/three@0.185.1` confirmed current via `npm view three version` / `npm view @types/three version` on 2026-07-15; both first published 2012-12-07 (`three`) and 2016-05-17 (`@types/three`) per `npm view <pkg> time.created` — long-lived, high-download packages. `@playwright/test@1.61.1` and `@axe-core/playwright@4.12.1` confirmed via the same command pattern.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| `three` | npm | 13+ yrs (created 2012-12-07) | 9.36M/wk | github.com/mrdoob/three.js | `SUS` (seam flag: "too-new" — false positive, see note) | Approved — checkpoint below is a formality |
| `@types/three` | npm | 9+ yrs (created 2016-05-17) | 7.13M/wk | github.com/DefinitelyTyped/DefinitelyTyped | `SUS` (seam flag: "too-new" — false positive) | Approved — checkpoint below is a formality |
| `@playwright/test` | npm | long-standing Microsoft package | very high | github.com/microsoft/playwright | not run through seam (dev-only test tooling, not a runtime dependency of the shipped bundle) | Approved by inspection — official Microsoft package |
| `@axe-core/playwright` | npm | long-standing (Deque Systems) | high | github.com/dequelabs/axe-core-npm | not run through seam (dev-only) | Approved by inspection — official Deque package |

**Packages removed due to `[SLOP]` verdict:** none.
**Packages flagged as suspicious `[SUS]`:** `three`, `@types/three` — the `package-legitimacy check` seam flagged both `too-new` because their **latest published version** date is recent (2026-07-01 / 2026-07-09 respectively). This is a false positive from a release-cadence heuristic: `npm view <pkg> time.created` shows both packages are 9–13 years old with 7–9M weekly downloads and canonical GitHub repos (`mrdoob/three.js`, `DefinitelyTyped/DefinitelyTyped`). The planner must still add a lightweight `checkpoint:human-verify` task before `pnpm add three` per protocol, but the check itself is a formality — confirm `npm view three repository.url` resolves to `mrdoob/three.js` and downloads are in the millions/week before proceeding.

*Package names in this table were discovered via direct `npm view`/`npm registry` lookups against names already present in the codebase's own `scene.js` (CDN URL `unpkg.com/three@0.161.0`) — not via WebSearch or training-data guessing — so they carry `[VERIFIED: npm registry]` provenance for the name itself, not just registry existence.*

## Architecture Patterns

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│ Browser                                                              │
│                                                                       │
│  ┌──────────────┐   ┌──────────────────┐   ┌──────────────────────┐ │
│  │ Landing (/)  │   │ Sign-in/Sign-up   │   │ /agents, /agents/new,│ │
│  │ 'use client' │   │ 'use client'      │   │ /agents/[id]/*        │ │
│  │              │   │                   │   │ 'use client'          │ │
│  │ <SceneMount> │   │ <SceneMount>?     │   │ (no three.js)         │ │
│  │  useEffect → │   │  useEffect →      │   │                       │ │
│  │  import(     │   │  import('three')  │   │  useQuery/useMutation │ │
│  │   'three')   │   │  (if adopted)     │   │  → fetch()            │ │
│  └──────┬───────┘   └──────┬────────────┘   └──────────┬────────────┘ │
│         │ canvas render     │ canvas render              │             │
│         ▼                   ▼                            ▼             │
│  ┌──────────────────────────────────────────┐  ┌─────────────────┐   │
│  │ Gotham design tokens (globals.css :root)  │  │ Clerk getToken()│   │
│  │ --bg --ink --live --pass --fail --ch-1..4 │  │ Bearer <JWT>    │   │
│  │ data-gate="open|blocked" on <html>        │  └────────┬────────┘   │
│  └────────────────────────────────────────────┘           │            │
└─────────────────────────────────────────────────────────────────────┘
                                                              │ HTTPS
                                                              ▼
                              ┌───────────────────────────────────────┐
                              │ FastAPI (apps/api)                    │
                              │ /api/v1/agents/{id}                   │
                              │ /api/v1/agents/{id}/documents         │
                              │ /api/v1/agents/{id}/eval-runs         │
                              │ /api/v1/agents/{id}/red-team-runs     │
                              │ /api/v1/agents/{id}/checklist-runs    │
                              │ /api/v1/agents/{id}/approve-deployment│
                              │ /api/v1/agents/{id}/alerts            │
                              └───────────────────────────────────────┘

Widget bundle (apps/admin/public/wchats/widget.iife.js) — served as a
static CDN-style asset, built by a SEPARATE Preact toolchain, never
imports 'three' or any Next.js/admin code. The "widget preview" shown
on the Deploy page is a decorative aria-hidden CSS mock in BOTH the
dusk and Gotham designs — not a live iframe of the real bundle.
```

### Recommended Project Structure
```
apps/admin/app/
├── globals.css                    # Gotham tokens installed wholesale (Wave 1)
├── layout.tsx                     # font links swapped (Space Grotesk/Newsreader/
│                                   #   JetBrains Mono/Inter replace Fraunces);
│                                   #   clerkAppearance re-themed to Gotham colors
├── components/
│   ├── SceneMount.tsx              # NEW — 'use client', useEffect + dynamic
│   │                                #   import('three'), calls window.mountGotham
│   │                                #   equivalent ported to a React-owned module
│   ├── GateProvider.tsx            # NEW — owns data-gate attribute on <html>,
│   │                                #   wraps AlertsBanner's red_team_critical signal
│   ├── Rail.tsx                    # REPLACES TopNav.tsx — fixed 56px left nav
│   ├── EmptyRegion.tsx             # NEW — shared empty-state for unbacked ops regions
│   └── ... (AgentCard, JourneyStepper, UserAvatar — re-skinned, same props/data contract)
├── page.tsx                        # landing — rebuilt from prototypes/gotham/index.html
├── agents/
│   ├── page.tsx                    # rebuilt from agents.html — GET /agents unchanged
│   ├── new/page.tsx                # rebuilt from agent-new.html — same 2 mutations
│   └── [id]/
│       ├── layout.tsx              # stepper unchanged logic, Rail-based chrome
│       ├── page.tsx                # operations room — rebuilt from agent.html,
│       │                           #   six <section> regions per Gotham source
│       ├── soul/page.tsx           # rebuilt from soul.html (tokens only — NO
│       │                           #   three.js VESSEL mount; out of Phase 20 scope)
│       ├── ingest/page.tsx         # rebuilt from ingest.html
│       ├── eval/page.tsx           # rebuilt from eval.html
│       ├── deploy/page.tsx         # rebuilt from deploy.html (decorative widget
│       │                           #   preview kept, same non-functional pattern)
│       └── settings/page.tsx       # rebuilt from settings.html
├── sign-in/[[...sign-in]]/page.tsx # tokens + Clerk appearance only
└── sign-up/[[...sign-up]]/page.tsx # tokens + Clerk appearance only
```

### Pattern 1: Client-only three.js mount without `next/dynamic` SSR crash
**What:** A `'use client'` component that mounts a canvas via `useEffect`, dynamically imports `three` inside the effect body (not at module top-level), and tears down the render loop + WebGL context on unmount.
**When to use:** Landing page specimen, and (if the design law is extended there — see Open Questions) auth pages.
**Example:**
```typescript
// Source: pattern derived from prototypes/gotham/scene.js (window.mountGotham)
// + Next.js App Router client-only-component convention (WebSearch-verified,
// see Sources) [CITED: nextjs.org/docs + community pattern]
'use client'
import { useEffect, useRef } from 'react'

export default function SceneMount({ scenarios = 64, fails = 3 }: { scenarios?: number; fails?: number }) {
  const hostRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let disposed = false
    let cleanup: (() => void) | undefined

    import('three').then((THREE) => {
      if (disposed || !host) return
      // ... port the body of prototypes/gotham/scene.js mountGotham() here,
      // using THREE.* instead of the dynamically-imported CDN module.
      // Return a dispose function that cancels the rAF loop, removes the
      // resize listener, and calls renderer.dispose().
      cleanup = () => { /* renderer.dispose(); cancelAnimationFrame(raf); ... */ }
    })

    return () => {
      disposed = true
      cleanup?.()
    }
  }, [scenarios, fails])

  return <div ref={hostRef} aria-hidden="true" style={{ position: 'absolute', inset: 0 }} />
}
```

### Pattern 2: The gate as a room-wide repaint, not a badge
**What:** `data-gate="blocked"` set on `<html>` (or a top-level wrapping element) flips CSS custom properties (`--live` → `--seal`) globally via the `:root[data-gate="blocked"]` selector already defined in `tokens.css`.
**When to use:** Any critical red-team finding or blocked-deploy state. Do NOT implement this as a component-local conditional class; it must be a single root-level attribute so every consumer of `var(--live)` repaints together (WARDEN law, `MESH.md`).
**Example:**
```css
/* Source: prototypes/gotham/tokens.css:80-88 — port verbatim into globals.css */
:root[data-gate="blocked"] {
  --live:            var(--seal);
  --live-hot:        var(--seal-hot);
  --live-dim:        var(--seal-dim);
  --live-ink:        #0E1012;
  --hairline:        rgba(229, 72, 77, 0.24);
  --hairline-soft:   rgba(229, 72, 77, 0.10);
  --hairline-strong: rgba(229, 72, 77, 0.48);
}
```

### Pattern 3: Preserve data-fetching verbatim while replacing markup
**What:** Every `useQuery`/`useMutation` call in the current dusk pages (query keys, endpoint URLs, `Authorization: Bearer ${token}` headers, `refetchInterval` polling logic) must be copied unchanged into the Gotham-rebuilt page. Only the JSX/styling around the returned data changes.
**When to use:** Every routed page in Waves 2–3.
**Example:**
```typescript
// Source: apps/admin/app/agents/[id]/layout.tsx:85-107 — the pattern to preserve
const agentQuery = useQuery({
  queryKey: ['agent', id],
  queryFn: async () => {
    const token = await getToken()
    if (!token) throw new Error('Not authenticated')
    const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return r.json() as Promise<AgentDetail>
  },
  enabled: isLoaded && !!isSignedIn,
  refetchInterval: (query) => { /* unchanged */ },
  staleTime: 0,
})
```

### Anti-Patterns to Avoid
- **Nested `<a>` inside a card `<a>` wrapper:** locked decision from CONTEXT.md — the browser ejects the inner anchor. Use `<span>`/`<button>` for in-card actions (e.g., AgentCard's per-agent action buttons).
- **Renaming a CSS var and leaving old call sites:** CSS fails silently on an undefined var (renders as unset/inherited, often white-on-white against the dark bg). Every `var(--old-token-name)` call site must be updated in the same commit as the token rename, and grepped for afterward (see Deletion/Parity).
- **Putting decoration in a functional slot:** e.g., a decorative bloom/orb in the live-prompt display slot (soul page) — locked anti-pattern from CONTEXT.md.
- **New copy that explains the metaphor:** never write UI text like "the gate represents whether your agent is verified" — the mechanism must be self-evident from its behavior (locked decision).
- **Mounting three.js at module top-level:** `import * as THREE from 'three'` at the top of a file that's part of any route's initial bundle pulls the ~600KB chunk into that route's first-load JS even if unused at runtime. Always dynamic-import inside a client-only effect or `next/dynamic(..., { ssr:false })`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WebGL specimen rendering | A custom canvas/WebGL abstraction | Port `prototypes/gotham/scene.js` logic directly into `three` npm calls | The shader math (noise-displaced icosahedron, additive blending, gate-color lerp) is already written and tuned; re-deriving it risks visual drift from the approved design |
| Reduced-motion handling | Per-component `matchMedia` checks scattered across files | The existing `@media (prefers-reduced-motion: reduce)` blocks already in both `tokens.css` (`.tint` transitions) and `app.css` (global animation-duration override) + the `REDUCED` flag already in `scene.js` | Both prototypes already implement this correctly; duplicating the check per-component risks missing a spot |
| Route-smoke / viewport-overflow verification | A bespoke screenshot diffing script | `@playwright/test` (Node) — `page.setViewportSize({width:1440/1280/900, height:900})` + `document.documentElement.scrollWidth > window.innerWidth` assertion | Standard, well-documented pattern; avoids reinventing browser automation, and sidesteps the broken Python Playwright launcher constraint |
| a11y auditing | Manual contrast/ARIA review only | `@axe-core/playwright` `AxeBuilder().analyze()` per route | Automated, catches missing landmarks/labels/contrast issues that manual review misses; still pair with manual keyboard-nav spot checks for the gate/shutter interaction |

**Key insight:** This phase's complexity is not "which library" — it's "faithful, verbatim port of an already-designed system into React, without breaking any of the ~20 existing live `fetch`/`useQuery` calls." The main hand-rolling risk is re-deriving CSS/shader values from memory instead of copying them from the prototype source files.

## Common Pitfalls

### Pitfall 1: Token rename leaves orphaned `var(--old-name)` call sites → silent white-on-white
**What goes wrong:** A component still references `var(--accent)` or `var(--text-1)` after `globals.css` no longer defines them (Gotham's token names are `--live`, `--ink`, not `--accent`, `--text-1`). CSS custom properties fail silently — the property resolves to its inherited/initial value, which is frequently invisible text (transparent or white-on-white against a dark bg), not a build error.
**Why it happens:** No build-time check for CSS custom-property existence; TypeScript/ESLint cannot see into inline `style={{ color: 'var(--text-1)' }}` strings.
**How to avoid:** After each page's rebuild, `grep -rn "var(--\(bg-deep\|bg-elev\|surface-[0-9]\|glass-\|border-\|accent\|lilac\|cyan\|amber\|text-[0-9]\|text-on-accent\|green\|red\|gold\|radius-\|shadow-\|font-display\)" apps/admin/app/` and confirm zero hits before marking a wave done. Keep a running list of the ~40 dusk token names to check against (enumerated below in Deletion/Parity).
**Warning signs:** Text or borders invisible against the dark background in a screenshot; DevTools "Computed" panel shows the CSS var as empty string.

### Pitfall 2: three.js SSR crash / hydration mismatch
**What goes wrong:** Importing `three` (which touches `window`, `document`, WebGL context) at module scope, or rendering a canvas whose dimensions depend on `window.innerWidth` during SSR, produces either a build-time `ReferenceError: window is not defined` or a client/server markup mismatch.
**Why it happens:** Next.js App Router server-renders every component by default unless explicitly client-only and effect-deferred.
**How to avoid:** `'use client'` directive + `import('three')` only inside `useEffect` (never at module top-level); render a stable, dimension-independent placeholder (`<div ref={hostRef} />`) during SSR, size the canvas only after mount via `ResizeObserver`/`resize` listener (already the pattern in `scene.js`'s `resize()` function).
**Warning signs:** `next build` fails with a `window is not defined` stack trace pointing into `three`; or a React hydration warning naming the scene container.

### Pitfall 3: three.js version drift from r161 (prototype) to a current npm install
**What goes wrong:** The prototype's CDN import pins `three@0.161.0`; a fresh `pnpm add three` installs `0.185.1`. three.js has had color-management and material-default changes across major point releases (e.g., `outputColorSpace` vs `outputEncoding`, `ColorManagement` defaults) that can silently shift the rendered hue/brightness of `AdditiveBlending` + `vertexColors` materials — exactly what this scene uses for the gate-color lerp.
**Why it happens:** three.js does not follow strict semver for rendering-affecting internals between minor versions.
**How to avoid:** After installing, visually diff the rendered specimen against the prototype's `file://` rendering (open `prototypes/gotham/index.html` directly in a browser as the reference) at the same `scenarios`/`fails` params; check that `renderer.outputColorSpace` (or `outputEncoding` on older API) is set explicitly rather than left to a version-dependent default.
**Warning signs:** The core/cage/rings render a visibly different color temperature or brightness than the static prototype screenshot.

### Pitfall 4: `--brass-*` / stale copy audit incomplete
**What goes wrong:** `MESH.md:46` in `prototypes/gotham/` still says "brass armature," and `scene.js` itself names its bone-colored constants `BRASS`/`BRASS_HOT` (values `0xe7e5e1`/`0xffffff` — actually bone, not brass hex `#C79A3C`) — a leftover from an earlier "Brass on Petrol" naming pass before the design settled on "Bone on Graphite." `prototypes/dusk-hybrid/` and `prototypes/dusk-console/` (separate, older prototype directories, NOT `prototypes/gotham/`) contain literal `amber-console`-branded token files — confirmed via repo-wide grep, these are NOT referenced by `apps/admin` today, but are a naming trap if anyone copies from them by mistake.
**Why it happens:** The design went through a documented "Brass on Petrol" → "Bone on Graphite" revision (see `MESH.md`'s own header, which still opens with "The palette — Brass on Petrol" while `tokens.css`'s actual `:root` values are unambiguously graphite/bone with zero brass hex present). The prose and identifier names didn't get fully swept.
**How to avoid:** Before Wave 1 completes, run `grep -rn -i "brass\|petrol\|oxblood" prototypes/gotham/` and confirm the planner's port only takes numeric/hex VALUES from `tokens.css` (which are correct) — do not port the word "brass" into any new copy, class name, or JS identifier. Rename `BRASS`/`BRASS_HOT` to `LIVE`/`LIVE_HOT` (or similar) when porting `scene.js` logic into `SceneMount.tsx`, matching the actual "colour is a verdict, live is brightness" law.
**Warning signs:** Any new file in `apps/admin` containing the literal string "brass."

### Pitfall 5: `data-gate` set on the wrong element in a multi-page app
**What goes wrong:** The prototype sets `data-gate="open|blocked"` on `<html>` (`document.documentElement`) because it's a single static page. In a Next.js App Router app with a shared `layout.tsx`, if `data-gate` is set imperatively via `useEffect` in a leaf page component, navigating away without cleanup leaves the attribute stuck, repainting unrelated routes red.
**Why it happens:** `<html>` is rendered once by the root layout and persists across client-side navigations; a page-level effect that sets it must also reset it on unmount, or (better) the gate state should be derived from a single source of truth (e.g., a React Context provided at the root, driven by the current agent's live red-team-critical status) rather than each page independently mutating the DOM.
**How to avoid:** Centralize gate-state ownership in one root-level provider/component (`GateProvider` in the recommended structure above) that reads the relevant signal (e.g., `alerts` query for the active agent) and is the only writer of `document.documentElement.dataset.gate`.
**Warning signs:** Navigating from a blocked agent's operations room to the landing page (or another agent) still shows the red gate theme.

### Pitfall 6: Horizontal overflow at 900px from the 56px fixed rail + unresponsive inline-style layouts
**What goes wrong:** Current dusk pages use `display:flex` with fixed pixel widths in inline styles (e.g., deploy page's `width: '300px'` sticky preview panel) and no responsive breakpoints beyond the one `@media (max-width: 1099px) { .preview-panel { display:none } }` rule in `globals.css`. `app.css`'s `.rail` already has a `@media (max-width: 900px)` rule converting the left rail into a bottom bar — but any dusk-derived inline-style layout ported without checking against that breakpoint will not automatically become responsive.
**Why it happens:** Inline styles don't participate in CSS media queries; only classes (like `.rail`, `.preview-panel`) can be conditionally overridden.
**How to avoid:** Any fixed-width panel (preview panels, sidebars) ported from a dusk page must be re-expressed as a class with an explicit `@media` rule (mirroring `app.css`'s existing `@media (max-width: 900px)` block), not left as an inline style. Test at 1440/1280/900 explicitly per UI2-08 (see Validation Architecture).
**Warning signs:** `document.documentElement.scrollWidth > document.documentElement.clientWidth` at any of the three required viewports.

## Code Examples

### Gotham gate-flip JS logic (verbatim reference, port into a React event handler)
```javascript
// Source: prototypes/gotham/index.html:395-451 (inline script, THE GATE / WARDEN)
function setGate(state) {
  var shut = state === 'blocked';
  root.dataset.gate = state;
  if (window.gotham) window.gotham.setGate(state);
  // ... updates copy, chip class, verdict icon, and moves focus to the
  // now-enabled counterpart button (accessibility: keep focus visible)
}
```

### Existing agent-detail data contract to preserve (do not touch endpoint shapes)
```typescript
// Source: apps/admin/app/agents/[id]/layout.tsx:11-22
interface AgentDetail {
  id: string
  name: string
  role: string
  status: string
  neon_project_id: string | null
  schema_version: string | null
  soul_role?: string | null
  soul_voice?: string | null
  soul_do_list?: string[] | null
  created_at: string
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Skyline photo background + glassmorphism (blur/translucency over `/skyline-w-chats.png`) | Graphite flat-color bench, no photo, no blur, hairline-engraved zones | This phase (Phase 20) | Removes `background-attachment: fixed` photo + `backdrop-filter: blur(24px)` from every panel — likely a meaningful paint-performance win on the 4GB dev machine, and removes the ~2.5MB `skyline-w-chats.png` asset from the shipped bundle entirely |
| Top horizontal `TopNav` | Fixed 56px left `.rail` (collapses to a bottom bar under 900px per `app.css`) | This phase | IA change — `TopNav.tsx` is fully replaced, not re-themed |
| Fraunces display serif for headings | Space Grotesk (display) + Newsreader italic serif (judge/verdict voice only, per MESH.md law #4) | This phase | Font `<link>` tags in `layout.tsx` must be swapped, and Fraunces should be fully removed if unused elsewhere |
| CDN-loaded three.js inside a static HTML prototype | npm-installed `three`, code-split per route via client-only dynamic import | This phase | Removes runtime CDN dependency; enables version pinning and offline dev |

**Deprecated/outdated:**
- "Hillbrow at Dusk" / skyline-photo design system (Phase 11): retired by this phase's UI2-07 requirement. The `wchats-design` skill (`.claude/skills/wchats-design/`) documents the OLD system — do not consult it for new design decisions in this phase; it is a Phase-11 artifact, not a Gotham source.
- `prototypes/dusk-hybrid/` and `prototypes/dusk-console/` (containing literal "amber-console" tokens): older, unrelated prototype explorations, not referenced by `apps/admin` and not part of the Gotham cutover — do not port anything from them.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | No dedicated Gotham "auth" HTML prototype exists; the recommendation to mount `SceneMount` behind sign-in/sign-up (reusing the landing pattern) is a research inference, not something demonstrated in `prototypes/gotham/`. | Routed Pages / Open Questions | If the planner instead expects a literal `auth.html` source to port from, none exists — this needs a locked decision in planning, not further research (flagging as Open Question below, not a hard recommendation). |
| A2 | `three@0.185.1` (current npm latest) is assumed to render the existing `scene.js` shader/material code correctly with no API-breaking changes since r161. Verified only that the package is legitimate and current; NOT verified that the specific `ShaderMaterial`/`AdditiveBlending`/`PointsMaterial` calls in `scene.js` are byte-for-byte compatible across that version range. | Standard Stack / Pitfall 3 | If a breaking change exists (e.g., color-space defaults), the rendered specimen may look visually different from the approved prototype until manually corrected — this is called out explicitly as Pitfall 3 with a verification step, not silently assumed safe. |
| A3 | The `AlertsBanner`'s two alert types (`eval_regression`, `red_team_critical`) map onto the Gotham design as: `red_team_critical` → gate shutter, `eval_regression` → a Judgement-region chip. This mapping is a research recommendation, not something explicit in any Gotham prototype file (no literal "alert" text found in `agent.html`). | Operations Room / Summary | If the planner/owner intends a different treatment (e.g., a dedicated alerts region), this needs to be a locked decision at plan time — flagged as Open Question. |

**If this table is empty:** N/A — see rows above; all three are inference-level product-flow decisions, not unverified factual/technical claims (the technical claims — token names, endpoint paths, package versions — were all directly verified against the codebase or npm registry).

## Open Questions

1. **Does the "three.js confined to landing + auth only" design law require an actual specimen mount on sign-in/sign-up, or just token/palette parity?**
   - What we know: CONTEXT.md states the law explicitly ("three.js confined to landing + auth only"); no Gotham prototype HTML file demonstrates an auth-page three.js mount (the file list is index/agents/agent-new/agent/soul/ingest/eval/deploy/settings/console — no `auth.html`).
   - What's unclear: Whether "auth" here means "the specimen may ALSO appear on auth pages if desired, but is prohibited everywhere else" (a permission/boundary statement) vs. "the specimen MUST appear on auth pages" (a requirement).
   - Recommendation: Read it as a boundary, not a requirement — the ROADMAP's UI2-02 requirement only names "landing" for the three.js specimen. Ship `SceneMount` on landing only for Wave 2; treat mounting it behind sign-in/sign-up as an optional enhancement the planner can explicitly scope in or out, not a blocking gap.

2. **Where does `AlertsBanner`'s functionality live in the Gotham IA?**
   - What we know: `GET /agents/{id}/alerts` + `POST .../alerts/{id}/resolve` are real, working endpoints (`observability.py:20,48`); no Gotham prototype page has literal alert UI.
   - What's unclear: Whether Wave 3 (operations room) should build a new alerts-specific region/strip, fold `red_team_critical` into the gate shutter and `eval_regression` into Judgement (this research's recommendation), or keep a Gotham-skinned version of the current `AlertsBanner` component verbatim as a strip above the six regions.
   - Recommendation: Fold into the gate (critical) + Judgement chip (eval regression) per the "colour is a verdict" law — a standalone alerts banner would reintroduce a decorative/duplicate status surface the Gotham system's minimalism argues against. Planner should confirm this interpretation with the user if there's any doubt, since it's a product-flow call, not a pure technical one.

3. **Does `soul.html`'s VESSEL three.js mount (config-as-shader-uniforms) get ported in Phase 20?**
   - What we know: `soul.html` loads `scene.js` and presumably calls `mountGotham`; CONTEXT.md's locked decision restricts three.js to "landing + auth only."
   - What's unclear: Nothing — this is actually resolved by the locked decision, but flagged here because it's easy to miss during execution (the temptation to port `soul.html` faithfully, three.js and all, contradicts the design law).
   - Recommendation: Rebuild `soul/page.tsx` from `soul.html`'s tokens/layout/copy, but explicitly DROP the `scene.js` mount and any VESSEL-specific canvas markup. This is already captured as a structural note in Recommended Project Structure above.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js | Next.js dev/build, Playwright | ✓ | v22.17.0 | — |
| `three` (npm) | Landing/auth specimen | ✗ (not yet installed) | — | Install per Standard Stack; no fallback needed, package confirmed legitimate |
| `@playwright/test` (npm, Node) | Route smoke / viewport-overflow / a11y checks (UI2-08 validation) | ✗ (not yet installed) | — | Install fresh; DO NOT use the existing Python `playwright` (`scripts/verify_new_page.py`) — confirmed broken in the main shell per user constraint. Run the Node Playwright checks from a subagent, not the primary execution shell, to sidestep the same class of environment issue. |
| Google Fonts CDN (Space Grotesk, Newsreader, JetBrains Mono) | Gotham typography | ✓ (already fetched via `<link>` for Fraunces/Inter/JetBrains Mono pattern; same CDN, new family names) | — | — |
| FastAPI backend (`apps/api`, local uvicorn) | All routed pages' data | Not probed this session (out of scope for a pure-frontend research pass) — assume available per project's standard local dev flow (`uvicorn`, no Docker) | — | — |

**Missing dependencies with no fallback:**
- None — both new dependencies (`three`, `@playwright/test`) are standard `pnpm add`/`pnpm add -D` installs with no environment blockers identified.

**Missing dependencies with fallback:**
- Python Playwright (broken launcher) → replaced entirely by Node `@playwright/test`, run from a subagent context per the task's stated constraint.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `@playwright/test` 1.61.1 (Node) — none currently installed; this is a Wave 0 gap |
| Config file | none yet — create `apps/admin/playwright.config.ts` in Wave 0 |
| Quick run command | `npx playwright test --grep @smoke` (per-route load + no-console-error check) |
| Full suite command | `npx playwright test` (includes viewport-overflow + axe a11y specs) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| UI2-01 | `globals.css` exposes Gotham tokens; no dusk token/class remains in the bundle | static/build | `grep -rn "var(--\(bg-deep\|glass-\|accent\|lilac\|cyan\|amber\)" apps/admin/app/ apps/admin/.next/static/ ; test $? -ne 0` (grep exit 1 = no matches = pass) + `pnpm --filter wchats-admin build` succeeds | ❌ Wave 0 (add as an npm script `check:no-dusk-tokens`) |
| UI2-02 | Landing renders as a real routed page; three.js specimen renders on landing only | e2e (route smoke) | `npx playwright test landing.spec.ts` — assert `<canvas>` present on `/`, absent on `/agents` | ❌ Wave 0 |
| UI2-03 | Agents dashboard is a real routed page reading `GET /agents` | e2e | `npx playwright test agents-dashboard.spec.ts` — mock/stub or hit local API, assert agent cards render | ❌ Wave 0 |
| UI2-04 | Provisioning flow (create→provision→ingest→deploy) unregressed; steps 2–4 locked until step 1 done | e2e | `npx playwright test provisioning.spec.ts` — drive the existing form, assert stepper `locked`/`active`/`done` states match `deriveStepState` logic in `layout.tsx` | ❌ Wave 0 |
| UI2-05 | Operations room renders six regions; backed regions show data, unbacked regions show honest empty states | e2e | `npx playwright test operations-room.spec.ts` — assert all six `<section aria-labelledby>` ids present; assert Judgement/Adversary show data-driven content, Live/Retrieval-health/bench/prompt show empty-state copy referencing Phase 21 | ❌ Wave 0 |
| UI2-06 | Widget preview (decorative) still present in Deploy | e2e | `npx playwright test deploy.spec.ts` — assert `.preview-panel`-equivalent element present, `aria-hidden="true"` | ❌ Wave 0 |
| UI2-07 | No `dusk-*`/skyline/`amber-console` reference in production bundle | static/build | `grep -rn "skyline-w-chats\|amber-console\|dusk-" apps/admin/app/ apps/admin/public/ ; test $? -ne 0` (also confirm `/skyline-w-chats.png` deleted from `public/`) | ❌ Wave 0 |
| UI2-08 | `prefers-reduced-motion` skips shutter repaint + row fades; no horizontal overflow at 1440/1280/900 | e2e (a11y + viewport) | `npx playwright test reduced-motion.spec.ts` (emulate `prefers-reduced-motion: reduce`, assert transition-duration ≈ 0/1ms) + `npx playwright test overflow.spec.ts` (three viewport sizes, assert `scrollWidth <= clientWidth` on every route) + `npx playwright test a11y.spec.ts` (`AxeBuilder` per route, zero critical/serious violations) | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pnpm --filter wchats-admin build` (fast, catches SSR crashes / TS errors) + the relevant single Playwright spec for the route just touched
- **Per wave merge:** Full `npx playwright test` suite (all routes, all three viewports, axe pass)
- **Phase gate:** Full suite green + the two static grep checks (no-dusk-tokens, no-skyline-asset) before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `apps/admin/playwright.config.ts` — base config: `webServer` block to boot `pnpm --filter wchats-admin dev` on `localhost:3000`, three named viewport projects (1440/1280/900)
- [ ] `apps/admin/e2e/` directory with the six spec files named above
- [ ] `pnpm add -D @playwright/test @axe-core/playwright` + `npx playwright install chromium` (run once, from a subagent given the broken-launcher constraint)
- [ ] `package.json` script: `"check:no-dusk-tokens"` wrapping the grep assertion for UI2-01/UI2-07
- Framework install: `cd apps/admin && pnpm add -D @playwright/test @axe-core/playwright && npx playwright install chromium`

## Security Domain

> `security_enforcement` not found as `false` in `.planning/config.json` — treated as enabled. This phase is pure frontend re-skin with zero new data flows, auth logic, or input surfaces; ASVS applicability is minimal but recorded for completeness.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (unchanged) | Clerk `<SignIn>`/`<SignUp>` components untouched — only `appearance`/`localization` theming changes, no auth-logic changes |
| V3 Session Management | no (unchanged) | `getToken()`/Bearer-header pattern preserved verbatim per Pattern 3 above |
| V4 Access Control | no (unchanged) | No route-guard logic is being modified; Clerk middleware (if any, not inspected this session — out of scope for a pure-frontend phase) is untouched |
| V5 Input Validation | no new surface | No new form fields introduced; existing `agent-new` form fields ported verbatim |
| V6 Cryptography | n/a | No crypto in this phase |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Third-party script injection via a CDN-loaded three.js | Tampering | Resolved by this phase's own recommendation: install `three` as a pinned npm dependency instead of the prototype's `unpkg.com` CDN `import()`, removing the runtime third-party-script trust boundary entirely |
| XSS via `dangerouslySetInnerHTML` (already present in `agent-new/page.tsx` for a `<style>` block) | Tampering / Injection | The existing usage injects a static string literal (keyframe CSS), not user input — confirm any Gotham-ported equivalent (`agent.html`'s inline `<script>` blocks) also only injects static content, never interpolates fetched/user data into `dangerouslySetInnerHTML` or raw `innerHTML` |

## Sources

### Primary (HIGH confidence)
- `apps/admin/app/globals.css` — direct read, full dusk token inventory
- `apps/admin/app/layout.tsx` — direct read, Clerk appearance + font links
- `apps/admin/app/agents/[id]/layout.tsx` — direct read, canonical data-fetching pattern
- `apps/admin/app/agents/new/page.tsx` — direct read, provisioning flow + inline-style pattern
- `apps/admin/app/agents/[id]/deploy/page.tsx` — direct read, widget preview mock confirmed decorative
- `apps/admin/app/agents/[id]/components/AlertsBanner.tsx` — direct read, alerts data contract
- `apps/admin/package.json`, `tailwind.config.ts`, `postcss.config.mjs`, `next.config.mjs` — direct read, stack confirmation
- `prototypes/gotham/tokens.css` — direct read, full Gotham token inventory + gate mechanism
- `prototypes/gotham/app.css` — direct read, component-class system + existing reduced-motion/responsive rules
- `prototypes/gotham/scene.js` — direct read, three.js specimen implementation
- `prototypes/gotham/MESH.md` — direct read, design law + confirmed "Brass on Petrol" staleness
- `prototypes/gotham/AGENT-OPS.md` — direct read, six-region research rationale
- `prototypes/gotham/index.html` — direct read, landing structure + gate-flip JS
- `.planning/AGENT-MGMT-GAPS.md` — direct read, per-region backend verdicts
- `apps/api/app/api/v1/observability.py`, `deployment.py`, `red_team.py`, `evals.py` — direct grep, confirmed real endpoint paths
- `.planning/ROADMAP.md` Phase 20/21 — direct read, requirement IDs + wave structure
- `.planning/phases/20-.../20-CONTEXT.md` — direct read, locked decisions
- `npm view three version` / `time.created`, `npm view @types/three ...`, `npm view @playwright/test version`, `npm view @axe-core/playwright version` — direct tool verification, 2026-07-15
- `gsd-tools query package-legitimacy check --ecosystem npm three @types/three` — direct tool verification, 2026-07-15

### Secondary (MEDIUM confidence)
- WebSearch: "Next.js App Router three.js client component dynamic import ssr false best practice avoid hydration mismatch" — confirmed the client-wrapper pattern for `ssr:false` inside App Router (cross-checked against multiple 2026 sources; general community consensus, not a single official doc page)

### Tertiary (LOW confidence)
- None used as a basis for a recommendation without corroboration.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every version number was verified via `npm view` against the live registry this session, not recalled from training data
- Architecture: HIGH — the inline-style-over-CSS-vars pattern, the Tailwind-is-barely-used finding, and the endpoint inventory are all direct file reads/greps of this exact codebase, not generic Next.js guidance
- Pitfalls: HIGH for the token/silent-failure and gate-ownership pitfalls (grounded in direct comparison of the two token files); MEDIUM for the three.js version-drift pitfall (the specific breaking-change claim about color-management defaults is general three.js knowledge, not verified against a r161→r185 changelog diff this session)

**Research date:** 2026-07-15
**Valid until:** 30 days (stable — no fast-moving external APIs beyond three.js's own release cadence; re-verify `npm view three version` if execution starts more than ~4 weeks after this research)
