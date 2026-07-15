# Phase 21: Agent-management backend completion — Research

**Researched:** 2026-07-15
**Domain:** Backend wiring of real operational telemetry into an existing multi-tenant RAG agent platform (FastAPI + Celery + per-tenant Neon + Langfuse + Ragas)
**Confidence:** HIGH — this phase extends code that already exists and was read directly; no new frameworks, no new packages, only new tables/columns/endpoints/tasks following six already-established conventions (IDOR pattern, Celery task pattern, Langfuse v4 pattern, Ragas 0.4.x pattern, migration pattern, ContextVar pattern).

## Summary

Phase 21 has **zero new external dependencies**. Every piece of infrastructure it needs already exists in the codebase and just needs to be wired to real write paths: `validation_service.py` already has the correct Langfuse v4 pattern (`start_as_current_generation` + `create_score` + `flush`), `eval_service.py` already has the correct Ragas 0.4.x pattern (`ragas.metrics.collections`, `InstructorLLM`, `reference` field), `evals.py`/`red_team.py` already have the correct IDOR + conn_str-at-runtime endpoint pattern, and `run_agent_turn` already computes (but discards) exactly the SDK `ResultMessage` fields OPS-01 needs. The work is disciplined plumbing, not invention.

Two architectural corrections to the phase's default assumption are important. First, **`prompt_versions` (OPS-16) belongs in the CONTROL DB**, not the tenant DB — `agents.soul`/`soul_role`/`soul_voice`/`soul_do_list`/`soul_donot_list` are control-DB columns (`app/models/agent.py`), and `patch_agent` already mutates them there; a tenant-DB `prompt_versions` table would need a cross-DB join against `agents` on every read, which nothing else in this codebase does. Control-DB Alembic (`apps/api/alembic/versions/`) is a **separate chain from `alembic_tenant`**, currently at head **0016** (confirmed: `0016_pending_confirmations_dedup_index.py`), so `prompt_versions` is control migration **0017**, using the same raw-SQL-with-`IF NOT EXISTS` convention already used for `checklist_runs` (`0011_checklist_runs_is_deployed.py`). Second, **`eval_scenarios.source` has a `CHECK` constraint currently limited to `('generated', 'mined')`** (`0005_verified_qa_eval_scenarios.py:79-80`) — OPS-11/OPS-14 need `'production'` and `'red_team'` added to that constraint, which requires an `ALTER TABLE ... DROP CONSTRAINT ... ADD CONSTRAINT` in the Wave 3 migration, not just a new `provenance` column.

All ten new tenant tables follow the established convention of **raw psycopg2, no SQLAlchemy ORM model** — exactly like `eval_scenarios`, `verified_qa`, `red_team_runs`, `conversations`. Only `prompt_versions` (control DB) gets an ORM model, matching `ChecklistRun`.

