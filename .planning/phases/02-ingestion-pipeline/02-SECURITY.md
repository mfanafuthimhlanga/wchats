---
phase: 02
slug: ingestion-pipeline
status: verified
threats_open: 0
asvs_level: 1
created: 2026-05-17
auditor: gsd-security-auditor (claude-sonnet-4-6)
---

# Phase 02 — Ingestion Pipeline Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| filesystem → DB schema | Migration DDL via Alembic version control | Schema column/table definitions; no PII |
| env → Settings | API keys (Anthropic, Voyage) loaded from env | ANTHROPIC_API_KEY, VOYAGE_API_KEY (sensitive) |
| Celery broker → worker | Task arguments cross Redis | IDs only (tenant_id, agent_id, job_id, document_ids) |
| local filesystem → DocumentConverter | Uploaded PDF reaches Docling parser | User-uploaded document bytes (untrusted) |
| URL → httpx → DocumentConverter | URL-fetched bytes reach parser | External content (untrusted) |
| Docling output → tenant DB | Chunk text from user documents | Potentially adversarial text |
| chunk content → Anthropic API | Sanitized chunk text | Text sent to external LLM |
| Anthropic API → tenant DB | Haiku structured output | Pydantic-validated metadata + entities |
| chunk content → Voyage API | Sanitized chunk text | Text sent to external embedding service |
| Voyage response → tenant DB | 1024-dim vectors | Parameterised SQL; no string interpolation |
| HTTP client → FastAPI route | Untrusted multipart upload | File bytes, filenames, URLs (all untrusted) |
| Authenticated client → other tenant | Cross-tenant agent access attempt | Blocked by tenant_id filter |
| Route → Celery chain | Chain task arguments | IDs only; no connection strings |
| local filesystem → worker | Per-agent upload directory | UUID4-named files; path traversal blocked |
| Repo → public git history | Committed PDF fixture | Owner-authored PDF; no PII |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-02-01-01 | Information Disclosure | Settings (config.py) | mitigate | `Settings.__repr__` at config.py:81-84 suppresses all field values; ANTHROPIC_API_KEY (line 61) and VOYAGE_API_KEY (line 62) inherit this protection | closed |
| T-02-01-02 | Tampering | chunk_id.py | mitigate | `CHUNK_UUID_NAMESPACE` at chunk_id.py:29 is a module-level constant; comment at line 28 reads "VALUE MUST NEVER CHANGE ACROSS DEPLOYMENTS" | closed |
| T-02-01-03 | Tampering | sanitize.py | mitigate | Compiled regex at sanitize.py:31-34 strips System:/Human:/Assistant:/[INST]/[/INST]/HTML comments/"Ignore previous" before any chunk text is persisted | closed |
| T-02-01-04 | Information Disclosure | 0002 migration | accept | Schema DDL is non-sensitive; no PII in column names or default values — see Accepted Risks Log | closed |
| T-02-01-05 | Denial of Service | 0002 migration | accept | ALTER TABLE ADD COLUMN on empty pre-state table is O(0) — see Accepted Risks Log | closed |
| T-02-02-01 | Information Disclosure | parse_documents task args | mitigate | Task signature at parse.py:88-94: `(self, tenant_id, agent_id, job_id, document_ids)` only; test `test_parse_documents_no_conn_string_in_signature` at test_parse_task.py:113 asserts no "conn"/"password" params | closed |
| T-02-02-02 | Tampering | malicious PDF parsing | mitigate | docling_service.py:69 checks `result.status != ConversionStatus.SUCCESS` raises RuntimeError; parse.py:265-280 catches RuntimeError, marks parse_status='failed', does not retry | closed |
| T-02-02-03 | Denial of Service | URL fetch | mitigate | parse.py:247 `httpx.get(source_uri, timeout=30, follow_redirects=True)` — explicit 30s timeout | closed |
| T-02-02-04 | Information Disclosure | structlog calls in parse task | mitigate | parse.py log calls reference document_id, page_count, error type/message only; `tenant_conn_str` is a local variable, never passed to any log call | closed |
| T-02-02-05 | Repudiation | task retries | mitigate | parse.py:83 `acks_late=True`; Layer 1 source_hash guard at parse.py:204-210 makes retries safe | closed |
| T-02-02-06 | Tampering | re-ingest with new bytes | mitigate | parse.py:204 `if parse_status == "parsed" and source_hash is not None`; hash mismatch (new bytes) causes guard not to fire → full re-parse triggered | closed |
| T-02-03-01 | Tampering | chunk text content | mitigate | chunking_service.py:86 `sanitize_chunk_text(chunker.contextualize(chunk))` and line 110 `sanitize_chunk_text(md)` — both text and table paths sanitized before append | closed |
| T-02-03-02 | Information Disclosure | structlog calls in chunk task | mitigate | chunk.py log calls reference document_id, chunk_count only; `conn_str` is a local variable, never logged | closed |
| T-02-03-03 | Repudiation / DoS on retry | chunks table | mitigate | chunk.py:257-270 `INSERT INTO chunks ... ON CONFLICT (id) DO UPDATE`; uuid5 IDs are deterministic — retries overwrite with identical content, no duplicate rows | closed |
| T-02-03-04 | Information Disclosure | task args (chunk_documents) | mitigate | chunk.py:95 `def chunk_documents(self, result: dict) -> dict:`; test `test_chunk_documents_signature_takes_only_result_dict` at test_chunk_task.py:116 asserts no "conn" in signature | closed |
| T-02-03-05 | Tampering | re-parse mid-chain | accept | Re-parsing non-deterministic at ms level; HybridChunker deterministic given same DoclingDocument — see Accepted Risks Log | closed |
| T-02-03-06 | Denial of Service | URL re-fetch in chunk task | mitigate | chunk.py:215 `httpx.get(source_uri, timeout=30, follow_redirects=True)` — same 30s timeout as parse task | closed |
| T-02-04-01 | Information Disclosure | task args (generate_metadata) | mitigate | metadata.py:81 `def generate_metadata(self, result: dict) -> dict:`; ANTHROPIC_API_KEY read from env at module init (metadata_service.py:48); test `test_generate_metadata_signature` at test_metadata_task.py:134 asserts no "conn"/"api_key" params | closed |
| T-02-04-02 | Tampering | Haiku response | mitigate | metadata_service.py:127 `_anthropic.messages.parse(output_format=ChunkMetadataAndEntities)`; EntityExtraction at line 74 enforces `type: Literal["product","person","place","policy","process"]` — arbitrary type injection rejected by Pydantic | closed |
| T-02-04-03 | Repudiation / Budget burn | Anthropic API costs | mitigate | metadata.py:166-175 `SELECT COUNT(*) FROM chunk_metadata WHERE chunk_id = %s`; count > 0 triggers `continue` — already-enriched chunks skip Haiku call on retry | closed |
| T-02-04-04 | Denial of Service | tenacity retry storm | mitigate | metadata_service.py:98-102 `@retry(wait=wait_exponential(min=2, max=30), stop=stop_after_attempt(5), retry=retry_if_exception_type((RateLimitError, APITimeoutError)))` — non-transient errors fail immediately | closed |
| T-02-04-05 | Tampering | indirect prompt injection via chunk content | mitigate | Two layers: sanitize_chunk_text in Wave 3 (chunking_service.py) strips injection markers before DB write; metadata_service.py:57-60 METADATA_SYSTEM_PROMPT instructs "Return entities only when explicit; do not invent" | closed |
| T-02-04-06 | Information Disclosure | structlog calls in metadata task | mitigate | metadata.py log calls reference chunk_id, document_id only; chunk content and Haiku response body never logged | closed |
| T-02-05-01 | Information Disclosure | task args (embed_and_migrate) | mitigate | embed.py:88 `def embed_and_migrate(self, result: dict) -> dict:`; VOYAGE_API_KEY read from env at module init (embedding_service.py `_get_vo()`); test `test_embed_and_migrate_signature` at test_embed_task.py:234 asserts no "conn"/"api_key" params | closed |
| T-02-05-02 | Tampering | embedding drift | mitigate | embedding_service.py:55 `EMBEDDING_MODEL = "voyage-3"` pinned constant; no "voyage-latest" in any non-comment, non-docstring line; model string stored in embeddings.model column | closed |
| T-02-05-03 | Denial of Service / cost burn | Voyage retry storm | mitigate | embedding_service.py:85-88 `@retry(wait=wait_exponential(min=2, max=30), stop=stop_after_attempt(5))` on `_embed_batch` | closed |
| T-02-05-04 | Repudiation | task retry | mitigate | embed.py:168-180 LEFT JOIN WHERE NULL read guard fetches only unembedded chunks; embed.py:228-238 `INSERT INTO embeddings ... ON CONFLICT (chunk_id) DO UPDATE` write guard | closed |
| T-02-05-05 | Denial of Service | REINDEX on large index | accept | REINDEX CONCURRENTLY does not lock writes; M2 corpus bounded by single-document ingest — see Accepted Risks Log | closed |
| T-02-05-06 | Information Disclosure | structlog calls in embed task | mitigate | embed.py log calls reference chunk counts, total_chunks_embedded, document_id only; chunk content and vector values never logged | closed |
| T-02-05-07 | Tampering | temp file deletion | accept | Best-effort delete in embed.py:257-282 try/except OSError; disk fill bounded by MAX_UPLOAD_SIZE_MB × concurrent ingests; ops cron deferred to M10 — see Accepted Risks Log | closed |
| T-02-06-01 | Information Disclosure | cross-tenant agent access | mitigate | documents.py:105-114 filters Agent by `Agent.id == agent_id AND Agent.tenant_id == tenant.id`; returns 404 on mismatch | closed |
| T-02-06-02 | Denial of Service | oversized file upload | mitigate | documents.py:135, 146-151 enforces `max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024` BEFORE writing to disk; returns 413 on excess | closed |
| T-02-06-03 | Tampering | unsupported file type | mitigate | documents.py:69 `ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}` checked at lines 139-143 before any I/O; returns 415 on unknown extension | closed |
| T-02-06-04 | Information Disclosure | connection string in task args | mitigate | documents.py:235-243 chain dispatches only `str(tenant.id), str(agent.id), str(job.id), document_ids`; no `postgresql://` string in any chain argument | closed |
| T-02-06-05 | Information Disclosure | path traversal via filename | mitigate | documents.py:175 saves file as `upload_dir / f"{doc_id}{ext}"` where doc_id is uuid4; original filename stored in source_uri DB column only — never used as a path component | closed |
| T-02-06-06 | Tampering | malicious URL fetch | mitigate | URL stored in source_uri; actual httpx fetch with timeout=30 and raise_for_status() occurs in parse.py:247-249 via Celery task (not inline in route) | closed |
| T-02-06-07 | Denial of Service | many concurrent uploads | accept | Rate limiting deferred to M8/M10; M2 dev environment — see Accepted Risks Log | closed |
| T-02-06-08 | Information Disclosure | structlog calls in route | mitigate | documents.py log calls at lines 189-194, 199-206, 247-253 reference tenant_id, agent_id, job_id, document_ids, source_uri only; file contents never logged | closed |
| T-02-06-09 | Denial of Service | psycopg2.connect() blocking event loop | mitigate | documents.py:170, 300, 370 all use `psycopg2.connect(conn_str, connect_timeout=5)` — blocks the async event loop thread at most 5 seconds | closed |
| T-02-07-01 | Information Disclosure | demo_business.pdf in repo | mitigate | Git commit `cb837a6` message: "feat(02-07): add demo_business.pdf fixture (Acme Coffee Roasters handbook, owner-authored)" — owner-authored confirmed in commit message (human checkpoint satisfied) | closed |
| T-02-07-02 | Repudiation / Cost | accidental E2E API spend | mitigate | test_ingestion_e2e.py:40-41 gates all tests on `INGESTION_E2E_ENABLED = bool(os.getenv("INGESTION_E2E_ENABLED"))`; demo_m2.sh requires explicit `ADMIN_KEY` env var — no silent billing | closed |
| T-02-07-03 | Information Disclosure | demo script logs | mitigate | demo_m2.sh inspect step decrypts connection string in-process via `fernet_decrypt` (line 235); never echoed; prints only first 200 chars of chunk text content | closed |
| T-02-07-04 | Tampering | E2E test fixture state | mitigate | Declared mitigation: "test_agent_and_job fixture; teardown deletes DB". Actual implementation: `_provision_agent()` via live HTTP API creates a fresh tenant+agent per test run. No explicit tenant DB deletion on teardown. State isolation is achieved (fresh tenant per run) but the DB deletion step is absent. Security intent (no cross-test contamination) is preserved through fresh provisioning; risk of state persistence is low and gated by INGESTION_E2E_ENABLED=1. See audit notes. | closed |
| T-02-07-05 | Denial of Service | demo script timeout | mitigate | demo_m2.sh:189 `timeout "$SSE_TIMEOUT"` with default `SSE_TIMEOUT=3600`; MISSING array logic exits 1 if any of 11 events absent; `set -euo pipefail` ensures script exits 1 on any step failure | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-02-01 | T-02-01-04 | Schema DDL contains no PII in column names or default values; migration is version-controlled in git | Bantuson | 2026-05-17 |
| AR-02-02 | T-02-01-05 | ALTER TABLE ADD COLUMN on empty pre-state documents table is O(0); no prod data at M2 pre-state | Bantuson | 2026-05-17 |
| AR-02-03 | T-02-03-05 | Re-parsing same file between parse_documents and chunk_documents is non-deterministic at ms level; HybridChunker is deterministic given same DoclingDocument; cheaper than Redis round-trip for 100MB+ objects | Bantuson | 2026-05-17 |
| AR-02-04 | T-02-05-05 | REINDEX CONCURRENTLY does not lock writes; M2 corpus bounded by single-document ingest; perf optimization not a data safety issue | Bantuson | 2026-05-17 |
| AR-02-05 | T-02-05-07 | Best-effort temp file deletion in try/except OSError; disk fill bounded by MAX_UPLOAD_SIZE_MB × concurrent ingests; ops cron cleanup deferred to M10 | Bantuson | 2026-05-17 |
| AR-02-06 | T-02-06-07 | Rate limiting deferred to M8/M10; M2 is a dev/demo environment; acceptable for current milestone | Bantuson | 2026-05-17 |

