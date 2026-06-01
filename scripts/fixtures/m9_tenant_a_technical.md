# W Chats Platform — Technical Architecture & Integration Guide

## Overview

The W Chats Platform is an enterprise-grade conversational AI infrastructure that enables organizations to deploy custom, knowledge-grounded customer service agents. This guide covers the complete technical architecture, deployment procedures, API integration patterns, configuration management, and operational runbooks for platform administrators and integrating development teams.

The platform is built on a multi-tenant architecture where each organization (tenant) receives an isolated Neon PostgreSQL database instance with dedicated vector storage for embedding-based retrieval. The core retrieval pipeline combines hybrid search strategies (dense vector similarity via pgvector HNSW indices and sparse keyword matching via native PostgreSQL tsvector) with LLM-powered reranking and contextual answer synthesis.

## Architecture Components

### Control Plane

The control plane consists of a FastAPI application server managing tenant lifecycle, authentication, configuration persistence, and pipeline orchestration. All long-running operations are dispatched to Celery workers via Redis message queues, ensuring the API tier remains non-blocking and horizontally scalable. The control database maintains tenant registry, agent configurations, job tracking, evaluation history, and audit logs.

Key services in the control plane include: the authentication service (argon2id key hashing, HMAC prefix indexing for O(1) tenant lookup), the provisioning service (Neon project creation via REST API, schema migrations via Alembic), the deployment service (pre-flight checklist orchestration via Claude Sonnet strategist agent), and the evaluation service (Ragas-based automated quality assessment with nightly beat schedule).

### Data Plane

Each tenant's data plane is an isolated Neon PostgreSQL project containing: the documents table (source file metadata, MIME type, ingestion status), the chunks table (text segments with embedding vectors, BM25 tsvector index, and structural metadata), the entities table (named entities extracted during ingestion: products, people, locations, policy names), the verified_qa table (high-quality QA pairs validated by the Gatekeeper and Auditor judges), and the eval_scenarios table (evaluation test cases with ground truth answers).

The vector storage uses pgvector's HNSW index type with cosine distance metric. Index parameters are tuned for the M3 retrieval strategy: ef_construction=64, m=16. These parameters balance index build time against query-time recall quality, and were validated against internal benchmarks showing >0.92 precision at k=20 for domain-specific technical corpora.

### Ingestion Pipeline

The ingestion pipeline is a five-stage Celery chain executed in the pipeline queue. Stage 1 (parse_documents) uses Docling for layout-aware document parsing, extracting structural elements including headings, paragraphs, tables, and figures with their positional metadata. Stage 2 (chunk_documents) uses Chonkie 1.6.5+ with structure-aware chunking boundaries that respect heading hierarchies and paragraph breaks, targeting 512-token segments with 64-token overlap. Stage 3 (generate_metadata) invokes Claude Haiku for entity extraction, source classification, and quality scoring. Stage 4 (embed_and_migrate) calls Voyage AI's voyage-3 model for 1024-dimensional embedding generation with document-type input mode, then performs bulk COPY into the tenant's chunks table. Stage 5 (synthesize_retrieval_strategy) collects corpus shape signals and invokes the Claude Sonnet strategist agent to generate an optimized RetrievalStrategy configuration.

## API Reference

### Authentication

All API endpoints except the health check require authentication via the X-API-Key header. API keys are generated during tenant provisioning as vrd_live_* prefixed URL-safe tokens. The key is returned only once at creation time; subsequent authentication uses the hashed value stored in the control database. Key rotation is performed via the tenant management API (requires X-Admin-Key).

### Agent Management

#### POST /api/v1/agents

Creates a new agent and dispatches the Celery provisioning chain (provision_neon → apply_migrations). The request body requires a name (string, max 120 chars), soul configuration (voice: string, do: list of behavioral directives, do_not: list of prohibitions), and role (one of: support, sales, helpdesk). The response is a 202 Accepted with the agent UUID. Poll GET /api/v1/agents/{id} for status transitions: pending → provisioning → ready (or failed with error detail).

The soul configuration is stored in denormalized columns (soul_role, soul_voice, soul_do_list, soul_donot_list) to enable efficient per-field PATCH updates without full object replacement. Input sanitization strips prompt injection markers from all soul fields at admit-time via the sanitize_chunk_text utility.

#### GET /api/v1/agents/{id}

Returns the agent detail including current status, soul configuration, retrieval strategy (JSONB), deployment metadata, and evaluation summary statistics. The retrieval_strategy field will be populated automatically after the first successful ingestion completes the synthesize_retrieval_strategy pipeline task. If the corpus has not been ingested, retrieval_strategy will be an empty JSON object {}.

#### PATCH /api/v1/agents/{id}

Partial update for agent soul fields and retrieval strategy. Accepts soul_voice, soul_role, soul_do_list, soul_donot_list, and retrieval_strategy as independent nullable fields. Setting retrieval_strategy to {} causes the next ingestion to re-synthesize the strategy from scratch. Setting strategy_resynthesis_flagged to true via internal Celery task triggers re-synthesis on the next Celery beat cycle.

