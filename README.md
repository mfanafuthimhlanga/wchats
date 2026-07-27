# W Chats

**A multi-tenant platform for deploying customer-service and transactional AI agents that are defensible before they ever meet a customer.**

Most "AI agent" products let you ship a chatbot and hope. This one refuses to. An agent cannot reach a customer until it has been grounded in the business's own documents, scored by an automated eval suite, and attacked by a red team — and every one of those results is a gate, not a dashboard widget.

The target user is a non-technical business owner — a salon, a repair shop, an e-commerce store — who needs a support agent on their site without hiring a developer.

There are two ways to drive it, and they are the same four steps either way:

- **By hand, in the console.** The owner walks the journey themselves: name the agent, draft its soul, upload documents, run the evals and the red team, clear the gate.
- **Programmatically, from a command line.** The whole journey is a REST + SSE surface behind `X-API-Key` auth, so a developer's coding agent can create the tenant, stream provisioning progress, ingest a corpus, trigger an eval or red-team run, and poll the deployment gate — without a browser.

Same gates on both paths. The console is a client of the API, not a privileged one.

---

## The four steps

The entire product is one journey. The console is built around it, and the API exposes it step for step — so the sequence below is what an owner clicks through, and equally what a coding agent scripts.

| | Step | What happens |
|---|---|---|
| **1** | **Create** | Name the agent and set its temper. A dedicated Neon Postgres project is provisioned for the tenant, migrations run programmatically, and progress streams back over SSE. |
| **2** | **Configure** | Draft the agent's *soul* (voice, do-list, do-not-list) and load the documents. Ingestion parses, chunks, enriches, and embeds them. |
| **3** | **Test** | Run the evals, then the red team. Both write real rows; both can block. |
| **4** | **Deploy** | Clear the pre-deployment gate, then take customers. A copy-paste widget snippet goes on the owner's own site. |

Nothing is public until it clears its gate.

---

## Advanced RAG

Retrieval is the part most systems treat as `embed → cosine similarity → top-k`. This one doesn't.

