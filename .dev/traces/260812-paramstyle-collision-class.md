# TRACE — `:param::type`, a bug class with three live instances in production

**Date:** 2026-08-12 · **Branch:** `chore/local-postgres` · **Commit:** `c65137e`
**Plan:** `.dev/plans/260812-paramstyle-collision-class.md` ·
**Proofs:** `.dev/reference/260812-paramstyle-mutation-proofs.md`

Continuation of `1.14`, which yesterday's flag-ON run surfaced and which was filed rather than
fixed. Scanning for the *class* rather than the instance changed the size of it.

## What changed

| File | Change |
|---|---|
| `services/deployment_service.py` | `:window_days::text` → `CAST(:window_days AS text)` ×2 |
| `worker/tasks/runtime/digest.py` | `:payload::jsonb` → `CAST(:payload AS jsonb)` |
| `tests/unit/test_sql_paramstyle_collisions.py` | **new** — the class gate + characterization |
| `tests/integration/test_paramstyle_real_db.py` | **new** — the statements, run by a real server |

## The mechanism, and the correction to `1.14`

The row said SQLAlchemy "leaves the bindparam unbound". Right symptom, wrong mechanism, and the
difference is the whole reason this survived review. SQLAlchemy's regex is
`(?<![:\w\x5c]):(\w+)(?!:)`; the trailing lookahead exists to avoid reading `::` casts as
parameters, and against `:window_days::text` the greedy `\w+` **backtracks one character**:

```
'SELECT :window_days::text'  -> ['window_day']    trailing 's' eaten
'SELECT :payload::jsonb'     -> ['payloa']        trailing 'd' eaten
'SELECT :b::int'             -> []                matches nothing at all
```

It invents a **silently misnamed** parameter. Nothing errors at construction; the value the call
site passes matches nothing; the literal `:` reaches Postgres and it raises. The string looks
correct on the page, which is exactly why five phases read past it.

## Three instances, and they fail in opposite directions

**`deployment_service.py:1237`/`:1253` fail SOFT — the dangerous one.** The caller catches, logs
`blast_radius_fetch_failed`, and substitutes a fallback where every `configured_max_*` and
`observed_max_*` is `None` while `warn_threshold_*` and `observed_window_days` populate from
settings. The payload therefore reads like a tenant with no history. **Phase 18's blast-radius
warnings (control `0019`) have never once evaluated real exposure.** Same shape as `5.13` and
`2.28`: a fail-soft `except` converting a permanently broken statement into a plausible empty state.

**`digest.py:87` fails LOUD, and is worse than it looks.** That INSERT is the WR-02 idempotency
anchor, committed *before* `send_digest_email` precisely so a failed send cannot double-send. It
raised, the task's outer `except` retried 3× and re-raised. So no `digest_runs` row has ever been
written and **`send_digest_email` has never been reached — OPS-04 has never sent a digest.** This
makes `5.2`'s complaint concrete rather than procedural: `REQUIREMENTS.md` line 415 ticks the weekly
digest as Phase 21 Complete.

## The scan found more than the grep did

A line-oriented `grep -rnoE ":[a-z_]+::[a-z]+"` over `app/` returned exactly the three sites. The
AST-based gate, which reads *concatenated* and f-string literals, is what would have caught a site
split across adjacent string fragments — and on its first run it also produced four false positives,
which is the next section.

## The gate caught its own defect before the commit

First run reported `widget.py:106,129,147,800`. Those are Redis key builders —
`f"rate:config:{client_ip}:{bucket}"` — and the scanner was dropping `{interpolations}`, fusing the
literals either side into `rate:config::`. That is retro **Family F**, "over-broad mechanical gates
produce false positives on their own prose", which this plan's own Risks section had predicted.
Fixed by substituting a `\x00` sentinel per interpolation, and **mutation-proved (M9)**: remove the
sentinel and all four come back.

## Why nothing caught any of this for months

Every test of these paths mocks the session, and a `MagicMock` accepts a string no database would.
`tests/unit/test_digest_service.py` has four tests; the only one reaching the INSERT region seeds
`fetchone` to return a row so the function returns **early** and the INSERT never executes. Same
shape as `2.29`, and the same retro family as yesterday's work (**Family I** — the boundary shape was
never checked). A SQL string that no database ever parses is not a query, it is a comment.

Hence the two-part fix: a static gate that needs no database, and an integration module that reads
the statements from their real source and makes a real server run them. One of its assertions goes
past "does not raise" — it seeds audit rows inside and outside the trailing window and pins that the
window bound, the very parameter that was never bound, actually discriminates.

## Observed

```
tests/unit/test_sql_paramstyle_collisions.py            9 passed   (new)
tests/integration/test_paramstyle_real_db.py            3 passed   (new)
tests/unit/test_digest_service.py + the above          13 passed
deployment/confirmation/task/migration_0019 modules    161 passed, 1 skipped
```

Four mutation proofs — M7, M8, M9, M10 — each red then green, verbatim in the reference file. M10
reproduces the exact production error, `psycopg2.errors.SyntaxError: syntax error at or near ":"`.

## Deviations from the plan

- The plan expected three sites. The AST gate is what confirmed there were no more; the grep that
  sized the plan could not have seen a split literal.
- The plan proposed excluding "comments/docstrings" from the scan. Implemented as: read only string
  **constants** via AST (so comments never enter), and skip docstring nodes. Comments were never a
  risk once the scan stopped reading raw source; f-strings turned out to be the real one.
- Two mutation attempts for M9 failed to mutate anything because a `\\x00` needle lost its escape in
  the heredoc and became a NUL byte. Recorded in the proofs file, because those runs looked exactly
  like "the mutation had no effect" — which is indistinguishable from a tautology if you stop there.

## Consequences the owner should know about

- **`run_weekly_digest` will start working.** It will begin writing `digest_runs` rows and calling
  `send_digest_email` for the first time. On a beat worker with SMTP configured that means **real
  email to real recipients**, from a task that has never sent one. This is behaviour change on an
  outward-facing path and is adjacent to `0.4`; it should be an explicit decision before any beat
  worker runs this code, not a side effect of a bug fix.
- **Blast-radius numbers become real**, so deploy checklists may emit warnings that have never fired.
  Correct, and it will look like a regression to anyone reading the checklist.
- Neither is a reason to hold the fix — a silently broken query is worse than either — but both are
  reasons the first production run should be watched rather than assumed.
