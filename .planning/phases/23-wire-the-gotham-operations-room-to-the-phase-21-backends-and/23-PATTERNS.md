# Phase 23: Wire the Gotham operations room - Pattern Map

**Mapped:** 2026-08-02
**Files analyzed:** 13 (4 modify, ~9 probable create)
**Analogs found:** 11 / 13 (2 have no close analog — flagged explicitly, not forced)

All mappings below were verified by direct source read this session (not taken from `23-UI-SPEC.md`/`23-RESEARCH.md` claims alone, though both fully corroborate what's cited here).

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `apps/admin/app/agents/[id]/page.tsx` (Live, Judgement region fix, Adversary severity/gate fix) | route/page (React Server-ish client component) | CRUD (read-mostly, some mutation) | `apps/admin/app/agents/[id]/deploy/page.tsx` | exact (same page family, same author, same session) |
| `apps/admin/app/agents/[id]/components/BenchPane.tsx` (probable new) | component | request-response + mutation (grade) | `deploy/page.tsx`'s `PendingConfirmationRow` (staged-confirm) + `AlertsBanner.tsx` (region-as-component extraction precedent) | role-match (no existing two-pane roving-listbox in this codebase — interaction shape is net-new, data-fetch shape is exact) |
| `apps/admin/app/agents/[id]/components/AdversaryCoverage.tsx` (probable new) | component | request-response + mutation (contain) | `AlertsBanner.tsx` (extraction shape) + `PendingConfirmationRow` (staged-confirm) | role-match |
| `apps/admin/app/agents/[id]/components/PromptVersionPanel.tsx` (probable new) | component | CRUD + mutation (canary/rollback) | `AlertsBanner.tsx` (extraction shape) + `PendingConfirmationRow` (staged-confirm x2) | role-match |
| `apps/api/app/worker/tasks/runtime/agent.py` (`_persist_messages` return + emit) | service/task (Celery) | event-driven (SSE emit) | itself — the `agent.escalated` emit at `agent.py:958-968` is the closest in-file analog for the `agent.response` emit shape | exact (same function, sibling emit call) |
| `apps/api/app/services/redteam_programme_service.py` (`open_findings` query) | service | CRUD (read) | `read_programme()`'s own `_LIST_STRATEGIES_SQL`/`_LIST_PROBES_SQL` pattern in the same file | exact (same file, same function, add a third query) |
| `apps/api/app/api/v1/red_team.py` (no route change needed per RESEARCH.md — same route) | route | request-response | `get_red_team_programme` (`red_team.py:286-332`) | exact (unchanged route, just returns a bigger dict) |
| `apps/api/tests/unit/test_agent_task.py` (7 mock-site updates) | test | unit | itself — `test_first_turn_creates_conversation_and_stores_sdk_session_id` (`:209-274`) is the clearest existing template for asserting on `response_payload` | exact |
| `apps/api/tests/unit/test_redteam_programme.py` (new `open_findings` tests) | test | unit | itself (file already exists per `21-VERIFICATION.md`) | exact |
| `apps/widget/src/components/FeedbackRow.jsx` (new) | component | event-driven (optimistic UI + POST) | `apps/widget/src/components/CitationRow.jsx` (icon/SVG convention, visual weight) | exact for icon convention; role-match for interaction (no existing button-with-optimistic-state component in the widget) |
| `apps/widget/src/Widget.jsx` (`onResponse` reads `p.message_id`) | component (state owner) | event-driven (SSE) | itself, `onResponse` handler `:55-59` | exact |
| `apps/widget/src/sse.js` (no change needed — payload passthrough) | utility | event-driven | itself | exact (verified: `JSON.parse(e.data)` already forwards any field added server-side, no widget-side change required here) |
| `apps/admin/tests/agent-room.spec.ts` (new) | test | render/integration | `apps/admin/tests/smoke.spec.ts` | weak match — see "No Analog Found" below |

---

## Pattern Assignments

### `apps/admin/app/agents/[id]/page.tsx` — Live region, Judgement ledger fix, Adversary gate fix (route/page, CRUD)

**Analog:** `apps/admin/app/agents/[id]/deploy/page.tsx` (same directory, same design system, read extensively this session: lines 1-100, 454-620, 1746-1889, 2100-2440)

**Imports pattern** (`page.tsx:1-10`, already established, extend not replace):
```typescript
'use client'
import { use, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Btn from '../../components/gotham/Btn'
import Chip from '../../components/gotham/Chip'
import EmptyState from '../../components/gotham/EmptyState'
import Ledger, { LedgerCell, LedgerColHead, LedgerRowHead } from '../../components/gotham/Ledger'
import { useGate } from '../../components/gotham/GateProvider'
import { AlertsBanner, type Alert } from './components/AlertsBanner'
```

**Auth pattern — every query in `deploy/page.tsx` uses this exact token-fetch shape** (`deploy/page.tsx:2118-2126`, reused verbatim by `page.tsx`'s existing `evalRunsQuery`/`redTeamQuery`):
```typescript
const metricsQuery = useQuery({
  queryKey: ['metrics', id],
  queryFn: async () => {
    const token = await getToken()
    if (!token) throw new Error('Not authenticated')
    const r = await fetch(`${apiBase}/api/v1/agents/${id}/metrics`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return (await r.json()) as MetricsResponse
  },
  enabled: isLoaded && !!isSignedIn,
})
```

**Core sentinel-rendering pattern** (adapted from `formatCents`, `deploy/page.tsx:482-497` — verified, read in full):
```typescript
// Source: apps/admin/app/agents/[id]/deploy/page.tsx:482-497
function formatCents(cents: number): string {
  const rand = cents / 100
  const [intPart, decPart] = rand.toFixed(2).split('.')
  const withThousands = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `R${withThousands}.${decPart}`
}
// DO NOT reuse this for cost_per_session — that field is DOLLARS, not cents
// (metrics.py:59 comment). Write a new formatDollars() instead.
```

**Judgement ledger fix — minimal, surgical, in place at exactly `page.tsx:462,467,488`:**
```typescript
// page.tsx:460-464 today — replace ledger.born_in_production_count in, keep
// the render-a-real-zero rule (WIRE-02, UI-SPEC §4.4/§7 rule 4):
<div className="chan">
  <span className="chan-name">born in production</span>
  <div className="chan-read"><span className="num chan-val">{ledger.born_in_production_count}</span></div>
  <p className="chan-thr">promoted from a trace</p>
</div>
// page.tsx:488 stays "not tracked yet" verbatim — EvalScenarioResult has no
// timestamp field (verified, §2.4 of UI-SPEC) — do not touch this cell.
```

**Adversary gate/severity recompute (§3.3 fix, bundled with WIRE-04) — the exact lines to change:**
- `page.tsx:251-260` `severityCounts`/`criticalFinding` — currently derived from `latestRedTeamRun?.findings` (JSONB snapshot). Must derive from the new `open_findings` array on the `red-team/programme` response instead.
- `page.tsx:303` `redTeamBlocked = latestRedTeamRun?.deployment_blocked === true` — must become `open_findings.some(f => f.severity === 'critical')`.
- `page.tsx:517-521` — `latestRedTeamRun` is retained **only** here (section-head "last programme run" timestamp).

**Error handling pattern** — fold into existing banner, do not add a new surface:
```typescript
// page.tsx:365-380 (existing loadError banner) — every new query's error
// state folds in here, per 23-UI-SPEC.md §4.1 "Error" instruction.
```

---

### Bench / Adversary / Prompt sub-components (probable new files) — component, request-response + mutation

**Analog for extraction shape:** `apps/admin/app/agents/[id]/components/AlertsBanner.tsx` (read lines 1-60 this session — the one existing precedent for pulling a region out of `page.tsx`).

**Callback-up pattern** (`AlertsBanner.tsx:59-60` comment, confirmed): the component owns its own `useQuery`, but lifts derived state the parent still needs via an `onXChange` prop — e.g. `AdversaryCoverage` should lift `severityCounts`/`redTeamBlocked` back to `page.tsx` for the gatebar, exactly as `AlertsBanner` lifts its alert list via `onAlertsChange`.

**Staged-confirm pattern for Contain / Canary / Rollback (all three "live the instant you click it" actions):**

**Analog:** `PendingConfirmationRow`, `deploy/page.tsx:1746-1889` (read in full this session) — reuse this shape verbatim, substituting `23-UI-SPEC.md` §5's locked copy.

```typescript
// Source: apps/admin/app/agents/[id]/deploy/page.tsx:1757, 1822-1886 (abbreviated)
const [staged, setStaged] = useState<'contain' | null>(null)
// ... resting button sets staged, staged block renders the question +
// autoFocus primary + Cancel secondary, exactly as PendingConfirmationRow:
{staged !== null && (
  <div className="cap-confirm">
    <p className="cap-confirm-q" id={`finding-${finding.id}-confirm-q`}>
      {finding.severity === 'critical'
        ? 'Contain this finding? This clears the deployment block if it was the only open critical finding.'
        : 'Contain this finding?'}
    </p>
    <div className="cap-confirm-actions">
      <Btn variant="ghost" autoFocus disabled={inFlight}
           aria-describedby={`finding-${finding.id}-confirm-q`}
           onClick={() => { setStaged(null); onContain(finding.id) }}>
        Yes, contain
      </Btn>
      <Btn variant="ghost" disabled={inFlight} onClick={() => setStaged(null)}>Cancel</Btn>
    </div>
  </div>
)}
```

**Per-row in-flight state, keyed by id — never a shared flag:**
```typescript
// Source: apps/admin/app/agents/[id]/deploy/page.tsx:2150, 2229-2235
const [savingConfirmations, setSavingConfirmations] = useState<Record<string, 'approved' | 'rejected'>>({})
onSettled: (_data, _err, { confirmationId }) => {
  setSavingConfirmations((prev) => { const next = { ...prev }; delete next[confirmationId]; return next })
},
```
Apply identically for Bench grade (keyed by `trace_id`) and Adversary contain (keyed by `finding_id`) — `23-UI-SPEC.md`'s own ACT-07-precedent instruction (§4.5, §4.3).

**409-as-inline-note pattern (Bench grade race only — contain has no 409 path, verified `red_team.py:379-384`, containing an already-contained finding is an idempotent no-op):**
```typescript
// Source: apps/admin/app/agents/[id]/deploy/page.tsx:2182-2227 (resolveConfirmation)
if (res.status === 409) {
  throw Object.assign(new Error('Someone already graded this trace.'), { traceId, concurrent: true })
}
// onError sets a transient per-id note, 6s self-clear timer — copied verbatim
// from resolveNotes/resolveNoteTimers, deploy/page.tsx:2154-2227.
```

**Conditional-polling pattern (usable for Bench tally / any "still resolving" state):**
```typescript
// Source: apps/admin/app/agents/[id]/deploy/page.tsx:2127-2144
refetchInterval: (query) => {
  const rows = query.state.data?.confirmations ?? []
  const stillAwaiting = rows.some((row) => row.resolution === 'approved' && row.execution_outcome === null)
  return stillAwaiting ? 3000 : false
},
```

**Verdict-chip enforcement — no raw color, ever:**
```typescript
// Source: apps/admin/app/components/gotham/Chip.tsx:14-22 (read in full)
export type ChipVerdict = 'live' | 'pass' | 'fail' | 'seal' | 'mute'
// Closed union, no color/background/raw-hex prop exists on Chip anywhere.
// Bench verdict chip: 'fail' for any Gatekeeper/Auditor fail verdict (real
// verdict). graded_status badge: 'mute' only ("Held"/"Dismissed" are
// operator decisions, never pass/fail — 23-UI-SPEC.md §4.3).
```

---

### `apps/api/app/worker/tasks/runtime/agent.py` — Gap A (`_persist_messages` return value + emit field) (service/task, event-driven)

**Analog:** itself — no external analog needed, this is a three-line internal contract change. Verified in full this session: `_persist_messages` (`agent.py:281-341`) has **no `return` statement anywhere in its body** — it returns `None` implicitly. `assistant_msg_id = str(uuid.uuid4())` is generated at `agent.py:311` and used only within the function's own cursor block.

**Required three-part fix (per `23-RESEARCH.md` Pitfall 1, independently confirmed):**

1. `_persist_messages` signature/body — add `return assistant_msg_id` as the function's last line (after `conn.commit()`, before the `log.debug(...)` call, or after it — either is safe, `conn.commit()` at `:335` must stay before the return).
2. Call site `agent.py:946-952` — capture the return:
```python
# Before (agent.py:946-952):
_persist_messages(
    conn=tenant_conn, conv_id=local_conversation_id,
    user_msg=message, assistant_msg=response_text, tool_calls_log=tool_calls_log,
)
# After:
assistant_msg_id = _persist_messages(
    conn=tenant_conn, conv_id=local_conversation_id,
    user_msg=message, assistant_msg=response_text, tool_calls_log=tool_calls_log,
)
```
3. Terminal emit, `agent.py:973-983` — add the field, matching the sibling `agent.escalated` emit's shape at `:958-968`:
```python
# Source: apps/api/app/worker/tasks/runtime/agent.py:973-983 (before)
emit(
    job_id, "agent.response",
    {"text": response_text, "citations": citations_list, "conversation_id": str(local_conversation_id)},
    db, _redis,
)
# After — one field added, same emit() call shape as the agent.escalated
# sibling emit at :958-968 (job_id, event_name, payload dict, db, _redis):
emit(
    job_id, "agent.response",
    {"text": response_text, "citations": citations_list,
     "conversation_id": str(local_conversation_id), "message_id": assistant_msg_id},
    db, _redis,
)
```

---

### `apps/api/tests/unit/test_agent_task.py` — 7 mock-site updates (test, unit)

**Analog:** itself, `test_first_turn_creates_conversation_and_stores_sdk_session_id` (`:209-274`).

**Confirmed this session — the exact 7 call sites** (grep-verified): `:234, 305, 355, 408, 483, 582, 637, 698` — every one is a bare `patch("app.worker.tasks.runtime.agent._persist_messages")` with no `return_value`. Each must become:
```python
patch("app.worker.tasks.runtime.agent._persist_messages", return_value="fixed-test-uuid-string"),
```
and at least the test at `:209-274` should gain an assertion on `response_payload["message_id"]`, following the existing `response_events`/`response_payload` capture pattern already used there for `conversation_id`/`text` assertions.

---

### `apps/api/app/services/redteam_programme_service.py` — Gap B (`open_findings` query) (service, CRUD read)

**Analog:** itself — `read_programme()`'s own `_LIST_STRATEGIES_SQL`/`_LIST_PROBES_SQL`/`_COVERAGE_ROLLUP_SQL` three-query pattern (`redteam_programme_service.py:29-56`, read in full this session).

**Core pattern to extend (add a fourth query + fourth key in the returned dict, same shape as the existing three):**
```python
# Source: apps/api/app/services/redteam_programme_service.py:29-56 (existing pattern to mirror)
_OPEN_FINDINGS_SQL = """
    SELECT id, strategy_id, severity, attack_vector, probe_message, agent_response, turn_count
    FROM red_team_findings
    WHERE status = 'open'
    ORDER BY severity DESC, created_at DESC
"""
# Inside read_programme(), same cursor/connection already open (:67-79):
cur.execute(_OPEN_FINDINGS_SQL)
open_finding_rows = cur.fetchall()
# Build list in the same dict-comprehension style as strategies/probes above
# (:81-100), then correlate description from the latest red_team_runs.findings
# JSONB in Python — same cross-source technique bench_service.py already uses
# per traces.py:14-20's own module docstring ("No cross-DB SQL join is
# possible... does the correlation in Python"). Never block contain on a
# missing description — fall back to a generic label from attack_vector alone.
```
Return dict grows from `{"strategies", "probes", "coverage"}` to `{"strategies", "probes", "coverage", "open_findings"}` — same top-level sibling-key shape, no nesting change to the existing three keys. `red_team.py`'s route (`:286-332`) needs **zero changes** — it already returns `programme` (the whole dict) verbatim.

---

### `apps/widget/src/components/FeedbackRow.jsx` (new) — component, event-driven

**Analog:** `apps/widget/src/components/CitationRow.jsx` (read in full — 15 lines) for icon/SVG convention and visual weight; `apps/widget/src/api.js` (read in full — 17 lines) for the HTTP call pattern; `apps/widget/src/components/AgentCluster.jsx` for the placement/composition shape.

**Imports pattern** (`CitationRow.jsx:1`, the only import any widget component needs):
```jsx
import { h } from 'preact'
```

**Icon/SVG convention — exact, verified, must be matched stroke-for-stroke:**
```jsx
// Source: apps/widget/src/components/CitationRow.jsx:8
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">...</svg>
// FeedbackRow's thumbs must use the same viewBox="0 0 24 24" fill="none"
// stroke="currentColor" stroke-width="2" convention (23-UI-SPEC.md §1, §6.3).
```

**HTTP call pattern (`sendChat`-shaped, `api.js:8-16` — the widget's only existing POST):**
```javascript
// Source: apps/widget/src/api.js:8-16 (existing pattern to extend, do not
// duplicate the JWT-handling logic — add a sibling sendFeedback function
// using the same _jwt module-level variable and getJwt() accessor)
export async function sendChat(apiBase, agentId, message, conversationId) {
  const res = await fetch(`${apiBase}/widget/${agentId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${_jwt}` },
    body: JSON.stringify({ message, conversation_id: conversationId })
  })
  if (res.status === 401) throw new Error('JWT expired')
  return res.json()
}
// New sibling, same file:
export async function sendFeedback(apiBase, agentId, messageId, conversationId, rating, csatScore) {
  const res = await fetch(`${apiBase}/widget/agents/${agentId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${_jwt}` },
    body: JSON.stringify({ message_id: messageId, conversation_id: conversationId, rating,
                            ...(csatScore ? { csat_score: csatScore } : {}) })
  })
  return res // caller inspects .ok/.status; 429/network errors degrade silently per §6.3
}
```

**Composition/placement pattern (`Widget.jsx:77-86`, the exact site `FeedbackRow` slots into):**
```jsx
// Source: apps/widget/src/Widget.jsx:77-86
{messages.map((m, i) => (
  m.role === 'agent'
    ? <AgentCluster agentName={agentName}>
        <MessageBubble role="agent" text={m.text} />
        <CitationRow citations={m.citations} />
        {/* FeedbackRow slots here, directly below CitationRow, per §6.1 */}
      </AgentCluster>
    : <MessageBubble role="user" text={m.text} />
))}
```

**Widget.jsx `onResponse` change (Gap A consumer side):**
```javascript
// Source: apps/widget/src/Widget.jsx:55-59 (before)
onResponse: (p) => {
  setMessages(m => [...m, { role: 'agent', text: p.text, citations: p.citations, messageId: p.message_id }])
  if (p.conversation_id) setConversationId(p.conversation_id)
  setStatus('idle')
},
// messageId: p.message_id is ALREADY WRITTEN in this exact line per the
// research's own code excerpt — verify this against the live file before
// treating it as pre-done; if not yet present, this is the one-line addition.
```

**Degrade-without-id pattern (no existing analog — new rule, stated explicitly in UI-SPEC §6.2):** if `message.messageId` is `undefined`, `FeedbackRow` must return `null` (render nothing), never a disabled-looking button. No codebase precedent for "renders nothing if a required prop is missing" exists in the widget today — this is a new but simple guard clause, not a pattern gap.

---

## Shared Patterns

### Auth (admin)
**Source:** every `useQuery`/`useMutation` in `deploy/page.tsx` (`:2118-2126` is the canonical shape)
**Apply to:** every new region query in `page.tsx` and its extracted sub-components — `useAuth()` → `getToken()` → `Authorization: Bearer` header, `enabled: isLoaded && !!isSignedIn`.

### Auth (widget)
**Source:** `apps/widget/src/api.js:1-17` — module-level `_jwt` set by `loadConfig()`, read by every subsequent call via the same closure variable (not React/Preact context — a plain module singleton).
**Apply to:** `sendFeedback()` — reuse `_jwt` directly, do not introduce a second auth mechanism.

### Staged-confirm for live-the-instant-you-click-it actions
**Source:** `PendingConfirmationRow`, `deploy/page.tsx:1746-1889`
**Apply to:** Adversary contain, Prompt canary, Prompt rollback — all three, per `23-UI-SPEC.md` §4.5/§4.6's own explicit instruction to reuse this shape verbatim.

### Per-row in-flight state keyed by id
**Source:** `savingConfirmations`/`savingSkills`, `deploy/page.tsx:2150` and `:2111`
**Apply to:** Bench grade buttons (keyed by `trace_id`), Adversary contain buttons (keyed by `finding_id`).

### Verdict-only color
**Source:** `apps/admin/app/components/gotham/Chip.tsx` (14-22, closed `ChipVerdict` union)
**Apply to:** every new colored element in every region — no raw hex, no new hue; drift-detected chip, high-severity cells, contain button's default `Btn` autofocus state all reuse the existing three-hue system.

### Honest sentinel handling — two distinct spellings, never coerced to a number
**Source:** `23-UI-SPEC.md` §2.1/§2.2, independently confirmed this session by reading `metrics_service.py`, `retrieval_metrics_service.py`, `staleness.py` sentinel constants (not re-read in full here — RESEARCH.md's grep-based confirmation is treated as sufficient corroboration, consistent with its own HIGH confidence rating)
**Apply to:** Live region (8 cells), Retrieval health region (12 `avg_*` rows + 4 `index_staleness` fields) — two independent `typeof value === 'string'` checks, never one shared helper.

### 409-as-inline-note, never a toast
**Source:** `resolveConfirmation`'s `onError`, `deploy/page.tsx:2182-2227`
**Apply to:** Bench grade race only (verified: Adversary contain has no 409 path — `_contain_finding_sync` is an idempotent no-op on an already-contained finding, `red_team.py:379-384`).

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `apps/admin/app/agents/[id]/components/BenchPane.tsx` — the two-pane roving-listbox interaction (`role="listbox"`, arrow-key navigation, `P`/`H`/`X` grade shortcuts) | component | request-response | No existing component in `apps/admin` implements a roving-listbox pattern anywhere — confirmed by the absence of `role="listbox"` in any grep-reachable file this session's reading covered. The **data-fetching half** (query/mutation/per-row state) has an exact analog (`deploy/page.tsx`); the **keyboard-interaction half** does not. The planner should treat `20-UI-SPEC.md §6.4.1`'s interaction contract (cited in `23-UI-SPEC.md` §4.3) as the spec to build against, not an existing component to copy. |
| `apps/admin/tests/agent-room.spec.ts` — render-level assertion of populated region data via `page.route()` fixture interception | test | render/integration | **No analog exists in this codebase, stated plainly rather than forced.** `23-RESEARCH.md`'s own Validation Architecture section confirms: zero uses of `page.route()` exist anywhere in `apps/admin` (grep-verified, zero matches, this session's research); the 4 existing Playwright specs (`smoke.spec.ts`, `overflow.spec.ts`, `a11y.spec.ts`, `reduced-motion.spec.ts`) never assert on rendered text content, only console-error absence and element/canvas counts. `smoke.spec.ts` is the *nearest* thing (same Playwright config, same 3-viewport project structure) but it is a weak match on data flow — it asserts presence, not content, and `NEXT_PUBLIC_DEMO=true` does not mock `/agents/[id]/*` sub-route APIs (only the dashboard list, confirmed `agents/page.tsx:51-54`). This is flagged in `23-RESEARCH.md`'s own "Wave 0 Gaps" as new infrastructure, not an extension — the planner should treat it that way rather than searching harder for a match that does not exist. |

---

## Metadata

**Analog search scope:** `apps/admin/app/agents/[id]/` (full page + deploy/page.tsx + components/), `apps/admin/app/components/gotham/` (Chip, Btn, EmptyState, Ledger), `apps/widget/src/` (all components, api.js, sse.js, Widget.jsx, widget.css), `apps/api/app/worker/tasks/runtime/agent.py`, `apps/api/app/api/v1/red_team.py`, `apps/api/app/services/redteam_programme_service.py`, `apps/api/tests/unit/test_agent_task.py`, `apps/admin/tests/smoke.spec.ts`.
**Files read directly this session (not inferred from RESEARCH.md/UI-SPEC.md alone):** `ROADMAP.md` (Phase 23 entry), `23-UI-SPEC.md` (full, 490 lines), `23-RESEARCH.md` (full, 531 lines), `apps/admin/app/components/gotham/Chip.tsx` (full), `apps/admin/app/agents/[id]/components/AlertsBanner.tsx` (first 60 lines), `apps/admin/app/agents/[id]/deploy/page.tsx` (lines 1746-1889, 2100-2250), `apps/admin/app/agents/[id]/page.tsx` (lines 1-100, 380-640), `apps/widget/src/components/CitationRow.jsx` (full), `apps/widget/src/components/AgentCluster.jsx` (full), `apps/widget/src/api.js` (full), `apps/widget/src/Widget.jsx` (grepped for composition site, lines 6-85), `apps/api/app/worker/tasks/runtime/agent.py` (lines 275-345, 935-989), `apps/api/app/api/v1/red_team.py` (lines 1-100, 280-461), `apps/api/app/services/redteam_programme_service.py` (full, 128 lines), `apps/api/tests/unit/test_agent_task.py` (grepped for mock-site line numbers, confirmed all 7 sites named in RESEARCH.md).
**Pattern extraction date:** 2026-08-02
