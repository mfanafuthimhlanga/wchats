export const meta = {
  name: 'ci-green',
  description:
    'Make W Chats CI green for the first time: the trigger gap + pytest-cov + a hardcoded machine path, then 461 ruff violations without breaking a side-effecting import, then mypy. Per .dev/plans/260806-ci-green.md',
  phases: [
    { title: 'P1 CI infrastructure', detail: 'run CI on every PR, add pytest-cov, derive the conftest path' },
    { title: 'P2 ruff to zero', detail: '461 violations, without deleting a load-bearing import or hoisting a deliberate late one' },
    { title: 'P3 mypy to zero', detail: 'pydantic false positives properly, genuine None-narrowing with tests' },
    { title: 'Tier-2 judge', detail: 'Fable 5 judges the bounded artifact: do the claims match the evidence?', model: 'fable' },
  ],
}

// Same shape as .dev/workflows/eval-foundation.workflow.js. Model discipline: every agent() below
// omits `model:` and inherits the session model; the tier-2 judge is the one sanctioned exception.
// Reviewers are never told to be conservative.

const PLAN = '.dev/plans/260806-ci-green.md'
const BASE = '7b2c68d' // feat/eval-foundation tip; this branch stacks on it

const PHASES = [
  { id: 'P1', title: 'P1 CI infrastructure' },
  { id: 'P2', title: 'P2 ruff to zero' },
  { id: 'P3', title: 'P3 mypy to zero' },
]

const IMPL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['committed', 'commit_ref', 'summary', 'files', 'gates_green', 'notes_for_next_phases'],
  properties: {
    committed: { type: 'boolean' },
    commit_ref: { type: 'string' },
    summary: { type: 'string', description: '<=12 lines: what changed, key decisions, deviations' },
    files: { type: 'array', items: { type: 'string' } },
    gates_green: { type: 'boolean', description: 'the backend unit suite still passes with ZERO failures AND the test count did not drop' },
    test_count_before: { type: 'string', description: 'observed passed/skipped/failed BEFORE your changes' },
    test_count_after: { type: 'string', description: 'observed passed/skipped/failed AFTER your changes. Any drop in passed is a failure, not a delta.' },
    tool_counts: { type: 'string', description: 'ruff/mypy violation counts before and after, as observed by running them' },
    load_bearing_imports_kept: {
      type: 'array',
      items: { type: 'string' },
      description: 'P2 especially: every import ruff flagged as unused that you KEPT, with the reason (model registration, re-export, task registration, sdk import guard, circular-import break). One line each.',
    },
    guard_demonstrations: {
      type: 'array',
      items: { type: 'string' },
      description: 'for every guard or pin you added: what you mutated, that you OBSERVED red, that you restored from HEAD, that you OBSERVED green. Quote observed counts. Omit any you did not actually run.',
    },
    stopped_short: { type: 'string', description: 'anything you deliberately did not do, and why. Empty otherwise.' },
    notes_for_next_phases: { type: 'array', items: { type: 'string' } },
  },
}

const REVIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['verdict', 'blockers', 'concerns'],
  properties: {
    verdict: { type: 'string', enum: ['approve', 'fix_required'] },
    blockers: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['file', 'issue', 'failure_scenario'],
        properties: {
          file: { type: 'string' },
          issue: { type: 'string' },
          failure_scenario: { type: 'string', description: 'concrete inputs/state -> wrong behavior' },
        },
      },
    },
    concerns: { type: 'array', items: { type: 'string' } },
  },
}

const ARTIFACT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['stat', 'diff', 'omitted', 'test_inventory'],
  properties: {
    stat: { type: 'string', description: 'git diff --stat <BASE>...HEAD, verbatim' },
    diff: { type: 'string', description: 'unified diff, verbatim. This branch is mostly mechanical import churn — if it must be trimmed, drop the PUREST mechanical hunks (import reordering with no deletion) first and record every one.' },
    omitted: { type: 'array', items: { type: 'string' } },
    test_inventory: { type: 'string', description: 'every test function added or changed, one per line' },
  },
}

