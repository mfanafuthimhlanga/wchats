---
phase: 12-production-go-live-deploy-the-w-chats-api-and-celery-workers
plan: "02"
subsystem: widget-delivery
tags: [widget, vercel, static-hosting, preact, embed]
dependency_graph:
  requires: []
  provides: [apps/admin/public/wchats/, widget-embed-vercel-delivery]
  affects: [bantuson.vercel.app/wchats/]
tech_stack:
  added: [pnpm-workspace-for-widget]
  patterns: [next-js-public-static-delivery, runtime-data-api-attribute, preact-iife-bundle]
key_files:
  created:
    - apps/admin/public/wchats/widget.js
    - apps/admin/public/wchats/index.html
    - apps/admin/public/wchats/widget.iife.js
    - apps/admin/public/wchats/widget.css
    - apps/widget/pnpm-lock.yaml
  modified:
    - apps/widget/embed/widget.iife.js
    - apps/widget/embed/widget.css
decisions:
  - "[12-02] Bundle sizes differ from RESEARCH.md baseline: new pnpm build (pnpm v11 replacing prior npm-installed modules) produced 20,835 B iife.js vs prior 17,833 B — gzip 8,087 B remains well under 20,480 B gate; new sizes recorded as authoritative"
  - "[12-02] pnpm-lock.yaml created in apps/widget/ — pnpm install ran fresh as part of build; committed alongside bundle to lock dependency versions"
metrics:
  duration: "~10 min"
  completed: "2026-05-29T14:03:57Z"
  tasks: 2
  files: 7
---

# Phase 12 Plan 02: Widget Embed Published to Vercel public/ Summary

Widget bundle verified current via fresh pnpm build, then all four embed files copied byte-identical into apps/admin/public/wchats/ for zero-config Vercel static delivery at https://bantuson.vercel.app/wchats/.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Rebuild widget bundle (pnpm) and sync embed/ (D-08) | 9d8ab27 | apps/widget/embed/widget.iife.js, apps/widget/embed/widget.css, apps/widget/pnpm-lock.yaml |
| 2 | Copy embed files into apps/admin/public/wchats/ (D-06, D-07) | ab943a1 | apps/admin/public/wchats/{widget.js,index.html,widget.iife.js,widget.css} |

## Acceptance Criteria Verified

### D-08: Bundle verified current (Task 1)

- `pnpm build` from `apps/widget/` exited 0
- `apps/widget/dist/widget.iife.js` and `apps/widget/dist/widget.css` produced
- Synced to `apps/widget/embed/widget.iife.js` and `apps/widget/embed/widget.css`
- Gzip size: 8,087 bytes < 20,480 byte limit (passes size gate)
- No npm or yarn invoked — pnpm only throughout

Bundle size baseline update (RESEARCH.md stated 17,833 B; pnpm v11 produced 20,835 B):
- pnpm moved prior npm-installed node_modules to `.ignored`, then installed fresh
- The output is a different minification run (same source, same terser config)
- Gzip remains 8,087 B — the deliverable requirement (< 20 KB gzip) is satisfied

### D-06 + D-07: Vercel delivery ready (Task 2)

- All four files present at `apps/admin/public/wchats/`:
  - `widget.js` (5,093 B) — loader that reads `data-agent` and `data-api` attributes at runtime
  - `index.html` (628 B) — iframe host with relative `./widget.iife.js` and `./widget.css` references
  - `widget.iife.js` (20,835 B) — compiled Preact bundle
  - `widget.css` (5,141 B) — widget stylesheet
- All four files are byte-identical to `apps/widget/embed/` sources (`diff` produced no output)
- `widget.js` reads `data-api` at runtime — no VM hostname baked into any copied file
- Next.js serves `apps/admin/public/` at site root with zero config — files will be live at
  `https://bantuson.vercel.app/wchats/<file>` on Vercel auto-deploy

## Canonical Paste-In Snippet (D-07)

```html
<script src="https://bantuson.vercel.app/wchats/widget.js"
        data-agent="fe230a9d-09f0-4043-b2f1-4506a2ef0059"
        data-api="https://wchats-api.duckdns.org"
        async></script>
```

- `data-agent` — the deployed agent id (live: `fe230a9d-09f0-4043-b2f1-4506a2ef0059`)
- `data-api` — the DuckDNS subdomain from plan 05 (placeholder; set to real subdomain after VM provisioning in plan 04/05)
- Repointing the API host requires only changing `data-api` — no rebuild, no redeployment

## Deviations from Plan

### Auto-noted Size Change

**1. [Rule 0 - Expected] Bundle size increased from RESEARCH.md baseline**
- **Found during:** Task 1
- **Issue:** RESEARCH.md stated widget.iife.js = 17,833 B, widget.css = 4,711 B. Fresh pnpm build produced 20,835 B and 5,141 B respectively.
- **Cause:** pnpm v11 replaced the prior npm-installed node_modules with fresh installs, producing a different minification run under the same terser config. The widget source code was unchanged.
- **Outcome:** Gzip target (< 20 KB) is met at 8,087 B; the build postbuild check-size.mjs printed "Bundle size OK: 8094 bytes". New sizes are now the authoritative baseline.
- **Files modified:** apps/widget/embed/widget.iife.js, apps/widget/embed/widget.css (synced from dist)

## Known Stubs

None — all four embed files are fully wired (no placeholder text, no hardcoded empty values, no TODO markers in copied files). The `data-api` value in the snippet above uses the DuckDNS placeholder `wchats-api.duckdns.org` which is documented explicitly as a to-be-confirmed hostname; this is intentional handoff documentation, not a stub in a deployed file.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. The four files added to `apps/admin/public/wchats/` are public static assets already described in the plan's threat model (T-12-02-01 through T-12-02-03, all mitigated/accepted). No new threat flags.

## Self-Check: PASSED

- apps/admin/public/wchats/widget.js: FOUND
- apps/admin/public/wchats/index.html: FOUND
- apps/admin/public/wchats/widget.iife.js: FOUND
- apps/admin/public/wchats/widget.css: FOUND
- Commit 9d8ab27: FOUND (chore: rebuild widget bundle via pnpm)
- Commit ab943a1: FOUND (feat: publish widget embed to apps/admin/public/wchats/)
