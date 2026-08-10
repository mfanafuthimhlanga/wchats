export const meta = {
  name: 'integration-failures',
  description: 'Fix the 10 remaining integration-suite failures now that a live DB and a real Neon key exist',
  phases: [
    { title: 'A docling skips' },
    { title: 'B neon live' },
    { title: 'C celery args' },
    { title: 'D sse live' },
    { title: 'Review' },
    { title: 'Fix' },
  ],
}

const REPO = 'C:/Users/Bantu/mzansi-agentive/wchats'
const API = REPO + '/apps/api'

const CONTEXT = `
REPO: ${REPO}   API: ${API}   BRANCH: chore/local-postgres  (do not create branches, do not merge)

## The environment is REAL now — read this before anything else

A local PostgreSQL 17.6 runs as a Windows service on localhost:5432 with pgvector 0.8.1.
Redis runs on localhost:6379. Both migration chains are applied: control DB \`wchats_control\` at
head 0019, and a tenant probe DB at 0016. \`.dev/traces/260810-local-postgres.md\` is the full story.

RUN THE INTEGRATION SUITE LIKE THIS (from ${API}):
  export INTEGRATION_DB_URL="postgresql://wchats:wchats@localhost:5432/wchats_control"
  export REDIS_URL="redis://localhost:6379/0"
  .venv/Scripts/python.exe -m pytest tests/integration -m integration -q --no-header -p no:cacheprovider

CURRENT MEASURED BASELINE: **10 failed, 9 passed, 21 skipped, 24 deselected, 0 errors, ~3m43s.**
Any number you report must come from a run you actually performed.

THE UNIT GATE MUST NOT REGRESS — 2112 passed, 11-12 skipped, 0 failed:
  .venv/Scripts/python.exe -m pytest tests/unit -q \\
    --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py

## NEON SAFETY — the most important rule in this workflow

\`${REPO}/.env\` and \`${API}/.env\` contain a REAL, WORKING \`NEON_API_KEY\`. It has already been
verified against the live Neon API (HTTP 200). It can CREATE and DELETE real cloud databases.

**EIGHT REAL PROJECTS ALREADY EXIST AND ARE NOT YOURS.** Their names are pinned in
\`C:/Users/Bantu/pg-setup/neon-baseline.txt\`. They include \`Veridian\`, two
\`bantuson-portfolio-assistant-*\`, two \`m9-demo-*\` and two \`caregiver-*\` projects. These are the
owner's. Deleting one is irreversible and destroys real data.

BINDING RULES:
  1. **NEVER delete a Neon project you did not create in this same run.** Not by name pattern, not
     by "looks like a test", not by age. Only by an id your own code just received from a create call.
  2. **Never print, log, echo or commit the API key**, or any connection string containing it.
  3. Before and after any test that touches Neon, list projects (\`GET
     https://console.neon.tech/api/v2/projects\`) and compare against the baseline file. **If any of
     the 8 baseline projects is missing, STOP EVERYTHING and report it as a critical finding.**
  4. Every project your run creates must be deleted before you finish. If one leaks, say so
     explicitly with its id — a silent leak costs the owner money and consumes the account's
     project quota.
  5. The free-tier project limit may be near: 8 already exist. If a create fails on quota, that is a
     finding to report, not a reason to delete somebody else's project to make room.
  6. \`CONTROL_DB_URL\` in \`.env\` points at LIVE NEON PRODUCTION. The integration conftest overrides
     it from \`INTEGRATION_DB_URL\` before app import. **Never undo that override**, and never point a
     test at the production control DB.

## Binding project rules (CLAUDE.md)

  - No Docker. Local processes only.
  - A test for every behaviour change. A skip is UNOBSERVED, never a pass.
  - A metric over zero valid observations is 'unknown', never 'pass'.
  - For any guard you add: mutate it, RUN it, observe red, restore from HEAD, RUN again, observe
    green, and record the VERBATIM output of both plus the exact selector.
  - PowerShell breaks on multi-line -m arguments: write the message to a temp file, git commit -F.
  - Commit style: type(scope): message, ending with
    Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  - NEVER merge to main. NEVER edit settings.json.
  - Persist your full findings to ${REPO}/.dev/reference/ BEFORE returning, and name the file.

HONESTY: report what you observed. If you did not run it, say so. This project has spent two whole
branches learning that an unverified claim which reads as verified is the defect.
`

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    tests_now_passing: { type: 'array', items: { type: 'string' } },
    tests_still_failing: { type: 'array', items: { type: 'string' } },
    integration_result: { type: 'string', description: 'Verbatim final pytest line, or NOT RUN' },
    unit_result: { type: 'string', description: 'Verbatim final unit-gate line, or NOT RUN with reason' },
    neon_projects_created: { type: 'array', items: { type: 'string' } },
    neon_projects_deleted: { type: 'array', items: { type: 'string' } },
    neon_baseline_intact: { type: 'string', description: 'Are all 8 baseline projects still present? State how you checked.' },
    mutation_proofs: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          guard: { type: 'string' }, selector: { type: 'string' },
          mutation: { type: 'string' }, observed_red: { type: 'string' }, observed_green: { type: 'string' },
        },
        required: ['guard', 'selector', 'mutation', 'observed_red', 'observed_green'],
      },
    },
    claims: {
      type: 'array',
      items: { type: 'object', properties: { claim: { type: 'string' }, evidence: { type: 'string' } }, required: ['claim', 'evidence'] },
    },
    not_done: { type: 'array', items: { type: 'string' } },
    persisted_to: { type: 'string' },
    commit_sha: { type: 'string' },
  },
  required: ['summary', 'files_changed', 'tests_now_passing', 'tests_still_failing', 'integration_result', 'unit_result', 'neon_projects_created', 'neon_projects_deleted', 'neon_baseline_intact', 'mutation_proofs', 'claims', 'not_done', 'persisted_to', 'commit_sha'],
}

