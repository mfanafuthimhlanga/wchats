export const meta = {
  name: 'd1-agent-invocation',
  description: 'Make the RAG eval invoke the real agent instead of scoring its own label (audit D1)',
  phases: [
    { title: 'P1 seam' },
    { title: 'P1 review' },
    { title: 'P1 fix' },
    { title: 'P1b safety' },
    { title: 'P1b review' },
    { title: 'P1b fix' },
    { title: 'P2 invoke' },
    { title: 'P2 review' },
    { title: 'P2 fix' },
    { title: 'P3 gate' },
    { title: 'P3 review' },
    { title: 'P3 fix' },
    { title: 'Collect' },
    { title: 'Judge', model: 'fable' },
  ],
}

const REPO = 'C:/Users/Bantu/mzansi-agentive/wchats'
const API = REPO + '/apps/api'

const CONTEXT = `
REPO: ${REPO}   API package: ${API}   BRANCH: feat/d1-agent-invocation

READ FIRST, in this order:
  1. ${REPO}/.dev/plans/260807-d1-agent-invocation.md   <- the contract for this work
  2. ${REPO}/CLAUDE.md                                  <- binding project rules
  3. ${REPO}/.dev/reference/measurement-layer-audit.md  <- D1 and its six siblings

THE GATE COMMAND (run from ${API}, this exact form):
  .venv/Scripts/python.exe -m pytest tests/unit -q \\
    --ignore=tests/unit/test_chunking_service.py \\
    --ignore=tests/unit/test_docling_service.py

OBSERVED BASELINE at af0f601: 1675 passed, 11 skipped, 0 failed, ~451s (it is slow; let it finish).
Any movement in the pass count is a result you must report, not smooth over.

BINDING RULES FROM CLAUDE.md THAT APPLY TO YOU:
  - No Docker. No PostgreSQL exists on this machine; every -m integration test SKIPS, and a skip is
    UNOBSERVED, never a pass. CONTROL_DB_URL is live Neon production and is NEVER a substitute.
  - Connection strings never in Celery task args; tasks take tenant_id/agent_id and decrypt at runtime.
  - acks_late=True AND idempotency, both, on every task.
  - A test for every behaviour change. A change that alters behaviour without a test is incomplete.
  - A metric over zero valid observations is 'unknown', never 'pass'. Missing data is never passing data.
  - A negative test never observed to fail is indistinguishable from a tautology. For any guard you
    add: mutate it, RUN the test, observe red, restore from HEAD unconditionally, RUN again, observe
    green. Record the OBSERVED OUTPUT of both runs, not your intention to have done it.
  - PowerShell breaks on multi-line -m arguments: write commit messages to a temp file, git commit -F.
  - Commit style: type(scope): message, ending with:
    Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  - NEVER merge to main. NEVER edit settings.json. Commit on feat/d1-agent-invocation only.

HONESTY CONTRACT: report what you observed. If you did not run something, say you did not run it. An
unverified claim that reads as verified is the single defect class this whole phase exists to remove.
`

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string', description: 'What you changed, in 3-6 sentences' },
    files_changed: { type: 'array', items: { type: 'string' } },
    tests_added: { type: 'array', items: { type: 'string' }, description: 'file::test_name for each' },
    suite_before: { type: 'string', description: 'Verbatim final pytest line before your change, or NOT RUN' },
    suite_after: { type: 'string', description: 'Verbatim final pytest line after your change, or NOT RUN' },
    mutation_proofs: {
      type: 'array',
      description: 'One per guard added. Verbatim observed output of the red run and the green run.',
      items: {
        type: 'object',
        properties: {
          guard: { type: 'string' },
          mutation: { type: 'string' },
          observed_red: { type: 'string' },
          observed_green: { type: 'string' },
        },
        required: ['guard', 'mutation', 'observed_red', 'observed_green'],
      },
    },
    claims: {
      type: 'array',
      description: 'Every substantive claim you want believed, each with the evidence that supports it',
      items: {
        type: 'object',
        properties: { claim: { type: 'string' }, evidence: { type: 'string' } },
        required: ['claim', 'evidence'],
      },
    },
    deviations: { type: 'array', items: { type: 'string' }, description: 'Where you departed from the plan and why' },
    not_done: { type: 'array', items: { type: 'string' }, description: 'Anything in scope you did not finish or could not verify' },
    commit_sha: { type: 'string' },
  },
  required: ['summary', 'files_changed', 'tests_added', 'suite_before', 'suite_after', 'mutation_proofs', 'claims', 'deviations', 'not_done', 'commit_sha'],
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
          category: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'integer' },
          claim: { type: 'string', description: 'The defect, one sentence' },
          failure_scenario: { type: 'string', description: 'Concrete inputs/state -> wrong outcome' },
          suggested_fix: { type: 'string' },
        },
        required: ['severity', 'category', 'file', 'claim', 'failure_scenario', 'suggested_fix'],
      },
    },
    unsupported_claims: {
      type: 'array',
      description: "Implementer claims whose stated evidence does not actually support them",
      items: {
        type: 'object',
        properties: { claim: { type: 'string' }, why_unsupported: { type: 'string' } },
        required: ['claim', 'why_unsupported'],
      },
    },
    summary: { type: 'string' },
  },
  required: ['findings', 'unsupported_claims', 'summary'],
}