const JUDGE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['verdict', 'claimed_but_unproven', 'evidence_mismatches'],
  properties: {
    verdict: { type: 'string', description: '3-5 sentences answering "is this actually safe to merge?". Written for the repo owner, verbatim.' },
    claimed_but_unproven: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['claim', 'claimed_where', 'why_unproven', 'what_would_prove_it'],
        properties: {
          claim: { type: 'string' },
          claimed_where: { type: 'string' },
          why_unproven: { type: 'string' },
          what_would_prove_it: { type: 'string' },
        },
      },
    },
    evidence_mismatches: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['claim', 'contradicting_evidence'],
        properties: {
          claim: { type: 'string' },
          contradicting_evidence: { type: 'string' },
        },
      },
    },
  },
}

const CONVENTIONS = `Repo conventions (binding — from CLAUDE.md):

- Backend gate, run from apps/api:
    .venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py
  Those two modules are excluded because docling is NOT installed here. Restore the venv if needed with
  uv sync --extra dev — ONE uv process at a time; two deadlock on the wheel cache lock for 300s.
- ruff and mypy are NOT in the venv. Run them with uvx: uvx ruff check apps/api/app/ apps/api/tests/
  and uvx --with-requirements <...> mypy, or pip-install them into the venv. Match what ci.yml runs:
  ruff check apps/api/app/ apps/api/tests/  and  python -m mypy app/ --ignore-missing-imports --strict-optional
  (the mypy one from the apps/api working directory).
- Commit style: type(scope): message, ending with the session model's co-author trailer.
  PowerShell breaks on multi-line -m arguments: write the message to a temp file, use git commit -F.
- 4 GB RAM: no parallel test workers.
- NO Docker. NO local PostgreSQL — every pytest -m integration harness SKIPS here, so a skip is
  UNOBSERVED, never a pass. CONTROL_DB_URL points at live Neon PRODUCTION and is never a test target.
- No product behaviour change on this branch. No new dependency except pytest-cov.

A NEGATIVE TEST NEVER OBSERVED TO FAIL IS INDISTINGUISHABLE FROM A TAUTOLOGY. Mutate, observe red,
restore from HEAD unconditionally, re-run, observe green. Record observed counts.`

const DOMAIN_LAW = `DOMAIN LAW for this branch:

- THE TEST COUNT IS THE GUARD, AND IT DOES NOT MOVE. The suite is 1657 passed / 11 skipped / 0 failed
  at this branch point. This branch changes NO product behaviour, so any drop in "passed" means a
  mechanical fix broke something. Report the count before AND after your changes. A drop is a failure
  to diagnose, never a delta to record.

- AN UNUSED IMPORT IS NOT NECESSARILY AN UNUSED IMPORT. 151 F401s, and some are load-bearing through
  side effects a linter cannot see: SQLAlchemy models registering on metadata, Celery task modules
  registering tasks, __init__.py re-exports that ARE the package's public surface, Alembic env
  imports, pytest fixture imports. This repo also has a RECORDED test-ordering defect in exactly this
  area: test_agent_chat_routes.py imports app.main and thus the real claude_agent_sdk first, which
  defeated test_agent_tools.py's its 'claude_agent_sdk not in sys.modules' guard and turned 18 tests
  red ONLY in full-suite order. Deleting the wrong import here reproduces that class of bug silently.
  When an import is load-bearing, KEEP IT and silence it explicitly with a one-line reason. List every
  such import in load_bearing_imports_kept.

- A DELIBERATE LATE IMPORT IS A DESIGN DECISION, NOT A LINT VIOLATION. The 8 E402s are probably
  breaking circular imports on purpose — red_team_service.py documents this pattern at :816, importing
  red_team_probe inside function bodies because a module-level import would be circular. Do NOT hoist
  an import to satisfy a linter and reintroduce a cycle. A '# noqa: E402' naming the reason is correct.

- REPORT THE REAL COVERAGE NUMBER. --cov-fail-under=80 has never executed. If real coverage is below
  80, the check fails for a TRUE reason. Report it; do not lower the threshold to make it pass. Turning
  a broken check into an honestly-failing one is progress and must be stated as such.

- MYPY: FIX THE NARROWING, DO NOT CAST. A 'str | None' reaching a 'str' is a latent AttributeError, not
  a typing inconvenience. cast() and '# type: ignore' on a genuine narrowing hide a real defect. The
  config.py Settings() call-arg errors ARE a false positive (pydantic-settings loads from env and mypy
  cannot model it) — fix those with the pydantic mypy plugin or a narrowly-scoped, commented ignore at
  that call site. Never widen global strictness to make errors disappear.

- THIS IS THE SAME DEFECT CLASS THE BRANCH BELOW IT DIAGNOSED. CI has never been green in its recorded
  history; the failures were visible and unread. retro.md Family B is "missing data treated as passing
  data". A gate that always fails is read as noise, which is the same disease. Leaving any check red
  at the end of this branch needs to be a stated, reasoned decision, not a silent remainder.`

