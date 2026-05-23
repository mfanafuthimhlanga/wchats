# Phase 6: Eval System — UI Design Contract

**Phase:** 06-eval-system
**Gathered:** 2026-05-23
**Status:** Approved
**Source:** Research + PRD + Design G (Parchment & Wine)

---

## 1. Scope

One page replacement: `apps/admin/app/agents/[id]/eval/page.tsx` — currently a "Coming soon" stub.

The eval dashboard lives inside the existing `AgentDetailLayout` with the `JourneyStepper` sidebar (Step 3 "Test"). No new layout shell is needed. The page content area (`<section style={{ flex: 1 }}>`) gets the dashboard.

**What this UI delivers (EVL-06 + EVL-07):**
- Pass rates over time per metric as a Recharts line chart
- Individual scenario pass/fail grid with per-metric scores
- Empty state when no eval runs exist
- Trigger button to manually kick off an eval run

---

## 2. Design System Tokens (Design G — locked)

Source: `apps/admin/app/globals.css`. Do NOT introduce new color values.

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#F0E8E0` | Page background |
| `--surface-1` | `#FFFCF9` | Card backgrounds, chart area |
| `--surface-2` | `#F7F0EA` | Tab bar, table header |
| `--surface-3` | `#EDE3D8` | Hover states |
| `--border` | `#D9CCBE` | Card borders, dividers |
| `--border-hard` | `#B8906A` | Active tab underline |
| `--accent` | `#7B1C3A` | Primary buttons, active tab text |
| `--accent-hover` | `#5E1229` | Button hover |
| `--text-1` | `#1A0A0F` | Headings, labels |
| `--text-2` | `#4A2030` | Body text |
| `--text-3` | `#8A6060` | Muted text, captions |
| `--text-4` | `#C4A0A0` | Placeholder text |
| `--green` | `#166534` | Pass badge text |
| `--green-bg` | `#F0FDF4` | Pass badge background |
| `--red` | `#B91C1C` | Fail badge text |
| `--red-bg` | `#FEF2F2` | Fail badge background |
| `--gold` | `#B8860B` | Metric line: Faithfulness |
| `--amber-bg` | `#FEF3C7` | Score highlight background |
| `--shadow-card` | see globals.css | Card shadow |
| `--radius-xs` | `8px` | Tag/badge radius |
| `--radius-sm` | `14px` | Card radius |
| `--font-sans` | `Inter, system-ui` | All text |
| `--font-mono` | `JetBrains Mono` | Score numbers |

---

## 3. Metric Color Map (locked)

Four Ragas metrics get distinct chart line colors drawn from Design G:

| Metric | Line Color | Token / Hex |
|--------|-----------|-------------|
| Faithfulness | Gold | `var(--gold)` `#B8860B` |
| Answer Relevancy | Wine | `var(--accent)` `#7B1C3A` |
| Context Precision | Sage | `#4A7C59` (one-off — no token) |
| Context Recall | Slate | `#4A6080` (one-off — no token) |

These are locked to avoid inconsistency between chart, legend, and scenario table column headers.

---

## 4. Page Structure

```
┌─────────────────────────────────────────────────────┐
│  JourneyStepper sidebar (existing — do not touch)   │
│  Step 3 "Test" highlighted active                   │
├─────────────────────────────────────────────────────┤
│  CONTENT AREA (flex: 1)                             │
│                                                     │
│  ← Back to Configure           [Run Eval] button    │
│  h1: Run evaluations                                │
│  p: Ragas nightly evals…                           │
│                                                     │
│  [Tab: Pass Rates] [Tab: Scenarios]                 │
│                                                     │
│  ─────────── TAB CONTENT ───────────               │
│                                                     │
│  Pass Rates tab:                                    │
│    ┌───────────────────────────────────────┐        │
│    │  Recharts ResponsiveContainer          │        │
│    │  LineChart — 4 metric lines            │        │
│    │  X axis: date  Y axis: score [0–1]    │        │
│    │  Legend below chart                    │        │
│    └───────────────────────────────────────┘        │
│                                                     │
│  Scenarios tab:                                     │
│    Table: Question | Source | F | AR | CP | CR | ✓ │
│    One row per scenario per most recent run         │
│                                                     │
│  Empty state (no runs yet):                         │
│    Dashed card: "No eval runs yet. Click Run Eval"  │
└─────────────────────────────────────────────────────┘
```

---

## 5. Component Specifications

### 5.1 Page Header

```
← Back to Configure              [▶ Run Eval]
Run evaluations
Ragas-scored nightly tests for your agent's knowledge base.
```

