export const meta = {
  name: 'm3-widget-endpoint',
  description: 'M3: make the widget and the agent endpoint products (7.1-7.6, 7.23)',
  phases: [
    { title: 'API surface', detail: '7.4 guards, 7.3 SSE terminal, 7.2a config serves widget_config', model: 'opus' },
    { title: 'Widget', detail: '7.2b theming contract, 7.23 JWT renew, 7.5 embed drift', model: 'opus' },
    { title: 'Snippet + copy', detail: '7.1 one generator, 7.6 honest nightly claim', model: 'opus' },
    { title: 'Verify API' },
    { title: 'Verify surface' },
  ],
}

const REPO = 'C:\\Users\\Bantu\\mzansi-agentive\\wchats'
const RULES = `
Rules for every phase: work on branch chore/m0-gate-followups. NEVER touch .dev/* (the orchestrator owns those files; they may be dirty). Do not commit - the orchestrator commits. Run ONLY the test files you touched, never the full suite (4 GB box). Finish with the relevant gate and quote output verbatim. Every behaviour change needs a test observed to FAIL: mutate, observe red, restore from a snapshot you took (git checkout HEAD -- would destroy same-session work), observe green, record verbatim. Report anything you could not do rather than widening scope.`

phase('API surface')
const api = await agent(
  `Repo: ${REPO}, working dir apps\\api. Three located defects on the API surface.${RULES}

**7.4 - the authenticated chat route has no rate limit and no budget guard.** \`app/api/v1/agent_chat.py\` \`post_agent_chat\` (~line 80-186) runs neither, while the widget path enforces both: read \`app/api/v1/widget.py\` lines ~354-363 (60/min per agent via Redis) and ~417-428 (tenant daily budget, ESTIMATED_TURN_COST_USD at ~:86) and apply the SAME mechanisms to the authenticated route. This is a tenant-facing product route; today it is uncapped spend on the tenant's own key. Reuse the existing helpers rather than writing new ones - if they are private to widget.py, lift them to a shared module and update both call sites. Tests: 429 when the ceiling is exceeded, and refusal when the daily budget is exhausted, in the existing tests/unit/test_agent_chat*.py.

**7.3 - the SSE stream never terminates itself.** \`app/services/sse.py:42\` \`TERMINAL_EVENTS = frozenset({"job.complete", "job.failed"})\` excludes \`agent.response\`, so after the answer the server emits keepalives until the 120s cap; the widget escapes only by calling es.close() client-side (apps/widget/src/sse.js). A third-party client holds a socket 120s per turn and burns one of the 50 per-agent slots. IMPORTANT CONTEXT you must respect: the judge chain (gatekeeper/auditor/strategist) emits MORE events on the SAME job_id AFTER agent.response - closing the customer stream must not break those writes, and must not break late-join replay. tests/unit/test_sse.py is ~450 lines and guards replay; read it before changing anything. Decide between adding agent.response to the terminal set vs emitting a distinct terminal marker after it, and justify the choice in the code comment.

**7.2a - the widget config route ignores per-tenant config.** \`app/api/v1/widget.py\` ~:296-302 returns a HARDCODED theming dict, never reading \`agent.widget_config\` (JSONB column, migration 0009, written by the admin via app/api/v1/agents.py ~:282-308). Serve the stored config, falling back to the current hardcoded values when it is null/empty. ALSO: \`app/schemas/widget.py:24\` declares the field \`name\` while the widget reads \`cfg.agent_name\` - fix the contract on the API side by ALSO returning agent_name (do not break the existing name field; the widget half is another agent's job and it will read agent_name).

Return: diff summary per file, the SSE decision and why, mutation red/green verbatim, targeted test output, \`.venv\\Scripts\\python.exe scripts\\gates.py fast\` tail, git status --porcelain.`,
  { label: 'impl:api-surface', phase: 'API surface', model: 'opus' }
)