function implPrompt(p, notes) {
  return `You are implementing phase ${p.id} of the ci-green branch in the repo at the current working directory (branch fix/ci-green — verify with git status; never switch branches).

READ FIRST:
1. ${PLAN} — your scope contract is EXACTLY its "${p.id}" section, plus "Non-goals" and "Risks".
2. The failing CI itself. \`gh run list --workflow ci.yml\` and \`gh run view --job <id> --log-failed\` work here and the CLI is authenticated — read the ACTUAL failure output rather than trusting the plan's summary of it. If the plan is wrong about a cause, say so in your summary.
3. .github/workflows/ci.yml — what CI actually runs, which is not the same as what CLAUDE.md's local gate runs.

${DOMAIN_LAW}

Verified facts from earlier phases (trust these over the plan's prose):
${notes.length ? notes.map((n) => '- ' + n).join('\n') : '- (none yet — you are the first phase)'}

${CONVENTIONS}

If uncommitted partial work exists in the tree, review it and build on what is correct rather than starting over.

DO: implement your phase scope completely. Run the backend unit suite BEFORE you start and AFTER you finish, and record both counts — that comparison is this branch's primary safety property. Run the tool your phase owns (ruff or mypy) and record its count before and after too. All green and the count intact -> commit with the co-author trailer. If you cannot get there, do NOT commit; report gates_green=false with the evidence.

Your final output goes to an orchestrator, not a human: fill the structured fields precisely.`
}