- Back link: `font-size: 14px`, `color: var(--accent)`, `gap: 6px` — matches existing admin pages
- `[▶ Run Eval]` button: `background: var(--accent)`, `color: #fff`, `border-radius: 8px`, `padding: 8px 16px`, `font-size: 14px`, `font-weight: 600`
- On hover: `background: var(--accent-hover)`
- Disabled when a run is in progress: `opacity: 0.5`, `cursor: not-allowed`
- h1: `font-size: 22px`, `font-weight: 700`, `color: var(--text-1)`, `margin-bottom: 4px`
- Subtitle p: `font-size: 14px`, `color: var(--text-3)`, `margin-bottom: 28px`

### 5.2 Tab Navigation

Two tabs: **Pass Rates** | **Scenarios**

```css
/* Tab bar container */
display: flex;
gap: 0;
border-bottom: 1px solid var(--border);
margin-bottom: 24px;

/* Each tab */
padding: 10px 20px;
font-size: 14px;
font-weight: 500;
color: var(--text-3);
cursor: pointer;
border-bottom: 2px solid transparent;
background: transparent;
transition: color 0.15s, border-color 0.15s;

/* Active tab */
color: var(--accent);
border-bottom-color: var(--accent);
font-weight: 600;
```

### 5.3 Pass Rates Chart (Tab 1)

**Library:** `recharts` — `pnpm add recharts` required in `apps/admin/`.

**Container:**
```css
background: var(--surface-1);
border: 1px solid var(--border);
border-radius: var(--radius-sm);
padding: 24px;
margin-bottom: 16px;
```

**Chart config:**
```tsx
<ResponsiveContainer width="100%" height={320}>
  <LineChart data={chartData} margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
    <XAxis
      dataKey="date"
      tick={{ fontSize: 12, fill: 'var(--text-3)', fontFamily: 'var(--font-sans)' }}
      axisLine={{ stroke: 'var(--border)' }}
      tickLine={false}
    />
    <YAxis
      domain={[0, 1]}
      tickFormatter={(v) => v.toFixed(1)}
      tick={{ fontSize: 12, fill: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}
      axisLine={false}
      tickLine={false}
      width={36}
    />
    <Tooltip
      contentStyle={{
        background: 'var(--surface-1)',
        border: '1px solid var(--border)',
        borderRadius: '8px',
        fontSize: '13px',
        fontFamily: 'var(--font-sans)',
      }}
      formatter={(v: number) => v.toFixed(3)}
    />
    <Legend
      wrapperStyle={{ paddingTop: 16, fontSize: 13, fontFamily: 'var(--font-sans)' }}
    />
    <Line type="monotone" dataKey="faithfulness"     stroke="#B8860B" strokeWidth={2} dot={{ r: 4 }} name="Faithfulness" />
    <Line type="monotone" dataKey="answer_relevancy" stroke="#7B1C3A" strokeWidth={2} dot={{ r: 4 }} name="Answer Relevancy" />
    <Line type="monotone" dataKey="context_precision" stroke="#4A7C59" strokeWidth={2} dot={{ r: 4 }} name="Context Precision" />
    <Line type="monotone" dataKey="context_recall"   stroke="#4A6080" strokeWidth={2} dot={{ r: 4 }} name="Context Recall" />
  </LineChart>
</ResponsiveContainer>
```

**Threshold reference line:** A dashed horizontal `<ReferenceLine y={0.9} stroke="var(--border-hard)" strokeDasharray="4 4" label={{ value: "Target 0.90", position: "right", fontSize: 11, fill: "var(--text-3)" }} />` marks the promotion threshold.

**Run summary row:** Below the chart, a single-row stats strip:

```
Last run: May 23 2026 02:04   Scenarios: 25   Duration: 4m 31s
```

```css
display: flex;
gap: 24px;
font-size: 13px;
color: var(--text-3);
padding-top: 12px;
border-top: 1px solid var(--border-soft);
```

### 5.4 Scenarios Table (Tab 2)

**Table wrapper:**
```css
background: var(--surface-1);
border: 1px solid var(--border);
border-radius: var(--radius-sm);
overflow: hidden; /* clips corner radius on thead */
```

**Column headers:**
```
| Question | Source | F | AR | CP | CR | ✓ |
```

- `Question` — 40% width, truncated with ellipsis after 60 chars
- `Source` — 80px, pill badge: `generated` (amber bg) / `mined` (blue tint)
- `F` `AR` `CP` `CR` — score columns, 80px each, monospace numeric
- `✓` — 60px, PASS/FAIL pill badge

**Header row:**
```css
background: var(--surface-2);
font-size: 12px;
font-weight: 600;
color: var(--text-3);
letter-spacing: 0.04em;
text-transform: uppercase;
padding: 10px 16px;
```

**Data row:**
```css
border-top: 1px solid var(--border-soft);
padding: 12px 16px;
font-size: 13px;
color: var(--text-2);
transition: background 0.1s;

&:hover {
  background: var(--surface-2);
}
```

