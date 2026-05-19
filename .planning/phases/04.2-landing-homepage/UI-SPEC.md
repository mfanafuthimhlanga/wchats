# UI-SPEC — Phase 4.2: Landing Page & App Homepage

**Phase:** 4.2  
**Goal:** Create a public landing page (pre-auth) and wire the full authenticated app shell so a user can flow from sign-up through provisioning, configuring, testing, and deploying their RAG agent.  
**Design system:** `design-g.html` (Parchment & Wine — Design G tokens)  
**Layout decision:** ✅ **LOCKED — Layout B (Top-nav Hub)** — `layout-b-topnav.html`  
**Reference mockup:** `.planning/phases/04.2-landing-homepage/layout-b-topnav.html`

---

## 1. Context & Existing State

| Existing | Status |
|----------|--------|
| `app/sign-in/[[...sign-in]]/page.tsx` | Clerk SignIn, public route — keep as-is |
| `app/agents/[id]/soul/page.tsx` | Soul editor — keep, integrate into shell |
| `middleware.ts` | Public: `/sign-in(.*)`, `/sign-up(.*)` — add `/` |
| `app/globals.css` | Design-G tokens already loaded — reuse exactly |
| Clerk auth | `@clerk/nextjs` 7.3.5, `NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=/agents` |

**Gap:** No landing page, no `/` route, no app shell, no `/agents` dashboard, no `/sign-up` page, no per-agent journey view, no ingest/eval/deploy pages, no widget customization screen.

---

## 2. Layout Architecture — Top-nav Hub

**Chosen pattern:** sticky 56px top navigation bar + full-width content area. No persistent sidebar.

