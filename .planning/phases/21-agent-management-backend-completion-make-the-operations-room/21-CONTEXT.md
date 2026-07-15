# Phase 21: Agent-management backend completion — Context

**Gathered:** 2026-07-15
**Status:** Ready for planning
**Source:** ROADMAP v1.2 Phase 21 (OPS-01..16) + `.planning/AGENT-MGMT-GAPS.md` + `prototypes/gotham/AGENT-OPS.md` + `21-DOMAIN-NOTES.md`

<domain>
## Phase Boundary

Close every non-provisioning E2E gap in `.planning/AGENT-MGMT-GAPS.md` so the Gotham operations room (shipped in Phase 20) is backed by **real data end-to-end, not mock**. This is a **backend phase**: new tenant-DB tables, services, endpoints, and Celery tasks. The four honest-empty ops-room regions (Live / Retrieval health / The bench / The prompt) become real; Judgement + Adversary get first-class programme objects.

**IN SCOPE:** tenant-DB migrations (start at 0009), FastAPI endpoints (IDOR-guarded, tenant-scoped), Celery tasks (pipeline/runtime queues), instrumentation of `run_agent_turn` + `retrieval_service`, Langfuse v4 tracing on the agent turn, Ragas 0.4.x groundedness, red-team programme tables, prompt versioning.

