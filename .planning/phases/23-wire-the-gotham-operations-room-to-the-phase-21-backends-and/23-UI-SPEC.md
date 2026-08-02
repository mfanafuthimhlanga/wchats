---
phase: 23
slug: wire-the-gotham-operations-room-to-the-phase-21-backends-and
status: draft
shadcn_initialized: false
preset: none
created: 2026-08-02
---

# Phase 23 — UI Design Contract

> Visual and interaction contract for wiring the six-region operations room (`apps/admin/app/agents/[id]/page.tsx`) to the Phase 21 backends, and for adding feedback capture to the customer widget (`apps/widget/src`). This phase introduces **no new design system**. GOTHAM "Bone on Graphite" is the only authority for `apps/admin` (`.planning/phases/20-.../20-UI-SPEC.md`, `apps/admin/app/globals.css`); the widget keeps its own existing warm light-mode palette (`apps/widget/src/widget.css`), which this phase extends, not replaces. The retired `wchats-design` skill ("Hillbrow at Dusk") and the repo-root `DESIGN.md` ("Amber Console") were explicitly excluded from this research and do not appear anywhere below.

Every claim below was checked against source this session — file:line evidence is given throughout, not taken from planning documents. Two genuine backend read-path gaps were found that no upstream document scoped; both are resolved, not left open, in **§3**.

---

## 0. Authority and non-negotiables

1. GOTHAM tokens are ported verbatim from `apps/admin/app/globals.css` (already shipped, `:root` at lines 16-85, gate at 92-100). This phase adds zero new tokens to `apps/admin`.
2. Component reuse only: `Zone`, `Chip`, `Btn`, `EmptyState`, `Ledger`/`LedgerColHead`/`LedgerRowHead` (`apps/admin/app/components/gotham/*.tsx`) — read in full this session, described in **§8**. No new Gotham primitive is introduced.
3. "Colour is a verdict" (`globals.css:6-14`) governs every region touched here. §7 works out the one hard case this phase creates: honestly rendering a backend sentinel without asserting a verdict the data does not support.
4. The widget (`apps/widget/src`) is **not** Gotham. It keeps its own tokens (`apps/widget/src/widget.css:1-31`: oxblood `--accent: #7B1C3A`, warm cream `--bg: #FDF9F5`, gold `--gold`/`--amber` for tool-call/escalation states). This phase's widget work (§6) extends that system, never imports a Gotham token into `apps/widget`.
5. No new backend endpoint, table, or migration — except the two minimal read-path completions in §3, which are argued explicitly as *closing an already-built seam*, not adding capability, and flagged for the planner to accept or reject with full evidence either way.
6. `check-no-dusk-tokens.mjs` must keep exiting 0. This phase does not touch `globals.css`'s token block.

---

## 1. Design System

| Property | Value |
|----------|-------|
| Tool (admin) | none — GOTHAM hand-built CSS custom-property system, `apps/admin/app/globals.css`. No `components.json`, confirmed absent this session. |
| Tool (widget) | none — hand-built CSS in `apps/widget/src/widget.css`, Preact, no component library. |
| Component library (admin) | Gotham set: `Zone`, `Chip`, `Btn`, `EmptyState`, `Ledger`/`LedgerColHead`/`LedgerRowHead`/`LedgerCell` — all read in full this session, zero new primitives added |
| Component library (widget) | none new — plain Preact function components matching the existing `MessageBubble.jsx`/`CitationRow.jsx` shape |
| Icon library (admin) | none new — no icon added by this phase |
| Icon library (widget) | inline hand-authored stroke SVG, 24×24 viewBox, `stroke-width="2"` — matching the exact convention already shipped in `CitationRow.jsx:8` and `InputBar.jsx:26-27`. Two new glyphs only: thumbs-up, thumbs-down. |
| Font (admin) | `--display` (Space Grotesk 500), `--sans` (Inter), `--mono` (JetBrains Mono) — reused, `--voice` (Newsreader italic) is NOT used by this phase (no machine-judge verdict is authored here) |
| Font (widget) | `var(--font-sans)` (system-ui stack), `var(--font-mono)` for the CSAT numeral — reused, unchanged |

**Registry Safety:** not applicable — no shadcn, no registry, in either package. See §11.

---

## 2. Verified backend contract per region (evidence, not assumption)

Every endpoint below was opened and read this session. Response shapes are copied from the actual route/service code, not the audit or the roadmap.

### 2.1 Live — `GET /agents/{id}/metrics` (`apps/api/app/api/v1/metrics.py:56-103`)

```
{ containment: float | "not_tracked",
  deflection: float | "not_tracked",
  escalation_rate: float | "not_tracked",
  csat_avg: float | "not_tracked",
  thumbs_down_rate: float | "not_tracked",
  p95_latency_ms: float | "not_tracked",
  cost_per_session: float | "not_tracked",   // DOLLARS (cost_usd sum), NOT cents — do not run through formatCents()
  sample_size: int,
  window_days: int }
```

Per `apps/api/app/services/metrics_service.py:10-36,60,172-210`: `"not_tracked"` (underscore, exact literal `NOT_TRACKED = "not_tracked"` at line 60) is returned **when zero underlying `turn_metrics`/`message_feedback` rows exist in the window** — the instrumentation is live (OPS-01 writes a row every turn); the sentinel means *no turns yet in this window*, not *this metric is unmeasured*. `deflection` is documented as **identical to `containment`** until an independent signal exists (`metrics_service.py:14-27`) — do not present it as a second, independently-verified number.

### 2.2 Retrieval health — `GET /agents/{id}/retrieval-health` (`metrics.py:111-165`)

```
{ sample_count: int,
  avg_bm25_top_score, avg_vector_top_score, avg_rrf_top_score, avg_rerank_top_score,
  avg_reranker_lift, avg_recall_at_k, avg_ndcg_at_10, avg_mrr, avg_cited_chunk_rank,
  avg_retrieved_tokens, avg_ctx_window_utilization, avg_carried_never_cited_tokens,
  avg_compaction_ratio, avg_citation_coverage, avg_faithfulness: float | "not tracked yet",
  index_staleness: {
    stale_count: int | "not_tracked",
    stale_document_ids: string[] (capped 20),
    drift_detected: bool | "not_tracked",
    drift_model_counts: Record<string, int> | "not_tracked",
    current_embedding_model: string } }
```

