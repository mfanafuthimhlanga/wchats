---
phase: 01
slug: control-plane-skeleton
status: verified
threats_open: 0
asvs_level: 1
created: 2026-05-13
---

# Phase 01 — Security: Control Plane Skeleton

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Environment → Config | NEON_ENCRYPTION_KEY, ADMIN_KEY, CONTROL_DB_URL, CONTROL_DB_SYNC_URL enter via environment at process startup | Encryption key (32 bytes), DB credentials, admin secret |
| HTTP Client → FastAPI | All inbound requests cross this boundary; X-API-Key and X-Admin-Key are the only auth tokens | API keys (argon2-hashed match), tenant-scoped data |
| FastAPI Auth → Control DB | get_current_tenant reads tenant rows for argon2 key verification | Hashed API key + HMAC prefix for indexed O(1) lookup |
| Celery Task Args → Control DB | tenant_id and agent_id UUIDs dispatched from FastAPI route; connection strings fetched from DB at runtime (never in args) | UUID identifiers only |
| Control DB → Celery Task | Encrypted BYTEA connection strings fetched by agent_id and decrypted at runtime (T-03-01 / CLAUDE.md rule) | Fernet-encrypted Neon URIs |
| Neon API → Task | Neon API response contains project_id and connection URIs | Project credentials (temporary) |
| SSE Endpoint → Redis Pub/Sub | FastAPI subscribes to per-job Redis channel and forwards to authenticated client | Job event metadata (no credentials) |
| Source → Migration | DDL strings in migration files are developer-authored | Schema DDL only |
| GitHub Actions → NEON_API_KEY_TEST | Secret passed to nightly workflow | Neon API key (test account) |
| E2E Test → Real Neon API | Creates and deletes real Neon projects during nightly run | Neon API key, live project credentials |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-01-01 | Information Disclosure | config.py: NEON_ENCRYPTION_KEY | mitigate | Settings.__repr__ suppressed to `Settings(LOG_LEVEL=…)` — no field values exposed; env-only read; .env excluded by .gitignore | closed |
| T-01-02 | Information Disclosure | config.py: CONTROL_DB_SYNC_URL | mitigate | Same __repr__ suppression; URL read from env only; __str__ = __repr__ applied | closed |
| T-01-03 | Tampering | alembic_tenant migration: op.execute() DDL | accept | DDL is developer-authored; no user input reaches migration authoring time | closed |
| T-01-04 | Information Disclosure | database.py: connection string in SQLAlchemy engine | mitigate | pool_pre_ping=True on both engines; echo=False suppresses SQL logging; no DSN in exception messages | closed |
| T-02-01 | Information Disclosure | security.py: fernet_decrypt return value | mitigate | fernet_decrypt docstring prohibits logging the return value; no log calls in module use the return value | closed |
| T-02-02 | Information Disclosure | security.py: verify_api_key | mitigate | Returns bool only; catches VerifyMismatchError + VerificationError + InvalidHashError; raw key never stored in module state; argon2 timing-safe | closed |
| T-02-03 | Information Disclosure | events.py: emit() payload | accept | M1 payloads contain only job metadata (job_id, event_type, timestamps); no PII or credentials | closed |
| T-02-04 | Spoofing | celery_app: task_serializer | mitigate | task_serializer="json", result_serializer="json", accept_content=["json"] — pickle disabled globally; RCE via untrusted Redis not possible | closed |
| T-02-05 | Denial of Service | celery_app: no rate limiting on task submission | accept | M1 is internal API only (X-API-Key protected); rate limiting deferred to M4/M5 when widget traffic begins | closed |
| T-03-01 | Information Disclosure | provision_neon: Neon connection URI in return value | mitigate | Task returns {"agent_id": str, "project_id": str} — no connection URI in return value (CLAUDE.md rule enforced) | closed |
| T-03-02 | Information Disclosure | provision_neon: structlog logging of connection URI | mitigate | Log calls bind only project_id, agent_id, status_code — no URI or encrypted bytes logged | closed |
| T-03-03 | Tampering | apply_migrations: Alembic DDL against tenant DB | accept | Alembic migrations are developer-authored; alembic_version table tracks applied revisions | closed |
| T-03-04 | Denial of Service | provision_neon: 90s Neon operation polling | accept | One worker thread per pipeline task; 90s timeout is bounded; other tasks unaffected on separate workers | closed |
| T-03-05 | Spoofing | agent_id in task args (Redis broker compromise) | accept | Redis is internal; not exposed externally; mTLS on Redis deferred to production hardening post-M1 | closed |
| T-03-06 | Information Disclosure | Neon API key in error messages | mitigate | Neon exceptions caught; only status_code logged (not exc.__str__()); error message uses sanitised f-string | closed |
| T-04-01 | Spoofing | deps.py: API key verification | mitigate | get_current_tenant uses argon2 verify() (timing-attack resistant); get_admin uses secrets.compare_digest (constant-time) | closed |
| T-04-02 | Information Disclosure | deps.py: X-API-Key in structlog contextvars | mitigate | api_key variable never bound to structlog context; route handlers do not log request.headers | closed |
| T-04-03 | Information Disclosure | API error responses leaking internals | mitigate | HTTPException detail strings are generic ("Invalid API key" / "Invalid admin key") — no DB error, key fragment, or stack trace included | closed |
| T-04-04 | Elevation of Privilege | POST /agents: tenant_id spoofing via body | mitigate | agent.tenant_id = tenant.id (from auth dependency); client cannot supply tenant_id in request body | closed |
| T-04-05 | Elevation of Privilege | GET /agents/{id}: cross-tenant agent access | mitigate | Query filters by BOTH Agent.id == agent_id AND Agent.tenant_id == tenant.id; returns 404 on mismatch | closed |
| T-04-06 | Spoofing | CORS: wildcard origins | mitigate | allow_origins=settings.CORS_ORIGINS (list); never "*"; widget CORS added in M4 only | closed |
| T-04-07 | Information Disclosure | Cache-Control missing on responses | mitigate | Cache-Control: no-store injected by @app.middleware("http") on ALL responses including SSE | closed |
| T-04-08 | Denial of Service | SSE endpoint held open indefinitely | accept | TERMINAL_EVENTS frozenset closes stream on job.complete/job.failed; request.is_disconnected() cleans up orphaned connections | closed |
| T-05-01 | Information Disclosure | .env file committed to source tree | mitigate | .gitignore line 19 excludes .env; only .env.example committed | closed |
| T-05-02 | Information Disclosure | demo_m1.sh: API key printed to terminal | accept | Demo is local-dev only; API key is for a local test tenant with no production access | closed |
| T-05-03 | Denial of Service | docker-compose postgres volume not cleaned | accept | Developer responsibility; `make down` + `docker volume prune` clears state | closed |
| T-05-04 | Information Disclosure | Dockerfile: secrets baked into image layers | mitigate | Dockerfile does not have an explicit `COPY .env` instruction; build context is `apps/api/` (excludes project root .env where secrets live). **Note:** no `.dockerignore` present — add one for defense-in-depth if `apps/api/.env` is ever created locally. | closed |
| T-06-01 | Information Disclosure | conftest.py: hardcoded test ADMIN_KEY | accept | Test values are development-only; value includes "for_tests_only" suffix; never matches production key | closed |
| T-06-02 | Information Disclosure | test_task_args.py: security invariant enforcement | mitigate | test_provision_neon_no_connection_string_arg uses inspect.signature to assert no connection_string param — automated CTL-08 enforcement | closed |
| T-07-01 | Tampering | integration tests leave orphaned Postgres rows | mitigate | Each test uses unique UUID tenant/agent IDs; teardown in finally block deletes created rows | closed |
| T-07-02 | Information Disclosure | test_worker_kill spawns real Celery process with local env | accept | Worker uses test credentials (local Postgres, no production Neon key); Neon API calls intercepted by respx | closed |
| T-08-01 | Information Disclosure | CI env: NEON_ENCRYPTION_KEY in GitHub Actions logs | mitigate | nightly.yml uses ${{ secrets.NEON_API_KEY_TEST }} and ${{ secrets.NEON_ENCRYPTION_KEY }}; CI unit tests generate ephemeral key inline (not stored as secret) | closed |
| T-08-02 | Denial of Service | Nightly E2E leaves orphaned Neon project on failure | mitigate | Workflow teardown step has `if: always()` condition; project_delete also called in pytest finally block for belt-and-suspenders | closed |
| T-08-03 | Information Disclosure | README: security architecture publicly exposed | accept | README describes defensive measures (argon2, Fernet) — does not expose key material or system internals | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-01 | T-01-03 | Alembic DDL is developer-authored at plan time; no user input reaches migration code | Bantuson | 2026-05-13 |
| AR-02 | T-02-03 | M1 emit() payloads contain job metadata only (timestamps, IDs) — no PII or credentials | Bantuson | 2026-05-13 |
| AR-03 | T-02-05 | Rate limiting deferred to M4/M5 (widget traffic); M1 API is internal X-API-Key protected | Bantuson | 2026-05-13 |
| AR-04 | T-03-03 | apply_migrations runs developer-authored Alembic DDL; alembic_version tracks applied revisions | Bantuson | 2026-05-13 |
| AR-05 | T-03-04 | 90s Neon operation polling is bounded; pipeline workers handle one task at a time per thread | Bantuson | 2026-05-13 |
| AR-06 | T-03-05 | Redis broker is internal-only; mTLS deferred to production hardening after M1 portfolio milestone | Bantuson | 2026-05-13 |
| AR-07 | T-04-08 | SSE terminal event detection + request.is_disconnected() provides adequate lifecycle management in M1 | Bantuson | 2026-05-13 |
| AR-08 | T-05-02 | demo_m1.sh runs in local dev only; key is for local test tenant; no production Neon access | Bantuson | 2026-05-13 |
| AR-09 | T-05-03 | Docker volume cleanup is developer responsibility; documented in Makefile (make down) | Bantuson | 2026-05-13 |
| AR-10 | T-06-01 | conftest.py test ADMIN_KEY is development-only; value self-documents this with "for_tests_only" suffix | Bantuson | 2026-05-13 |
| AR-11 | T-07-02 | Worker kill test uses local Postgres credentials; Neon API intercepted by respx in test | Bantuson | 2026-05-13 |
| AR-12 | T-08-03 | README describes defenses (argon2, Fernet) at a conceptual level; exposes no key material or implementation secrets | Bantuson | 2026-05-13 |

