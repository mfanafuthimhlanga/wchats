---
phase: 21
slug: agent-management-backend-completion-make-the-operations-room-real-per-agent-ops-md
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-15
---

# Phase 21 — Validation Strategy

> Per-phase validation contract. Source: `21-RESEARCH.md` § Validation Architecture.
> Backend phase — validation is pytest against real local Postgres + Redis (the existing
> test pattern: `dependency_overrides`, `CELERY_TASK_ALWAYS_EAGER`), migration roundtrips,
> IDOR/tenant-isolation tests, and grep/source assertions. Live-service items (Langfuse,
> Anthropic/Ragas online) are gated and may defer to a live gate, mirroring Phases 14/15.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing `apps/api` suite; real local Postgres + Redis; `CELERY_TASK_ALWAYS_EAGER=True`) |
| **Config file** | `apps/api/pyproject.toml` / existing conftest |
| **Quick run command** | `cd apps/api && pytest -q <touched test module>` |
| **Full suite command** | `cd apps/api && pytest -q` |
| **Estimated runtime** | module ~5–20 s; full suite ~1–3 min |

---

## Sampling Rate

- **After every task commit:** run the touched test module(s)
- **After every plan wave:** full `apps/api` suite green + migration roundtrip for that wave's migration
- **Before `/gsd-verify-work`:** full suite green
- **Max feedback latency:** ~120 s

---

## Success-Criterion → Validation Map (ROADMAP Phase 21 SC1–SC5)

| SC | How validated | Notes |
|----|---------------|-------|
| SC1 — turn_metrics + Langfuse trace + `GET /agents/{id}/metrics` from stored rows | migration 0009 roundtrip; unit test: a simulated `run_agent_turn` `ResultMessage` writes a `turn_metrics` row; endpoint test computes containment/escalation/p95/cost from seeded rows; IDOR test | Langfuse trace emission asserted via mock/span capture (live Langfuse deferred to live gate) |
| SC2 — `GET /agents/{id}/retrieval-health` from stored `retrieval_metrics` | migration 0010 roundtrip; instrument `retrieve_tool` and assert a row with recall@k/nDCG/reranker-lift (tsvector BM25 baseline)/ctx-window-util/citation-coverage is written; endpoint test | Ragas `faithfulness` sampled — real-model call gated; unit test uses a stub score |
| SC3 — grade failing trace `filed` → `promote_trace_to_scenario` → eval_scenarios(source='production',origin_trace_id) → next eval run; filed irrevocable | migration 0011 roundtrip (widen source CHECK + provenance); task test: filed trace inserts a scenario with provenance; born-in-production count increments; `filed`-cannot-withdraw test (409/immutable) | trace source = `job_events` `agent.response` payload (NOT `Job.conversation_id` — ORM gap) |
| SC4 — red-team programme queryable; critical finding files source='red_team' scenario; live critical → checklist `block` → `POST /approve-deployment` 422 | migration 0012 roundtrip; endpoint test for `/red-team/programme`; `red_team_findings` critical → `_fetch_red_team_summary_sync` → `recommendation='block'` → approve returns 422 (deployment gate test) | reuse existing red_team/deployment test patterns |
| SC5 — soul edit writes immutable `prompt_versions`; list+diff; canary %-routes; rollback restores; history never overwritten | control migration 0017 roundtrip; test soul PATCH appends a version (no overwrite); canary routing selects version at turn dispatch deterministically (seeded); rollback test | `prompt_versions` in CONTROL DB (0017), not tenant |

---

## Wave 0 Requirements

- [ ] Migration roundtrip harness for each new migration (tenant 0009–0012, control 0017) — up/down or up + head-check per the existing alembic test convention
- [ ] Test fixtures: seed `turn_metrics` / `retrieval_metrics` / `red_team_findings` rows; a fake failing `job_events` trace
- [ ] `:r::jsonb` → `CAST(:r AS JSONB)` regression guard reused for any new `text()` JSONB insert

*Existing infra (real Postgres/Redis, eager Celery, dependency_overrides) covers the rest.*

---

## Manual-Only / Live-Gated Verifications

| Behavior | Requirement | Why | Handling |
|----------|-------------|-----|----------|
| Live Langfuse v4 trace visible in Langfuse UI | OPS-04 | needs live Langfuse project | live gate (defer, mirror Phase 15) |
| Real Ragas faithfulness on a live model | OPS-07 | needs Anthropic + real retrieval | sampled; unit uses stub; live gate for real numbers |
| p95 latency / cost realism | OPS-03 | environment-bound (4 GB box) | assert computation correctness on seeded rows; real numbers on prod infra |

---

## Validation Sign-Off

- [ ] Every task has an `<automated>` verify or a Wave 0 dependency
- [ ] No 3 consecutive tasks without automated verify
- [ ] Each new migration has a roundtrip test
- [ ] Live-only items explicitly marked as gated, not silently skipped
- [ ] `nyquist_compliant: true` set once the planner fills the per-task map

**Approval:** pending