phase('Widget')
const widget = await agent(
  `Repo: ${REPO}, working dir apps\\widget (Preact, Vite, NO test infrastructure today). The API half is being fixed in parallel by another agent - it will make GET /widget/{id}/config serve stored per-tenant theming AND return an \`agent_name\` field. Do not edit anything under apps/api.${RULES}

**7.2b - injected CSS variables are read by nothing.** \`src/Widget.jsx\` ~:29-30 injects \`--\${k.replace(/_/g,'-')}\` for the config keys (primary_color, accent_gold, font_family, border_radius, background) producing --primary-color, --accent-gold, --font-family, --border-radius, --background. \`src/widget.css\` consumes DIFFERENT names: --bg, --accent, --gold, --font-sans, --radius-sm, --surface-*, --text-*, --border*. **Zero of the five injected variables are read by any rule.** Fix the mapping so per-tenant theming actually applies (map config keys to the variables the stylesheet consumes; do not rename the whole stylesheet). Also \`Widget.jsx:31\` reads cfg.agent_name which was always undefined - the API now returns it.
**The guard is the important half, and it must be a real check, not a comment**: add \`scripts/check-theming-contract.mjs\` in the repo's existing check-script idiom (see apps/widget/scripts/check-size.mjs and apps/admin/scripts/check-no-dusk-tokens.mjs) that parses the variables Widget.jsx injects and asserts EVERY one is consumed by at least one rule in widget.css, exiting non-zero otherwise. Wire it into the build alongside check-size.

**7.23 - the widget session dies at 15 minutes.** The JWT from /config expires 900s after minting (apps/api/app/api/v1/widget.py:178). \`src/api.js\` holds it in module scope and throws 'JWT expired' on the resulting 401 with no re-fetch, so a customer who leaves the widget open and asks a follow-up gets an error. Observed for real: a long-running capture hit 9 consecutive 401s. Fix: on 401, re-fetch config ONCE and retry the send; do not loop indefinitely, and do not change the 900s expiry (it is doing its job).
**Testing this needs infrastructure that does not exist.** You are authorised to add vitest as a devDependency to apps/widget (devDeps do not affect the 20480-byte gzip budget) plus a \`test:unit\` script, and to write the retry test against a faked fetch. Keep the setup minimal - this is the first test in this workspace, so it sets the pattern.

**7.5 - the deployable artefact is stale.** \`embed/widget.iife.js\` is dated Jun 1 (20,834 B) while \`dist/widget.iife.js\` is Aug 4 (23,552 B); embed/README.md still claims "17.8 KB". embed/ is what the README documents as publishable, so what would ship is months behind source. Make the build regenerate embed/ from dist/ and gate the drift (hash or size comparison) so they cannot diverge again. Correct the stale README figure to the measured one.

Verify with: \`npm run build\` (postbuild runs check-size), your new theming check, and \`npm run test:unit\`. Quote all output verbatim. The gzip budget is 20480 bytes - report the measured size.
Return: diff summary per file, the theming key mapping you chose, verbatim build/size/check/test output, the mutation proof for the 401 retry, git status --porcelain.`,
  { label: 'impl:widget', phase: 'Widget', model: 'opus' }
)

phase('Snippet + copy')
const snippet = await agent(
  `Repo: ${REPO}. Two defects that span the API and the admin console. Another agent has just changed apps/api/app/api/v1/widget.py and apps/api/app/services/sse.py, and a third changed apps/widget - read current file state before editing, and do not revert their work.${RULES}

**7.1 - two snippet generators that disagree, and the authoritative one is broken.** \`apps/api/app/services/deployment_service.py\` ~:384-389 \`_make_iframe_snippet\` emits \`<script src="https://widget.wchats.app/widget.js" data-agent="{id}" async></script>\` - a HARDCODED cdn host and **no data-api attribute at all**. The loader treats a missing API base as warning-and-continue (apps/widget/embed/widget.js ~:43-47), so that snippet produces a widget that renders and cannot talk to anything. Meanwhile \`apps/admin/app/agents/[id]/deploy/page.tsx\` ~:229-239 computes its OWN snippet from NEXT_PUBLIC_WCHATS_API_BASE (default '', same failure) and renders THAT (~:2907) instead of the iframe_snippet the API returned.
Fix: **the API is the single generator.** It must emit data-api and take both the CDN base and the public API base from settings rather than hardcoding (if no such Settings fields exist, add them with sensible defaults and document them in BOTH apps/api/.env.example and the repo-root .env.example - the OPTIONAL block, with a line saying what breaks if unset). Expose the snippet so the console can render what the API produced: add \`GET /api/v1/agents/{agent_id}/embed-snippet\` (this is one of ADR-0002's named provisioning ops, so it is forward-compatible with the MCP surface later). The console fetches and renders it, deleting its local generator. Pin with a test that the snippet contains BOTH data-agent and data-api, and a contract test that the console no longer builds its own (a grep-style assertion in the admin check-script idiom, see apps/admin/scripts/check-ops-room-wiring.mjs).
NOTE the console's three 'appearance' modes (deploy/page.tsx ~:242-266) have no counterpart in the loader, which only ever renders a floating button. Do NOT implement them; report the discrepancy for the backlog.

**7.6 - the console tells the owner something untrue.** \`apps/admin/app/agents/[id]/eval/page.tsx:614\` reads "Your agent is evaluated automatically each night. Run a check now to see how it performs." **No beat worker is deployed anywhere**, so nightly evals have never run (that ships in MASTERPLAN M4). Rewrite the copy to describe what the product does TODAY (the owner triggers a run), without promising the scheduled behaviour. Keep it short and non-apologetic.

Verify: apps/api targeted tests + \`.venv\\Scripts\\python.exe scripts\\gates.py fast\`; apps/admin \`npx tsc --noEmit\` (must stay at ZERO errors), \`npm run check:no-dusk-tokens\`, \`npm run check:ops-room-wiring\`, \`npm run test:unit\`. Quote verbatim.
Return: diff summary, the settings you added, mutation red/green, all gate output verbatim, git status --porcelain.`,
  { label: 'impl:snippet-copy', phase: 'Snippet + copy', model: 'opus' }
)

