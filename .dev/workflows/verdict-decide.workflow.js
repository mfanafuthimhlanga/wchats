export const meta = {
  name: 'verdict-decide',
  description:
    'Ticket 17 (#54): pure decide() over EvalResult, RedTeamResult and CalibrationStatus computes the Verdict; the checklist sequences both jobs; the model turn narrates and cannot change the outcome.',
  phases: [
    { title: 'S1 verdict.py', detail: 'Verdict, Reason, decide(), Wilson interval, rule_version=1 table tests', model: 'opus' },
    { title: 'S2 red-team reader', detail: 'RedTeamResult.from_payload + read_red_team_result', model: 'opus' },
    { title: 'S3 sequencer', detail: 'checklist dispatches both jobs, waits terminal bounded, stale-signal regression test', model: 'opus' },
    { title: 'S4 verdict wired', detail: 'decide() drives recommendation; prompt narrates; threshold text gone', model: 'opus' },
    { title: 'Tier-2 judge', detail: 'Fable judges the bounded artifact: do the claims match the evidence?', model: 'fable' },
  ],
}

// Shape follows .dev/workflows/eval-foundation.workflow.js (the reference implementation):
// strictly sequential, one agent at a time (4GB RAM box), each slice impl -> adversarial
// review -> bounded fix -> fix verification, then a tier-2 judge on a bounded artifact.
//
// Model discipline (wchats CLAUDE.md): implementation agents pass model 'opus' with small
// bounded briefs naming exact files and exact exit checks. Review, verification and the
// collector omit `model:` and inherit the session model. The tier-2 judge runs 'fable'.
//
// Reviewers are NEVER told to be conservative. Ask for everything; the orchestrator filters.

const BASE = 'b0c25e7' // main at branch cut, after #123 merged

const RULES = `
THE RULE TABLE (rule_version=1, decision #19 on the map plus its 2026-08-24 amendment; quote
these in code comments by rule slug, never re-derive them):

1. A Scenario passes when every measured dimension passes; an unmeasured dimension never
   counts as passed. Pass rate is scenarios_passed over scored. (Already enforced upstream by
   EvalResult; decide() reads the counts, computes no verdict per scenario.)
2. Golden gates hard. datasets['golden'].scenarios_failed > 0 -> block, rule 'golden_failure'.
   Golden dataset absent OR attempted < 10 -> block, rule 'golden_set_below_floor' (the
   amendment: "golden gates absolutely" is vacuously true at zero rows).
3. Exploratory gates on the Wilson 95% interval over (scenarios_passed, scored):
   lower bound >= 0.85 -> ship-eligible; upper bound < 0.70 -> block ('exploratory_ci_blocks');
   otherwise ship_with_warnings ('exploratory_ci_inconclusive'). Both marked provisional=true.
   An exploratory dataset with scored == 0 contributes no CI rule; the coverage floor is what
   catches an unscored run.
4. Eval coverage floor: run-level scored / attempted < 0.90 -> block, rule
   'eval_coverage_below_floor'. Fail closed: the first run's timeouts lived in the missing
   tail. attempted == 0 is below the floor by definition (never divide; test the guard).
5. Calibration refuses: calibration.calibrated is False -> block, rule 'judge_not_calibrated',
   observed = the status and its reason ('not_calibrated_yet' is an absence, never a pass).
6. Red team: any finding at critical -> block, 'critical_breach'. Any high -> block when
   block_on_high else ship_with_warnings, rule 'high_breach'. record.k < 3 OR
   incomplete_vectors non-empty -> block, 'red_team_coverage_incomplete' (every one of the
   seven vectors, three independent attempts each; today's dispatcher yields attempts=1, so
   today's Agent honestly blocks).
7. Absent records: eval_result None -> block 'absent_eval_measurement'; red_team None ->
   block 'absent_red_team_measurement'. Missing data is never passing data.
8. Fold: outcome is the worst across all reasons (block > ship_with_warnings > ship);
   no reasons -> ship. decide() never reads settings, the clock, or the DB. Pure.
`

