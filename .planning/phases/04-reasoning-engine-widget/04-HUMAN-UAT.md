---
status: partial
phase: 04-reasoning-engine-widget
source: [04-VERIFICATION.md]
started: 2026-05-17T00:00:00Z
updated: 2026-05-17T00:00:00Z
---

## Current Test

[awaiting human decision]

## Tests

### 1. AGT-10: Public test site — visitor asks a question and receives a grounded answer with citations
expected: A publicly accessible URL where a visitor can embed the widget, ask a question against a real ingested corpus, and receive a non-empty `agent.response` with at least one citation in the footer. This is ROADMAP Success Criteria #1.
result: [pending]

**Context for decision:**
- The full local stack is wired and working. `test_agent_e2e.py` (AGENT_E2E_ENABLED=1) proves the chain: ingest → retrieve → agent → widget → response with citations, all against a real Claude API call.
- Plan 04-08 (which held the public demo page + human checkpoint) was superseded and replaced by plan 04-09 (cleanup/rebrand) and plan 04-10 (Clerk auth).
- `apps/demo/index.html` now redirects to `/sign-in` — there is no demo page for anonymous visitors.
- AGT-10 as written requires "public test site" — internet-accessible hosting.

**Options:**
1. **Deploy now** — deploy the stack (Fly.io, Railway, Render, or similar) and confirm the widget works publicly. Full AGT-10 satisfaction.
2. **Re-scope AGT-10** — formally accept that M4's hireable artifact is the local-runnable stack + Clerk-authenticated demo, and that public hosting is a post-M4 concern. Update REQUIREMENTS.md and ROADMAP to reflect this decision.
3. **Defer to M5** — track public deployment as a prerequisite for M5, close M4 now with a noted exception.

## Summary

total: 1
passed: 0
issues: 0
pending: 1
skipped: 0
blocked: 0

## Gaps