phase('A docling skips')
const a = await agent(`${CONTEXT}

TASK A — the four \`tests/integration/test_ingestion_chain.py\` failures.

All four patch \`app.services.chunking_service.HybridChunker\`, which does NOT exist at module scope:
\`chunking_service.py:64\` imports it LAZILY INSIDE the function
(\`from docling.chunking import HybridChunker\`). That is BACKLOG 4.1's \`_KNOWN_BROKEN\` pin, and
docling is not installed on this machine by design (BACKLOG 4.4, CLAUDE.md environment constraints).

These tests currently FAIL. They should SKIP when docling is absent — a failing test that ought to
skip is noise that hides real failures, and it is why nobody could see the other nine defects.

  - Add a docling-availability guard consistent with how the repo already handles docling elsewhere
    (look at how \`test_chunking_service.py\` / \`test_docling_service.py\` are treated, and at
    \`tests/unit/test_pipeline_patch_targets.py\`). Do NOT invent a second convention.
  - **If docling IS importable, they must RUN, not skip.** A guard that always skips is a
    tautology — the exact defect class this repo keeps finding. Prove the guard both ways:
    demonstrate it skips when docling is absent AND that the skip condition is false when it is
    present (you can simulate presence with a fake module entry if docling truly is not installed —
    but say plainly which you did).
  - Also fix or re-pin the patch target itself if the correct target is \`docling.chunking.HybridChunker\`.
    \`tests/unit/test_patch_targets_resolve.py::_KNOWN_BROKEN\` pins the broken sites WITH EXACT
    COUNTS — if you fix sites, that pin must be updated or the unit gate goes red. Re-measure with
    \`.venv/Scripts/python.exe tests/unit/test_patch_targets_resolve.py\`.

Run BOTH suites. Commit.`, { schema: IMPL_SCHEMA })

phase('B neon live')
const b = await agent(`${CONTEXT}

TASK B — the four Neon failures: \`test_provision.py\` (2) and \`test_chain.py\` (2).

They fail with \`agent.neon_project_id was not set within 30s\` and \`chain did not reach 'ready'
within 60s\`, because \`tests/integration/conftest.py:58\` sets
\`NEON_API_KEY=test_neon_key_integration\` — a placeholder — via \`os.environ.setdefault\`.

**A real key now exists** in \`${REPO}/.env\`. Because conftest uses \`setdefault\`, exporting the real
key into the environment before pytest runs is enough to override it; confirm that is actually how
it resolves rather than assuming.

RE-READ THE NEON SAFETY RULES ABOVE. They are the most important part of this task. In particular:
list projects before you start, list them after, and compare against
\`C:/Users/Bantu/pg-setup/neon-baseline.txt\`. Report created and deleted ids explicitly.

Decide and justify which of these is right, then implement it:
  (a) run these tests against the real Neon API, with guaranteed teardown of every project created; or
  (b) mock the Neon client at its boundary so they need no network at all.

Consider that (a) proves the real integration but costs money, leaks projects if teardown fails, and
cannot run in CI without a secret; (b) always runs but proves less. **If you choose (a), teardown
must be in a \`finally\` and must survive the test failing** — a leaked project on every red run is
not acceptable. Whichever you choose, the tests must be deterministic about which they are doing:
a test that silently falls back to a mock when the key is absent, while reporting a pass, is a
tautology of exactly the kind this repo exists to prevent.

Run BOTH suites. Commit.`, { schema: IMPL_SCHEMA })

phase('C celery args')
const c = await agent(`${CONTEXT}

TASK C — \`tests/integration/test_query_route.py::test_post_query_returns_202\`.

It now reaches HTTP 202 (the earlier 404 was a missing \`/api/v1\` prefix, already fixed) and fails at
\`tests/integration/test_query_route.py:235\` with \`agent_id not found in task args: []\` — the
dispatched Celery task's args are empty when inspected.

Find out which is true and fix accordingly:
  - the assertion reads the wrong surface (e.g. it inspects a mock or a broker queue that does not
    carry args the way it assumes), or
  - the dispatch genuinely does not carry \`agent_id\`, which would be a PRODUCT defect.

The captured stdout shows \`query_agent.dispatched agent_id=... job_id=...\`, so the route clearly
knows the agent_id. Determine where it is lost between there and what the test inspects.

**If it is a product defect, fix the product and say so loudly** — that is a far more valuable
finding than a test fix. If it is a test defect, fix the test so it asserts something that would
actually fail if dispatch dropped the agent_id, and prove that with a mutation.

Run BOTH suites. Commit.`, { schema: IMPL_SCHEMA })