const HOUSE_RULES = `
House rules (non-negotiable):
- Work on branch feat/verdict-decide. Commit per logical change, stage by path, NEVER git add -A.
- Commit style type(scope): message. End the message with exactly this trailer line:
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- PowerShell breaks on multi-line -m: write the message to a temp file, git commit -F <file>.
- Python runs via apps/api/.venv/Scripts/python.exe. pytest via that interpreter: -m pytest.
- No em-dashes or en-dashes anywhere, raw or as entities. Active voice. Comments say WHY.
- A test for every behaviour change. For any guard or refusal you add: mutate the guard,
  OBSERVE red, restore from HEAD, OBSERVE green, and record the observed counts in
  guard_demonstrations. An unobserved negative test is indistinguishable from a tautology.
- app/domain modules import ONLY the standard library, third-party packages and domain
  siblings. Never app.core, app.services, app.models. import-linter enforces this.
- Measurement honesty: zero observations is unknown, never pass. A default number is
  indistinguishable from a measurement one reader later.
`

const IMPL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['committed', 'commit_ref', 'summary', 'files', 'gates_green', 'gate_output_tail', 'guard_demonstrations', 'notes_for_next_slices'],
  properties: {
    committed: { type: 'boolean' },
    commit_ref: { type: 'string', description: 'short SHA of the slice commit, empty if not committed' },
    summary: { type: 'string', description: '<=12 lines: what was built, key decisions, deviations from the brief' },
    files: { type: 'array', items: { type: 'string' } },
    gates_green: { type: 'boolean', description: 'the named exit check passed with zero failures' },
    gate_output_tail: { type: 'string', description: 'last ~15 lines of the exit check output, ALWAYS filled, green or red, with observed counts' },
    guard_demonstrations: {
      type: 'array', items: { type: 'string' },
      description: 'per guard added: what was mutated, observed red (quoted), restored from HEAD, observed green (quoted). Omit any not actually run.',
    },
    notes_for_next_slices: { type: 'array', items: { type: 'string' }, description: 'exported names, signatures, shape decisions, deviations' },
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
        type: 'object', additionalProperties: false,
        required: ['file', 'issue', 'failure_scenario'],
        properties: {
          file: { type: 'string' },
          issue: { type: 'string' },
          failure_scenario: { type: 'string', description: 'concrete inputs/state -> wrong behavior' },
        },
      },
    },
    concerns: { type: 'array', items: { type: 'string' }, description: 'everything else: style, suspicions, latent risks, questions. Non-blocking; forwarded onward.' },
  },
}

const REVIEW_BRIEF = `
You are the tier-1 adversarial reviewer for one slice on branch feat/verdict-decide
(repo C:/Users/Bantu/mzansi-agentive/wchats, backend in apps/api). Read the implementer's
claims below, then read the actual diff of their commit (git show <sha>) and the files it
touches. Report EVERYTHING you find, at every severity; the orchestrator filters. Never
soften, never limit yourself to high severity.

Standing detectors this repo has paid for (.dev/failure-modes.jsonl):
- FM-016: for every value a check or gate PRINTS, find the assertion that READS it; a
  printed-but-unasserted value is a hole. Break each printed value in turn and ask which
  test goes red.
- FM-013: for every gate, ask what it outputs when it does NOT run, and whether that is
  distinguishable from a pass.
- FM-004: for any ordering, dedup or precedence test, ask what the fixture arrangement
  returns with the logic removed. A vacuous arrangement approves anything.
- FM-006: compare the commit range you reviewed against the branch head; work added after
  your read is unreviewed work.
- Rule-table fidelity: compare every threshold, slug and comparison in the code against the
  rule table verbatim below. An inverted comparison or an off-by-one at a boundary
  (>= vs >) is exactly the class of defect this slice exists to prevent.
${RULES}
`

// ---------------------------------------------------------------------------
// Slice briefs
// ---------------------------------------------------------------------------