**Two different sentinel spellings exist in this one payload** — verified directly: `retrieval_metrics_service.py:145` sets `_NOT_TRACKED = "not tracked yet"` (spaced) for every `avg_*` field; `staleness.py`'s `NOT_TRACKED` (imported, underscore form, same constant as metrics.py) governs the four `index_staleness` fields. A region that string-matches `"not_tracked"` and misses `"not tracked yet"` (or vice versa) will silently render a fabricated `NaN`/`0` for one of the two groups — **the executor must check both literal strings**, not one. This is a real, verified landmine, not a hypothetical.

### 2.3 The bench — `GET /agents/{id}/traces?status=failing` + `POST /agents/{id}/traces/{trace_id}/grade` (`apps/api/app/api/v1/traces.py:84-186`)

```
GET  → { traces: [{ trace_id, verdict, judge_rationale, customer_turn, agent_turn,
                     conversation_id, graded_status }],
         tally: { filed: int, held: int, dismissed: int } }
POST → body { grade: "filed" | "held" | "dismissed" }
     → 200 { trace_id, grade, tally }
     → 409 if the trace's graded_status is already "filed" (TERRARIUM law — irrevocable, `traces.py:142-145`)
     → 404 if trace_id doesn't belong to agent_id
```

Filing (`grade: "filed"`) dispatches `promote_trace_to_scenario` (`traces.py:164-167`) — this is the flywheel's only trigger and it already fires correctly server-side; the UI's only job is to call `POST .../grade` and reflect the returned `tally`.

### 2.4 Judgement — `GET /agents/{id}/eval-runs` (`apps/api/app/api/v1/evals.py:108-189`, already called by `page.tsx:200`)

The response the page **already fetches** carries a `ledger` key it currently discards:

```
{ eval_runs: [...],
  ledger: { born_in_production_count: int, red_team_count: int, authored_count: int } }
```

`_LEDGER_SQL` (`evals.py:97-105`) computes this over **all** `eval_scenarios`, not scoped to the latest run — it is a suite-wide count, not a per-run count. `page.tsx:47-59`'s `EvalRunSummary` interface does not declare `ledger` at all (it's a sibling of `eval_runs`, not a field on each run) and `page.tsx:462,467,488` hardcode `not tracked yet` over exactly this data. This is INT-02's literal fix: add `ledger` to the response type as its own top-level field (not nested in `EvalRunSummary`), read `data.ledger` alongside `data.eval_runs` in the existing `evalRunsQuery`, render the two real integers.

**`provenance IS NULL` rows are deliberately folded into `authored_count`** (`evals.py:101-103` comment: "predate provenance tracking... always treated as authored, never as an error state") — never render these as a third bucket or as an error.

### 2.5 Adversary — `GET /agents/{id}/red-team-runs` (existing, already called), `GET /agents/{id}/red-team/programme` (`red_team.py:11,83-96` route not yet called from the frontend — zero callers confirmed), `POST /agents/{id}/red-team/findings/{finding_id}/contain` (`red_team.py:414-460`, zero callers confirmed)

`GET /red-team/programme` → `redteam_programme_service.read_programme()` (`apps/api/app/services/redteam_programme_service.py:59-120`):

```
{ strategies: [{ id, attack_vector, description, created_at }],
  probes: [{ id, strategy_id, harm_category, probe_message, created_at }],
  coverage: [{ strategy_id, attack_vector, probes_tested, findings_count,
               high_severity_count, attack_success_rate }] }
```

**Verified against source, and it corrects a planning-document assumption**: `20-UI-SPEC.md §6.4` (written before OPS-13 shipped) assumed a future coverage table with columns *Strategy / Probes / Coverage % / Open findings / Last run*. The real, shipped `_COVERAGE_ROLLUP_SQL` (`redteam_programme_service.py:44-56`) has no "Coverage %" concept, no run-timestamp, and `findings_count` is **all-time, all-status** (no `WHERE status='open'` filter — confirmed by reading the SQL). Do not port the assumed column set; **§4.5 below defines the real one.**

`POST .../contain` (`red_team.py:414-460`) — transitions a `red_team_findings` row `open → contained`, and if `severity == 'critical'`, files a `source='red_team'` regression scenario (`red_team.py:392-403`). Returns `{ finding: { id, severity, status }, scenario_filed: bool }`.

---

## 3. Data-model gaps found — resolved, not left open

Following the `22-UI-SPEC.md` precedent (its Surface 2 "Data model gap found" section): two genuine backend read-path gaps exist that block requirements this phase is explicitly asked to close. Both are real, both were verified by reading source (not inferred), and both are resolved below rather than deferred to a question mark.

### 3.1 Gap A — the widget has no `message_id` to send with feedback (blocks INT-05 entirely)

`POST /widget/agents/{id}/feedback` requires `message_id: UUID` with **no default** (`apps/api/app/schemas/widget.py:118`, `WidgetFeedbackRequest`). The assistant message's real ID is generated server-side (`assistant_msg_id = str(uuid.uuid4())`, `apps/api/app/worker/tasks/runtime/agent.py:311`) and written to the tenant `messages` table (`agent.py:312-318`) — but the terminal `agent.response` SSE event the widget actually receives (`agent.py:973-983`) emits only `{ text, citations, conversation_id }`. **`assistant_msg_id` is never sent to the client anywhere.** Confirmed by reading `sse.js:6` (the widget's only handler for this event) and grepping the whole `apps/widget/src` tree — no `message_id` reference exists, and no other endpoint lists messages by ID.

**Resolution:** this is a one-field completion of an emit call that already exists and already computes the value it's missing — not a new endpoint, not a new table, not a new capability. Add `"message_id": assistant_msg_id` to the `agent.response` payload at `agent.py:973-983`. This is in scope under the phase's own framing ("close the seam", "wire... nothing new") even though the phase's literal out-of-scope line says "no new backend capability" — exposing an already-computed local variable on an already-existing emit is the same class of change as Phase 21's own `21-06` grade→promote wiring fix, not new capability. **Flagged for the planner to execute or explicitly decline with the operator's sign-off recorded** — if declined, INT-05 cannot ship a functioning feedback control (see the degrade path in §6.2, which the widget must implement regardless).

### 3.2 Gap B — no endpoint returns individual open findings with real IDs (blocks INT-04's contain trigger)

`POST .../contain` needs a `red_team_findings.id`. The only two places findings are exposed today:
- `GET /agents/{id}/red-team-runs` → `findings` is a raw JSONB dump of `RedTeamResult.findings` (`red_team_service.py:49-58`, fields: `severity, description, attack_vector, probe_message, agent_response, turn_count`) — **no `id` field exists on this model at all**, confirmed by reading the Pydantic class.
- `red_team_findings` (the first-class table `POST .../contain` operates on) is only read server-side, aggregated to severity counts with no per-row detail, by `deployment_service._fetch_red_team_summary_sync` (`deployment_service.py:216-263`) — this function is called by the checklist gate, not exposed on any route.