**Primary recommendation:** Treat this as four additive migrations (tenant `0009`–`0011` + control `0017`) plus targeted instrumentation inserts into three already-identified write points (`run_agent_turn`'s `ResultMessage` branch, `retrieve_tool`'s post-rerank return, and `run_agent_turn`'s turn-dispatch system-prompt construction) — do not restructure any existing function signatures beyond adding new optional trailing parameters (`job_id` to `build_tool_server`, `prompt_version_id` reporting to `turn_metrics`).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Turn-level cost/latency/escalation capture (OPS-01) | API/Backend (Celery `runtime`) | Database (tenant) | `ResultMessage` is only observable inside `run_agent_turn`; the write is a tenant-DB insert alongside the existing `_persist_messages` call |
| Widget thumbs/CSAT capture (OPS-02) | API/Backend (FastAPI public route) | Database (tenant) | Public unauthenticated write (widget JWT-scoped), same trust tier as `POST /widget/{id}/chat` |
| Aggregate metrics read (OPS-03) | API/Backend (FastAPI, tenant-scoped) | Database (tenant) | Pure aggregation query over `turn_metrics`/`message_feedback`/`conversations`; mirrors `evals.py`'s `list_eval_runs` |
| Langfuse trace/generation on agent turn (OPS-04) | API/Backend (Celery `runtime`) | External Observability (Langfuse) | Emission point is the same `run_agent_turn` body; Langfuse is an external service boundary, not app-owned storage |
| Retrieval instrumentation (OPS-05/06/07) | API/Backend (Celery `runtime`, inside the MCP tool) | Database (tenant) | Score/rank data only exists inside `retrieve_tool`'s closure (`agent_tools.py`) — never returned to the SDK loop, so it must be written from there, not from `agent.py` |
| Index staleness/drift detection (OPS-08) | API/Backend (Celery `pipeline`, scheduled) | Database (tenant) | Read-only scan over `documents`/`chunks`/`embeddings`; belongs on the `pipeline` queue (ingestion-adjacent), not `runtime` |
| Failing-trace listing/grading (OPS-09/10) | API/Backend (FastAPI, tenant-scoped) | Database (control + tenant, joined in application code) | `job_events` (control DB) has the judge verdicts; `messages` (tenant DB) has the question/answer text — no cross-DB SQL join is possible, so the join happens in Python, same pattern `scenario_service.mine_production_scenarios` already uses |
| Promote-trace-to-scenario flywheel (OPS-11/12) | API/Backend (Celery `runtime`) | Database (tenant) | Writes into `eval_scenarios`, already a tenant table; task pattern mirrors `run_eval_suite` |
| Red-team programme objects (OPS-13/14/15) | API/Backend (Celery `runtime` + FastAPI read routes) | Database (tenant) | Coverage rollup and findings are tenant-scoped like `red_team_runs` today |
| Deploy-gate wiring (OPS-15) | API/Backend (Celery `runtime`, `deployment_service.py`) | Database (control, `checklist_runs`) | The gate itself (`checklist_runs.recommendation` → 422) is already control-DB; only the *signal source* changes from JSONB-blob parsing to a first-class-table query |
| Prompt versioning + canary routing (OPS-16) | API/Backend (FastAPI write routes + Celery `runtime` read-at-dispatch) | Database (**control**, not tenant) | `agents.soul*` columns are control-DB; canary selection happens inside `run_agent_turn` before `build_system_prompt()` is called |

## Package Legitimacy Audit

**No new external packages are introduced by this phase.** Every dependency needed (`langfuse>=4.0.0,<5.0.0`, `ragas>=0.4.0,<0.5.0`, `psycopg2-binary==2.9.12`, `alembic==1.18.4`, `anthropic`, `instructor`) is already pinned in `apps/api/pyproject.toml` and already imported by `validation_service.py` / `eval_service.py` / `scenario_service.py`. No `npm view` / `pip index versions` / registry check is required — this table is intentionally empty.

| Package | Registry | Verdict | Disposition |
|---------|----------|---------|-------------|
| — | — | — | No new packages; audit not applicable |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Standard Stack

### Core (all already installed — versions confirmed from `pyproject.toml` and live imports)
| Library | Version (pinned) | Purpose in Phase 21 | Why Standard (in this codebase) |
|---------|---------|---------|--------------|
| `langfuse` | `>=4.0.0,<5.0.0` [VERIFIED: apps/api/pyproject.toml:26] | OPS-04 trace/generation emission | Already used in `validation_service.py`/`actor_seam.py` with the exact `start_as_current_generation`+`create_score`+`flush()` v4 pattern CLAUDE.md requires |
| `ragas` | `>=0.4.0,<0.5.0` [VERIFIED: apps/api/pyproject.toml:31] | OPS-07 lightweight per-turn faithfulness | `eval_service.py:run_ragas_eval` is the canonical 0.4.x usage (`ragas.metrics.collections.Faithfulness`, `InstructorLLM`, dataset field `reference`) |
| `psycopg2-binary` | `==2.9.12` [VERIFIED: apps/api/pyproject.toml:16] | All new tenant-table writes | Every tenant-DB write in the codebase uses raw psycopg2 (never SQLAlchemy) — `retrieval_service.py`, `scenario_service.py`, `agent_tools.py` all follow connect/try/finally/close |
| `alembic` | `==1.18.4` [VERIFIED: apps/api/pyproject.toml:14] | Migrations 0009–0011 (tenant) + 0017 (control) | Two independent Alembic chains already exist (`alembic/` control, `alembic_tenant/` tenant) |
| `anthropic` + `instructor` | already pinned | Reused by Ragas `InstructorLLM` wrapper for OPS-07 | `eval_service.py:119-120` is the exact call site to copy |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Per-turn Ragas faithfulness on every turn | Sample 5–10% of turns (DOMAIN-NOTES §2) | Running Ragas on 100% of production turns doubles the LLM cost of every conversation for a metric whose primary value is trend detection, not per-turn gating — the codebase's own Auditor already gates every turn cheaply via a forced-tool Haiku call |
| First-class `red_team_findings` table replacing the JSONB blob entirely | Keep `red_team_runs.findings` JSONB for backward read compat, add `red_team_findings` as the new source of truth | `_fetch_red_team_summary_sync` (`deployment_service.py:189`) currently parses the JSONB; OPS-15 requires updating that function to read from the new table instead — a straight replacement is cleaner than maintaining two sources of truth long-term |

**Installation:** none — no `pip install` needed for this phase.

## Architecture Patterns

### System Architecture Diagram

```
Wave 1 (Live):
  Widget → POST /widget/{id}/chat → run_agent_turn (Celery, runtime queue)
     SDK ResultMessage (currently log.info-only, agent.py:355-365)
        │
        ├──► INSERT turn_metrics (job_id, cost_usd, num_turns, latency_ms, escalated, tool_count)
        └──► Langfuse: start_as_current_generation(name="agent-turn", trace via job_id) + create_score + flush
  Widget → POST /widget/agents/{id}/feedback (new, public JWT-scoped)
        └──► INSERT message_feedback (message_id, rating, csat_score)
  Admin UI → GET /agents/{id}/metrics (new, IDOR-guarded)
        └──► SELECT aggregates FROM turn_metrics + message_feedback + conversations

Wave 2 (Retrieval health):
  run_agent_turn → SDK tool loop → retrieve_tool (agent_tools.py, MCP tool)
     rrf_fuse() → rerank()   [scores/ranks only exist HERE]
        └──► INSERT retrieval_metrics (job_id, bm25/vector/rrf/rerank scores+ranks, retrieved_tokens)
  run_agent_turn (after _extract_citations) → celery_chain(...).apply_async
        └──► (sampled) run_retrieval_faithfulness.si(...) → UPDATE retrieval_metrics SET
             citation_coverage, faithfulness  [Ragas 0.4.x, same pattern as eval_service.py]
  Celery beat (pipeline queue) → check_index_staleness (per-tenant)
        └──► scans documents/chunks/embeddings → flags stale/drifted → readable via
             GET /agents/{id}/retrieval-health

Wave 3 (Bench flywheel):
  Admin UI → GET /agents/{id}/traces?status=failing (new)
        └──► control DB job_events (gatekeeper/auditor verdicts) JOINed in Python with
             tenant DB messages (question/answer text) — no cross-DB SQL join possible
  Admin UI → POST /agents/{id}/traces/{trace_id}/grade (filed|held|dismissed)
        └──► filed → promote_trace_to_scenario.apply_async (Celery, runtime queue)
                  └──► INSERT eval_scenarios (source='production', provenance=origin_trace_id)
  Red-team finding containment (Wave 4) also calls the SAME insert path
                  └──► INSERT eval_scenarios (source='red_team', provenance=finding_id)

Wave 4 (Adversary + Prompt):
  run_red_team (existing) → NEW: also writes red_team_strategies/red_team_probes/red_team_findings
        └──► GET /agents/{id}/red-team/programme reads coverage rollup
  Containing a critical finding → UPDATE red_team_findings SET status='contained'
        └──► _fetch_red_team_summary_sync (deployment_service.py) now queries red_team_findings
             (open + severity='critical') instead of parsing findings JSONB
        └──► run_deployment_checklist → recommendation='block' → POST /approve-deployment → 422
  Admin UI → PATCH /agents/{id} (soul edit, existing) → NEW: also INSERT prompt_versions (control DB)
  run_agent_turn (turn dispatch, BEFORE build_system_prompt) → NEW: canary %-routing reads
        prompt_versions WHERE agent_id AND label IN ('production','canary') → weighted pick
        → turn_metrics.prompt_version_id records which version served this turn
```

### Recommended Project Structure (additions only — existing structure is followed, not changed)
```
apps/api/
├── alembic_tenant/versions/
│   ├── 0009_turn_metrics_message_feedback.py     # Wave 1
│   ├── 0010_retrieval_metrics.py                 # Wave 2
│   └── 0011_bench_and_redteam_and_scenario_provenance.py  # Wave 3 + Wave 4 tenant tables
├── alembic/versions/
│   └── 0017_prompt_versions.py                   # Wave 4, CONTROL DB
├── app/
│   ├── models/
│   │   └── prompt_version.py                     # ORM model, control DB only (mirrors checklist_run.py)
│   ├── services/
│   │   ├── metrics_service.py                    # OPS-01/03 aggregation queries (new)
│   │   ├── retrieval_metrics_service.py           # OPS-05/06/07 write/read helpers (new)
│   │   ├── bench_service.py                       # OPS-09/10/11 trace listing + grading + promote (new)
│   │   ├── redteam_programme_service.py            # OPS-13/14 strategies/probes/findings (new)
│   │   └── prompt_version_service.py               # OPS-16 version/diff/canary/rollback (new)
│   ├── api/v1/
│   │   ├── metrics.py         # GET /agents/{id}/metrics, /retrieval-health          (new)
│   │   ├── traces.py          # GET /traces, POST /traces/{id}/grade                (new)
│   │   ├── red_team.py        # extend existing: GET /red-team/programme            (edit)
│   │   ├── prompt_versions.py # GET/POST prompt-versions, diff, canary, rollback    (new)
│   │   └── widget.py          # extend existing: POST /widget/agents/{id}/feedback  (edit)
│   └── worker/tasks/
│       ├── pipeline/staleness.py    # check_index_staleness (new, pipeline queue)
│       └── runtime/
│           ├── bench.py             # promote_trace_to_scenario (new, runtime queue)
│           └── retrieval_eval.py    # run_retrieval_faithfulness sampled task (new, runtime queue)
```

### Pattern 1: Tenant-DB write inside an already-open MCP tool (retrieval_metrics)
**What:** `retrieve_tool` in `agent_tools.py` already has `conn_str` (ContextVar), `rrf_result` (vector/bm25/fused candidates with ranks), and `reranked` (final scores) in scope after line 341. Add one more `psycopg2.connect(conn_str)`/insert/close block before the `return` at line 360 — following the exact connect/try/finally/close idiom already used by `vector_search`/`bm25_search`/`rrf_fuse` in `retrieval_service.py`.
**When to use:** Any metric that depends on data only visible inside the tool closure (rank/score), never returned to the SDK.
**Example:**
```python
# Source: apps/api/app/services/agent_tools.py:337-363 (existing code to extend)
rrf_result: dict = await loop.run_in_executor(
    None, lambda: rrf_fuse(conn_str, query_vector, query, strategy)
)
reranked: list[dict] = await loop.run_in_executor(
    None, lambda: rerank(query, rrf_result["fused"], strategy)
)
job_id = _job_id_var.get()  # NEW ContextVar — set by build_tool_server, same pattern as conn_str

def _write_retrieval_metrics() -> None:
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO retrieval_metrics
                  (id, job_id, conversation_id, bm25_top_score, vector_top_score,
                   rrf_top_score, rerank_top_score, retrieved_tokens, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (str(uuid.uuid4()), job_id, conversation_id,
                 rrf_result["bm25_candidates"][0]["bm25_score"] if rrf_result["bm25_candidates"] else None,
                 rrf_result["vector_candidates"][0]["cosine_score"] if rrf_result["vector_candidates"] else None,
                 rrf_result["fused"][0]["rrf_score"] if rrf_result["fused"] else None,
                 reranked[0]["rerank_score"] if reranked else None,
                 sum(len(c.get("content", "")) for c in reranked[:MAX_CHUNKS]) // 4),
            )
        conn.commit()
    finally:
        conn.close()

await loop.run_in_executor(None, _write_retrieval_metrics)
```

### Pattern 2: Cross-DB correlation in application code, not SQL (OPS-09 trace listing)
**What:** Judge verdicts (`gatekeeper.complete`/`auditor.complete`) live in control-DB `job_events`; the actual question/answer text lives in tenant-DB `messages`. No SQL can `JOIN` across two separate Neon projects. `scenario_service.mine_production_scenarios` (lines 284-397) already solves this exact problem: query control DB for flagged `job_id`s, extract `conversation_id` from the `agent.response` event's payload (NOT the `jobs` table — that column doesn't exist on `Job`, see Pitfall 5), then query tenant DB `messages` for that `conversation_id`.
**When to use:** OPS-09's `GET /agents/{id}/traces?status=failing`.
**Example:**
```python
# Source: apps/api/app/services/scenario_service.py:325-337, 371-380 (existing pattern to reuse)
flagged_rows = control_db.execute(
    sa_text("""
        SELECT je.job_id, je.payload->>'verdict' AS verdict, je.payload->>'reason' AS reason
        FROM job_events je
        WHERE je.event_type IN ('gatekeeper.complete', 'auditor.complete')
          AND je.payload->>'agent_id' = :agent_id
          AND je.payload->>'verdict' IN ('fail', 'ungrounded', 'partial')
        ORDER BY je.created_at DESC LIMIT 50
    """), {"agent_id": agent_id},
).fetchall()

# For each job_id, fetch the SAME job's 'agent.response' event for conversation_id + response text
# (job_events already has both rows — no jobs.conversation_id needed, unlike scenario_service's
#  current broken fallback path).
response_row = control_db.execute(
    sa_text("SELECT payload FROM job_events WHERE job_id = :jid AND event_type = 'agent.response' LIMIT 1"),
    {"jid": job_id},
).fetchone()
conversation_id = response_row.payload.get("conversation_id") if response_row else None
```
**Correction to note in the plan:** `scenario_service.py`'s existing miner tries `SELECT conversation_id FROM jobs WHERE id = :job_id` — the `Job` ORM model (`app/models/job.py`) has **no `conversation_id` column**, so that query silently returns nothing every time (this is *why* AGENT-MGMT-GAPS.md calls the miner "frequently cannot recover the question"). OPS-09 must NOT copy that broken fallback — use the `agent.response` event payload instead, which reliably has `conversation_id` (`agent.py:677`).