const SLICES = [
  {
    id: 'S1',
    phase: 'S1 verdict.py',
    brief: `
Implement slice S1 of ticket 17 (#54) on branch feat/verdict-decide in
C:/Users/Bantu/mzansi-agentive/wchats/apps/api.

BUILD: app/domain/verdict.py (new) and tests/unit/test_verdict_type.py (new). Nothing else.

The interface, pinned (derive nothing else the caller must learn):
  class Outcome(StrEnum): SHIP='ship'; SHIP_WITH_WARNINGS='ship_with_warnings'; BLOCK='block'
  @dataclass(frozen=True) class Reason:
      rule: str; signal: str; observed: str; threshold: str; outcome: Outcome
      provisional: bool = False
      # payload() -> dict, and a module-level reason_from_payload or Reason.from_payload
  @dataclass(frozen=True) class Verdict:
      outcome: Outcome; reasons: tuple[Reason, ...]; rule_version: int = RULE_VERSION
      # payload() -> dict and from_payload(), following EvalResult's conventions exactly:
      # frozen, refuse unknown/missing keys, never default a number.
  def wilson_interval(successes: int, trials: int) -> tuple[float, float]
      # 95% two-sided, z=1.96, closed form, stdlib math only. Refuse trials<=0 and
      # successes outside [0, trials] by raising, never by clamping.
  def decide(eval_result, red_team_result, calibration, *, block_on_high: bool = True) -> Verdict
      # eval_result: EvalResult | None; red_team_result: RedTeamResult | None;
      # calibration: CalibrationStatus. block_on_high crosses the seam as a parameter
      # because app.domain must not import app.core.config.

Structure decide() as a flat list of small pure rule functions, each returning
Reason | None (or a tuple of Reasons), folded at the end; one rule function per row of the
rule table. Every Reason names its signal, its observed value and its threshold in words a
non-technical owner can read (#54 criterion 4). Thresholds are module constants beside
RULE_VERSION = 1; they live here and nowhere else.

Read first: app/domain/eval_result.py (DatasetOutcome counts, datasets mapping keys
'golden'/'exploratory'), app/domain/red_team_result.py (k, incomplete_vectors, findings,
Severity), app/domain/calibration_status.py (calibrated property, status, reason). Follow
these files' docstring and validation conventions; they are the precedent.
${RULES}
TESTS: table-test every rule edge (#54 criterion 1), each as its own test with real domain
records built through the public constructors, never mocks: golden failure; golden absent;
golden at 9 and at 10 pairs (boundary observed both sides); exploratory CI lower exactly at
0.85; CI straddling both thresholds; CI upper just under 0.70; exploratory scored==0;
coverage at 0.89 and 0.90; attempted==0; uncalibrated (absent and measured-not-calibrated);
critical breach; high breach with block_on_high True and False; k=1 incomplete; both records
None; the all-clear ship. Plus wilson_interval against two hand-computed fixtures (state the
arithmetic in the test comment) and payload/from_payload round trips including refusal of a
missing key.

EXIT CHECK (run and quote the tail): .venv/Scripts/python.exe -m pytest tests/unit/test_verdict_type.py -q
Then: .venv/Scripts/python.exe scripts/gates.py static
Commit on green: feat(domain): Verdict and decide(), the computed deployment decision (#54)
${HOUSE_RULES}`,
  },
  {
    id: 'S2',
    phase: 'S2 red-team reader',
    brief: `
Implement slice S2 of ticket 17 (#54) on branch feat/verdict-decide in
C:/Users/Bantu/mzansi-agentive/wchats/apps/api.

BUILD: RedTeamResult.from_payload on app/domain/red_team_result.py, and
read_red_team_result(run_id, conn_str) in app/services/red_team_service.py. Tests in
tests/unit/test_red_team_result_type.py and tests/unit/test_red_team_service.py. Nothing else.

from_payload mirrors EvalResult.from_payload exactly (read app/domain/eval_result.py for
the convention): every key required, refusal (InvalidRedTeamResult) on a missing key, a
wrong type, or any construction rule; never default a count. It reads what
RedTeamResult.payload writes; round-trip payload -> from_payload -> payload must be
identity, and the test observes it on a record with findings and on one without.

read_red_team_result mirrors eval_service.read_eval_result (line ~2615) exactly: SELECT
result FROM red_team_runs WHERE id = %s against the tenant conn_str; None for a missing
row, a NULL column, a pre-0021 tenant (UndefinedColumn, rolled back and logged), and a
payload from_payload refuses (logged at error). None means unmeasured, never a pass.

EXIT CHECK (run and quote the tail):
.venv/Scripts/python.exe -m pytest tests/unit/test_red_team_result_type.py tests/unit/test_red_team_service.py -q
Commit on green: feat(redteam): the stored result reads back as the frozen record (#54)
${HOUSE_RULES}`,
  },
  {
    id: 'S3',
    phase: 'S3 sequencer',
    brief: `
Implement slice S3 of ticket 17 (#54) on branch feat/verdict-decide in
C:/Users/Bantu/mzansi-agentive/wchats/apps/api.

THE CHECKLIST BECOMES THE SEQUENCER (decision #19 rule 5). Today
app/worker/tasks/runtime/deployment.py dispatches an eval best-effort on two signal states
and never dispatches a red team; it then reads whatever summaries exist, so the first
checklist ever run read eval_signal=no_runs seconds after starting the eval it was asking
about. That stale shape is the breakage this slice closes by construction.

BUILD, in app/worker/tasks/runtime/deployment.py plus a small pure helper where it fits
(deployment_service.py is acceptable), with settings in app/core/config.py:

1. run_deployment_checklist dispatches BOTH jobs up front: the existing
   generate_eval_suite -> run_eval_suite chain, and run_red_team (import inside the
   function, agent_id only, both existing idempotency guards absorb a run already in
   flight). Record the dispatch wall-clock time.
2. Wait for both to a terminal state ('complete' or 'failed') with a bounded ceiling:
   new settings CHECKLIST_WAIT_CEILING_S (default 1500) and CHECKLIST_WAIT_POLL_S
   (default 10). Poll the tenant DB: the newest eval_runs row for this agent (reuse the
   _latest_run query family in deployment_service.py, config key prefix m6:<agent_id>) and
   the newest red_team_runs row, in each case only rows created at or after the dispatch
   time, so a stale terminal run from last night never satisfies the wait. Design the wait
   as a small function taking fetch-status callables, a poll interval, a ceiling and an
   injected sleep/clock, so the tests drive it without real time.
3. On the ceiling expiring: proceed with whatever is terminal; a job that never reached
   terminal reads as an absent record, and decide() in S4 blocks with the typed absent
   reason. Never raise; never read the pre-dispatch summary. Log which half timed out with
   the observed wait.
4. The signal collectors run AFTER the wait, never before, so every summary and record the
   rest of the task reads describes the runs the checklist itself sequenced.
5. The existing conditional dispatch block (eval_dispatched on no_runs and the
   agent_not_invoked half) folds into the unconditional dispatch; keep the
   eval_dispatched key on the summary so the owner-facing warning still says the platform
   started the measurement.

REGRESSION TEST (the first run's failure shape, #54 criterion 3), in
tests/unit/test_deployment_task.py: an agent with no prior runs; the checklist dispatches;
the fetch-status callables report running until the injected clock advances; the collectors
must not be called before both report terminal, and the collected summary must be the
post-terminal one. A second test: ceiling expires with the red team still running; the task
completes, the red-team record reads absent, and the log names the timeout. Drive the wait
helper directly for its own edges: both already terminal at first poll; one terminal one
failed; ceiling of zero.

Respect acks_late (#85 family): the 60-minute idempotency guard is what absorbs a
redelivery mid-wait; do not add a second guard, and say so in a comment where the wait
starts. CTL-08 holds: agent_id only in task args, conn_str decrypted in the task.

EXIT CHECK (run and quote the tail):
.venv/Scripts/python.exe -m pytest tests/unit/test_deployment_task.py -q
Commit on green: feat(deploy): the checklist sequences both jobs and waits terminal (#54)
${HOUSE_RULES}`,
  },
  {
    id: 'S4',
    phase: 'S4 verdict wired',
    brief: `
Implement slice S4 of ticket 17 (#54) on branch feat/verdict-decide in
C:/Users/Bantu/mzansi-agentive/wchats/apps/api. Read the S1-S3 notes below; they landed.

decide() DRIVES THE RECOMMENDATION; THE MODEL NARRATES (issues #36 and #54).

BUILD, in app/worker/tasks/runtime/deployment.py and app/services/deployment_service.py,
tests in tests/unit/test_deployment_task.py and tests/unit/test_deployment_service.py:

1. After the wait (S3) and the collectors, the task reads the three records:
   read_eval_result(run_id, conn_str) off the awaited eval run's id;
   read_red_team_result off the awaited red team run's id; load_calibration_status
   (settings.CALIBRATION_ARTIFACT_PATH, record.judge_identity as _calibration_block does).
   Then: verdict = decide(eval_record, red_team_record, calibration,
   block_on_high=settings.DEP_BLOCK_ON_HIGH_RED_TEAM).
2. verdict.outcome IS the recommendation. It is persisted on the checklist run, and every
   Reason lands as a DeploymentWarning (warning_id = the rule slug, category by signal,
   severity 'warning', message rendered from signal + observed + threshold) so 'block'
   never arrives unexplained. verdict.payload() is stored on the report under 'verdict'.
3. The orchestrator's turn becomes narration: signals_json now also carries the rendered
   verdict. Rewrite _DEPLOYMENT_SYSTEM_PROMPT: every blocking, warning and ship threshold
   sentence GOES (#54 criterion 2: the prompt's threshold text is gone); the model is told
   the platform computed the verdict, given the reasons, and asked for the 2-3 sentence
   owner summary and narrative warnings only. submit_report's input_schema loses
   'recommendation'; the tool handler and DeploymentReport construction stop reading it
   (DeploymentReport.recommendation is filled from verdict.outcome).
4. apply_signal_evidence_gate STAYS, unchanged in direction, as the one-way floor under
   the summaries: it runs on verdict.outcome, and when it downgrades below what decide()
   said, log run_deployment_checklist.evidence_gate_disagrees loudly; the two disagreeing
   is a defect signal, never a silent resolution. It can never upgrade.
5. An orchestrator failure or timeout no longer fails the checklist: the verdict exists
   without the model. Persist status='complete' with recommendation=verdict.outcome and a
   fixed fallback summary naming that the narration was unavailable. status='failed' is
   reserved for the task's own exceptions before the verdict exists.
6. Port the two prompt-era warn conditions that decide() cannot see (verified_qa row_count
   under 50; medium findings over 2) into a deterministic derive_quality_warnings() beside
   derive_blast_radius_warnings, merged on the same de-dup terms. They never change the
   outcome.

TESTS: verdict-to-warning mapping renders slug, observed and threshold; recommendation is
verdict.outcome even when the scripted model submits a contradictory report; submit_report
schema has no recommendation key; the prompt contains no threshold number (assert the
literal strings '0.85', '0.70', '>= 50' are absent from _DEPLOYMENT_SYSTEM_PROMPT);
orchestrator timeout still persists complete-with-verdict; gate disagreement logs and
takes the more conservative outcome. Update every existing test the prompt rewrite and
schema change break, preserving each test's original intent rather than weakening it.

EXIT CHECK (run and quote the tail):
.venv/Scripts/python.exe -m pytest tests/unit/test_deployment_task.py tests/unit/test_deployment_service.py tests/unit/test_deployment_routes.py -q
Then the whole suite: .venv/Scripts/python.exe scripts/gates.py fast
Commit on green: feat(deploy): decide() computes the verdict; the model turn narrates (#54, closes #36)
${HOUSE_RULES}`,
  },
]

