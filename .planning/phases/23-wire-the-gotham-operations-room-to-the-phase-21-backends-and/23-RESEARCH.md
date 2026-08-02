# Phase 23: Wire the Gotham operations room to the Phase 21 backends - Research

**Researched:** 2026-08-02
**Domain:** Frontend-backend wiring (Next.js/React Query admin console + Preact widget) against already-shipped, already-tested FastAPI/Celery backends. No new backend capability except two named read-path completions.
**Confidence:** HIGH — every claim below was checked directly against source this session (file:line cited throughout); this is a source-verification-heavy phase by its own framing, not a library-research phase. No web search was used or needed (no new library, no new pattern — the house pattern already exists in this codebase). `brave_search`/`firecrawl`/`exa_search` were all unavailable in this session's tool config and were not required.

## Summary

Phase 23 is not a "build" phase. Every backend endpoint it consumes exists, is unit-tested, and (for the five it reads) is covered by `21-SECURITY.md`'s 33/33 closed threats. The work is: (1) replace six hardcoded `<EmptyState>`/false-claim blocks in `apps/admin/app/agents/[id]/page.tsx` with real `useQuery`/`useMutation` calls following the exact pattern already shipped in `apps/agents/[id]/deploy/page.tsx` (React Query v5.100.11, per-row `aria-disabled` in-flight state, staged-confirm for anything live-the-instant-you-click-it, honest sentinel handling); (2) add a feedback control to the Preact widget under the 20 KB gzip budget (currently at 8.09 KB, 11.9 KB of headroom); and (3) close two backend read-path gaps that block WIRE-04/WIRE-05 entirely, both of which are one-field/one-query completions of code that already computes the missing value, not new capability.

The two backend gaps deserve emphasis because they are easy to underscope. Gap A (widget `message_id`) is **not** "add a field to a dict" — `assistant_msg_id` is generated and used entirely inside `_persist_messages()` (`agent.py:281-341`), a function that returns `None`. `run_agent_turn()` (the caller, and the function that owns the `agent.response` emit) has **no access to that value today**. The fix requires changing `_persist_messages`'s return type, updating its one call site (`agent.py:946-952`), and touching the emit at `agent.py:973-983` — and this function is mocked in seven existing unit tests (`test_agent_task.py`) with no `return_value` set, meaning those tests currently receive a `MagicMock` wherever `_persist_messages(...)`'s result is used. This is a small, contained fix, but it is not a one-line one.

