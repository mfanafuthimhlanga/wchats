export const meta = {
  name: 'd6-labelling-loop',
  description: 'Build the path that produces an owner-authored label (audit D6) — backend only',
  phases: [
    { title: 'P1 tier' },
    { title: 'P1 review' },
    { title: 'P1 fix' },
    { title: 'P2 queue' },
    { title: 'P2 review' },
    { title: 'P2 fix' },
    { title: 'P3 downstream' },
    { title: 'P3 review' },
    { title: 'P3 fix' },
    { title: 'Yield' },
    { title: 'Collect' },
    { title: 'Judge', model: 'fable' },
  ],
}

const REPO = 'C:/Users/Bantu/mzansi-agentive/wchats'
const API = REPO + '/apps/api'

const CONTEXT = `
REPO: ${REPO}   API package: ${API}   BRANCH: feat/d6-labelling-loop
Based on feat/d1-agent-invocation (4179a5c), NOT main. Do not rebase, do not merge anything.

READ FIRST, in this order:
  1. ${REPO}/.dev/plans/260808-d6-labelling-loop.md   <- the contract for this work
  2. ${REPO}/CLAUDE.md                                <- binding project rules
  3. app/services/eval_service.py LABEL_TRUST_TIERS / SCENARIO_SOURCE_TRUST_TIER / promotable_answer

THE GATE COMMAND (run from ${API}, this exact form):
  .venv/Scripts/python.exe -m pytest tests/unit -q \\
    --ignore=tests/unit/test_chunking_service.py \\
    --ignore=tests/unit/test_docling_service.py

OBSERVED BASELINE at 4179a5c: 1873 passed, 11 skipped, 0 failed, 366s, 1884 collected.
It takes ~6 minutes; let it finish. ~15 tests legitimately cost 14-16s each on the turn path.

TWO CONTROLS THIS PROJECT LEARNED THE HARD WAY — both are required of you:

  (a) THE IGNORED-NEW-FILES CONTROL (BACKLOG 2.26). Prove your delta is exactly the tests you added:
      re-run the gate with your new test file(s) ALSO --ignore'd and show it still reads 1873/11.
      Test-count arithmetic cannot see a pre-existing test silently changing status; this can.
  (b) PERSIST YOUR OUTPUT (BACKLOG 2.20). The previous branch lost 17 of 48 review findings to a
      temp-directory journal that did not survive the session. Write your full findings to
      ${REPO}/.dev/reference/ as a markdown file BEFORE you return, and name the file in your result.

BINDING RULES FROM CLAUDE.md:
  - No Docker. No PostgreSQL on this machine — every -m integration test SKIPS, and a skip is
    UNOBSERVED, never a pass. CONTROL_DB_URL is live Neon production and is NEVER a substitute.
    No migration you write can be applied here. Say so plainly; never imply you ran one.
  - Connection strings never in Celery task args. acks_late=True AND idempotency on every task.
  - A test for every behaviour change.
  - A metric over zero valid observations is 'unknown', never 'pass'.
  - A rate without its denominator must not be constructible from what a task returns.
  - A negative test never observed to fail is indistinguishable from a tautology. For every guard:
    mutate it, RUN it, observe red, restore from HEAD unconditionally, RUN again, observe green,
    and record the VERBATIM OUTPUT of both runs. Record the exact selector you ran, too.
  - PowerShell breaks on multi-line -m arguments: write the message to a temp file, git commit -F.
  - Commit style: type(scope): message, ending with:
    Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  - NEVER merge to main. NEVER edit settings.json.

HONESTY CONTRACT: report what you observed. If you did not run something, say so. This project has
just spent a whole branch learning that an unverified claim which reads as verified is the defect.
`

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    tests_added: { type: 'array', items: { type: 'string' } },
    suite_after: { type: 'string', description: 'Verbatim final pytest line, or NOT RUN' },
    ignored_new_files_control: { type: 'string', description: 'Verbatim final line of the gate re-run with your new test files also ignored, or NOT RUN with the reason' },
    mutation_proofs: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          guard: { type: 'string' },
          selector: { type: 'string', description: 'The exact pytest selector you ran' },
          mutation: { type: 'string' },
          observed_red: { type: 'string' },
          observed_green: { type: 'string' },
        },
        required: ['guard', 'selector', 'mutation', 'observed_red', 'observed_green'],
      },
    },
    claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: { claim: { type: 'string' }, evidence: { type: 'string' } },
        required: ['claim', 'evidence'],
      },
    },
    deviations: { type: 'array', items: { type: 'string' } },
    not_done: { type: 'array', items: { type: 'string' } },
    persisted_to: { type: 'string', description: 'Path under .dev/ where your full report was written' },
    commit_sha: { type: 'string' },
  },
  required: ['summary', 'files_changed', 'tests_added', 'suite_after', 'ignored_new_files_control', 'mutation_proofs', 'claims', 'deviations', 'not_done', 'persisted_to', 'commit_sha'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'nit'] },
          file: { type: 'string' },
          line: { type: 'integer' },
          claim: { type: 'string' },
          failure_scenario: { type: 'string' },
          suggested_fix: { type: 'string' },
        },
        required: ['severity', 'file', 'claim', 'failure_scenario', 'suggested_fix'],
      },
    },
    unsupported_claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: { claim: { type: 'string' }, why_unsupported: { type: 'string' } },
        required: ['claim', 'why_unsupported'],
      },
    },
    summary: { type: 'string' },
    persisted_to: { type: 'string' },
  },
  required: ['findings', 'unsupported_claims', 'summary', 'persisted_to'],
}

