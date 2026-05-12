# Project State

## Current Status

**Phase:** Not started
**Milestone:** M1 — Control Plane Skeleton
**Last updated:** 2026-05-12

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-12)

**Core value:** A non-technical business owner completes signup → ingest → deploy and gets a customer service agent that is defensible: grounded, evaluated, and red-teamed before it goes live.
**Current focus:** Not started — run `/gsd-plan-phase 1` to begin

## Phase Progress

| Phase | Name | Status | Requirements |
|-------|------|--------|--------------|
| 1 | Control Plane Skeleton | ○ Pending | CTL-01 to CTL-15 |
| 2 | Ingestion Pipeline | ○ Pending | ING-01 to ING-10 |
| 3 | Hybrid Retrieval | ○ Pending | RET-01 to RET-08 |
| 4 | Reasoning Engine + Widget | ○ Pending | AGT-01 to AGT-11 |
| 5 | Validation Chain | ○ Pending | VAL-01 to VAL-07 |
| 6 | Eval System | ○ Pending | EVL-01 to EVL-08 |
| 7 | Red Team | ○ Pending | RED-01 to RED-08 |
| 8 | Pre-deployment Checklist | ○ Pending | DEP-01 to DEP-08 |
| 9 | Retrieval Strategy Synthesis | ○ Pending | STR-01 to STR-03 |
| 10 | Maintenance + Observability | ○ Pending | OPS-01 to OPS-06 |

## Performance Metrics

| Metric | Target | Current |
|--------|--------|---------|
| Time signup → deployed widget | < 30 minutes | Not measured |
| Pre-deployment checklist pass rate | > 70% | Not measured |
| Median onboarding cost per tenant | < $5 (200-page corpus) | Not measured |
| Faithfulness (Ragas) | > 0.85 | Not measured |
| Answer Relevance | > 0.85 | Not measured |
| Auditor grounded rate (production) | > 0.90 | Not measured |
| Critical red team findings (weekly) | 0 | Not measured |
| p95 response latency | < 4 seconds | Not measured |

## Accumulated Context

### Key Decisions Made

| Decision | Rationale |
|----------|-----------|
| 10 phases, one per PRD milestone | PRD milestone structure is architecturally correct; each milestone is independently demonstrable |
| M1–M4 marked as first hireable artifact | Explicitly stated in PRD; M4 is the portfolio milestone for hiring market |
| M6 and M7 both depend on Phase 4 | Parallelizable after M4 — both need a running agent, not each other |
| M8 depends on Phase 6 AND Phase 7 | Pre-deployment checklist aggregates eval results + red team findings |
| Phase 9 depends on Phase 3 and Phase 2 | Strategy synthesis requires retrieval architecture + corpus shape data |
| UI hint: yes on Phases 4, 6, 8, 10 | Phases with observable owner-facing UI (widget, eval dashboard, deployment report, observability dashboards) |

### Stack Constraints (from research)

- Python 3.11 or 3.12 required (Langfuse v4 + Ragas 0.4.x constraint)
- Pydantic v2 globally (hard requirement from Langfuse v4 and Ragas 0.4.x)
- Langfuse v4 API only — `start_observation()` not `start_span()`, never install v3
- Ragas 0.4.x API — `ragas.metrics.collections`, `reference=` not `ground_truths=`, `MetricResult.value`
- pg_search (ParadeDB) is deprecated on Neon — use native `tsvector + ts_rank_cd` for BM25
- Eight stack additions not in PRD: asyncpg, tenacity, sse-starlette, PyJWT, python-multipart, pydantic-settings, structlog, cryptography

### Research Flags (phases needing deeper research during planning)

| Phase | Research Needed |
|-------|----------------|
| M2 | Table-aware chunking implementation; Docling HybridChunker open issue with tables |
| M4 | Preact widget CSP compatibility; JWT delivery pattern inside iframe |
| M6 | Ragas v0.4 API patterns for custom harness |
| M7 | PyRIT custom Claude probe patterns; indirect injection in retrieval path |
| M9 | Automated retrieval strategy synthesis (limited public documentation) |

### Critical Pitfalls to Watch

1. **M1/M2**: `acks_late=True` does not make tasks idempotent — use deterministic chunk_id + `ON CONFLICT DO NOTHING` upserts
2. **M2**: Table flattening destroys faithfulness on tabular queries — tables need a separate ingestion pathway
3. **M2**: Chunk boundary splits from fixed-size chunking — use Docling HybridChunker on DoclingDocument, not raw text
4. **M4**: Neon cold starts add 300–800ms to demo — disable scale-to-zero on demo tenant explicitly
5. **M1**: Neon provisioning race condition — add explicit `SELECT 1` connection probe loop before dispatching migrations

### Portfolio Notes

- M4 public demo + architecture blog post is the hiring market delivery package
- Actively job hunting — M4 timeline is the constraint that drives all scope decisions
- Admin UI must be distributed across milestones (not deferred to M4) — SSE consumer and file upload UI needed for M1/M2 demos

## Session Continuity

**How to resume:**
1. Check this file for current phase and focus
2. Check .planning/ROADMAP.md for phase details and success criteria
3. Check .planning/REQUIREMENTS.md for requirement traceability and status
4. Run `/gsd-plan-phase {N}` to begin planning the current phase
5. Run `/gsd-discuss-phase {N}` to discuss implementation approach before planning

**Last session:** 2026-05-12 — roadmap created, state initialized
**Next action:** `/gsd-plan-phase 1` — plan Phase 1 (M1: Control Plane Skeleton)

---
*State initialized: 2026-05-12*