phase('Verify API')
const verifyApi = await agent(
  `Repo: ${REPO}, working dir apps\\api. Read-only plus test runs and probes; restore anything you mutate and prove the tree is clean. Never touch .dev/*.

Adversarially verify the API-half of M3. Implementer claims: <claims>${api}</claims>

Report EVERYTHING you find, not only high severity:
1. **7.4**: read \`git diff\` on agent_chat.py and any shared module. Is the rate limit actually enforced per agent (not per process), and does the budget guard use the same accounting as the widget path - or has a second, divergent implementation appeared? Two readers of one rule is how this repo's 5.1 defect happened; check for it. Can a request bypass either guard (different code path, early return, exception swallowed)?
2. **7.3**: does the stream still deliver late-join replay? Does closing on the agent's terminal event break the judge-chain events that are written to the SAME job_id afterwards (gatekeeper/auditor/strategist)? Drive the actual test file: run tests/unit/test_sse.py and report verbatim. Look for a case where a client that connects AFTER agent.response now gets nothing.
3. **7.2a**: does the config route fall back safely when widget_config is null, empty dict, or contains unexpected keys? Could a tenant-supplied value reach the browser unescaped (it is injected as CSS custom properties by the widget - consider a value like \`red;} body{display:none\`)? That is a real injection surface and I want it assessed, not assumed.
4. Vacuity: for each new test, would it still pass if the fix were reverted? Prove one by mutation.
5. Re-run \`.venv\\Scripts\\python.exe scripts\\gates.py fast\` yourself and quote it.
Return: verdict SOUND or defects with evidence, verbatim outputs, and anything claimed that the diff does not support.`,
  { label: 'verify:api', phase: 'Verify API' }
)

phase('Verify surface')
const verifySurface = await agent(
  `Repo: ${REPO}. Read-only plus builds and test runs; restore anything you mutate. Never touch .dev/*.

Adversarially verify the widget and console half of M3. Claims: <widget>${widget}</widget> <snippet>${snippet}</snippet>

Report EVERYTHING, not only high severity:
1. **The theming contract check is the deliverable that matters most** - a guard that cannot fail is worse than no guard. Probe it: add a NEW injected variable in Widget.jsx that widget.css does not consume, run the check, confirm it goes RED, restore, confirm green. Quote verbatim. Then ask whether the check would catch the ORIGINAL defect (all five names mismatched) - reason it through explicitly.
2. **7.23**: read the 401-retry code. Can it loop forever (retry triggering another 401)? Does it retry non-401 errors it should not? Is the retry test actually driving api.js rather than asserting on a mock of itself? Run \`npm run test:unit\` in apps/widget and quote it.
3. **7.1**: is there now exactly ONE snippet generator? Grep both apps/api and apps/admin for snippet construction and report every site. Does the emitted snippet carry data-agent AND data-api, and what does it contain when the new settings are unset (the failure this fix exists to prevent)? Fetch the actual string from the code path, do not infer it.
4. **7.5**: confirm embed/ is now regenerated from dist/ and that the drift gate actually fails when they diverge - prove it by making them diverge, observing red, restoring.
5. Size: run \`npm run build\` in apps/widget and report the measured gzip against the 20480 budget.
6. Run apps/admin: \`npx tsc --noEmit\`, \`npm run test:unit\`, both check scripts. Quote verbatim. tsc must be ZERO errors.
Return: verdict SOUND or defects with evidence, all verbatim outputs, and the final git status --porcelain.`,
  { label: 'verify:surface', phase: 'Verify surface' }
)

return { api, widget, snippet, verifyApi, verifySurface }