const REVIEW_STANCE = `
You are an adversarial reviewer. Report EVERYTHING you find at every severity, including things you
are unsure about — the orchestrator filters, you do not. Under-reporting is the failure mode here.

Investigate the actual codebase; do not take the implementer's word for anything. Specifically:
  - Re-run the gate command yourself and report the verbatim final line.
  - For every mutation_proof claimed, consider whether the mutation could pass for a reason other
    than the guard working. A guard demonstrated only inside the complement of its own blind spot is
    not demonstrated (this repo has shipped exactly that defect — see BACKLOG 3.3).
  - For every test added, ask what it would still pass if the production change were reverted.
  - Look for measurement that cannot move: a metric whose value is determined by construction rather
    than by the system's behaviour is the whole reason this phase exists, and it can reappear in the
    fix as easily as in the original.
`

// ---------------------------------------------------------------------------
// P1 — the shared invocation seam
// ---------------------------------------------------------------------------

phase('P1 seam')
const p1 = await agent(`${CONTEXT}

TASK — Phase P1 of the plan: THE SEAM. Read the plan's P1 section; it is the contract.

Extract the construction of ClaudeAgentOptions out of run_agent_turn
(app/worker/tasks/runtime/agent.py) into ONE callable that both run_agent_turn and the eval task will
go through. The eval does not use it yet — that is P2. P1 delivers the seam and its guard only.

Requirements:
  - NO behaviour change to the chat path. The gate suite must read exactly 1675 passed / 11 skipped
    / 0 failed afterwards. Any other number is a result to report, not to explain away.
  - The seam must carry everything that determines agent behaviour: system prompt, tools, capability
    envelope, model, whatever else run_agent_turn currently assembles. If something behaviour-
    determining stays outside the seam, the eval will measure a different agent than production
    serves, which is this repo's recurring defect wearing a new hat.
  - THE DRIFT GUARD IS THE POINT OF THIS PHASE. Add a test that FAILS if run_agent_turn constructs
    ClaudeAgentOptions by any route other than the seam. Structural, not advisory: a comment saying
    "always use the seam" is not a guard. Then mutate it, run it, observe red, restore from HEAD,
    run again, observe green, and record both verbatim.

Commit on feat/d1-agent-invocation when the suite is green.`, { schema: IMPL_SCHEMA })

phase('P1 review')
const p1r = await agent(`${CONTEXT}
${REVIEW_STANCE}

Review the P1 commit on feat/d1-agent-invocation (diff it against its parent).

The implementer reported:
${JSON.stringify(p1, null, 2)}

The question that matters most: CAN THE SEAM DRIFT? If a future edit to run_agent_turn changed the
system prompt, the tool set, or the capability envelope without going through the seam, would
anything fail? Try to construct that edit and see whether the guard catches it. If the guard only
catches a literal 'ClaudeAgentOptions(' string in one file, say so plainly — that is a grep, not a
guard.

Also: did anything behaviour-determining stay OUTSIDE the seam?`, { schema: REVIEW_SCHEMA })

phase('P1 fix')
const p1f = await agent(`${CONTEXT}

Fix the findings below on feat/d1-agent-invocation. You are a BOUNDED fixer: address these findings
and nothing else. Do not refactor, do not improve unrelated code, do not start P2.

If you judge a finding to be wrong, do NOT silently skip it — record it in not_done with your reason.

FINDINGS:
${JSON.stringify(p1r, null, 2)}

Re-run the gate command and report the verbatim final line. Commit.`, { schema: IMPL_SCHEMA })

