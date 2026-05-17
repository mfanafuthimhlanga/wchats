---
phase: 03
slug: hybrid-retrieval
status: verified
threats_open: 0
asvs_level: 1
created: 2026-05-17
---

# Phase 03 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Alembic CLI → control DB | Migration runs with admin DB credentials; ADD COLUMN is DDL | DDL commands (low sensitivity) |
| Settings env vars → application | COHERE_API_KEY is an optional secret; absent = no Cohere fallback | API key (high sensitivity) |
| query_text → psycopg2 | User query string passed as parameterized SQL only | User query text (medium) |
| query_vector → psycopg2 | Embedding vector stringified as str(list); numeric, no injection risk | Float vector (low) |
| Voyage API → service | External API call; response shapes validated by namedtuple access | Query text + rerank results |
| Cohere API → service | External fallback API call; only invoked on Voyage exception | Query text + rerank results |
| Celery message → task | job_id, agent_id, query arrive from Redis; no secrets in task args | Job IDs + query text |
| task → control DB | Agent and Job fetched by ID; tenant ownership enforced in route | Agent/Job records |
| task → tenant DB | Connection string fetched encrypted, decrypted via fernet_decrypt() at runtime | Chunks + embeddings |
| task → Voyage API | query text sent to Voyage embed + rerank | Query text (medium) |
| HTTP client → POST /agents/{id}/query | Untrusted query string from caller; validated by Pydantic QueryRequest | Query text (medium) |
| Route → Celery | Only (job_id, agent_id, query) dispatched — no connection string, no API key | Job context (low) |
| Route → control DB | Agent ownership check: Agent.tenant_id == tenant.id AND deleted_at IS NULL | Agent record lookup |
| Test fixtures → local Postgres | Integration tests use local test DB; no real Neon project accessed | Synthetic test data (none) |
| E2E guard → Voyage API | Real Voyage API only called when RETRIEVAL_E2E_ENABLED=1 | Real query text |
| Notebook → running API | Notebook calls real API; credentials from .env (never hardcoded) | Credentials + query text |
| demo_m3.sh → running API | Shell script reads API_KEY from env var; no credentials in script | API key (env-var scoped) |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-03-01 | Information Disclosure | Settings.COHERE_API_KEY | mitigate | Field is `Optional[str] = None`; `__repr__` suppressed on Settings class — key never logged | closed |
| T-03-02 | Tampering | 0003 migration downgrade | accept | `downgrade()` uses `DROP COLUMN IF EXISTS` — idempotent; only runnable by operator with DB access | closed |
| T-03-03 | Elevation of Privilege | agents.retrieval_strategy JSONB default | accept | Default is `'{}'::jsonb`; parsed via `RetrievalStrategy.model_validate()` which ignores unknown fields | closed |
| T-03-04 | Injection | bm25_search — plainto_tsquery | mitigate | `plainto_tsquery` normalizes input — no arbitrary SQL execution; zero string interpolation | closed |
| T-03-05 | Injection | vector_search — query_vector | accept | `query_vector` is `str(list[float])` — purely numeric; no SQL injection risk | closed |
| T-03-06 | Information Disclosure | rerank — log statements | mitigate | Only `error_type` (exception class name) logged, never query text or vector values | closed |
| T-03-07 | Information Disclosure | COHERE_API_KEY in _cohere_rerank | mitigate | Key read from settings only; never passed to logs; `cohere` imported lazily inside `except` block | closed |
| T-03-08 | Tampering | RetrievalStrategy JSONB injection | mitigate | `model_validate` with `ConfigDict(extra="ignore")` — unknown keys silently dropped; numeric fields type-validated | closed |
| T-03-09 | Information Disclosure | retrieve_and_rank — log statements | mitigate | Full query text never logged; only `job_id`, `agent_id`, and counts emitted | closed |
| T-03-10 | Information Disclosure | retrieve_and_rank — return value | accept | Task returns `{}` only; no connection strings, query vectors, or API keys in return | closed |
| T-03-11 | Tampering | idempotency guard — job_events check | accept | `SELECT 1` check is read-only; only skips re-execution, never silently corrupts state | closed |
| T-03-12 | Denial of Service | rrf_fuse — large fused candidate set | accept | `final_k` bounded by `RetrievalStrategy` (default 5); Voyage `top_k` bounded by `final_k` | closed |
| T-03-13 | Spoofing | agent_id from task args | mitigate | `db.get(Agent, agent_id)` fetches from control DB — agent must exist; ownership enforced at route dispatch | closed |
| T-03-14 | Information Disclosure | POST query — log body.query | mitigate | `body.query` never logged in `query.py` — only `job_id` and `agent_id` emitted | closed |
| T-03-15 | Elevation of Privilege | Cross-tenant query dispatch | mitigate | `Agent.tenant_id == tenant.id` filter ensures agent belongs to authenticated tenant before dispatch | closed |
| T-03-16 | Tampering | QueryRequest.filters field | accept | `filters` accepted but not applied in M3 (empty list default); enforced in M4+ | closed |
| T-03-17 | Denial of Service | GET /queries — unbounded result | mitigate | `LIMIT 50` on query jobs list; no full-table scan possible | closed |
| T-03-18 | Injection | QueryRequest.query min_length=1 | mitigate | Pydantic `min_length=1` rejects empty queries before dispatch; psycopg2 parameterized SQL prevents injection | closed |
| T-03-19 | Tampering | test_bm25_search_uses_tsvector — source inspection | accept | Source inspection test verifies pg_search/pgbm25 never introduced; runs in CI | closed |
| T-03-20 | Information Disclosure | test mock data — no real vectors/queries | accept | Test data uses `[0.1]*1024` placeholder and "test query" — no real user data in tests | closed |
| T-03-21 | Information Disclosure | Integration test — query text in assertions | accept | Test data uses "What is the refund policy?" placeholder — no real tenant data | closed |
| T-03-22 | Denial of Service | E2E test — real Voyage API calls | mitigate | `RETRIEVAL_E2E_ENABLED=1` guard prevents accidental CI execution; 60s timeout bounds runtime | closed |
| T-03-23 | Elevation of Privilege | test_post_query_wrong_tenant | mitigate | Test verifies 404 returned when agent belongs to different tenant — cross-tenant isolation confirmed | closed |
| T-03-24 | Information Disclosure | demo notebook secrets | mitigate | All credentials read from `os.environ` / `.env`; `.env` in `.gitignore`; no hardcoded values | closed |
| T-03-25 | Information Disclosure | demo_m3.sh — API_KEY | mitigate | `API_KEY` read from env var only; never echoed to stdout | closed |
| T-03-26 | Denial of Service | notebook polling — no timeout | mitigate | `poll_query_complete()` has `timeout=60` parameter with `TimeoutError` raise | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-03-01 | T-03-02 | Alembic downgrade is an operator-only action; DROP COLUMN IF EXISTS is idempotent and safe | project-owner | 2026-05-17 |
| AR-03-02 | T-03-03 | JSONB default `'{}'` produces empty RetrievalStrategy; `model_validate(extra="ignore")` neutralizes any injected keys | project-owner | 2026-05-17 |
| AR-03-03 | T-03-05 | `str(list[float])` is purely numeric — SQL injection is structurally impossible | project-owner | 2026-05-17 |
| AR-03-04 | T-03-10 | Task contract (CLAUDE.md rule) mandates `return {}`; no sensitive data path exists | project-owner | 2026-05-17 |
| AR-03-05 | T-03-11 | Idempotency guard is a read-only SELECT; only consequence is early-return on duplicate job | project-owner | 2026-05-17 |
| AR-03-06 | T-03-12 | `final_k` default of 5 caps Voyage rerank calls; strategy is tenant-configurable but bounded | project-owner | 2026-05-17 |
| AR-03-07 | T-03-16 | `filters` field is future-scoped (M4+); accepted as no-op in M3 with empty list default | project-owner | 2026-05-17 |
| AR-03-08 | T-03-19 | CI source inspection test provides continuous enforcement against pg_search/pgbm25 introduction | project-owner | 2026-05-17 |
| AR-03-09 | T-03-20 | Test data is synthetic; no real PII or tenant data enters test fixtures | project-owner | 2026-05-17 |
| AR-03-10 | T-03-21 | Integration test query is a generic placeholder; real tenant data never used in test suite | project-owner | 2026-05-17 |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-05-17 | 26 | 26 | 0 | gsd-secure-phase (short-circuit: all threats authored at plan-time with dispositions) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-05-17
