export const meta = {
  name: 'eval-foundation',
  description:
    'Make the W Chats measurement layer mean something: persistence + config tuple + label fix, gate repair + golden set + validity denominators, a transactional decision eval, and calibration inputs. Per .dev/plans/260805-eval-foundation.md',
  phases: [
    { title: 'P1 persistence + tuple + label', detail: 'results to production, config tuple on eval_runs, promotion disabled, bench label inversion fixed' },
    { title: 'P2 gate + golden set', detail: 'fix the column typo and make it fail closed, split golden/exploratory, validity denominators' },
    { title: 'P3 transactional eval', detail: 'decision eval over tool_calls_audit as a confusion matrix, FP and FN separate' },
    { title: 'P4 calibration + dead attackers', detail: 'capture calibration inputs (never the scores), register the probe tools, uncover the mocked-away loop' },
    { title: 'Tier-2 judge', detail: 'Fable 5 judges the bounded artifact: do the claims match the evidence?', model: 'fable' },
  ],
}

// Shape follows sentinel-v2's m10-position-governance workflow (the reference implementation):
// strictly sequential, one agent at a time (4GB RAM box), each phase impl -> adversarial review ->
// bounded fix, then a tier-2 judge on a bounded artifact.
//
// Model discipline (wchats CLAUDE.md): every agent() call below omits `model:` and inherits the
// session model. The ONE exception is the tier-2 judge, which runs `model: 'fable'`.
//
// Reviewers are NEVER told to be conservative — that makes current models report less. Ask for
// everything; the orchestrator filters.

const PLAN = '.dev/plans/260805-eval-foundation.md'
const AUDIT = '.dev/reference/measurement-layer-audit.md'

// Diff base for the tier-2 artifact. NOT `main`: this branch sits on top of
// chore/dev-workflow-convention (fd8fa20), which carries the .dev scaffold and the plan
// itself. Diffing from main would sweep ~1500 lines of convention docs into the judge's
// bounded artifact, crowding out the implementation it is supposed to be judging.
const BASE = 'fd8fa20'

const PHASES = [
  { id: 'P1', title: 'P1 persistence + tuple + label' },
  { id: 'P2', title: 'P2 gate + golden set' },
  { id: 'P3', title: 'P3 transactional eval' },
  { id: 'P4', title: 'P4 calibration + dead attackers' },
]

const IMPL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['committed', 'commit_ref', 'summary', 'files', 'gates_green', 'notes_for_next_phases'],
  properties: {
    committed: { type: 'boolean' },
    commit_ref: { type: 'string', description: 'short SHA of the phase commit, or empty if not committed' },
    summary: { type: 'string', description: '<=12 lines: what was built, key decisions, deviations from plan' },
    files: { type: 'array', items: { type: 'string' } },
    gates_green: { type: 'boolean', description: 'the backend unit suite passes with zero failures' },
    gate_output_tail: { type: 'string', description: 'last ~15 lines of pytest output — ALWAYS fill this, green or red, with the observed pass/fail/skip counts' },
    guard_demonstrations: {
      type: 'array',
      items: { type: 'string' },
      description: 'for every guard/absence pin you added: what you mutated, that you OBSERVED red, that you restored from HEAD, and that you OBSERVED green again. Quote the observed counts. Omit any you did not actually run.',
    },
    stopped_short: { type: 'string', description: 'if you deliberately stopped rather than expanding scope (P4 permits this), what and why. Empty otherwise.' },
    notes_for_next_phases: { type: 'array', items: { type: 'string' }, description: 'exported names/signatures/columns later phases call, shape decisions, deviations' },
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
    concerns: { type: 'array', items: { type: 'string' }, description: 'everything else: style, unverified suspicions, latent risks, questions. Non-blocking; forwarded to later phases.' },
  },
}

const ARTIFACT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['stat', 'diff', 'omitted', 'test_inventory'],
  properties: {
    stat: { type: 'string', description: 'git diff --stat <BASE>...HEAD, verbatim' },
    diff: { type: 'string', description: 'the unified diff of the source changes, verbatim. Prefer completeness; drop lockfiles/generated/snapshot files first.' },
    omitted: { type: 'array', items: { type: 'string' }, description: 'every path whose diff was left out, with the reason. Never omit silently.' },
    test_inventory: { type: 'string', description: 'names of the test functions added/changed in this branch (from the diff), one per line — the judge needs to see what is actually pinned' },
  },
}