### Pattern 3: Canary routing at turn dispatch (OPS-16)
**What:** `run_agent_turn` builds `system_prompt = build_system_prompt(agent)` at `agent.py:543`, using the agent's live `soul_role`/`soul_voice`/`soul_do_list`/`soul_donot_list`. To canary a prompt version, resolve which version to use *before* this call, using a lightweight weighted-random pick against `prompt_versions` (control DB), then override the four soul fields on a shallow copy (or pass explicit override kwargs into `build_system_prompt`) rather than mutating the live `agent` row.
**When to use:** Every turn, cheap control-DB read (indexed on `agent_id`).
**Example:**
```python
# Insertion point: apps/api/app/worker/tasks/runtime/agent.py, immediately before line 543
prompt_version_id, soul_override = resolve_prompt_version(db, agent_id)  # new helper, control DB
system_prompt = build_system_prompt(agent, soul_override=soul_override)  # build_system_prompt gets one new optional kwarg
# ... later, in _persist turn_metrics write (Wave 1 table, extended in Wave 4 migration):
# INSERT turn_metrics (..., prompt_version_id) VALUES (..., %s)
```

### Anti-Patterns to Avoid
- **Writing retrieval_metrics from `agent.py` instead of `agent_tools.py`:** the tool's return payload (`{"content": [...], "_citations": [...]}`) does not carry rank/score data back to the SDK loop — reconstructing it in `agent.py` is impossible without re-running retrieval. Write it where the data already exists.
- **Adding `conn_str` to any Celery task's argument list** to avoid a second DB round-trip: every new task (`check_index_staleness`, `promote_trace_to_scenario`, a hypothetical canary-sync task) must take only `agent_id`/`tenant_id` and decrypt at runtime, exactly like `run_deployment_checklist` and `run_eval_suite` already do (CLAUDE.md rule 4).
- **Running Ragas faithfulness on every production turn:** DOMAIN-NOTES §2 and the existing eval-cost economics (Ragas = LLM calls) both argue for sampling; wire OPS-07's faithfulness computation as a *sampled* Celery task in the same chain as Gatekeeper/Auditor/Strategist, gated by `random.random() < settings.RETRIEVAL_FAITHFULNESS_SAMPLE_RATE` (new setting, default 0.05–0.10) plus 100% of turns the Auditor already flagged `ungrounded`/`partial` (DOMAIN-NOTES: "100% of guardrail-flagged/low-confidence traces").
- **Treating `eval_scenarios.source` as an open TEXT column:** it has a live `CHECK` constraint (`0005_verified_qa_eval_scenarios.py:79-80`) restricted to `('generated', 'mined')`. Inserting `source='production'` or `source='red_team'` without first widening the constraint will raise a `CheckViolation` at INSERT time, not at migration time — this WILL be silently swallowed if the insert helper has a bare `except Exception` around it (several existing helpers do).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Langfuse v4 trace emission | A new Langfuse wrapper/helper module | Copy `validation_service.py:_log_verdict` (lines 359-399) verbatim in shape — `start_as_current_generation(...)` context manager + `create_score(trace_id=job_id, ...)` + `flush()`, wrapped in `try/except` with `_langfuse is None` no-op guard | This is the one Langfuse call site in the codebase that has already survived a live-verification pass (Phase 15 found and fixed a `flush()` latency bug here — see Pitfall 3) |
| Ragas 0.4.x faithfulness scoring | A custom prompt-based groundedness scorer | `eval_service.py:run_ragas_eval`'s pattern: `InstructorLLM(client=instructor.from_anthropic(anthropic.Anthropic()), model=HAIKU_MODEL, provider="anthropic")` + `ragas.metrics.collections.Faithfulness(llm=llm)` | The Auditor (`validation_service.py:call_auditor`) already does a *similar but distinct* Haiku-judge grounding check — OPS-07 wants the quantitative Ragas metric specifically, not a re-implementation of the Auditor |
| BM25 scoring | `pg_search`/pgbm25 or a custom TF-IDF implementation | `retrieval_service.bm25_search`'s existing `ts_rank_cd(to_tsvector(...), plainto_tsquery(...))` query — reuse directly, do not reimplement | CLAUDE.md rule 8 (deprecated on Neon March 2026); the query is already correct and indexed via `chunks_content_tsv_idx` |
| IDOR-guarded tenant-scoped endpoints | A new auth/ownership abstraction | Copy the 6-step pattern from `evals.py`'s `list_eval_runs` (fetch agent from control DB → check `agent.tenant_id == tenant.id` → check `neon_connection_string` present → `fernet_decrypt` → `asyncio.to_thread(_query_tenant_db_sync, ...)`) for every new GET route | Six existing routes (`evals.py` x3, `red_team.py` x3) already implement and test this exact pattern; deviating risks reintroducing an IDOR bug that's already been closed elsewhere |
| Cross-DB "join" for OPS-09 | A federated query layer / foreign data wrapper | Two sequential queries + a Python dict merge, exactly as `scenario_service.mine_production_scenarios` already does | Control DB and tenant DB are genuinely separate Neon projects; there is no SQL-level join available, and building one is out of scope |
| Prompt version diff | A generic JSON diff library | Simple field-by-field comparison of the four soul fields (`soul_role`, `soul_voice`, `soul_do_list`, `soul_donot_list`) between two `prompt_versions` rows — these are the only fields that change | The "prompt" here is 4 structured fields, not a single text blob; a generic text-diff library adds a dependency for a problem that's a 10-line dict comparison |

