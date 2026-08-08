# Tier-2 judgement — D1 (feat/d1-agent-invocation)

**Judge:** Fable 5, reading a bounded artifact only (the diff, the eight implementer reports, the
tier-1 findings) assembled by a session-model collector. It did not explore the repository.
**Date:** 2026-08-08. **Run:** wf_05023b63-a3a. **Artifact:** .dev/reference/d1-tier2-artifact.md

**This is the FIRST tier-2 read of this branch.** The P1/P1b/P2/P3 reviewers were tier-1 —
session-model agents that investigated the codebase and asked "what is broken?". Several commit
messages and BACKLOG rows on this branch called them tier-2; they were not. Tier-2 is this file.

**Verdict: mergeable = True**

---

## The verdict, verbatim

> This branch genuinely removes D1 at the code level and surrounds the removal with layered, mostly-falsified guards: the scenario dict no longer carries an agent_response key at all, the stored contexts column is renamed out of the scorer's vocabulary, the scored row is built only from the agent's own turn, the second orchestrator refuses tautologies before billing a judge call, and the deploy pipeline refuses non-invoked evidence at three layers (collector signal, gate, and — after the high finding — the approve route itself). The evidence trail is unusually honest: four vacuous guards were caught by adversarial review rather than shipped, two proofs that failed to go red were recorded rather than quietly fixed, and three overclaims were corrected in writing. But everything is proven at unit level against doubles: no eval run has ever executed end to end, the one SQL write the whole gate depends on has never touched a database, no recorded-mode turn has faced a real SDK or ProviderAdapter, the metric has never been observed to move off ~1.0, 44 of 122 claimed mutation proofs have no surviving record (including all of P1, the phase the plan calls "the whole bet"), 17 of 48 tier-1 findings are lost and unadjudicatable, the migration the plan required was argued away without the owner settling it, and the branch creates two unmeasured consequences (unbounded within-turn Actor spend; production-row egress to the judge API with the PII firewall off) that it names only obliquely or not at all. What it delivers is a correctly-shaped, fail-closed measurement pipeline that has never measured anything — which is a large improvement over a pipeline that confidently measured its own label, and is honestly labelled as such.

---

## Is D1 actually dead?