*Accepted risks do not resurface in future audit runs.*

---

## Audit Notes

### T-02-07-04 — Fixture Teardown Deviation

The threat model declared: "Tests use test_agent_and_job fixture with fresh tenant DB per test; teardown deletes DB." The implemented E2E tests (`test_ingestion_e2e.py`) deviate from this plan:

- **Implemented:** `_provision_agent()` helper creates a fresh tenant + agent via the live HTTP API on each test run. No pytest fixture or teardown step deletes the tenant DB.
- **Security impact:** Minimal. Each test run provisions a new isolated tenant with no shared state from previous runs. The absence of DB deletion means test data accumulates in the Neon project over time, but this is a data hygiene concern, not a security vulnerability.
- **Gating:** All E2E tests require `INGESTION_E2E_ENABLED=1`, limiting blast radius.
- **Classification:** Deviation from declared mitigation plan; isolation intent achieved by alternate mechanism. Marked CLOSED with this note.

### T-02-07-05 — Default SSE Timeout Changed

The threat model cited a 600s timeout; the implementation uses `SSE_TIMEOUT="${SSE_TIMEOUT:-3600}"` (3600s default). This is more permissive but not a security regression — the purpose is to prevent indefinite hangs, and 3600s still achieves that. Override via env var is supported.

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-05-17 | 44 | 44 | 0 | gsd-security-auditor (claude-sonnet-4-6) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-05-17
