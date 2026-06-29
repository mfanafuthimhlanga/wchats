---
phase: 13-production-hosting-and-durable-deployment
plan: "05"
subsystem: admin-ui
tags: [embed-snippet, widget, env-vars, deploy-page]
requires: ["13-01"]
provides: ["working-embed-snippet"]
affects: ["apps/admin/app/agents/[id]/deploy/page.tsx"]
tech-stack:
  added: []
  patterns: ["NEXT_PUBLIC_ env vars for build-time config"]
key-files:
  modified:
    - apps/admin/app/agents/[id]/deploy/page.tsx
    - apps/admin/.env.example
decisions:
  - "Fallback for NEXT_PUBLIC_WCHATS_WIDGET_CDN is https://widget.wchats.app so the snippet is always valid HTML even before CloudFront is wired up"
  - "Fallback for NEXT_PUBLIC_WCHATS_API_BASE is empty string (consistent with plan); widget falls back to window.WCHATS_API_BASE resolution chain"
metrics:
  duration: "5 minutes"
  completed: "2026-06-29"
status: complete
---

# Phase 13 Plan 05: Env-Driven Embed Snippet Summary

**One-liner:** Fixed `EMBED_SNIPPET` to emit a real CloudFront `src` and `data-api` from build-time env vars (`NEXT_PUBLIC_WCHATS_WIDGET_CDN`, `NEXT_PUBLIC_WCHATS_API_BASE`), with the CDN-not-live disclaimer removed.

## What Was Built

### Task 1: Emit real src + data-api + data-agent from env, and remove the disclaimer

**Commit:** `5ad99f4`
**Files:** `apps/admin/app/agents/[id]/deploy/page.tsx`, `apps/admin/.env.example`

**Changes:**
- Added two module-level constants above `EMBED_SNIPPET`:
  ```ts
  const WIDGET_CDN_BASE = process.env.NEXT_PUBLIC_WCHATS_WIDGET_CDN || 'https://widget.wchats.app'
  const WIDGET_API_BASE = process.env.NEXT_PUBLIC_WCHATS_API_BASE || ''
  ```
- Rewrote `EMBED_SNIPPET(id)` to produce:
  ```html
  <script src="{WIDGET_CDN_BASE}/widget.js" data-agent="{id}" data-api="{WIDGET_API_BASE}" async></script>
  ```
- Removed the italic disclaimer paragraph that read "Note: The CDN URL above is a preview placeholder. Widget CDN deployment is not yet live..."
- Appended `NEXT_PUBLIC_WCHATS_WIDGET_CDN` and `NEXT_PUBLIC_WCHATS_API_BASE` to `apps/admin/.env.example` with Terraform output provenance comments (`widget_cdn_url`, `api_url` from 13-01)

## Verification

```
SNIPPET_OK
```

All acceptance grep assertions pass:
- `data-api=` present in page.tsx
- `data-agent=` present in page.tsx
- `NEXT_PUBLIC_WCHATS_API_BASE` used in page.tsx
- `NEXT_PUBLIC_WCHATS_WIDGET_CDN` used in page.tsx (line 114)
- `not yet live` count: 0
- `NEXT_PUBLIC_WCHATS_API_BASE` documented in `.env.example`

## Deviations from Plan

None — plan executed exactly as written.

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|-----------|
| T-13-05-01 | `data-api` now always emitted from stable env-configured API host |
| T-13-05-03 | No ephemeral tunnel host — `data-api` comes from `NEXT_PUBLIC_WCHATS_API_BASE` (Terraform `api_url`) |

## Self-Check: PASSED

- `apps/admin/app/agents/[id]/deploy/page.tsx` — modified and committed at `5ad99f4`
- `apps/admin/.env.example` — modified and committed at `5ad99f4`
- Commit `5ad99f4` present in git log