const REVIEW_STANCE = `
You are an adversarial reviewer. Report EVERYTHING at every severity, including things you are
unsure about — the orchestrator filters, you do not. Under-reporting is the failure mode.

Investigate the actual codebase; take the implementer's word for nothing. Re-run the gate yourself.
For every mutation proof, ask whether the red could have come from something other than the guard
working — a missing attribute, a wrong mock, an import-order artifact. This repo has shipped a guard
demonstrated only inside the complement of its own blind spot, and a fallback mutation that survived
all 163 tests of its own module.

THE STANDING TRAP FOR THIS PHASE: the whole point is a label a HUMAN authored. Any path by which a
model, an agent, a task, a judge or a fixture can write a reference_answer at a human trust tier
defeats the entire phase, and it will not announce itself. Hunt for it specifically.
`

phase('P1 tier')
const p1 = await agent(`${CONTEXT}

TASK — P1: THE TIER THAT DOES NOT EXIST YET. Read the plan's P1 section; it is the contract.

eval_service.LABEL_TRUST_TIERS defines human_verified (2) and human_authored (3). Nothing in the
system can produce either: SCENARIO_SOURCE_TRUST_TIER maps all four scenario sources to
model_generated or customer_negative. Give the system a way to represent "a human wrote this answer."

  - The tier must be carried by the LABEL, not inferred from the scenario's source.
    SCENARIO_SOURCE_TRUST_TIER reasons about where the QUESTION came from. A mined question with an
    owner-written answer is customer_negative in origin and human_authored in label. Collapsing the
    two is how a model_generated string gets admitted on a human tier — promotable_answer's docstring
    already warns about exactly this coming apart.
  - alembic_tenant migration for the label provenance. Widen 0011's CHECK the way 0011 itself did:
    introspect the constraint name via pg_constraint/pg_attribute at apply time, do NOT hardcode it.
    NOTE HONESTLY that no ALTER TABLE can execute here.
  - NO MODEL MAY EVER WRITE AT A HUMAN TIER. Structural, not advisory. The write path that stamps
    human_authored must be unreachable from any agent, task, judge or fixture. Mutate the
    restriction, observe red, restore, observe green, record verbatim.

Commit on feat/d6-labelling-loop when the gate is green.`, { schema: IMPL_SCHEMA })

phase('P1 review')
const p1r = await agent(`${CONTEXT}
${REVIEW_STANCE}

Review the P1 commit(s) on feat/d6-labelling-loop. The implementer reported:
${JSON.stringify(p1, null, 2)}

Most important questions:
  1. Can anything that is not a human reach the human_authored write path? Enumerate every caller and
     try to construct one. A fixture counts. decision_eval_service.FIXTURE_LABEL_TRUST_TIER is
     already the literal 'human_authored' — check whether that creates a route or a confusion.
  2. Is label provenance genuinely separate from source provenance, or did they get collapsed?
  3. Does the migration introspect the constraint name, or hardcode it? 0011 exists as the reference.
  4. Is the guard structural, or does it only catch one spelling of one mutation? That is the exact
     defect the previous branch's P1 guard had.`, { schema: REVIEW_SCHEMA })

