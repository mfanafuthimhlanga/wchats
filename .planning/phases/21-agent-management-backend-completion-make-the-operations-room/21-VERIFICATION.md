---
phase: 21-agent-management-backend-completion-make-the-operations-room
verified: 2026-07-18T09:11:51Z
status: passed
score: 7/7 must-haves verified (the one SC3 gap was closed post-verification -- see Gap Closure)
behavior_unverified: 0
overrides_applied: 0
gaps:
  - truth: "An operator grades a failing production trace filed; promote_trace_to_scenario inserts it into eval_scenarios with source='production' + origin_trace_id, the born-in-production count increments"
    status: closed
    reason: >
      promote_trace_to_scenario (apps/api/app/worker/tasks/runtime/bench.py) is fully
      implemented, idempotent, acks_late=True, and correctly inserts a source='production'
      scenario when invoked directly (all unit tests pass). But nothing in the application
      ever invokes it. POST /agents/{id}/traces/{trace_id}/grade (apps/api/app/api/v1/traces.py,
      built in plan 21-05, Wave 1) persists the grade and returns — it contains no
      .apply_async()/.si()/send_task call to promote_trace_to_scenario. bench.py itself
      (built in plan 21-06, Wave 3, after 21-05 already shipped) was never wired back into
      traces.py: its own plan explicitly says "(21-05's grade endpoint dispatches this by
      task name via send_task on a 'filed' grade — do not edit traces.py here.)" — but
      21-05 was executed in Wave 1, before bench.py/promote_trace_to_scenario existed, and
      could not have added that dispatch. 21-RESEARCH.md §Architecture (line 95) documents
      the intended flow as "filed → promote_trace_to_scenario.apply_async (Celery, runtime
      queue)" but no later plan (21-07, 21-08, 21-09) closes this gap either. A repo-wide
      grep for "promote_trace_to_scenario" and "app.worker.tasks.runtime.bench" outside
      tests/celery_app.py/bench.py itself returns zero call sites.
    artifacts:
      - path: "apps/api/app/api/v1/traces.py"
        issue: "grade_trace (POST /agents/{id}/traces/{trace_id}/grade) writes the grade via bench_service.grade_trace and returns — no dispatch of promote_trace_to_scenario on grade='filed'"
      - path: "apps/api/app/worker/tasks/runtime/bench.py"
        issue: "promote_trace_to_scenario is correctly implemented and registered in celery_app.py's include list, but is an orphaned task with zero callers in application code"
    missing:
      - "In traces.py's grade_trace, after bench_service.grade_trace succeeds with grade=='filed', dispatch celery_app.send_task('app.worker.tasks.runtime.bench.promote_trace_to_scenario', args=[str(agent_id), str(trace_id)], queue='runtime') (or an equivalent .apply_async/.si() call) — matching 21-RESEARCH.md's own documented architecture."
      - "A regression test in test_bench_routes.py or test_promote_trace.py asserting that POST .../grade with grade='filed' dispatches promote_trace_to_scenario, and that grade='held'/'dismissed' do not."
human_verification: []
---

# Phase 21: Agent Management Backend Completion Verification Report

**Phase Goal:** Close every non-provisioning E2E gap in `.planning/AGENT-MGMT-GAPS.md` so the ops room is backed by REAL data — new tenant/control-DB tables, IDOR-guarded endpoints, and acks_late+idempotent Celery tasks — honoring CLAUDE.md (Langfuse v4, Ragas 0.4.x, native tsvector BM25, conn_str never in task args, no Docker).
**Verified:** 2026-07-18T09:11:51Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Overall Verdict