function reviewPrompt(p, impl) {
  return `You are an adversarial reviewer for phase ${p.id} of the ci-green branch (branch fix/ci-green). The implementer committed ${impl.commit_ref || '(uncommitted — review the working tree)'}: ${impl.summary}

It claims test counts ${impl.test_count_before || '?'} -> ${impl.test_count_after || '?'} and tool counts: ${impl.tool_counts || '(none given)'}.

Review the diff: git show ${impl.commit_ref || 'HEAD'}. Read ${PLAN} ("${p.id}" section). Attack it along these axes:

- A DELETED IMPORT THAT WAS LOAD-BEARING. This is the highest-risk failure mode on this branch and it is SILENT. For every removed import in the diff, ask what it did besides being referenced: does a SQLAlchemy model lose its metadata registration, a Celery task its registration, a package its public re-export, a conftest its fixture, Alembic its revision discovery? The implementer says it kept these: ${JSON.stringify(impl.load_bearing_imports_kept || [])} — check the ones it did NOT keep. Specifically check anything touching claude_agent_sdk import ordering: this repo has a recorded 18-test full-suite-order-only failure in exactly that area, and a suite run in file order would not catch its return.
- A HOISTED LATE IMPORT. Did any E402 fix move an import to module level? Trace whether that creates a cycle. Import the module in isolation and see.
- THE TEST COUNT. Re-run the suite yourself and compare to the claim. A claim of "no change" that you cannot reproduce is a blocker. Also run it in a DIFFERENT order (-p no:randomly is not enough; try running a couple of the SDK-adjacent modules together) if imports moved.
- COVERAGE HONESTY (P1). Was --cov-fail-under lowered, removed, or routed around instead of reporting the real number? Was the threshold met by adding trivial tests?
- MYPY APPEASEMENT (P3). Every cast(), type: ignore, Any annotation and Optional widening in the diff: is it hiding a real None that can reach a str? Is the config.py fix narrowly scoped, or did global strictness get widened? Is there a test for each genuine narrowing fixed, and would that test fail if the narrowing were reverted?
- SCOPE. Any product behaviour change? Any new dependency beyond pytest-cov? Anything the Non-goals forbid?
- THE CI FILE ITSELF (P1). Does the trigger change actually cause this PR to get checks? Could it cause runs to fire on branches nobody wants? Is the push trigger still restricted to main?
- TEST HONESTY. Mentally mutate: revert the conftest path to a hardcoded string; remove pytest-cov again; re-delete a load-bearing import. Would anything fail? Name every vacuous test.
- Anything the plan promised that the diff does not contain, and anything in the diff the plan did not ask for.

Run the tools yourself (uvx ruff, mypy, the unit suite). Verify counts against the code rather than the commit message.

Do NOT fix anything. Report via the structured fields. REPORT EVERYTHING YOU FIND — over-report rather than under-report; the orchestrator filters, you do not. Concrete failure scenarios go in blockers; everything else — style, suspicions, latent risks, questions, anything you would raise with more time — goes in concerns. Do not self-censor either list to seem measured. Empty blockers with a full concerns list is a perfectly good review, and so is verdict approve.`
}

function fixPrompt(p, impl, review) {
  return `You are fixing review blockers for phase ${p.id} of the ci-green branch (branch fix/ci-green; phase commit ${impl.commit_ref || 'uncommitted'}).

Blockers — verify each first by constructing the failure, then fix it, then add the test that would have caught it:
${review.blockers.map((b, i) => `${i + 1}. [${b.file}] ${b.issue} — failure: ${b.failure_scenario}`).join('\n')}

Read ${PLAN} ("${p.id}" section) for scope; do not expand beyond these fixes.

${DOMAIN_LAW}

${CONVENTIONS}

Run the unit suite and your phase's tool. Count intact and zero failures -> commit as fix(ci): ${p.id} review fixes with the co-author trailer. If a blocker is a false positive after honest verification, say so with the evidence instead of "fixing" it.`
}

function collectPrompt() {
  return `Assemble the bounded review artifact for the ci-green branch. You are a collector, not a reviewer — do not judge, do not fix, do not run tests.

Run: git diff --stat ${BASE}...HEAD, then git diff ${BASE}...HEAD. Use that base exactly.

Return the stat verbatim and the diff verbatim. This is the ONLY view the downstream judge has — it cannot open a file, so anything omitted is invisible to it. This branch is mostly mechanical import churn, so if it must be trimmed: drop the PUREST mechanical hunks first (pure import REORDERING with no deletion), then large fixture files. NEVER drop a hunk that DELETES an import, changes ci.yml, pyproject.toml, conftest.py, or any type annotation — those are exactly what the judge must see. Record every omission with its reason.

For test_inventory, extract every test function name added or changed, one per line.`
}

