---
phase: "04-reasoning-engine-widget"
plan: "06"
subsystem: "admin-ui + agent-api"
tags: ["soul-editor", "nextjs", "patch-route", "design-g", "tdd"]
dependency_graph:
  requires: ["04-01", "04-04"]
  provides: ["PATCH /agents/{id}", "apps/admin Soul Editor", "AgentSoulUpdate schema"]
  affects: ["agent soul fields", "admin UI surface", "system prompt assembly"]
tech_stack:
  added:
    - "Next.js 16.2.6 (App Router, Turbopack)"
    - "React 19.2.0"
    - "Tailwind CSS 4.1.16 + @tailwindcss/postcss"
    - "next/font/google (Inter + JetBrains Mono)"
    - "Pydantic v2 AgentSoulUpdate + AgentDetailResponse"
  patterns:
    - "TDD RED/GREEN on PATCH route (6 tests)"
    - "ASGITransport + AsyncClient + dependency_overrides"
    - "model_dump(exclude_unset=True) partial update semantics"
    - "sessionStorage for admin API key (T-04-06-02)"
    - "CSS custom properties (Design G tokens) in globals.css"
    - "buildSystemPromptPreview TypeScript port of Python build_system_prompt"
key_files:
  created:
    - "apps/api/tests/unit/test_agents_patch.py"
    - "apps/api/app/schemas/agent.py (AgentSoulUpdate + AgentDetailResponse added)"
    - "apps/admin/package.json"
    - "apps/admin/next.config.mjs"
    - "apps/admin/tsconfig.json"
    - "apps/admin/postcss.config.mjs"
    - "apps/admin/tailwind.config.ts"
    - "apps/admin/app/globals.css"
    - "apps/admin/app/layout.tsx"
    - "apps/admin/app/agents/[id]/soul/page.tsx"
    - "apps/admin/.gitignore"
    - "apps/admin/.env.example"
  modified:
    - "apps/api/app/api/v1/agents.py (PATCH route appended)"
    - "apps/api/app/schemas/agent.py (AgentSoulUpdate + AgentDetailResponse appended)"
decisions:
  - "@types/react-dom pinned to 19.2.3 (plan specified 19.2.5 which does not exist on npm)"
  - "Next.js auto-updated tsconfig.json: jsx='react-jsx' (was 'preserve'), added .next/dev/types/**/*.ts to include"
  - "Missing API key test asserts status_code in (401, 403) — FastAPI APIKeyHeader auto_error=True raises 403 on missing header, not 401; matches existing test_auth.py pattern"
metrics:
  duration: "949 seconds (~16 minutes)"
  completed_date: "2026-05-16"
  tasks_completed: 2
  files_created: 12
  files_modified: 2
---

# Phase 04 Plan 06: Admin Soul Editor + PATCH /agents/{id} Summary

**One-liner:** PATCH /agents/{id} with AgentSoulUpdate partial-update schema + Next.js 16 admin Soul Editor with Design G tokens, live system prompt preview, and structured Do/Do-Not lists.

---

## PATCH /agents/{id} Request/Response Shape

```
PATCH /api/v1/agents/{agent_id}
Headers:
  X-API-Key: <tenant key>
  Content-Type: application/json

Body (all fields optional — partial update semantics):
  {
    "name":          string | null   (1-60 chars, min_length=1)
    "soul_role":     string | null   (max 120 chars)
    "soul_voice":    string | null   (max 500 chars)
    "soul_do_list":  string[] | null (empty strings stripped server-side)
    "soul_donot_list": string[] | null (empty strings stripped server-side)
  }

Response 200:
  {
    "id":              UUID
    "name":            string
    "soul_role":       string | null
    "soul_voice":      string | null
    "soul_do_list":    string[]
    "soul_donot_list": string[]
    "status":          string
    "created_at":      datetime
  }

Errors:
  404 — agent not found or does not belong to authenticated tenant
  422 — Pydantic validation failure (e.g. name="" violates min_length=1)
  401 — invalid X-API-Key
  403 — missing X-API-Key header (FastAPI APIKeyHeader auto_error=True)
```

**Auth model:** X-API-Key header → `get_current_tenant` → HMAC prefix lookup + argon2 verify → Tenant row. Agent ownership enforced by `WHERE id=agent_id AND tenant_id=tenant.id AND deleted_at IS NULL`.

---

## AgentSoulUpdate Constraint Summary

| Field | Type | Constraints |
|-------|------|-------------|
| name | `str \| None` | optional, min_length=1, max_length=60 |
| soul_role | `str \| None` | optional, max_length=120 |
| soul_voice | `str \| None` | optional, max_length=500 |
| soul_do_list | `list[str] \| None` | optional; empty/whitespace strings stripped server-side |
| soul_donot_list | `list[str] \| None` | optional; empty/whitespace strings stripped server-side |

Uses `model_dump(exclude_unset=True)` — only fields present in the JSON body are applied. Missing fields leave the agent row unchanged.

---

## Admin App File List + Build Status

**Build output:** `npm run build` exits 0 (Turbopack)

