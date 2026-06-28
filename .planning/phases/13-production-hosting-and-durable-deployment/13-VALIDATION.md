---
phase: 13
slug: production-hosting-and-durable-deployment
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-28
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

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 13-01-01 | 01 | 1 | PROD-01..07 | T-13-01 / — | TLS-only ALB; secrets from Secrets Manager not env files in image | infra/smoke | `bash scripts/smoke_vm.sh <alb-url>` | ❌ W0 | ⬜ pending |
| 13-01-02 | 01 | 1 | PROD-06 | — | re-embed idempotent; query+doc same model | unit+integration | `pytest apps/api/tests -k embed -q` | ❌ W0 | ⬜ pending |

*Planner replaces these seed rows with the full per-task map. Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `apps/api/tests/unit/test_embedding_bedrock.py` — Bedrock Titan v2 client returns 1024-dim; dimension-match guard
- [ ] `apps/api/tests/unit/test_agent_tools_contextvar.py` — concurrency>1 no-state-bleed (ContextVar isolation, PROD-14/15)
- [ ] `apps/api/tests/unit/test_s3_uploads.py` — S3 put/get round-trip stubbed (moto or boto stub), parse-from-bytes path
- [ ] Existing pytest infra covers route/Celery patterns — extend, don't reinstall

*Key validation landmines from research: (1) embedding space-match after re-embed — retrieval regression test; (2) ALB idle-timeout vs SSE survival — live smoke; (3) ContextVar propagation across `asyncio.run()` — concurrency test before full refactor.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ALB streams SSE to completion (no 60s cut) | PROD-01/04 | Needs live AWS ALB + agent turn | Deploy; run `scripts/smoke_vm.sh` §SSE against the ALB domain; confirm full event sequence + grounded answer |
| External-site embed works with zero hand-editing | PROD-11 | Needs a real third-party page + live CloudFront + ALB | Paste the copied snippet on an external test page; open; confirm widget loads from CloudFront and answers via the ALB `data-api` |
| Re-embed retrieval quality holds | PROD-06 | Needs live Bedrock + a tenant corpus | After backfill, run retrieval on known queries; compare top-k relevance to pre-migration baseline |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s (unit)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
</content>
