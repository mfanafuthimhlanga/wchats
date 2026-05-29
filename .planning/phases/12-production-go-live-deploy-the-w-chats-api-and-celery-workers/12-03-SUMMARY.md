---
phase: 12-production-go-live-deploy-the-w-chats-api-and-celery-workers
plan: "03"
subsystem: documentation/architecture
tags: [adr, cloud-native, aws, cutover, d-15]
dependency_graph:
  requires: []
  provides: [D-15-adr]
  affects: [future-aws-migration-phase]
tech_stack:
  added: []
  patterns: [Nygard-ADR-format]
key_files:
  created:
    - docs/adr/0001-cloud-native-cutover.md
  modified: []
decisions:
  - "[12-03] D-15: ADR documents cloud-native AWS cutover with concrete trigger threshold (50 tenants / $100/mo / >80% RAM 7 days / SLA requirement)"
  - "[12-03] Flip mechanism confirmed config-only via D-14 _find_env_file seam — no code rewrite required for compute flip"
  - "[12-03] Data migration (Neon -> Aurora) explicitly separated from compute flip as distinct pg_dump/restore task"
metrics:
  duration: "~8 min"
  completed_date: "2026-05-29"
  tasks_completed: 1
  files_created: 1
  files_modified: 0
---

# Phase 12 Plan 03: Cloud-Native Cutover ADR Summary

## One-Liner

Nygard-format ADR documenting the cloud-native AWS cutover target (ECS Fargate + Aurora Serverless v2 + pgvector RLS + Bedrock) with four concrete trigger thresholds and a config-only flip mechanism via the D-14 env seam.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Write cloud-native cutover ADR (D-15) in Nygard format | 91fa468 | docs/adr/0001-cloud-native-cutover.md |

## What Was Built

`docs/adr/0001-cloud-native-cutover.md` — a 266-line Nygard-format Architecture Decision Record documenting:

- **Status / Date / Deciders** header fields (Proposed, 2026-05-29, Bantuson)
- **Context** section: current Oracle Cloud Always Free baseline, per-tenant Neon project constraint (Neon project limit + eval branching requirement), D-14 env seam explanation
- **Decision** section: cut over to AWS when any trigger threshold is met
- **Target Architecture** section: ECS Fargate compute (uvicorn + Celery; pipeline on Fargate Spot), Aurora Serverless v2 + pgvector + RLS/schema-per-tenant (replaces per-tenant Neon projects), Aurora fast clones replacing Neon eval branching, Amazon Bedrock for Claude + embeddings, SQS/ElastiCache Redis for broker
- **Trigger Threshold** section: four concrete conditions — tenant count exceeds ~50, monthly API spend exceeds $100, VM RAM sustained >80% for 7 days, or an uptime SLA is required
- **Flip Mechanism** section: compute flip is config-only (D-14 `_find_env_file` seam; swap `.env` values, no source code changes); data migration (Neon → Aurora) is a separate pg_dump/restore task explicitly decoupled from the compute flip
- **Consequences** section: positive (scalability, managed ops, RLS isolation, fast eval clones, consolidated billing) and negative (cost, AWS lock-in, migration effort, RLS complexity, Bedrock model availability)

## Acceptance Criteria Verification

```
FILE EXISTS:                PASS
Has "Trigger Threshold":    PASS
Has Aurora:                 PASS
Has Fargate:                PASS
Has Bedrock:                PASS
ECS Fargate:                PASS
Aurora Serverless v2:       PASS
pgvector:                   PASS
RLS/schema-per-tenant:      PASS
Aurora fast clones:         PASS
Trigger condition mentions: 15 (>= 4 required)
Env/config seam mentions:   19 (cites _find_env_file, config.py, D-14)
pg_dump:                    PASS
Status / Date / Deciders:   PASS
Line count:                 266 (>= 40 required)
Secret scan:                PASS (no sk-ant, postgresql://, voyage-)
```

## Deviations from Plan

None — plan executed exactly as written. The ADR content followed the RESEARCH.md D-15 outline verbatim as instructed, with prose expanded to portfolio-artifact quality. No TBDs, no "v1/future" hedging on the decision itself.

## Known Stubs

None — this is a documentation-only plan. The ADR documents architecture; it does not stub any runtime functionality.

## Threat Flags

T-12-03-01 (Information Disclosure — ADR accidentally embedding secrets) verified mitigated: `grep -n "sk-ant\|postgresql://\|voyage-" docs/adr/0001-cloud-native-cutover.md` returns nothing. The ADR contains architecture descriptions and threshold values only.

## Self-Check: PASSED

- [x] `docs/adr/0001-cloud-native-cutover.md` exists (created mode confirmed in git commit)
- [x] Commit 91fa468 exists: `git log --oneline | grep 91fa468` confirmed
- [x] No unexpected file deletions in commit
- [x] All acceptance criteria verified via grep assertions above