// ---------------------------------------------------------------------------
// Run: impl -> review -> fix -> verify, strictly sequential
// ---------------------------------------------------------------------------

const results = []
let notes = []

for (const slice of SLICES) {
  phase(slice.phase)
  log(`${slice.id}: implementing`)
  const impl = await agent(
    slice.brief + (notes.length ? `\n\nNOTES FROM EARLIER SLICES:\n- ${notes.join('\n- ')}` : ''),
    { label: `${slice.id}:impl`, phase: slice.phase, schema: IMPL_SCHEMA, model: 'opus' },
  )
  if (!impl) throw new Error(`${slice.id} implementer returned nothing`)
  notes = notes.concat(impl.notes_for_next_slices || [])

  log(`${slice.id}: adversarial review of ${impl.commit_ref}`)
  const review = await agent(
    `${REVIEW_BRIEF}\n\nTHE IMPLEMENTER'S CLAIMS (verify, never trust):\n${JSON.stringify(impl, null, 2)}\n\nReview commit ${impl.commit_ref} on feat/verdict-decide. Also confirm the branch head equals the commit you read (FM-006).`,
    { label: `${slice.id}:review`, phase: slice.phase, schema: REVIEW_SCHEMA },
  )
  // FM-013: a review agent that died would otherwise be indistinguishable from an
  // approval. A null review is an unreviewed slice, and the run must say so loudly.
  if (!review) throw new Error(`${slice.id} review agent returned nothing; the slice is UNREVIEWED`)
  let fixed = null
  if (review.verdict === 'fix_required' && review.blockers.length) {
    log(`${slice.id}: ${review.blockers.length} blocker(s), fixing`)
    fixed = await agent(
      `Fix these reviewed blockers on branch feat/verdict-decide (repo
C:/Users/Bantu/mzansi-agentive/wchats, backend apps/api), touching only the slice's own
files. Each fix gets a test that observes the failure the reviewer named, red before the
fix where feasible. Re-run the slice's exit check and quote the tail.

BLOCKERS:\n${JSON.stringify(review.blockers, null, 2)}

CONTEXT (the slice's brief):\n${slice.brief}`,
      { label: `${slice.id}:fix`, phase: slice.phase, schema: IMPL_SCHEMA, model: 'opus' },
    )
    const verify = await agent(
      `${REVIEW_BRIEF}\n\nA fixer addressed these blockers:\n${JSON.stringify(review.blockers, null, 2)}\n\nTheir claims:\n${JSON.stringify(fixed, null, 2)}\n\nVerify each blocker is actually closed in commit ${fixed ? fixed.commit_ref : '(none)'} on feat/verdict-decide; reread the code, rerun nothing you cannot, and report any blocker still open plus anything the fix broke.`,
      { label: `${slice.id}:verify-fix`, phase: slice.phase, schema: REVIEW_SCHEMA },
    )
    // Same FM-013 hole as the review null: a dead verifier must not read as resolved.
    if (!verify) throw new Error(`${slice.id} fix-verify agent returned nothing; the fix is UNVERIFIED`)
    if (verify.verdict === 'fix_required') {
      log(`${slice.id}: blockers still open after fix; surfacing to orchestrator`)
      results.push({ slice: slice.id, impl, review, fixed, verify, unresolved: true })
      continue
    }
    results.push({ slice: slice.id, impl, review, fixed, verify, unresolved: false })
  } else {
    results.push({ slice: slice.id, impl, review, fixed: null, verify: null, unresolved: false })
  }
}