The first-class table's own INSERT (`apps/api/app/worker/tasks/runtime/red_team.py:544-565`) does **not** persist `description` — its columns are `run_id, strategy_id, probe_id, severity, status, attack_vector, probe_message, agent_response, turn_count`. So even a hypothetical direct read of `red_team_findings` would be missing the one field (`description`) that best explains a finding to an operator in plain language.

**Resolution:** extend `GET /agents/{id}/red-team/programme`'s existing response (§2.5) with one additional field, `open_findings`, sourced from `SELECT id, strategy_id, severity, attack_vector, probe_message, agent_response, turn_count FROM red_team_findings WHERE status = 'open'` — the same table, same connection, same route this phase is already required to wire for the coverage table, adding one more query the route already has every dependency for. Recover `description` by matching `(attack_vector, probe_message, turn_count)` against the latest `red_team_runs.findings` JSONB in Python (same cross-source-correlation technique `bench_service.py` already uses for trace/message correlation, per `traces.py:14-20`'s own module docstring — "No cross-DB SQL join is possible... does the correlation in Python"); if no match is found (JSONB history rotated out), fall back to a generic label built from `attack_vector` alone — never block the contain action on a missing description, since `description` is explanatory, not required by the mutating call.

```
open_findings: [{ id, severity, attack_vector, probe_message, agent_response, turn_count,
                   description: string | null }]
```

**Flagged for the planner**, same status as Gap A: this is presented as the minimal, evidence-grounded read completion the requirement cannot be met without; if the planner or operator declines it, **INT-04 cannot ship a working contain trigger** and the Adversary region must ship §4.5's coverage/summary work only, with the contain button omitted rather than built non-functional (never ship a button with no ID to call).

### 3.3 Consequence of Gap B's fix — a stale-verdict bug this phase must also close

Reading `page.tsx:251-260,303` against `deployment_service.py:216-263` surfaced a third, related defect that Gap B's fix makes fixable and that must be fixed in the same pass, or INT-04 introduces a false verdict:

`latestRedTeamRun.deployment_blocked` (`page.tsx:303`, feeding `gateBlocked = redTeamBlocked || checklistBlocked || ...` at line 305) is a **snapshot written once, at the moment a red-team run completes** (`RedTeamResult.deployment_blocked`, `red_team_service.py:66`: `True iff max_severity == "critical"`). It is never updated by `POST .../contain`. Once any run ever produced a critical finding, `redTeamBlocked` stays `true` forever in this page's local state, **even after the operator contains it** — because `gateBlocked` is an OR across three sources, a permanently-stuck `true` on this one source means the gate can never honestly reopen through this code path again, regardless of what `checklistBlocked` (the live, correct signal `deployment_service.py` already reads) says. This is the exact class of false verdict `DESIGN.md`/`22-UI-SPEC.md`'s `T-22-ACT-17` prohibits, and it is *created*, not merely exposed, by shipping a contain button without this fix.

**Resolution, locked:** `redTeamBlocked` and the `.sev` severity-tile counts (`page.tsx:526-543`, currently computed from `latestRedTeamRun.findings` JSONB at lines 251-257) must both be recomputed from the **live** `open_findings` list (§3.2), not from the per-run JSONB snapshot. `latestRedTeamRun` stays the source for exactly one thing: the "last programme run" timestamp shown in the section head (`page.tsx:517-521`) — a display fact, never a verdict input.

---

## 4. Region-by-region UI contract

Shared honest-state vocabulary (see §7 for the full reasoning) used throughout this section:

- **Loading** — the query is in flight, no data yet fetched client-side.
- **No data yet** — the backend's `"not_tracked"` / `"not tracked yet"` sentinel (§2.1, §2.2) — instrumentation is live, zero rows in the window.
- **Not tracked at all** — a field this phase's endpoints genuinely do not compute (e.g. per-scenario "Added" timestamp, coverage-table "Last run") — keep the literal words "not tracked," they are accurate here.
- **Populated** — a real number/row.

### 4.1 Live (`page.tsx:386-395`, currently a bare `<EmptyState>`)

Replace with the `.chans` grid (`globals.css` provides no `.chans` rule — it's page-local CSS already defined at `page.tsx:615-633`, reused unchanged) with **eight** channel cells (the prototype/20-UI-SPEC scoped seven; `deflection` is a real field this phase's endpoint returns and the design law is "render what you have honestly" — adding the eighth cell to the existing `auto-fit, minmax(140px, 1fr)` grid, `page.tsx:617`, costs nothing and omits nothing real):

| Cell | Field | Format |
|---|---|---|
| Sessions | `sample_size` | integer, mono |
| Containment | `containment` | percentage (1 decimal), mono |
| Deflection | `deflection` | percentage (1 decimal), mono, **caption below**: `"Same signal as containment until an independent measure ships."` (per §2.1 — never present as a second data point) |
| Escalation to human | `escalation_rate` | percentage |
| CSAT | `csat_avg` | `x.x / 5` |
| Thumbs down | `thumbs_down_rate` | percentage |
| p95 latency | `p95_latency_ms` | `{n} ms`, mono |
| Cost / session | `cost_per_session` | `$x.xx` — **new formatter needed; do NOT reuse `deploy/page.tsx`'s `formatCents()`** (§2.1 note: this field is dollars, `formatCents` divides by 100 and would render a value 100× too small) |

Any cell whose value is the literal string `"not_tracked"` renders `No data in the last {window_days} days.` in `--ink-3`, never a `0`, `0%`, or `—` (§7 rule: a sentinel is not a number). `window_days` defaults to 7 per the endpoint (`metrics.py:59`); no window-selector UI is in scope for this phase — display the fixed 7-day window in the section head's `.head-count` slot: `"last 7 days"`.

**Loading:** while `metricsQuery.isLoading`, render the same 8-cell grid shell with `--` mono placeholders (matching `.metrics .pending` treatment already established at `globals.css:387`) — never a spinner, matching this codebase's established convention (no loading-spinner primitive exists anywhere in the Gotham set).

**Error:** fold into the existing `loadError` banner pattern (`page.tsx:365-380`) — do not add a second error surface.

### 4.2 Retrieval health (`page.tsx:397-407`)

Three sub-blocks, all real data now:

