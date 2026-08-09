# Tier-2 judgement — D6 (feat/d6-labelling-loop)

**Judge:** Fable 5, bounded artifact only — `.dev/reference/d6-tier2-artifact.md`. It did not
explore the repository. **Date:** 2026-08-09. **Run:** wf_d0eb060d-712.

**The headline is not the verdict, it is the yield report** (`.dev/reference/d6-mining-yield.md`):
`mine_production_scenarios` queries `jobs.conversation_id`, a column that does not exist on the `Job`
model and that no `alembic` revision creates. The statement raises `UndefinedColumn` inside the
best-effort `try/except` at `eval.py:397`, which logs a warning and continues. **Mining has never
produced a row and cannot.** Independently verified against `app/models/job.py` — the model has
`id`, `tenant_id`, `agent_id`, `kind`, `status`, `error`, `started_at`, `finished_at`, `created_at`,
and nothing else.

**Verdict: mergeable = True**

---

## The verdict, verbatim

> The claims match the evidence, and the artifact is unusually honest about where evidence ends. The two phase-deciding properties hold on the reproduced hunks: no machine path to a human-tier label exists in this diff (guarded by a no-tier-parameter API, a credential-kind 403, fail-closed context detectors, extra='forbid', and a CHECK — with refusals observed red under mutation, not asserted), and verified_qa stays unreachable behind three deliberate locks that the branch correctly describes as pins rather than physics. The gate chain 1873 -> 2112 is unbroken, controls ran in all six phases, failures and self-corrections are on the record rather than smoothed over. What is unproven is exactly what the artifact says is unproven: no SQL in the branch has ever been executed by Postgres, 0016 is applied nowhere (so the entire feature 503s today), 16 findings are fixed only in test code the judge cannot see, evidenced solely by their authors' own mutation proofs, and no adversary has probed the post-fix tree. The mining-yield analysis reframes the branch honestly: D6 built a correct, well-guarded write path and queue for a producer that raises before it can produce — the plan's premise about the miner was wrong, and the correction currently lives in an untracked file. Mergeable once four cheap dispositions land: commit the yield doc, dispose the orphaned P2 F13, file BACKLOG rows for the yield doc's three discovered defects, and have the owner rule on the d1 stacking. None of these touches app code; all four are record-keeping the project's own rules mandate. Nothing in this branch has moved a row out of the unlabelled state, and nothing could have — the branch says so itself, which is precisely why it passes.

---

## Phase-deciding question 1 — can a machine write a human-tier label?

