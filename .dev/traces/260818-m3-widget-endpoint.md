# Trace: M3 — the widget and the agent endpoint become products

Plan `.dev/plans/260816-m3-widget-endpoint.md`, workflow `.dev/workflows/m3-widget-endpoint.workflow.js`
(3 Opus implementers, 2 session-model verifiers, sequential for the 4 GB box), then one Opus pass on
the blocking defects the verifiers found. Closed: `7.1`-`7.6`, `7.23`.

## What shipped

| Row | Change |
|---|---|
| `7.4` | The authenticated chat route gains the 60/min ceiling and the daily budget guard, via a shared `rate_limit.py` rather than a second copy. Its own bucket prefix, so an integration cannot starve the tenant's live widget customers |
| `7.3` | `event_generator` takes a per-stream terminal set; the widget route stops at `agent.response`/`agent.failed` |
| `7.2` | Config serves stored per-tenant theming; `theming.js` maps API keys to the variables the stylesheet actually reads; `check-theming-contract.mjs` gates it |
| `7.1` | The API is the single snippet generator, emits `data-api`, reads both hosts from settings, exposed at `GET /agents/{id}/embed-snippet`; the console renders what it returns |
| `7.5` | `sync-embed.mjs` keeps `dist/`, `embed/` and `apps/admin/public/wchats/` in step, gate blocks on drift |
| `7.6` | The eval page no longer claims nightly evals that no deployed beat worker performs |
| `7.23` | 401 re-mints the widget JWT once and retries |

## The four things worth remembering

**A per-stream terminal set, not a global one.** Adding `agent.response` to `TERMINAL_EVENTS`
directly would also have truncated the authenticated admin stream — and `run_agent_turn` dispatches
the judge chain *after* emitting the answer, so the admin stream is the only place those verdicts
are ever watched live. A distinct terminal marker was rejected for a different reason: it occupies
the same position in `created_at` order, so it truncates a replay identically while costing a
`job_events` row per turn and a worker path that can fail after the customer already has the answer.

**Making theming work is what made a control disappear.** `header_bg` and `user_bubble_bg` both
default to `#7B1C3A` in the API's own schema, and both fed variables the citation row and its VIEW
link share: 1.00:1 at the schema defaults, on a widget that had been legible while inert. The naive
repair created a second one — `header_text` (white by default) also wrote the citation row's own
text colour, 1.13:1 — so both header keys moved to variables only the disclosure bar reads.
Restored to 8.99:1 and 12.08:1, computed rather than eyeballed. **A theming layer that does nothing
cannot be wrong; switching it on is what makes contrast a live risk.**

**The rate limit was a cross-tenant vector for one ordering.** Counted before the ownership SELECT
and keyed on a public `agent_id`, any valid API key could burn 61 requests a minute against a
victim tenant's agent — 404 each time, while the victim's own integration ate the 429. The guard
now sits below the ownership check. The comment justifying the original order ("before any DB
access") was also false: the auth dependency already runs a SELECT and an argon2 verify.

**The stale artefact had a second home, and it was the live one.** `7.5` was filed about
`embed/`, but `apps/admin/public/wchats/` held the same June bundle and is what the deploy runbook
uploads to CloudFront, what terraform names as the origin, and what the smoke test probes. Deleting
it was not available; it became a third sync target.

## Gates observed

`fast gates passed in 62.6s` (exit 0) · widget build + postbuild exit 0, **9471 bytes gzipped
against the 20480 budget** · `check:theming-contract PASS, 15 keys` · `check:embed-sync PASS, 6
files` · widget `14 passed` · admin `tsc` exit 0 zero errors, `check:no-dusk-tokens` and
`check:ops-room-wiring` (13/13) pass, `45 passed`.

Every fix carries a mutation proof, restored from scratchpad snapshots rather than
`git checkout HEAD --` (which would have destroyed same-session work), `grep MUTATION` clean.

## Not proven, and it is the milestone's own exit criterion

**PROD-11 has not been run**: nobody has pasted the snippet on a plain external page and watched a
real conversation work. Everything here is unit-level. That test needs a public API base and a
served widget, so it belongs with M4. The BYO-client proof (curl plus EventSource against the
documented contract) is likewise unrun, and the endpoint documentation page is not written.

Follow-ups filed rather than fixed: `7.24` (four guards narrower than their names, each probed),
`7.25` (theming keys still share variables), `7.26` (no cross-workspace guard on the key contract),
`7.27` (inert appearance controls, coral launcher, path drift), `7.28` (the per-agent aggregate
ceiling doubled to 120/min).
