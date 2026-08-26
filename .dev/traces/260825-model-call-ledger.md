# The model client factory, the ModelCall ledger and pricing (#46)

Ticket #46, decision #22 on map #4 with its rand addendum. Four commits on
`feat/model-call-ledger` off main at the #43 merge: pricing domain (`88e4db1`),
factory and hook (`c067187`), rollup (`b41e0c8`), review round (`27c5ca4`). Closes #29
and #30 with it. Runs parallel to PRs #71 and #74.

## The seam and the fact

`make_client(purpose, provider, credentials, recorder)` builds every direct-API
client; an httpx event hook parses `usage` and `model` from each response body into a
frozen `ModelCall` handed to the injected recorder, so instructor and Ragas will be
counted untouched when #47 migrates them. Tokens are the stored fact in the tenant
`model_calls` table (migration 0019); money is derived at read time by pure functions
over a versioned price book and a dated fx table in `app/domain/pricing.py`, and an
unknown model raises `UnknownPrice` rather than pricing at zero. The daily rollup
(control migration 0020, beat at 00:30 UTC) upserts per (tenant_id, purpose, day)
where day is the CAT calendar date, per the decision's letter; an unpriced group keeps
its tokens with NULL money and a loud log.

## Decisions and deviations recorded

- `model_source` is a three-value enum: `reported`, `mapped_by_docs`, and `unreported`
  for a body naming no model. The decision named two; the third exists because
  inventing provenance for a stripped response poisons the audit the enum serves.
- Recording is fail-open with a loud log; a Customer turn never dies for telemetry.
  The eval path can revisit this when `EvalResult` lands (#51).
- The fx seed is a dated market close (16.0237, source recorded) because the SARB site
  refused the connection; the first real rollup replaces it. `deepseek-v4-pro` is
  absent from the book, so #47 must fetch its tariff before the Attacker migrates.
- `cache_creation` prices at the fresh input rate; the fetched tariff names no write
  premium, and the row is explicit.
- The rollup day was UTC for one commit because the slice brief said so against the
  decision's CAT line; the review caught it, and the fix deleted the version-straddle
  machinery outright, one book version and one fx date per CAT day.
- A streamed response skips the ledger with a warning naming the gap; stream parsing
  belongs to the owned loop (#48).
- The e2e narrows its fan-out to the seeded tenant because the local control DB holds
  agent rows whose connection strings name live Neon; a test never opens those.

## Also fixed on the way

`test_strategy_service.py` deleted `sys.modules["anthropic"]` instead of restoring
it, so a later `patch("anthropic.Anthropic")` missed and sent a live request that
returned 401 from api.anthropic.com. It restores the module now.

## Evidence, observed

- Red-first at every stage across all four commits, including the CAT boundary red
  (a 22:30 UTC call asserted into the next CAT day) and the `ON CONFLICT` idempotency
  guard mutated to a `UniqueViolation` red, restored.
- A probe row seeded through the real recorder read back and priced
  `(Decimal('0.001408'), '2026-08-23.1')` and `(Decimal('0.0225613696'),
  'usd_zar-2026-08-24')`; the rollup's two runs produced identical rows.
- Migration round trips observed on both trees (tenant 0019 on the probe, control
  0020 on local `wchats_control`), re-run after the CAT wording change.
- `full gates passed in 616.1s.` at slice C and `611.2s.` after the review round;
  suite `2750 passed, 13 skipped`; collection 2763; inserted dashes 0 across 4,516
  inserted lines; the money path is Decimal end to end with the no-rounding rule
  stated where sums happen.
- Baselines untouched on this branch in both directions.
- `Ledger` entered CONTEXT.md; the entry anchors after Provisioning to avoid a merge
  collision with #74's Outcome entry at the Transactional spot.
