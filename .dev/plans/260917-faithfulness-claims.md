# Faithfulness rows carry the judge's claims (#290 step 1)

Branch `feat/faithfulness-claims`. Backend only, no labelling, gate unchanged.

## Shape

- `app/domain/judge_record.py`: `Claim(statement, supported, reason)` and
  `JudgeRecord.claims: tuple[Claim, ...] | None`. Claims exist only beside a score, and the
  share of supported claims must equal that score, so a claim list cannot land on the wrong row.
- `alembic_tenant/versions/0031_eval_result_claims.py`: `eval_results.claims JSONB`, nullable,
  no default. NULL means the judge recorded no claims, which is every row before 0031 and every
  row of a metric that has none.
- `app/services/faithfulness_metric.py`: `FaithfulnessWithClaims`, ragas 0.4 `Faithfulness`
  whose `ascore` keeps the statements and verdicts it already produced, on the
  `MetricResult.traces` slot ragas provides. `claims_of(result)` reads them back.
- `app/services/eval_service.py`: the faithfulness builder uses the subclass; `_ragas_cell`
  returns the claims beside the score; the score row carries them under `faithfulness_claims`;
  `build_judge_records` puts them on the faithfulness record; the INSERT writes `claims`, with a
  pre-0031 rung that logs what it loses.

## Gates

- unit: the type, the migration statements, the subclass over the canned ragas LLM, the row
  written by `_judge_row_params`, the pre-0031 rung observed to fire
- migration round trip against `wchats_tenant_probe` through `run_tenant_migrations`
- `scripts/gates.py full`

## Observed 2026-09-17

Migration round trip through `migrations.run_tenant_migrations` against `wchats_tenant_probe`,
`command.downgrade(cfg, "0030")` on the same injected connection for the way down:

```
before                   revision=0030 column=None comment=no
after upgrade head       revision=0031 column=('jsonb', 'YES', None) comment=yes
after downgrade 0030     revision=0030 column=None comment=no
after re-upgrade         revision=0031 column=('jsonb', 'YES', None) comment=yes
```

Mutations, each restored from a backup copy afterwards, since nothing was committed yet:

```
drop the share check in _as_claims        3 failed, 34 passed   (test_judge_record_claims + test_faithfulness_claims)
drop the pre-0031 rung, re-raise instead   1 failed, 19 passed   (test_faithfulness_claims)
restored                                  37 passed
```

Neighbourhood before the full battery: 353 passed across the eval service, scoring
concurrency, truncation, samples, provenance, rejudge and migration 0030/0031 tests.

## Adversarial review, 2026-09-17, and what it changed

Two blockers, both a single judge quirk failing a whole run where every other judge failure
degrades per cell: a blank statement in the judge's claims reached `JudgeRecord` as a refusal
out of `build_judge_records`, after every agent turn and judge call was paid for; and
model-authored claim text reached the jsonb column through bare `json.dumps`, the #117 shape.
Fixed by `_judge_record`, which logs `build_judge_records.claims_dropped` and writes the record
without claims, and `_bounded_claim_text`, which scrubs each field for the sink and cuts it at
`CLAIM_FIELD_CHAR_CAP`. Also fixed: the score row key is written by the constant, the NaN guard
in `_ragas_cell` has a pin, the parity test runs three judge shapes, `Claim` is in `CONTEXT.md`,
the migration names where its round trip is recorded.

Mutations after the fix, restored from a backup copy each time:

```
drop the per-cell degrade in _judge_record   2 failed, 27 passed   (test_faithfulness_claims)
drop the scrub in _bounded_claim_text        1 failed, 28 passed
key the degrade on the row, not the metric   1 failed, 33 passed   (after the second review)
restored                                     34 passed
```

A second review of the fix found the degrade branch deciding by the row rather than the
metric, so a bad score on another metric beside real faithfulness claims logged
`claims_dropped` for a metric that never had claims. The branch now re-raises for every metric
but faithfulness, a claim field that is not a string is refused rather than rendered with
`str()`, and a verdict that is not a bool is refused rather than coerced.

Not changed, by design: nothing reads `eval_results.claims` yet. Step 3 of #290 is the reader.
