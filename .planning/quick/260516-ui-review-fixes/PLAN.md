---
slug: ui-review-fixes
date: 2026-05-16
status: in-progress
---

# Quick Task: Fix P0+P1 UI Review Findings

Fix all P0 (accessibility violations) and P1 (visible spec gaps) findings from
04-UI-REVIEW.md across the widget and admin surfaces, then re-run Playwright
screenshot audit to verify scores improve.

## Tasks

### Wave 1 — widget.css (CSS-only fixes)
- [ ] T1: Citation row background `--surface-1` → `--surface-2`
- [ ] T2: Escalation panel add `border-left: 3px solid var(--gold)`
- [ ] T3: Escalation submit button `--gold` → `--accent` fill
- [ ] T4: Add `--shadow-focus` token and apply on send button focus
- [ ] T5: Disclosure bar "Powered by AI" text: `--text-3` → `--text-2` (contrast fix)
- [ ] T6: TypingIndicator wrapped in agent-bubble container style

### Wave 2 — Component: EscalationPanel.jsx
- [ ] T7: Add `role="dialog"` `aria-modal="false"` to panel root
- [ ] T8: Add explicit `<label>` elements for name/email inputs
- [ ] T9: Change header copy to "Flagged for our team"
- [ ] T10: Fix submit button class to use `--accent`

### Wave 3 — Component: InputBar.jsx
- [ ] T11: Add `aria-label="Message"` to textarea
- [ ] T12: Add spinner SVG swap when `submitting` prop is true

### Wave 4 — New components
- [ ] T13: Create AgentCluster.jsx (AgentNameLabel + AGENT badge)
- [ ] T14: Create UserMeta.jsx (You · HH:MM timestamp)
- [ ] T15: Create EmptyState.jsx (greeting)

### Wave 5 — Widget.jsx restructure
- [ ] T16: Use AgentCluster for agent messages, UserMeta after user messages
- [ ] T17: Move EscalationPanel outside scroll-area
- [ ] T18: Show TypingIndicator during tool_call (both visible)
- [ ] T19: Add auto-scroll via useRef on messages/status change
- [ ] T20: Add EmptyState when messages.length === 0
- [ ] T21: Fix error state: only show after send attempt, not on config fail
- [ ] T22: Thread agentName from config into Widget state

### Wave 6 — Admin: page.tsx
- [ ] T23: Fix tab names to spec (Overview/Soul/Conversations/Retrieval/Settings)
- [ ] T24: Change Role from input to select with presets
- [ ] T25: Form label style: 11px, uppercase, letter-spacing 0.08em, --text-3
- [ ] T26: Validation only fires on blur/save, not on mount
- [ ] T27: Inactive tabs get aria-disabled + cursor-not-allowed
- [ ] T28: Add responsive collapse at <1100px (hide preview panel)

### Wave 7 — Verify
- [ ] T29: Re-run Playwright screenshot audit
- [ ] T30: Commit