const JUDGE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['verdict', 'claimed_but_unproven', 'evidence_mismatches'],
  properties: {
    verdict: { type: 'string', description: '3-5 sentences answering "is this work actually verified?". Written for the repo owner, verbatim, not for an orchestrator.' },
    claimed_but_unproven: {
      type: 'array',
      description: 'every claim the evidence does not actually establish. Over-report.',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['claim', 'claimed_where', 'why_unproven', 'what_would_prove_it'],
        properties: {
          claim: { type: 'string' },
          claimed_where: { type: 'string', description: 'phase summary, commit message, plan, or a comment in the diff' },
          why_unproven: { type: 'string', description: 'what is missing: no test, a test that cannot fail, a test asserting the wrong surface, deferred to a live gate, or asserted only in prose' },
          what_would_prove_it: { type: 'string' },
        },
      },
    },
    evidence_mismatches: {
      type: 'array',
      description: 'claims the diff actively contradicts, as opposed to merely failing to support',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['claim', 'contradicting_evidence'],
        properties: {
          claim: { type: 'string' },
          contradicting_evidence: { type: 'string', description: 'the hunk that contradicts it' },
        },
      },
    },
  },
}

const CONVENTIONS = `Repo conventions (binding — from CLAUDE.md):

- Python tooling is uv. Backend gate, run from apps/api:
    .venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py
  Those two modules are excluded because docling is NOT installed here — that is expected, not a
  failure to fix. If .venv lacks pytest, restore with: uv sync --extra dev (ONE uv process at a time;
  two concurrent runs deadlock on the wheel cache lock).
- Tests for EVERY behavior change. Match surrounding code style, docstring voice and comment density
  (this codebase carries substantial explanatory module docstrings — match that).
- Commit style: type(scope): message, ending with the session model's co-author trailer.
  PowerShell breaks on multi-line -m arguments: write the message to a temp file, use git commit -F.
- 4 GB RAM: no parallel test workers, small fixtures, do not run the full e2e suite.
- NO Docker, ever. NO local PostgreSQL exists on this machine — nothing listens on 5432-5435, so every
  pytest -m integration harness SKIPS. A skipped gate is UNOBSERVED, never a pass; say so plainly.
  CONTROL_DB_URL points at live Neon PRODUCTION and is never an acceptable test target.
- Never put a connection string in a Celery task argument (tasks take agent_id and decrypt at
  runtime). Every Celery task carries acks_late=True AND its own idempotency guard.
- Langfuse v4 API only. Ragas 0.4.x only (ragas.metrics.collections, 'reference' not 'ground_truths').
- No new dependency in this branch. No change to apps/api/pyproject.toml.

A NEGATIVE TEST NEVER OBSERVED TO FAIL IS INDISTINGUISHABLE FROM A TAUTOLOGY. For every guard,
absence pin or fail-closed path you add: mutate the guard, run the tests and OBSERVE red, restore the
file from HEAD unconditionally (before asserting anything about the outcome), re-run and OBSERVE
green. Record the observed counts in guard_demonstrations. This discipline exists because
.dev/retro.md Family A has four recorded recurrences in this repo.`