// ---------------------------------------------------------------------------
// P1b — the two agent.py changes P1's review surfaced. Both settled by the owner.
// ---------------------------------------------------------------------------

phase('P1b safety')
const p1b = await agent(`${CONTEXT}

TASK — Phase P1b of the plan. Read the plan's "P1b" section; it is the contract. TWO changes to
app/worker/tasks/runtime/agent.py, both already decided by the owner. Implement the decisions; do not
reopen them.

--- 1. RECORDED MODE. This one is why P2 cannot start without you. ---

build_agent_options currently returns options carrying a LIVE tool server bound to the tenant's real
conn_str, granting all six mutating skills — tests/unit/test_agent_options_seam.py:1147 pins exactly
that. From P2, an eval scenario in which the agent decides to refund would execute a real refund
against the tenant's provider.

SETTLED: a mandatory \`side_effects: Literal["live", "recorded"]\` parameter on the seam. NO DEFAULT —
a caller that does not state which it wants must fail loudly, because a default is how the eval path
silently ends up live.

  - "live"     — byte-for-byte what run_agent_turn does today. The chat path must not change; the
                 suite reading 1695 passed / 11 skipped / 0 failed is your evidence that it did not.
  - "recorded" — swaps notify_fn, the retrieval-metrics writer and the transactional ProviderAdapter
                 for no-ops.

The owner rejected the alternative (stripping mutating skills from the eval's allowed_tools) for a
reason you must preserve: the agent must still SEE all seven tools and still be able to CHOOSE them,
so a scenario testing "the agent should refuse to refund here" can still fail. An agent that cannot
attempt the wrong thing cannot be measured on refusing it.

Two further requirements the owner attached:
  - THE NO-OP MUST BE UNMISSABLE, NEVER A SILENT SUCCESS. A recorded issue_refund that returns a
    cheerful confirmation teaches the agent it worked and diverges the remainder of the turn. Record
    the attempt and return something the transcript shows plainly for what it is.
  - THE RECORDING IS EVAL SIGNAL, NOT DEBRIS. That the agent chose to call a mutating skill is one of
    the most valuable things an eval can observe — it is capability-envelope adherence. Make it
    retrievable by P2, do not just drop it on the floor.

--- 2. CANARY ORDERING (BACKLOG 2.6). ---

P1 moved _resolve_turn_prompt_version ahead of the seam, so conversations.metadata.prompt_version_id
is now committed BEFORE build_agent_options can raise. A turn that dies there leaves the conversation
sticky to a version that never served it, where it used to re-roll.

SETTLED: resolve before, commit after. The resolution stays where P1 put it — its soul fields are a
genuine input to the system prompt. The WRITE moves back behind a successful build_agent_options.

  - tests/unit/test_agent_options_seam.py::test_the_canary_choice_is_committed_before_the_options_can_fail
    currently pins P1's behaviour. It must be INVERTED to pin the new behaviour, and you must observe
    it RED against the current code before your change and green after. Record both verbatim.

--- BOTH ---

Every guard you add or invert gets the full treatment: mutate, run, observe red, restore from HEAD,
run, observe green, record the verbatim output of both. Commit on feat/d1-agent-invocation when the
gate suite is green, and report its verbatim final line.`, { schema: IMPL_SCHEMA })

phase('P1b review')
const p1br = await agent(`${CONTEXT}
${REVIEW_STANCE}

Review the P1b commit(s) on feat/d1-agent-invocation.

The implementer reported:
${JSON.stringify(p1b, null, 2)}

The questions that matter most, in order:
  1. CAN THE EVAL PATH REACH A LIVE ADAPTER? Trace every route from build_agent_options(side_effects=
     "recorded") to the six mutating skills and to notify_fn. If ANY of them still touches the real
     ProviderAdapter, the real mailer, or the tenant's metrics tables, that is critical and it is the
     finding that matters most in this entire phase. Money is downstream of it.
  2. Is there any way to call the seam WITHOUT stating side_effects — a default that crept in, a
     **kwargs, a wrapper, a partial? Try it and see what happens.
  3. Is the recorded no-op distinguishable from a real success IN THE TRANSCRIPT THE AGENT SEES? If a
     recorded issue_refund reads like a successful one, the agent's subsequent turns diverge from
     production and the eval measures a conversation that could not happen.
  4. Does "live" still behave byte-for-byte as before? The chat path is production.
  5. On the canary inversion: does the rewritten test fail against the OLD code for the RIGHT reason,
     or does it fail incidentally (missing attribute, wrong mock) — the exact defect P1's own guard
     was found to have?`, { schema: REVIEW_SCHEMA })

