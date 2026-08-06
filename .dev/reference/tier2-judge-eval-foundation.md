# Tier-2 judge — eval-foundation (Fable 5, run wf_b3321f17-511, 2026-08-06)

Verbatim output of the tier-2 judge on the eval-foundation branch. It read a bounded
artifact only — the diff, the implementers' claims, and the tier-1 findings — and never
explored the tree. Extracted from the workflow journal so it survives the temp directory.

Five of these were summarised in `.dev/traces/260805-eval-foundation.md`; the rest existed
nowhere durable until now.

## Verdict

> The unit-level verification on this branch is genuinely strong — mutation demonstrations with observed red/green, several independently reproduced by tier-1 — but every one of them stops at the process boundary: there is no PostgreSQL, no real SDK session, and no agent invocation behind any claim, so "fixed" is proven against mocks and source text, never against the systems the audit's failures actually lived in. The thing the packet must say plainly: the audit's headline defect (D1, the label used as the prediction) is still shipped — run_eval_suite still sets agent_response = reference_answer, and while that is now honestly recorded in config (agent_invoked=False), the deploy gate does not read that field, so the gate now fail-closes on an ABSENT eval signal while shipping on a PRESENT one that still measures nothing about the agent. Similarly, red-team runs recorded before P4 — the era in which five of seven attackers provably had no tools and reported "clean" — still read as signal='measured' with clean findings, and unrecorded coverage only warns and substitutes the current build's now-7/7 capability, so a historically fake-clean run remains shippable evidence. All three new instruments (decision eval, judge calibration, SDK attackers) have zero real observations to date, and the migrations, SQL column names, and Neon-branch behavior all rest on skipped integration tests. This is an honest and well-guarded instrument-building milestone, and mergeable as such — but do not read it as "the platform is now evaluated": nothing on this branch has yet measured a real agent, and the one live signal the gate consumes is still vacuous.

## Claimed but unproven (17)

### 1. The deploy gate is now fail-closed: it ships only on measured eval evidence

- **Claimed in:** P2 summary ("deploy gate made fail-closed") and the deployment.py hunk's comments
- **Why unproven:** The gate fail-closes on ABSENT signals, but the 'measured' eval signal it accepts is produced by scoring the reference answer against itself — the eval.py diff still contains `"agent_response": row[3]  # use reference_answer as proxy` and a test literally named test_the_task_still_scores_the_reference_answer_against_itself. No test in TestEvidenceGate reads config.agent_invoked or scored_response_source, so the gate ships on scores that structurally cannot fail for agent-quality reasons — the retro's 'measurement that cannot fail' family, now behind a fail-closed door.
- **What would prove it:** A gate test showing ship/ship_with_warnings is refused (or at minimum a mandatory warning is forced) while eval_runs.config.agent_invoked is False; ultimately, an eval run that invokes the agent.

### 2. eval_results now go to production (the persistence split works)

- **Claimed in:** P1 summary and guard demonstration 'PERSISTENCE SPLIT (D2)'
- **Why unproven:** Proven only at the which-connection-was-opened level against a mock cursor. write_eval_results' column names (eval_run_id, metric) are pinned by no test — tier-1 showed rewriting the INSERT to the D3 names leaves the whole suite green — and the DB roundtrip is a skip. D3 was exactly a column-name mistake in a sibling file, so this is the single most on-theme unpinned surface on the branch.
- **What would prove it:** A test asserting the INSERT's column list against the migration/schema source (the same technique the trust-tier tests use on migration 0011), or one live integration run.

### 3. tests/integration/test_eval_e2e.py: 3 passed, offered as end-to-end evidence for the split

- **Claimed in:** P1 gate_output_tail
- **Why unproven:** That file exercises run_eval_for_agent, which tier-1 confirmed has zero production callers — a duplicate copy of the sequence that nothing runs. The passing e2e asserts the wrong surface: the real Celery task (run_eval_suite) has never executed against any database.
- **What would prove it:** Point the e2e at run_eval_suite's actual path, or delete run_eval_for_agent so the duplicate cannot masquerade as coverage.

### 4. Migrations 0013/0014/0015 are additive, nullable, rollback-safe and Postgres-valid