### AR-02 UPDATE (2026-05-18, Phase 4.1)

Phase 04 SSE now streams agent response text through Redis pub/sub (not just job metadata).
Agent responses may contain customer-query context (PII risk). The original premise
"M1 emit() payloads contain job metadata only — no PII" is no longer universally true.
Revised acceptance: Redis pub/sub carries agent responses in prod; mTLS deferred to M10.
Mitigating control: Redis RDB disabled on broker (Phase 4.1); result_expires=300.

### AR-06 UPDATE (2026-05-18, Phase 4.1)

Phase 04 changed the content traversing the Redis broker. Task args now include
body.message (customer-supplied text, up to 2000 chars) from anonymous widget callers.
The original premise "Redis carries UUID identifiers only, no PII" no longer holds.
Revised acceptance: mTLS deferred to M10 production hardening; mitigating controls
added — (a) Celery result_expires=300 (5 min), (b) Redis RDB disabled on broker,
(c) body.message documented as sensitive in Phase 04 trust boundary table.

---

## Recommendations (Non-Blocking)

| ID | Threat Ref | Recommendation | Priority |
|----|------------|----------------|----------|
| REC-01 | T-05-04 | ~~Add `apps/api/.dockerignore` with `.env` entry for defense-in-depth against accidental secret baking if `apps/api/.env` is ever created locally~~ **IMPLEMENTED** — `apps/api/.dockerignore` added (commit follows) | Low |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-05-13 | 30 | 30 | 0 | gsd-security-auditor (automated — State B, register_authored_at_plan_time: true) |
| 2026-05-17 | 30 | 30 | 0 | gsd-security-auditor (re-audit — State A, short-circuit: threats_open=0 + register_authored_at_plan_time=true; no phase 01 impl changes since prior audit; WR-01 api_key_hash rename is phase 04 scope only) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-05-13
