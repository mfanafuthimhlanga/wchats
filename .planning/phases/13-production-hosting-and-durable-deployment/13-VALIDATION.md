---
phase: 13
slug: production-hosting-and-durable-deployment
status: planned
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-28
updated: 2026-06-29
---

# Phase 13 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `13-RESEARCH.md` § Validation Architecture. The planner fills the per-task map.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (existing — `apps/api/tests/`) + smoke scripts (`scripts/smoke_vm.sh`) |
| **Config file** | `apps/api/pyproject.toml` / existing pytest config |
| **Quick run command** | `pytest apps/api/tests/unit -q` |
| **Full suite command** | `pytest apps/api/tests -q` |
| **Estimated runtime** | ~varies (unit fast; integration gated by *_E2E_ENABLED env flags) |

---

## Sampling Rate

- **After every task commit:** Run `pytest apps/api/tests/unit -q`
- **After every plan wave:** Run `pytest apps/api/tests -q`
- **Before `/gsd-verify-work`:** Full suite green + the phase's live AWS smoke (ALB SSE-survival) passes
- **Max feedback latency:** ~120 seconds (unit); live AWS checks are manual/gated

---

## Per-Task Verification Map

> Wave 0 test scaffolds are created in-task (tdd-style) by the code tasks below — every code task has an `<automated>` verify referencing the test it creates. Live AWS gates are manual/checkpoint by nature (see Manual-Only Verifications).

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Created By | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-----------------|--------|
| 13-01-01 | 01 | 1 | PROD-01,07 | T-13-01-01/03/05 | No secret literal in HCL/image; least-privilege IAM; ElastiCache SG closed | infra | `terraform -chdir=deploy/terraform validate` | task | ⬜ pending |
| 13-01-02 | 01 | 1 | PROD-01,02,03,04,10 | T-13-01-02/06 | ALB idle_timeout=4000 (SSE); 80→443 redirect; /health TG | infra | `terraform -chdir=deploy/terraform validate` | task | ⬜ pending |
| 13-01-03 | 01 | 1 | PROD-08(infra) | T-13-01-04 | S3 BPA on; widget via OAC; CloudFront cert us-east-1 | infra | `terraform -chdir=deploy/terraform validate` | task | ⬜ pending |
| 13-02-01 | 02 | 1 | PROD-06 | T-13-02-01/SC | Titan v2 1024-dim guard; boto3 IAM (no static key) | unit | `pytest tests/unit/test_embedding_bedrock.py -x -q` | task | ⬜ pending |
| 13-02-02 | 02 | 1 | PROD-06 | T-13-02-01 | Both paths one provider seam; Voyage fallback intact | unit | `pytest tests/unit/test_embedding_bedrock.py tests/unit/test_embedding_service.py -x -q` | task | ⬜ pending |
| 13-03-01 | 03 | 1 | PROD-05 | T-13-03-01/02/03 | One pooled conn/turn; no named prepared stmts; finally-close | unit | `pytest tests/unit/test_agent_turn_connection_batch.py -x -q` | task | ⬜ pending |
| 13-04-01 | 04 | 2 | PROD-06 | T-13-04-01/02/04 | acks_late+idempotent; single-tenant; direct-conn REINDEX | unit | `pytest tests/unit/test_reembed_task.py -x -q` | task | ⬜ pending |
| 13-04-02 | 04 | 2 | PROD-06 | — | pipeline-queue registration | unit | `python -c "from app.worker.celery_app import celery_app; assert 'app.worker.tasks.pipeline.reembed' in celery_app.conf.include"` | n/a | ⬜ pending |
| 13-05-01 | 05 | 2 | PROD-09,10 | T-13-05-01/03 | data-api emitted; stable host; no disclaimer | source-assert | `grep -q 'data-api=' && grep -q 'data-agent=' && ! grep -qi 'not yet live'` (see plan) | n/a | ⬜ pending |
| 13-06-01 | 06 | 2 | PROD-12 | T-13-06-01/02 | Tenant-scoped S3 key; private bucket; no local write | unit | `pytest tests/unit/test_s3_uploads.py -x -q` | task | ⬜ pending |
| 13-06-02 | 06 | 2 | PROD-13 | T-13-06-03 | Parse from S3 bytes; /vrd-uploads cleanup removed | unit | `pytest tests/unit/test_s3_uploads.py tests/unit/test_parse_task.py tests/unit/test_embed_task.py -x -q` | task | ⬜ pending |
| 13-07-01 | 07 | 3 | PROD-14 | T-13-07-01/02 | ContextVar isolation; asyncio.run propagation; no bleed | unit | `pytest tests/unit/test_agent_tools_contextvar.py tests/unit/test_agent_tools.py tests/unit/test_agent_task.py -x -q` | task | ⬜ pending |
| 13-07-02 | 07 | 3 | PROD-15,06 | T-13-07-03/04 | prefork (not solo); concurrency=2; throttle lifted | unit+source | `pytest tests/unit/test_agent_tools.py -x -q && grep -q 'concurrency=2' deploy/terraform/fargate.tf` | n/a | ⬜ pending |
| 13-08-01 | 08 | 3 | PROD-01..04 | T-13-08-02 | SSE-survival smoke bounded just above 120s cap | infra | `bash -n scripts/smoke_fargate.sh` | task | ⬜ pending |
| 13-08-02 | 08 | 3 | PROD-01,02,03,04,07 | T-13-08-01 | 3 services healthy; /health 200 at stable domain | manual/live | `bash scripts/smoke_fargate.sh --target https://<api-domain>` (§health) | n/a | ⬜ manual |
| 13-08-03 | 08 | 3 | PROD-06 | T-13-08-02/03/04 | ALB SSE survives; re-embed quality ≥0.90; idempotent | manual/live | `bash scripts/smoke_fargate.sh` (§SSE) + `EVAL_E2E_ENABLED=1 pytest tests/test_eval_service.py` | n/a | ⬜ manual |
| 13-09-01 | 09 | 4 | PROD-08 | T-13-09-03 | S3 sync + CloudFront invalidation; not public | infra | `bash -n scripts/publish_widget.sh` | task | ⬜ pending |
| 13-09-02 | 09 | 4 | PROD-08,11 | T-13-09-01/02 | widget.js 200 from CloudFront (bucket private); external embed zero-edit | manual/live | external third-party page returns grounded agent.response | n/a | ⬜ manual |
| 13-10-01 | 10 | 4 | PROD-12,13 | T-13-10-01/02/03 | Upload lands under tenant-scoped S3 key; parse from S3; no local disk | manual/live | live upload + ingestion-complete; bucket-direct AccessDenied | n/a | ⬜ manual |
| 13-11-01 | 11 | 4 | PROD-15 | T-13-11-01/02 | prefork concurrency=2; two-tenant concurrent isolation | manual/live | two concurrent multi-tenant turns; distinct-corpus answers | n/a | ⬜ manual |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky · ⬜ manual = live AWS human-verify gate*