const DOMAIN_LAW = `DOMAIN LAW for this branch (from ${AUDIT} — read it, it is the reason this work exists):

- THE ORDERING CONSTRAINT. Defect D2 (eval results written to a Neon branch that is deleted in
  finally) currently MASKS defect D5 (a filed FAILING trace stores the agent's own failing answer as
  reference_answer). verified_qa rows are served to real customers by retrieval_service.py:98 BEFORE
  hybrid search at 0.93 cosine similarity. Fixing the write-back without fixing the label inversion
  in the same change ACTIVATES a path that serves a human-flagged failure to customers. P1 owns both.

- MISSING DATA IS NEVER PASSING DATA. A metric over zero valid observations is 'unknown', never
  'pass'. Every run reports (attempted, valid, scored/findings); valid is the denominator; a rate
  without its denominator must not be constructible. This is retro.md Family B, two recurrences —
  deployment_service.py:201's UndefinedColumn is swallowed at deployment.py:157 and substituted with
  an empty dict, so the 'pass_rate < 0.70' blocking condition CANNOT FIRE. Repairing the column names
  without also making the absent-signal path fail CLOSED leaves the gate just as useless.
  Prior art that got this right: red_team_service.py:1076 treats provider_not_configured as a finding
  because the run was "INVALID, not clean." Generalize that, do not reinvent it.

- A MODEL-GENERATED LABEL MAY NEVER GATE A DEPLOY OR REACH A CUSTOMER. The trust hierarchy is
  human-authored > human-verified > customer thumbs-down (labels negatives only) > model-generated
  (exploratory metrics only). promote_to_verified_qa currently promotes a Haiku-written answer at
  0.90/0.90 straight into the customer-serving cache. P1 disables promotion behind an explicit
  trust-tier check; it does NOT quietly move it to production.

- THE HUMAN'S SCORES ARE THE HUMAN'S. tests/evals/calibration/human_scores.csv has an empty
  human_score column by design. NEVER fill it, never estimate it, never "seed" it with a model score.
  P4 prepares every other input and leaves that column untouched. An agent-filled calibration set
  would silently destroy the only instrument that can tell us whether any judge is trustworthy.

- OBSERVATIONS ABOUT A RUN ARE NOT TENANT DATA. D-10 ("never evaluate against production") is correct
  for tenant data and was over-applied to the run's own results. Scoring runs against the branch;
  eval_runs status and eval_results rows belong on production. Branch deletion in finally must keep
  working on every path.

- THE UNIT OF EVALUATION IS A CONFIGURATION TUPLE, not "the agent". Without
  (prompt_version_id, model_id, retrieval_config_hash, envelope_hash, corpus state) stamped on the
  run, two runs cannot be compared and "what changed?" is unanswerable. turn_metrics.prompt_version_id
  (migration 0009:86, nullable, additive) is the precedent — follow its shape.

- MIGRATIONS ARE ADDITIVE AND NULLABLE. This is the first tenant migration since 0012 and it CANNOT be
  verified against a live database here. Strictly additive, strictly nullable, rollback is a no-op.
  Never fork the migration tree.`

function implPrompt(p, notes) {
  return `You are implementing phase ${p.id} of the eval-foundation branch in the repo at the current working directory (branch feat/eval-foundation — verify with git status; never switch branches).

READ FIRST, in order:
1. ${PLAN} — your scope contract is EXACTLY its "${p.id}" section, plus "The ordering constraint that governs this whole plan", "Non-goals" and "Risks". Do not implement other phases' scope.
2. ${AUDIT} — the source-verified findings this plan answers. Every file:line in it was confirmed by reading; trust it over any narrative in .planning/.
3. The code you are changing, before you change it. Every claim in the plan cites a file:line — open each one and confirm it still says what the plan says it says. If it does not, report the discrepancy in your summary rather than silently following either.

${DOMAIN_LAW}

Verified facts from earlier phases (trust these over the plan's prose):
${notes.length ? notes.map((n) => '- ' + n).join('\n') : '- (none yet — you are the first phase)'}

${CONVENTIONS}

If uncommitted partial work from a prior attempt exists in the tree (git status / git diff), review it and build on what is correct rather than starting over.

DO: implement your phase scope completely, with its tests. Prefer adversarial and property tests over happy-path examples — especially for anything whose failure mode is silent (a fail-open gate, an absence pin, a promotion path that should be unreachable). Run the backend gate and WAIT for it. All green -> git add your phase's files and commit as the appropriate type(scope): message with the co-author trailer. If the gate cannot be made green after genuine effort, do NOT commit — report gates_green=false with the failing output tail.

Fill gate_output_tail with the OBSERVED pass/fail/skip counts whether green or red — the orchestrator needs the real number, and the pre-existing baseline in .planning/ (1199 passed / 8 skipped) was never re-observed and may be wrong.

Your final output goes to an orchestrator, not a human: fill the structured fields precisely. notes_for_next_phases must carry every exported name, signature, column, migration revision id and shape decision that later phases depend on.`
}

