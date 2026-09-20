# The review of flagged claims (#290 step 3)

Branch `feat/claims-review`. Backend first; the page in the console is last and is the only
part the owner looks at. Gate rule unchanged; the threshold stays at 0.80.

## Shape

- `app/domain/claim_review.py`: `ClaimReview(eval_run_id, scenario_id, position, statement,
  supported, reviewed_at)`. One Tenant answer on one flagged claim.
- `alembic_tenant/versions/0032_claim_reviews.py`: `claim_reviews`, unique on (run, scenario,
  position), so a second answer replaces the first.
- `app/services/claim_review_service.py`: `read_flagged_claims` (the judge's unsupported claims
  on a run's faithfulness rows, joined to `eval_samples` for the answer and the passages, with the
  stored answer beside each), `write_claim_reviews` (upsert, refusing an answer about a claim the
  run did not flag or whose statement differs), `read_claim_reviews`.
- Routes: `GET /agents/{id}/eval-runs/{run}/claims`, `POST .../claims/review`. The payload
  carries no judge reason and no judge verdict.
- `tests/evals/calibration/benchmark/import_reviews.py`: turns a run's answers into
  `truth.csv` rows (`source=review`, `novel` empty) and its answers into `rows.csv` rows, so the
  scorer's precision becomes a ratio. Owner-run, reads `CALIBRATION_TENANT_DSN`.

## Gates

- unit: the type, the migration statements, the service over a fake connection (flag listing
  drops supported claims and the judge's reason; refusals; upsert params; the 0032 fallback),
  the two routes (shape, IDOR, 422 on a stray answer), the import script over fixtures
- migration round trip on `wchats_tenant_probe`
- `scripts/gates.py full`

## Observed 2026-09-20

Migration round trip through `migrations.run_tenant_migrations` against `wchats_tenant_probe`,
`command.downgrade(cfg, "0031")` on the same injected connection for the way down:

```
before                   revision=0031 table=no unique=0 index=no
after upgrade head       revision=0032 table=yes unique=1 index=yes
after downgrade 0031     revision=0031 table=no unique=0 index=no
after re-upgrade         revision=0032 table=yes unique=1 index=yes
```

Mutations on the service, restored from a backup copy each time (test_claim_review_service):

```
drop the "judge flagged no claim" refusal    2 failed, 20 passed
leak the judge's reason into the payload     1 failed, 21 passed
list supported claims beside the flags       6 failed, 16 passed
restored                                     22 passed
```

## Adversarial review, 2026-09-20, and what it changed

Two blockers. The import wrote into the platform benchmark and so moved the numbers pinned per
judge identity; it now writes a second benchmark, `benchmark/reviewed/`, and carries no Tenant
text: the run and scenario id, the judge's statements and whether it found them, the Tenant's
answers. Reviewed truth with no fabricated word matched by overlap alone, so a neighbouring judge
claim the Tenant never saw inherited their answer; a truth row with `novel` empty now matches the
judge's own statement exactly and nothing else, and the answer-level "planted" split counts
construction truth only. Also fixed: a whitespace statement was 500 where 422 was promised; a
tenant behind 0031 had no rung and behind 0032 the write was a bare 500 while the read looked
like "none answered", so the listing carries `reviews_available` and the write raises
`ClaimReviewsUnavailable` (503); one unreadable stored claim cost the whole listing, now that
scenario alone with a log line and a count; the passages repeated once per flag, now once per
scenario; the payload surface nobody called is gone. The service's SQL runs against a throwaway
tenant database in `tests/integration/test_claim_reviews_db.py`, observed:

```
INTEGRATION_TESTS_ENABLED=1 pytest tests/integration/test_claim_reviews_db.py -m integration
3 passed in 5.19s
```

Left as noted: `run_id` is scoped to the Tenant's agent and not to the agent's runs, the
precedent every route in `evals.py` keeps; a rejudge run is reviewable and its reviews would land
under a second key for the same answer text.