Gap B (red-team finding IDs) is exactly as scoped in `23-UI-SPEC.md` §3.2 — verified directly against `redteam_programme_service.py` and migration `0012`: `red_team_findings` has no `description` column, so recovering a human-readable description for the contain UI requires the JSONB-correlation technique already established in `bench_service.py` (per that module's own docstring, cited in `traces.py:14-20`), not a new column.

The stale-verdict bug (§3.3 of the UI-SPEC, `page.tsx:251-260,303`) is a real correctness defect, independently confirmed by reading `page.tsx` in full this session: `redTeamBlocked` derives from `latestRedTeamRun?.deployment_blocked`, a value written once when a red-team run completes and never touched again — containing a finding cannot ever un-stick it under the current code. This must be fixed in the same pass as WIRE-04 or WIRE-04 introduces a new false-verdict class the project has already named and prohibited (`T-22-ACT-17`).

**Primary recommendation:** Treat this as six independent, parallelizable wiring tasks (one per operations-room region) plus one widget task plus the two backend-gap fixes, all following the `deploy/page.tsx` house pattern verbatim — no new frontend architecture, no new library, no new test framework decision beyond what's flagged in Validation Architecture below.

<phase_requirements>
## Phase Requirements

No new REQ-IDs are minted by this phase (per `ROADMAP.md`'s own framing) — it makes existing UI2-05 and OPS-01..16 reachable. The IDs below (WIRE-01..05) are the milestone-audit integration gaps this phase closes, defined in `.planning/v1.2-MILESTONE-AUDIT.md`.

| ID | Description | Research Support |
|----|-------------|------------------|
| WIRE-01 | Six operations-room regions call their real Phase 21 endpoints (currently 4/6 hardcoded `EmptyState`) | Architecture Patterns (house pattern to reuse verbatim), §2 backend response shapes verified per-region in `23-UI-SPEC.md` and independently re-verified against `metrics.py`, `traces.py`, `red_team.py`, `prompt_versions.py` this session |
| WIRE-02 | Judgement region renders the ORRERY ledger counts it already fetches (`evals.py`'s `ledger` field, currently discarded) | Pattern 1 (honest-sentinel rendering, "a zero is not a sentinel"), `evals.py:97-189` verified, Validation Architecture's WIRE-02 test row |
| WIRE-03 | Remove three false "ships in a future release" capability claims | Common Pitfalls (none specific — this is a deletion, not a build); Validation Architecture's static-grep gate row |
| WIRE-04 | Adversary region can contain a finding from the console, clearing the deploy block | Gap B (backend read-path completion), Pitfall 4 (stale-verdict bug that must be fixed in the same pass), Pattern 3 (staged-confirm), Security Domain's stale-gate risk row |
| WIRE-05 | Widget captures feedback, reaching `message_feedback`, bundle stays under 20 KB gzipped | Gap A (backend `message_id` completion, Pitfall 1/2), §6 of `23-UI-SPEC.md` (widget interaction contract), Validation Architecture's widget-gap note |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Live/Retrieval-health/Bench/Judgement/Adversary/Prompt region rendering | Frontend Server (Next.js `apps/admin`) | API/Backend (already built) | Pure consumption of existing REST endpoints via React Query; zero new backend logic |
| `message_id` on `agent.response` SSE | API/Backend (Celery task, `agent.py`) | Browser/Client (widget SSE handler) | The value must exist server-side before any client can read it — Gap A is backend-first even though its consumer is the widget |
| `open_findings` on `GET .../red-team/programme` | API/Backend (`red_team.py`, `redteam_programme_service.py`) | Frontend Server (Adversary region) | Same reasoning — Gap B is a backend query extension consumed by the frontend |
| Contain / canary / rollback staged-confirm actions | Frontend Server (staged-confirm UI state) | API/Backend (the mutating POST) | The "live the instant you click it" pattern is a client-side UX contract over an already-correct, already-secured backend mutation |
| Widget feedback capture | Browser/Client (Preact widget) | API/Backend (`POST /widget/agents/{id}/feedback`, already shipped) | Optimistic UI + POST from an untrusted browser context; JWT auth and rate limiting already enforced server-side |
| Stale-verdict recomputation (gate/severity state) | Frontend Server (`page.tsx` local derived state) | API/Backend (`open_findings`, the new source of truth) | This is a client-side bug (stale local derivation from a snapshot) whose fix is to read a different, already-correct field from the response Gap B adds |

## Package Legitimacy Audit

**Not applicable.** This phase adds zero new npm/pip packages to `apps/admin`, `apps/widget`, or `apps/api`. Verified: `apps/admin/package.json` and `apps/widget/package.json` were read in full this session (see Standard Stack below); no dependency this phase's work requires is absent from either file. The two backend-gap fixes use only `psycopg2`/SQLAlchemy already imported in their respective modules. No `npm install`/`pip install` step belongs in any plan this research supports.

## Standard Stack

### Core (already installed — verified via `apps/admin/package.json` this session, no `npm view` needed since nothing new is added)

| Library | Version (from package.json) | Purpose | Why Standard (for this phase) |
|---------|---------|---------|--------------|
| `@tanstack/react-query` | 5.100.11 | All data fetching/mutation in `apps/admin` | Already the sole data layer on this exact page (`agentQuery`, `evalRunsQuery`, `redTeamQuery`, `checklistQuery` all use it, `page.tsx:141-294`); `deploy/page.tsx` establishes every pattern this phase needs (query, mutation, per-row in-flight state, staged-confirm, 409-as-inline-note) |
| `@clerk/nextjs` | 7.3.5 | `useAuth()` → `getToken()` for every fetch's `Authorization: Bearer` header | Already the sole auth mechanism on every existing query in `page.tsx` |
| `next` | 16.2.6 | App Router, `apps/admin` | Unchanged by this phase |
| `react` / `react-dom` | 19.2.0 | UI runtime | Unchanged |
| `preact` | 10.29.1 (`apps/widget/package.json`) | Widget UI runtime | Unchanged — the widget stays Preact, no React added to it |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `psycopg2` | already a transitive dep of `apps/api` | The two backend-gap SQL completions (Gap A does not need it — no new query; Gap B's `open_findings` query) | Already the exact library every other route in `red_team.py`/`redteam_programme_service.py` uses — no alternative to consider |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| React Query polling (`refetchInterval`) for the bench/adversary "in-flight" states | WebSocket/SSE push | Rejected — this codebase's own established convention (`agentQuery`, `pendingConfirmationsQuery` in `deploy/page.tsx:2137-2143`) is conditional `refetchInterval` polling that stops once nothing is awaiting; introducing SSE here would be new architecture for a problem the existing pattern already solves correctly |
| A new admin unit-test framework (Vitest/Jest + React Testing Library) for render-level assertions | Playwright-only with `page.route()` mocking | Both are viable; see Validation Architecture — this research recommends the Playwright-route-mock path as the *minimum* viable regression gate given the codebase's existing all-Playwright investment, but flags RTL as a stronger alternative for the planner to weigh against setup cost |

**Installation:** none required. Every dependency this phase needs is already present.

**Version verification:** Confirmed by direct read of `apps/admin/package.json` and `apps/widget/package.json` this session (paths above) — not via `npm view`, since no new package is being added and the existing pinned versions are what ships. `apps/api`'s `psycopg2` usage is confirmed by direct import in `red_team.py`/`redteam_programme_service.py`, both already in production.

## Architecture Patterns

### System Architecture Diagram

```
                    ┌─────────────────────────────────────────────┐
                    │         apps/admin/app/agents/[id]           │
                    │              page.tsx (Next.js)               │
                    │                                                │
  Clerk session ───▶│  useAuth().getToken() ──▶ Authorization header │
                    │                                                │
                    │  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
                    │  │  Live    │  │Retrieval │  │  Bench   │    │
                    │  │ region   │  │ health   │  │ region   │    │
                    │  └────┬─────┘  └────┬─────┘  └────┬─────┘    │
                    │       │useQuery     │useQuery     │useQuery+ │
                    │       │             │             │useMutation│
                    │  ┌────┴─────┐  ┌────┴─────┐  ┌────┴─────┐    │
                    │  │Judgement │  │Adversary │  │  Prompt  │    │
                    │  │(existing,│  │(existing +│ │ (net-new) │    │
                    │  │ 2-field  │  │ open_find-│ │ 4 queries│    │
                    │  │  fix)    │  │ ings query│ │+3 mutate)│    │
                    │  └──────────┘  └──────────┘  └──────────┘    │
                    └───────────────────┬───────────────────────────┘
                                         │ HTTPS + Bearer JWT
                                         ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │                    apps/api (FastAPI, already shipped)            │
   │                                                                     │
   │  GET /agents/{id}/metrics ──────────► metrics_service.py           │
   │  GET /agents/{id}/retrieval-health ─► retrieval_metrics_service.py │
   │                                        + staleness.py               │
   │  GET /agents/{id}/traces?status=failing ─► bench_service.py         │
   │  POST /agents/{id}/traces/{id}/grade ─► bench_service.py            │
   │                                        └─dispatch─► promote_trace_  │
   │                                                       to_scenario   │
   │  GET /agents/{id}/eval-runs (ledger field, EXISTING call) ─► evals.py│
   │  GET /agents/{id}/red-team/programme ──► redteam_programme_service │
   │     [+ Gap B: open_findings query, same route]                     │
   │  POST /agents/{id}/red-team/findings/{id}/contain ─► red_team.py    │
   │  GET/POST/POST /agents/{id}/prompt-versions[...] ──► prompt_version_│
   │                                                        service.py   │
   └─────────────────────────────────────────────────────────────────┘
                                         │
                                         ▼ (per-turn, unrelated request path)
   ┌─────────────────────────────────────────────────────────────────┐
   │           apps/api/app/worker/tasks/runtime/agent.py               │
   │                                                                     │
   │  run_agent_turn() ──► _persist_messages() [Gap A: must now RETURN  │
   │                         assistant_msg_id, currently discards it]   │
   │                    ──► emit("agent.response", {..., message_id})   │
   │                            │ Redis pub/sub + job_events durable row │
   └────────────────────────────┼──────────────────────────────────────┘
                                 ▼
                    ┌──────────────────────────────┐
                    │  apps/widget/src (Preact)      │
                    │  sse.js ── onResponse(p) ─────▶│ Widget.jsx stores
                    │                                 │ p.message_id on the
                    │  AgentCluster.jsx               │ message object
                    │    └─ CitationRow.jsx            │
                    │    └─ FeedbackRow.jsx (NEW)  ───▶│ POST /widget/agents/
                    │         thumbs + optional CSAT    │  {id}/feedback
                    └──────────────────────────────┘
```

### Recommended Project Structure

No new top-level structure — this phase extends existing files/directories:

```
apps/admin/app/agents/[id]/
├── page.tsx                    # six regions wired in place; grows substantially —
│                                #   consider extracting Bench/Adversary/Prompt into
│                                #   sub-components under ./components/ (see below)
├── components/
│   ├── AlertsBanner.tsx         # existing precedent for the sub-component pattern
│   ├── BenchPane.tsx            # NEW (recommended, not mandatory) — the bench's
│   │                            #   two-pane roving-listbox is a self-contained unit
│   ├── AdversaryCoverage.tsx    # NEW (recommended) — coverage Ledger + contain UI
│   └── PromptVersionPanel.tsx   # NEW (recommended) — version list/diff/canary/rollback
└── deploy/page.tsx              # UNCHANGED — read-only reference for the house pattern

apps/widget/src/
├── Widget.jsx                   # onResponse handler reads p.message_id (1-line change)
└── components/
    └── FeedbackRow.jsx          # NEW — thumbs + optional CSAT, per §6 of the UI-SPEC

apps/api/app/worker/tasks/runtime/agent.py   # Gap A: _persist_messages return + emit field
apps/api/app/services/redteam_programme_service.py  # Gap B: open_findings query
apps/api/app/api/v1/red_team.py                      # Gap B: no route change needed (same route)
```

**On splitting `page.tsx` into sub-components:** the file is 663 lines today with four regions still stubbed. Fully wiring Live (8-cell grid), Retrieval health (3 sub-blocks including a 12-row Ledger), The bench (two-pane roving listbox + grade actions), Judgement (2-field fix, trivial), Adversary (coverage Ledger + contain staged-confirm), and The prompt (version list + diff + canary + rollback, 3 staged-confirms) in one file would plausibly push it past 1800-2000 lines. `AlertsBanner.tsx` is the one existing precedent for extracting a region into `./components/`. This research recommends the planner split Bench, Adversary, and The prompt into their own components (each owns its own `useQuery`/`useMutation` calls, following `AlertsBanner`'s `onXChange` callback-up pattern where the parent still needs the data, e.g. `severityCounts`/`redTeamBlocked` for the gatebar). This is a structural recommendation, not a hard requirement — the planner should weigh it against the "surgical, minimal diff" instinct the codebase otherwise favors (`22-UI-SPEC.md`'s own restraint).

### Pattern 1: Query + honest-sentinel rendering (Live region)

**What:** A `useQuery` whose response fields may be a number or the literal string `"not_tracked"` (or, for retrieval-health, `"not tracked yet"` — two different spellings in the same payload, verified `retrieval_metrics_service.py:145` vs `metrics.py`'s import of `staleness.py`'s `NOT_TRACKED`).
**When to use:** Every cell in the Live and Retrieval-health regions.
**Example (verified pattern, adapted from `centsOrNotTracked` at `deploy/page.tsx:489-497`):**
```typescript
// Source: apps/admin/app/agents/[id]/deploy/page.tsx:482-497 (existing house pattern)
function formatCents(cents: number): string {
  const rand = cents / 100
  const [intPart, decPart] = rand.toFixed(2).split('.')
  const withThousands = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `R${withThousands}.${decPart}`
}

// New for this phase — cost_per_session is DOLLARS, not cents (metrics.py:59
// comment, 23-UI-SPEC.md §4.1). Do NOT run it through formatCents (100x error).
function formatDollars(value: number): string {
  return `$${value.toFixed(2)}`
}

// A metric cell must check typeof before formatting — never coerce a sentinel
// string into Number() (23-UI-SPEC.md §7 rule 1).
function renderMetricCell(value: number | string, windowDays: number, format: (n: number) => string): string {
  if (typeof value === 'string') return `No data in the last ${windowDays} days.`
  return format(value)
}
```

### Pattern 2: Per-row in-flight state, keyed by id (not a single shared flag)

**What:** `useState<Record<string, 'approved' | 'rejected'>>` keyed by row id, so one row's in-flight mutation never disables a sibling row.
**When to use:** Bench grade buttons (keyed by `trace_id`), Adversary contain buttons (keyed by `finding_id`).
**Example:**
```typescript
// Source: apps/admin/app/agents/[id]/deploy/page.tsx:2150,2229-2235 (existing house pattern)
const [savingConfirmations, setSavingConfirmations] = useState<Record<string, 'approved' | 'rejected'>>({})
// ... mutation's onSettled clears only the one key:
onSettled: (_data, _err, { confirmationId }) => {
  setSavingConfirmations((prev) => {
    const next = { ...prev }
    delete next[confirmationId]
    return next
  })
},
```

### Pattern 3: Staged-confirm for a live-the-instant-you-click-it action

**What:** A two-step local `useState<'stateA' | 'stateB' | null>` UI that shows a confirmation question with `autoFocus` primary button before firing the real mutation, exactly as `PendingConfirmationRow` does.
**When to use:** Contain (Adversary), canary (Prompt), rollback (Prompt) — every action `23-UI-SPEC.md` §4.5/§4.6 explicitly calls out as "live the moment you click it."
**Example:** see `PendingConfirmationRow`, `deploy/page.tsx:1746-1889` (read in full this session) — reuse this shape verbatim, substituting the copy locked in `23-UI-SPEC.md` §5.

### Pattern 4: 409-as-inline-note, never a toast

**What:** A concurrent-resolve race (two operators grading/containing the same row) returns 409; the mutation's `onError` refetches and shows a transient per-row inline note, self-clearing after ~6s — never a toast/modal.
**When to use:** Bench grade (`traces.py:142-145`, `409` on re-grading a filed trace), and by extension any Adversary contain race (not currently a documented 409 case in `red_team.py` — containing an already-contained finding is an idempotent no-op per `_contain_finding_sync`, `red_team.py:379-384`, so no 409 path exists there; the planner does not need to build inline-note handling for contain's own race, only reuse the pattern if any future 409 appears).
**Example:** `deploy/page.tsx:2182-2227` (`resolveConfirmation`'s `onError`), read in full this session.

### Anti-Patterns to Avoid

- **Coercing a sentinel string to a number:** `Number("not_tracked")` → `NaN` → often silently rendered as `0` or `0%` by a careless template literal. This is the exact false-verdict class `T-22-ACT-17` prohibits, applied to a number instead of a chip (`23-UI-SPEC.md` §7 rule 1).
- **Checking only one sentinel spelling:** `retrieval_metrics_service.py`'s `_NOT_TRACKED = "not tracked yet"` (spaced) and `staleness.py`'s `NOT_TRACKED = "not_tracked"` (underscore) coexist in the **same** `/retrieval-health` response — verified directly by reading both files this session. A region that string-matches only one will fabricate a `0`/`NaN` for the other group.
- **Reading `latestRedTeamRun.deployment_blocked` as live state:** it is a snapshot from the moment the run completed (`red_team_service.py:66`), never updated by `contain`. §3.3 of the UI-SPEC names this precisely — the fix is to derive `redTeamBlocked` from the new `open_findings` list, keeping `latestRedTeamRun` for the section-head timestamp only.
- **Sharing a single `isPending`/`isSaving` flag across a list of rows:** disables every row when only one is in flight. The house pattern is always per-id keyed state (Pattern 2 above).
- **Building a bespoke "recently contained" success sub-list:** `23-UI-SPEC.md` §4.5 explicitly rejects this — default to the row disappearing on refetch, matching how filed bench traces already behave.
- **Reusing `formatCents()` for `cost_per_session`:** confirmed by reading `metrics.py:59`'s comment — that field is dollars, not cents. `formatCents` divides by 100 and would render a value 100x too small.
- **Introducing `--green`/`--red` in the widget for thumbs up/down:** `--green` is declared in `widget.css:20` but used nowhere in the shipped widget; `--red` is reserved for `.error-msg`. Both are correctly excluded by `23-UI-SPEC.md` §6.3 — reuse `--accent` (oxblood) for both directions.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Staged-confirm UI for a live mutation | A new confirm-dialog component/modal library | The exact `PendingConfirmationRow` local-`useState` pattern, copied | Already built, already reviewed, already accessible (`aria-describedby`, `autoFocus`) — a second implementation is guaranteed visual/behavioral drift |
| Per-row busy-state tracking | Redux/Zustand/a global "busy set" store | `useState<Record<id, state>>` scoped to the component, exactly as `deploy/page.tsx` does | The codebase has zero global state management anywhere; introducing one for this would be new architecture for a solved problem |
| Detecting two sentinel spellings | A single generic "isSentinel(value)" helper that regexes both strings | Two explicit `typeof value === 'string'` checks, one per field group, as the UI-SPEC's §2.2 finding requires | A clever unification risks silently treating a genuinely different failure mode (staleness scan failure vs. zero-rows) as the same UI state, which §4.2 explicitly requires to render as *different* copy |
| Reading the widget's assistant-message-id | Fetching a separate "list messages" endpoint after the fact | The `message_id` field added to the existing `agent.response` SSE payload (Gap A) | No such listing endpoint exists (`23-UI-SPEC.md` §3.1 confirms this by grep); building one would be a second, larger new-capability addition this phase's own out-of-scope line prohibits |
| Recovering a finding's description | A new `red_team_findings.description` column + backfill migration | The JSONB-correlation-in-Python technique `bench_service.py` already established (cited via `traces.py:14-20`'s docstring) | `23-UI-SPEC.md` §3.2 explicitly rejects the migration route as unnecessary scope; the correlation technique is a proven, already-shipped pattern for exactly this cross-source problem |

**Key insight:** every "don't hand-roll" item above resolves to "the pattern already exists in this exact codebase, on this exact page or its sibling `deploy/page.tsx`." This phase has zero legitimate reason to introduce a new UI library, state-management approach, or backend query technique.

## Common Pitfalls

### Pitfall 1: Treating Gap A as a one-line emit change
**What goes wrong:** A plan scoped as "add `message_id` to the `agent.response` payload" (as `23-UI-SPEC.md` §3.1's own summary phrasing might suggest at a glance) undercounts the work: `assistant_msg_id` is a local variable inside `_persist_messages()` (`agent.py:281-341`), not in scope at the emit call site (`agent.py:973-983`).
**Why it happens:** The UI-SPEC's own phrasing ("Add `"message_id": assistant_msg_id` to the `agent.response` payload") reads like a same-scope edit; it is not — `assistant_msg_id` does not exist in `run_agent_turn`'s namespace today.
**How to avoid:** The fix is three edits: (1) `_persist_messages` must `return assistant_msg_id` (verified: the function currently returns `None` — no `return` statement exists in its body, `agent.py:281-341` read in full); (2) its one call site (`agent.py:946-952`) must capture the return value; (3) the emit payload (`agent.py:973-983`) adds the field.
**Warning signs:** If a plan's diff for this fix touches only lines 973-983, it is incomplete and `message_id` will be `undefined` or throw a `NameError` at runtime.

### Pitfall 2: Breaking seven existing unit tests by changing `_persist_messages`'s contract
**What goes wrong:** `test_agent_task.py` mocks `_persist_messages` with `patch(...)` and no `return_value` at 7 call sites (lines 234, 305, 355, 408, 483, 582, 637, 698 — grep-verified this session). A bare `patch()` returns a `MagicMock` by default. Once `run_agent_turn` starts doing `assistant_msg_id = _persist_messages(...)` and passing that into the `emit()` payload, these tests will receive a `MagicMock` object as `message_id` in the captured `emitted_events` list, which is not a crash (since `emit` itself is separately mocked in the same tests — verified: `emit` is patched at every one of those 7 sites, `test_agent_task.py:158,238,311,359,412,487,529,587,642,703`) but is silently wrong data if any new assertion checks `response_payload["message_id"]`.
**Why it happens:** `_persist_messages`'s contract change is a genuine breaking change to a widely-mocked internal function.
**How to avoid:** Any plan implementing Gap A must update all `patch("app.worker.tasks.runtime.agent._persist_messages")` call sites in `test_agent_task.py` to `patch(..., return_value=<some-fixed-uuid-string>)`, and add or update assertions on `response_payload["message_id"]` in at least the tests that already assert on `response_payload` (the `test_first_turn_creates_conversation_and_stores_sdk_session_id` test at line 209 is the clearest existing template, `response_events`/`response_payload` pattern at lines 257-266).
**Warning signs:** Any test failure mentioning `MagicMock` where a UUID string was expected, or a test that silently continues to pass because it never asserts on `message_id` at all (the more dangerous outcome — a false green).

### Pitfall 3: Missing the second sentinel spelling in Retrieval health
**What goes wrong:** A region that string-matches `"not_tracked"` (underscore) will correctly catch the four `index_staleness` fields (`staleness.py:65`) but silently render `NaN`/`0` for all twelve `avg_*` averages, which use `"not tracked yet"` (spaced, `retrieval_metrics_service.py:145`).
**Why it happens:** Both sentinels look similar and it is easy to write one string-equality check and assume it covers the whole payload — this session's direct read confirms they are genuinely two different literal constants imported from two different modules.
**How to avoid:** Two independent `typeof value === 'string'` checks (or explicit checks against both literals) — one for the `avg_*` group, one for the `index_staleness` group. `23-UI-SPEC.md` §2.2 calls this "a real, verified landmine, not a hypothetical," and this session's source read confirms that characterization.
**Warning signs:** Any single shared `isNotTracked(value)` helper string-matching only one of the two literals.

### Pitfall 4: Shipping the contain button without recomputing the gate from live data
**What goes wrong:** Wiring `POST .../contain` without also changing `redTeamBlocked`'s source (currently `latestRedTeamRun?.deployment_blocked === true`, `page.tsx:303`) creates a UI that lets an operator contain a finding, sees the row disappear, but the gate stays shut forever — because the snapshot field never updates.
**Why it happens:** `criticalFinding`/`severityCounts` (`page.tsx:251-260`) and `redTeamBlocked` (`page.tsx:303`) are both currently derived from `latestRedTeamRun?.findings`, a per-run JSONB array frozen at run-completion time — verified by reading these exact lines this session.
**How to avoid:** Both must be recomputed from the new `open_findings` array (Gap B). `latestRedTeamRun` should retain exactly one remaining use: the "last programme run" timestamp in the section head (`page.tsx:517-521`).
**Warning signs:** Any diff that adds a contain button but does not also touch `severityCounts`/`criticalFinding`/`redTeamBlocked`'s data source at `page.tsx:251-260,303`.

### Pitfall 5: No route.ts / mock-fetch fixture infrastructure exists yet
**What goes wrong:** A plan that assumes Playwright can already exercise "populated" region states (the states most likely to have a rendering bug, e.g. the ORRERY-ledger zero-vs-not-tracked distinction) will find that `NEXT_PUBLIC_DEMO=true` (`playwright.config.ts:18-30`) only fakes the agents-dashboard list — the `/agents/[id]/*` sub-routes still call `useAuth()`/`getToken()` for real and, with no seeded session, their queries stay `enabled: false` and never populate.
**Why it happens:** Demo mode was built in Phase 20 for a different purpose (unauthenticated route/shell/three.js-confinement checks) and was never extended to mock API responses.
**How to avoid:** See Validation Architecture below — `page.route()` interception is the smallest addition that closes this gap without a new test framework.
**Warning signs:** A Playwright spec asserting on rendered metric values that passes only because the region under test is still rendering its loading/empty shell, not real data.

## Runtime State Inventory

**Not applicable — this is not a rename/refactor/migration phase.** No string renames, no data migrations, no OS-registered state changes. Skipped per the trigger condition (Step 2.5 of the research protocol).

## Code Examples

### Query with conditional polling (existing house pattern, reuse for Bench's tally / any in-flight resolution)
```typescript
// Source: apps/admin/app/agents/[id]/deploy/page.tsx:2127-2144
const pendingConfirmationsQuery = useQuery({
  queryKey: ['pending-confirmations', id],
  queryFn: async () => { /* ... */ },
  enabled: isLoaded && !!isSignedIn,
  staleTime: 10_000,
  refetchInterval: (query) => {
    const rows = query.state.data?.confirmations ?? []
    const stillAwaiting = rows.some(
      (row) => row.resolution === 'approved' && row.execution_outcome === null,
    )
    return stillAwaiting ? 3000 : false
  },
})
```

### Mutation with per-item saving state, 409-as-note, and settle cleanup
```typescript
// Source: apps/admin/app/agents/[id]/deploy/page.tsx:2164-2241 (abbreviated)
const resolveConfirmation = useMutation({
  mutationFn: async ({ confirmationId, resolution }) => {
    const res = await fetch(`${apiBase}/api/v1/agents/${id}/pending-confirmations/${confirmationId}/resolve`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ resolution }),
    })
    if (res.status === 409) {
      throw Object.assign(new Error('Someone already resolved this request.'), { confirmationId, concurrent: true })
    }
    if (!res.ok) { /* throw with .confirmationId attached */ }
    return res.json()
  },
  onSuccess: (_data, { confirmationId }) => {
    queryClient.invalidateQueries({ queryKey: ['pending-confirmations', id] })
  },
  onError: (err) => { /* set a transient per-id note, 6s self-clear timer */ },
  onSettled: (_d, _e, { confirmationId }) => {
    setSavingConfirmations((prev) => { const n = { ...prev }; delete n[confirmationId]; return n })
  },
})
```

### Backend — the exact `red_team_findings` schema Gap B queries against
```sql
-- Source: apps/api/alembic_tenant/versions/0012_red_team_programme.py:82-96
CREATE TABLE IF NOT EXISTS red_team_findings (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id         UUID REFERENCES red_team_runs(id) ON DELETE CASCADE,
    strategy_id    UUID REFERENCES red_team_strategies(id) ON DELETE SET NULL,
    probe_id       UUID REFERENCES red_team_probes(id) ON DELETE SET NULL,
    severity       TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'contained', 'closed')),
    attack_vector  TEXT,
    probe_message  TEXT,
    agent_response TEXT,
    turn_count     INT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- NOTE: no `description` column — confirmed. Gap B's fix must correlate
-- against red_team_runs.findings JSONB in Python for a human description,
-- never blocking the contain action if no match is found.
```

### Widget — the exact SSE handler that must gain `message_id`
```javascript
// Source: apps/widget/src/sse.js (full file, 10 lines)
export function startSSEStream(apiBase, jobId, handlers) {
  const es = new EventSource(`${apiBase}/widget/jobs/${jobId}/events`)
  es.addEventListener('agent.response', e => { handlers.onResponse?.(JSON.parse(e.data)); es.close() })
  // ... other listeners unchanged
}

// Source: apps/widget/src/Widget.jsx:55-59 — the one call site to change
onResponse: (p) => {
  setMessages(m => [...m, { role: 'agent', text: p.text, citations: p.citations, messageId: p.message_id }])
  if (p.conversation_id) setConversationId(p.conversation_id)
  setStatus('idle')
},
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Hardcoded `<EmptyState>` for Live/Retrieval-health/Bench/Prompt regions | Real `useQuery`/`useMutation` against Phase-21 endpoints | This phase | Closes WIRE-01 |
| Judgement region discards `ledger` field it already fetches | Declare `ledger` on the response type, render the two real integers | This phase (WIRE-02) | Zero new network call — the data is already in the response |
| Three hardcoded "ships in a future release" strings | Deleted, replaced with real UI | This phase (WIRE-03) | The console stops asserting a falsehood about its own capabilities |
| `redTeamBlocked` derived from a per-run JSONB snapshot | Derived from live `open_findings` (Gap B) | This phase (§3.3 fix, bundled with WIRE-04) | Prevents a permanently-stuck-blocked or falsely-cleared gate |
| `_persist_messages` returns `None` | Returns `assistant_msg_id` | This phase (Gap A) | Enables WIRE-05 entirely; no other consumer of `_persist_messages` is affected (verified: its only call site is `agent.py:946-952`) |

**Deprecated/outdated:** none — no library or pattern in this phase is being replaced; everything here is net-new wiring of already-current code.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Splitting Bench/Adversary/Prompt into sub-components under `./components/` is a net improvement over a single growing `page.tsx` | Architecture Patterns → Recommended Project Structure | Low — this is a structural recommendation, not a requirement; the planner can reject it and keep everything in `page.tsx` with no functional difference, only a larger single file |
| A2 | A fixed `return_value` UUID string is sufficient for the seven `_persist_messages` test-mock updates (Pitfall 2), rather than a per-test-generated unique value | Common Pitfalls → Pitfall 2 | Low — if two tests in the same run need distinguishable message IDs for their assertions, a shared fixed value would need to become per-test; this is a mechanical test-authoring detail the plan/execute step will resolve directly against the actual test bodies |
| A3 | No `page.route()` interception exists anywhere in `apps/admin` today (verified via a zero-match Grep this session) — this is treated as a genuine gap, not an oversight in this research's search | Validation Architecture | Low — the Grep search was scoped to `apps/admin`; if a mocking helper exists elsewhere (e.g. a shared test-utils package not yet discovered), the planner should re-check before authoring new fixture code from scratch |

**None of the above are load-bearing for the phase's success criteria** — they are structural/authoring-detail assumptions, not claims about backend behavior, security posture, or requirement scope, all of which were verified directly against source with no assumption involved.

## Open Questions

1. **Does `message_feedback` need a unique constraint on `(message_id)` to prevent the two-POST-per-message double-row case `23-UI-SPEC.md` §6.3 flags as an accepted, unresolved nit?**
   - What we know: migration `0009_turn_metrics_message_feedback.py` (read in full this session) defines `message_feedback` with no `UNIQUE` constraint on `message_id` — only an index (`ix_message_feedback_message_id`).
   - What's unclear: whether `GET /agents/{id}/metrics`'s CSAT aggregation (`metrics_service.py`) double-counts a message that received both a thumbs-only POST and a thumbs+CSAT POST as two separate feedback events, skewing `csat_avg`/`thumbs_down_rate`.
   - Recommendation: `23-UI-SPEC.md` explicitly accepts this as a non-blocking data-quality nit and does not require a fix. This research concurs it is out of scope for Phase 23 (no migration is in this phase's charter) but flags it for the planner to note as a candidate follow-up, not to silently drop.

2. **Should Bench/Adversary/Prompt be extracted into sub-components (Architecture Patterns recommendation), and if so, in which plan/wave?**
   - What we know: `AlertsBanner.tsx` is the one existing precedent; the file will grow substantially without extraction.
   - What's unclear: whether the planner will treat this as pure refactor-while-building (each region's wiring plan authors its own sub-component from the start) or ship everything inline first and split later.
   - Recommendation: author each region directly as its own component from the start (avoids a later refactor pass); this is a plan-authoring decision, not a research gap.

## Environment Availability

Skipped per the skip condition — this phase has no new external tool/service/runtime dependency. Every dependency (Node/npm, the FastAPI/Celery stack, PostgreSQL/Neon) is identical to what Phases 20-22 already ran against, and this phase adds no new one. The one environment constraint worth restating (not a new dependency, but relevant to test strategy): no local PostgreSQL server is confirmed available on this machine (per `22-06-SUMMARY.md`'s deferral note) — any plan considering a live-DB integration test for Gap A/Gap B should expect the same deferral pattern Phase 22 already established (author + unit-prove, defer the live gate).

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `@playwright/test` 1.61.1 (`apps/admin`); **no unit-test framework exists in `apps/admin` or `apps/widget`** — confirmed via `package.json` read + `find`/`Glob` for `*.spec.ts`/`*.test.*` this session (only 4 Playwright specs exist: `smoke.spec.ts`, `overflow.spec.ts`, `a11y.spec.ts`, `reduced-motion.spec.ts`) |
| Config file | `apps/admin/playwright.config.ts` (3 viewport projects: 1440/1280/900, `NEXT_PUBLIC_DEMO=true`) |
| Quick run command | `pnpm exec playwright test tests/smoke.spec.ts --project=desktop-1440` (single-viewport, single-spec — fastest useful signal) |
| Full suite command | `pnpm exec playwright test` (all 4 specs x 3 viewports) |
| Widget | **No test framework at all** — `apps/widget/package.json` has only `build`/`postbuild` (size check) scripts, no `test` script, no Playwright/Vitest/Jest dependency |

### The core gap this section must close

The milestone audit's WIRE-02/WIRE-03 defects (real data discarded, false "future release" strings) would **not** have been caught by any test that exists in this repo today, because:
1. No render-level assertion exists anywhere in `apps/admin` (`smoke.spec.ts` only asserts "no console pageerror" and element/canvas counts, never text content).
2. `NEXT_PUBLIC_DEMO=true` does not mock the `/agents/[id]/*` sub-route API calls — those routes' queries stay `enabled: false` under demo mode with no real Clerk session, so they render their natural loading/empty shell, never populated data (`playwright.config.ts:18-30`, confirmed by reading `agents/page.tsx:51-54` — only the dashboard list is demo-mocked, not the operations room).
3. Zero uses of `page.route()` interception exist anywhere in `apps/admin` (Grep-verified this session, zero matches) — there is no established fixture-mocking pattern to extend.

**This means the cheapest regression gate that would actually have caught WIRE-02/WIRE-03-class defects is new infrastructure, not an extension of something that already exists.** The smallest correct addition:

- Add `page.route('**/api/v1/agents/*/eval-runs', ...)` (and one route per region: `/metrics`, `/retrieval-health`, `/traces*`, `/red-team/programme`, `/prompt-versions`) interception in a new Playwright spec (`apps/admin/tests/agent-room.spec.ts`) that fulfills each route with a **fixture JSON matching the exact verified response shape** from §2 of `23-UI-SPEC.md` (e.g. the ORRERY ledger fixture must include `ledger.born_in_production_count: 0` to specifically test the "a zero is not a sentinel" rule, §7 rule 4 — the exact class of bug WIRE-02 was).
- This still requires a real Clerk session or a Clerk-bypass — `NEXT_PUBLIC_DEMO=true` only fakes the dashboard list, not `useAuth()`/`getToken()` on `[id]` sub-routes (confirmed above). The plan must either extend demo mode to also short-circuit `getToken()` on these routes (smallest change, consistent with the existing demo-mode philosophy) or accept these specs run only against a real signed-in session (heavier, likely `autonomous:false`).
- This is Playwright-only, no new framework dependency, and directly extends the existing 3-viewport project structure.

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WIRE-01 | Six regions call real endpoints, no hardcoded `EmptyState` remains for a backed region | render (Playwright + `page.route` fixture) | `pnpm exec playwright test tests/agent-room.spec.ts -g "renders real data"` | ❌ Wave 0 |
| WIRE-02 | Judgement tiles render `ledger.born_in_production_count`/`authored_count`, **including literal 0** | render (Playwright + `page.route` fixture, zero-value case) | `pnpm exec playwright test tests/agent-room.spec.ts -g "orrery ledger zero"` | ❌ Wave 0 |
| WIRE-03 | Zero occurrences of the three locked false strings remain in `page.tsx` | static grep gate | `grep -c "ships in a future release" apps/admin/app/agents/\[id\]/page.tsx` (expect 0) | ❌ Wave 0 (trivial to add to CI, not a Playwright spec) |
| WIRE-04 | Contain a critical finding → gate reopens; `open_findings`/`redTeamBlocked` recompute from live data, not the stale snapshot | render (Playwright + `page.route` fixture, before/after contain) | `pnpm exec playwright test tests/agent-room.spec.ts -g "contain clears the gate"` | ❌ Wave 0 |
| WIRE-05 | Widget feedback POST fires with a real `message_id`; degrades to no-render when `message_id` is absent | unit-equivalent (no widget test framework exists — see Wave 0 Gaps) | none — manual/visual verification only until Wave 0 gap is closed | ❌ Wave 0 |
| Gap A | `_persist_messages` returns `assistant_msg_id`; `agent.response` payload carries `message_id` | unit (pytest, existing framework) | `apps/api/.venv/Scripts/python.exe -m pytest tests/unit/test_agent_task.py -k message_id -x` | ❌ Wave 0 (new assertion in existing file) |
| Gap B | `open_findings` query returns real ids, description-correlation degrades gracefully on no match | unit (pytest) | `apps/api/.venv/Scripts/python.exe -m pytest tests/unit/test_redteam_programme.py -k open_findings -x` | ❌ Wave 0 (new test in existing file, `test_redteam_programme.py` already exists per `21-VERIFICATION.md`'s targeted-test-runs table) |

### Sampling Rate

- **Per task commit:** targeted pytest module for backend gap fixes (`test_agent_task.py`, `test_redteam_programme.py`); for frontend wiring tasks, `pnpm exec playwright test tests/agent-room.spec.ts --project=desktop-1440` (single viewport, fastest useful signal) plus `pnpm run check:no-dusk-tokens` (must stay exit 0 — this phase touches no tokens but the gate is cheap to re-run).
- **Per wave merge:** full `pnpm exec playwright test` (all specs, all 3 viewports) + full backend unit suite (`apps/api/.venv/Scripts/python.exe -m pytest tests/unit -q`, matching `21-VERIFICATION.md`'s own "targeted, not full-suite, disk-constrained" convention if disk space is still constrained on this machine).
- **Phase gate:** Full Playwright suite green + backend unit suite green + `grep -c "ships in a future release" page.tsx` == 0 + `node scripts/check-size.mjs` (widget, must stay under 20480 bytes gzipped) before `/gsd-verify-work 23`.

### Wave 0 Gaps

- [ ] `apps/admin/tests/agent-room.spec.ts` — new Playwright spec with `page.route()` fixtures for all six regions, covering WIRE-01/02/04's render-level assertions (the class of defect that shipped undetected in Phase 20/21).
- [ ] A demo-mode / Clerk-bypass extension so `[id]` sub-routes' queries can be exercised under Playwright without a real signed-in session — either extend `NEXT_PUBLIC_DEMO` to short-circuit `getToken()`, or explicitly scope `agent-room.spec.ts` as requiring a real session (and therefore likely `autonomous:false`, matching the pattern Phase 20's `20-15-SUMMARY.md` already used for a similar constraint).
- [ ] `apps/api/tests/unit/test_agent_task.py` — 7 existing `_persist_messages` mock sites need `return_value=` added once Gap A ships (Pitfall 2); new assertions on `response_payload["message_id"]`.
- [ ] `apps/api/tests/unit/test_redteam_programme.py` (exists per `21-VERIFICATION.md`) — new test(s) for `open_findings`, including the JSONB-correlation-miss fallback path (Gap B).
- [ ] No widget test framework exists at all. This research does **not** recommend introducing one for this phase alone (disproportionate setup cost for one feature) — recommend manual/visual verification for the widget feedback interaction, backstopped by the existing `check-size.mjs` gate (bundle budget) and a Playwright smoke addition if `apps/admin`'s console preview iframe can exercise the widget build (worth the planner confirming whether `deploy/page.tsx`'s `WidgetPreview` component, referenced in `23-UI-SPEC.md` §3.1's own precedent, loads the real widget bundle or a decorative mock — if real, it is a free Playwright surface for this feature).

## Security Domain

`security_enforcement` is absent from `.planning/config.json` → treated as enabled per the default rule.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes (pre-existing, unchanged) | Clerk session (`apps/admin`), JWT via `GET /widget/{id}/config` (`apps/widget`) — this phase adds no new auth surface |
| V3 Session Management | no | Not touched by this phase |
| V4 Access Control (IDOR) | yes | Every endpoint this phase wires already enforces `agent.tenant_id == tenant.id` → 404-not-403 (verified directly in `metrics.py`, `traces.py`, `red_team.py`, `prompt_versions.py`, `evals.py` this session — identical `_get_owned_agent`/inline pattern in all five). Gap B's `open_findings` query rides the same already-verified route (`red_team.py:285-332`) — no new IDOR surface is introduced. |
| V5 Input Validation | yes | Widget feedback: `WidgetFeedbackRequest` (`schemas/widget.py:109-121`) already Pydantic-bounds `rating: Literal["up","down"]` and `csat_score: int = Field(ge=1, le=5)`, backstopped by DB `CHECK` constraints (migration `0009`) — this phase's widget code must send exactly this shape, no new validation to author |
| V6 Cryptography | no | No new secret/credential path — `conn_str` decryption for Gap B's `open_findings` query reuses the exact `fernet_decrypt(agent.neon_connection_string)` call already present in `get_red_team_programme` (`red_team.py:321`); no new decryption call site is introduced |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| IDOR on any newly-wired GET (e.g. an operator viewing another tenant's metrics by guessing an agent id) | Elevation of Privilege / Information Disclosure | Already closed — every route this phase consumes has the `agent.tenant_id != tenant.id` → 404 check, verified in Phase 21's own security audit (`21-SECURITY.md`, T-21-02-01 through T-21-09-03, all `closed`). This phase adds no new route requiring a new check, with the sole exception of Gap B, which extends an already-IDOR-guarded route rather than adding a new one. |
| Widget feedback flooding / rating injection | Denial of Service / Tampering | Already closed server-side — `widget.py:800-809` Redis-bucket rate limit (60/min, own key namespace) and Pydantic `Literal`/`ge`/`le` bounds (T-21-02-03, T-21-02-04, both `closed` in `21-SECURITY.md`). The widget's client-side responsibility is only to degrade gracefully on 429 (silent revert, no toast) — `23-UI-SPEC.md` §6.3 already specifies this correctly; no new server-side mitigation is this phase's responsibility. |
| A stale/incorrect deploy-gate verdict reaching the operator (the §3.3 bug) | Tampering (of trust, not data) / Correctness | This is the one **genuinely new** risk this phase must close, not merely preserve — recomputing `redTeamBlocked`/`severityCounts` from live `open_findings` (Gap B) rather than the frozen `latestRedTeamRun.deployment_blocked` snapshot. Unlike the other rows in this table, this is a frontend-only correctness fix with no backend security control to lean on — the backend's own gate (`deployment_service.py`, OPS-15, already `closed` per T-21-08-01) is unaffected and remains correct; only the *console's own local mirror* of that state can go stale. |
| `_persist_messages`'s new return value leaking into a log line | Information Disclosure | Low risk — `assistant_msg_id` is already a non-secret UUID inserted into the `messages` table and already present in application logs indirectly via `conversation_id`; adding it to the SSE payload does not cross a new trust boundary (`21-SECURITY.md`'s existing trust-boundary table already lists "public widget → POST /widget/agents/{id}/feedback" as untrusted-browser-scoped, and `message_id` is the exact non-secret correlation key that route already requires as a parameter — Gap A supplies the widget the id it already needs to legitimately call an endpoint that already validates ownership via JWT). |

## Sources

### Primary (HIGH confidence — direct source read this session)
- `.planning/ROADMAP.md` — Phase 23 entry (goal, WIRE-01..05, success criteria, backend exposure gaps, design constraint, out-of-scope), read in full
- `.planning/v1.2-MILESTONE-AUDIT.md` — full audit report, read in full
- `.planning/phases/23-.../23-UI-SPEC.md` — the approved design contract, read in full (490 lines)
- `.planning/phases/21-.../21-VERIFICATION.md`, `21-SECURITY.md` — read in full
- `.planning/phases/22-.../22-03-SUMMARY.md` — read in full (house pattern precedent)
- `apps/admin/app/agents/[id]/page.tsx` — read in full (663 lines, the file being rewritten)
- `apps/admin/app/agents/[id]/deploy/page.tsx` — extensively read (lines 1-100, 454-620, 1746-1889, 2100-2440) — the reference implementation
- `apps/admin/app/components/gotham/{Chip,Btn,EmptyState,Ledger}.tsx` — read in full
- `apps/admin/app/agents/[id]/components/AlertsBanner.tsx` — read (first 50 lines, structural precedent)
- `apps/admin/package.json`, `apps/widget/package.json` — read in full (version verification)
- `apps/admin/playwright.config.ts`, `apps/admin/tests/smoke.spec.ts` — read in full
- `apps/admin/scripts/check-no-dusk-tokens.mjs`, `apps/widget/scripts/check-size.mjs` — read
- `apps/api/app/api/v1/metrics.py` — read in full
- `apps/api/app/api/v1/traces.py` — read in full
- `apps/api/app/api/v1/red_team.py` (lines 280-460) — read
- `apps/api/app/api/v1/prompt_versions.py` — read in full
- `apps/api/app/api/v1/evals.py` (lines 80-190) — read
- `apps/api/app/services/redteam_programme_service.py` — read in full
- `apps/api/app/services/metrics_service.py`, `retrieval_metrics_service.py`, `apps/api/app/worker/tasks/pipeline/staleness.py` — grepped for `NOT_TRACKED` sentinel definitions, confirmed both spellings
- `apps/api/app/schemas/widget.py` (lines 100-130) — read
- `apps/api/app/api/v1/widget.py` (lines 760-830) — read (feedback route + rate limit)
- `apps/api/app/worker/tasks/runtime/agent.py` (lines 281-345, 900-1000) — read (`_persist_messages`, the terminal `agent.response` emit)
- `apps/api/app/services/events.py` — read in full (`emit()`'s JSON serialization, relevant to Pitfall 2's blast-radius analysis)
- `apps/api/alembic_tenant/versions/0009_turn_metrics_message_feedback.py` — read (message_feedback schema, no unique constraint confirmed)
- `apps/api/alembic_tenant/versions/0012_red_team_programme.py` — read in full (red_team_findings schema, no description column confirmed)
- `apps/widget/src/Widget.jsx`, `sse.js`, `api.js`, `components/CitationRow.jsx`, `components/AgentCluster.jsx`, `widget.css` (lines 1-35) — read in full
- Grep searches (this session): `_persist_messages` call sites and mock sites across `apps/api`; `page.route` usage across `apps/admin` (zero matches, confirmed); `NOT_TRACKED`/sentinel definitions across three service modules

### Secondary (MEDIUM confidence)
None used — no web search was performed or required for this phase.

### Tertiary (LOW confidence)
None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every version number and pattern is read directly from the exact files this phase will edit; nothing is inferred from training data
- Architecture: HIGH — the house pattern (`deploy/page.tsx`) was read in depth and every recommendation traces to a specific existing line range
- Pitfalls: HIGH — all five pitfalls are grounded in a direct source read this session (function bodies, test files, migration schemas), not inferred or assumed
- Backend gap scoping (Gap A/B): HIGH — independently re-derived from source, not merely copied from `23-UI-SPEC.md`'s own claims (though it fully corroborates them)
- Validation Architecture: MEDIUM-HIGH — the test-framework facts (what exists, what doesn't) are HIGH confidence (direct file enumeration); the specific recommendation to add `page.route()` fixtures is a design judgment call, not a verified fact, hence MEDIUM on that one sub-claim

**Research date:** 2026-08-02
**Valid until:** 30 days (stable — no framework/library churn risk; the phase's entire premise is that nothing new needs to be learned, only wired). Re-verify sooner only if `apps/admin/package.json`'s React Query version changes before this phase is planned.