phase('P1 fix')
const p1f = await agent(`${CONTEXT}

Fix the findings below on feat/d6-labelling-loop. BOUNDED: these findings only. Do not start P2.
A finding you believe is wrong goes in not_done with your reason — never silently skipped.

FINDINGS:
${JSON.stringify(p1r, null, 2)}

Re-run the gate and the ignored-new-files control. Commit.`, { schema: IMPL_SCHEMA })

phase('P2 queue')
const p2 = await agent(`${CONTEXT}

TASK — P2: THE QUEUE. Read the plan's P2 section; it is the contract.

GET unlabelled scenarios, POST a label, in app/api/v1/evals.py. Ordering is the interesting part.

  - ORDER BY UNCERTAINTY, NOT RECENCY. validators.py:220 already emits judge confidence into
    job_events and it is discarded for ranking (BACKLOG 6.4). Surfacing the rows the judges were
    least sure about is worth 5-10x per owner label over surfacing the newest. If the confidence
    signal turns out not to be joinable to a scenario, say so plainly and order by something you CAN
    defend — do not invent a proxy and present it as uncertainty.
  - A labelled row becomes eligible to the EXISTING selector with NO change to the selector. The
    'reference_answer != ""' exclusion is correct and is pinned by
    test_the_scenario_is_inert_to_the_eval_selector_by_construction. Do not touch it.
  - Report (unlabelled, labelled, eligible) as counts with their denominator.
  - Auth: these are tenant-scoped routes. Match the existing auth and tenant-isolation pattern in
    evals.py exactly; a labelling route that crosses tenants is a critical defect.

Commit when the gate is green.`, { schema: IMPL_SCHEMA })

phase('P2 review')
const p2r = await agent(`${CONTEXT}
${REVIEW_STANCE}

Review the P2 commit(s). The implementer reported:
${JSON.stringify(p2, null, 2)}

Most important questions:
  1. TENANT ISOLATION. Can one tenant list or label another tenant's scenarios? Trace the auth
     dependency and the SQL. This is the highest-severity thing in the phase.
  2. Is the ordering actually uncertainty, or a proxy presented as one?
  3. Can the POST write an empty or whitespace-only reference_answer, re-inerting the row while
     marking it labelled? What about a label that equals the agent's own failing answer?
  4. Does the selector still exclude unlabelled rows — is its pin still green and still meaningful?
  5. Are the three counts derivable without their denominator anywhere?`, { schema: REVIEW_SCHEMA })

phase('P2 fix')
const p2f = await agent(`${CONTEXT}

Fix the findings below. BOUNDED: these findings only. Do not start P3.
A finding you believe is wrong goes in not_done with your reason.

FINDINGS:
${JSON.stringify(p2r, null, 2)}

Re-run the gate and the ignored-new-files control. Commit.`, { schema: IMPL_SCHEMA })

phase('P3 downstream')
const p3 = await agent(`${CONTEXT}

TASK — P3: WHAT A LABEL DOES DOWNSTREAM. Read the plan's P3 section; it is the contract.

  - Labelled rows enter the eval. Whether they enter the GOLDEN set is a SEPARATE ASSERTION, never
    inherited — eval.py:372 already insists membership of the golden set is asserted, not inherited.
  - SETTLED BY THE OWNER, do not re-litigate: verified_qa promotion stays OFF. This run is EVAL-ONLY.
    Nothing an owner labels may reach a customer on this branch. Record the disablement WITH ITS
    REASON on the run, the way eval_service already does for the existing disablement — an absence a
    later reader has to infer is not acceptable. Add a guard test that fails if a labelled scenario
    can reach verified_qa, and mutate it to prove the guard works.
  - A labelled row's effect on the eval's counts must keep (attempted, valid, scored) honest.

Commit when the gate is green.`, { schema: IMPL_SCHEMA })