### Document Ingestion

#### POST /api/v1/agents/{id}/documents

Accepts multipart/form-data with one or more file uploads (allowed extensions: .pdf, .md, .png, .jpg, .jpeg). Returns 202 Accepted with a job_id for SSE event stream subscription. The ingestion pipeline processes files asynchronously; use GET /api/v1/jobs/{job_id}/events for real-time progress updates.

File size limits: 10MB per file, 50MB total per request. The pipeline supports batch ingestion: multiple files are processed in parallel within a single job, with individual document status tracked in the documents table.

## Retrieval Configuration

### RetrievalStrategy Schema

The RetrievalStrategy is a Pydantic model with six configurable fields governing the retrieval behavior for all agent queries:

- **vector_k** (integer, default 20): Number of candidate results from HNSW vector similarity search. Higher values improve recall at the cost of reranking latency. Effective range: 10-50 for typical corpora.
- **bm25_k** (integer, default 20): Number of candidate results from BM25 tsvector search. Should be increased for corpora with high table density or structured data where exact keyword matching outperforms semantic search.
- **final_k** (integer, default 5): Maximum results returned to the LLM context after reranking. Increasing this beyond 7 rarely improves answer quality and increases token cost.
- **rerank_threshold** (float, default 0.0): Minimum Voyage rerank score for result inclusion. Setting to 0.3 filters low-confidence retrievals for long-document corpora; 0.1 is appropriate for general use.
- **query_expansion** (boolean, default false): When true, generates 2 alternative query phrasings via Claude Haiku and retrieves candidates for all 3 variants before merging by RRF score. Effective for FAQ-style corpora where users phrase questions variably.
- **metadata_filters** (list of objects, default []): Entity-based pre-filters applied before vector/BM25 search. Supports product_name, source_type, and entity_type filter keys.

### Strategy Synthesis Heuristics

The Claude Sonnet strategist analyzes corpus shape signals to generate optimized strategy values. The heuristics are:

**Corpus size heuristics:**
- chunk_count > 5000: vector_k=30, bm25_k=25 (large corpus benefits from wider candidate nets)
- chunk_count 1000-5000: vector_k=20, bm25_k=20 (standard configuration)
- chunk_count < 1000: vector_k=15, bm25_k=15 (small corpus, tighter focus)

**Average chunk length heuristics:**
- avg_chunk_len > 400: rerank_threshold=0.3 (long documents have noisier semantic similarity)
- avg_chunk_len < 150: query_expansion=true (short FAQ-style content benefits from query variants)
- avg_chunk_len 150-400: rerank_threshold=0.1, query_expansion=false

**Structural heuristics:**
- table_ratio > 0.20: bm25_k += 5 (table-heavy corpora benefit from exact keyword matching)
- entity_count > 500: metadata_filters hint (rich entity corpus benefits from entity pre-filtering)

## Security Model

### Threat Model Summary

The W Chats platform was designed with the STRIDE threat model applied at every API boundary, Celery task boundary, and data store boundary. Critical mitigations include:

**Information Disclosure:**
- Connection strings are Fernet-encrypted at rest in the control database. Celery tasks receive only agent_id and fetch the encrypted connection string at runtime, decrypting in-memory. Connection strings never appear in task arguments, logs, SSE events, or API responses.
- API keys use argon2id hashing (time_cost=2, memory_cost=65536, parallelism=2) with HMAC-derived 8-character prefix indexing to avoid full-table scans during authentication without exposing the hash to timing attacks.

**Tampering:**
- LLM-generated strategy configurations are validated through RetrievalStrategy.model_validate() before persistence. Unknown fields are silently ignored (extra="ignore"). Validation failure falls back to RetrievalStrategy() defaults rather than persisting invalid state.
- Soul field injection is mitigated by sanitize_chunk_text() stripping prompt injection markers (system prompt delimiters, role-switching phrases) at admit-time.

**Denial of Service:**
- asyncio.wait_for() with timeout=60.0 bounds all Claude Agent SDK calls.
- Budget guard middleware (TENANT_DAILY_BUDGET_USD) rejects requests once daily token cost exceeds threshold.
- Query expansion is capped at 3 variants with single-batch Voyage embedding to limit per-query latency overhead.

## Evaluation Framework

### Ragas Integration

The evaluation framework uses Ragas 0.4.x with three primary metrics:

**Faithfulness** measures whether the generated answer is grounded in the retrieved context. A faithfulness score of 1.0 means every claim in the answer is supported by at least one retrieved chunk. Scores below 0.7 indicate significant hallucination risk.

**Answer Relevance** measures how directly the answer addresses the user query. Computed by embedding the answer and comparing cosine similarity against generated question variants.

**Context Precision** measures the proportion of retrieved chunks that contain information relevant to answering the query. High context precision indicates the retrieval strategy is returning focused, relevant content rather than broad background material.