**gaps_found — one real, code-observable wiring gap in SC3 (the flywheel's grade→promote step is not dispatched anywhere). Every other requirement (OPS-01 through OPS-10, OPS-12 through OPS-16) is genuinely implemented, wired, and passes its targeted tests. All CLAUDE.md compliance rules (Langfuse v4-only, Ragas 0.4.x reference-free Faithfulness, native tsvector/ts_rank_cd BM25, conn_str never in Celery task args, acks_late+idempotent on every new task, no `:r::jsonb`, no Docker) hold across every file touched in this phase.**

## Migration Chain Verification

| Chain | Head before phase | New revisions | Chains correctly | Forks? |
|---|---|---|---|---|
| Tenant (`alembic_tenant/versions/`) | 0008 | 0009→0010→0011→0012 | ✓ Yes (0008→0009→0010→0011→0012, each down_revision confirmed) | ✓ None |
| Control (`alembic/versions/`) | 0016 | 0017→0018 | ✓ Yes (0016→0017 `alerts_index_staleness_type` [21-04] →0018 `prompt_versions` [21-09, correctly renumbered from a plan-authoring-time collision with 21-04's own 0017]) | ✓ None |

Both chains verified by direct `revision`/`down_revision` inspection of every migration file (not by trusting SUMMARY claims).

## Per-Success-Criterion Verdict

| SC | Description | Status | Evidence |
|----|---|---|---|
| SC1 | turn_metrics + Langfuse v4 trace from `run_agent_turn`; `GET /agents/{id}/metrics` honest-empty | ✓ VERIFIED | `_write_turn_metrics`/`_emit_langfuse_turn_trace` in agent.py:343-469, called after the terminal `agent.response` emit (agent.py:963-999); `start_as_current_generation` used, no `start_span`/`start_generation`; metrics_service.py `NOT_TRACKED` sentinel confirmed; IDOR-guarded route confirmed; 23+21 tests pass |
| SC2 | `GET /agents/{id}/retrieval-health` from stored `retrieval_metrics`; BM25 baseline native tsvector | ✓ VERIFIED | retrieval_metrics written inside `retrieve_tool` (agent_tools.py:281-474), NOT in agent.py (`grep -c "INSERT INTO retrieval_metrics" agent.py` == 0); `ts_rank_cd`/`tsvector` confirmed in retrieval_service.py, zero `pg_search`/`pgbm25` hits; sampled Ragas Faithfulness reference-free (no `ground_truths`); check_index_staleness on `queue="pipeline"`; route returns vitals + staleness; 28+31 tests pass |
| SC3 | Grade `filed` → `promote_trace_to_scenario` → `eval_scenarios(source='production', origin_trace_id)` → next eval run; filed irrevocable | ⚠️ **PARTIAL / GAPS_FOUND** | Filed-irrevocable (409) ✓ VERIFIED (bench_service.grade_trace refuses any write once filed — 20 tests pass). Migration 0011 widening + `insert_provenance_scenario` + `promote_trace_to_scenario` (idempotent, acks_late, agent_id/trace_id-only args) all ✓ VERIFIED in isolation (21 tests pass). **But the causal chain "operator grades filed → promotion happens" does not exist in code** — see gap above. The task works when called; nothing calls it. |
| SC4 | Red-team strategies/probes/coverage queryable; critical finding files `source='red_team'`; live critical → `block` → 422 | ✓ VERIFIED | Migration 0012 creates all three tables; `run_red_team` upserts strategies/probes (ON CONFLICT DO NOTHING) and writes one `red_team_findings` row/finding (status='open'); contain/close on a critical finding calls the shared `insert_provenance_scenario(source="red_team", ...)`; `_fetch_red_team_summary_sync` rewired to `SELECT ... FROM red_team_findings WHERE status = 'open'`, `deployment_blocked = critical_count > 0`; zero `:r::jsonb` in deployment_service.py; 25+7+3 tests pass; integration gate test present, correctly SKIPPED without `INTEGRATION_TESTS_ENABLED` |
| SC5 | Soul edit → immutable `prompt_versions` row; diff/canary/rollback; canary sticky per conversation | ✓ VERIFIED | Control migration 0018 (0017 correctly avoided — 21-04 had already claimed it); `patch_agent` calls `create_version_from_agent` on any soul-field edit, never mutates a prior row; `resolve_prompt_version` filters `label IN ('production','canary')` — a draft is structurally unselectable; canary resolved once per conversation and stored on `conversations.metadata` (agent.py `_resolve_turn_prompt_version`/`_set_prompt_version_id`), reused on subsequent turns; `turn_metrics.prompt_version_id` populated; 32+2 tests pass |

## Requirements Coverage (OPS-01..OPS-16)

| Req | Description | Status | Evidence |
|---|---|---|---|
| OPS-01 | turn_metrics write from run_agent_turn | ✓ SATISFIED | agent.py `_write_turn_metrics`, called post-emit; idempotency guard unmodified |
| OPS-02 | message_feedback + widget feedback route | ✓ SATISFIED | widget.py `POST /widget/agents/{id}/feedback`, JWT-guarded, own rate-limit bucket, 422 on bad rating/csat |
| OPS-03 | GET /agents/{id}/metrics | ✓ SATISFIED | metrics.py, IDOR-guarded, NOT_TRACKED sentinels on zero rows |
| OPS-04 | Langfuse v4 trace on the agent turn | ✓ SATISFIED | `_emit_langfuse_turn_trace`, `start_as_current_generation` + `create_score(trace_id=job_id)` + single `flush()` |
| OPS-05 | retrieval_metrics + BM25/vector/RRF/rerank scores, recall/nDCG/MRR | ✓ SATISFIED | Written inside `retrieve_tool`, never agent.py; native tsvector/ts_rank_cd baseline |
| OPS-06 | ctx-window utilization, carried-never-cited tokens, compaction ratio | ✓ SATISFIED | Computed in `retrieve_tool`, migration 0010 columns confirmed |
| OPS-07 | Sampled Ragas faithfulness + citation coverage | ✓ SATISFIED | `run_retrieval_faithfulness`, sample-rate-or-auditor-flag gate evaluated inside the task (post-Auditor chain step), reference-free Faithfulness, idempotent UPDATE |
| OPS-08 | check_index_staleness (pipeline queue) | ✓ SATISFIED | `queue="pipeline"`, agent_id-only args, alert_service reuse, control migration 0017 widens `alerts.alert_type` CHECK |
| OPS-09 | GET /agents/{id}/traces?status=failing | ✓ SATISFIED | bench_service.list_failing_traces, conversation_id sourced from agent.response payload (never `jobs.conversation_id` — `grep -c "FROM jobs"` == 0) |
| OPS-10 | POST .../grade (filed irrevocable) | ✓ SATISFIED | 409 on re-grading a filed trace; append-only job_events rows |
| OPS-11 | promote_trace_to_scenario | ⚠️ **PARTIAL** | Task itself correct and tested; **never dispatched** — see SC3 gap |
| OPS-12 | provenance column + ORRERY ledger | ✓ SATISFIED | Migration 0011 widened CHECK + provenance/origin_trace_id; GET /eval-runs returns `ledger{born_in_production_count, red_team_count, authored_count}`; NULL-provenance rows fold into authored |
| OPS-13 | red_team_strategies/probes + programme endpoint | ✓ SATISFIED | Migration 0012; upsert writes; GET /red-team/programme with ASR-per-cell coverage rollup |
| OPS-14 | red_team_findings rows + contain→file scenario | ✓ SATISFIED | One row per finding; critical containment reuses `insert_provenance_scenario(source='red_team')` |
| OPS-15 | Deploy gate reads live findings → 422 | ✓ SATISFIED | `_fetch_red_team_summary_sync` rewired; zero `:r::jsonb`; integration test present (env-gated, skips cleanly) |
| OPS-16 | prompt_versions immutable + canary at dispatch | ✓ SATISFIED | See SC5 |

No orphaned requirements — OPS-01 through OPS-16 all appear in a plan's `requirements:` frontmatter and are covered above.

## CLAUDE.md Compliance Checks

| Rule | Check | Result |
|---|---|---|
| Rule 4 — conn_str never in Celery task args | Signature inspection on `run_retrieval_faithfulness`, `check_index_staleness`, `promote_trace_to_scenario` | ✓ PASS (all three: `conn_str` absent from `inspect.signature(...).parameters`, confirmed via passing tests) |
| Rule 5 — acks_late=True AND idempotency | grep on all three new task modules | ✓ PASS (`acks_late=True` present on every new `@celery_app.task`; each has an explicit idempotency guard — `SELECT faithfulness ... WHERE job_id`, read-only repeatable scan, `SELECT 1 FROM eval_scenarios WHERE origin_trace_id`) |
| Rule 6 — Langfuse v4 only | grep for `start_span(`/`start_generation(` in touched files | ✓ PASS (zero hits; `start_as_current_generation` used) |
| Rule 7 — Ragas 0.4.x only | `_compute_ragas_faithfulness` uses `ragas.metrics.collections.Faithfulness`, `InstructorLLM`, reference-free (no `ground_truths`) | ✓ PASS |
| Rule 8 — No pg_search/pgbm25, native tsvector+ts_rank_cd | grep across retrieval_service.py, agent_tools.py, retrieval_eval.py | ✓ PASS (zero `pg_search`/`pgbm25` hits; `ts_rank_cd`/`to_tsvector` confirmed as the sole BM25 mechanism) |
| Rule 9 — No Docker | No new Docker artifacts added this phase | ✓ PASS (not applicable — no docker-compose/Dockerfile touched) |
| Pitfall 1 — `CAST(:r AS JSONB)` not `:r::jsonb` | grep across deployment_service.py, prompt_version_service.py, agent.py | ✓ PASS (zero `:r::jsonb` hits anywhere in touched files) |

## Anti-Patterns Scan

No `TBD`/`FIXME`/`XXX` unreferenced debt markers found in any file this phase modified. No stub `return {}`/`return []` patterns found on a path that isn't explicitly an honest-empty/not-tracked sentinel (metrics_service.py's `NOT_TRACKED`, retrieval_metrics_service.py's "not tracked yet" are documented, deliberate, and tested — not stubs). No hardcoded empty-props-at-render-site patterns found (backend-only phase, no frontend artifacts).

One structural anti-pattern found and reported above: **an orphaned Celery task** (`promote_trace_to_scenario`) — present, correctly implemented, registered in `celery_app.py`'s `include` list, covered by passing unit tests, but with zero call sites in application code. This is the single blocking gap.

## Targeted Test Runs (all via `apps/api/.venv/Scripts/python.exe -m pytest`, no full-suite run — disk-constrained per instructions)

| Module(s) | Result |
|---|---|
| test_migration_0009, test_agent_turn_metrics, test_agent_turn_langfuse | 23 passed, 1 skipped |
| test_metrics_routes, test_widget_feedback | 21 passed |
| test_migration_0010, test_retrieval_metrics | 31 passed, 1 skipped |
| test_retrieval_faithfulness_task, test_index_staleness, test_retrieval_health_route | 28 passed |
| test_bench_routes | 20 passed |
| test_migration_0011, test_promote_trace | 21 passed, 1 skipped |
| test_migration_0012, test_redteam_programme | 25 passed, 1 skipped |
| test_redteam_findings | 7 passed |
| test_deployment_task | 3 passed |
| test_deploy_gate_redteam (integration) | 3 skipped (correctly gated, `INTEGRATION_TESTS_ENABLED` unset) |
| test_migration_0018, test_prompt_versions | 32 passed, 1 skipped |
| test_prompt_versions_e2e (integration) | 2 passed, 4 skipped (correctly gated) |

**Total: 213 passed, 8 skipped (all skips are correctly-gated integration/roundtrip tests), 0 failures.**

## Known Pre-Existing Issue (confirmed present, NOT a Phase-21 gap)

Importing `app.main` fails via `ragas → ragas.llms.base → langchain_community.chat_models.vertexai` `ModuleNotFoundError`. Confirmed present on unmodified HEAD across multiple independent plan executions (21-01, 21-02, 21-05, 21-06, 21-08, 21-09). Every plan in this phase worked around it correctly (targeted router-module imports, or a `sys.modules` stub for tests that must import `evals.py`), never by silently skipping coverage. This blocks route-level `app.main` test collection only — it does not affect any production code path this phase touches, and does not mask the SC3 gap found above (which was found by direct code inspection, not by a blocked test).

## Deferred / Live-Gated Items (correctly deferred per plan `<verification>` sections, not gaps)

- Real Langfuse trace visibility in the Langfuse UI (SC1)
- Real Ragas Faithfulness scoring against Anthropic (SC2)
- Migration roundtrips against a live Neon tenant/control DB (all migrations)
- Real red-team 422 assertion at the route/HTTP layer (SC4 — integration test proves the signal at the service+DB layer; route-level assertion blocked by the pre-existing `app.main` import issue)
- Real canary distribution + rollback against a live control Neon (SC5)

These are appropriately marked `INTEGRATION_TESTS_ENABLED`-gated and skip cleanly; they are explicitly scoped to `/gsd-verify-work 21` per every plan's own `<verification>` section and are not scored as gaps here.

## Gaps Summary

One real gap: **the flywheel's causal link from "operator grades a trace filed" to "promote_trace_to_scenario runs" does not exist anywhere in the codebase.** Every individual piece (grading with 409-on-refile, the promotion task itself, the widened CHECK constraint, the shared insert helper, the ORRERY ledger counts) is genuinely built and tested. The dispatch call connecting them was assumed by 21-06's plan to already exist in 21-05 ("21-05's grade endpoint dispatches this... do not edit traces.py here") — but 21-05 executed in Wave 1, before `bench.py`/`promote_trace_to_scenario` existed in Wave 3, so it could not have added that call, and no plan in Waves 3-5 (21-07, 21-08, 21-09) circled back to close it. This is a one-file, small fix: add a dispatch call in `traces.py`'s `grade_trace` on `grade == "filed"`.

This does not block SC1, SC2, SC4, or SC5, all of which are genuinely, fully wired end-to-end and verified against the actual codebase.

---

*Verified: 2026-07-18T09:11:51Z*
*Verifier: Claude (gsd-verifier)*

---

## Gap Closure (post-verification, 2026-07-16)

The single gap above (SC3 — a filed trace never dispatched `promote_trace_to_scenario`)
was closed by the orchestrator immediately after this report was written.

**Fix:** `apps/api/app/api/v1/traces.py` — `grade_trace` now dispatches
`promote_trace_to_scenario.apply_async(args=[agent_id, trace_id])` when `grade == "filed"`.
Only IDs cross the task boundary (conn_str is decrypted in-task, CLAUDE.md rule 4). The task
is imported lazily inside the handler to keep the worker task graph off the route module's
import path. A broker failure logs `grade_trace.promote_dispatch_failed` at error level
rather than failing the request — the grade is already committed and the task is idempotent,
so it is safely re-dispatchable; a silently un-promoted trace stays visible.

**Regression guard:** two tests added to `apps/api/tests/unit/test_bench_routes.py` —
`test_grade_filed_dispatches_promote_trace_to_scenario` (asserts the dispatch fires with
exactly `[agent_id, trace_id]`) and `test_grade_held_does_not_dispatch_promote_trace_to_scenario`
(asserts `held` never promotes). `pytest tests/unit/test_bench_routes.py -q` → **22 passed**
(was 20 before the fix).

**Commit:** `fix(21-08): dispatch promote_trace_to_scenario when a trace is filed (OPS-11 gap)`

**Root cause (recorded so the pattern is not repeated):** a cross-plan seam. Plan 21-05
(Wave 1) built the grade endpoint before `bench.py` existed, and plan 21-06 (Wave 3) built
the task while explicitly instructing "do not edit traces.py here — 21-05 dispatches this."
Neither could satisfy the other; no later plan closed it. Wave-ordered plans that hand a
seam across waves must name which plan owns the wiring, in the LATER plan.

**Residual (unchanged, deliberately deferred to `/gsd-verify-work 21` live gate):** live
Langfuse trace visibility, real Ragas faithfulness numbers, and the end-to-end
`POST /approve-deployment` → 422 assertion (blocked at route level only by the pre-existing
`ragas → langchain_community.chat_models.vertexai` dependency mismatch).