function reviewPrompt(p, impl) {
  return `You are an adversarial reviewer for phase ${p.id} of the eval-foundation branch in the repo at the current working directory (branch feat/eval-foundation). The implementer committed ${impl.commit_ref || '(uncommitted work — review the working tree)'}: ${impl.summary}

Review the phase diff: git show ${impl.commit_ref || 'HEAD'} (or git diff if uncommitted). Read ${PLAN} ("${p.id}" section + the ordering constraint + Risks) and ${AUDIT}. Attack it along these axes:

- THE ORDERING CONSTRAINT (P1 especially, but check it every phase). Is there ANY path by which a model-generated or production-sourced answer can reach verified_qa, and from there retrieval_service.py:98's customer-serving lookup? Trace it: promote_to_verified_qa's call sites, the trust-tier check, whether the check is a property of the path or a condition someone must remember to call. A promotion path reachable without a trust-tier check is a BLOCKER even if no caller exercises it today.
- FAIL-OPEN vs FAIL-CLOSED. For every error handler, default value and empty-collection substitution in the diff: what does the system conclude when the data is missing? Construct the missing-data case and follow it to the deploy recommendation. If absent evidence can still produce 'ship', that is a blocker. Check the ORCHESTRATOR PROMPT text too, not just the Python — its blocking conditions are prose and can silently disagree with the code.
- DENOMINATORS. Can a rate be constructed anywhere without its valid-count? Does a run with zero valid observations render/return as a pass, a zero, or an honest unknown? Is 'unknown' actually distinguishable downstream, or does it collapse to 0.0 at the first float() cast?
- TEST HONESTY. Mentally mutate the code: revert the column names; delete the trust-tier check; make the branch deletion unconditional; drop the config stamp; return data on a failed write. Would a test fail? Name every vacuous test you find. Check specifically for tests that patch out the very function under test (retro.md Family A recurrence 2 is exactly that: test_red_team_service.py patches asyncio.run so the broken loop never executes).
- THE GUARD DEMONSTRATIONS. The implementer claims: ${JSON.stringify(impl.guard_demonstrations || [])}. Spot-check at least one by actually performing it — mutate, run the relevant test file, observe, restore from HEAD. A demonstration that was claimed but does not reproduce is a blocker.
- MIGRATION SAFETY. Additive? Nullable? Does downgrade actually reverse it? Does the revision chain fork? Does any code path assume the new column is populated on rows written before the migration?
- CELERY RULES. acks_late=True on every task, an idempotency guard that is real (not deferred to "the caller"), no connection string in any task argument, correct queue.
- SCOPE. Did the diff touch anything the plan's Non-goals forbid — OPS-15, REQUIREMENTS.md, frontend files, pyproject.toml, a new dependency?
- Anything the plan promised that the diff does not contain, and anything in the diff the plan did not ask for.

Run the backend gate yourself where you are suspicious (targeted test files, not the full suite — 4 GB RAM). Verify claims against the code rather than the commit message.

Do NOT fix anything. Report via the structured fields. REPORT EVERYTHING YOU FIND — over-report rather than under-report; the orchestrator filters, you do not. Anything you can express as a concrete failure scenario with inputs goes in blockers. Everything else goes in concerns: style, naming, duplicated logic, latent risks, suspicions you could not confirm, questions about intent, and anything you would raise with more time. Do not self-censor either list to seem measured. Empty blockers with a full concerns list is a perfectly good review, and so is verdict approve.`
}

function fixPrompt(p, impl, review) {
  return `You are fixing review blockers for phase ${p.id} of the eval-foundation branch in the repo at the current working directory (branch feat/eval-foundation; phase commit ${impl.commit_ref || 'uncommitted'}).

Blockers to address — verify each first by constructing the failure input, then fix it, then add the test that would have caught it:
${review.blockers.map((b, i) => `${i + 1}. [${b.file}] ${b.issue} — failure: ${b.failure_scenario}`).join('\n')}

Read ${PLAN} ("${p.id}" section) for scope; do not expand scope beyond these fixes.

${DOMAIN_LAW}

${CONVENTIONS}

Run the backend gate. All green -> commit as fix(evals): ${p.id} review fixes with the co-author trailer. If a blocker turns out to be a false positive after honest verification, say so in the summary with the evidence instead of "fixing" it.`
}

