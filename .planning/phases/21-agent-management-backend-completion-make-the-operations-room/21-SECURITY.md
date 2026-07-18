---
phase: 21
slug: agent-management-backend-completion-make-the-operations-room
status: secured
# threats_open = count of OPEN threats at or above workflow.security_block_on severity (the blocking gate)
threats_open: 0
asvs_level: 1
created: 2026-07-18
---

# Phase 21 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Celery task → tenant Neon DB | `conn_str` decrypted at runtime inside the task; never a task argument | Connection string (secret), metrics rows |
| agent turn → Langfuse (external) | External observability; an outage must never fail the served turn | Trace/generation spans, token + cost counts |
| admin client → `/metrics`, `/retrieval-health`, `/traces`, `/red-team/*`, `/prompt-versions` | Tenant-scoped, IDOR-guarded (404-not-403) | Agent operational data, failing traces, findings |
| public widget → `POST /widget/agents/{id}/feedback` | Untrusted browser; JWT-scoped write | Thumb rating, CSAT score, message id |
| `retrieve_tool` (task closure) → tenant DB | `conn_str` already in scope; retrieval scores must not cross back to the SDK | Rank/score telemetry |
| turn dispatch → `prompt_versions` (control DB) | Canary selection must never serve an unapproved draft | Prompt version rows, labels |

---

## Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation | Status |
|-----------|----------|-----------|----------|-------------|------------|--------|
| T-21-01-01 | Information Disclosure | turn_metrics write path | high | mitigate | `agent.py:723` runtime `fernet_decrypt`; `conn_str` absent from `run_agent_turn.run` params (live signature check) | closed |
| T-21-01-02 | Denial of Service | Langfuse flush on hot turn path | medium | mitigate | `agent.py:438-467` `_langfuse is None` no-op, single `flush()`, try/except at call site | closed |
| T-21-01-03 | Availability | telemetry INSERT failure | low | mitigate | `agent.py:973-992` `_write_turn_metrics` in its own try/except after the `agent.response` emit | closed |
| T-21-01-SC | Tampering (supply chain) | package installs | n/a | n/a | No new packages this phase; no unrelated dependency changes in the diff | closed |
| T-21-02-01 | Information Disclosure | `GET /metrics` | high | mitigate | `metrics.py:80-88` `agent.tenant_id != tenant.id` → 404 (not 403) | closed |
| T-21-02-02 | Spoofing | widget feedback route | high | mitigate | `widget.py:794` `validate_widget_jwt(...)` before any DB work | closed |
| T-21-02-03 | Denial of Service | feedback flooding | medium | mitigate | `widget.py:800-809` Redis INCR `rate:feedback:{agent_id}:{bucket}`, 429 over 60/min | closed |
| T-21-02-04 | Tampering | rating/csat injection | low | mitigate | `schemas/widget.py:118-121` `Literal["up","down"]` + `ge=1, le=5`; DB CHECK in migration 0009 | closed |
| T-21-03-01 | Information Disclosure | honest metric surface | low | mitigate | `agent_tools.py:453-467` `metrics_row` carries no `filters`/`filters_applied` key | closed |
| T-21-03-02 | Availability | metric write failure inside the tool | low | mitigate | `retrieval_metrics_service.py:74-82` `write_retrieval_metrics` never raises | closed |
| T-21-03-03 | Correctness | BM25 baseline via deprecated pg_search | medium | mitigate | `retrieval_service.py:239-242` native `ts_rank_cd`/`to_tsvector`; zero `pg_search`/`pgbm25` hits repo-wide (CLAUDE.md rule 8) | closed |
| T-21-04-01 | Denial of Service / cost | Ragas on every turn | medium | mitigate | `retrieval_eval.py:311-315` sample gate evaluated before the Ragas call at :333 | closed |
| T-21-04-02 | Denial of Service | staleness contends with live turns | medium | mitigate | `staleness.py:190,235` `queue="pipeline"` on task and beat fan-out | closed |
| T-21-04-03 | Information Disclosure | task args | high | mitigate | `run_retrieval_faithfulness.run` params = `{agent_id, job_id}` only (live signature check) | closed |
| T-21-04-04 | Information Disclosure | `GET /retrieval-health` | high | mitigate | Shared IDOR block in `metrics.py` | closed |
| T-21-05-01 | Information Disclosure / Tampering | GET/POST traces routes | high | mitigate | `traces.py:71-73` IDOR + `bench_service.py:167-374` `_TRACE_OWNER_CHECK_SQL` → 404 before any write | closed |
| T-21-05-02 | Tampering | filed grade withdrawal | medium | mitigate | `bench_service.py:378` refuses transition FROM `filed` → `traces.py:155-156` 409 | closed |
| T-21-05-03 | Tampering | grade enum injection | low | mitigate | `traces.py:51` `Literal["filed","held","dismissed"]` → 422 | closed |
| T-21-06-01 | Information Disclosure | promote task args | high | mitigate | `promote_trace_to_scenario.run` params = `{agent_id, trace_id}` only (live signature check) | closed |
| T-21-06-02 | Tampering / Correctness | source CHECK violation | medium | mitigate | `0011_eval_scenarios_provenance.py:67-98` constraint DROP + re-ADD widened in one migration | closed |
| T-21-06-03 | Correctness | duplicate promotion on retry | low | mitigate | `bench.py:132-141` idempotency pre-check on `origin_trace_id` → `already_promoted` | closed |
| T-21-07-01 | Information Disclosure | `GET /red-team/programme` | high | mitigate | `red_team.py:101-109,175-183` IDOR pattern | closed |
| T-21-07-02 | Correctness | strategy double-write on retry | low | mitigate | `red_team.py:393-395` `ON CONFLICT (attack_vector) DO NOTHING` | closed |
| T-21-07-03 | Availability | new writes break the red-team run | medium | mitigate | Writes on existing `_agents_conn` inside existing try; `acks_late=True` at :141,184 | closed |
| T-21-08-01 | Tampering / Elevation | deploy gate bypass (OPS-15) | critical | mitigate | `deployment_service.py:189-229` reads `red_team_findings WHERE status='open'`; `deployment_blocked = critical > 0`; real-Postgres integration test asserts 422→200 across containment | closed |
| T-21-08-02 | Information Disclosure | contain endpoint | high | mitigate | `red_team.py:310-314` IDOR pattern | closed |
| T-21-08-03 | Correctness | `:r::jsonb` crash on a real DB | medium | mitigate | `grep -c ":r::jsonb" deployment_service.py` = 0 | closed |
| T-21-08-04 | Tampering | second scenario-insert path | low | mitigate | `red_team.py:35,394` calls shared `insert_provenance_scenario(..., source="red_team")` | closed |
| T-21-09-01 | Tampering (agent behavior) | canary serves a draft version | high | mitigate | `prompt_version_service.py:270` `label.in_(("production","canary"))` — a draft is structurally unselectable | closed |
| T-21-09-02 | Tampering | history overwrite / rollback deletes | medium | mitigate | `prompt_version_service.py:178-224` rollback only INSERTs + archives; no DELETE on any prior row | closed |
| T-21-09-03 | Information Disclosure | prompt-version routes | high | mitigate | `prompt_versions.py:70-72` IDOR pattern | closed |
| T-21-09-04 | Correctness | `:r::jsonb` crash on real control DB | medium | mitigate | `grep -c ":r::jsonb"` = 0 in `prompt_version_service.py` and `agent.py` | closed |
| T-21-09-05 | Availability | canary resolve fails a turn | medium | mitigate | `agent.py:249-274` `_resolve_turn_prompt_version` returns `(None, None)` on any failure → falls back to live agent soul | closed |