---

## Wave 0 Requirements (created in-task, tdd-style)

- [x] `apps/api/tests/unit/test_embedding_bedrock.py` — Titan v2 1024-dim + dimension guard + provider seam (created by 13-02-01)
- [x] `apps/api/tests/unit/test_agent_turn_connection_batch.py` — one pooled conn per turn (created by 13-03-01)
- [x] `apps/api/tests/unit/test_reembed_task.py` — idempotent + single-tenant + direct-conn REINDEX (created by 13-04-01)
- [x] `apps/api/tests/unit/test_s3_uploads.py` — S3 put/get stubbed + parse-from-bytes path (created by 13-06-01/02)
- [x] `apps/api/tests/unit/test_agent_tools_contextvar.py` — asyncio.run propagation + concurrency no-bleed (created by 13-07-01)
- [x] Existing pytest infra (route/Celery patterns) is extended, not reinstalled

*Key validation landmines from research: (1) embedding space-match after re-embed — retrieval regression test (13-08-03); (2) ALB idle-timeout vs SSE survival — live smoke (13-08); (3) ContextVar propagation across `asyncio.run()` — propagation test BEFORE the full refactor (13-07-01).*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ALB streams SSE to completion (no 60s cut) | PROD-01/04 | Needs live AWS ALB + agent turn | Deploy; run `scripts/smoke_vm.sh` §SSE against the ALB domain; confirm full event sequence + grounded answer |
| External-site embed works with zero hand-editing | PROD-11 | Needs a real third-party page + live CloudFront + ALB | Paste the copied snippet on an external test page; open; confirm widget loads from CloudFront and answers via the ALB `data-api` |
| Re-embed retrieval quality holds | PROD-06 | Needs live Bedrock + a tenant corpus | After backfill, run retrieval on known queries; compare top-k relevance to pre-migration baseline |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify (code tasks: pytest/grep/terraform validate; live gates: manual smoke, declared in Manual-Only)
- [x] Sampling continuity: every code task carries an automated verify; the only manual-only sequences are the live AWS gates (13-08/09/10/11), which are inherently human-checkpointed
- [x] Wave 0 covered: all five test scaffolds are created in-task (tdd-style) by their owning code tasks
- [x] No watch-mode flags (all pytest runs use `-x -q`, non-watch)
- [x] Feedback latency < 120s (unit suites are fast; live AWS checks are gated/manual)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** planner-approved 2026-06-29 (per-task map populated; nyquist_compliant=true)
</content>