function judgePrompt(artifact, reports) {
  return `You are the tier-2 judge for the ci-green branch of W Chats. One judge runs before the merge.

You are NOT a code reviewer and this is NOT a second opinion on correctness — a per-phase adversarial reviewer already did that, on the tree, and its findings are below. Your question is narrower:

  DO THE CLAIMS MATCH THE EVIDENCE, AND WHAT IS ASSERTED BUT UNPROVEN?

Context you need. This branch exists because W Chats' CI has NEVER been green: all 7 recorded runs failed, and only 7 exist because the workflow triggered on pushes to a branch that sat unpushed for 8 weeks. Four checks were failing — a missing pytest-cov, a hardcoded developer path pointing at the project's OLD name, 461 ruff violations, and mypy errors. None was introduced by recent work.

The branch's central safety property is a NEGATIVE one: it changes no product behaviour, so the unit suite must remain at 1657 passed / 11 skipped / 0 failed. The dangerous move it makes is deleting ~151 "unused" imports, some of which may be load-bearing through side effects no linter can see — and this repo has a recorded defect where an import-ordering change turned 18 tests red ONLY in full-suite order, which a normal run would not surface.

So: read every claim of "no tests lost" and "no behaviour changed" against what the evidence can actually establish. A suite that passes in one order is not proof for a change that moved imports. A claim that an import was safe to delete is only as good as the reason given for it.

WORK FROM THE ARTIFACT BELOW AND NOTHING ELSE. Do not open files, do not grep, do not run tests, do not explore the repository. If the artifact is insufficient to judge a claim, that insufficiency IS your finding — say so rather than going to look.

=== WHAT THE IMPLEMENTERS CLAIM ===
${JSON.stringify(reports, null, 2)}

=== TIER-1 REVIEW FINDINGS ===
${JSON.stringify(reports.map((r) => ({ phase: r.phase, verdict: r.review_verdict, blockers_fixed: r.blockers_fixed, concerns: r.concerns })), null, 2)}

=== DIFF STAT ===
${artifact.stat}

=== OMITTED FROM THE DIFF ===
${artifact.omitted.length ? artifact.omitted.join('\n') : '(nothing omitted)'}

=== TESTS ADDED OR CHANGED ===
${artifact.test_inventory}

=== DIFF ===
${artifact.diff}

Report everything. Over-report rather than under-report: an item you are unsure about goes in with your uncertainty stated. Do not self-censor to seem measured, and do not soften a finding because tier 1 approved the phase — tier 1 asked whether the code is broken, which is not what you were asked.

Your verdict is read by the repo owner verbatim, and the decision it informs is whether to merge a large mechanical diff. Write it plainly and for a person.`
}

const notes = []
const phaseReports = []

