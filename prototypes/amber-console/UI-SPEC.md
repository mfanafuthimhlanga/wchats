# Amber Console — UI spec (prototype contract)

Read `../../DESIGN.md` first; it is the visual law. This file maps every page: structure, content, states. Prototypes are static HTML in this folder, sharing `tokens.css` + `app.css` (+ `scene.js` on landing/auth only). Open directly via file:// — no build step. All data is realistic mock data (marked in comments), mirroring the real app's content model (see apps/admin/app/**).

## Shared shell (all admin pages)

- Top bar, 56px, bg `--bg`, single bottom hairline: wordmark `w.chats` (mono 14, amber `w.`), center nav links (Agents), right: account chip (initials in 28px surface-2 square, radius 6).
- Content column max-width 1200px, padding 0 32px, 40px top.
- Agent pages (agent.html, soul, ingest, eval, deploy, settings) add a left rail (220px): agent name + id (mono 12 ink-2, e.g. `agent.lindiwe-beauty`), then journey steps as a vertical list: Configure / Ingest / Evals / Deploy, each row = step name + state glyph (done: green ✓ rendered as SVG check; active: amber block; locked: ink-3). Below: Settings link. Rail separates from content with a right hairline. Under 900px the rail becomes a horizontal strip.
- Page header inside content: h1 mono 20/500 + one-line Inter description in ink-2. No glass, no photos.

## Mock data spine (use consistently everywhere)

Tenant: Lindiwe's Beauty Bar (Melville salon). Agents:
1. `agent.lindiwe-beauty` — "Lindiwe's Beauty Bar" — live — 34 docs, pass rate 0.94, 1,208 sessions
2. `agent.yeoville-hardware` — "Yeoville Hardware" — testing — 12 docs, pass rate 0.87
3. `agent.melville-books` — "Melville Stationers" — building — 3 docs, evals pending
Timestamps around 2026-07-08..10, mono `2026-07-09 14:32`.

## Pages