phase('P1b fix')
const p1bf = await agent(`${CONTEXT}

Fix the findings below on feat/d1-agent-invocation. BOUNDED: these findings only. Do not start P2.
A finding you believe is wrong goes in not_done with your reason — never silently skipped.

FINDINGS:
${JSON.stringify(p1br, null, 2)}

Re-run the gate command, report the verbatim final line, commit.`, { schema: IMPL_SCHEMA })

// ---------------------------------------------------------------------------
// P2 — invoke, and record that you invoked
// ---------------------------------------------------------------------------

phase('P2 invoke')
const p2 = await agent(`${CONTEXT}

TASK — Phase P2 of the plan: INVOKE. Read the plan's P2 section; it is the contract.

P1 built the seam and P1b added recorded mode to it (both reviewed and fixed). Now make
app/worker/tasks/runtime/eval.py actually invoke the agent through that seam, per scenario, instead
of eval.py:374-375's
  # For M6: use reference_answer as proxy agent_response to test the eval harness
  "agent_response": row[3],
where row[3] IS reference_answer. That line is D1. Killing it is this phase.

Requirements, each of which the plan explains:
  - THE EVAL PATH INVOKES THE SEAM WITH side_effects="recorded", ALWAYS. Never "live". P1b made the
    parameter mandatory precisely so this cannot be forgotten; add a test that fails if the eval path
    ever requests "live", and observe it red. One eval scenario in which the agent decides to refund
    would otherwise execute a real refund against the tenant's provider.
  - The mutating-skill attempts P1b records ARE eval signal. Persist them with the run: an agent that
    tried to issue a refund it should have refused is a finding, and it is invisible unless you carry
    it out of the turn.
  - agent_response becomes the agent's real response_text.
  - retrieved_contexts must come from the AGENT'S OWN retrieve tool result, not from row[4].
    Scoring faithfulness against contexts the agent never saw is D1 in a different costume.
  - agent.py:588 truncates the retrieve result to 1800 chars. Faithfulness over a truncated context
    marks a claim unsupported when the support was merely cut off. Either carry the untruncated
    result on the eval path or record the truncation in the run's provenance. Do not leave it implicit.
  - A scenario whose agent call FAILS is EXCLUDED AND COUNTED, never scored 0. Zero is not a low
    score, it is the absence of one — tests/evals/calibration/compute_correlation.py:485 already
    learned this; read it and reuse the shape.
  - A run where too few scenarios produced a response reports 'unknown', never 'pass'. Reuse the
    MIN_PAIR_RATE shape from compute_correlation.py rather than inventing a second one.
  - Write the provenance field agent_invoked on the eval run. P3 consumes it.
  - Bound cost and latency: the golden set runs in FULL every eval plus EXPLORATORY_SAMPLE_SIZE
    rotating rows, one live SDK call each at a 90s per-turn timeout. Add a concurrency bound and a
    per-run ceiling, and put both in provenance.

Tests, at minimum: the agent is invoked once per scenario; a failing scenario is excluded not zeroed;
a run below the response-rate floor reports unknown; and THE D1 REGRESSION PIN — agent_response is
never equal to reference_answer for a scored row. Mutate each guard, observe red, restore, observe
green, record verbatim.

Note honestly in not_done that no end-to-end eval run can be observed on this machine (no Postgres),
and do not imply otherwise anywhere.

Commit when the gate suite is green.`, { schema: IMPL_SCHEMA })

phase('P2 review')
const p2r = await agent(`${CONTEXT}
${REVIEW_STANCE}

Review the P2 commit(s) on feat/d1-agent-invocation.

The implementer reported:
${JSON.stringify(p2, null, 2)}

The questions that matter most:
  1. Is there ANY remaining path where the eval's prediction is derived from the label? Grep for it.
     D1 returning by a side door is the failure this phase must not have.
  2. Are retrieved_contexts genuinely the agent's own? If they still come from the scenario row on
     any path, faithfulness is measured against contexts the agent never saw.
  3. What happens on partial failure — 3 of 40 scenarios erroring, 38 of 40, all 40? Trace each and
     say what the run reports. If any of those paths can report a PASS, that is critical.
  4. Does the truncation at agent.py:588 still silently affect the score?
  5. Is agent_invoked written on every path that persists a run, including the error and early-return
     paths? A run that persists without it will be refused by P3 — is that correct here, or a bug?`, { schema: REVIEW_SCHEMA })

