# Trace — D6 P1 adversarial-review fixes

**Branch** `feat/d6-labelling-loop` **Commits** `9e43d80`, `e682106`, `f23930e`, + this docs commit
**Input** `.dev/reference/d6-p1-adversarial-review.md` (15 findings, 8 unsupported claims)
**Full report** `.dev/reference/d6-p1-review-fixes.md` — dispositions, 12 mutation proofs, gate output

Bounded to the review's findings. **P2 not started.**

## What changed

| path | |
|---|---|
| `apps/api/tests/unit/test_label_provenance.py` | R3 rewritten as two scans (composed-SQL reconstruction + name-level absence pin), 8 forgery fixtures, R2 region narrowed to `app/api/v1/evals.py`, fixture ban over all 159 test files, composed-import-path arms, documented blind spot, SET-clause parse |
| `apps/api/app/services/label_service.py` | R4's two context detectors split: `ImportError` silent, any other exception refuses. Docstring corrected on R2 and R3; new section on what the four restrictions do **not** cover |
| `apps/api/app/services/eval_service.py` | `label_trust_tier()` returns `unknown` for a non-scenario mapping and for a human tier over a present-and-empty `reference_answer` |
| `apps/api/app/services/decision_eval_service.py` | `FIXTURE_LABEL_TRUST_TIER` → `FIXTURE_LABEL_PROVENANCE`; field → `label_provenance`; report key → `fixture_label_provenance` |
| `apps/api/tests/unit/test_decision_eval_service.py` | follows the rename; new test that no fixture and no report reads as a labelled eval scenario |
| `apps/api/alembic_tenant/versions/0016_...py` | CHECK gains `AND COALESCE(reference_answer,'') <> ''` inside the human-tier arm; catalog lookup and DROP schema-qualified; the "presence IS the human claim" overclaim reworded |
| `apps/api/tests/unit/test_migration_tenant_0016.py` | three new tests (empty-answer arm, schema qualification, head identity); roundtrip updated |

## Decisions

- **Two scans, not one.** A single stronger scan would have one blind spot doing all the work —
  which is the defect being fixed, one level up. The reconstruction and the name pin fail on
  different things, and both fail closed.
- **The claim is downgraded to what is true.** "Physically cannot" → "no forgery shape anyone has
  devised passes unnoticed". The residual (fragment-composed identifiers inside the allowlisted
  reader) is written down as `BACKLOG 4.8` rather than argued away.
- **Docstrings exempt from both detectors.** Prose is not reachability, and a detector that fires on
  the explanation teaches the next author to delete the explanation. Both directions have controls.
- **The decision-eval collision fixed twice** — renamed at the source *and* refused by the resolver.
  The rename alone depends on every future module choosing a different spelling.
- **`labelled_by` decided, not pinned.** The principal-derivation rule is written into
  `label_service`'s docstring and `BACKLOG 4.7`. The route does not exist, and a test asserting a
  property of an unwritten module passes vacuously.

## Deviations

- **The `test_migration_tenant_0015.py` packaging finding cannot be fully closed.** Its suggested
  fix is to move the edit into its own commit; that means rewriting `c860780`, and the task forbids
  rebasing. Head identity is instead pinned in the file this phase owns, and the misleading sentence
  is deleted from the reference doc.
- **The control reads 1874, not 1873**, because one test was added to a pre-existing test file
  (`test_decision_eval_service.py`) to pin the collision from both sides. Deselecting exactly that
  test gives 1873/11 — run, observed, recorded in the reference doc.

## Not proven

Migration 0016 still has never been applied — no PostgreSQL here, the roundtrip skips, and a skip is
unobserved. Neither the new CHECK arm nor the schema qualification has executed against any
database, and neither can be from this machine.