Yes, at the level of every path the diff shows — with the explicit caveat that this is a static, unit-proven death, not an observed one. The defect had one origin and two doors, and all three are closed in the quoted hunks. Origin: the scenario-fetch dict in run_eval_suite (section A, @@ -367,12 +834,29) deletes `"agent_response": row[3]` outright — a scenario row now has no response key at all rather than a plausible placeholder — and renames row[4] to `stored_retrieved_contexts`, a name run_ragas_eval does not read, so reconnecting label to prediction is a deliberate edit, not a drift. The scored row (section A, _invoke_agent_for_scenarios) sets `"agent_response": response_text` from the agent's own turn and `"retrieved_contexts": contexts` from the agent's retrieve calls with no fallback to the stored column; the fallback mutation that originally survived all 163 tests now goes red (P2-fix, stored-context-fallback: 1 failed, verbatim). Door one: run_eval_suite calls run_ragas_eval only when invocation status == MEASURED, otherwise writes no scores and bills no judge (section A scoring-block hunk). Door two: run_eval_for_agent — the orchestrator P2's guards did not cover — raises ValueError on any row whose agent_response is empty or equals its reference_answer (section F hunk; mutation RED 3 failed, verbatim). Downstream, evidence produced the old way is refused at the collector (EVAL_SIGNAL_AGENT_NOT_INVOKED, absent refused as false), the gate, and the approve route's new 422 (section E; the high finding's mutation went red 5/2 with sha256-verified restore). The trap the task names — the fix reintroducing the defect — was in fact attempted three times on this branch (the fallback token, the [:1800] prose-satisfied guard, the at-cap fixture that separated nothing) and каждый was caught by mutation, which is the strongest evidence here. The deleted pin test_the_task_still_scores_the_reference_answer_against_itself has its inverse replacement (test_a_scored_row_never_carries_the_reference_answer_as_its_response). What remains open is not a path but an observation: no end-to-end run has shown the metric move off ~1.0, so 'the label is no longer the prediction' is proven while 'the eval now measures the agent' is not yet demonstrated. One quoted hunk carries a non-English word above by accident of drafting; disregard — the finding stands as written: every claim-deciding hunk quoted in sections A, E and F closes D1, and nothing in the structural map suggests an unquoted hunk reopens it. If any verdict-deciding hunk exists outside the quotes, the artifact directs to .dev/reference/d1-tier2-artifact.md §1.2 rather than inference.

---

## Must fix before merge (3)

1. The owner must explicitly settle the missing alembic_tenant migration: the plan and the workflow prompt both required it, none exists (0015 remains head), and the implementers' one-home-in-config-JSONB argument — however coherent — is a deviation from a written contract that only the owner can accept. A one-line signed-off decision in BACKLOG or HANDOFF suffices; silence does not.

2. Re-frame BACKLOG 2.11 before merge enables production evals: the missing PII firewall on the eval path is currently recorded only as a scoring-fidelity question, but the same fact means up to sixty nightly turns run against the tenant's production conn_str, lookup_structured returns SELECT * customer rows into the transcript, and that transcript goes to the Ragas judge API with pii_firewall_applied=False. The egress reading is addressed nowhere on the branch and must at least be a named, owner-visible decision before the first real run.

3. Extract this tier-2 judgement to .dev/reference/ before the session ends (CLAUDE.md requirement), and extend the same rule to tier-1 outputs — this branch lost 17 of 48 findings to a temp-directory journal, which is exactly the failure the rule exists to prevent.

---

## Claims the evidence supports (13)

- D1's line is dead at its origin: the scenario dict sets no agent_response key and renames the stored column to stored_retrieved_contexts (section A hunk), and re-adding "agent_response": row[3] was observed red (P2 M2, verbatim: 1 failed, 22 passed).
- The scored row is built only from the agent's own turn: agent_response=response_text and retrieved_contexts=contexts with no fallback; both substitution mutations (M3 reference_answer, M4 stored contexts) observed red verbatim, and the one-token fallback `contexts or scenario['stored_retrieved_contexts']` — which originally passed all 163 tests — now goes red after the P2-fix (stored-context-fallback: 1 failed).
- run_eval_for_agent, the second door no P2 guard covered, now raises ValueError on any row whose prediction equals or blanks its label (section F hunk; second-orchestrator-accepts-the-tautology mutation: RED 3 failed, verbatim).
- A below-floor run writes no eval_results and bills no judge call; run_ragas_eval executes only when invocation status == MEASURED (section A scoring-block hunk; below-floor-run-scores-anyway mutation observed red).
- The gate refuses non-invoked evidence fail-closed: _agent_invoked_from_run_config maps absent/non-bool to None, both None and False are refused, RUN_FAILED is checked ahead as an allow-list of one, and score suppression is one falsifiable property proven by exhaustive three-way mutation (structural-only green, state-only green, both red — sha256-verified restores).
- The approve route (the artifact POST /approve-deployment actually reads) now 422s on stored runs without a recorded invocation — the high finding's fix, mutation-proved 5 failed / 2 passed with the correct 2 surviving, sha256 identical both sides.
- At the diff level, no eval path reaches a live ProviderAdapter, the real mailer, or retrieval/turn metrics tables: all four adapter doors (step 5.5, replay, require_human, confirm_action) return _not_executed_result when recorded, notify_fn binds the mailer only on the live arm, and _run_sdk_turn opens no DB connection (collector-verified: conn_str appears in its signature and nowhere in its body).
- Sticky-mode leakage is closed at three sites (reset first in build_agent_options, publish last in build_tool_server, per-iteration reset plus finally in the eval loop), with the safe default 'live', and the relevant P1b/P2 mutations (M18, M19, M9-reset) observed red verbatim.
- The suite endpoints were independently observed: 1675/11/0 at af0f601 and 1873/11/0 at a021118 (collector's own run, exit 0); skip count unmoved, fail count unmoved, delta arithmetic consistent with the ladder.
- Retry no longer re-buys the invocation: a failure after agent_was_invoked returns without retry (hunk quoted; retry-re-buys-the-invocation mutation observed red).
- The convergence dispatch fires only for the absent (None) half of agent_invoked, and both directions are mutation-proved (no-dispatch-for-the-historical-population and dispatch-fires-for-an-explicit-false-too, both red verbatim).
- The chat path was unchanged by P1 specifically: the suite re-run with the new test file ignored reproduced 1675/11 twice — the only phase where 'the delta is exactly the added tests' was proven structurally rather than arithmetically.
- Recorded rows cannot enter the Actor's supervised set: RECORDED_NOT_EXECUTED is first in _ERROR_DISPOSITIONS and classified None (hunk quoted).

---

## Claims asserted but NOT established (10)

1. **The measurement can now move — that faithfulness falls from ~1.0 to a true value when scored against real agent turns. This is the branch's entire purpose and no run has demonstrated it.**

   *What would prove it:* One end-to-end eval run against a real tenant DB (blocked on BACKLOG 0.2 / a local PostgreSQL), observing scores that differ from the pre-D1 near-1.0 figures.

2. **update_eval_run_config's JSONB patch — the single write that turns agent_invoked from a default into an observation, and the exact thing the gate reads — works against a real database. It has only ever run against a cursor double, as has `status <> 'running'` and the pre-0013 UndefinedColumn fallback.**

   *What would prove it:* Executing the COALESCE(config,'{}')||patch SQL and both selectors against a real PostgreSQL with a real eval_runs row, including a pre-0013 schema.

3. **P1's and P1-fix's '12 mutations, each observed red and then green' (24 total) and P1b-fix's 18 — 44 of 122 claimed mutation proofs. No verbatim record survives for any of them, and P1's own review then showed 7 of 8 drift probes left all 12 P1 guards green, so the unrecorded proofs demonstrated guards later shown inadequate.**

   *What would prove it:* Re-running those mutations against the final tree with recorded output (the P3-fix sha256 protocol), or formally writing the debt off in BACKLOG as was done for 3.9.

4. **Recorded mode holds against a real SDK and a real ProviderAdapter, and the agent's post-'NOT EXECUTED' behaviour is unchanged by the removal of the evaluation-frame disclosure. The P1b-fix trace itself calls the fidelity argument 'a reasoned one, not a measured one'.**

   *What would prove it:* One recorded-mode turn through the real claude-agent-sdk against a sandbox tenant, observing zero provider calls and reading the transcript.

5. **The retrieve-context decode handles real SDK ToolResultBlocks; it has only seen the payload retrieve_tool constructs plus three hand-written shapes.**

   *What would prove it:* A live SDK turn whose retrieve result round-trips through _tool_result_text/_retrieved_chunk_texts with source='chunks'.

6. **The eight intermediate suite figures in the per-commit ladder. Only the endpoints and 5011f97 were independently observed; 65eab9e's 1821/11 is explicitly corroborated only by a def test_ count, and no phase after P1 re-ran the ignored-new-files control, so a pre-existing test silently changing status behind an equal number of new passes would be invisible.**

   *What would prove it:* Checkouts and re-runs at the intermediate commits, or accepting endpoint consistency as sufficient (which the collector correctly declines to call proof of any individual row).

7. **ruff and mypy cleanliness. ruff is not installed in apps/api/.venv; every ruff claim depends on a network uvx fetch leaving no artifact, and neither was re-run by the collector.**

   *What would prove it:* Installing ruff into the venv (or pinning the uvx version) and re-running both on the final tree with recorded output.

8. **The modified integration test (test_prompt_versions_e2e.py, +48/-20 for the new three-tuple signature) is correct. It has never executed — all 11 skips are -m integration, and per CLAUDE.md a skip is unobserved, never a pass.**

   *What would prove it:* A local PostgreSQL (BACKLOG 0.2) and one observed integration run.

9. **The owner ever sees the new refusal: the warning text is the only owner-visible account, apps/admin greps empty for agent_invoked/eval_signal, no frontend gate ran on this branch, and the accepted fail-closed cost for pre-D1 runs and pre-0013 tenants (including its recurrence, BACKLOG 2.18) has never been observed against a real tenant.**

   *What would prove it:* A real readiness check against a pre-D1 tenant, plus an admin-console surface (or at minimum a verified render path) for the warning.

10. **Every mutation red was caused by its mutation: all proofs used hand-picked node ids, the branch's own trace records that hand-picked subsets in the wrong file order produce 74 spurious failures from the fake-@tool import artifact, and no proof states its selector's import order (-p no:randomly, used only by P2-fix/P3-fix, fixes ordering within a selector, not which files import first).**

   *What would prove it:* Recording the selector and import order per proof, or re-running proofs under full-suite collection.

---

## Evidence mismatches (10)

1. **Claimed:** P1 (ec5f445) and P1-fix (d15be3a): '12 mutations, each observed red and then green.'

   **Evidence says:** Zero verbatim records exist for either set; the tally is explicit (claimed 12+12, on-disk 0+0). For P1 the review then showed the guards recognised 'one spelling of one mutation, in one file' — the proofs, whatever they showed, demonstrated guards that were not guards.

2. **Claimed:** P1b-fix trace: '18 guards ... Observed output recorded in the phase report rather than paraphrased.'

   **Evidence says:** The phase report is the workflow journal in a temp directory and is gone. 18 claimed, 0 survive; only M8-was-incomplete and the swallowed-NameError draft survive, in prose.

3. **Claimed:** P1 commit: the resolver reorder is safe because 'the two are order-independent.'

   **Evidence says:** P1-fix downgraded this to 'ContextVar-independent' and P1b partly reversed the ordering itself (the canary write moved after the seam, with two inverted guards observed red against the prior commit).

4. **Claimed:** d15be3a: 'Tier-2 probed P1's guard...' — and the traces throughout call the per-phase reviewers 'tier-2'.

   **Evidence says:** Those were tier-1 adversarial reviewers (9d81e34 corrects the attribution). No tier-2 judge read any part of this branch before this one.

5. **Claimed:** P3: 'The scores are suppressed twice — structurally and by state', presented as two independent defences, and 'two independent points, and both are needed' for the gate arm.

   **Evidence says:** Exhaustive re-run showed neither layer alone produces a red — one falsifiable property, not two defences — and neutering the gate arm alone leaves every collector test green because the collector is the only producer of a measured payload. Both claims were corrected in writing rather than defended.

6. **Claimed:** P2: 'Every failure between the two writes leaves the run claiming LESS than it did.'

   **Evidence says:** P3-fix found the opposite shape made ordinary by P2's own ordering: a run marked failed after scoring still carried agent_invoked=true and a full set of high pass_rates, and the collector returned MEASURED on it. Fixed by EVAL_SIGNAL_RUN_FAILED, but the original claim was false as stated.

7. **Claimed:** The plan's P3 section and the workflow's P3 prompt both require an alembic_tenant migration for agent_invoked.

   **Evidence says:** git diff on '*alembic*' is empty; 0015 remains the tenant head. The implementers' argument (config JSONB is the single home; absent and false are refused identically) is coherent, but it is a deviation from a written contract the owner never settled.

8. **Claimed:** P3: 'The prompt was updated so the narration cannot contradict the verdict.'

   **Evidence says:** Nothing in the repo executes run_orchestrator (BACKLOG 3.10; the collector's own run emits the never-awaited warning), so no test observes the model obeying any prose condition. What constrains narration is score suppression. Corrected in writing.

9. **Claimed:** The gate figures quoted per phase read as observed suite states of the branch.

   **Evidence says:** Eight of ten ladder rows were relayed, not reproduced; 65eab9e's row is explicitly flagged by its own author as not reproduced. Only the endpoints (and 5011f97, twice) were independently observed.

10. **Claimed:** The tier-1 review loop is fully accounted for in the fixes.

   **Evidence says:** The reviewers' finding lists did not survive; 17 of 48 findings and 7 of 13 'unsupported claims' exist only as counts and cannot be adjudicated by anyone. Coverage of the adjudication table is a lower bound, not a reconciliation.

---

## New backlog items the judge raised (8)

1. Tier-1 review outputs must be persisted to .dev/reference/ (or committed traces) before the workflow journal is discarded — this branch lost 17/48 findings and 7/13 unsupported-claim entries; the persistence rule currently covers only the tier-2 judgement.

2. Mutation-proof protocol hardening: record each proof's selector and import order (the repo has a known fake-@tool import artifact producing 74 spurious failures in hand-picked subsets), adopt the P3-fix sha256-both-sides restore protocol as standard, and either re-run or formally write off the 44 unrecorded proofs (P1 x12, P1-fix x12, P1b-fix x18) — extending the debt BACKLOG 3.9 already carries for the previous branch.

3. Data-egress reading of the eval path: production customer rows can reach the Ragas judge API via lookup_structured with the output firewall off; decide firewall-on-eval-path vs. accepted egress, separately from the scoring-fidelity question BACKLOG 2.11 currently frames.

4. Admin console has no consumer for agent_invoked / eval_signal / the new warning_id — the owner-facing account of a refused deploy exists only as API payload text; frontend surface plus the four frontend gates were never run against it.

5. Fix the pre-existing sys.modules pollution: test_agent_task.py installs a fake claude_agent_sdk and never removes it (class identity depends on collection order — observed directly by P1-fix), and the passthrough @tool fake in test_recorded_side_effects.py / test_retrieval_metrics.py causes the 74-failure ordering artifact.

6. Install ruff into apps/api/.venv (or pin the uvx version in the gate command) so lint claims leave an artifact and do not depend on a network fetch.

7. Post-P1 phases never re-ran the ignored-new-files control; adopt it (or an equivalent) as the standard per-phase proof that the delta is exactly the added tests, rather than the def-test-count arithmetic that cannot see a pre-existing test silently changing status.

8. The scorable floor can be thin: a run with 60 responses but only 3 retrieving turns is MEASURED with 3 scored observations gating the deploy; coverage is reported but nothing bounds the scorable fraction — decide whether a scorable-rate floor is wanted alongside MIN_SCORED_OBSERVATIONS (adjacent to BACKLOG 2.15/2.16, not identical).

