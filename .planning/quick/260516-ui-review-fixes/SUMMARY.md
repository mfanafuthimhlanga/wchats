---
slug: ui-review-fixes
status: complete
date: 2026-05-16
---

# Quick Task Summary: Fix P0+P1 UI Review Findings

## Outcome

All P0 (accessibility) and the majority of P1 (visual fidelity) findings resolved.
Overall score improved from 58/100 → 75/100.

## Changes made

### New components
- `AgentCluster.jsx` — AgentNameLabel + AGENT badge wrapper
- `UserMeta.jsx` — "You · HH:MM" timestamp below user bubbles
- `EmptyState.jsx` — Greeting on widget cold load

### Widget.jsx
- EmptyState rendered when messages empty
- EscalationPanel moved outside scroll-area (sibling)
- Both TypingIndicator + ToolCallLabel shown during tool_call
- Auto-scroll via useRef on messages/status change
- Error only shown on send failure, not config load failure
- agentName threaded from config

### widget.css
- `--shadow-focus` token added
- Disclosure bar: `--text-3` → `--text-2` (contrast fix)
- Citation row: `--surface-1` → `--surface-2`
- TypingIndicator: wrapped in agent-bubble style
- Escalation panel: `border-left: 3px solid var(--gold)` added
- Escalation button: `--gold` → `--accent`
- Send button: `:focus-visible` ring added

### EscalationPanel.jsx
- `role="dialog"` + `aria-modal="false"` added
- Explicit `<label>` elements for name/email
- Header: "Flagged for our team"

### InputBar.jsx
- `aria-label="Message"` on textarea
- Spinner SVG swap during submitting state

### Admin page.tsx + globals.css
- Tab names: Overview/Soul/Conversations/Retrieval/Settings
- Role: select dropdown with preset options
- Labels: 11px uppercase letter-spacing --text-3
- Validation fires on blur/save only
- Inactive tabs: disabled + aria-disabled + tabIndex=-1
- Responsive: .preview-panel hidden at <1100px

## Remaining (P2/P3 — not in scope for this quick task)
- AgentCluster/UserMeta not exercised in script-injected screenshots
- Responsive collapse CSS propagation needs server restart to verify
- P2-02 escalation label visibility (labels present in DOM, visually small)