1. **Context-window bar** — `avg_ctx_window_utilization` (0-1 float) as a horizontal fill against the 200k-token budget, with `avg_retrieved_tokens` and `avg_carried_never_cited_tokens` as the two supporting mono numerals underneath (`{retrieved} tokens retrieved · {carried} carried but never cited`). Per `senior-ux-review`'s pillar 2 restraint and this codebase's own established distaste for "filled background track" comparison bars (`22-UI-SPEC.md`'s reviewed patterns) — use a single hairline-bordered bar with one `--live`-tinted fill segment, no drop shadow, no gradient.
2. **Readings ledger** (`Ledger` component, real `<caption>` per §13's a11y rule) — one row per remaining metric: BM25 top score, vector top score, RRF top score, rerank top score, reranker lift, recall@k, nDCG@10, MRR, cited-chunk rank, compaction ratio, citation coverage, faithfulness. Twelve rows. Each cell independently checks **both** sentinel spellings (§2.2) and renders `not tracked yet` in `--ink-3` mono for either — this field genuinely reflects "not tracked at all" language correctly here, since `_NOT_TRACKED = "not tracked yet"` is this endpoint's literal, and per §7's rule the copy should match backend intent: for *this specific field group*, the honest translation is `No queries in this window yet.` (same semantic correction as §4.1 — zero rows, not zero capability) since `retrieval_metrics_service.py:158-160` confirms the same "zero rows exist" cause as metrics.py, just spelled differently.
3. **Index staleness** — a small `.sev`-style tile row (reusing the pattern already shipped for red-team severity counts, `globals.css`/`page.tsx:638-641`, not a new component): `stale_count`, `drift_detected` (chip: `pass` if `false`, `fail` if `true` — this genuinely is a verdict, unlike the averages above), `current_embedding_model` (mono, always present, never sentinel). If `stale_count`/`drift_detected` are the literal `"not_tracked"` string (a scan failure per `staleness.py:79-82`, a materially different cause than "no data yet"), render `Staleness scan unavailable.` — distinct copy from the "no queries yet" case above, because this really is "we tried to measure and failed," not "nothing has happened yet."

**Empty (zero documents, `documents.length === 0`):** skip the three sub-blocks entirely, render `EmptyState` — `heading: "No documents to retrieve from yet"`, `body: "Retrieval health has nothing to measure until this agent has a knowledge base."`, `linkHref: /agents/{id}/ingest`, `linkLabel: "Go to Ingest"`. This is a genuinely different empty cause than "zero queries" and deserves its own message.

### 4.3 The bench (`page.tsx:409-418`)

Two-pane layout per `20-UI-SPEC.md §6.4.1`'s interaction contract (roving-listbox, arrow keys, `P`/`H`/`X` grade shortcuts, `aria-live="polite"` grading announcement) — that contract was written spec-only in Phase 20 for Phase 21 to build against; this phase is that build.

- **Left pane** — a `role="listbox"` list of `traces` (§2.3), each row: truncated `customer_turn` (first line, `--ink`), `verdict` as a `Chip` (`fail` for any Gatekeeper/Auditor fail verdict — this is a real verdict, not a sentinel), `graded_status` badge only when non-null (`mute` "Held" / `mute` "Dismissed" — **never `pass`/`fail` for a grade**, a grade is an operator decision like Reject in `22-UI-SPEC.md`'s Surface 2, not a machine verdict).
- **Right pane** ("the enlarger") — full `customer_turn`, `agent_turn`, `judge_rationale` (`.voice` italic Newsreader — this IS the one legitimate use of `--voice` in this phase, since `judge_rationale` is literally the Gatekeeper/Auditor's own verdict prose, matching `20-UI-SPEC.md §2.7`'s "the judge's hand" role exactly), and three grade actions (`Btn` `ghost` variant): **File**, **Hold**, **Dismiss**.
- **Filed is irrevocable** (TERRARIUM law, §2.3): once `graded_status === 'filed'`, all three grade buttons render `aria-disabled`, with `.help` caption `This trace has been filed. It cannot be re-graded.` A 409 from a race (two operators grading simultaneously) refetches and shows the same inline note pattern `22-UI-SPEC.md` locked: `Someone already graded this trace.` — never a toast, never treated as an error (identical reasoning to that document's concurrent-resolve handling).
- **Filing succeeds → tally updates** — after a successful `filed` grade, `tally.filed` increments in the returned response; render it directly from the response, do not locally increment (avoids drift if the promote dispatch silently fails per `traces.py:168-177`'s own best-effort comment — the grade itself always succeeds even if promotion logging fails, and the UI has no separate signal for that, which is correct: the grade is what the UI owns, promotion is a fire-and-forget backend concern per that code's own design).

**Empty:** `EmptyState` — `heading: "Nothing on the bench"`, `body: "No failing production traces right now. Every recent turn passed its judge."` (a materially different, more confident empty-state message than the placeholder-era `"No failing production traces to review yet."` — this phase's data is real, so "yet" implying a future promise is no longer accurate; "right now" is honest about a genuinely good state).

### 4.4 Judgement (`page.tsx:420-511`) — the ORRERY fix (INT-02)

Minimal, surgical change. Keep the existing wired structure; fix exactly what INT-02 names:

1. Declare `ledger: { born_in_production_count: number; red_team_count: number; authored_count: number }` on the response type read by `evalRunsQuery`, sibling to `eval_runs` (§2.4 — it is NOT nested per-run).
2. `page.tsx:462` (`born in production` tile) → render `ledger.born_in_production_count` as a plain mono numeral, **including when it is `0`** — a real zero is a real, meaningful, populated state (INT-02's own instruction), never the "not tracked yet" string.
3. `page.tsx:467` (`authored` tile) → render `ledger.authored_count` the same way.
4. `page.tsx:488` (the per-scenario "Added" column) → **this one genuinely has no backing field** (`EvalScenarioResult`, §2.4, carries no timestamp) — keep `not tracked yet` here, verbatim, because unlike the two tiles above, this really is "not tracked at all," not "zero rows." Do not change this cell; INT-02 only names the two summary tiles.

No other part of this region changes — the eval-runs/results wiring, the `originLabel()` mapping, the pass/fail chip on `res.passed` are all already correct per the Phase 20 verification and stay untouched.

### 4.5 Adversary (`page.tsx:513-578`) — INT-03 copy fix + INT-04 contain trigger

**Severity tiles and gate input — recomputed from live data (§3.3, locked):**

Replace `severityCounts` (currently derived from `latestRedTeamRun?.findings`, `page.tsx:251-257`) and `redTeamBlocked` (`page.tsx:303`) with values derived from the new `open_findings` array (§3.2). `criticalFinding` (`page.tsx:259-260`, feeds the `.critical` banner) becomes `open_findings.find(f => f.severity === 'critical')`. `latestRedTeamRun` is retained **only** for the section-head timestamp (`page.tsx:517-521`) — never as a verdict input again.

**Coverage table — real data, real (corrected) columns, replacing the INT-03 false claim at `page.tsx:548`:**

`GET /agents/{id}/red-team/programme` → render `coverage` (§2.5) as a `Ledger`:

| Column | Field | Note |
|---|---|---|
| Strategy | `attack_vector` | title-cased for display, matching the existing `originLabel()`-style sentence-case convention used elsewhere on this page |
| Probes tested | `probes_tested` | mono integer |
| Findings | `findings_count` | mono integer — **all-time, all-status**, per §2.5's verified SQL; do not caption this as "open findings," it is not filtered by status |
| High severity | `high_severity_count` | mono integer, `--fail` colored only if `> 0` (matching `page.tsx:454`'s existing `failedCount > 0` pattern for the same treatment on the Judgement region — precedent already established on this exact page) |
| Attack success rate | `attack_success_rate` | percentage (already a 0-1 float rounded to 4dp server-side, `redteam_programme_service.py:107,115`) |

This replaces the exact locked false string:

> ~~"Per-strategy coverage detail ships in a future release; showing the latest run summary above."~~ → **DELETED.** The capability shipped in Phase 21 (OPS-13); this phase's whole purpose is removing exactly this class of statement.

**Empty (zero strategies — a genuinely possible state if no red-team run has ever completed):** `EmptyState` — `heading: "No coverage data yet"`, `body: "Run the programme to populate strategy coverage."` (reuses the existing "Run the programme" `Btn` already on this page, `page.tsx:568-577` — no new CTA needed, point the empty state at the same control).

**The contain trigger (INT-04, net-new):**

Each row in the `.critical` banner (and, if more than one open finding exists, a small list below the coverage table — see below) gets a `Btn` `ghost` "Contain" action, following the **exact staged-confirm shape** `22-UI-SPEC.md` Surface 2 already established and this session verified in the shipped `PendingConfirmationRow` (`apps/admin/app/agents/[id]/deploy/page.tsx:1746-1889`) — the house pattern for "this action is live the moment you click it." **It applies here, and more so than the CAP-05 enable checkbox did**: containing a finding immediately removes the console's own block on `POST /approve-deployment` (§2.5, §3.3) — the single highest-consequence effect a click on this page can have, since it can put a previously-blocked agent one Approve-click away from serving customers. Non-critical findings' contain action is lower-stakes (no gate effect) but stages identically for consistency — a caller should never have to remember which findings are staged and which aren't.

| State | Copy |
|---|---|
| Resting button | `Contain` |
| Staged question (critical finding) | `Contain this finding? This clears the deployment block if it was the only open critical finding.` |
| Staged question (non-critical finding) | `Contain this finding?` |
| Primary button (`autoFocus`) | `Yes, contain` |
| Secondary button | `Cancel` |
| In-flight | `Containing…` |
| Success (folds into existing pattern) | row disappears from `open_findings` on refetch; if it was critical and it was the last one, the gatebar's own `useEffect` (`page.tsx:314-316`) re-fires from the recomputed `redTeamBlocked` and the room re-tints via the existing `.tint`/`data-gate` mechanism — **no bespoke success message is needed or wanted**; the room visibly reopening IS the confirmation, per `20-UI-SPEC.md §8.1`'s "the room changes temperature" law |

`scenario_filed: true` in the response (only for critical findings, §2.5) is **not** surfaced as a separate toast — fold one line into the same row's resting state after containment resolves, if the row is kept visible in a "recently contained" list for the session: `Filed as a regression scenario.` (mono, `--ink-3`). If the row simply disappears from the open list (simpler, and consistent with how `The bench`'s filed traces work — filed items leave the "failing" view), this line is unnecessary; **default to disappearing**, since a persistent "recently resolved" sub-list is not required by any success criterion and would be new UI surface this phase does not need.

**Per-row `aria-disabled` during save, keyed per finding id, not shared** — identical convention to `22-UI-SPEC.md`'s locked ACT-07 rule and the reason given there (two findings should never share a busy state).

### 4.6 The prompt (`page.tsx:580-591`) — INT-03 copy fix, full version UI

Replace the `EmptyState` entirely with real data from all four `prompt-versions` endpoints (§2's file, already fully shipped: list/diff/canary/rollback).

- **Version list** — `Ledger`: Version # / Label (or `"—"` if null) / Canary % (mono, `0%` if null — a real zero, not a sentinel) / Created. Newest first (already the service's own order, `prompt_version_service.list_versions`).
- **Diff** — selecting two versions (checkbox-pair or a simple "compare to previous" default) calls `GET .../prompt-versions/diff?a=&b=` and renders the four soul-field deltas (`soul_role`, `soul_voice`, `soul_do_list`, `soul_donot_list`) as before/after pairs. No diff-highlighting library is in scope — plain two-column before/after text in `.well` blocks (existing code-block primitive, `globals.css:340-348`), matching this system's restraint.
- **Canary** — `POST .../canary` with a percent input (0-100, matches the Pydantic bound exactly, `prompt_versions.py:52`) — staged-confirm, same house pattern, because setting a canary percent routes real customer turns to a version immediately at dispatch time (`ROADMAP.md` Phase 21 OPS-16 note: "percent routing chosen at turn dispatch in `run_agent_turn`") — this is the same "live the instant you click it" class as CAP-05 and the contain action above.

  | State | Copy |
  |---|---|
  | Staged question | `Route {percent}% of turns to version {version_number} now?` |
  | Primary button | `Set {percent}% canary` |
  | Secondary button | `Cancel` |

- **Rollback** — `POST .../rollback` — also staged-confirm, since it changes what a live agent says on its very next turn:

  | State | Copy |
  |---|---|
  | Staged question | `Roll back to version {version_number}? This creates a new version with that content. Nothing is deleted.` |
  | Primary button | `Yes, roll back` |
  | Secondary button | `Cancel` |

  The "nothing is deleted" clause is load-bearing copy, not decoration. `prompt_version_service.rollback` creates a **new** version rather than overwriting (`prompt_versions.py:187`'s docstring: "restore agent to `body.version_id`'s soul WITHOUT deleting any history") and an operator who doesn't know that may hesitate to use a genuinely safe, non-destructive action. Locking this sentence prevents the copy drifting into implying data loss.

This replaces the exact locked false string:

> ~~"Version history, canary releases and rollback ship in a future release."~~ → **DELETED.**

**Empty (zero versions — should not occur in practice since a version is created on every save, but the region must not crash if it does):** `EmptyState` — `heading: "No prompt versions yet"`, `body: "Save a change in the soul editor to create the first version."`, `linkHref: /agents/{id}/soul`, `linkLabel: "Edit in the soul editor"`.

---

## 5. Copywriting Contract

Locked verbatim, per `22-UI-SPEC.md`'s own precedent ("a locked string is checkable by a gate, a paraphrase is not").

| Element | Copy |
|---|---|
| INT-03 deletion #1 (was `page.tsx:405`) | `"Retrieval health instrumentation ships in a future release."` → **removed entirely**, replaced by §4.2's real content |
| INT-03 deletion #2 (was `page.tsx:548`) | `"Per-strategy coverage detail ships in a future release; showing the latest run summary above."` → **removed entirely**, replaced by §4.5's coverage table |
| INT-03 deletion #3 (was `page.tsx:587`) | `"Version history, canary releases and rollback ship in a future release."` → **removed entirely**, replaced by §4.6's version UI |
| Live — no-data sentinel translation | `No data in the last {window_days} days.` |
| Retrieval health — averages no-data translation | `No queries in this window yet.` |
| Retrieval health — staleness scan failure | `Staleness scan unavailable.` |
| Retrieval health — zero-documents empty | heading `No documents to retrieve from yet` / body `Retrieval health has nothing to measure until this agent has a knowledge base.` |
| Deflection caption | `Same signal as containment until an independent measure ships.` |
| The bench — empty | heading `Nothing on the bench` / body `No failing production traces right now. Every recent turn passed its judge.` |
| The bench — filed-irrevocable caption | `This trace has been filed. It cannot be re-graded.` |
| The bench — concurrent-grade note | `Someone already graded this trace.` |
| Adversary — coverage empty | heading `No coverage data yet` / body `Run the programme to populate strategy coverage.` |
| Adversary — contain, critical | `Contain this finding? This clears the deployment block if it was the only open critical finding.` |
| Adversary — contain, non-critical | `Contain this finding?` |
| Adversary — contain buttons | `Yes, contain` / `Cancel` / in-flight `Containing…` |
| Adversary — filed-as-scenario note | `Filed as a regression scenario.` |
| The prompt — canary staged question | `Route {percent}% of turns to version {version_number} now?` |
| The prompt — canary buttons | `Set {percent}% canary` / `Cancel` |
| The prompt — rollback staged question | `Roll back to version {version_number}? This creates a new version with that content. Nothing is deleted.` |
| The prompt — rollback buttons | `Yes, roll back` / `Cancel` |
| The prompt — empty | heading `No prompt versions yet` / body `Save a change in the soul editor to create the first version.` |
| Widget — feedback recorded (thumbs, no visible copy change) | no toast; button fills to the selected/`--accent` state (see §6.3). Silence is the confirmation, matching this widget's existing pattern of no confirmation toasts anywhere |
| Widget — CSAT prompt | `Rate this reply` (label above the 1-5 row, visually-hidden `aria-label` on the row: `Rate this reply, 1 to 5`) |

Zero em-dashes anywhere in the locked copy above (self-audited and corrected before hand-off — the rollback staged question originally used one and was rewritten to two sentences).

---

## 6. Widget feedback capture (INT-05)

`apps/widget/src` contains zero feedback code today (confirmed by reading every file in the tree, listed in the research). Current bundle: **8.09 KB gzipped** (`dist/widget.iife.js`, measured this session via `npm run build`) against the **20 KB hard budget** (`scripts/check-size.mjs:5`, UI2-06) — **11.9 KB of headroom**. This feature does not risk the budget; the smallest-thing-that-works instruction below is about interaction simplicity and honesty, not byte-shaving.

### 6.1 Placement

Thumbs up/down render inside `AgentCluster` (`components/AgentCluster.jsx`), directly below `CitationRow`, only for `role === 'agent'` messages — matching OPS-02's own scope ("per assistant message"; user messages never get feedback controls). A new `FeedbackRow.jsx` component, styled to match `CitationRow`'s existing visual weight (12px text, `--text-3` resting color), not `MessageBubble`'s.

### 6.2 The message_id dependency (Gap A, §3.1)

`Widget.jsx`'s `onResponse` handler (`Widget.jsx:55-59`) must read `p.message_id` from the SSE payload once §3.1's backend fix lands, store it alongside `{ role: 'agent', text, citations }` in the message list, and pass it to `FeedbackRow` as a prop. **If `message_id` is `undefined`** (the backend fix hasn't shipped, or an older cached payload), `FeedbackRow` renders **nothing** — never a disabled/broken-looking control. A feedback button with no ID to send is not a smaller version of the feature; it is a non-functional button, which `senior-ux-review`'s pillar 6 and this project's own anti-pattern list (`20-UI-SPEC.md §10.6`, "decoration in a functional slot") both prohibit.

### 6.3 Interaction

- **Thumbs up / down** — two 24×24 hand-drawn stroke SVG buttons (matching `CitationRow.jsx:8`/`InputBar.jsx:26-27`'s exact convention: `viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"`), each in an ≥28×28px hit target (padding around the 24px glyph — clears WCAG 2.5.5 AA's 24×24 minimum with margin; this is a secondary/optional action row, not a primary CTA, so the 44px AAA target used for `.input-bar button.send` is not required here, but the button must still be comfortably tappable). Resting state: `--text-3` outline stroke. Selected state (the one the user clicked): filled `--accent` (oxblood) — **not** `--green`/`--red`. `--green` is declared in `widget.css:20` but used nowhere in the shipped widget; introducing it now for "thumbs up = good" would create a second accent color this restrained one-accent system doesn't otherwise use. `--red` is already reserved for `.error-msg` (something broke) — applying it to "customer disliked this answer" would conflate a rating with a failure, which are different concepts. One accent, both directions, consistent with `AgentCluster`/`CitationRow`'s existing exclusive use of `--accent`.
- **On click:** optimistic fill to selected state, immediately `POST /widget/agents/{id}/feedback` with `{ message_id, conversation_id, rating }` (no `csat_score` on this first call). A failed POST (network error, 429) reverts the optimistic fill silently — no toast, matching this widget's existing `sendError` pattern of a single inline `.error-msg`, and a feedback-send failure is not worth interrupting the conversation over; log to console only.
- **CSAT (optional, tied to the same click — the schema requires `rating` on every call, §3, so CSAT cannot be sent standalone):** immediately after either thumbs button is clicked, a small dismissible 1-5 star row fades in below the message (`Rate this reply` label, five 20×20 tap targets) for the remainder of that message's visibility — no timeout-based auto-dismiss (a customer scrolling back up later should still be able to rate). Tapping a star fires a **second** `POST` with the same `{ message_id, conversation_id, rating }` plus `csat_score`. This may produce two `message_feedback` rows for one message (schema has no documented upsert/unique constraint — not verified either way this session, flagged as a UI Consideration below); accepted as a minor, non-blocking data-quality nit rather than over-engineering a merge that isn't asked for.
- **Rate limit (60/min per agent, `widget.py:801-809`):** a 429 on either call degrades the same way as any other failed POST — silent revert, console log. A customer giving feedback on more than 60 messages a minute is not a real scenario this UI needs to explain itself over.

### 6.4 Copy

See §5's widget rows. No new visible strings beyond the `Rate this reply` label and its `aria-label`.

### 6.5 Accessibility

- Each thumbs button: `aria-label="Rate this reply helpful"` / `aria-label="Rate this reply unhelpful"`, `aria-pressed` reflecting selected state.
- CSAT row: `role="radiogroup" aria-label="Rate this reply, 1 to 5"`, five `role="radio"` stars, `aria-checked` on the selected one.
- Reduced motion: the CSAT row's fade-in respects `prefers-reduced-motion` (instant appearance instead of a transition) — the widget currently has no reduced-motion handling anywhere (`widget.css` has zero `@media (prefers-reduced-motion)` blocks); this phase adds the widget's **first** one, scoped only to this new element, since nothing else in the widget currently animates beyond the typing-indicator's dot pulse (`widget.css:149-152`, decorative and already exempt-by-convention from the reduced-motion discussion in the same way a spinner is).

---

## 7. "Colour is a verdict" applied to honest sentinels — the resolved rule

This is the phase's one hard design problem, worked out precisely rather than left as a general instruction:

1. **A backend sentinel string is not a number, and must never be coerced into one.** Any region rendering a metric must check `typeof value === 'string'` (or equivalent) before formatting — treating `"not_tracked"` as `0` (e.g. by accidental `Number("not_tracked")` → `NaN` → rendered as `0%`) is exactly the false-verdict class this project's `T-22-ACT-17` names, applied to a number instead of a chip.
2. **The backend's sentinel spelling is not always the right customer-facing word choice.** Verified this session (§2.1, §2.2): `metrics_service.py` and `retrieval_metrics_service.py` both use their sentinel to mean *zero rows in the window*, not *this capability doesn't exist* — the instrumentation is live in both cases (Phase 21 shipped it). Rendering the literal string `"not tracked yet"` to a business owner reads as "we don't measure this," which is the opposite of true. This phase's copy (§5) translates that semantic correctly: `No data in the last N days.` / `No queries in this window yet.` — never a verbatim echo of a backend sentinel whose literal wording would mislead.
3. **Genuinely absent fields keep the literal "not tracked" wording**, because for those it is accurate: the Judgement region's per-scenario "Added" column (§4.4) and the coverage table's non-existent "Last run"/"Coverage %" columns (§4.5, corrected out of the design entirely rather than faked) are fields this phase's actual endpoints do not compute at all.
4. **A zero is not a sentinel.** INT-02's own instruction (§4.4): `born_in_production_count: 0` is real, meaningful data and renders as `0`, never as an empty-state message.
5. **No new hue anywhere.** Every colored element introduced by this phase — the drift-detected chip (§4.2), the high-severity coverage cells (§4.5), the contain action — reuses the existing three-hue system (`--live`/`--pass`/`--fail`/`--seal`, bone-neutral otherwise) through the existing `Chip` component, which enforces this by construction (`Chip.tsx:14-22`, a closed `ChipVerdict` union with no raw-color prop). This phase adds zero new `Chip` verdicts.
6. **A verdict must never outlive the event that produced it.** §3.3 is this rule's concrete instance: `latestRedTeamRun.deployment_blocked` is a snapshot, and rendering it as if it were live state (after a contain action has changed reality) is the same failure class as Phase 22's execution-outcome gap — asserting a state the current data does not, in fact, support.

---

## Spacing Scale

Reused verbatim from `20-UI-SPEC.md §2.12` — GOTHAM's documented, approved exception to the 8-point grid (hand-tuned instrument-panel values: `4, 6, 8, 9, 10, 11, 12, 13, 14, 16, 18, 20, 22, 24, 26, 30, 34, 40, 44, 48, 56, 60, 72px`). This phase introduces **zero new spacing values** in `apps/admin` — every new element (readings ledger, coverage table, contain confirm block, prompt-version list) reuses `.field`/`.zone`/`.ledger`/`.cap-confirm`-family spacing already shipped in `globals.css` and `deploy/page.tsx`'s `PAGE_CSS`.

In `apps/widget`, the existing scale (`padding: 10px 14px` message bubbles, `gap: 8px` scroll area, `padding: 12px` disclosure bar — all from `widget.css`) is reused for `FeedbackRow`: `gap: 6px` between the two thumbs buttons (matching `CitationRow`'s `gap: 6px`, `widget.css:117`), `margin-top: 4px` from the citation row above it (matching the existing `.scroll-area` inter-element `gap: 8px` rhythm at a slightly tighter sub-grouping, since feedback is a sub-element of one message cluster, not a new top-level item).

Exceptions: none.

---

## Typography

Admin (reused verbatim, zero new sizes/weights):

| Role | Size | Weight | Line Height | Source |
|---|---|---|---|---|
| Ledger cell | 13.5px | 400 | 1.55 | `.ledger td`, `globals.css:329-332` |
| Ledger header | 10px mono uppercase | 700 | 1.2 | `.ledger th`, `globals.css:323-328` |
| Channel value | 19px | 400 mono | 1.2 | `.chan-val`, `page.tsx:631` |
| Channel label | 9px mono uppercase | 700 | — | `.chan-name`, `page.tsx:624-629` |
| Help/caption text | 12.5-13px | 400 | 1.55 | `.help`, `globals.css:275` |
| Section label | 10px mono uppercase | 700 | — | `.label`, `globals.css:229-232` |
| Judge voice (bench only) | 14.5-17.5px italic | 400 | 1.62 | `.voice`, `globals.css:239` |

`--voice` (Newsreader italic) is used **only** for `judge_rationale` in The bench (§4.3) — nowhere else in this phase, matching `22-UI-SPEC.md`'s same restraint ("neither surface here is a machine verdict" — inverted here: this IS one, so it's the one legitimate use).

Widget (reused verbatim):

| Role | Size | Weight | Source |
|---|---|---|---|
| Feedback icon buttons | 24×24 SVG, no text | — | matches `CitationRow` icon sizing |
| CSAT label | 10px | 600 uppercase | matches `.escalation-label`, `widget.css:305-312` |
| CSAT star glyphs | 20×20 SVG | — | new, sized between the 14px citation icon and the 44px send button — a secondary but still comfortably-tappable control |

---

## Color

Admin — reused verbatim (`globals.css:16-85`), zero new tokens:

| Role | Value | This phase's usage |
|---|---|---|
| Dominant (~60%) | `--bg #0E1012` | unchanged |
| Secondary (~30%) | `--surface`/`--surface-2` | ledger rows, readings table, coverage table |
| Accent — live | `--live` (brightness, not hue) | contain/canary/rollback staged-confirm autofocus buttons (inherited `Btn` default), context-window bar fill |
| Pass | `--pass #4CC38A` | drift-detected `false` chip |
| Fail | `--fail #E5484D` | high-severity coverage cells `> 0`, failing bench-trace verdict chip |
| Seal | `--seal #E5484D` / `--seal-hot #FF6369` | the `.critical` banner (already shipped), unchanged |
| Bone-neutral | `--ink`/`--ink-2`/`--ink-3` | every sentinel/no-data/not-tracked message — **never a hue** |

Accent reserved for: the list above only. This phase introduces no new `--live`-as-decoration usage — every new colored element is a verdict (pass/fail/seal) or the existing `Btn` default autofocus state, per the same discipline `22-UI-SPEC.md` locked.

Widget (`widget.css:1-31`, unchanged tokens, one new usage):

| Role | Value | This phase's usage |
|---|---|---|
| Accent | `--accent #7B1C3A` | selected thumbs state, selected CSAT stars — both directions, no green/red split (§6.3 reasoning) |
| Text-3 | `--text-3` | resting thumbs stroke, CSAT label |
| Border | `--border` | CSAT row's optional hairline separator if needed for visual grouping |

No new widget token is introduced. `--green` (declared, unused) stays unused by this phase, per §6.3's explicit reasoning.

---

## UI Considerations

Applicable state considerations resolved: 14 covered, 3 backstop, 2 unresolved.

| Category | Element(s) | Status | Resolution / Reason |
|---|---|---|---|
| loading | all six regions | ✅ covered | Each region's own query-pending state renders a shell with `--` mono placeholders (Live) or defers rendering entirely until first response (others), matching the existing `Loading agent…` convention already on this page |
| no-data-yet vs not-tracked-at-all | Live, Retrieval health | ✅ covered | §7 rule 2/3 — semantic translation locked, distinct copy for each cause |
| error (GET failure, any region) | all six | ✅ covered | Folds into the existing `loadError` banner (`page.tsx:365-380`) — no new error surface added |
| populated | all six regions | ✅ covered | Per-region contract in §4, each field's real source cited |
| zero-one-many | Adversary open findings | ✅ covered | Zero → no contain UI rendered (nothing to contain); one → single row in `.critical` banner; many → banner shows the first critical, remaining findings (critical and non-critical) list below the coverage table, each independently containable |
| zero-one-many | The bench traces | ✅ covered | Zero → `EmptyState`; one-to-many → listbox, arrow-key roving per `20-UI-SPEC.md §6.4.1` |
| destructive-adjacent staged action | CAP-style live-effect controls (contain, canary, rollback) | ✅ covered | All three staged per §4.5/§4.6, using the verified house pattern (`deploy/page.tsx:1746-1889`) |
| irrevocable action | Bench "File" grade | ✅ covered | 409 on re-grade attempt, `aria-disabled` once filed, exact copy locked §5 |
| concurrent-resolve (race) | Bench grade, Adversary contain | ✅ covered | Same inline-note-not-toast pattern as `22-UI-SPEC.md`, reused verbatim: `Someone already graded this trace.` |
| stale-verdict prevention | Adversary gate/severity | ✅ covered | §3.3 — recompute from live `open_findings`, never the per-run JSONB snapshot |
| long-text | bench `customer_turn`/`agent_turn`/`judge_rationale`, finding `probe_message`/`agent_response` | 🧪 backstop | No length cap specified; text should wrap in its `.well`/`.voice` container, not truncate — a judge rationale or an adversarial probe transcript truncated silently is a real information-loss risk in a review UI. Needs a rendered check, same class as `22-UI-SPEC.md`'s equivalent row |
| overflow | readings ledger (12 rows), coverage table | 🧪 backstop | Reuse the existing `.scroll-x` wrapper already shipped for the Judgement ledger (`page.tsx:473,635`) — needs a rendered check at 1440/1280/900px per the project's established three-viewport suite |
| in-flight / double-submit | contain, canary, rollback, bench grade | ✅ covered | Per-item `aria-disabled`, not global, matching the established convention exactly |
| widget feedback — degrade without message_id | `FeedbackRow` | ✅ covered | Renders nothing rather than a broken control (§6.2) — the honest backstop if Gap A (§3.1) is not closed |
| widget feedback — duplicate CSAT rows | message_feedback table | ⚠ unresolved | No unique constraint on `(message_id)` was verified this session (out of scope to check the migration for this UI-focused pass) — two POSTs (rating-only, then rating+csat) may produce two rows. Accepted as a minor data-quality nit per §6.3, not a blocker; the planner should confirm the actual constraint and decide whether a client-side "already rated" lock is worth adding, but the UI contract does not require it to ship |
| open_findings gap | Adversary contain trigger | ⚠ unresolved | §3.2 — the whole INT-04 feature is contingent on the planner accepting the minimal read-path extension; if declined, ship §4.5's coverage/summary work without the contain button, never a non-functional one |
| accent-color misuse | any new element, either package | ✅ covered | §7 rule 5 (admin, closed by `Chip`'s construction) and §6.3 (widget, explicit no-green/no-red reasoning) |
| empty-vs-genuinely-good state copy | The bench | ✅ covered | §5 — "No failing production traces right now. Every recent turn passed its judge." replaces the placeholder-era "...to review yet." now that the data is real and a clean bench is a real, confident state |

---

## Registry Safety

Not applicable — neither `apps/admin` nor `apps/widget` has a `components.json` or any shadcn/component-registry dependency, confirmed this session.

| Registry | Blocks Used | Safety Gate |
|---|---|---|
| — | — | not applicable |

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PASS
- [ ] Dimension 2 Visuals: PASS
- [ ] Dimension 3 Color: PASS
- [ ] Dimension 4 Typography: PASS
- [ ] Dimension 5 Spacing: PASS
- [ ] Dimension 6 Registry Safety: PASS

**Approval:** pending