**Score cell:** monospace, `var(--font-mono)`, `font-size: 13px`. Color coding:
- ≥ 0.90: `color: var(--green)`
- 0.70–0.89: `color: var(--amber)`
- < 0.70: `color: var(--red)`

**PASS / FAIL badge:**
```css
/* PASS */
background: var(--green-bg);
color: var(--green);
padding: 2px 8px;
border-radius: var(--radius-xs);
font-size: 11px;
font-weight: 600;

/* FAIL */
background: var(--red-bg);
color: var(--red);
/* same padding + radius */
```

**Source badge:**
```css
/* generated */
background: var(--amber-bg);
color: var(--amber);
font-size: 11px;
font-weight: 500;
padding: 2px 8px;
border-radius: var(--radius-xs);

/* mined */
background: #EFF6FF;
color: #1D4ED8;
/* same size */
```

**Metric column header color dots:** Tiny 8px circle before each metric label using the locked metric colors. Implemented as `<span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#B8860B', marginRight: 4 }} />`.

### 5.5 Empty State

Shown when `evalRuns.length === 0`:

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│         (dashed border, border-radius 14px)          │
│                                                      │
│    📊                                                │
│    No eval runs yet                                  │
│    Your agent will be automatically evaluated        │
│    nightly once deployed. Run a manual check now.   │
│                                                      │
│    [▶ Run First Eval]                                │
│                                                      │
└──────────────────────────────────────────────────────┘
```

```css
border: 2px dashed var(--border);
border-radius: var(--radius-sm);
padding: 64px 40px;
text-align: center;
background: var(--surface-1);

/* Icon */
font-size: 40px;
margin-bottom: 16px;

/* Title */
font-size: 16px;
font-weight: 600;
color: var(--text-1);
margin-bottom: 8px;

/* Subtitle */
font-size: 14px;
color: var(--text-3);
max-width: 340px;
margin: 0 auto 24px;
line-height: 1.5;
```

### 5.6 Loading States

- Chart area: skeleton shimmer using existing `@keyframes shimmer` from globals.css
- Table: 5 skeleton rows at `height: 44px`, `border-radius: 6px`
- Skeleton background: `linear-gradient(90deg, var(--surface-2) 25%, var(--surface-3) 50%, var(--surface-2) 75%)` with `background-size: 200% 100%; animation: shimmer 1.5s infinite;`

---

## 6. Data Shapes

### API → chart data transform

```ts
// GET /api/v1/agents/{id}/eval-runs response
interface EvalRun {
  id: string
  started_at: string
  finished_at: string | null
  status: 'running' | 'complete' | 'failed'
  scenario_count: number
  aggregate_scores: {
    faithfulness: number
    answer_relevancy: number
    context_precision: number
    context_recall: number
  }
}

// Recharts data point
interface ChartPoint {
  date: string              // toLocaleDateString()
  faithfulness: number
  answer_relevancy: number
  context_precision: number
  context_recall: number
}
```

### Scenario table data

```ts
// GET /api/v1/agents/{id}/eval-runs/{run_id}/results
interface ScenarioResult {
  scenario_id: string
  question: string
  source: 'generated' | 'mined'
  scores: {
    faithfulness: number
    answer_relevancy: number
    context_precision: number
    context_recall: number
  }
  passed: boolean    // true if ALL four scores ≥ threshold (0.90 default)
}
```

---

## 7. State Machine

```
idle
  → user clicks "Run Eval" → running (button disabled, spinner inline)
  → Celery task dispatches → poll GET /eval-runs every 5s
  → status: "complete" → refresh chart + scenarios tab
  → status: "failed" → show inline error toast

loading
  → skeleton screens for chart + table

error
  → toast notification bottom-right: "Eval run failed — {reason}"
  → retry button appears in header
```

---

## 8. Responsive Behaviour

The admin UI targets 1280px+ desktop (no mobile layout required per PRD — SMB admin is desktop-only). At widths below 1100px, `globals.css` already hides `.preview-panel`. No additional responsive breakpoints needed for the eval dashboard.

Minimum viable width: 900px (accounts for JourneyStepper sidebar ~240px + dashboard content ~660px).

---

## 9. Copywriting (locked)

| Element | Copy |
|---------|------|
| Page title | `Run evaluations` |
| Page subtitle | `Ragas-scored nightly tests for your agent's knowledge base.` |
| Tab 1 | `Pass Rates` |
| Tab 2 | `Scenarios` |
| Run button | `▶ Run Eval` |
| Running state button | `Running…` |
| Chart card title | `Metric scores over time` |
| Chart threshold label | `Target 0.90` |
| Empty state title | `No eval runs yet` |
| Empty state subtitle | `Your agent is evaluated automatically each night. Run a check now to see how it performs.` |
| Empty CTA button | `▶ Run First Eval` |
| PASS badge | `PASS` |
| FAIL badge | `FAIL` |
| Source badge: generated | `generated` |
| Source badge: mined | `mined` |
| Scenario table col: Faithfulness abbr | `F` (with gold dot) |
| Scenario table col: Answer Relevancy abbr | `AR` (with wine dot) |
| Scenario table col: Context Precision abbr | `CP` (with sage dot) |
| Scenario table col: Context Recall abbr | `CR` (with slate dot) |
| Question truncated | max 60 chars + `…` |
| Last run label | `Last run:` |
| Duration label | `Duration:` |
| Scenarios label | `Scenarios:` |