// ---------------------------------------------------------------------------
// Tier-2: session-model collector assembles the bounded artifact; fable judges it
// ---------------------------------------------------------------------------

phase('Tier-2 judge')

const ARTIFACT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['stat', 'diff', 'omitted', 'test_inventory', 'gate_tails'],
  properties: {
    stat: { type: 'string', description: `git diff --stat ${BASE}...HEAD, verbatim` },
    diff: { type: 'string', description: 'the unified diff of the source changes, verbatim; drop lockfiles and generated files first' },
    omitted: { type: 'array', items: { type: 'string' }, description: 'anything dropped from the diff and why' },
    test_inventory: { type: 'string', description: 'every test file touched, with test names added or changed' },
    gate_tails: { type: 'string', description: 'the observed exit-check tails from the slice claims, verbatim' },
  },
}

log('collecting the bounded artifact')
const artifact = await agent(
  `Assemble the tier-2 artifact for branch feat/verdict-decide in
C:/Users/Bantu/mzansi-agentive/wchats. Run git diff --stat ${BASE}...HEAD and
git diff ${BASE}...HEAD in the repo root, inventory the tests, and carry over these
observed gate tails from the slice runs:\n${JSON.stringify(results.map(r => ({ slice: r.slice, impl_tail: r.impl?.gate_output_tail, fix_tail: r.fixed?.gate_output_tail })), null, 2)}\n\nReturn the artifact verbatim; judge nothing yourself.`,
  { label: 'tier2:collector', phase: 'Tier-2 judge', schema: ARTIFACT_SCHEMA },
)

