# PLAN — `:param::type` is a bug CLASS, and three live instances are in production code

**Date:** 2026-08-12 · **Branch:** `chore/local-postgres` · **Source:** `1.14` (filed 2026-08-11
from the flag-ON run), extended by scanning for the class.

## Goal

Fix the three production instances, and — the actually valuable part — **turn the class into a
gate**, so the ninth instance cannot be written. `1.1` records fixing five of these in tests; I found
three more in `app/`. Eight instances across the repo's history is a class, not a series of slips.

## The mechanism, verified rather than assumed

SQLAlchemy 2.0.49's bindparam regex is `(?<![:\w\x5c]):(\w+)(?!:)`. The trailing `(?!:)` exists to
avoid mistaking PostgreSQL's `::` cast for a parameter — and it makes `:param::type` parse wrong.
`\w+` is greedy, fails the lookahead on the first `:` of `::`, and **backtracks by one character**:

```
'SELECT :window_days::text'          -> bindparams found: ['window_day']    # trailing 's' eaten
'SELECT :payload::jsonb'             -> bindparams found: ['payloa']        # trailing 'd' eaten
'SELECT :a, :b::int'                 -> bindparams found: ['a']             # ':b::int' matches nothing
'SELECT CAST(:window_days AS text)'  -> bindparams found: ['window_days']   # correct
```

So it does **not** merely leave the parameter unbound — it silently invents a **misnamed, truncated**
one. The value passed by the call site then matches nothing, the literal `:` reaches Postgres, and
it raises. Filed row `1.14` said "leaves the bindparam unbound", which is the right symptom and the
wrong mechanism; correct it.

Confirmed against the real local Postgres, both forms raising
`psycopg2.errors.SyntaxError: syntax error at or near ":"`, and four candidate fixes all returning
values: `CAST(:p AS text)`, `make_interval(days => :p)`, `CAST(:p AS jsonb)`, and even `:p ::jsonb`
(a space defeats the lookahead).

## The three instances and what each costs

| Site | Consequence |
|---|---|
| `deployment_service.py:1237` | blast-radius observed single-action max |
| `deployment_service.py:1253` | blast-radius observed hourly aggregate max |
| `digest.py:87` | **`run_weekly_digest` raises on every run** |

**`deployment_service` fails SOFT and is therefore the more dangerous of the two.** The caller
catches, logs `blast_radius_fetch_failed`, and substitutes a fallback in which every
`configured_max_*` / `observed_max_*` is `None` while `warn_threshold_*` and `observed_window_days`
populate from settings — so the payload reads like a tenant with no history. Phase 18's blast-radius
warnings (control `0019`) have never evaluated real exposure.

**`digest.py` fails LOUD, and it is worse than it looks.** That INSERT is the WR-02 idempotency
anchor, committed *before* `send_digest_email` precisely so a failed send cannot double-send. The
statement raises, the task's outer `except` retries 3×, then re-raises. So no `digest_runs` row has
ever been written and **`send_digest_email` has never been reached** — OPS-04 has never sent a
digest. Note this makes `5.2`'s complaint concrete: `REQUIREMENTS.md` ticks the weekly digest as
Phase 21 Complete.

## Why nothing caught any of them

Every test of these paths mocks the session. `tests/unit/test_digest_service.py` has four tests and
`test_digest_idempotency_within_7d` — the only one that reaches the INSERT region — seeds
`fetchone` to return a row so the function *returns early* and the INSERT never executes;
`mock_db.execute` is a `MagicMock` that accepts any string. Same shape as `2.29`, and the same retro
family as yesterday (**Family I** — the boundary shape was never checked). A SQL string that no
database ever parses is not a query, it is a comment.

## Approach

1. **Fix the three sites** with `CAST(:p AS type)` — the minimal edit that preserves the surrounding
   expression exactly. Deliberately *not* `make_interval`, which would be a rewrite rather than a
   paramstyle fix, and not the whitespace trick, which reads like a typo and invites re-breaking.
2. **The class gate (the point of this plan).** A unit test scanning `app/` for `:\w+::` — cheap,
   total, no DB, and it fails with the mechanism written into the message. This is what converts a
   recurring class into something that cannot recur.
3. **A characterization test of the trap itself**, asserting the truncation directly
   (`text("SELECT :window_days::text")` yields `window_day`). It documents *why* the gate exists, and
   it will fail loudly if a future SQLAlchemy changes the regex — at which point the gate can be
   relaxed deliberately instead of silently.
4. **Real-DB execution proof** (integration): run both fixed statement shapes against local
   Postgres. The existing tests could not have caught this precisely because no database ever saw
   the string.

## Files

- `apps/api/app/services/deployment_service.py` (×2)
- `apps/api/app/worker/tasks/runtime/digest.py` (×1)
- new `apps/api/tests/unit/test_sql_paramstyle_collisions.py` (gate + characterization)
- `apps/api/tests/integration/…` — real execution of the fixed statements

## Risks

- **`digest.py` starts working.** `run_weekly_digest` will begin writing `digest_runs` rows and
  calling `send_digest_email` for the first time. That is the fix, but it means a beat worker on a
  configured SMTP host starts sending real email to real people. Must be stated in the trace and
  raised to the owner — it is behaviour change on an outward-facing path, adjacent to `0.4`.
- Blast-radius numbers start being real, so deploy checklists may begin emitting warnings that never
  fired before. Correct, and it will look like a regression to anyone reading the checklist.
- The scan is a mechanical gate over source text; `4.x`/Family F record that such gates produce
  false positives on prose. Scope it to `app/**.py` and exclude comments/docstrings, or it will trip
  on this plan's own examples.

## Tests

- Gate: zero `:\w+::` in `app/`; mutate by reinstating one site, observe red.
- Characterization: the truncation behaviour, asserted exactly.
- Per-site: the three fixed statements bind the parameter names their call sites actually pass.
- Integration: both shapes execute against real Postgres and return a value.
- `digest.py`: a test that actually **executes** the INSERT rather than returning early.
