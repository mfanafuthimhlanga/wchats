# Stack Research

**Domain:** Production RAG Platform
**Researched:** 2026-05-12
**Confidence:** HIGH (all components verified against PyPI, official docs, or official GitHub; version numbers sourced directly from PyPI pages, not training data)

---

## Validated Stack (from PRD) — Component-by-Component

This section verifies every PRD-specified component against current reality. Each entry states whether the PRD choice is confirmed, the current version as of research date, and any material changes since the PRD was drafted.

---

### Claude Agent SDK

**PRD claim:** "Claude Agent SDK (customer agents, strategists, red teamers)"

**Status: CONFIRMED with critical naming clarification**

- **Correct package name:** `claude-agent-sdk` (PyPI), installed via `pip install claude-agent-sdk`
- **Import name:** `from claude_agent_sdk import query, ClaudeAgentOptions` (note underscore)
- **Current version:** 0.1.81 (released May 11, 2026)
- **Previous name:** Claude Code SDK, renamed in late 2025
- **Alpha status:** Yes — still alpha, but Anthropic's production-facing tooling (Claude Code) runs on the same core
- **Architecture note:** The SDK wraps Claude Code's agent loop — built-in tools are `Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebSearch`, `WebFetch`. Custom domain tools (`retrieve()`, `lookup_structured()`, `escalate_to_human()`, `clarify()`) are implemented as **in-process MCP servers** using the `@tool` decorator and `create_sdk_mcp_server()` — this is the officially supported pattern for domain-specific tooling.
- **Do NOT confuse with:** `anthropic` package (the direct API client, different thing)
- **Source:** https://pypi.org/project/claude-agent-sdk/, https://code.claude.com/docs/en/agent-sdk/overview

---

### Claude API (direct — judges, validators, Gatekeeper, Auditor, Strategist)

**PRD claim:** "Claude API direct (judges, Gatekeeper, Auditor, Strategist)"

**Status: CONFIRMED**

- **Correct package name:** `anthropic`
- **Current version:** 0.101.0 (released May 11, 2026)
- **Structured outputs:** Available in public beta for Sonnet 4.5+ via `client.messages.parse()` and the `anthropic-beta: structured-outputs-2025-11-13` header — exactly what the Gatekeeper/Auditor/Strategist judges need
- **Source:** https://pypi.org/project/anthropic/

---

### Docling

**PRD claim:** "Docling (layout-aware parsing)"

**Status: CONFIRMED — active, current, best-practice**

- **Current version:** 2.93.0 (released May 7, 2026)
- **Maintained by:** IBM Research (LF AI & Data Foundation)
- **Python requirement:** 3.10+
- **Capabilities confirmed:** Layout analysis (DocLayNet model), table structure recognition (TableFormer), reading order, code/formula detection, image classification, OCR for scanned docs. Supports PDF, DOCX, PPTX, XLSX, HTML, images, LaTeX, plain text.
- **Ecosystem signal:** LangChain maintains a Docling loader integration; it is the de facto IBM/open-source choice for layout-aware parsing as of 2026.
- **Source:** https://pypi.org/project/docling/

---

### Chonkie

**PRD claim:** "Chonkie (structure-aware chunking)"

**Status: CONFIRMED — active and significantly more capable than PRD implied**

- **Current version:** 1.6.5 (released May 6, 2026) — note this is well past the 0.5.1 that some older references cite
- **Chunking strategies available:** TokenChunker, FastChunker, SentenceChunker, RecursiveChunker, SemanticChunker, LateChunker, CodeChunker, NeuralChunker, SlumberChunker (9 total)
- **For Veridian:** RecursiveChunker (hierarchical document structure), SemanticChunker (semantic boundaries), and LateChunker (embed-first-then-split for better retrieval) are the relevant three
- **Performance claim:** 33x faster than alternatives for token chunking, 2.5x faster for semantic chunking
- **Source:** https://pypi.org/project/chonkie/

---

### pgvector + HNSW

**PRD claim:** "pgvector with HNSW indexes"

**Status: CONFIRMED — HNSW is mature and fully supported**