---

## 10. HTML Mockup

The mockup below is a single self-contained HTML file that renders the eval dashboard in both states (with data and empty). It uses the exact CSS custom properties from `globals.css` and faithfully represents the final implementation.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Veridian — Eval Dashboard Mockup (Phase 6)</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
<style>
  :root {
    --bg: #F0E8E0;
    --surface-1: #FFFCF9;
    --surface-2: #F7F0EA;
    --surface-3: #EDE3D8;
    --border: #D9CCBE;
    --border-soft: #EDE3D8;
    --border-hard: #B8906A;
    --accent: #7B1C3A;
    --accent-hover: #5E1229;
    --accent-dim: rgba(123,28,58,0.08);
    --gold: #B8860B;
    --gold-light: #FEF9E7;
    --text-1: #1A0A0F;
    --text-2: #4A2030;
    --text-3: #8A6060;
    --text-4: #C4A0A0;
    --radius-xs: 8px;
    --radius-sm: 14px;
    --green: #166534;
    --green-bg: #F0FDF4;
    --red: #B91C1C;
    --red-bg: #FEF2F2;
    --amber: #92400E;
    --amber-bg: #FEF3C7;
    --shadow-card: 0 1px 2px rgba(74,32,48,0.04), 0 4px 12px rgba(74,32,48,0.06);
    --shadow-focus: 0 0 0 3px rgba(123,28,58,0.18);
    --font-sans: 'Inter', system-ui, sans-serif;
    --font-mono: 'JetBrains Mono', ui-monospace, monospace;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--font-sans);
    background: var(--bg);
    color: var(--text-1);
    -webkit-font-smoothing: antialiased;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  /* ── TopNav (stub) ─────────────────────────────── */
  .topnav {
    height: 56px;
    background: var(--text-1);
    display: flex;
    align-items: center;
    padding: 0 24px;
    gap: 12px;
    flex-shrink: 0;
  }
  .topnav-logo {
    font-family: var(--font-mono);
    font-size: 15px;
    font-weight: 500;
    color: #FFFCF9;
    letter-spacing: -0.02em;
  }
  .topnav-logo span { color: var(--gold); }

  /* ── Layout shell ──────────────────────────────── */
  .layout {
    display: flex;
    flex: 1;
    min-height: 0;
  }

  /* ── JourneyStepper sidebar ────────────────────── */
  .sidebar {
    width: 240px;
    flex-shrink: 0;
    background: var(--surface-1);
    border-right: 1px solid var(--border);
    padding: 24px 16px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .sidebar-agent-name {
    font-size: 13px;
    font-weight: 700;
    color: var(--text-1);
    margin-bottom: 4px;
    padding: 0 8px;
  }
  .sidebar-agent-role {
    font-size: 11px;
    color: var(--text-3);
    padding: 0 8px;
    margin-bottom: 20px;
  }
  .step {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 10px 8px;
    border-radius: 10px;
    cursor: pointer;
    transition: background 0.1s;
  }
  .step:hover { background: var(--surface-2); }
  .step.active { background: var(--accent-dim); }
  .step-num {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 600;
    flex-shrink: 0;
    border: 2px solid var(--border);
    color: var(--text-3);
    background: var(--surface-1);
  }
  .step.done .step-num {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .step.active .step-num {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .step.locked .step-num {
    background: var(--surface-2);
    border-color: var(--border-soft);
    color: var(--text-4);
  }
  .step-text { flex: 1; min-width: 0; }
  .step-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-1);
  }
  .step.locked .step-title { color: var(--text-4); }
  .step-subtitle {
    font-size: 11px;
    color: var(--text-3);
    margin-top: 1px;
  }

  /* ── Content area ──────────────────────────────── */
  .content {
    flex: 1;
    overflow-y: auto;
    padding: 32px 40px;
    max-width: 960px;
  }

  /* ── Page header ───────────────────────────────── */
  .back-link {
    font-size: 14px;
    color: var(--accent);
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 24px;
  }
  .page-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 24px;
  }
  .page-header h1 {
    font-size: 22px;
    font-weight: 700;
    color: var(--text-1);
    margin-bottom: 4px;
  }
  .page-header p {
    font-size: 14px;
    color: var(--text-3);
  }
  .btn-primary {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: var(--radius-xs);
    padding: 9px 18px;
    font-size: 14px;
    font-weight: 600;
    font-family: var(--font-sans);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
    transition: background 0.15s;
  }
  .btn-primary:hover { background: var(--accent-hover); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

  /* ── Tabs ──────────────────────────────────────── */
  .tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
  }
  .tab {
    padding: 10px 20px;
    font-size: 14px;
    font-weight: 500;
    color: var(--text-3);
    cursor: pointer;
    border-bottom: 2px solid transparent;
    background: transparent;
    border-top: none;
    border-left: none;
    border-right: none;
    font-family: var(--font-sans);
    transition: color 0.15s, border-color 0.15s;
    margin-bottom: -1px;
  }
  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
    font-weight: 600;
  }
  .tab:hover:not(.active) { color: var(--text-2); }

  /* ── Chart card ────────────────────────────────── */
  .chart-card {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 24px;
    margin-bottom: 16px;
    box-shadow: var(--shadow-card);
  }
  .chart-card-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-2);
    margin-bottom: 20px;
  }

  /* ── SVG chart (static mockup) ─────────────────── */
  .chart-area {
    width: 100%;
    height: 280px;
    position: relative;
  }

  /* ── Chart legend ──────────────────────────────── */
  .chart-legend {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    padding-top: 16px;
    margin-bottom: 16px;
  }
  .legend-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--text-2);
  }
  .legend-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  /* ── Run summary strip ─────────────────────────── */
  .run-summary {
    display: flex;
    gap: 24px;
    font-size: 13px;
    color: var(--text-3);
    padding-top: 14px;
    border-top: 1px solid var(--border-soft);
  }
  .run-summary strong { color: var(--text-2); font-weight: 500; }

  /* ── Threshold band label ──────────────────────── */
  .threshold-label {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--text-3);
  }

  /* ── Scenarios table ───────────────────────────── */
  .table-card {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    overflow: hidden;
    box-shadow: var(--shadow-card);
  }
  table {
    width: 100%;
    border-collapse: collapse;
  }
  thead tr {
    background: var(--surface-2);
  }
  thead th {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-3);
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 10px 16px;
    text-align: left;
    white-space: nowrap;
  }
  thead th:not(:first-child) { text-align: center; }
  tbody tr {
    border-top: 1px solid var(--border-soft);
    transition: background 0.1s;
  }
  tbody tr:hover { background: var(--surface-2); }
  tbody td {
    font-size: 13px;
    color: var(--text-2);
    padding: 12px 16px;
    vertical-align: middle;
  }
  tbody td:not(:first-child) { text-align: center; }
  .question-cell {
    max-width: 280px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text-1);
    font-weight: 500;
  }

  /* ── Metric score cell ─────────────────────────── */
  .score { font-family: var(--font-mono); font-size: 13px; }
  .score.pass { color: var(--green); }
  .score.warn { color: var(--amber); }
  .score.fail { color: var(--red); }

  /* ── Metric col header with dot ────────────────── */
  .metric-header {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .metric-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
  }

  /* ── Badges ────────────────────────────────────── */
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: var(--radius-xs);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.02em;
  }
  .badge-pass { background: var(--green-bg); color: var(--green); }
  .badge-fail { background: var(--red-bg); color: var(--red); }
  .badge-generated { background: var(--amber-bg); color: var(--amber); font-weight: 500; }
  .badge-mined { background: #EFF6FF; color: #1D4ED8; font-weight: 500; }

  /* ── Empty state ───────────────────────────────── */
  .empty-state {
    border: 2px dashed var(--border);
    border-radius: var(--radius-sm);
    padding: 64px 40px;
    text-align: center;
    background: var(--surface-1);
  }
  .empty-icon { font-size: 40px; margin-bottom: 16px; display: block; }
  .empty-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--text-1);
    margin-bottom: 8px;
  }
  .empty-subtitle {
    font-size: 14px;
    color: var(--text-3);
    max-width: 340px;
    margin: 0 auto 24px;
    line-height: 1.6;
  }

  /* ── Divider between states ────────────────────── */
  .section-divider {
    margin: 48px 0;
    border: none;
    border-top: 2px dashed var(--border-hard);
    opacity: 0.4;
  }
  .section-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 16px;
  }
