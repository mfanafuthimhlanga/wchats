# Plan: M3 — the widget and the agent endpoint become products (7.1-7.6)

Branch: `chore/m0-gate-followups`. Credential-free; runs while `7.7` waits on the owner's keys.

## Goal

The six located defects on the two runtime surfaces fixed, each with a test observed to fail.

## Phases

1. `7.4` chat-route-ungated: `POST /api/v1/agents/{id}/chat` gains the widget path's 60/min rate
   limit and daily budget guard. Tests: 429 and budget-refusal cases, mutation-proved.
2. `7.3` sse-no-terminal-event: the stream closes itself after the agent's terminal event.
   Investigate what `run_agent_turn` emits last; extend `TERMINAL_EVENTS` or emit a terminal
   marker; late-join replay must survive. Tests in `test_sse.py`.
3. `7.2` widget-theming-inert: config route serves `agent.widget_config` over defaults; injected
   CSS variable names mapped to what `widget.css` actually consumes; `agent_name` field fixed.
4. `7.1` snippet-two-generators: one snippet generator (the API's), carrying `data-api` from
   config; the console renders what the API returns. Appearance modes stay for M5.
5. `7.5` embed-artifact-stale: `embed/` rebuilt from `dist/` in the build, drift gated by hash;
   stale README size claim corrected.
6. `7.6` eval-page-false-nightly-claim: honest copy, plus a wiring-check pin so the false claim
   cannot return while no beat exists.
7. Two adversarial verifiers (api half, surface half), mutations with restore hygiene.
8. Orchestrator: full battery detached, commits per logical change, BACKLOG closures, trace.

## Constraints

- 4 GB: agents sequential; each runs only its targeted test files, never the full suite.
- Execution on Opus, bounded briefs; verification on the session model.
- Widget stays under 20480 bytes gzip; api changes stay inside the new lizard/ruff floors.

## Risks

- `7.3` touches replay semantics guarded by 450 lines of SSE tests; the fix must thread them.
- `7.2` needs a semantic mapping (config key to consumed variable), easy to get cosmetically
  wrong; verifier checks every injected variable is consumed by at least one CSS rule.