for (const p of PHASES) {
  phase(p.title)

  let impl = await agent(implPrompt(p, notes), { label: `impl:${p.id}`, phase: p.title, schema: IMPL_SCHEMA })
  if (!impl) {
    log(`${p.id}: implementer died — one relaunch`)
    impl = await agent(implPrompt(p, notes), { label: `impl-relaunch:${p.id}`, phase: p.title, schema: IMPL_SCHEMA })
  }
  if (!impl) throw new Error(`${p.id}: implementer died twice`)

  if (!impl.gates_green) {
    log(`${p.id}: gate not green — one retry with failure context`)
    const retry = await agent(
      implPrompt(p, notes) +
        `\n\nPREVIOUS ATTEMPT FAILED. Its summary: ${impl.summary}\nCounts before/after: ${impl.test_count_before} -> ${impl.test_count_after}\nDiagnose the root cause first. If the suite lost tests, find WHICH import removal did it before changing anything else.`,
      { label: `impl-retry:${p.id}`, phase: p.title, schema: IMPL_SCHEMA },
    )
    if (retry) impl = retry
    if (!impl.gates_green) throw new Error(`${p.id}: gate still red after retry — stopping for orchestrator`)
  }

  if (impl.stopped_short) log(`${p.id}: stopped short — ${impl.stopped_short}`)
  log(`${p.id} counts: ${impl.test_count_before} -> ${impl.test_count_after} | ${impl.tool_counts || ''}`)

  let review = await agent(reviewPrompt(p, impl), { label: `review:${p.id}`, phase: p.title, schema: REVIEW_SCHEMA })
  if (!review) {
    log(`${p.id}: reviewer died — one relaunch`)
    review = await agent(reviewPrompt(p, impl), { label: `review-relaunch:${p.id}`, phase: p.title, schema: REVIEW_SCHEMA })
  }

  let fixSummary = null
  if (review && review.verdict === 'fix_required' && review.blockers.length) {
    let fix = await agent(fixPrompt(p, impl, review), { label: `fix:${p.id}`, phase: p.title, schema: IMPL_SCHEMA })
    if (!fix) {
      log(`${p.id}: fixer died — one relaunch`)
      fix = await agent(fixPrompt(p, impl, review), { label: `fix-relaunch:${p.id}`, phase: p.title, schema: IMPL_SCHEMA })
    }
    if (!fix) throw new Error(`${p.id}: fixer died twice with open blockers`)
    if (!fix.gates_green) throw new Error(`${p.id}: fix round left the gate red`)
    fixSummary = fix.summary
    for (const n of fix.notes_for_next_phases) notes.push(`${p.id}-fix: ${n}`)
  }

  for (const n of impl.notes_for_next_phases) notes.push(`${p.id}: ${n}`)
  if (review) for (const c of review.concerns) notes.push(`${p.id} review concern (non-blocking): ${c}`)

  phaseReports.push({
    phase: p.id,
    commit: impl.commit_ref,
    summary: impl.summary,
    files: impl.files,
    test_count_before: impl.test_count_before,
    test_count_after: impl.test_count_after,
    tool_counts: impl.tool_counts,
    load_bearing_imports_kept: impl.load_bearing_imports_kept || [],
    guard_demonstrations: impl.guard_demonstrations || [],
    stopped_short: impl.stopped_short || null,
    review_verdict: review ? review.verdict : 'reviewer-died',
    blockers_fixed: review && review.blockers ? review.blockers.length : 0,
    fix_summary: fixSummary,
    concerns: review ? review.concerns : [],
  })
  log(`${p.id} done: ${impl.commit_ref} (${review ? review.verdict : 'no review'}, ${review && review.blockers ? review.blockers.length : 0} blockers)`)
}

phase('Tier-2 judge')

let artifact = await agent(collectPrompt(), { label: 'collect:artifact', phase: 'Tier-2 judge', schema: ARTIFACT_SCHEMA })
if (!artifact) {
  log('collector died — one relaunch')
  artifact = await agent(collectPrompt(), { label: 'collect-relaunch', phase: 'Tier-2 judge', schema: ARTIFACT_SCHEMA })
}

let judgement = null
if (!artifact) {
  log('WARNING: no bounded artifact — tier-2 judge SKIPPED. Do not merge until a judge has run.')
} else {
  if (artifact.omitted.length) log(`artifact omits ${artifact.omitted.length} path(s): ${artifact.omitted.join('; ')}`)
  judgement = await agent(judgePrompt(artifact, phaseReports), { label: 'judge:tier-2', phase: 'Tier-2 judge', schema: JUDGE_SCHEMA, model: 'fable' })
  if (!judgement) {
    log('judge died — one relaunch')
    judgement = await agent(judgePrompt(artifact, phaseReports), { label: 'judge-relaunch', phase: 'Tier-2 judge', schema: JUDGE_SCHEMA, model: 'fable' })
  }
  if (judgement) log(`tier-2: ${judgement.claimed_but_unproven.length} claimed-but-unproven, ${judgement.evidence_mismatches.length} evidence mismatch(es)`)
  else log('WARNING: tier-2 judge died twice. Do not merge until a judge has run.')
}

log('BRANCH COMPLETE. Write .dev/traces/260806-ci-green.md, then push and let CI actually run — the point of this branch is that the remote gate, not the local suite, is the thing that must be green.')

return { phases: phaseReports, accumulated_notes: notes, judgement }