function collectPrompt() {
  return `Assemble the bounded review artifact for the eval-foundation branch in the repo at the current working directory. You are a collector, not a reviewer — do not judge anything, do not fix anything, do not run tests.

Run: git diff --stat ${BASE}...HEAD, then git diff ${BASE}...HEAD. Use that base commit exactly — do NOT diff against main, which would pull in an unrelated convention-scaffold commit.

Return the stat verbatim, and the diff verbatim in the diff field. This is the ONLY view a downstream judge will have of this work — it cannot open a file, so anything you leave out is invisible to it. If the diff is too large to return whole, drop in this order and only as far as you must: lockfiles, generated artifacts, then large mechanical fixture files. NEVER drop a source file to save room, and record every omission with its reason in "omitted". A silent truncation would make the judge's verdict a statement about a diff nobody read.

For test_inventory, extract from the diff the name of every test function added or changed (def test_... names, and any class-level grouping), one per line. Do not summarise or editorialise them.`
}

function judgePrompt(artifact, reports) {
  return `You are the tier-2 judge for the eval-foundation branch of W Chats. One judge runs per milestone, immediately before the merge to main.

You are NOT a code reviewer and this is NOT a second opinion on correctness — a per-phase adversarial reviewer already did that, against the tree, and its findings are below. Your question is different and narrower:

  DO THE CLAIMS MATCH THE EVIDENCE, AND WHAT IS ASSERTED BUT UNPROVEN?

The implementers marked their own homework. Every phase reported a gate result and a summary of what it built. Your job is to find the places where the confident sentence has nothing underneath it: a guarantee with no test, a test that asserts the wrong surface, a test that could not fail if the behaviour were deleted, an invariant defended in a comment rather than in code, work "verified" that was actually deferred to a live gate that cannot run on this machine, and any claim the diff contradicts outright.

WORK FROM THE ARTIFACT BELOW AND NOTHING ELSE. Do not open files, do not grep, do not run tests, do not explore the repository. If the artifact is insufficient to judge a claim, that insufficiency IS your finding — say the claim cannot be verified from the evidence provided, rather than going to look. This bound is deliberate: it is what keeps you reading claims against evidence instead of drifting into the review that already happened.

This branch's law is worth holding while you read. It exists because an audit found that the eval measured nothing (the label was used as the prediction), its results were written to a database branch that was then deleted, the deploy gate's eval query referenced columns that do not exist and therefore failed OPEN, five of seven red-team attackers were never given their tools and so reported "clean", and a human filing a FAILING trace stored that failing answer as ground truth. The repo's own retro records four recurrences of "a measurement that cannot fail" and two of "missing data treated as passing data".

So: a claim that any of these is now fixed deserves the hardest look at whether a test actually pins it — and specifically whether the test asserts the ABSENCE (no promotion path, no fail-open branch, no unstamped run) rather than merely that the new happy path works. Note also that no live PostgreSQL exists on this machine, so any claim resting on an integration test is resting on a SKIP.

=== WHAT THE IMPLEMENTERS CLAIM ===
${JSON.stringify(reports, null, 2)}

=== TIER-1 REVIEW FINDINGS (already raised, and where fixed, fixed) ===
${JSON.stringify(reports.map((r) => ({ phase: r.phase, verdict: r.review_verdict, blockers_fixed: r.blockers_fixed, concerns: r.concerns })), null, 2)}

=== DIFF STAT ===
${artifact.stat}

=== OMITTED FROM THE DIFF ===
${artifact.omitted.length ? artifact.omitted.join('\n') : '(nothing omitted)'}

=== TESTS ADDED OR CHANGED ===
${artifact.test_inventory}

=== DIFF ===
${artifact.diff}

Report everything. Over-report rather than under-report: an item you are unsure about goes in with your uncertainty stated, it does not get dropped. Do not self-censor to seem measured, and do not soften a finding because the tier-1 reviewer approved the phase — tier 1 was asking whether the code is broken, which is not what you were asked.

Your verdict field is read by the repo owner, verbatim. Write it plainly and for a person.`
}

const notes = []
const phaseReports = []

