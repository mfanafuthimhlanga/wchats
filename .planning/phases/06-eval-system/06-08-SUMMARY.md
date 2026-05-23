---
phase: "06"
plan: "06-08"
title: "Next.js eval dashboard — Recharts charts + scenarios table"
status: complete
completed_at: "2026-05-23"
---

## What Was Built

Replaced the "Coming soon" stub at `apps/admin/app/agents/[id]/eval/page.tsx`
with a fully functional eval dashboard. Installed recharts 3.8.1 via pnpm.

## Tasks Completed

### Task 1 — Install recharts dependency
- Ran `pnpm add recharts` from `apps/admin/`
- `apps/admin/package.json` now includes `"recharts": "^3.8.1"`
- `apps/admin/node_modules/recharts/` present; `pnpm list recharts` confirms 3.8.1

### Task 2 — Replace eval page stub with full dashboard
- `apps/admin/app/agents/[id]/eval/page.tsx` fully replaced (951 lines added)
- `'use client'` component using `use(params)` to unwrap async route params
- Two-tab layout: **Pass Rates** | **Scenarios** with `useState<'passrates' | 'scenarios'>`
- Data fetching via TanStack `useQuery` + `useAuth` Bearer token pattern matching
  `apps/admin/app/agents/page.tsx`
- Queries: `GET /api/v1/agents/{id}/eval-runs` and
  `GET /api/v1/agents/{id}/eval-runs/{run_id}/results`
- Recharts `LineChart` with four locked metric lines:
  - Faithfulness `#B8860B` (gold)
  - Answer Relevancy `#7B1C3A` (wine)
  - Context Precision `#4A7C59` (sage)
  - Context Recall `#4A6080` (slate)
- `ReferenceLine y={0.9}` threshold indicator
- Schedule strip showing last run time and next-run countdown
- Run Now button → `POST /api/v1/agents/{id}/eval-runs/trigger` with 5s polling
- Empty state with 📊 icon, locked copy, "▶ Run First Eval" CTA
- Loading skeletons using `@keyframes shimmer` from `globals.css`
- Full Design G token compliance (`--bg`, `--surface-*`, `--border`, `--accent`, etc.)
- TypeScript compilation passes: `pnpm build` exits 0

## Acceptance Criteria

- [x] `apps/admin/package.json` contains `"recharts"` key
- [x] `apps/admin/node_modules/recharts/` exists
- [x] `pnpm list recharts` shows recharts 3.8.1
- [x] `'use client'` at top of eval page
- [x] Imports `ResponsiveContainer`, `LineChart`, `Line` from recharts
- [x] Four `Line` components: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`
- [x] `stroke="#B8860B"`, `stroke="#7B1C3A"`, `stroke="#4A7C59"`, `stroke="#4A6080"` present
- [x] `ReferenceLine y={0.9}` present
- [x] h1 text: "Run evaluations"
- [x] Subtitle: "Ragas-scored nightly tests for your agent's knowledge base."
- [x] "Pass Rates" and "Scenarios" tabs present
- [x] "No eval runs yet" empty state title present
- [x] `useQuery` from `@tanstack/react-query`
- [x] `useAuth` from `@clerk/nextjs`
- [x] `Authorization: \`Bearer ${token}\`` in fetch headers
- [x] "Coming soon" placeholder fully removed
- [x] `pnpm build` exits 0 (TypeScript compilation passes)

## Key Decisions

- Tooltip `formatter` typed as `any` with runtime check (`typeof v === 'number'`)
  to satisfy Recharts v3 generic `Formatter<ValueType, NameType>` signature
  (ValueType includes `undefined` in strict mode)
- Chart data reversed before mapping so oldest run appears leftmost on the X-axis
- Row hover implemented via `onMouseEnter`/`onMouseLeave` inline (no CSS module)
  since the admin uses inline styles throughout

## Commits

1. `feat(06-08): install recharts 3.8.1 via pnpm for eval dashboard charts`
2. `feat(06-08): replace eval page stub with full Ragas dashboard`

## Files Modified

- `apps/admin/package.json` — recharts dependency added
- `apps/admin/pnpm-lock.yaml` — lockfile updated
- `apps/admin/app/agents/[id]/eval/page.tsx` — full dashboard (replaces stub)
