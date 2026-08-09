# Trace — D6 P1, the label trust tier and the wall around it

**Branch** `feat/d6-labelling-loop` (off `feat/d1-agent-invocation` @ `4179a5c`, **not `main`**)
**Commits** `c860780`, `8bc6f38`, `316ab9a`, `8c956f1`, `aeb949b`
**Plan** `.dev/plans/260808-d6-labelling-loop.md` § P1
**Reference** `.dev/reference/d6-p1-label-trust-tier.md` (the long form, with all 10 mutation proofs)

> **Written retrospectively on 2026-08-09.** P1 landed on 2026-08-08 with no trace, which the
> adversarial review flagged as a process defect: CLAUDE.md says *"No task is done without its
> trace"*. This file closes that. It records what P1 actually did; the corrections the review then
> forced are in `260809-d6-p1-review-fixes.md`.

## What changed

| path | |
|---|---|
| `apps/api/alembic_tenant/versions/0016_eval_scenario_label_provenance.py` | new — `label_trust_tier` / `labelled_by` / `labelled_at`, all nullable, no DEFAULT, no backfill, plus one named CHECK |
| `apps/api/app/services/label_service.py` | new — `record_human_label`, the only human-tier write, and four restrictions around it |
| `apps/api/app/services/eval_service.py` | `HUMAN_LABEL_TIERS`, `LABEL_TIER_COLUMN`, `is_human_label_tier()`, `label_trust_tier()`, `is_human_labelled()` |
| `apps/api/tests/unit/test_label_provenance.py` | new — vocabulary, R1–R4, the write, the absence pins |
| `apps/api/tests/unit/test_migration_tenant_0016.py` | new |
| `apps/api/tests/unit/test_migration_tenant_0015.py` | head assertion relaxed `heads == {"0015"}` → `len(heads) == 1` |

## Decisions

- **The tier is carried by the LABEL, not inferred from `source`.** A mined production failure the
  owner answers by hand is `customer_negative` in origin and `human_authored` in label at the same
  time. Fusing them is how a model-written string gets admitted on a human tier.
- **0011's `source` CHECK was NOT widened** — a deviation from the literal instruction. Adding
  `source='owner_authored'` re-collapses origin into label *and* makes
  `is_promotable_to_verified_qa()` return True for a schema-allowed source, opening the
  customer-facing `verified_qa` write the owner settled eval-only on 2026-08-08.
- **A CHECK where 0014/0015 banned one.** The lesson from 0005 is about *unnamed inline* CHECKs on a
  column live INSERTs already write. 0016's is explicitly named, on a brand-new column.
- **No API route, no selector change.** `GET` unlabelled / `POST` a label is P2; downstream is P3.

## Deviations

- The head assertion in `test_migration_tenant_0015.py` — a file P1 does not own — was relaxed
  inside the feature commit rather than in a commit of its own. The relaxation is right; its
  packaging was not, and it was justified in the P1 report with a control that cannot see an
  assertion getting weaker. Head identity is now pinned in `test_migration_tenant_0016.py`.

## Not proven

Migration 0016 has never been applied. No PostgreSQL on this machine; the roundtrip skips and a skip
is unobserved. No real `eval_scenarios` row has been labelled; `record_human_label` has only run
against a recording cursor. R4 has never run in a real Celery worker.

## What the review then found

The headline claim — *"no model may write at a human tier, four independent structural
restrictions"* — was **refuted by observation** for the direct-SQL route. See
`.dev/reference/d6-p1-adversarial-review.md` and the fixes trace.