const JUDGE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['verdict', 'claims_vs_evidence', 'asserted_but_unproven', 'must_fix_before_merge'],
  properties: {
    verdict: { type: 'string', enum: ['merge', 'merge_after', 'do_not_merge'] },
    claims_vs_evidence: { type: 'array', items: { type: 'string' }, description: 'each implementer claim, and whether the diff and gate tails actually evidence it' },
    asserted_but_unproven: { type: 'array', items: { type: 'string' } },
    must_fix_before_merge: { type: 'array', items: { type: 'string' } },
  },
}

log('tier-2 judge (fable) reading the bounded artifact')
const judgement = await agent(
  `You are the tier-2 judge for ticket 17 (#54) of the W Chats repo. You read a BOUNDED
ARTIFACT ONLY; never explore the tree, never run commands. Tier 1 already asked "what is
broken?" against the code. Your question is: DO THE CLAIMS MATCH THE EVIDENCE, AND WHAT IS
ASSERTED BUT UNPROVEN?

The ticket's acceptance criteria:
1. decide is pure and table-tested across every rule edge.
2. The thresholds live in the versioned rule, not in any prompt; the prompt's threshold text is gone.
3. The checklist waits for both jobs and never reads a stale signal; the first run's failure shape is a regression test.
4. Every block carries reasons naming signal, observed value and threshold.
(5, ADR 0007, is written after this judgement, in-session.)
${RULES}
THE SLICE CLAIMS AND REVIEWS:\n${JSON.stringify(results, null, 2)}

THE ARTIFACT:\n${JSON.stringify(artifact, null, 2)}`,
  { label: 'tier2:judge', phase: 'Tier-2 judge', schema: JUDGE_SCHEMA, model: 'fable', effort: 'high' },
)

return { results, judgement }