</style>
</head>
<body>

<!-- TopNav -->
<nav class="topnav">
  <span class="topnav-logo">veri<span>dian</span></span>
</nav>

<!-- Layout -->
<div class="layout">

  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="sidebar-agent-name">Acme Consulting Bot</div>
    <div class="sidebar-agent-role">Customer Support</div>

    <div class="step done">
      <div class="step-num">✓</div>
      <div class="step-text">
        <div class="step-title">Provision</div>
        <div class="step-subtitle">Dedicated tenant database</div>
      </div>
    </div>

    <div class="step done">
      <div class="step-num">✓</div>
      <div class="step-text">
        <div class="step-title">Configure</div>
        <div class="step-subtitle">Soul, voice, knowledge base</div>
      </div>
    </div>

    <div class="step active">
      <div class="step-num">3</div>
      <div class="step-text">
        <div class="step-title">Test</div>
        <div class="step-subtitle">Evaluations + adversarial probes</div>
      </div>
    </div>

    <div class="step locked">
      <div class="step-num">4</div>
      <div class="step-text">
        <div class="step-title">Deploy</div>
        <div class="step-subtitle">Embed snippet + design</div>
      </div>
    </div>
  </aside>

  <!-- Content -->
  <main class="content">

    <!-- ══════════════════════════════════════════ -->
    <!-- STATE 1 — Dashboard with data              -->
    <!-- ══════════════════════════════════════════ -->

    <p class="section-label">State 1 — Dashboard with eval run data</p>

    <a href="#" class="back-link">← Back to Configure</a>

    <div class="page-header">
      <div>
        <h1>Run evaluations</h1>
        <p>Ragas-scored nightly tests for your agent's knowledge base.</p>
      </div>
      <button class="btn-primary">▶ Run Eval</button>
    </div>

    <!-- Tabs -->
    <div class="tabs">
      <button class="tab active" onclick="showTab('passrates')">Pass Rates</button>
      <button class="tab" onclick="showTab('scenarios')">Scenarios</button>
    </div>

    <!-- Pass Rates tab content -->
    <div id="tab-passrates">

      <div class="chart-card">
        <div class="chart-card-title">Metric scores over time</div>

        <!-- SVG chart (static representation) -->
        <svg viewBox="0 0 820 260" xmlns="http://www.w3.org/2000/svg"
             style="width:100%;height:260px;overflow:visible">
          <!-- Grid lines -->
          <line x1="48" y1="20" x2="800" y2="20"  stroke="#D9CCBE" stroke-dasharray="3 3"/>
          <line x1="48" y1="80" x2="800" y2="80"  stroke="#D9CCBE" stroke-dasharray="3 3"/>
          <line x1="48" y1="140" x2="800" y2="140" stroke="#D9CCBE" stroke-dasharray="3 3"/>
          <!-- Threshold line at 0.90 (maps to y≈38) -->
          <line x1="48" y1="38" x2="800" y2="38"  stroke="#B8906A" stroke-dasharray="4 4" stroke-width="1.5"/>
          <text x="804" y="42" font-size="10" fill="#8A6060" font-family="JetBrains Mono, monospace">Target 0.90</text>
          <line x1="48" y1="200" x2="800" y2="200" stroke="#D9CCBE" stroke-dasharray="3 3"/>
          <line x1="48" y1="260" x2="800" y2="260" stroke="#D9CCBE" stroke-dasharray="3 3"/>

          <!-- Y-axis labels -->
          <text x="40" y="24"  font-size="11" fill="#8A6060" font-family="JetBrains Mono, monospace" text-anchor="end">1.0</text>
          <text x="40" y="84"  font-size="11" fill="#8A6060" font-family="JetBrains Mono, monospace" text-anchor="end">0.8</text>
          <text x="40" y="144" font-size="11" fill="#8A6060" font-family="JetBrains Mono, monospace" text-anchor="end">0.6</text>
          <text x="40" y="204" font-size="11" fill="#8A6060" font-family="JetBrains Mono, monospace" text-anchor="end">0.4</text>

          <!-- X-axis labels (5 dates) -->
          <text x="100"  y="248" font-size="11" fill="#8A6060" font-family="Inter, sans-serif" text-anchor="middle">May 19</text>
          <text x="275"  y="248" font-size="11" fill="#8A6060" font-family="Inter, sans-serif" text-anchor="middle">May 20</text>
          <text x="450"  y="248" font-size="11" fill="#8A6060" font-family="Inter, sans-serif" text-anchor="middle">May 21</text>
          <text x="625"  y="248" font-size="11" fill="#8A6060" font-family="Inter, sans-serif" text-anchor="middle">May 22</text>
          <text x="800"  y="248" font-size="11" fill="#8A6060" font-family="Inter, sans-serif" text-anchor="middle">May 23</text>

          <!-- Faithfulness line (#B8860B gold) — scores: 0.81, 0.85, 0.88, 0.87, 0.91 -->
          <!-- y = 20 + (1 - score) * 240 -->
          <polyline points="100,66 275,56 450,49 625,52 800,38"
                    fill="none" stroke="#B8860B" stroke-width="2.5" stroke-linejoin="round"/>
          <circle cx="100" cy="66"  r="4" fill="#B8860B"/>
          <circle cx="275" cy="56"  r="4" fill="#B8860B"/>
          <circle cx="450" cy="49"  r="4" fill="#B8860B"/>
          <circle cx="625" cy="52"  r="4" fill="#B8860B"/>
          <circle cx="800" cy="38"  r="4" fill="#B8860B"/>

          <!-- Answer Relevancy line (#7B1C3A wine) — scores: 0.79, 0.84, 0.87, 0.90, 0.91 -->
          <polyline points="100,74 275,60 450,52 625,44 800,38"
                    fill="none" stroke="#7B1C3A" stroke-width="2.5" stroke-linejoin="round"/>
          <circle cx="100" cy="74"  r="4" fill="#7B1C3A"/>
          <circle cx="275" cy="60"  r="4" fill="#7B1C3A"/>
          <circle cx="450" cy="52"  r="4" fill="#7B1C3A"/>
          <circle cx="625" cy="44"  r="4" fill="#7B1C3A"/>
          <circle cx="800" cy="38"  r="4" fill="#7B1C3A"/>

          <!-- Context Precision line (#4A7C59 sage) — scores: 0.74, 0.78, 0.82, 0.84, 0.86 -->
          <polyline points="100,86 275,76 450,67 625,62 800,58"
                    fill="none" stroke="#4A7C59" stroke-width="2.5" stroke-linejoin="round"/>
          <circle cx="100" cy="86"  r="4" fill="#4A7C59"/>
          <circle cx="275" cy="76"  r="4" fill="#4A7C59"/>
          <circle cx="450" cy="67"  r="4" fill="#4A7C59"/>
          <circle cx="625" cy="62"  r="4" fill="#4A7C59"/>
          <circle cx="800" cy="58"  r="4" fill="#4A7C59"/>

          <!-- Context Recall line (#4A6080 slate) — scores: 0.71, 0.76, 0.80, 0.82, 0.85 -->
          <polyline points="100,90 275,82 450,68 625,66 800,56"
                    fill="none" stroke="#4A6080" stroke-width="2.5" stroke-linejoin="round" stroke-dasharray="6 3"/>
          <circle cx="100" cy="90"  r="4" fill="#4A6080"/>
          <circle cx="275" cy="82"  r="4" fill="#4A6080"/>
          <circle cx="450" cy="68"  r="4" fill="#4A6080"/>
          <circle cx="625" cy="66"  r="4" fill="#4A6080"/>
          <circle cx="800" cy="56"  r="4" fill="#4A6080"/>
        </svg>

        <!-- Legend -->
        <div class="chart-legend">
          <div class="legend-item">
            <span class="legend-dot" style="background:#B8860B"></span>
            Faithfulness
          </div>
          <div class="legend-item">
            <span class="legend-dot" style="background:#7B1C3A"></span>
            Answer Relevancy
          </div>
          <div class="legend-item">
            <span class="legend-dot" style="background:#4A7C59"></span>
            Context Precision
          </div>
          <div class="legend-item">
            <span class="legend-dot" style="background:#4A6080;width:10px;height:3px;border-radius:0"></span>
            Context Recall
          </div>
        </div>

        <!-- Run summary strip -->
        <div class="run-summary">
          <span>Last run: <strong>May 23 2026, 02:04 UTC</strong></span>
          <span>Scenarios: <strong>25</strong></span>
          <span>Duration: <strong>4m 31s</strong></span>
          <span>Promoted to verified_qa: <strong>18</strong></span>
        </div>
      </div>
    </div>

    <!-- Scenarios tab content (hidden by default in mockup) -->
    <div id="tab-scenarios" style="display:none">
      <div class="table-card">
        <table>
          <thead>
            <tr>
              <th>Question</th>
              <th>Source</th>
              <th>
                <span class="metric-header">
                  <span class="metric-dot" style="background:#B8860B"></span>F
                </span>
              </th>
              <th>
                <span class="metric-header">
                  <span class="metric-dot" style="background:#7B1C3A"></span>AR
                </span>
              </th>
              <th>
                <span class="metric-header">
                  <span class="metric-dot" style="background:#4A7C59"></span>CP
                </span>
              </th>
              <th>
                <span class="metric-header">
                  <span class="metric-dot" style="background:#4A6080"></span>CR
                </span>
              </th>
              <th>✓</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="question-cell">What is your return policy for damaged goods?</td>
              <td><span class="badge badge-generated">generated</span></td>
              <td><span class="score pass">0.941</span></td>
              <td><span class="score pass">0.912</span></td>
              <td><span class="score pass">0.903</span></td>
              <td><span class="score pass">0.921</span></td>
              <td><span class="badge badge-pass">PASS</span></td>
            </tr>
            <tr>
              <td class="question-cell">What are your business hours on public holidays?</td>
              <td><span class="badge badge-generated">generated</span></td>
              <td><span class="score pass">0.897</span></td>
              <td><span class="score warn">0.874</span></td>
              <td><span class="score warn">0.861</span></td>
              <td><span class="score warn">0.843</span></td>
              <td><span class="badge badge-fail">FAIL</span></td>
            </tr>
            <tr>
              <td class="question-cell">Can I get a full refund within 24 hours?</td>
              <td><span class="badge badge-mined">mined</span></td>
              <td><span class="score warn">0.783</span></td>
              <td><span class="score pass">0.921</span></td>
              <td><span class="score warn">0.812</span></td>
              <td><span class="score warn">0.798</span></td>
              <td><span class="badge badge-fail">FAIL</span></td>
            </tr>
            <tr>
              <td class="question-cell">How do I track my order status?</td>
              <td><span class="badge badge-generated">generated</span></td>
              <td><span class="score pass">0.961</span></td>
              <td><span class="score pass">0.944</span></td>
              <td><span class="score pass">0.931</span></td>
              <td><span class="score pass">0.910</span></td>
              <td><span class="badge badge-pass">PASS</span></td>
            </tr>
            <tr>
              <td class="question-cell">Do you ship internationally to South Africa?</td>
              <td><span class="badge badge-generated">generated</span></td>
              <td><span class="score pass">0.912</span></td>
              <td><span class="score pass">0.901</span></td>
              <td><span class="score pass">0.899</span></td>
              <td><span class="score fail">0.672</span></td>
              <td><span class="badge badge-fail">FAIL</span></td>
            </tr>
            <tr>
              <td class="question-cell">What payment methods are accepted?</td>
              <td><span class="badge badge-mined">mined</span></td>
              <td><span class="score pass">0.934</span></td>
              <td><span class="score pass">0.927</span></td>
              <td><span class="score pass">0.918</span></td>
              <td><span class="score pass">0.903</span></td>
              <td><span class="badge badge-pass">PASS</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <hr class="section-divider" />

    <!-- ══════════════════════════════════════════ -->
    <!-- STATE 2 — Empty state (no runs yet)        -->
    <!-- ══════════════════════════════════════════ -->

    <p class="section-label">State 2 — Empty state (no eval runs yet)</p>

    <a href="#" class="back-link">← Back to Configure</a>

    <div class="page-header">
      <div>
        <h1>Run evaluations</h1>
        <p>Ragas-scored nightly tests for your agent's knowledge base.</p>
      </div>
      <button class="btn-primary">▶ Run Eval</button>
    </div>

    <div class="tabs">
      <button class="tab active">Pass Rates</button>
      <button class="tab">Scenarios</button>
    </div>

    <div class="empty-state">
      <span class="empty-icon">📊</span>
      <div class="empty-title">No eval runs yet</div>
      <p class="empty-subtitle">
        Your agent is evaluated automatically each night. Run a check now to see how it performs.
      </p>
      <button class="btn-primary">▶ Run First Eval</button>
    </div>

  </main>
</div>

<script>
function showTab(name) {
  document.getElementById('tab-passrates').style.display = name === 'passrates' ? 'block' : 'none';
  document.getElementById('tab-scenarios').style.display = name === 'scenarios' ? 'block' : 'none';
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', (i === 0 && name === 'passrates') || (i === 1 && name === 'scenarios'));
  });
}
</script>
</body>
</html>
```

---

## 11. Accessibility

- All interactive elements (`button`, `a`) are keyboard-reachable
- Tab bar uses `<button>` elements (not `<div>`) for native keyboard nav
- Score colors supplemented by PASS/FAIL text badge — colour is not the only indicator
- Chart has SVG with meaningful `<text>` axis labels — screen reader can read values
- `aria-selected` on active tab, `role="tablist"` on tab container (implementation-time addition)

---

## 12. Dimension Checklist

| Dimension | Status | Notes |
|-----------|--------|-------|
| 1. Visual hierarchy | ✓ | h1 + subtitle + tab + content; clear information architecture |
| 2. Design system compliance | ✓ | All tokens from Design G; no new colours except 2 one-off metric lines (locked in spec) |
| 3. Interaction states | ✓ | Hover, active tab, disabled button, loading skeleton, empty state all specified |
| 4. Copywriting | ✓ | All copy locked in §9; no lorem ipsum |
| 5. Responsiveness | ✓ | Desktop-only per PRD; min-width 900px noted |
| 6. Accessibility | ✓ | Button elements, colour + text indicators, keyboard nav |

## UI-SPEC COMPLETE
