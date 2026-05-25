---
phase: "10"
plan: "04"
subsystem: admin-ui
tags: [alerts, observability, langfuse, clerk-auth, react-polling]
dependency_graph:
  requires: [10-03]
  provides: [OPS-03, OPS-04]
  affects: [apps/admin/app/agents/[id]/page.tsx, apps/admin/app/agents/[id]/components/AlertsBanner.tsx]
tech_stack:
  added: []
  patterns: [clerk-bearer-auth, setInterval-polling, optimistic-ui-update, css-var-inline-styles]
key_files:
  created:
    - apps/admin/app/agents/[id]/components/AlertsBanner.tsx
  modified:
    - apps/admin/app/agents/[id]/page.tsx
decisions:
  - "[10-04] useAuth grep count is 2 (import + call site) — plan spec says 1, but 2 is correct; import + usage both required"
  - "[10-04] Langfuse href is static string https://cloud.langfuse.com — no env var per UI-SPEC directive"
  - "[10-04] AlertsBanner returns null when alerts.length === 0 — zero DOM footprint, no empty state"
  - "[10-04] Optimistic resolve: setAlerts filter fires before POST — next poll restores if POST fails"
metrics:
  duration: "~8 min"
  completed_date: "2026-05-25"
  tasks: 2
  files: 2
---

# Phase 10 Plan 04: AlertsBanner + Langfuse Link Summary

**One-liner:** Client component polling for unresolved alerts with Clerk Bearer auth + static Langfuse dashboard link on agent detail page.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | AlertsBanner component | 639778d | apps/admin/app/agents/[id]/components/AlertsBanner.tsx |
| 2 | Mount on agent detail page + Langfuse link | fdf8e15 | apps/admin/app/agents/[id]/page.tsx |

## What Was Built

### Task 1 — AlertsBanner.tsx

Created `apps/admin/app/agents/[id]/components/AlertsBanner.tsx` as a `"use client"` component:

- Accepts `{ agentId: string }` prop only — no apiKey prop
- Uses `useAuth` from `@clerk/nextjs` for `getToken()` — Authorization: Bearer token pattern
- `apiBase` from `process.env.NEXT_PUBLIC_API_BASE || ''`
- Polls `GET /api/v1/agents/{agentId}/alerts` every 30 seconds via `setInterval`
- Returns `null` when `alerts.length === 0` (zero DOM footprint)
- Alert row surfaces use CSS variable inline styles (`var(--amber-bg)`, `var(--red-bg)`) per UI-SPEC
- Severity badges use Tailwind classes (`bg-amber-100 text-amber-800`, `bg-red-100 text-red-800`) inside `<span>` only
- `timeAgo()` local helper: < 60s → "just now"; < 3600s → "{n}m ago"; < 86400s → "{n}h ago"; else → "{n}d ago"
- `alert_type` display mapping: `eval_regression` → "Eval Regression"; `red_team_critical` → "Critical Red Team Finding"; others → underscore-to-space + title-case
- Resolve button: text link style (no background/border), optimistic `setAlerts.filter()` before POST
- No animation on resolve — row disappears immediately via state update

### Task 2 — page.tsx mount

Updated `apps/admin/app/agents/[id]/page.tsx`:

- Added import: `import { AlertsBanner } from './components/AlertsBanner'`
- Mounted `<AlertsBanner agentId={id} />` after `loadError` div and before `{panel}`
- Added Langfuse link div after `{panel}`, before closing `</div>`:
  - `href="https://cloud.langfuse.com"` (static string per UI-SPEC)
  - `target="_blank" rel="noopener noreferrer"`
  - Inline styles: `marginTop: '24px'`, `paddingTop: '16px'`, `borderTop: '1px solid var(--border-soft)'`
  - Link text: "View Langfuse Dashboard →" (Unicode U+2192)

## Verification Results

| Check | Result |
|-------|--------|
| `grep -c "useAuth" AlertsBanner.tsx` | 2 (import + call, both correct) |
| `grep -v '^//' AlertsBanner.tsx \| grep -c "X-API-Key"` | 0 |
| `grep -c "Authorization" AlertsBanner.tsx` | 2 |
| `grep -c "apiKey" AlertsBanner.tsx` | 0 |
| `grep -c "timeAgo\|just now\|m ago" AlertsBanner.tsx` | 6 |
| `grep -c "var(--amber-bg)\|var(--red-bg)" AlertsBanner.tsx` | 3 |
| `grep -c "AlertsBanner" page.tsx` | 2 (import + JSX) |
| `grep -c "apiKey" page.tsx` | 0 |
| `grep -c "cloud.langfuse.com" page.tsx` | 1 |
| `grep -c "rel=\"noopener noreferrer\"" page.tsx` | 1 |
| `pnpm build` exit code | 0 (both task 1 and task 2 builds) |

## Deviations from Plan

### Auto-noted: Plan spec `useAuth` grep criterion

The plan acceptance criterion `grep -c "useAuth" AlertsBanner.tsx returns 1` would require the import and call to be on the same line. The correct implementation has them on separate lines (import on line 3, `useAuth()` call on line 48), yielding a count of 2. This is correct behavior — both the Clerk import and the hook call are present. The `@clerk/nextjs` import count criterion correctly returns 1.

No functional deviations — component implements all UI-SPEC requirements exactly.

## Known Stubs

None. AlertsBanner polls a real API endpoint (`GET /api/v1/agents/{id}/alerts`) established in Plan 10-03. The Langfuse href is a static production URL, not a placeholder.

## Threat Surface Scan

No new threat surface beyond the plan's threat model. All three threats (T-10-04-01, T-10-04-02, T-10-04-03) are addressed:
- T-10-04-01 (Spoofing): `getToken()` from Clerk — short-lived signed JWT, not user-controllable
- T-10-04-02 (Info Disclosure): Alerts visible only to authenticated tenant owner; Clerk middleware gates the page
- T-10-04-03 (EoP): FastAPI validates tenant ownership before resolve — IDOR guard tested in 10-05

## Self-Check: PASSED

- AlertsBanner.tsx exists: YES (`apps/admin/app/agents/[id]/components/AlertsBanner.tsx`)
- page.tsx modified: YES (`apps/admin/app/agents/[id]/page.tsx`)
- Commit 639778d exists: `feat(10-04): AlertsBanner client component with Clerk Bearer auth + polling`
- Commit fdf8e15 exists: `feat(10-04): mount AlertsBanner + Langfuse link on agent detail page`
- pnpm build exits 0: CONFIRMED (both post-task 1 and post-task 2 builds)