phase('P2 fix')
const p2f = await agent(`${CONTEXT}

Fix the findings below on feat/d1-agent-invocation. BOUNDED: these findings only. Do not start P3.
A finding you believe is wrong goes in not_done with your reason — never silently skipped.

FINDINGS:
${JSON.stringify(p2r, null, 2)}

Re-run the gate command, report the verbatim final line, commit.`, { schema: IMPL_SCHEMA })

// ---------------------------------------------------------------------------
// P3 — the gate learns to refuse a tautology
// ---------------------------------------------------------------------------

phase('P3 gate')
const p3 = await agent(`${CONTEXT}

TASK — Phase P3 of the plan: THE GATE. Read the plan's P3 section; it is the contract.

P2 now writes agent_invoked onto the eval run. Make the deploy gate refuse an eval signal that lacks
it (app/services/deployment_service.py, and see BACKLOG 2.2).

SETTLED BY THE OWNER — implement this, do not re-litigate it: the gate refuses an ABSENT
agent_invoked as well as an explicit false. Every eval run persisted before this branch was produced
by the tautology and carries no such field, so a gate refusing only 'false' would keep shipping on
the whole of history — the exact shape of BACKLOG 3.1, where pre-P4 red-team runs still read
signal='measured' with clean findings. Accepted consequence: fail-closed on all pre-D1 runs.

Also needed:
  - The alembic_tenant migration for the new field. NOTE HONESTLY: no ALTER TABLE can execute on this
    machine (no Postgres). Do not write or imply that you ran it. Say plainly in not_done that it is
    unapplied and unobserved, and that BACKLOG 3.5 already carries three migrations in that state.
  - Gate tests: refuses false, refuses absent, accepts true. Mutate each, observe red, restore,
    observe green, record verbatim. These three tests close BACKLOG 2.2.

Commit when the gate suite is green.`, { schema: IMPL_SCHEMA })

phase('P3 review')
const p3r = await agent(`${CONTEXT}
${REVIEW_STANCE}

Review the P3 commit(s) on feat/d1-agent-invocation.

The implementer reported:
${JSON.stringify(p3, null, 2)}

The questions that matter most:
  1. Does the gate ACTUALLY fail closed on absent? Construct the absent case as it would arrive from
     a real pre-D1 persisted run — not as a hand-built dict that happens to have the key missing.
     A dict literal in a test can miss the shape the database actually returns.
  2. Is there any other route to a deploy approval that skips this check entirely? BACKLOG 5.1 says
     POST /approve-deployment gates on the frozen run.recommendation and never consults live findings
     — check whether the same bypass exists for this signal.
  3. Does the migration match the field name and type P2 actually writes? They were written by
     different agents; a mismatch here is invisible until a live database exists, which is nowhere.
  4. Do the three gate tests fail for the RIGHT reason when mutated, or for an incidental one?`, { schema: REVIEW_SCHEMA })

phase('P3 fix')
const p3f = await agent(`${CONTEXT}

Fix the findings below on feat/d1-agent-invocation. BOUNDED: these findings only.
A finding you believe is wrong goes in not_done with your reason.

FINDINGS:
${JSON.stringify(p3r, null, 2)}

Then run the gate command one final time for the whole branch and report the verbatim final line.
Commit.`, { schema: IMPL_SCHEMA })

// ---------------------------------------------------------------------------
// Collector — assembles the bounded artifact for the tier-2 judge
// ---------------------------------------------------------------------------