**MINIMAL FRONTEND:** only the small wiring needed to turn a Phase-20 EmptyState into a live region once its endpoint exists (and OPS-02's widget feedback route/button). Do NOT redesign UI — the Gotham contract stands.

**OUT OF SCOPE:** anything not in OPS-01..16; the Phase-13 production deploy; A2A/MCP.
</domain>

<decisions>
## Implementation Decisions (LOCKED — from CLAUDE.md, non-negotiable)

- **Every Celery task:** `acks_late=True` AND idempotent (both, always). Tasks receive `tenant_id`/agent IDs only — connection strings are fetched + decrypted from the control DB at runtime, NEVER passed in task args (CLAUDE.md rule 4/5).
- **Two queues:** `pipeline` (ingestion/build) and `runtime` (evals, agent calls). New tasks route to the correct queue.
- **Langfuse v4 API only** — `start_as_current_span` / `update_current_generation`. No pre-v4 patterns (`start_span`/`start_generation` are gone). Follow the existing `validation_service.py` Langfuse-v4 pattern.
- **Ragas 0.4.x only** — `ragas.metrics.collections`, `MetricResult`, `reference` (not `ground_truths`). Reuse the existing eval service's Ragas usage.
- **BM25 = native `tsvector` + `ts_rank_cd` only** — no `pg_search`/pgbm25 (deprecated on Neon Mar 2026). The reranker-lift metric (OPS-05) computes the BM25 baseline this way.
- **No Docker.** Local processes only (Redis, Postgres, uvicorn, celery worker). 4 GB RAM dev box.
- **Per-tenant Neon** — new tables are tenant-DB (via `alembic_tenant`), migrations start at **0009** (0008 = customer_identities is head). Platform metadata (if any) → control DB.
- **Endpoints:** tenant-scoped + IDOR-guarded, mirroring the existing `evals.py` / `red_team.py` patterns (agent_id ownership check, conn_str resolution). New routes registered in `main.py`.
- **Migrations:** raw SQL with `IF NOT EXISTS` guards (safe re-run), matching the established convention (e.g. 0006/0007/0011).
- **Honest empty states:** every surfaced metric computed from a stored row; never mock/seeded; say "not tracked yet" where a signal genuinely doesn't exist.
</decisions>

<requirements>
## Requirements OPS-01..16 (4 waves — from ROADMAP; every ID → some plan's `requirements`)

**Wave 1 — Trace/span capture + live performance (Live region):**
- OPS-01 `turn_metrics` tenant table + write from `run_agent_turn` capturing the SDK `ResultMessage` (cost_usd, num_turns, latency_ms, escalated, tool_count) currently logged-only (`agent.py:~347-365`).
- OPS-02 `message_feedback` tenant table + widget `POST /widget/agents/{id}/feedback` (thumbs ±, optional 1–5 CSAT) per assistant message.
- OPS-03 `GET /agents/{id}/metrics` — containment, deflection, escalation rate, CSAT/thumbs-down, p95 latency, cost/session over a window from `turn_metrics` + `message_feedback` + conversations.
- OPS-04 Langfuse v4 trace+generation per production turn in `run_agent_turn`, linked to the `turn_metrics` row by `job_id` (agent.py currently untraced).

**Wave 2 — RAG health instrumentation (Retrieval-health region):**
- OPS-05 `retrieval_metrics` tenant table + instrument `retrieval_service`: recall@k, nDCG@10, MRR, reranker lift (BM25→vector→hybrid→reranker delta; BM25 via native tsvector), cited-chunk rank.
- OPS-06 extend OPS-05 write path: context-window utilization (retrieved tokens vs 200k budget), carried-never-cited tokens, compaction ratio per turn.
- OPS-07 production groundedness + citation coverage: per-turn citation coverage from the CITATIONS parse in `run_agent_turn` + lightweight groundedness (Ragas 0.4.x `faithfulness`), stored in `retrieval_metrics`.
- OPS-08 `check_index_staleness` Celery task (acks_late + idempotent; tenant_id in args) flagging stale docs (source newer than last embed) + embedding-model drift → `GET /agents/{id}/retrieval-health`.

**Wave 3 — Failure-triage flywheel (The bench):**
- OPS-09 `GET /agents/{id}/traces?status=failing` — failing production turns (Gatekeeper/Auditor fail/ungrounded/partial from `job_events`) with customer turn, agent turn, judge rationale.
- OPS-10 `POST /agents/{id}/traces/{trace_id}/grade` (filed|held|dismissed) + bench tally; a `filed` trace cannot be withdrawn (TERRARIUM law).
- OPS-11 `promote_trace_to_scenario` Celery task (acks_late + idempotent; tenant_id in args, conn_str at runtime) inserting a filed trace into `eval_scenarios` with `source='production'` + `origin_trace_id`; increment born-in-production count.
- OPS-12 add `provenance` column to `eval_scenarios` (origin trace-id / finding-id / authored); surface born-in-production vs authored in eval-runs response (ORRERY ledger).

**Wave 4 — Red-team programme + prompt versioning (Adversary + The prompt):**
- OPS-13 `red_team_strategies` + `red_team_probes` tenant tables + coverage rollup → `GET /agents/{id}/red-team/programme` (strategies/probes/coverage as first-class queryable objects, not a per-run JSON blob).
- OPS-14 `red_team_findings` tenant table (one row/finding: severity, status) replacing embedded findings JSON; containing/closing a critical finding files it into `eval_scenarios` (`source='red_team'`, provenance=finding-id).
- OPS-15 wire the critical-finding gate-block to the real deploy gate: `_fetch_red_team_summary_sync` (`deployment.py:154`) drives `run_deployment_checklist` → `recommendation='block'` so `POST /approve-deployment` returns 422 on a live critical finding.
- OPS-16 `prompt_versions` table capturing every soul edit as an immutable version; `GET /agents/{id}/prompt-versions` + diff, `POST .../canary` (% routing chosen at turn dispatch in `run_agent_turn`), `POST .../rollback` (`patch_agent` no longer overwrites history).
</requirements>

<canonical_refs>
## Canonical References (MUST read before planning/implementing)

### Scope / domain
- `.planning/ROADMAP.md` Phase 21 (OPS-01..16 + success criteria SC1–SC5)
- `.planning/AGENT-MGMT-GAPS.md` — the E2E gap table this phase closes
- `prototypes/gotham/AGENT-OPS.md` — the frontier-lab agent-ops completeness bar
- `.planning/phases/21-.../21-DOMAIN-NOTES.md` — operator vocabulary + "what good looks like"
- `prototypes/gotham/agent.html` — the six-region ops room this backend feeds; `apps/admin/app/agents/[id]/page.tsx` (shipped) + its `EmptyState` regions

### Code to extend (grounding)
- `apps/api/app/worker/tasks/runtime/agent.py` — `run_agent_turn`, `ResultMessage` handling (~L347), CITATIONS parse (OPS-01/04/07/16)
- `apps/api/app/services/retrieval_service.py` (or equivalent) — hybrid retrieval to instrument (OPS-05/06/07)
- `apps/api/app/services/validation_service.py` — the Langfuse-v4 + Haiku-judge pattern to mirror (OPS-04/07)
- `apps/api/app/services/eval_service.py` + `scenario_service.py` — Ragas 0.4.x usage, `eval_scenarios`/`verified_qa` (OPS-07/11/12)
- `apps/api/app/worker/tasks/runtime/deployment.py` — `_fetch_red_team_summary_sync` (L44/154), `run_deployment_checklist`, `recommendation` (OPS-15)
- `apps/api/app/api/v1/evals.py` and `red_team.py` — endpoint + IDOR + conn_str patterns to copy; red_team_runs schema (OPS-09/13/14)
- `apps/api/alembic_tenant/versions/` — migration convention; head = 0008; new tables start 0009
- `apps/api/app/worker/celery_app.py` — task include + beat schedule + queues
- `CLAUDE.md` — the non-negotiable rules above
</canonical_refs>

<specifics>
## Specific Ideas
- OPS-01/04 are tightly coupled: capture `ResultMessage` into `turn_metrics` AND emit the Langfuse trace in the same `run_agent_turn` write path, linked by `job_id`.
- OPS-11 + OPS-14 share the "file into `eval_scenarios` with provenance" mechanic (source='production' vs 'red_team') — build one insertion path, two callers.
- OPS-16 canary routing happens at turn dispatch in `run_agent_turn` (percent chosen per turn) — coordinate with OPS-01/04 which also touch that path.
- Reuse existing infra wherever possible: Langfuse client, Ragas metrics, the eval scenario insert, the deployment checklist. This phase is mostly *wiring real data into shapes the ops room already renders*.
</specifics>

<deferred>
## Deferred Ideas
- Any new frontend design (Gotham UI is the contract; only minimal region-wiring here).
- Phase 13 production deploy; A2A/MCP; classifier firewall (v1.2/v1.3 out-of-scope per ROADMAP).
- Full judge-alignment TPR/TNR tooling (DOMAIN-NOTES §1) unless an OPS req names it — surface as a follow-up if it exceeds OPS-07's "lightweight groundedness".
</deferred>

---
*Phase: 21-agent-management-backend-completion*
*Context: ROADMAP v1.2 + AGENT-MGMT-GAPS + domain research, 2026-07-15*