### index.html — landing
Register: brand. three.js scene full-bleed behind hero (55% viewport height fade). Nav: wordmark left; right: Docs, Pricing, Sign in, [Start free] amber button. Hero: left-aligned, max 4 elements: h1 mono 44px, 2 lines: "Ship a support agent that survives the audit." with a blinking amber block cursor after the final period; subtext Inter 16 ink-2 ≤20 words: "W Chats grounds an agent in your documents, scores every answer, and red-teams it before customers ever see it."; CTAs: [Build your agent] amber + [See a live eval] ghost. NO trust strip in hero.
Below hero, 4 sections, different layout families:
1. **Evidence ledger** (full-width): a real bordered table (the product's actual output): 5 eval scenario rows — scenario, faithfulness, relevancy, verdict (PASS/FAIL chips). Caption above: mono 13 ink-2 "Every deploy is gated by this table."
2. **How it works** (3 steps as a single horizontal rule with 3 stations, not cards): Ingest → Evaluate → Deploy, each station: mono step name + 1 Inter sentence.
3. **Red team** (split 50/50): left headline + body; right a terminal-style log excerpt (surface panel, mono 12, 6 lines of probe → blocked entries, e.g. `probe: ignore prior instructions … refused`).
4. **Final CTA** (centered, minimal): one line + [Start free].
Footer: hairline, mono 12 ink-3: wordmark, Docs, Security, Sign in.

### sign-in.html / sign-up.html — auth
Scene at 55% opacity. Centered column 360px: wordmark, panel (surface, radius 10, hairline border): mono h1 16 "Sign in" / "Create your account", email + password inputs, [Continue] amber full-width, below panel: ink-2 12 "No account? Create one" / inverse link. Focus ring: 2px amber outline offset 2. sign-up adds business name input.

### agents.html — dashboard
No rail. Header: h1 "Agents" + right [New agent] amber. Under header: summary line mono 13 ink-2: "1 live · 1 testing · 1 building" (single · allowed twice here as tabular metadata: use two separate stat spans with 24px gaps instead of dots). Then a LEDGER TABLE (not cards): columns Name / id / Status (chip) / Docs / Pass rate / Sessions / Created. Row click affordance: name in ink, hover row surface-2 + name→amber. Status chips: live=green-dim/green, testing=amber-dim/amber, building=surface-2/ink-2. Empty state (render at bottom behind a comment toggle): mono "No agents yet." + one Inter sentence + [New agent].

### agent-new.html — create
No rail. Narrow column 560px. h1 "New agent". Form panel: fields Name, Business type (select), Tone (select: Professional / Friendly / Direct). Inter help lines under each. [Create agent] amber. Right of form (≥1000px): a static provisioning preview panel (surface): mono 12 checklist of what will happen (Provision tenant database / Create knowledge base / Generate soul) each with ink-3 pending squares.

### agent.html — journey home (Configure step active)
Rail present. Header: agent name h1 + status chip + id mono. Content: journey board, 4 step blocks separated by hairlines (no cards-in-cards): each = step name mono 14, one-line state, and either a [primary action] or mono done summary with timestamp. Configure=done (soul saved 2026-07-08 09:12), Ingest=done (34 documents · ready), Evals=active (amber left block accent 2px allowed as active indicator only, not decoration): "Last run pass rate 0.94 · 2026-07-09" + [Run evals], Deploy=available: [Open deploy]. Alerts strip above board when present: one row, red-dim bg, mono 12: "Eval regression on returns-policy scenarios" + [Resolve].

### soul.html — soul editor
Rail. h1 "Soul". Two columns ≥1100px (form 1fr / preview 380px), stacked below. Form: Name, Role (select), Greeting (textarea), Do list / Do-not list: each an editable list (rows with mono text + remove ✕ button as SVG), [Add item] ghost buttons. [Save soul] amber sticky at form bottom. Preview panel: mono label "Live prompt", then a code well (bg near-black `oklch(0.07 0 0)`, mono 12, ink-2, 12 lines of the system prompt) inside the surface panel; caption ink-3 12.

### ingest.html — knowledge base
Rail. h1 "Ingest documents". Tabs (Upload file / Add URL) as mono 13 underline tabs (active: amber underline 2px). Upload zone: dashed hairline, radius 10, mono 13 "Drop PDF, DOCX or MD" + Inter help; on-drag state note in comment. URL tab: input + [Fetch]. Below: "Knowledge base" section (mono 13 label + count chip "34"): ledger table: Document / Type chip / Chunks (right) / Status chip (parsed/processing/failed) / Added (mono). One processing row with amber dot + elapsed mono. Failed row: red chip + [Retry] ghost. Modal spec (documented as comment, optionally rendered hidden): document detail = full-height right sheet, surface, hairline left.

### eval.html — evaluations
Rail. h1 "Run evaluations" + [Run evals] amber right. Stat strip: 4 plain stats (no boxes): big mono 28 number + ink-2 label: Pass rate 0.94 / Scenarios 52 / Failures 3 / Last run 2026-07-09. Hairline below. Chart: pass-rate trend as inline SVG line chart (amber line, hairline grid, 8 points, no libs). Then scenario ledger table: Scenario / Source chip / Faithfulness (mono right) / Relevancy (mono right) / Verdict chip / Ran (mono). 8 rows, 2 FAIL. Failed row expandable spec noted in comment. Empty + running states as commented blocks.

### deploy.html — deploy
Rail. h1 "Deploy". Pre-deploy gate section first: 4 signals as a single ledger list (not cards): Evals pass ≥0.90 ✓ green / Red team: 0 critical ✓ / Knowledge base: 34 docs ✓ / Soul saved ✓ — each row: name, mono value, verdict glyph. Then [Approve deploy] amber (enabled state) + ink-2 note. Embed section: mono label "Embed code", code well with the script tag (mono 12), [Copy] ghost top-right of well. Appearance section: three radio tiles (Floating button / Panel / Inline) as surface-2 tiles with SVG glyphs, selected = amber hairline + amber-dim. Widget preview: right column 320px ≥1100px: the customer widget mock (this one keeps friendly styling: white chat card, user/agent bubbles, THIS is the only surface allowed emoji 👋 inside chat copy).

### settings.html
Rail. h1 "Settings". Single panel: Agent name (disabled input, value + ink-3 note "Renaming arrives with multi-agent workspaces"), Danger zone: hairline-top section, red ghost [Delete agent] + Inter warning sentence.

## Component states (all pages)

Buttons: default / hover (brightness 1.08) / active (translateY 1px) / focus-visible (2px amber outline, offset 2) / disabled (surface-2 + ink-3). Inputs: surface-2 bg, hairline border, focus = amber border + outline. Chips: pill, mono 11. Tables: hover rows. Every interactive element keyboard reachable; skip-to-content link on admin pages.

## Bans (verbatim from skills, enforced)

No em/en dashes anywhere visible. No Fraunces/serif. No glassmorphism. No photo backgrounds. No cards-in-cards. No eyebrow above every section. No decorative dots. No `border-t`+`border-b` double rules. No fake version footers. Amber ≤10% per screen. `prefers-reduced-motion` honored (scene → static frame, cursor solid).