Yes, within this diff — and the branch earns the narrower claim it actually makes, not the absolute one. Holding hunks (§5.1): (a) record_human_label(conn, *, scenario_id, reference_answer, labelled_by) has NO tier parameter and HUMAN_AUTHORED_TIER is a module constant — callers cannot select a tier; (b) `if credential_kind != CREDENTIAL_CLERK_JWT: raise HTTPException(status_code=403, ...)` with CREDENTIAL_UNKNOWN also refused, backed by deps.py setting request.state.credential_kind on all three success returns — an API key or worker credential cannot reach the handler; (c) assert_human_context() as the handler's FIRST statement, both detectors raising HumanLabelRefused on malfunction rather than returning None (fail-closed, and P1-fixes #8/#9 captured the stamped-label stdout when this was mutated open); (d) ConfigDict(extra="forbid") with reference_answer the only field; (e) the CHECK admitting only NULL or the two human tiers AND a non-empty answer. The refusals were observed red under mutation, including the reviewer's exact f-string forgery (P1-fixes #1) and a Celery task stamping human_authored with one arm removed (P1 #5) — these are not tautologies. What keeps "unwritable" from being absolute, stated by the branch's own docstrings: 0016 — "A raw `... = 'human_authored'` is NOT refused. The database bounds the vocabulary; it does not authenticate the writer"; label_service — the guards "authenticate the CALL SITE. They say nothing about the CONTENT of reference_answer"; so a human pasting model output, or future raw SQL, is a docstring decision, not a mechanism, and labelled_by = f"tenant:{tenant.id}" names an account, not a person. Residuals: R4 never run in a real Celery worker, the R3 composed-column-name hole is statically undetectable, the post-fix guards were never adversarially re-probed, and the CHECK has never executed anywhere. Dominating fact cutting both ways: with 0016 applied to no database, every label attempt 503s — today nothing, human included, can write a human tier. This is not a laundering machine; it is a guarded door in front of a room that does not yet exist.

---

## Phase-deciding question 2 — does verified_qa stay unreachable?

Yes — by three deliberate locks visible in the diff, not by the vocabulary, which would permit it (record_human_label stamps human_authored, rank 3, clearing VERIFIED_QA_MIN_TRUST_TIER rank 2 outright — the branch names this inversion itself). Lock zero: promote_to_verified_qa is invoked from nowhere under app/ — the collector's independent grep returns only the definition at eval_service.py:2028, two internal log lines, and prose; "the `promoted: 0` this task returns is a literal, not a result." Lock one: `_LABEL_SQL` assigns exactly {reference_answer, label_trust_tier, labelled_by, labelled_at} and never `source`, while select_promotion_candidates gates on source; the swap hazard is latent because no eval.py selector projects label_trust_tier (P3 F13, verified against the reproduced selectors). Lock two: `if not VERIFIED_QA_PROMOTION_DECISION["enabled"]: _refuse(PROMOTION_DISABLED_REFUSAL); continue`, on a MappingProxyType where subscript assignment raises. Caveats the diff itself states and I endorse: all three locks are process-local and recorded in no database; lock zero's absence pins are module-scoped, so "a THIRD module introducing the call would trip neither" — the locks are honest about being pins, "written down, not closed." And the trivial outer lock: no database has the labelled rows to promote. Unreachable today, on the evidence; the module-scoped pins are the right first backlog item for whenever promotion is deliberately revisited.

---

## Must fix before merge (4)

1. Track and commit .dev/reference/d6-mining-yield.md. It is untracked, it corrects five existing records (including the D6 plan premise and BACKLOG 4.10's description of the miner), and it holds the only written analysis of why the queue's producer cannot produce. Losing it to a clean checkout (the BACKLOG 2.20 failure mode, on this very branch) would orphan the branch's most consequential finding.

2. Dispose P2 review F13 in the merge commit: either add the UndefinedColumn fallback to _unlabelled_page_sync (matching the counts and datasets queries) or add a BACKLOG row accepting the pre-0011 500. The project rule is transactional — a discovered defect with no disposition row anywhere in the branch's records is exactly the loss mode BACKLOG.md exists to prevent, and the renumbering that dropped it is confirmed by the diff.

3. Add BACKLOG rows for the yield doc's discovered defects before its file is the only place they live: the unreachable conversation_id skip / miner abort (supersedes 4.10's wording), the ON CONFLICT DO NOTHING that can never fire (seven-night duplicate re-insert on a repaired miner), and needs_clarification missing from the miner's verdict IN list. Same transactional rule.

4. Owner decision, not an implementer fix: this branch is stacked on unmerged feat/d1-agent-invocation with two open owner decisions — merging D6 to main pulls d1 in with it. The owner must dispose d1 first or explicitly accept merging both.

---

## Claims the evidence supports (1)

- (duplicate-guard placeholder — see array above)

---

## Claims asserted but NOT established (13)

1. **The CHECK constraint refuses model_generated and empty answers**

   *What would prove it:* Apply 0016 to a real PostgreSQL database and observe both CHECK arms reject an INSERT — the migration has executed on no database; test_migration_tenant_0016_db_roundtrip SKIPS, and a skip is unobserved, never a pass.

2. **Any SQL in this branch is valid and behaves as asserted (_UNLABELLED_QUEUE_SQL, _QUEUE_COUNTS_SQL, _QUEUE_COUNTS_PRE_0016_SQL, _LABEL_SQL, _SCENARIO_EXISTS_SQL, the DO $$ block, array_position ... ASC NULLS LAST, the scoped UPDATE, AVG(score) GROUP BY metric)**

   *What would prove it:* Execute each against Postgres; today all are asserted only at string level against a recording cursor.

3. **The write-then-probe pair yields a 409 under concurrency**

   *What would prove it:* A two-connection race against a real database; the pair is not FOR UPDATE and the argument is, per the branch itself, 'reasoning about the manual, not an observation' (BACKLOG 4.11).

4. **R4 refuses a real Celery worker context**

   *What would prove it:* Run the detectors inside an actual Celery worker process; R4 has only ever run under pytest.

5. **request.state.credential_kind is stamped correctly in production auth flows**

   *What would prove it:* A real ASGI process with live JWKS; never exercised.

6. **The 403 credential gate broke no existing caller**

   *What would prove it:* Enumeration or exercise of actual callers; currently an argument from absence.

7. **The 16 test-side fixes are real fixes rather than assertions weaker than their names**

   *What would prove it:* Independent inspection of the 5,866 summarised test lines or an adversarial pass over them — each fix's only evidence is a mutation proof written by the same agent that wrote the fix, and the ignored-new-files control is stated by two reviewers to be blind to weakened assertions inside still-passing tests (a blind spot that already fired once, on c860780).

8. **The post-fix guard set (post-fix detectors, the 403, the scoped UPDATE, the three locks) withstands adversarial probing**

   *What would prove it:* An adversarial review of the post-fix tree; all three tier-1 reviews examined pre-fix code.

9. **'No forgery shape anyone has yet devised passes unnoticed' generalises**

   *What would prove it:* It cannot be proven, only falsified — the branch itself concedes a mutation ledger 'measures the ledger's author's imagination, not the suite's coverage', and the R3 residual (a column name composed from fragments inside allowlisted eval_service.py) is statically undetectable (4.8).

10. **The production-trace path (F) actually yields rows**

   *What would prove it:* The owner-run control-DB COUNT the yield doc recommends; F is 'unknown, plausibly 0' and the doc's author ran nothing.

11. **The 0.85 ship bar gates anything**

   *What would prove it:* It is prompt prose applied by a model, not code — under the project's own principle a model-applied bar may never gate a deploy, so this needs a code-enforced check or an explicit statement that it gates nothing.

12. **Lock zero holds against future code**

   *What would prove it:* Its absence pins are module-scoped — 'a THIRD module introducing the call would trip neither'; a repo-wide pin would close it. All three locks are process-local and recorded in no database.

13. **The suite is green at the branch tip d0a3b4e**

   *What would prove it:* A gate run at the tip; the last observed run is at f78524e, with the two trailing commits verified docs-only via git show --stat — near-proven, but stated as a gap by the collector and unobserved in fact.

---

## Evidence mismatches (6)

1. **Claimed:** Every tier-1 finding was disposed

   **Evidence says:** P2 review F13 (_UNLABELLED_QUEUE_SQL projects 0011 columns with no UndefinedColumn handler, so a pre-0011 tenant gets a 500 while the sibling counts and datasets queries both fall back) is confirmed unfixed by the diff, and the fixes report renumbered its F-list (its F13 = the review's F14), so the finding has no disposition row anywhere in the branch's records.

2. **Claimed:** BACKLOG 4.10, the plan, and the miner docstring: unmineable jobs are gracefully skipped via the conversation_id 'continue'

   **Evidence says:** The 'continue' is unreachable — jobs has no conversation_id column, the SELECT raises first, and run_eval_suite swallows the abort as a warning. Three records describe a behaviour the code has never had; the correction currently lives only in an untracked file.

3. **Claimed:** D6 plan premise: red-team rows are stored and never scored

   **Evidence says:** The yield doc shows red-team rows carry a non-empty reference_answer, so they are already labelled, are scored, and never enter the queue — one of five existing records the untracked yield doc corrects.

4. **Claimed:** P1-fixes mutation table heading: '11' proofs

   **Evidence says:** The table contains 12 numbered rows (#1-#12). Minor, but it is a counting error inside evidence offered as proof, in a branch whose commit message also miscounted findings (13 vs 14, corrected in d0a3b4e).

5. **Claimed:** P1 finding #14 (the c860780 head-assertion weakening) is resolved

   **Evidence says:** Confirmed PARTIAL by the diff: test_migration_tenant_0015.py still carries +13/-4, the weakening still lives in the feature commit, and only the head identity was re-pinned elsewhere; the commit cannot be repackaged because rebasing is forbidden.

6. **Claimed:** The label write path exists as a working feature

   **Evidence says:** On every tenant database today the route returns 503 and human_labelled comes back null — 0016 is applied nowhere. What merged is a correctly-guarded write path to a column that exists in no database, feeding a queue whose nominal producer raises before inserting. The claims themselves say this; the mismatch is only against any reading of D6 as 'the labelling loop now works'.

---

## New backlog items (9)

1. Adversarial re-probe of the POST-fix tree: all three tier-1 reviews examined pre-fix code, so the post-fix detectors, the 403 credential gate, the scoped UPDATE, and the three verified_qa locks have never been probed by an adversary — only defended by their authors' mutation ledgers.

2. Repair the miner per the yield doc's ordered plan: two guard tests (no DB) -> owner-run control-DB COUNT of flagged job_events -> the ~30-line repair plus tenant migration only if the count is non-zero -> revisit P4. If the count is zero, the labelling loop is waiting on production traffic, not a queue UI.

3. When any PostgreSQL becomes available: apply 0016, run the integration roundtrip, and observe both CHECK arms actually reject (model_generated tier; empty reference_answer) — the constraint has never rejected anything, and the downgrade has never run.

4. Strengthen lock zero from module-scoped pins to a repo-wide absence check, so a third module introducing a promote_to_verified_qa call trips a test.

5. Exercise R4 in a real Celery worker and credential_kind stamping in a real ASGI process with live JWKS — both currently pytest-only.

6. Concurrency observation for the write-then-probe 409 path (extends existing 4.11) and an index for the queue's WHERE+ORDER BY (extends existing 4.9) once a database exists.

7. Either enforce the 0.85 ship bar in code or record explicitly that it gates nothing — as prompt prose applied by a model, it currently sits on the wrong side of the 'a model-generated label may never gate a deploy' principle.

8. Independent (non-author) inspection pass over the four new test files (5,866 lines) targeted at assertions weaker than their names — the one defect class the ignored-new-files control is provably blind to, and which occurred once on this branch (c860780).

9. BACKLOG 1.3 remains open with no captured traceback for the wait_for_neon_ready flake; BACKLOG 4.6's ContextVar leak has now cost two identical fixtures — both already filed, noting recurrence.