phase('P3 review')
const p3r = await agent(`${CONTEXT}
${REVIEW_STANCE}

Review the P3 commit(s). The implementer reported:
${JSON.stringify(p3, null, 2)}

Most important questions:
  1. Is verified_qa GENUINELY unreachable from a labelled scenario? Try to construct the path.
     promotable_answer + VERIFIED_QA_MIN_TRUST_TIER now have a tier that clears the gate for the
     first time in the project's history — that is exactly when a dead path comes alive by accident.
  2. Does golden-set membership stay asserted rather than inherited?
  3. Is the disablement recorded with a reason, or merely absent?
  4. Do the eval's counts stay honest once labelled rows enter?`, { schema: REVIEW_SCHEMA })

phase('P3 fix')
const p3f = await agent(`${CONTEXT}

Fix the findings below. BOUNDED: these findings only.
A finding you believe is wrong goes in not_done with your reason.

FINDINGS:
${JSON.stringify(p3r, null, 2)}

Then run the gate one final time for the whole branch, plus the ignored-new-files control. Commit.`, { schema: IMPL_SCHEMA })

// ---------------------------------------------------------------------------
// Yield — the question the owner chose to answer before any UI is built
// ---------------------------------------------------------------------------

phase('Yield')
const yieldReport = await agent(`${CONTEXT}

You write NO CODE. You answer one question the owner explicitly asked before any console work starts:

    HOW MANY ROWS WOULD THE LABELLING QUEUE ACTUALLY CONTAIN?

scenario_service.mine_production_scenarios is the only producer of mined rows. Read it closely. Its
own docstring admits the job_events emit payload carries NEITHER conversation_id NOR question, and
the code 'continue's past every flagged job where it cannot recover a question via
jobs.conversation_id -> tenant messages.

You cannot measure this empirically — there is no PostgreSQL here and CONTROL_DB_URL is live
production which you must NOT touch. So do the honest version:

  1. Enumerate every condition that must hold for ONE flagged event to become ONE queued row.
     Quote the code at each step with file:line.
  2. For each condition, say what makes it true or false in production, and whether anything in the
     repo establishes that it holds. 'jobs.conversation_id is populated' is a schema-and-writer
     question you CAN answer from the code — go and answer it.
  3. State the yield as a conjunction of unknowns, not a number you cannot support. If the honest
     answer is 'plausibly zero', say plausibly zero and show why.
  4. Say exactly what measurement WOULD settle it, and what it needs (BACKLOG 0.2).
  5. Recommend whether the console queue (P4) is worth building yet.

Write it to ${REPO}/.dev/reference/d6-mining-yield.md and return it.`, { schema: {
  type: 'object',
  properties: {
    conditions: { type: 'string', description: 'Every condition with file:line, in order' },
    established_or_not: { type: 'string' },
    yield_estimate: { type: 'string' },
    what_would_settle_it: { type: 'string' },
    p4_recommendation: { type: 'string' },
    persisted_to: { type: 'string' },
  },
  required: ['conditions', 'established_or_not', 'yield_estimate', 'what_would_settle_it', 'p4_recommendation', 'persisted_to'],
} })

phase('Collect')
const artifact = await agent(`${CONTEXT}

You are the COLLECTOR. You write no code. Assemble the bounded artifact for the tier-2 judge, which
never explores the repository and reads only what you hand it.

Produce:
  1. The full diff of feat/d6-labelling-loop against feat/d1-agent-invocation (4179a5c). If it
     exceeds ~1500 lines, include every hunk of app/ and alembic_tenant/ in full and summarise only
     test files, naming which and how many lines you dropped. NEVER silently truncate.
  2. The verbatim final pytest line from the last gate run, versus the 1873/11/0 baseline, AND the
     ignored-new-files control line from each phase (or note which phase failed to produce one).
  3. Every implementer claim and every mutation proof, verbatim, from all six implementation reports,
     each with the selector it was run under.
  4. Every tier-1 finding and whether the DIFF shows it fixed, partially fixed, or not fixed —
     determined from the diff, not from the fixer's say-so. Note the .dev/reference/ path each
     reviewer persisted its findings to.
  5. THE PHASE-DECIDING QUESTION, answered from the diff and stated plainly: can anything that is not
     a human write a reference_answer at a human trust tier, and can a labelled scenario reach
     verified_qa? Quote the hunks that decide both.
  6. What the branch does NOT prove: every gate that skipped, the unapplied migration, every path
     unexercised for want of a database.
  7. The mining-yield report's conclusion.

Write it to ${REPO}/.dev/reference/d6-tier2-artifact.md and return it.`, { schema: {
  type: 'object',
  properties: {
    diff: { type: 'string' },
    suite_line: { type: 'string' },
    controls: { type: 'string' },
    implementer_claims: { type: 'string' },
    mutation_proofs: { type: 'string' },
    tier1_findings_status: { type: 'string' },
    phase_deciding_question: { type: 'string' },
    unproven: { type: 'string' },
    yield_conclusion: { type: 'string' },
    summarised_or_dropped: { type: 'string' },
  },
  required: ['diff', 'suite_line', 'controls', 'implementer_claims', 'mutation_proofs', 'tier1_findings_status', 'phase_deciding_question', 'unproven', 'yield_conclusion', 'summarised_or_dropped'],
} })