phase('D sse live')
const d = await agent(`${CONTEXT}

TASK D — \`tests/integration/test_sse.py::test_sse_receives_live_events_after_replay\`.

Live events published to Redis channel \`job_events:{job_id}\` never reach the SSE stream. The loop is
now bounded by \`asyncio.timeout(SSE_STREAM_TIMEOUT_S = 30)\` — it used to HANG INDEFINITELY, and two
runs sat on it for 10 and 40 minutes before that bound existed. **Do not remove or weaken that
bound.** If you need longer, say why.

Already ruled out, do not re-derive:
  - NOT a channel mismatch. The test publishes to \`job_events:{job_id}\` (test_sse.py:341,350) and
    \`app/services/sse.py:79\` subscribes to exactly \`f"job_events:{job_id}"\`.
  - NOT a replay/subscribe ordering gap. \`sse.py:79\` subscribes BEFORE the \`:82\` DB replay,
    deliberately, and the module docstring says why.

This is the project's SSE-via-Redis-pub/sub architecture principle (CLAUDE.md), so a real defect here
matters well beyond one test. Candidates worth checking: whether the ASGITransport in-process client
actually begins consuming before the publisher fires; whether the generator is started lazily;
whether the pubsub listener is being starved by the event loop; whether a terminal event ends the
stream before the third event arrives.

**If the product is at fault, fix the product.** If the test races, fix the race deterministically —
not with a longer sleep, which converts a race into a slow race.

Run BOTH suites. Commit.`, { schema: IMPL_SCHEMA })

phase('Review')
const review = await agent(`${CONTEXT}

You are an ADVERSARIAL REVIEWER. Report EVERYTHING at every severity, including what you are unsure
of — the orchestrator filters, you do not. Under-reporting is the failure mode.

Review every commit made on \`chore/local-postgres\` by the four tasks above. Their reports:

A (docling): ${JSON.stringify(a, null, 2)}
B (neon):    ${JSON.stringify(b, null, 2)}
C (celery):  ${JSON.stringify(c, null, 2)}
D (sse):     ${JSON.stringify(d, null, 2)}

Investigate the codebase yourself; take none of it on trust. Re-run both suites and report the
verbatim lines.

The questions that matter most, in order:
  1. **NEON SAFETY.** Are all 8 baseline projects in
     \`C:/Users/Bantu/pg-setup/neon-baseline.txt\` still present? Check via the API yourself. Did any
     task delete something it did not create, or leak something it did? Is teardown in a \`finally\`
     that survives a failing test? Can any code path delete by name pattern rather than by an id it
     just created? This is the highest-consequence question on the branch.
  2. **Did anything become a tautology?** A docling guard that always skips; a Neon test that
     silently falls back to a mock and still reports pass; an assertion weakened until it passes.
     For each test moved from failing to passing, ask what it would still do if the behaviour it
     checks were reverted — and try that revert where you can.
  3. Was the \`asyncio.timeout\` bound on the SSE loops removed, raised, or worked around?
  4. Does \`tests/unit/test_patch_targets_resolve.py::_KNOWN_BROKEN\` still agree with reality?
  5. Any secret printed, logged or committed?`, { schema: {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'nit'] },
          file: { type: 'string' }, line: { type: 'integer' },
          claim: { type: 'string' }, failure_scenario: { type: 'string' }, suggested_fix: { type: 'string' },
        },
        required: ['severity', 'file', 'claim', 'failure_scenario', 'suggested_fix'],
      },
    },
    unsupported_claims: {
      type: 'array',
      items: { type: 'object', properties: { claim: { type: 'string' }, why_unsupported: { type: 'string' } }, required: ['claim', 'why_unsupported'] },
    },
    neon_baseline_verdict: { type: 'string' },
    integration_result: { type: 'string' },
    unit_result: { type: 'string' },
    summary: { type: 'string' },
    persisted_to: { type: 'string' },
  },
  required: ['findings', 'unsupported_claims', 'neon_baseline_verdict', 'integration_result', 'unit_result', 'summary', 'persisted_to'],
} })

phase('Fix')
const fix = await agent(`${CONTEXT}

Fix the findings below on \`chore/local-postgres\`. BOUNDED: these findings only, no refactoring.
A finding you believe is wrong goes in \`not_done\` with your reason — never silently skipped.

Treat any NEON SAFETY finding as first priority regardless of its stated severity.

FINDINGS:
${JSON.stringify(review, null, 2)}

Then run BOTH suites one final time and report both verbatim lines, plus a final Neon baseline check.
Commit.`, { schema: IMPL_SCHEMA })

log('integration-failures workflow complete')

return { a, b, c, d, review, fix }