### Evaluation Scenarios

Evaluation scenarios are stored per-tenant in the eval_scenarios table. Each scenario has a query (user question), ground_truth (expected answer), and context_docs (list of expected relevant document IDs). Scenarios are generated during ingestion via the generate_metadata Celery task and supplemented by the verified_qa pipeline (Gatekeeper + Auditor judges reviewing agent responses).

The nightly evaluation beat (02:00 UTC) runs run_eval_suite_beat for all agents with is_deployed=true or evaluation_enabled=true. Results are stored in eval_runs with aggregate scores and per-scenario breakdowns available via the eval API.

## Operational Runbooks

### Provision New Tenant

1. Generate API key: POST /api/v1/tenants (X-Admin-Key) — returns plaintext key once
2. Create agent: POST /api/v1/agents (X-API-Key) — dispatches provision_neon + apply_migrations
3. Poll status: GET /api/v1/agents/{id} until status=ready (typically 45-90 seconds)
4. Ingest corpus: POST /api/v1/agents/{id}/documents (multipart)
5. Poll strategy: GET /api/v1/agents/{id} until retrieval_strategy != {}
6. Run pre-deployment checklist: POST /api/v1/agents/{id}/checklist-runs
7. Review checklist: GET /api/v1/agents/{id}/checklist-runs/{run_id}
8. Deploy embed widget: GET /api/v1/agents/{id}/embed-snippet

### Re-synthesize Strategy

If corpus is updated or strategy quality is unsatisfactory:

1. Flag for re-synthesis: PATCH /api/v1/agents/{id} with {"strategy_resynthesis_flagged": true} — this sets the flag but does not immediately trigger re-synthesis
2. Re-ingest or wait for Celery beat: Re-ingesting new documents triggers synthesize_retrieval_strategy automatically; the resynthesis flag bypasses the idempotency guard
3. Alternatively, PATCH retrieval_strategy to {} and re-ingest to force fresh synthesis

### Debugging Pipeline Failures

Pipeline failures are logged with structured JSON to stdout/stderr. Each task emits structured log lines with task_id, task_name, agent_id, and error details. Common failure modes:

- **Neon provisioning timeout**: NEON_API_KEY missing or Neon API rate limit. Check settings.NEON_API_KEY and Neon dashboard.
- **Embedding failure**: VOYAGE_API_KEY missing or corpus contains non-extractable binary content. Check Voyage API quota.
- **Strategy synthesis failure**: ANTHROPIC_API_KEY missing or Claude API rate limit. The task falls back to RetrievalStrategy() defaults rather than failing the chain.
- **SSE event delivery failure**: Redis connection lost. Events are persisted to the job_events table for replay on reconnect.

## Appendix A: Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| DATABASE_URL | Yes | — | Control PostgreSQL connection string |
| REDIS_URL | Yes | — | Redis broker URL |
| ANTHROPIC_API_KEY | Yes | — | Claude API key for agent/judge/strategist calls |
| VOYAGE_API_KEY | Yes | — | Voyage AI key for embeddings and reranking |
| NEON_API_KEY | Yes | — | Neon API key for project provisioning |
| NEON_ENCRYPTION_KEY | Yes | — | Fernet key for encrypting tenant connection strings |
| ADMIN_KEY | Yes | — | X-Admin-Key for administrative API endpoints |
| JWT_SECRET | Yes | dev-secret-change-in-production | JWT signing secret for widget auth |
| TENANT_DAILY_BUDGET_USD | No | 5.0 | Per-tenant daily token cost ceiling |
| RED_TEAM_MAX_TURNS | No | 5 | Maximum turns per red team agent session |
| RED_TEAM_ATTACK_SEQUENCES | No | 3 | Number of attack sequences per red team run |

## Appendix B: Database Schema Reference

### Control Database Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| tenants | Tenant registry | id, name, api_key_hash, api_key_prefix, daily_budget_usd |
| agents | Agent configurations | id, tenant_id, name, status, soul_*, retrieval_strategy, is_deployed |
| jobs | Pipeline job tracking | id, agent_id, status, created_at, completed_at |
| job_events | SSE event persistence | id, job_id, event_name, payload, created_at |
| checklist_runs | Pre-deployment checklist results | id, agent_id, report, status |

### Tenant Database Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| documents | Source document registry | id, agent_id, source_type, filename, status |
| chunks | Text segments with embeddings | id, document_id, agent_id, content, vector, tsvector_content |
| entities | Extracted named entities | id, chunk_id, agent_id, entity_type, entity_value |
| verified_qa | Validated QA pairs | id, agent_id, query, answer, faithfulness, relevance |
| eval_scenarios | Evaluation test cases | id, agent_id, query, ground_truth, context_docs |
| eval_runs | Evaluation run results | id, agent_id, status, aggregate_scores, created_at |
| red_team_runs | Red team scan results | id, agent_id, status, findings, deployment_blocked |