*Status: open · closed · open — below high threshold (non-blocking)*
*Severity: critical > high > medium > low — only open threats at or above `workflow.security_block_on` (high) count toward `threats_open`*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Gap-Closure Verified

`21-VERIFICATION.md` found `promote_trace_to_scenario` implemented but never dispatched — the grade→promote causal link was missing across the 21-05/21-06 wave seam. Independently confirmed fixed in code, not merely narrated: `apps/api/app/api/v1/traces.py:158-177` dispatches `promote_trace_to_scenario.apply_async(args=[str(agent_id), str(trace_id)])` on `grade == "filed"`, wrapped so a broker failure logs rather than fails the request. `pytest tests/unit/test_bench_routes.py -k promote` → 2 passed.

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|

No accepted risks. All 33 threats closed by mitigation.

---

## Residual Observations (informational — not counted in `threats_open`)

**LLM-as-gatekeeper on the deploy recommendation.** `deployment_service.py`'s `recommendation` field — which drives the `POST /approve-deployment` 422 — is ultimately produced by a Claude Sonnet tool call instructed via system prompt to block when `red_team_summary.deployment_blocked == True`. This design predates Phase 21 (Phase 8), and Phase 21's plan explicitly scoped out touching it ("only the signal source changes").

T-21-08-01's declared mitigation — that the *signal* is now sourced from the first-class `red_team_findings` table rather than a stale JSONB — is genuinely verified end-to-end including a real-Postgres integration test. That test necessarily reproduces the system prompt's rule deterministically rather than hitting the live LLM, so it proves the signal path but **not** LLM instruction-following reliability under adversarial pressure.

Worth a threat entry in a future phase that touches `deployment_service.py`. Not introduced by Phase 21 and not in its remit to close.

**Test-isolation fragility (non-security).** `test_retrieval_metrics.py` fails when run in the same session as files importing the real `claude_agent_sdk` — its `sys.modules` stub is shadowed by an earlier `agent_tools` import — but passes cleanly in isolation. Test-infrastructure fragility, not a broken mitigation; `retrieve_tool` source was read directly to confirm.

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-07-18 | 33 | 33 | 0 | gsd-security-auditor (ASVS L1, block_on: high) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log (none — all closed by mitigation)
- [x] `threats_open: 0` confirmed