### Top nav (`<TopNav>`)
- Height: 56px, background: `--bg` (#FDF9F5 alabaster — matches page content exactly, no internal contrast banding)
- Border-bottom: 1px `--border-soft`
- Left: Veridian logo mark (30×30px wine square, rounded 7px) + wordmark
- Center-left: nav links (Agents, Evals, Settings) — active link gets `--accent-dim` pill
- Right: notification icon button + user avatar (32px circle, `--accent` fill, initials)

**Background rule (LOCKED):** The entire app shell — nav, page content, journey panels, section backgrounds — uses `--bg` (#F0E8E0). Cards that need subtle elevation (stat cards, substep cards, widget preview card, customise panel) use `--surface-2` (#F7F0EA). `--surface-1` (#FFFCF9) is reserved exclusively for form inputs and select fields — never for card or panel backgrounds. Agent cards (dashboard) and how-it-works step cards sit flush on `--bg` and are defined by their border + shadow only.

---

## 3. Route Map (full app)

### Public routes
| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | `app/page.tsx` | **Landing page** — two-column hero + How it works steps |
| `/sign-in/[[...sign-in]]` | existing | Clerk hosted sign-in |
| `/sign-up/[[...sign-up]]` | `app/sign-up/[[...sign-up]]/page.tsx` | Clerk hosted sign-up |

### Protected routes (require Clerk session)
| Route | Component | Purpose |
|-------|-----------|---------|
| `/agents` | `app/agents/page.tsx` | Dashboard — stats row, agent cards, empty state |
| `/agents/new` | `app/agents/new/page.tsx` | Create agent wizard (name → provision via SSE) |
| `/agents/[id]` | `app/agents/[id]/page.tsx` | **Agent journey view** — vertical 4-step stepper |
| `/agents/[id]/soul` | existing (keep) | Soul editor — integrate into journey step 2 |
| `/agents/[id]/ingest` | `app/agents/[id]/ingest/page.tsx` | Document upload + SSE pipeline status |
| `/agents/[id]/eval` | `app/agents/[id]/eval/page.tsx` | Eval results + run eval (stub) |
| `/agents/[id]/deploy` | `app/agents/[id]/deploy/page.tsx` | Embed code + widget customization + preview |

### Middleware update
Add `/` to `isPublicRoute` matcher in `middleware.ts`.

---

## 4. User Flow

```
/ (landing)
  ├─ "Sign In"      → /sign-in → /agents (Clerk fallback redirect)
  └─ "Get Started"  → /sign-up → /agents (Clerk fallback redirect)

/agents (dashboard)
  ├─ Empty state: "Create your first agent" → /agents/new
  └─ Agent card click → /agents/[id]
       Agent card also shows journey progress bar (x of 4 steps)

/agents/new
  └─ Submit name → POST /api/v1/agents → SSE progress → redirect /agents/[id]

/agents/[id] (journey view — vertical stepper left, content right)
  ├─ Step 1 — Provision (auto-complete on create, shows ✓)
  ├─ Step 2 — Configure
  │     ├─ Substep: Soul → /agents/[id]/soul
  │     └─ Substep: Ingest → /agents/[id]/ingest
  ├─ Step 3 — Test → /agents/[id]/eval
  └─ Step 4 — Deploy
        ├─ Substep: Embed Code → copy widget snippet
        └─ Substep: Customise Widget → /agents/[id]/deploy?tab=design
```

---

## 5. Design Tokens (from design-g.html — verbatim)

```css
--bg:            #F0E8E0   /* warm parchment — PRIMARY app background */
--surface-1:     #FFFCF9   /* form inputs and select fields ONLY */
--surface-2:     #F7F0EA   /* elevated cards (stat, substep, widget preview, customise panel) */
--surface-3:     #EDE3D8   /* icon containers, disabled states, deep inset */
--border:        #D9CCBE
--border-soft:   #EDE3D8
--border-hard:   #B8906A
--accent:        #7B1C3A
--accent-hover:  #5E1229
--accent-deep:   #3D0B1F
--accent-dim:    rgba(123,28,58,0.08)
--gold:          #B8860B
--gold-light:    #FEF9E7
--gold-soft:     #E8D8A6
--text-1:        #1A0A0F
--text-2:        #4A2030
--text-3:        #8A6060
--text-4:        #C4A0A0
--red:           #B91C1C
--red-bg:        #FEF2F2
--green:         #166534
--green-bg:      #F0FDF4
--amber:         #92400E
--amber-bg:      #FEF3C7
--radius-xs:     8px
--radius-sm:     14px
--radius-md:     20px
--font-sans:     "Inter", system-ui
--font-mono:     "JetBrains Mono", monospace
```

**Rules:**
- Never introduce new color values. Every color must resolve to a variable above.
- `--bg` is the page-level background everywhere (nav, content, journey panels, landing sections, how-it-works, agent cards, journey-nav panel).
- `--surface-1` only for `<input>`, `<textarea>`, `<select>` backgrounds. No exceptions.
- `--surface-2` for cards that need visible elevation: stat-card, substep-card, check-item, widget-preview-card, customise-panel, customise-panel-footer, appearance-card, radius-pill, font-select-field.
- `--surface-3` for icon container backgrounds (e.g. appearance-card-icon) and disabled/locked states.

---

## 6. Component Inventory

### Shell (new, shared across protected routes)
| Component | Path | Description |
|-----------|------|-------------|
| `TopNav` | `app/components/TopNav.tsx` | 56px sticky nav — logo, links, user avatar |
| `JourneyStepper` | `app/components/JourneyStepper.tsx` | Left-panel vertical 4-step nav for agent pages |
| `AgentCard` | `app/components/AgentCard.tsx` | Dashboard card with progress bar + status chip |
| `StepSubtaskCard` | `app/components/StepSubtaskCard.tsx` | Substep card within a journey step (completed/active states) |

### Page Components (new)
| Page | Key UI Elements |
|------|----------------|
| Landing `/` | Two-column hero (copy left, step-pill preview right), How it works 4-card row |
| Dashboard `/agents` | Greeting, stats row (3 cards), agent card grid, empty state |
| Journey `/agents/[id]` | JourneyStepper left, active step content right with substep cards |
| Soul `/agents/[id]/soul` | Existing editor + "← Back to Configure" breadcrumb link + full system prompt preview panel |
| Ingest `/agents/[id]/ingest` | Upload zone, doc list with SSE status, pipeline status panel |
| Eval `/agents/[id]/eval` | Ragas metrics table, run eval button, pass/fail badge (stub) |
| Deploy `/agents/[id]/deploy` | Embed/Design sub-tabs, embed code block, widget customizer |

---

## 7. Landing Page Spec

### Two-column hero (grid: 1fr 1fr, gap 64px)
**Left:**
- Tag pill: "For non-technical founders & teams" — `--accent-dim` bg, wine border, wine text
- Headline: 52px, weight 800, letter-spacing -0.03em — one word in `--accent`
- Subtext: 17px, `--text-2`, line-height 1.6, max-width 480px
- CTA row: "Start for free →" (wine primary, 14px 28px) + "Sign in to dashboard" (outline, same height)
- Proof line: 12px mono `--text-4`

**Right (step-pill preview — background `--bg`):**
- 4 `.hero-step-pill` cards stacked (gap 12px), each on `--bg`, border `--border-soft`, `--radius-xs`, `--shadow-card`
- Shows the 4-step journey: Provision ✓, Configure ✓, Test (running), Deploy (locked)
- Done pills: wine filled circle; Active: wine outline circle; Locked: `--surface-3` circle

### How it works section
- `--bg` background, `--border-soft` top border
- Section label: 11px uppercase `--text-3` with wine rule `::before`
- 4-card horizontal grid: numbered circle (wine), title, description

---

## 8. Agent Journey View Spec (`/agents/[id]`)

Two-column layout: 320px left nav + fluid right content.

### Left — JourneyStepper
- Agent name (18px bold) + role subtitle
- 4 step items stacked; each: circle indicator + title + subtitle
- Vertical connector line between steps (wine for done→done, `--border-soft` otherwise)
- Active step: `--accent-dim` background + wine border on container
- Step states: done (wine fill ✓), active (wine outline), locked (surface-3, 60% opacity)
- Done steps also show a green "Done" chip aligned right

### Right — Step content
- Title + subtitle for active step
- SubtaskCards: icon (40px `--accent-dim` square) + title + description + CTA button
  - Completed substep: green left border (3px)
  - Active substep: wine left border + primary CTA button
- Warning banner (gold-light bg, gold-soft border, gold left-border) when a prerequisite is missing

---

## 8b. Soul Editor — System Prompt Preview Panel

The Soul editor page (`/agents/[id]/soul`) has a collapsible dark preview panel below the form grid.

**Panel structure:**
- Background: `--accent-deep` (#3D0B1F)
- Header row (flex, space-between): `--bg` rgba overlay — left: "Live System Prompt Preview" label (12px uppercase mono), right: "Copy prompt" button + "▼ collapse" toggle
- Body: scrollable (max-height 360px, custom scrollbar), 20px × 24px padding, font-mono 12px, line-height 1.75

**Copy prompt button:**
- `padding: 4px 12px`, `rgba(255,255,255,0.10)` bg, `rgba(255,255,255,0.20)` border, `--radius-xs`
- Color: `rgba(255,255,255,0.72)` — hover brightens to `0.90`
- Clicking copies the full plain-text prompt to clipboard (no page reload)

**Full prompt content (generated from form field values):**
```
# ── Veridian · {agent_name} · Generated system prompt ──────────────

You are a {role} agent named {agent_name}, operating on behalf of {org}.

Voice and tone:
{voice_tone}

You MUST always:
{do_list items as bullet lines}

You MUST NEVER:
{dont_list items as bullet lines}

Knowledge base:
{doc_count} documents indexed · Hybrid retrieval (dense + BM25) · Top-5 results
Always cite retrieved chunks. If no relevant chunk is found, say so.

Tools available:
retrieve(query, top_k=5)  → returns ranked chunks with source citations
escalate(reason)          → hands off to human agent queue

# ── Runtime context (injected per session) ──────────────────────────
session_id:  {session_id}
tenant_id :  {tenant_id}
agent_id  :  {agent_id}
```

**Color coding (dark theme):**
- Section headers / key names: `#93C5FD` (blue)
- Tool names / runtime keys: `#F9A8D4` (pink)
- Body text: `rgba(255,255,255,0.82)`
- Runtime values: `#86EFAC` (green)
- Comment lines (`# ──…`): `rgba(255,255,255,0.30)`

---

## 9. Widget Customization Spec (`/agents/[id]/deploy` — Design sub-tab)

**This is a first-class feature, not a stub.** Full implementation required in Phase 4.2.

### Access
Deploy page has two sub-tabs: `Embed Code` | `Customise Widget`. Customise Widget is the Design sub-tab.
In the journey view, Deploy step shows two substep cards: "Embed Code" and "Customise Widget".

### Three-column panel layout

**Col 1 — Appearance Mode (255px)**
Three stacked radio cards — vertical layout (icon + radio in top row, name + hint text below).
- Card background: `--surface-2`; selected: `--accent-dim` + wine border
- Top row: icon (36×36px, `--surface-3` bg, 8px radius) left-aligned; radio-dot (16px) right-aligned
- Name: 13.5px weight 600; hint: 12px `--text-3`, line-height 1.45
- Col-label has a `--border-soft` bottom rule separator before the first card
| Mode | Description |
|------|-------------|
| Floating Button | Circular launcher fixed to page corner — chat opens on click |
| Floating Mini-modal | Compact card showing greeting + input, expands on click |
| Slide-out Panel | Full-height panel slides in from right page edge |

**Col 2 — Style Pickers (310px)**
All widget elements individually configurable. Each row: color dot (20px) + label + hex value (mono).

| Element | Default |
|---------|---------|
| Widget Background | `--bg` #FDF9F5 |
| Header Background | `--accent` #7B1C3A |
| Header Text | #FFFFFF |
| Agent Bubble Background | `--bg` #FDF9F5 |
| Agent Bubble Text | `--text-2` #4A2030 |
| User Bubble Background | `--accent` #7B1C3A |
| User Bubble Text | #FFFFFF |
| Send Button | `--accent` #7B1C3A |
| Input Field Background | `--surface-2` #F7F0EA |

Typography controls:
- Font family: Inter (default) / System UI / Georgia / Custom (text input)
- Border radius preset: Sharp (4px) / Rounded (14px, default) / Pill (24px)

**Col 3 — Live Preview (auto)**
- Updates reactively as user changes pickers (client-side state, no API call)
- Shows widget in selected appearance mode with applied styles
- Expanded chat view: disclosure bar + agent bubble + user bubble + input/send
- Floating launcher: 42px circle below the expanded preview, with chat icon

### Persistence
- `POST /api/v1/agents/{id}/widget-config` — saves appearance mode + style overrides as JSON
- `GET /api/v1/agents/{id}/widget-config` — loads on page open to restore saved design
- "Save Design" button triggers POST; shows green confirmation inline
- Saved design is applied when the widget script is loaded — no redeploy needed
- "Edit Design" link on the Deploy overview card returns to the customization panel

### Widget config schema (stored in DB)
```json
{
  "appearance": "floating-button | floating-mini-modal | slide-out-panel",
  "launcher_shape": "circle | square",
  "colors": {
    "widget_bg": "#FDF9F5",
    "header_bg": "#7B1C3A",
    "header_text": "#FFFFFF",
    "agent_bubble_bg": "#FDF9F5",
    "agent_bubble_text": "#4A2030",
    "user_bubble_bg": "#7B1C3A",
    "user_bubble_text": "#FFFFFF",
    "send_button": "#7B1C3A",
    "input_bg": "#F7F0EA"
  },
  "typography": {
    "font_family": "Inter | System UI | Georgia | custom",
    "font_custom_url": null,
    "border_radius_preset": "sharp | rounded | pill"
  }
}
```

---

## 10. Accessibility Requirements

- All interactive elements min 44×44px touch target
- Color contrast: text on `--bg` must meet WCAG AA (4.5:1 body, 3:1 large text)
- Focus rings: `box-shadow: 0 0 0 3px rgba(123,28,58,0.18)`
- Semantic HTML: `<nav>`, `<main>`, `<header>`, `<section>` as appropriate
- `aria-current="page"` on active nav link; `aria-selected="true"` on active sub-tab
- Color picker swatches need `aria-label` with color name + hex value
- Widget preview marked `aria-hidden="true"` (decorative)

---

## 11. Responsive Breakpoints

| Breakpoint | Behavior |
|-----------|----------|
| ≥1100px | Full two-column layout (journey stepper left, content right) |
| 768–1099px | Journey stepper collapses to top step-progress strip; content full width |
| <768px | Single column; stepper hidden, replaced by step badge in header; sub-tabs scroll horizontally |

---

## 12. Authentication Integration

- Landing page: **server component**, no auth check, static render
- Protected pages: `useAuth()` + `auth.protect()` in middleware
- `getToken()` for all API calls (same pattern as existing soul editor)
- After sign-up/sign-in: Clerk redirects to `/agents` (already in `.env.local`)
- User avatar from `useUser()` Clerk hook in `<TopNav>`

---

## 13. API Integration Points

| Page | Call | Endpoint |
|------|------|---------|
| `/agents` | List agents | `GET /api/v1/agents` |
| `/agents/new` | Create + provision | `POST /api/v1/agents` → SSE `GET /api/v1/jobs/{id}/events` |
| `/agents/[id]` | Fetch agent + journey state | `GET /api/v1/agents/{id}` |
| `/agents/[id]/ingest` | Upload docs | `POST /api/v1/agents/{id}/documents` → SSE |
| `/agents/[id]/eval` | Run eval | `POST /api/v1/agents/{id}/eval` (stub — endpoint TBD) |
| `/agents/[id]/deploy` | Embed config | `GET /api/v1/agents/{id}/widget` |
| `/agents/[id]/deploy` | Save widget design | `POST /api/v1/agents/{id}/widget-config` |
| `/agents/[id]/deploy` | Load saved design | `GET /api/v1/agents/{id}/widget-config` |

---

## 14. Execution Status

**Layout:** ✅ Layout B — Top-nav Hub (locked 2026-05-18)  
**Mockup:** `layout-b-topnav.html` (6 screens including widget customization)  
**Widget customization:** ✅ Logged as first-class feature — full implementation required  
**Execution:** Ready — plan files written, run `/gsd-execute-phase 4.2`