phase('Collect')
const artifact = await agent(`${CONTEXT}

You are the COLLECTOR. You write no code. You assemble the bounded artifact the tier-2 judge will
read, because the judge never explores the repository — it reads only what you hand it.

Produce:
  1. The full diff of feat/d1-agent-invocation against main. If it exceeds ~1500 lines, include every
     hunk of app/ and alembic_tenant/ in full and summarise only test files, saying which you
     summarised and how many lines you dropped. NEVER silently truncate.
  2. The verbatim final pytest line from the last gate run, and how it compares to the 1675/11/0
     baseline at af0f601.
  3. Every implementer claim and every mutation proof, verbatim, from all EIGHT implementation
     reports (P1, P1b, P2, P3, and each of their bounded fixes).
  3b. THE MONEY QUESTION, answered from the diff and stated plainly for the judge: can any eval path
     reach a live ProviderAdapter, the real mailer, or the tenant's metrics tables? Quote the hunks
     that decide it. This is the single highest-consequence claim on the branch.
  4. Every tier-1 finding, and whether the diff shows it was fixed, partially fixed, or not fixed.
     Determine this from the DIFF, not from the fixer's say-so.
  5. What the branch does NOT prove: list every gate that skipped, every migration unapplied, every
     path unexercised because no Postgres and no live agent exist here.

Write it to ${REPO}/.dev/reference/d1-tier2-artifact.md and return it as your result.`, { schema: {
  type: 'object',
  properties: {
    diff: { type: 'string' },
    suite_line: { type: 'string' },
    baseline_comparison: { type: 'string' },
    implementer_claims: { type: 'string' },
    mutation_proofs: { type: 'string' },
    tier1_findings_status: { type: 'string' },
    unproven: { type: 'string' },
    summarised_or_dropped: { type: 'string' },
  },
  required: ['diff', 'suite_line', 'baseline_comparison', 'implementer_claims', 'mutation_proofs', 'tier1_findings_status', 'unproven', 'summarised_or_dropped'],
} })

// ---------------------------------------------------------------------------
// Tier 2 — the judge. Fable, per CLAUDE.md. Bounded artifact only.
// ---------------------------------------------------------------------------

phase('Judge')
const verdict = await agent(`You are the TIER-2 JUDGE for milestone D1 of the W Chats project.

You judge a BOUNDED ARTIFACT ONLY. Do not explore the repository, do not read files, do not run
anything. Everything you may consider is below.

Your question is NOT "what is broken?" — a tier-1 adversarial reviewer already asked that against the
live code, three times. Your question is:

    DO THE CLAIMS MATCH THE EVIDENCE, AND WHAT IS ASSERTED BUT UNPROVEN?

Context you need: this branch exists to fix audit defect D1 — the RAG eval set
agent_response = reference_answer, so the label was the prediction and faithfulness approached 1.0
by construction. The project's standing principle is that a metric computed over zero valid
observations is 'unknown', never 'pass', and that a model-generated label may never gate a deploy.
The machine has no PostgreSQL, so every integration test skips and no migration has been applied.

The specific trap to watch for: this branch's own fix can reintroduce the defect it removes. A
measurement that cannot move, a guard demonstrated only inside the complement of its own blind spot,
a mutation proof whose red run failed for an incidental reason — each would leave the branch looking
measured while measuring nothing. That is what previous milestones here shipped.

Report everything. Do not filter to high severity.

=== THE ARTIFACT ===
${JSON.stringify(artifact, null, 2)}
`, { model: 'fable', schema: {
  type: 'object',
  properties: {
    verdict: { type: 'string', description: 'The honest one-paragraph read of what this branch actually delivers' },
    mergeable: { type: 'boolean' },
    claims_supported: { type: 'array', items: { type: 'string' } },
    claims_unproven: {
      type: 'array',
      description: 'Asserted but not established by the evidence given',
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
    d1_actually_dead: { type: 'string', description: 'Is the label-as-prediction defect genuinely gone, on every path the diff shows? Cite the hunk.' },
    must_fix_before_merge: { type: 'array', items: { type: 'string' } },
    new_backlog_items: { type: 'array', items: { type: 'string' }, description: 'Work this branch discovered or deferred, for .dev/BACKLOG.md' },
  },
  required: ['verdict', 'mergeable', 'claims_supported', 'claims_unproven', 'evidence_mismatches', 'd1_actually_dead', 'must_fix_before_merge', 'new_backlog_items'],
} })

log('D1 workflow complete — tier-2 verdict returned')

return {
  p1: { impl: p1, review: p1r, fix: p1f },
  p1b: { impl: p1b, review: p1br, fix: p1bf },
  p2: { impl: p2, review: p2r, fix: p2f },
  p3: { impl: p3, review: p3r, fix: p3f },
  verdict,
}
