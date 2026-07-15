# Agent Management — E2E Gap Analysis

**Framing:** PROVISIONING (create agent → tenant Neon DB → ingest → checklist → first deploy) is the only
end-to-end-complete flow. Everything about *managing an already-live agent* — the six capability areas the
Gotham operations room (`prototypes/gotham/agent.html`, research in `prototypes/gotham/AGENT-OPS.md`) is built
around — is stubbed, partial, or has no backend at all. Verdicts below are from reading the actual backend.

**Completeness bar = AGENT-OPS.md.** Live performance · retrieval health · failure-triage bench · eval
runs/suites+provenance · red-team programme · prompt versioning.

## Verdict table

| Capability area | Status | Evidence (backend that exists / is missing) | Gap to close |
|---|---|---|---|
| Live performance metrics | **PARTIAL** | Conversations/messages/tool_calls persist (`worker/tasks/runtime/agent.py:173` `_persist_messages`); escalation is captured from `ToolUseBlock` evidence + emitted + read back per-conversation (`agent_chat.py:240`). SDK `total_cost_usd`/`num_turns` are **logged only, never stored** (`agent.py:355-365`). `observability.py` exposes **only alerts** (list/resolve). No aggregate metrics endpoint; **CSAT/thumbs, p95 latency, cost/session, containment, deflection = ABSENT** (grep: no feedback/csat/p95 route). | Persist per-turn cost+latency+escalation to a metrics table; add widget thumbs/CSAT capture; add `GET /agents/{id}/metrics` aggregation. |
| Retrieval health | **ABSENT** | Ragas faithfulness/answer_relevancy/context_precision/context_recall are computed **at eval time only** (`evals.py:82-85`, stored in `eval_results`). No production retrieval telemetry: recall@k, nDCG@10, MRR, reranker lift, context-window utilization, compaction ratio, cited-chunk rank, index staleness/embedding drift are **not computed or stored anywhere**. Citations are parsed per-turn (`agent.py:240` `_extract_citations`) but never aggregated into citation coverage. | Instrument `retrieval_service` to emit rank/lift/window telemetry into a `retrieval_metrics` table + an index-staleness/drift task + a read endpoint. |
| Failure-triage bench | **ABSENT (UI-only)** | `mine_production_scenarios` auto-mines flagged convos into `source='mined'` scenarios (`scenario_service.py:284`) but is heuristic and frequently **cannot recover the question** (conversation_id linkage missing — its own comments, lines 296-307). No operator bench endpoint listing failing traces, no grade/triage action (to-grade/filed/held/dismissed), no operator-driven PROMOTE of a specific graded trace, no born-in-production counter, no "filed = cannot withdraw" law. | Trace-listing + grade endpoints + a `promote_trace_to_scenario` worker writing provenance into the suite. |
| Eval runs/suites + provenance | **PARTIAL** | Runs are real: `eval_runs`/`eval_results`/`eval_scenarios` persist in the tenant DB, are queryable (`evals.py` GET runs + per-scenario results), and `POST .../eval-runs/trigger` dispatches `run_eval_suite` (`evals.py:263`). Provenance is **coarse** — only a `source` enum (`generated`/`mined`/`production_failure`, `scenario_service.py`). No per-scenario origin trace-id / finding-id / authored provenance, no born-in-production vs authored count (the ORRERY ledger column). | Add a `provenance` column (origin trace/finding/authored) + born-in-production count surfaced in the eval-runs response. |
| Red-team programme | **PARTIAL** | `red_team_runs` persist (findings JSON, `max_severity`, `deployment_blocked`); endpoints list/get/trigger (`red_team.py`). **Critical-finding → deploy-gate linkage EXISTS**: the checklist worker reads `_fetch_red_team_summary_sync` (`deployment.py:154`) → `deployment_blocked`/`critical_count` feed `recommendation`. Langfuse traces via `red_team_service`. But findings are a **JSON blob inside a run**, not first-class rows; **strategies / probes / coverage are not persisted as queryable objects**; no per-finding severity triage; containing a finding does not file it back as a scenario (no flywheel). | First-class `red_team_strategies`/`red_team_probes`/`red_team_findings` + coverage rollup; contain-a-critical → file scenario. |
| Prompt versioning | **ABSENT** | `PATCH /agents/{id}` overwrites the soul JSONB **in place** (`agents.py:152` `patch_agent`). No `prompt_versions` table or model (none in `models/`); no diff, canary, or rollback anywhere (grep: `canary`/`rollback` hits are transactional-tool only). Soul editing is destructive, single-version. | `prompt_versions` table + version-on-save, diff endpoint, canary % routing at turn dispatch, rollback endpoint. |

## Notes on the substrate that DOES exist (reuse in Phase 21)

- **Trace capture is half-wired.** Langfuse v4 is imported in the validation chain
  (`validators.py`, `actor_seam.py`, `validation_service.py`, `red_team_service.py`) — so
  Gatekeeper/Auditor/Strategist/Actor turns emit traces — but **the main agent turn (`agent.py`) does not**,
  and there is **no read-back endpoint** to pull Langfuse traces into the ops room. Trace/span capture is
  therefore write-only and partial; the "Live" and "bench" regions have no queryable trace source.
- **Failure signals already exist** in `job_events` (`gatekeeper.complete`/`auditor.complete` with
  `fail`/`ungrounded`/`partial` verdicts) — the raw material for the bench — but they are only mined
  heuristically, never surfaced as a gradeable trace list.
- **The deploy gate is real** (`run_deployment_checklist` → `checklist_runs.recommendation` →
  `approve_deployment` flips `is_deployed`, `deployment.py`). Red-team block already flows into it. This is
  the hook Phase 21 wires the red-team programme and (optionally) live-metric gates onto.

## Current admin UI coverage (dusk pages)

The dusk `apps/admin/app/agents/[id]/*` screens that call **real** endpoints: `soul` (PATCH), `eval`
(eval-runs), `deploy` (checklist + approve), `ingest` (documents), `components/AlertsBanner` (alerts). There
is **no** screen for live-performance metrics, retrieval health, the triage bench, or prompt versioning —
those regions of the Gotham ops room have **no backend to call**. Phase 20 ports the UI; Phase 21 builds the
missing backends.