- **Claimed in:** P1/P2 summaries; the migration docstrings themselves
- **Why unproven:** All three are verified by source-text assertions only; every *_db_roundtrip test is skipped (no PostgreSQL on this machine). The diffs state this honestly, but the packet should carry it: no ALTER TABLE on this branch has ever been executed anywhere.
- **What would prove it:** Running the three roundtrip tests once against any disposable Postgres before the next tenant is provisioned.

### 5. SDK attackers are actually handed their tools and can use them (D4 closed)

- **Claimed in:** P4 summary
- **Why unproven:** All loop evidence runs through a fake _SDKHarness that tier-1 showed structurally cannot detect the wiring being removed (deleting mcp_servers= turned 1 of 55 tests red). Nothing pins the three-way agreement between create_sdk_mcp_server's name, the mcp_servers dict key, and ALLOWED_PROBE_TOOLS' mcp__{name}__ prefix — if any drifts, every attacker silently degrades to permanent INVALID and no test fails. The real claude-agent-sdk has never driven a probe.
- **What would prove it:** One test asserting the three name surfaces agree, plus a single live red-team run whose stored coverage shows vectors_valid=7 with real probe traffic.

### 6. Red-team coverage is now 7/7 and 'a vector reporting nothing counts INVALID'

- **Claimed in:** P4 summary and fix_summary blocker 2
- **Why unproven:** 7/7 is a compile-time constant, not an observation. Per tier-1, three deterministic vectors (content_injection, value_bound_evasion, identity_bypass) still do `except Exception: log.warning; return []` — clean over an unobserved run — while coverage asserts all seven valid. Additionally the identity_bypass vs identity_verification_bypass vocabulary split means vector_can_probe() classifies the string stored on that vector's findings as incapable, so coverage and findings cannot even be joined per vector.
- **What would prove it:** The same VectorObservation/INVALID treatment applied to all seven runners (the guard mutation exists as a template), and one vocabulary for vector names asserted by a test.

### 7. Missing data is no longer treated as passing data at the deploy gate

- **Claimed in:** P2 summary, fix_summary, and the 0015 migration docstring
- **Why unproven:** A red-team run stored BEFORE P4 — made with tool-less attackers that the audit proved reported fake 'clean' — still reads signal='measured' with zero findings; its unrecorded coverage only warns and is substituted with the current build's capability, which after SDK_ATTACKERS_CAN_PROBE=True is complete. Nothing invalidates or fences pre-P4 runs, so the gate can still be satisfied by a measurement this very branch documented as fake.
- **What would prove it:** A gate test that a run predating coverage recording (or predating the tool wiring) cannot alone satisfy the red-team evidence requirement — e.g. by run date, by coverage_recorded, or by requiring one post-P4 run.

### 8. human_scores.csv is 'pinned untouchable'