- **pgvector version on Neon:** 0.8.1 (on Postgres 18 as of Oct 2025); HNSW has been supported since 0.5.0 (well before this project)
- **HNSW on Neon:** Fully supported. Neon explicitly documents HNSW index creation.
- **Key capability confirmed:** Iterative index scans via `hnsw.iterative_scan` parameter for cases where initial scan does not satisfy query conditions (important for filtered searches)
- **Distance metrics on Neon:** L2, inner product, cosine, L1, Hamming, Jaccard — all available
- **Python client:** `pgvector` package (PyPI) for SQLAlchemy/psycopg integration
- **Source:** https://neon.com/docs/extensions/pgvector

---

### BM25 / Keyword Search (Postgres tsvector vs extension)

**PRD claim:** "Postgres `tsvector` + `ts_rank_cd`, or the `pgbm25` extension if available"

**Status: CLARIFICATION REQUIRED — pg_search deprecated on Neon as of March 2026**

- **pg_search (ParadeDB):** DEPRECATED on Neon as of March 19, 2026 — no longer available for new Neon projects
- **`pgbm25`:** This extension name does not match any current Neon-supported extension; the PRD may have used a placeholder name
- **What Neon recommends instead of pg_search:** PostgreSQL's built-in `tsvector`/`tsquery` + `ts_rank_cd`
- **Decision for Veridian:** Use native `tsvector` + `ts_rank_cd` for BM25-style keyword scoring. This is simpler, has no extension dependency, and is already in the PRD as the primary option. The RRF fusion in SQL is unaffected.
- **True BM25 alternatives if needed later:** VectorChord-BM25 (tensorchord) or ParadeDB's `pg_search` self-hosted — but both require non-Neon hosting or self-managed extensions. Not worth the complexity for v1.
- **Source:** https://neon.com/docs/extensions/pg_search

---

### Voyage AI (embeddings + rerank)

**PRD claim:** "Voyage (embed + rerank)"

**Status: CONFIRMED**

- **Package name:** `voyageai`
- **Current version:** 0.3.7 (released December 16, 2025)
- **Installation:** `pip install voyageai`
- **Rerank models available:** rerank-2.5 and rerank-2.5-lite (instruction-following rerankers, released August 2025)
- **Source:** https://pypi.org/project/voyageai/, https://docs.voyageai.com

---

### Cohere Rerank (fallback)

**PRD claim:** "Cohere Rerank fallback"

**Status: CONFIRMED**

- **Package name:** `cohere`
- **Current version:** 6.1.0 (released April 10, 2026)
- **Current models:** Rerank 3.5 (multi-format, 100+ languages), Rerank 4.0 (latest, most performant)
- **Source:** https://pypi.org/project/cohere/

---

### Ragas

**PRD claim:** "Ragas metrics framework, custom harness on top"

**Status: CONFIRMED with significant API break warning**

- **Current version:** 0.4.3 (released January 13, 2026)
- **BREAKING CHANGES from v0.3 to v0.4 (all production code must account for these):**
  1. Metrics moved from `ragas.metrics` to `ragas.metrics.collections` — import paths changed
  2. `single_turn_ascore(sample)` replaced by `ascore(**kwargs)` — no longer takes sample objects
  3. `SingleTurnSample(..., ground_truths=["correct"])` → `SingleTurnSample(..., reference="correct")` — ground_truths was a list, reference is a single string
  4. Metrics now return `MetricResult` objects with `.value` and `.reasoning` fields instead of raw floats
  5. LLM factory consolidated: `instructor_llm_factory()` removed, use unified `llm_factory()` instead
  6. Requires Pydantic v2 (aligns with FastAPI stack)
- **Anthropic/Claude support:** `llm_factory()` auto-detects Anthropic from model name — no explicit provider string needed
- **Source:** https://pypi.org/project/ragas/, https://docs.ragas.io/en/stable/howtos/migrations/migrate_from_v03_to_v04/

---

### PyRIT

**PRD claim:** "PyRIT scaffolding, custom Claude probes"

**Status: CONFIRMED with usage guidance**