**Ingestion is structure-aware.** Documents are parsed with [Docling](https://github.com/DS4SD/docling) so headings, lists and tables survive as structure rather than being flattened into prose — tables in particular go through a dedicated table-aware path that preserves row/column relationships. Chunking uses [Chonkie](https://github.com/chonkie-inc/chonkie), so boundaries respect document structure instead of arbitrary token counts. Every chunk gets a deterministic UUID (`document_id` + ordinal hash) so the whole pipeline is idempotent on retry.

**Every chunk is enriched before it is embedded** — a generated summary, a keyword list, and a set of hypothetical questions the chunk would answer. Those enrichments are themselves retrievable surface area.

**Retrieval is genuinely hybrid**, fused in a single SQL CTE:

```
query ─┬─► pgvector HNSW  (vector_cosine_ops, 1024-dim)  ─┐
       │                                                  ├─► Reciprocal Rank Fusion ─► rerank ─► top-k
       └─► BM25 via native tsvector + ts_rank_cd         ─┘
```

BM25 runs on native Postgres `tsvector` + `ts_rank_cd` — deliberately not `pg_search`/`pgbm25`, which Neon deprecates. Fused candidates are reranked (Voyage, with Cohere as fallback), and a `verified_qa` cache is consulted *before* the hybrid search so known-good answers short-circuit the whole path.

**Retrieval strategy is per-tenant configuration**, not a global constant — `k` values, rerank threshold, query expansion on/off, metadata filters. From M9 those strategies are synthesized automatically from the tenant's own corpus signals rather than hand-tuned.

**The full retrieval trace is a first-class response field**: which path matched, the fusion scores, the rerank deltas. And production retrieval is instrumented — recall@k, nDCG@10, MRR, reranker lift over the BM25 baseline, context-window utilization, cited-chunk rank, and index staleness all land in a `retrieval_metrics` table you can query.

---

## The validation chain

Four independent judges, each answering a different question. Three run asynchronously after the response streams to the customer; the fourth runs *synchronously, before* anything mutates.

| Node | Asks | Verdicts |
|---|---|---|
| **Gatekeeper** | Does this address the question actually asked? | `pass · fail · needs_clarification` |
| **Auditor** | Is every factual claim supported by retrieved context? | `grounded · ungrounded · partial` (with citation spans) |
| **Strategist** | Is this coherent, on-brand, aligned with the agent's role? | `ship · revise · escalate` |
| **Actor** | Should this action be allowed to execute at all? | `approve · block · require_human` |

Persistent `ungrounded` verdicts on a retrieval pattern flag the agent for strategy resynthesis. Every verdict is Pydantic-validated and traced to Langfuse.

---

## Managed deploys: evals and red teaming as gates

**Evals.** A scenario-generator agent builds a suite from the tenant's own domain. Runs are scored on four Ragas 0.4.x metrics — Faithfulness, Answer Relevance, Context Precision, Context Recall — and execute against a *Neon branch* of the tenant database, never the production branch. Celery beat runs them nightly. Production conversations that the Gatekeeper or Auditor flagged are mined back into new scenarios, so the suite grows from real failures.

**The failure-triage flywheel.** An operator grades a failing production trace `filed`, and it is promoted into the eval suite with `source='production'` and its originating trace id. A filed trace cannot be withdrawn. The eval ledger reports born-in-production scenarios separately from authored ones.

**Red teaming.** Adversarial agents attack the deployed agent across prompt-injection (split into conversation-injection and content-injection variants), data-leakage, and hallucination classes, plus transaction-specific probes — confused-deputy, value-bound evasion, identity-bypass. Findings are severity-classified and stored as first-class rows with strategy/probe/coverage rollups.

**These are gates, not reports.** A live critical red-team finding drives the pre-deployment checklist to `recommendation='block'`, and `POST /approve-deployment` then returns **422**. The owner cannot deploy past it.

---

## Transactional capability

Agents don't just answer — they act. Refunds, orders, cancellations, subscription changes, bookings. That turns every prompt injection into a potential financial loss, so the transactional path is layered:

- **Typed tool contracts.** Six mutating tools plus `confirm_action`, all typed Pydantic functions. No string blobs, no SQL, no URLs as tool input.
- **Capability envelopes.** Per-skill limits — enabled, rate limit, max amount, confirmation required, identity verification required, Actor mode — enforced fail-closed in the service layer, so a direct API call is rejected exactly as the UI would be. Owners can tighten these, never loosen them.
- **Idempotency with atomic reserve-before-execute.** A replayed key returns the original result and does not re-execute.
- **The Actor gate**, synchronous and pre-mutation, catching the injection-to-action class where the conversation looks legitimate but the proposed action does not match the customer's intent.
- **Customer identity verification.** Email/SMS OTP issuing short-lived verified sessions, required per-skill, enforced server-side and never inferred from agent prose.
- **Server-held credentials.** Provider credentials are Fernet-encrypted under an HKDF-derived per-tenant key and resolved only inside the dispatcher, behind a redacting handle. No agent code path ever sees a raw credential. Adapters: Stripe, Shopify, WooCommerce, Calendly.
- **Complete audit.** Every mutating call writes a `tool_calls_audit` row — including denials.

Provider SDKs sit behind our own narrow tools deliberately, rather than exposing a provider MCP server to the model: a provider toolkit would bypass the capability envelope, the Actor gate and the audit trail, and dump the provider's entire API surface into context.

---

## Architecture

```
   Owner (Next.js console)                Customer (Preact widget, 8 KB gzipped)
             │                                          │
             └──────────────┬───────────────────────────┘
                            ▼
                 ┌──────────────────────┐
                 │  FastAPI control     │   never does work inline
                 │  plane · 18 routers  │
                 └──────┬───────────────┘
                        │ dispatch                    ┌──────────────────┐
                        ▼                             │ Redis pub/sub    │
              ┌───────────────────┐  publish          │ job_events:{id}  │──► SSE
              │  Celery           │──────────────────►└──────────────────┘
              │  pipeline queue   │  ingest · embed · staleness
              │  runtime queue    │  agent turns · evals · red team · validators
              └─────────┬─────────┘
                        │
        ┌───────────────┴────────────────┐
        ▼                                ▼
  Control DB (Neon)              Per-tenant Neon project
  tenants · agents · jobs        documents · chunks · embeddings (HNSW)
  capability_envelopes           conversations · messages · tool_calls
  tool_calls_audit               eval_runs · red_team_findings
  prompt_versions                turn_metrics · retrieval_metrics
```

**Per-tenant Neon projects, not schema-per-tenant** — required so evals can run against a database branch. Connection strings are never Celery task arguments; tasks receive a `tenant_id` and decrypt at runtime. Every task is `acks_late=True` *and* idempotent.

---

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI · Pydantic · SSE over Redis pub/sub |
| Workers | Celery (two queues: `pipeline`, `runtime`) · Redis |
| Data | Neon Postgres · pgvector (HNSW) · Alembic (19 control + 12 tenant migrations) |
| Agents | Claude Agent SDK for customer agents, strategists and red teamers; Claude API direct for judges and gates |
| Ingestion | Docling (layout-aware) · Chonkie (structure-aware) |
| Embeddings | Amazon Bedrock Titan v2, Voyage fallback · Voyage/Cohere rerank |
| Evals | Ragas 0.4.x + a custom harness |
| Observability | Langfuse v4 |
| Console | Next.js 16 · React 19 · a hand-built design system |
| Widget | Preact, 8,087 B gzipped |

---

## Status

Built in 22 planned phases across three milestones. ~29k lines of Python, 1,103 passing unit tests.

| | |
|---|---|
| **M1–M11** | ✅ Control plane, ingestion, hybrid retrieval, reasoning engine + widget, validation chain, evals, red team, pre-deployment checklist, retrieval-strategy synthesis, observability, admin UI |
| **v1.1 — Transactional** | ✅ Phases 14–17 (tool contracts, Actor validator, integration adapters, identity verification). ⏳ Phase 18 at 9/11 (blast-radius gate, capability UI, transaction red-team, PII firewall). Phase 19 (docs + E2E proof) not started. |
| **v1.2 — Console + agent management** | ✅ Frontend cutover and the agent-management backend — live metrics, RAG-health instrumentation, the triage flywheel, prompt versioning with canary and rollback |
| **Production hosting** | ⏸️ **Paused.** Terraform, Bedrock embeddings, connection pooling, S3 uploads and concurrency-safe workers are all written; standing them up needs a real AWS account. |

**Being honest about what that means:** the end-to-end path has been proven live — a grounded, cited answer served to a browser through a tunnel — but there is no always-on deployment today, and the v1.2 migrations have not yet been applied to a live database. This is a complete, tested system awaiting infrastructure, not a running service.

---

## Running locally

Development targets local processes on a modest machine. There is no Docker path — it was tried and abandoned at 6 GB+ minimum.

```bash
redis-server                                              # broker + SSE pub/sub
uvicorn app.main:app --reload                             # from apps/api
celery -A app.worker.celery_app worker -Q pipeline        # ingestion
celery -A app.worker.celery_app worker -Q runtime         # agent turns, evals, red team
pnpm dev                                                  # from apps/admin
```

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit -q
```

You will need a Neon API key, an Anthropic API key, and either Bedrock access or a Voyage key. See `.env.example`.

---

## Repository layout

```
apps/api/          FastAPI app, Celery workers, services, both Alembic chains
apps/admin/        Next.js console
apps/widget/       Preact customer widget
.planning/         Phase plans, research, verification and security artifacts
docs/adr/          Architecture decision records
prototypes/        Design prototypes for the console
```

The `.planning/` directory is worth a look if you want to see how this was built: every phase carries its research, its plan, a verification report, and — where relevant — a threat register and a security audit.