for (const p of PHASES) {
  phase(p.title)

  let impl = await agent(implPrompt(p, notes), { label: `impl:${p.id}`, phase: p.title, schema: IMPL_SCHEMA })
  if (!impl) {
    log(`${p.id}: implementer died (transient API error?) — one relaunch`)
    impl = await agent(implPrompt(p, notes), { label: `impl-relaunch:${p.id}`, phase: p.title, schema: IMPL_SCHEMA })
  }
  if (!impl) throw new Error(`${p.id}: implementer died twice`)

  if (!impl.gates_green) {
    log(`${p.id}: gate not green — one retry with failure context`)
    const retry = await agent(
      implPrompt(p, notes) +
        `\n\nPREVIOUS ATTEMPT FAILED THE GATE. Its summary: ${impl.summary}\nFailing output tail:\n${impl.gate_output_tail || '(none provided)'}\nDiagnose the root cause first (read the failing tests and the actual error), then fix and complete the phase. If the failure is the pre-existing docling collection error, you are running the gate wrong — use the --ignore flags from CLAUDE.md.`,
      { label: `impl-retry:${p.id}`, phase: p.title, schema: IMPL_SCHEMA },
    )
    if (retry) impl = retry
    if (!impl.gates_green) throw new Error(`${p.id}: gate still red after retry — stopping for orchestrator`)
  }

  if (impl.stopped_short) log(`${p.id}: implementer stopped short deliberately — ${impl.stopped_short}`)

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
    if (!fix) throw new Error(`${p.id}: fixer died twice with open blockers — stopping for orchestrator`)
    if (!fix.gates_green) throw new Error(`${p.id}: fix round left the gate red — stopping for orchestrator`)
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
    gate_output_tail: impl.gate_output_tail,
    guard_demonstrations: impl.guard_demonstrations || [],
    stopped_short: impl.stopped_short || null,
    review_verdict: review ? review.verdict : 'reviewer-died',
    blockers_fixed: review && review.blockers ? review.blockers.length : 0,
    fix_summary: fixSummary,
    concerns: review ? review.concerns : [],
  })
  log(`${p.id} done: ${impl.commit_ref} (${review ? review.verdict : 'no review'}, ${review && review.blockers ? review.blockers.length : 0} blockers)`)
}

// ---------------------------------------------------------------- tier-2 judge
// Bounded artifact only. The collector runs on the session model and touches the
// repo; the judge runs on Fable and never does.

phase('Tier-2 judge')

let artifact = await agent(collectPrompt(), { label: 'collect:artifact', phase: 'Tier-2 judge', schema: ARTIFACT_SCHEMA })
if (!artifact) {
  log('collector died — one relaunch')
  artifact = await agent(collectPrompt(), { label: 'collect-relaunch', phase: 'Tier-2 judge', schema: ARTIFACT_SCHEMA })
}

let judgement = null
if (!artifact) {
  log('WARNING: could not assemble the bounded artifact — tier-2 judge SKIPPED. Do not merge until a judge has run.')
} else {
  if (artifact.omitted.length) log(`artifact omits ${artifact.omitted.length} path(s): ${artifact.omitted.join('; ')}`)

  judgement = await agent(judgePrompt(artifact, phaseReports), {
    label: 'judge:tier-2',
    phase: 'Tier-2 judge',
    schema: JUDGE_SCHEMA,
    model: 'fable',
  })
  if (!judgement) {
    log('judge died — one relaunch')
    judgement = await agent(judgePrompt(artifact, phaseReports), { label: 'judge-relaunch', phase: 'Tier-2 judge', schema: JUDGE_SCHEMA, model: 'fable' })
  }

  if (judgement) {
    log(`tier-2 verdict: ${judgement.claimed_but_unproven.length} claimed-but-unproven, ${judgement.evidence_mismatches.length} evidence mismatch(es)`)
  } else {
    log('WARNING: tier-2 judge died twice. Do not merge until a judge has run.')
  }
}

log('BRANCH COMPLETE. Write .dev/traces/260805-eval-foundation.md before the merge. Step 0 of the ladder remains OWNER work: score apps/api/tests/evals/calibration/human_scores.csv — no agent may fill that column.')

return { phases: phaseReports, accumulated_notes: notes, judgement }