| File | Role |
|------|------|
| `apps/admin/package.json` | Next 16.2.6 + React 19.2.0 + TS + Tailwind 4.x |
| `apps/admin/next.config.mjs` | `{ reactStrictMode: true }` |
| `apps/admin/tsconfig.json` | Strict TS, bundler module resolution, @/* alias |
| `apps/admin/postcss.config.mjs` | @tailwindcss/postcss plugin |
| `apps/admin/tailwind.config.ts` | content: ./app/**/*.{ts,tsx} |
| `apps/admin/app/globals.css` | Design G CSS tokens + @import "tailwindcss" |
| `apps/admin/app/layout.tsx` | RootLayout, Inter + JetBrains Mono via next/font/google |
| `apps/admin/app/agents/[id]/soul/page.tsx` | Soul Editor client component |
| `apps/admin/.gitignore` | excludes node_modules/, .next/, *.log, .env.local |
| `apps/admin/.env.example` | NEXT_PUBLIC_API_BASE=http://localhost:8000 |

Build route: `ƒ /agents/[id]/soul` (Dynamic — server-rendered on demand)

---

## Soul Editor Field Map

| Form Field | API Body Field | Agent Model Column |
|------------|---------------|-------------------|
| Agent Name input | `name` | `agents.name` |
| Role input | `soul_role` | `agents.soul_role` |
| Voice & Tone textarea | `soul_voice` | `agents.soul_voice` |
| Do List inputs (array) | `soul_do_list` | `agents.soul_do_list` (JSONB) |
| Do-Not List inputs (array) | `soul_donot_list` | `agents.soul_donot_list` (JSONB) |

---

## Live Preview Function

**Function name:** `buildSystemPromptPreview`

**Location:** `apps/admin/app/agents/[id]/soul/page.tsx` (top-level function, defined before the component)

**Invocation point:** `const preview = buildSystemPromptPreview({ name, soul_role: soulRole, soul_voice: soulVoice, soul_do_list: soulDoList, soul_donot_list: soulDonotList })` — computed on every render, displayed in the `<pre>` element in the Live Preview Panel.

**Pattern:** Pure function, TypeScript port of Python `build_system_prompt` from `apps/api/app/services/agent_prompt.py`. Logic is mirrored client-side (not an RPC call) for zero-latency live preview.

---

## Unit Test Count

**6 tests** in `apps/api/tests/unit/test_agents_patch.py`:

| # | Test Name | Covers |
|---|-----------|--------|
| 1 | `test_patch_agent_soul_full_update` | 200 with all 4 soul fields updated |
| 2 | `test_patch_agent_soul_partial_update` | Only soul_voice changed; soul_role unchanged |
| 3 | `test_patch_agent_strips_empty_list_items` | Empty/whitespace strings stripped from soul_do_list |
| 4 | `test_patch_agent_not_owned_returns_404` | Cross-tenant access blocked |
| 5 | `test_patch_agent_missing_api_key_returns_401` | Missing header → 401 or 403 |
| 6 | `test_patch_agent_empty_name_returns_422` | name="" → 422 (min_length=1) |

All 6 pass: `6 passed in 3.55s`

---

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] @types/react-dom version 19.2.5 does not exist on npm registry**
- **Found during:** Task 2 (npm install)
- **Issue:** Plan specified `"@types/react-dom": "19.2.5"` but that version was never published. Latest at execution time was 19.2.3.
- **Fix:** Updated to `"@types/react-dom": "19.2.3"` — closest available version
- **Files modified:** `apps/admin/package.json`

### Auto-modified by tooling

**2. tsconfig.json auto-updated by Next.js 16 build**
- **What changed:** `"jsx": "preserve"` → `"jsx": "react-jsx"` (Next.js requirement); `".next/dev/types/**/*.ts"` added to `include` array
- **Impact:** None — these are correct values for Next.js App Router
- **Committed as-is** — changes are correct and expected

### Test deviation: test 5 uses `in (401, 403)` not `== 401`

The plan says test 5 checks for 401. FastAPI's `APIKeyHeader(auto_error=True)` raises HTTP 403 when the header is absent (not 401 — 401 is returned when the key is present but invalid). The test asserts `status_code in (401, 403)` to match the existing project pattern in `test_auth.py`. This is behaviorally correct.

---

## Known Stubs

None. The Soul Editor fetches live data from the API via GET /agents/{id} and PATCHes via the backend. The live preview is a computed function, not stubbed data.

---

## Threat Flags

None — no new network endpoints beyond the planned PATCH route. No new auth paths or trust boundaries beyond those in the plan's threat model.

---

## Self-Check: PASSED

Files verified:
- `apps/api/tests/unit/test_agents_patch.py` — exists
- `apps/api/app/schemas/agent.py` — contains AgentSoulUpdate and AgentDetailResponse
- `apps/api/app/api/v1/agents.py` — contains @router.patch("/agents/{agent_id}"
- `apps/admin/app/agents/[id]/soul/page.tsx` — exists, starts with 'use client'
- `apps/admin/app/globals.css` — contains --accent: #7B1C3A
- `apps/admin/package.json` — contains "next": "16.2.6"

Commits verified:
- `142550a` — test(04-06): TDD RED tests
- `432131c` — feat(04-06): PATCH route + schema (GREEN)
- `1634670` — feat(04-06): Next.js admin scaffold