- **Claimed in:** P4 summary; GUARD 8
- **Why unproven:** The write-ban is a substring list (HUMAN_SCORES_CSV.write / .open("w" / csv.writer / csv.DictWriter) that misses the most idiomatic form `with open(HUMAN_SCORES_CSV, 'w')`. GUARD 8's red demonstration used csv.DictWriter — a form on the list — so the guard was demonstrated only inside its own blind spot's complement.
- **What would prove it:** Re-run GUARD 8 with `open(HUMAN_SCORES_CSV, 'w')`; expect green (i.e. the pin fails), then widen the pin.

### 9. Every eval run reports independent (attempted, valid, scored) denominators

- **Claimed in:** P2 summary item 4; eval.py docstring
- **Why unproven:** Through the production path attempted == valid always: both selectors carry WHERE reference_answer != '', so every fetched row is valid by construction. The tests that show attempted != valid construct states the SQL cannot produce (tier-1). The denominator that moves is scored; 'valid' as THE denominator is exercised only on synthetic input. Also test_the_run_reports_all_three_counts passes because of a fixture artifact (scored==0 from a mismatched scenario_id), not because of the property it names.
- **What would prove it:** Either drop the SQL-level label filter and let the code-level validity check be the filter, or rewrite the tests to assert the property over states the shipped queries can actually yield.

### 10. P3's confusion matrix is 'computed from real audit rows, not mocked verdicts'

- **Claimed in:** P3 summary, quoting the plan's test criterion
- **Why unproven:** There is no driver, so no real audit row has ever been scored: every possible run reports valid=0 / signal='no_observations' (stated in stopped_short). The 1,865-line service was omitted from the diff, none of its SQL (LIKE prefix, JSONB operator, UUID-vs-text compare) has ever been executed, and the read-only guarantee is a case-sensitive substring scan of module source. The instrument is plausible and well-guarded; it has measured nothing.
- **What would prove it:** The driver P3 deliberately deferred, run once against a seeded tenant; until then the claim should read 'a scorer exists', not 'a decision eval exists'.

### 11. P2's nine and P3's eleven guard mutations were observed red then green

- **Claimed in:** P2 and P3 guard_demonstrations
- **Why unproven:** Tier-1 reproduced four of P1's six guards and two of P4's, and explicitly reports doing neither for P2 nor P3 — those twenty demonstrations are implementer self-reports. Nothing suggests fabrication (the reproduced ones all matched exactly), but the packet should distinguish replicated guards from asserted ones.
- **What would prove it:** Spot-reproduce two or three of the P2/P3 mutations (the D3 column-name revert and the money-cell narrowing are the highest-value ones).

### 12. Config collectors are reused from deployment_service 'so a checklist and an eval run cannot disagree'

- **Claimed in:** P1 summary
- **Why unproven:** No test in the inventory pins the reuse, eval_service.py's source is omitted from the artifact, and deployment_service was substantially rewritten in P2 after the claim was made. Whether the two still share collectors is unverifiable from the evidence provided.
- **What would prove it:** A test importing both call sites and asserting they are the same function objects, or a source pin.

### 13. The day-1 eval dispatch 'fires at most once per agent rather than on every readiness check'

- **Claimed in:** _dispatch_first_eval_run docstring in the deployment.py diff
- **Why unproven:** The convergence rests on the empty run being recorded terminally, and that write is best-effort: the eval.py diff's own except path (run_recorded=False) leaves EVAL_SIGNAL_NO_RUNS standing, in which case the dispatch re-fires on every readiness check indefinitely. No test covers repeated dispatch under a persistent record failure.
- **What would prove it:** A test driving two consecutive checklist runs with insert_eval_run failing, asserting the second does not dispatch (or that a bounded backoff exists).

### 14. Evidence-gate warnings always reach the persisted checklist run

- **Claimed in:** P2 summary ('the reason merges into warnings by warning_id'); deployment.py comment 'or block arrives with no explanation'
- **Why unproven:** In the visible hunk, `derived = evidence_warnings + derive_blast_radius_warnings(blast_radius)` sits inside what appears to be the blast-radius merge block; whether evidence warnings persist when that branch does not run cannot be determined from the hunk provided. test_the_reason_is_persisted_as_a_warning exists but its fixture state is not visible.
- **What would prove it:** Confirm the merge executes on the no-blast-radius path, or a test with blast_radius absent asserting the evidence warning still lands.

### 15. The suite baselines and deltas reconcile across phases

- **Claimed in:** P1/P2 gate_output_tail
- **Why unproven:** P1 reports 1298 passed / 9 skipped (1307), but P2 reports '1323 collected at P1's HEAD' — a 16-test discrepancy nothing explains; P2's gate note also says '29 failed mid-edit' while its fix_summary says '4 tests red at task start'. Probably benign (collection vs selection, different moments), but the numbers offered as evidence do not add up within the artifact.
- **What would prove it:** One sentence reconciling collected vs passed+skipped at P1's HEAD, and which of 29/4 describes the inherited state.

### 16. The evidence-gate wiring is verified end to end in the deployment task

- **Claimed in:** P2 guard demonstration 3 and TestEvidenceGateWiring
- **Why unproven:** The P4 gate output shows `RuntimeWarning: coroutine '_run_orchestrator_loop' was never awaited` raised from test_deployment_service — the exact warning class this branch's own audit doc cites as runtime proof of a mocked-away region. The wiring tests are legitimate unit tests, but run_orchestrator itself is never executed anywhere on this branch, so every claim about how the prompt's prose conditions interact with the gate is untested by construction — and the warning went unremarked by all four phases and tier-1.
- **What would prove it:** Acknowledge it in the packet at minimum; ideally one test that drives run_orchestrator with a stubbed model client rather than patching the loop away.

### 17. Judge calibration semantics are fixed (D7)

- **Claimed in:** P4 summary
- **Why unproven:** The exit-code and pair-rate logic is genuinely pinned, but zero calibration measurements exist (tests/evals/responses/ is empty, stated honestly), MIN_PAIRS=3 makes rho>=0.75 nearly free at the floor (tier-1), and the floor is pinned by no derivation. The claim that the judge is or can be shown calibrated remains fully open.
- **What would prove it:** Running capture_responses.py against a live agent once, and deriving/raising the pair floor toward the 10-row CSV the spec frames.

## Evidence mismatches (8)

Claims the diff actively contradicts, as opposed to merely failing to support.

### 1. P1 commit: "every -m integration harness SKIPs"

**Contradicted by:** tests/integration/test_eval_e2e.py's pytestmark gates only on EVAL_E2E_ENABLED=1 and needs no database — tier-1 ran it on this machine and it passed, so it is not a harness that skips; the blanket claim is false for the one integration file this phase modified.

### 2. test_the_capability_flag_cannot_be_flipped_without_wiring_the_tools: the flag and the wiring 'must move together, IN BOTH DIRECTIONS'

**Contradicted by:** Under tier-1's GUARD-1 mutation (mcp_servers= deleted, attacker handed nothing) this test stayed GREEN — ALLOWED_PROBE_TOOLS' references to _TOOL_SEND_PROBE['name'] satisfy the string count without any wiring. The test's own docstring claim is demonstrated false in one of its two directions.

### 3. P4 fix_summary: 'Every runner now appends a VectorObservation… a vector reporting nothing counts INVALID'

**Contradicted by:** Tier-1 cites run_content_injection_agent (:1142-1144), run_value_bound_evasion_agent (:1397-1399) and run_identity_bypass_agent (:1528-1530) still doing `except Exception: log.warning(...); return []` — clean over an unobserved run — while the coverage report asserts all seven vectors valid. The claim holds for 4 of 7 runners, and is stated as universal.

### 4. P2 docstring on _fetch_eval_summary_sync: the kind filter means 'the deploy gate and the console can never disagree about how much a run measured'

**Contradicted by:** _LIST_EVAL_RUNS_SQL and _LIST_EVAL_RUN_DATASETS_SQL in evals.py carry no kind filter (tier-1), so with two agents in one tenant DB the gate and the console read different runs entirely — the docstring asserts the exact property the two query sets do not share.

### 5. Migration 0015 docstring: the coverage column exists because '"unknown" and "pass" render the same on screen, which the recurrence Family B exists to stop'

**Contradicted by:** The shipped deploy console still renders RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL as '0 critical · 0 high' with a Pass chip (deploy/page.tsx:2428-2430, 2730/2741 per tier-1), and no frontend file is in the diff — so on screen, unknown still renders as pass; the migration's stated purpose is achieved in the gate but not at the surface its own rationale names.

### 6. _dispatch_first_eval_run docstring: 'this fires at most once per agent rather than on every readiness check'

**Contradicted by:** The eval.py diff's empty-run path is best-effort: on record failure it returns run_recorded=False and writes nothing, leaving eval_signal == EVAL_SIGNAL_NO_RUNS — the precise condition deployment.py's step 4b keys the dispatch on — so the same diff contains the path on which the dispatch fires on every readiness check.

### 7. eval_service docstrings: 'branch_conn_str … is used by write_eval_results() and promote_to_verified_qa()' (:199-200) and 'scoring (run_ragas_eval) -> the branch, unchanged' (:18)

**Contradicted by:** The same phase removed branch_conn_str from both functions and made scoring open no database at all (P1 fix 1; the eval.py task docstring in the diff says so explicitly) — the service-module prose describes an architecture its own diff deleted.

### 8. verified_qa table comment: red_team scenario rows 'label a NEGATIVE… they never assert what the right answer is, so they can never become a served answer'

**Contradicted by:** api/v1/red_team.py:398 writes reference_answer=_SAFE_SCENARIO_REFERENCE_ANSWER — an authored CORRECT answer (tier-1). The refusal to promote red_team rows holds, but for the trust-tier reason, not the stated one; the comment a future reader would use to decide promotability is contradicted by the write it describes.