**Key insight:** This phase's biggest risk is not "what library to use" — it's silently deviating from six patterns that already work and are already load-bearing elsewhere in the codebase. Every new endpoint/task/write-path has a near-identical sibling already merged; copy its shape.

## Runtime State Inventory

Not applicable — this is a purely additive phase (new tables, new columns, new endpoints, new tasks). No rename, no refactor, no migration of existing data or identifiers. **Skipping this section per the trigger condition** (rename/refactor/migration phases only).

One item worth flagging as a **data backfill**, not a rename: existing `eval_scenarios` rows all have `provenance IS NULL` after the OPS-12 column is added (they predate provenance tracking). The metrics/ORRERY-ledger read path (`GET /eval-runs`) must treat `provenance IS NULL` as `"authored"` (pre-provenance scenarios were either `generated` or `mined`, neither of which is `"born in production"`), not as an error state. This is a code-edit-only concern (read-path default), not a migration requirement — `provenance` should be nullable with no backfill UPDATE needed.

## Common Pitfalls

### Pitfall 1: The `:param::jsonb` SQLAlchemy `text()` cast crash
**What goes wrong:** `sa_text("UPDATE t SET result = :r::jsonb WHERE ...")` raises at execution time (not at query-construction time) because SQLAlchemy's `text()` bind-parameter parser treats `:r::jsonb` ambiguously — it swallows the `::jsonb` cast incorrectly.
**Why it happens:** Documented in `.planning/phases/15-.../15-03-SUMMARY.md`: this exact bug shipped in Phase 14's idempotency SQL and crashed every approved mutating call on a real DB, only caught during Phase 15's live-DB verification gate (not by unit tests, which mock the DB).
**How to avoid:** Use `CAST(:r AS JSONB)` instead of `:r::jsonb` in every `sqlalchemy.text()` statement that casts a bind parameter to JSONB. This applies to any control-DB write in this phase that uses SQLAlchemy `text()` (e.g., updating `checklist_runs.report`, if OPS-15's `_fetch_red_team_summary_sync` rewrite touches it) — psycopg2 raw-cursor code (used for all tenant-DB writes in this phase) is unaffected; this is purely a SQLAlchemy `text()` quirk.
**Warning signs:** The bug does not surface in unit tests that mock `db.execute`; it only appears against a real Postgres connection. Any new/changed control-DB `UPDATE`/`INSERT` using `sa_text` with a JSONB cast must be exercised against a real Neon connection before being considered done.

### Pitfall 2: `eval_scenarios.source` CHECK constraint will reject OPS-11/OPS-14 inserts
**What goes wrong:** `INSERT INTO eval_scenarios (..., source, ...) VALUES (..., 'production', ...)` raises `psycopg2.errors.CheckViolation` because the live constraint (`0005_verified_qa_eval_scenarios.py:79-80`) only allows `'generated'`/`'mined'`.
**Why it happens:** The constraint was written for M6 (Eval System) before OPS-11/OPS-14's new source values existed.
**How to avoid:** The Wave 3 (or Wave 4) migration must `ALTER TABLE eval_scenarios DROP CONSTRAINT eval_scenarios_source_check` (verify actual constraint name via `\d eval_scenarios` or an information_schema query, since Postgres auto-names unnamed inline `CHECK`s) then `ADD CONSTRAINT ... CHECK (source IN ('generated','mined','production','red_team'))`, in the SAME migration that adds `provenance`.
**Warning signs:** `store_scenarios`-style insert helpers that wrap the `cur.execute()` call in a broad `try/except` will swallow this error silently and report `0` rows inserted — always test the new source values against a live/local Postgres, not just a mock.

### Pitfall 3: Synchronous `_langfuse.flush()` on a per-call basis can hang for ~30s
**What goes wrong:** Calling `flush()` after every single Langfuse generation, on a synchronous request path, blocks for the full network timeout if Langfuse is unreachable (observed: 596s → 52s after removal in Phase 15's live-verification gate, `15-03-SUMMARY.md`).
**Why it happens:** `flush()` forces a synchronous network round-trip; on a hot path (every agent turn, OPS-04) this multiplies badly under any Langfuse latency or outage.
**How to avoid:** `validation_service._log_verdict` already calls `flush()` per-call and is the pattern this phase copies — but OPS-04's *agent-turn* trace is higher-volume than the three judge calls, so consider batching (flush once per turn after all Langfuse calls in that turn, not once per generation) or accepting Langfuse SDK's background flush thread instead of forcing synchronous flush on every write. At minimum, wrap in the same `try/except` + `_langfuse is None` guard already used, so a Langfuse outage never fails the turn.
**Warning signs:** Turn latency spikes correlated with Langfuse dashboard unavailability; this will not show up in unit tests (Langfuse client is mocked/absent in test envs).

### Pitfall 4: `agent_tools.py` ContextVars are per-task-context, but `retrieve_tool` runs in an executor thread for the DB calls
**What goes wrong:** Reading a NEW ContextVar (e.g., `job_id`) *inside* a `run_in_executor` lambda returns the default (empty string), not the value set by `build_tool_server`.
**Why it happens:** Documented in `agent_tools.py`'s own module docstring (lines 23-26, 129-136): executor threads do not inherit the asyncio context automatically. Every existing tool reads all needed ContextVars into local variables at the TOP of the async function body, then passes those locals (never `.get()` calls) into executor lambdas.
**How to avoid:** When adding `_job_id_var` for OPS-05/06/07's write path, read it into a local (`job_id = _job_id_var.get()`) alongside `conn_str`/`strategy` at the top of `retrieve_tool`, before any `run_in_executor` call — exactly like the existing `conn_str`/`strategy` locals at lines 263-265.
**Warning signs:** `retrieval_metrics.job_id` rows come back empty/NULL in testing even though `build_tool_server` was clearly called with a real `job_id`.

### Pitfall 5: `Job` (control DB) has no `conversation_id` column — do not rely on it
**What goes wrong:** `scenario_service.mine_production_scenarios` (line 362) queries `SELECT conversation_id FROM jobs WHERE id = :job_id` — this column does not exist on the `Job` model (`app/models/job.py`, confirmed: `id, tenant_id, agent_id, kind, status, error, started_at, finished_at, created_at` only). The query either errors or (if the DB has a stray column from manual DDL) silently returns unrelated data.
**Why it happens:** Historical drift — AGENT-MGMT-GAPS.md explicitly calls this out ("cannot recover the question — conversation_id linkage missing — its own comments, lines 296-307").
**How to avoid:** OPS-09/OPS-11 must source `conversation_id` from the `agent.response` event's JSONB `payload` in `job_events` (control DB), which reliably contains it (`agent.py:671-681`), not from a `jobs` table column.
**Warning signs:** Trace listing/promote-to-scenario silently returns empty/broken results for real production failures — this bug already causes exactly that symptom in the existing miner.

### Pitfall 6: `worker_pool="solo"` on Windows dev means genuinely sequential task execution
**What goes wrong:** New Celery tasks (`check_index_staleness`, `promote_trace_to_scenario`, sampled `run_retrieval_faithfulness`) added to the same `runtime` queue as `run_agent_turn`/validators will queue behind (or in front of) live agent turns on a local dev box, since `celery_app.py` sets `worker_pool="solo"` for `ENVIRONMENT in ("development", "test")` — no true concurrency.
**Why it happens:** Windows `billiard` prefork pool has a known pipe-handle bug (documented in `celery_app.py`'s own module docstring); `solo` is the accepted local-dev tradeoff.
**How to avoid:** Not a correctness bug, but a planning/verification consideration — when live-verifying OPS-08/09/11 timing behavior locally, expect queue serialization; do not write a test that asserts sub-second task pickup under concurrent load on the dev box. `check_index_staleness` (Wave 2) should route to the `pipeline` queue specifically to avoid contending with `runtime`-queue agent-turn traffic even under `solo`.

### Pitfall 7: `retrieve_tool`'s current filters parameter is a documented no-op
**What goes wrong:** If OPS-05/06 instrumentation tries to record "metadata filters applied" as a retrieval-health signal, there is currently nothing real to record — `agent_tools.py:290-301` explicitly logs a warning and ignores any `filters` value supplied (`TODO-RET-01`, tracked as a separate M5 follow-up, not in scope here).
**Why it happens:** Filters were never wired to enforcement.
**How to avoid:** Do not surface a "filters applied" column/metric in `retrieval_metrics` that implies filtering is active — it isn't. If this needs surfacing, it should say "not enforced" (per CONTEXT.md's honest-empty-state discipline), not silently omit or fabricate a value.
**Warning signs:** none observable at runtime; this is a documentation-honesty pitfall, not a crash risk.

## Code Examples

### `run_agent_turn`'s existing `ResultMessage` handling (OPS-01/04 write point)
```python
# Source: apps/api/app/worker/tasks/runtime/agent.py:347-374 (current — logged only)
elif isinstance(msg, ResultMessage):
    sdk_session_id_out = msg.session_id
    log.info(
        "_run_sdk_turn.result",
        job_id=job_id,
        subtype=msg.subtype,
        is_error=msg.is_error,
        num_turns=msg.num_turns,
        total_cost_usd=msg.total_cost_usd,
        stop_reason=msg.stop_reason,
        api_error_status=msg.api_error_status,
        response_length=len(response_text),
    )
```
This entire dict (`num_turns`, `total_cost_usd`, `stop_reason`, `is_error`, plus wall-clock latency measured around the `asyncio.run(asyncio.wait_for(...))` call at line 607-620, plus `escalated`/`tool_count` already available as local variables at line 622-627) is the exact `turn_metrics` row for OPS-01. `_run_sdk_turn` must additionally `return` these fields (it currently discards `msg.total_cost_usd`/`msg.num_turns`/`msg.stop_reason` after logging — only `sdk_session_id`, `response_text`, `tool_calls_log`, `escalated`, `escalation_reason`/`escalation_context` are returned at lines 376-383). **Required change:** add `total_cost_usd`, `num_turns`, `stop_reason` to `_run_sdk_turn`'s return dict.

### The IDOR/conn_str endpoint pattern to copy exactly
```python
# Source: apps/api/app/api/v1/evals.py:94-156 — copy this shape for every new GET route
agent = await db.get(Agent, agent_id)
if agent is None:
    raise HTTPException(status_code=404, detail="Agent not found")
if agent.tenant_id != tenant.id:
    raise HTTPException(status_code=404, detail="Agent not found")  # 404, not 403 — no existence leak
if not agent.neon_connection_string:
    raise HTTPException(status_code=404, detail="Agent database not provisioned")
conn_str = fernet_decrypt(agent.neon_connection_string)
rows = await asyncio.to_thread(_query_tenant_db_sync, conn_str, SQL, params)
```

### The Celery task skeleton to copy exactly (acks_late + idempotent + tenant_id-only args)
```python
# Source: apps/api/app/worker/tasks/runtime/deployment.py:53-61 — shape for check_index_staleness / promote_trace_to_scenario
@celery_app.task(
    bind=True, acks_late=True, max_retries=2, default_retry_delay=30,
    queue="runtime",  # or "pipeline" for check_index_staleness
    name="app.worker.tasks.runtime.<module>.<task_name>",
)
def new_task(self, agent_id: str) -> dict:
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        conn_str = fernet_decrypt(agent.neon_connection_string)  # decrypt at runtime, never in args
    # idempotency guard: check for an existing row/marker before doing work
    ...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `ResultMessage.total_cost_usd`/`num_turns` logged via `structlog` only | Persisted to `turn_metrics` + surfaced via `GET /agents/{id}/metrics` | This phase (OPS-01) | Enables containment/deflection/cost KPIs that currently do not exist anywhere in the product |
| Findings as a JSONB blob inside `red_team_runs.findings` | First-class `red_team_findings` rows with `severity`/`status` | This phase (OPS-14) | Enables per-finding triage-to-closure tracking and the contain→file-scenario flywheel; `_fetch_red_team_summary_sync` must be updated to query the new table |
| `patch_agent` overwrites `agents.soul*` columns in place, no history | `prompt_versions` immutable rows + canary label + rollback | This phase (OPS-16) | First time soul edits are non-destructive; canary requires threading version selection into `run_agent_turn`'s dispatch path |

**Deprecated/outdated:**
- `pg_search`/pgbm25: deprecated on Neon March 2026 (CLAUDE.md rule 8) — not used anywhere in this codebase already; no migration risk, just a constraint on any new BM25 code (there is none needed — reuse `bm25_search`).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `prompt_versions` should live in the control DB (not tenant DB) because `agents.soul*` columns are control-DB | Summary, Architectural Responsibility Map | If wrong, every prompt-version read/write needs an extra tenant-DB round-trip and a redundant `agent_id`/tenant-scoping check that control DB already provides via `tenant_id` on `agents` — moderate rework, not a correctness bug, but should be confirmed with the user/planner before Wave 4 is planned since CONTEXT.md's blanket "new tables start at 0009" statement assumes tenant DB |
| A2 | Sampling rate for OPS-07 Ragas faithfulness (5–10%) is a reasonable default, not a locked number | Anti-Patterns, Don't Hand-Roll | If the actual desired sample rate differs, `settings.RETRIEVAL_FAITHFULNESS_SAMPLE_RATE` is a one-line config change — low risk, but DOMAIN-NOTES only gives a range ("1–10%"), not a locked value; CONTEXT.md doesn't lock it either |
| A3 | `check_index_staleness` should route to the `pipeline` queue, not `runtime` | Pitfall 6, Architectural Responsibility Map | If routed to `runtime` instead, it contends with live agent-turn traffic on the same worker under `worker_pool="solo"` in dev — a queue-routing config change, not a data-model change, if this assumption is wrong |
| A4 | `retrieval_metrics` should be written once per `retrieve_tool` call (not aggregated once per turn) | Pattern 1, System Architecture Diagram | If the planner prefers one row per turn (aggregating multiple retrieve calls), the write site moves from `agent_tools.py` to `agent.py`'s post-turn block and loses direct access to per-call rank data unless `tool_calls_log` is extended to carry structured retrieval metadata (not just the truncated string it holds today) |
| A5 | `eval_scenarios_source_check` is the actual Postgres-assigned constraint name (unnamed inline CHECK) | Pitfall 2 | If Postgres assigned a different auto-generated name, the `DROP CONSTRAINT` in the migration will fail at apply time — the migration author must verify the exact name against a live/local tenant DB (`\d eval_scenarios`) before finalizing the migration file, not just from reading the 0005 migration source |

## Open Questions

1. **Should `prompt_versions` canary selection be sticky per-conversation or re-rolled per-turn?**
   - What we know: OPS-16 says "% routing chosen at turn dispatch in `run_agent_turn`" — this reads as per-turn.
   - What's unclear: whether a customer mid-conversation should stay on the same variant for consistency (avoids a user seeing the agent's "personality" shift mid-conversation) or whether per-turn re-rolling is acceptable/intended for faster canary sample accumulation.
   - Recommendation: default to per-conversation stickiness (roll once, store `prompt_version_id` on the conversation's `metadata` JSONB alongside `sdk_session_id`, reuse on subsequent turns) unless the planner/user prefers strict per-turn — this is a Claude's-discretion item per CONTEXT.md, not locked, and should be confirmed in discuss-phase or flagged for the planner to decide with a one-line rationale.

2. **Does `turn_metrics` need a `prompt_version_id` column added in Wave 1's migration (pre-emptively) or added later via a Wave 4 `ALTER TABLE`?**
   - What we know: canary analysis (comparing cost/latency/containment across prompt versions) needs `turn_metrics` and `prompt_versions` correlated.
   - What's unclear: whether the planner wants to avoid a later `ALTER TABLE turn_metrics ADD COLUMN` (Wave 4 migration touching a Wave 1 table) by adding the (nullable, unused-until-Wave-4) column upfront.
   - Recommendation: add it nullable in the Wave 1 migration (`0009`) — costs nothing, avoids a cross-wave migration dependency, and `IF NOT EXISTS`-guarded `ALTER TABLE` is trivially safe either way per the established convention.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Local PostgreSQL / Neon connectivity | All new tables, all endpoints, all tasks | Assumed ✓ (existing phases 1-20 all depend on this) | — | — |
| Redis (`redis-server`) | Celery broker/backend for new tasks; SSE for widget feedback route (none needed — feedback is not SSE) | Assumed ✓ | — | — |
| `ANTHROPIC_API_KEY` | OPS-07 Ragas faithfulness (Haiku via InstructorLLM); OPS-16 uses no new LLM calls | Assumed ✓ (already required by existing validators/eval system) | — | — |
| `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` | OPS-04 trace emission | **Optional at runtime** — `validation_service.py`'s `_langfuse` is `None` if unset, and every Langfuse call site already no-ops gracefully | — | OPS-04's trace write becomes a no-op; `turn_metrics` (the DB row) is unaffected since it's a separate write path from the Langfuse call |
| A local/live tenant Neon DB with schema migrated to tenant-`0008`+ | All Wave 1-4 migrations | Depends on dev environment state — verify `alembic_tenant` is at head 0008 before adding 0009 | — | Run `alembic -c alembic_tenant.ini upgrade head` first if not |

**Missing dependencies with no fallback:** none identified — this phase can be fully built and unit-tested without any live external service.

**Missing dependencies with fallback:** Langfuse (graceful no-op already established pattern).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio 1.3.0 [VERIFIED: apps/api/pyproject.toml:60-61] |
| Config file | `apps/api/pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `cd apps/api && pytest tests/unit -x -q` |
| Full suite command | `cd apps/api && pytest tests/unit tests/integration -q` (E2E/live tests are env-gated, see below) |

### Existing test conventions to follow (confirmed from `tests/unit/test_eval_routes.py`, `tests/integration/test_eval_e2e.py`)
- **Unit tests** for endpoints: mock `get_current_tenant`/`get_async_db` dependency overrides, `MagicMock(spec=Agent)`/`MagicMock(spec=Tenant)`, assert IDOR 404s and happy-path response shapes. No real DB.
- **Integration/E2E tests** for Celery tasks and Ragas/Langfuse-touching code: gated behind an env flag (`EVAL_E2E_ENABLED=1` is the existing precedent for eval; use analogous flags per capability, e.g. `RETRIEVAL_METRICS_E2E_ENABLED=1`, `PROMPT_VERSION_E2E_ENABLED=1`) via `pytestmark = pytest.mark.skipif(os.environ.get("X") != "1", ...)` at module level — entire module skips cleanly in CI without live keys.
- **Live-DB verification gates** (mirroring Phase 13/14/15/16 precedent in STATE.md): migration roundtrip, real Langfuse trace appearing in the dashboard, and a real Ragas call against Anthropic are all "human_needed"/deferred-to-live-gate items, not blocking automated CI.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| OPS-01 | `turn_metrics` row written with correct cost/latency/escalated/tool_count | unit (mock SDK `ResultMessage`) + integration (real local Postgres insert) | `pytest tests/unit/test_agent_turn_metrics.py -x` | ❌ Wave 0 |
| OPS-02 | `POST /widget/agents/{id}/feedback` inserts `message_feedback`, validates rating enum | unit | `pytest tests/unit/test_widget_feedback.py -x` | ❌ Wave 0 |
| OPS-03 | `GET /agents/{id}/metrics` aggregates correctly, 404 on IDOR | unit | `pytest tests/unit/test_metrics_routes.py -x` | ❌ Wave 0 |
| OPS-04 | Langfuse trace call is attempted, no-ops gracefully when keys absent, never raises into the turn | unit (mock `_langfuse`) | `pytest tests/unit/test_agent_turn_langfuse.py -x` | ❌ Wave 0 |
| OPS-05/06 | `retrieval_metrics` row written from `retrieve_tool` with correct scores/ranks | unit (mock psycopg2) + integration | `pytest tests/unit/test_retrieval_metrics.py -x` | ❌ Wave 0 |
| OPS-07 | Sampled faithfulness task computes and updates `retrieval_metrics`; sample-rate gating logic | unit | `pytest tests/unit/test_retrieval_faithfulness_task.py -x` | ❌ Wave 0 |
| OPS-08 | `check_index_staleness` correctly flags stale docs + model drift; idempotent | unit + integration | `pytest tests/unit/test_index_staleness.py -x` | ❌ Wave 0 |
| OPS-09/10 | Trace listing joins control+tenant correctly; grade transitions enforce `filed` irrevocability | unit | `pytest tests/unit/test_bench_routes.py -x` | ❌ Wave 0 |
| OPS-11/12 | `promote_trace_to_scenario` inserts with correct provenance; idempotent on retry | unit + integration | `pytest tests/unit/test_promote_trace.py -x` | ❌ Wave 0 |
| OPS-13/14 | Coverage rollup query correctness; finding severity/status transitions | unit | `pytest tests/unit/test_redteam_programme.py -x` | ❌ Wave 0 |
| OPS-15 | Live critical finding → `recommendation='block'` → `POST /approve-deployment` → 422 | integration | `pytest tests/integration/test_deploy_gate_redteam.py -x` | ❌ Wave 0 |
| OPS-16 | Version created on `patch_agent`; canary weighted-pick distribution; rollback restores without deleting history | unit + integration | `pytest tests/unit/test_prompt_versions.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/unit -k <touched module> -x -q`
- **Per wave merge:** `pytest tests/unit tests/integration -q` (integration tests that need live services stay env-gated/skipped)
- **Phase gate:** Full suite green before `/gsd-verify-work 21`; live-service items (Langfuse trace visible in dashboard, live Ragas/Anthropic call, live Postgres migration roundtrip on a real tenant Neon project) explicitly deferred to a human-verify live gate, mirroring the Phase 13/14/15/16 precedent already established in `.planning/STATE.md`.

### Wave 0 Gaps
- [ ] `tests/unit/test_agent_turn_metrics.py`, `test_widget_feedback.py`, `test_metrics_routes.py`, `test_agent_turn_langfuse.py` — Wave 1
- [ ] `tests/unit/test_retrieval_metrics.py`, `test_retrieval_faithfulness_task.py`, `test_index_staleness.py` — Wave 2
- [ ] `tests/unit/test_bench_routes.py`, `test_promote_trace.py` — Wave 3
- [ ] `tests/unit/test_redteam_programme.py`, `tests/integration/test_deploy_gate_redteam.py`, `test_prompt_versions.py` — Wave 4
- [ ] No new shared fixtures required — existing `conftest.py` (env-var bootstrap) and the `MagicMock(spec=Agent)`/`MagicMock(spec=Tenant)` helper pattern from `test_eval_routes.py` cover all new routes
- [ ] Framework install: none — pytest/pytest-asyncio already installed

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes (widget feedback route) | Reuse existing widget JWT (`validate_widget_jwt`) — OPS-02's feedback route must NOT be unauthenticated the way `/widget/{id}/config` is; it should require the same Bearer JWT as `/widget/{id}/chat` |
| V3 Session Management | no new session concept | N/A |
| V4 Access Control | yes (every new admin-facing GET/POST route) | The `evals.py`/`red_team.py` IDOR pattern (`agent.tenant_id == tenant.id`, 404-not-403) copied verbatim for every new route |
| V5 Input Validation | yes | Pydantic request/response schemas for every new endpoint body (grade enum `filed|held|dismissed`, feedback rating enum, canary percent bounds 0-100) — mirror `AgentSoulUpdate`'s validation style |
| V6 Cryptography | yes (indirect) | No new crypto — reuses existing `fernet_decrypt`/`neon_connection_string` pattern; no new secrets introduced |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| IDOR on any new `GET /agents/{id}/...` route (guessing another tenant's agent UUID) | Tampering / Information Disclosure | `agent.tenant_id == tenant.id` check returning 404 (not 403) — established pattern, must be copied for every one of the ~10 new routes |
| Widget feedback route accepting unauthenticated writes and being used to pollute `message_feedback` at scale | Tampering / Denial of Service | Require the same Bearer JWT + rate limit pattern already used on `POST /widget/{id}/chat` (60 req/min per agent_id via Redis INCR) |
| `promote_trace_to_scenario` / grade endpoints allowing a filed trace to be un-filed (bypassing the TERRARIUM "irrevocable" law) | Tampering | Enforce at the SQL/service layer: `UPDATE ... WHERE status != 'filed'` guard, never allow a state transition FROM `filed` |
| Canary routing accidentally serving an unapproved/draft prompt version to production traffic | Tampering (of agent behavior) | `prompt_versions` selection query must filter `WHERE label IN ('production', 'canary')` explicitly — a version with no label (draft) must never be selectable at turn dispatch |
| `check_index_staleness` / `promote_trace_to_scenario` tasks passing `conn_str` in Celery args (violates CLAUDE.md rule 4) | Information Disclosure (Redis broker is not a hardened secrets store) | Every new task takes only `agent_id`; decrypt `neon_connection_string` from control DB at task-body runtime, exactly like every existing task |

## Sources

### Primary (HIGH confidence — read directly from the working codebase in this session)
- `apps/api/app/worker/tasks/runtime/agent.py` — `run_agent_turn`, `_run_sdk_turn`, `ResultMessage` handling, citation extraction, celery_chain dispatch
- `apps/api/app/worker/tasks/runtime/deployment.py` + `apps/api/app/services/deployment_service.py` — `_fetch_red_team_summary_sync`, `run_deployment_checklist`, `DeploymentReport`
- `apps/api/app/services/agent_tools.py` — `retrieve_tool`, `build_tool_server`, ContextVar pattern, executor-thread caveat
- `apps/api/app/services/retrieval_service.py` — `rrf_fuse`, `bm25_search`, `vector_search`, `rerank`
- `apps/api/app/services/validation_service.py` — Langfuse v4 pattern (`_log_verdict`), Haiku judge pattern
- `apps/api/app/services/eval_service.py` — Ragas 0.4.x pattern (`run_ragas_eval`)
- `apps/api/app/services/scenario_service.py` — cross-DB correlation pattern, `store_scenarios`, the `Job.conversation_id` bug
- `apps/api/app/worker/tasks/runtime/validators.py` — `run_gatekeeper`/`run_auditor`/`run_strategist`, `job_events` payload shapes
- `apps/api/app/api/v1/evals.py`, `red_team.py`, `agents.py`, `widget.py` — IDOR pattern, `patch_agent`, widget auth pattern
- `apps/api/app/worker/celery_app.py` — queue topology, task routing, `worker_pool` Windows note
- `apps/api/app/models/agent.py`, `job.py`, `checklist_run.py` — control-DB ORM schema
- `apps/api/alembic_tenant/versions/0001,0002,0005,0006,0008` — tenant-DB migration convention + current schema (confirms `eval_scenarios` CHECK constraint, confirms tenant head = 0008)
- `apps/api/alembic/versions/0011,0016` — control-DB migration convention (confirms control head = 0016, confirms raw-SQL `IF NOT EXISTS` convention applies to control DB too)
- `apps/api/pyproject.toml` — pinned versions (`langfuse>=4.0.0,<5.0.0`, `ragas>=0.4.0,<0.5.0`, `psycopg2-binary==2.9.12`, `alembic==1.18.4`, `pytest-asyncio==1.3.0`)
- `apps/api/tests/unit/test_eval_routes.py`, `tests/integration/test_eval_e2e.py` — existing test conventions

### Secondary (MEDIUM confidence)
- `.planning/STATE.md` — confirmed the `:r::jsonb` bug and its `CAST(:r AS JSONB)` fix, and the established pattern of deferring live-service verification to a human-verify gate across Phases 12-16

### Tertiary (LOW confidence)
- None — this research was conducted entirely against the live codebase; no web search was required since the phase introduces no new external technology.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages, all versions confirmed from `pyproject.toml`
- Architecture: HIGH — every write path and pattern was located and read in the actual source, not inferred
- Pitfalls: HIGH — five of seven pitfalls are documented, previously-hit bugs from this exact codebase's git history (`STATE.md`), not speculative

**Research date:** 2026-07-15
**Valid until:** 30 days (stable — no external API surface is changing; codebase is the source of truth and was read directly)
