---
phase: 04-reasoning-engine-widget
plan: "05"
subsystem: widget
tags: [preact, vite, widget, css, sse, jwt, wcag]
dependency_graph:
  requires: [04-04]
  provides: [apps/widget build, widget IIFE bundle, Design G CSS, SSE client, JWT memory storage]
  affects: [apps/widget/dist/widget.iife.js]
tech_stack:
  added: [preact@10.29.1, vite@8.0.13, "@preact/preset-vite@2.10.5", terser@5.47.1]
  patterns: [Preact IIFE library build, module-scope JWT, EventSource SSE, CSS custom properties]
key_files:
  created:
    - apps/widget/package.json
    - apps/widget/vite.config.js
    - apps/widget/scripts/check-size.mjs
    - apps/widget/.gitignore
    - apps/widget/index.html
    - apps/widget/src/index.jsx
    - apps/widget/src/Widget.jsx
    - apps/widget/src/widget.css
    - apps/widget/src/api.js
    - apps/widget/src/sse.js
    - apps/widget/src/components/DisclosureBar.jsx
    - apps/widget/src/components/MessageBubble.jsx
    - apps/widget/src/components/CitationRow.jsx
    - apps/widget/src/components/TypingIndicator.jsx
    - apps/widget/src/components/ToolCallLabel.jsx
    - apps/widget/src/components/EscalationPanel.jsx
    - apps/widget/src/components/InputBar.jsx
decisions:
  - "Bundle output: IIFE format (widget.iife.js) per R-04 — single self-contained script for iframe embed"
  - "JWT storage: module-scope let _jwt only — never written to localStorage/sessionStorage (XSS mitigation T-04-05-01)"
  - "CSS: fully inline widget.css, no @import url(), no Google Fonts — mitigates T-04-05-03"
  - "Send button: 44x44px enforced via both CSS class and inline style — dual WCAG 2.5.5 guard"
  - "Bundle size gate: Node zlib.gzipSync — cross-platform, no gzip CLI dependency on Windows"
metrics:
  duration_minutes: 15
  completed_date: "2026-05-16"
  tasks_completed: 2
  files_created: 17
---

# Phase 04 Plan 05: Preact Widget Summary

Preact iframe chat widget built at apps/widget/ with IIFE bundle, Design G (Parchment & Wine) CSS tokens, JWT in-memory auth, and SSE streaming — gzipped bundle 7218 bytes (35% of 20480-byte limit).

## Final Widget Bundle Size

- **Raw:** 17.83 KB (`dist/widget.iife.js`)
- **Gzipped:** **7218 bytes** (7.05 KB) — 35.2% of the 20480-byte hard limit
- **CSS (separate):** 4.71 KB raw / 1.44 KB gzip (inlined at runtime via Vite)
- Gate result: `Bundle size OK: 7218 bytes` (postbuild script exit 0)

## Component File List

| File | Purpose |
|------|---------|
| `src/Widget.jsx` | Root state machine — idle/loading/submitting/thinking/tool_call/escalated/error |
| `src/api.js` | fetch wrappers for /config and /chat with in-memory JWT |
| `src/sse.js` | EventSource wrapper for /widget/jobs/{job_id}/events |
| `src/widget.css` | All Design G tokens + component styles (single bundled stylesheet) |
| `src/index.jsx` | Entry point — URL param parsing, render to #root |
| `src/components/DisclosureBar.jsx` | 32px "Powered by AI" + mono version tag |
| `src/components/MessageBubble.jsx` | Agent (left, accent border) and user (right, accent fill) variants |
| `src/components/CitationRow.jsx` | Citation footer with SVG doc icon, document_name + section, VIEW link |
| `src/components/TypingIndicator.jsx` | Three pulsing dots, aria-label="Agent is typing" |
| `src/components/ToolCallLabel.jsx` | Dashed gold border, blinking dot, mono tool_name(input) |
| `src/components/EscalationPanel.jsx` | Gold-themed panel with name/email form + "Got it" confirmation |
| `src/components/InputBar.jsx` | Textarea + 44x44px send button, Enter-to-submit, Shift+Enter newline |

## Theming Application Path

```
GET /widget/{agent_id}/config
  → response.theming: { primary_color, accent_gold, font_family, border_radius, background }
  → Widget.jsx useEffect:
      Object.entries(cfg.theming).forEach(([k, v]) =>
        document.documentElement.style.setProperty(`--${k.replace(/_/g, '-')}`, v))
  → CSS custom properties on :root overridden at runtime
```

## SSE Event Handler Map

| Event | State Transition | Action |
|-------|-----------------|--------|
| `agent.thinking` | → `thinking` | Show TypingIndicator |
| `agent.tool_call` | → `tool_call` | Show ToolCallLabel with tool_name |
| `agent.tool_result` | → `thinking` | Return to TypingIndicator |
| `agent.response` | → `idle` | Append agent message + CitationRow; close EventSource |
| `agent.escalated` | → `escalated` | Show EscalationPanel; stream stays open |
| `agent.failed` | → `error` | Show error message; close EventSource |
| `onerror` | → `error` | Show error message; close EventSource |

## JWT Storage Policy

**Policy:** Module-scope variable only — `let _jwt = null` in `src/api.js`. Never written to Web Storage.

**Grep evidence (zero matches required):**
```
grep -rn "localStorage|sessionStorage" apps/widget/src/  → ZERO matches
```

Mitigates T-04-05-01: XSS exfiltration via storage APIs. JWT lifetime is 15 min (API-enforced), capping blast radius.

## WCAG 2.5.5 Send Button Confirmation

Send button in `src/components/InputBar.jsx` has **both** CSS class and inline style enforcement:
- CSS class `.input-bar button.send`: `width: 44px; height: 44px; min-width: 44px; min-height: 44px`
- Inline style: `style="width:44px;height:44px;min-width:44px;min-height:44px;"`

Meets WCAG 2.5.5 (minimum 44×44 CSS pixels target size). Dual-enforcement guards against CSS specificity override.

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

Files verified present:
- apps/widget/dist/widget.iife.js: FOUND (post-build)
- apps/widget/src/Widget.jsx: FOUND
- apps/widget/src/api.js: FOUND
- apps/widget/src/sse.js: FOUND
- apps/widget/src/widget.css: FOUND (contains `--accent: #7B1C3A`)
- All 7 component files: FOUND

Commits verified:
- 37cde38 (Task 1 scaffold): FOUND
- 137d3e5 (Task 2 source): FOUND