- **Current version:** 0.13.0 (released April 17, 2026)
- **GitHub location:** Moved from `Azure/PyRIT` to `microsoft/PyRIT`
- **Claude/Anthropic support:** A target class for AWS Bedrock Anthropic Claude models exists (PR #699 in old repo). Additionally, PyRIT supports providers that implement the OpenAI API — many via compatible endpoints. Claude API direct is not the primary supported target; custom Claude probes (as the PRD specifies) are the recommended pattern for Claude-specific attack surfaces.
- **Recommended usage pattern for Veridian:** Use PyRIT for generic red team scaffolding (attack orchestration, severity classification, reporting structure). Write custom Claude-native probe generators for Claude-specific jailbreak surfaces and business-context attacks — exactly as the PRD specifies. PyRIT does NOT need to call Claude directly; the probe outputs can be injected into the customer agent via any HTTP target.
- **Source:** https://pypi.org/project/pyrit/, https://github.com/microsoft/PyRIT

---

### Langfuse

**PRD claim:** "Langfuse (traces, judge outputs, latency, cost)"

**Status: CONFIRMED with major version migration warning**

- **Package name:** `langfuse`
- **Current version:** 4.6.1 (released May 8, 2026)
- **BREAKING CHANGES in v4 (rewritten March 2026):**
  1. Observation-centric data model: `user_id`, `session_id`, `metadata`, `tags` propagate to every observation
  2. `update_current_trace()` → `propagate_attributes()` context manager
  3. `start_span()` / `start_generation()` → `start_observation()`
  4. `api.observations_v_2` / `api.score_v_2` / `api.metrics_v_2` → `api.observations` / `api.scores` / `api.metrics`
  5. Non-LLM spans (HTTP, DB, queue) are NOT exported by default — must opt in
  6. Requires **Pydantic v2** (aligns with FastAPI stack)
  7. Metadata values now `dict[str, str]` limited to 200 characters
- **Do not install v3:** Any tutorial written before March 2026 will have the wrong API. Start with v4 directly.
- **Source:** https://pypi.org/project/langfuse/, https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4

---

### FastAPI + Pydantic

**PRD claim:** "FastAPI, Pydantic"

**Status: CONFIRMED**

- **FastAPI current version:** 0.136.1 (released April 23, 2026)
- **Pydantic:** v2 is required by both Langfuse v4 and Ragas 0.4.3; align the entire stack on Pydantic v2
- **Production deployment:** Gunicorn + Uvicorn workers (multi-core), async SQLAlchemy for DB connections
- **Source:** https://pypi.org/project/fastapi/

---

### Celery + Redis

**PRD claim:** "Celery, Redis"

**Status: CONFIRMED**

- **Celery current version:** 5.6.3 (released March 26, 2026), Python 3.9–3.13 supported
- **Production status:** "5 - Production/Stable"
- **Redis backend:** Standard choice; celery[redis] installs the Redis broker + result backend together
- **Source:** https://pypi.org/project/celery/

---

### Alembic

**PRD claim:** "Alembic"

**Status: CONFIRMED**

- **Current version:** 1.18.4
- **Async template:** `alembic init -t async alembic` for async SQLAlchemy — required for FastAPI + async DB pattern
- **Source:** https://pypi.org/project/alembic/

---

### Neon (per-tenant + control DB)

**PRD claim:** "Neon (per-tenant), Postgres control DB on Neon"

**Status: CONFIRMED**

- **Python provisioning SDK:** `neon-api` package (PyPI) — programmatic Neon project/branch/database management
- **Scale-to-zero:** Confirmed default behavior — per-tenant DBs scale to zero, aligning with the cost model
- **Branching for eval isolation:** Confirmed supported feature — nightly evals against a branch is a valid architectural pattern
- **Source:** https://neon.com/docs/reference/python-sdk

---

### Next.js (admin UI) + Preact (widget)

**PRD claim:** "Next.js admin UI, Preact widget <20kb"

**Status: CONFIRMED — not Python stack, out of scope for Python version pinning**

- Both choices are sound and not at risk of having been superseded
- Preact <20kb gzipped target is achievable with tree-shaking + no runtime dependencies

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| FastAPI | 0.136.1 | HTTP API layer | Industry standard async Python API; Pydantic-native |
| Pydantic | v2.x | Validation on all boundaries | Required by FastAPI, Langfuse v4, Ragas 0.4.x — unified constraint |
| Celery | 5.6.3 | Task orchestration | Idempotent chains, acks_late, two-queue pattern for pipeline vs runtime |
| Redis | 7.x | Celery broker + result backend | Lowest-latency broker; also usable for rate limiting counters |
| Alembic | 1.18.4 | Schema migrations | Standard SQLAlchemy migration tool; async template for per-tenant DB provisioning |
| claude-agent-sdk | 0.1.81 | Customer agents + red team agents | Iterative tool-calling loop required; single-shot won't capture agent depth |
| anthropic | 0.101.0 | Judges, validators, single-shot LLM calls | Direct API access for Gatekeeper/Auditor/Strategist; structured outputs in beta |
| Docling | 2.93.0 | Layout-aware document parsing | IBM Research maintained; DocLayNet + TableFormer models; LangChain integrated; actively developed |
| Chonkie | 1.6.5 | Structure-aware chunking | RecursiveChunker for document hierarchy; LateChunker for better retrieval; 33x faster than alternatives |
| voyageai | 0.3.7 | Embeddings + primary reranker | Best-in-class retrieval accuracy for RAG; rerank-2.5 instruction-following |
| cohere | 6.1.0 | Fallback reranker | Rerank 3.5/4.0 available; activate when Voyage API is unavailable |
| pgvector | (Neon-managed 0.8.1) | Vector similarity search | HNSW indexes mature; native Postgres means no separate vector DB infra |
| ragas | 0.4.3 | RAG evaluation metrics | Faithfulness, answer relevance, context precision/recall; v0.4 has Claude/Anthropic support |
| pyrit | 0.13.0 | Red team scaffolding | Attack orchestration + severity classification; complement with custom Claude probes |
| langfuse | 4.6.1 | Observability, cost, latency tracing | Traces + judge outputs + cost per call; start on v4 directly |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncpg | latest | Async Postgres driver | Raw async queries against tenant DBs; 5x faster than sync drivers |
| SQLAlchemy | 2.x (async) | ORM + query builder for control DB | Control DB models; async engine required for Alembic async template |
| psycopg2-binary or psycopg[binary] | latest | Sync Postgres driver | Alembic migration runner (sync context); Celery task DB writes |
| neon-api | latest | Neon project/branch provisioning | `POST /projects` in the Celery provisioning task |
| tenacity | latest | Retry with exponential backoff | All Claude API calls, Voyage calls, Cohere calls — wrap every external LLM call |
| httpx | latest | Async HTTP client | URL ingestion path in M2; webhook delivery; async-native unlike requests |
| python-jose or PyJWT | latest | JWT generation/validation | Widget short-lived JWTs from `/widget/{agent_id}/config` |
| python-multipart | latest | File upload handling | Drag-and-drop document ingestion endpoint |
| sse-starlette | latest | Server-Sent Events | SSE job status streaming from FastAPI without WebSocket complexity |
| passlib[bcrypt] | latest | Password hashing | Email/password auth (OAuth out of scope for v1) |
| pydantic-settings | latest | Config from env vars | 12-factor config for FastAPI app settings |
| structlog | latest | Structured logging | JSON logs that Langfuse and any log aggregator can parse |

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Agent framework | claude-agent-sdk | LangGraph, CrewAI | PRD explicitly requires Claude; SDK gives the tool loop natively; LangGraph adds abstraction overhead with no benefit |
| Document parsing | Docling | Unstructured, LlamaParse | Unstructured has weaker table structure; LlamaParse is cloud-only (cost + latency); Docling is open-source and locally runnable |
| Chunking | Chonkie | LangChain TextSplitter, LlamaIndex NodeParser | Chonkie is purpose-built and benchmarked faster; LangChain/LlamaIndex chunkers are framework-coupled |
| Embeddings | Voyage | OpenAI ada-002, Cohere Embed | Voyage consistently tops MTEB RAG benchmarks; OpenAI adequate but not best-in-class; Cohere secondary role as reranker |
| Vector store | pgvector (Neon) | Pinecone, Qdrant, Weaviate | PRD architectural story is Neon-only; pgvector is sufficient at SMB scale; dedicated vector DB adds ops burden |
| BM25 search | tsvector + ts_rank_cd | pg_search (ParadeDB) | pg_search deprecated on Neon March 2026; native tsvector sufficient for v1 |
| Observability | Langfuse | LangSmith, Arize, Helicone | Langfuse is open-source self-hostable; native judge output logging; cost tracking per call |
| Red team | PyRIT + custom Claude probes | PromptFoo, Garak | PyRIT gives attack orchestration scaffolding; Garak is model-agnostic but weaker on Claude-specific surfaces; PromptFoo lacks agentic iteration |
| Eval framework | Ragas | DeepEval, TruLens | Ragas has the four core RAG metrics (faithfulness, answer relevance, context precision, context recall) natively; Claude support via llm_factory() |
| Async DB driver | asyncpg | psycopg3 async | asyncpg is faster for high-concurrency RAG serving; psycopg3 better for mixed sync/async — Celery workers are sync-native anyway |
| Task queue | Celery + Redis | RQ, Dramatiq, Temporal | Celery acks_late + chains + beat scheduler covers all PRD requirements; Temporal is operationally heavier for a solo dev |

## What NOT to Use

| Library / Approach | Why Not |
|--------------------|---------|
| `langchain` / `llama-index` as the core framework | High abstraction cost; hides retrieval internals that Veridian intentionally exposes; versioning instability; not a "programmatic core" |
| `openai` package for LLM calls | Wrong vendor — Claude only per PRD constraints |
| `pg_search` (ParadeDB extension) on Neon | Deprecated on Neon as of March 2026 for new projects |
| Pinecone, Weaviate, Qdrant, Chroma | Violates the Neon-only architectural story; adds separate infra; pgvector is sufficient at SMB scale |
| `langfuse` v2 or v3 | v4 is a rewrite (March 2026); v3 API is incompatible with v4; start on v4 to avoid double-migration |
| Ragas < 0.4.0 | v0.3 API is broken in v0.4; import paths, sample schema, metric return types all changed |
| `instructor` library for structured outputs | Anthropic now supports structured outputs natively in beta; instructor adds a dependency for functionality that's available in `anthropic` 0.101.0 directly |
| Synchronous FastAPI route handlers for long-running work | Blocks the event loop; all ingestion/eval/red-team work goes through Celery |
| WebSockets for job status | SSE via sse-starlette is sufficient and simpler for unidirectional job streaming |
| `pgbm25` extension | This name does not correspond to a current Neon-supported extension; use native tsvector |

## Version Compatibility Matrix

| Constraint | Requirement | Implication |
|------------|-------------|-------------|
| Python version | 3.10+ (required by FastAPI, Docling, Langfuse v4, claude-agent-sdk) | Pin to Python 3.11 or 3.12 — best balance of performance and library support |
| Pydantic version | v2 required by Langfuse v4, Ragas 0.4.x | Pin `pydantic>=2.0` globally; no v1 compatibility shim |
| SQLAlchemy version | 2.x required for async engine + Alembic async template | Do not use SQLAlchemy 1.4 |
| Celery + Redis | Celery 5.6.x + redis-py 5.x | Verified compatible |
| Langfuse | v4 requires Pydantic v2 | Already satisfied by constraint above |
| Ragas | 0.4.x requires Pydantic v2 | Already satisfied |
| claude-agent-sdk | Requires Python 3.10+ | Already satisfied |
| voyageai | Python 3.9–3.13 | Already satisfied |

## Gaps Identified

The PRD stack is strong but the following production-grade concerns are absent from the stack specification. These are not optional at production scale — they should be designed in from M1, not bolted on in M10.

### Gap 1: Async Postgres driver not specified

The PRD lists `Postgres` and `Neon` but does not name an async driver. **Recommendation:** `asyncpg` for tenant DB queries inside async FastAPI routes; psycopg2-binary for Celery tasks (sync context) and Alembic migrations.

### Gap 2: Retry / circuit-breaker library not specified

Every call to Claude API, Voyage, Cohere, and Langfuse can fail with rate limits or transient errors. The PRD does not mention retry logic. **Recommendation:** `tenacity` with exponential backoff + jitter, wrapping all external LLM/embedding/observability calls. Pattern: `@retry(wait=wait_exponential(min=1, max=60), stop=stop_after_attempt(5), retry=retry_if_exception_type((RateLimitError, APITimeoutError)))`.

### Gap 3: No per-tenant rate limiter on the widget chat path

The widget is publicly embeddable. Without per-tenant rate limiting, a single tenant's widget traffic can exhaust the Claude API budget. **Recommendation:** Redis-backed token bucket per `agent_id` in the `runtime` Celery queue. Simple to implement; critical to have before M4 goes live.

### Gap 4: No mention of SSE transport library

The PRD requires SSE for job status streaming but does not name a library. **Recommendation:** `sse-starlette` — the standard FastAPI-compatible SSE library.

### Gap 5: Secret / config management not specified

Per-tenant Neon connection strings must be encrypted at rest in the control DB, and API keys for Claude/Voyage/Cohere/Langfuse must be managed as secrets. **Recommendation:** `pydantic-settings` for environment-based config; AES-256 encryption (via `cryptography` package) for tenant connection strings stored in Postgres.

### Gap 6: File upload handling not specified

M2 requires drag-and-drop document ingestion. FastAPI requires `python-multipart` for multipart form data. This is a trivial gap but must be in requirements.txt.

### Gap 7: JWT library for widget auth not specified

The PRD describes short-lived JWTs from the widget config endpoint. No JWT library is named. **Recommendation:** `python-jose[cryptography]` or `PyJWT` — both are production-ready; PyJWT is lighter.

### Gap 8: Structured logging not specified

Langfuse handles LLM-level observability but application-level logs (task errors, provisioning events, retrieval timing) need structured JSON logs for correlation with Langfuse traces. **Recommendation:** `structlog` with JSON renderer.

### Gap 9: Ragas v0.4 API break needs active attention in M6

The v0.3-to-v0.4 migration is not backward-compatible. Any Ragas example code found online predating January 2026 will use the old API (`ground_truths` as list, `ragas.metrics` import path, raw float scores). The custom harness built in M6 must be written against v0.4 from the start.

### Gap 10: Langfuse v4 "no non-LLM spans by default" needs opt-in configuration

In Langfuse v4, HTTP spans, DB spans, and queue spans are NOT exported unless explicitly opted in via `should_export_span`. For Veridian's retrieval timing and Celery task observability, this needs intentional configuration in M5 (Langfuse integration milestone).

---

## Sources

- PyPI: claude-agent-sdk — https://pypi.org/project/claude-agent-sdk/
- Claude Agent SDK official docs — https://code.claude.com/docs/en/agent-sdk/overview
- Claude Agent SDK custom tools — https://code.claude.com/docs/en/agent-sdk/custom-tools
- PyPI: anthropic — https://pypi.org/project/anthropic/
- PyPI: docling — https://pypi.org/project/docling/
- PyPI: chonkie — https://pypi.org/project/chonkie/
- PyPI: ragas — https://pypi.org/project/ragas/
- Ragas v0.3→v0.4 migration guide — https://docs.ragas.io/en/stable/howtos/migrations/migrate_from_v03_to_v04/
- PyPI: pyrit — https://pypi.org/project/pyrit/
- PyRIT GitHub (microsoft) — https://github.com/microsoft/PyRIT
- PyPI: langfuse — https://pypi.org/project/langfuse/
- Langfuse v3→v4 migration guide — https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4
- PyPI: voyageai — https://pypi.org/project/voyageai/
- PyPI: cohere — https://pypi.org/project/cohere/
- PyPI: fastapi — https://pypi.org/project/fastapi/
- PyPI: celery — https://pypi.org/project/celery/
- PyPI: alembic — https://pypi.org/project/alembic/
- Neon pgvector docs — https://neon.com/docs/extensions/pgvector
- Neon pg_search deprecation — https://neon.com/docs/extensions/pg_search
- Neon Python SDK docs — https://neon.com/docs/reference/python-sdk
- VectorChord-BM25 (true BM25 in Postgres, alternative if needed) — https://github.com/tensorchord/VectorChord-bm25