phase('Judge')
const verdict = await agent(`You are the TIER-2 JUDGE for milestone D6 of the W Chats project.

You judge a BOUNDED ARTIFACT ONLY. Do not explore the repository, do not read files, do not run
anything. Everything you may consider is below.

Your question is NOT "what is broken?" — tier-1 adversarial reviewers already asked that against the
live code, three times. Your question is:

    DO THE CLAIMS MATCH THE EVIDENCE, AND WHAT IS ASSERTED BUT UNPROVEN?

Context. This branch closes audit defect D6. The system's label trust hierarchy
(eval_service.LABEL_TRUST_TIERS) defined human_verified and human_authored tiers that NOTHING could
produce, so mined production failures were stored and never scored, and the customer-facing
verified_qa path was dead code. This branch builds the missing path: an owner supplies a
reference_answer at a human tier.

The two things that decide whether it is real:
  - A label at a human tier must be unwritable by any model, agent, task, judge or fixture. If any
    such path exists, the phase has built a machine for laundering model output into human-tier
    labels, which is worse than the gap it replaced.
  - The owner settled that this run is EVAL-ONLY: verified_qa promotion stays off. A labelled
    scenario reaching verified_qa would be served to real customers ahead of retrieval, and it would
    be a scope violation as well as a safety one.

The project's standing principles: a metric over zero valid observations is 'unknown', never 'pass';
a model-generated label may never gate a deploy or reach a customer; a negative test never observed
to fail is indistinguishable from a tautology. There is no PostgreSQL, so no migration has been
applied and every integration test skipped.

Report everything. Do not filter to high severity.

=== THE ARTIFACT ===
${JSON.stringify(artifact, null, 2)}
`, { model: 'fable', schema: {
  type: 'object',
  properties: {
    verdict: { type: 'string' },
    mergeable: { type: 'boolean' },
    human_tier_is_unwritable_by_machines: { type: 'string', description: 'Cite the hunks. This is the phase-deciding question.' },
    verified_qa_stays_unreachable: { type: 'string', description: 'Cite the hunks.' },
    claims_supported: { type: 'array', items: { type: 'string' } },
    claims_unproven: {
      type: 'array',
      items: {
        type: 'object',
        properties: { claim: { type: 'string' }, what_would_prove_it: { type: 'string' } },
        required: ['claim', 'what_would_prove_it'],
      },
    },
    evidence_mismatches: {
      type: 'array',
      items: {
        type: 'object',
        properties: { claim: { type: 'string' }, evidence_says: { type: 'string' } },
        required: ['claim', 'evidence_says'],
      },
    },
    must_fix_before_merge: { type: 'array', items: { type: 'string' } },
    new_backlog_items: { type: 'array', items: { type: 'string' } },
  },
  required: ['verdict', 'mergeable', 'human_tier_is_unwritable_by_machines', 'verified_qa_stays_unreachable', 'claims_supported', 'claims_unproven', 'evidence_mismatches', 'must_fix_before_merge', 'new_backlog_items'],
} })

log('D6 workflow complete — tier-2 verdict returned')

return {
  p1: { impl: p1, review: p1r, fix: p1f },
  p2: { impl: p2, review: p2r, fix: p2f },
  p3: { impl: p3, review: p3r, fix: p3f },
  yield: yieldReport,
  verdict,
}
