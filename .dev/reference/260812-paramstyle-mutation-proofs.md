# Mutation proofs — the `:param::type` collision class (BACKLOG 1.14)

**Date:** 2026-08-12 · **Branch:** `chore/local-postgres` · **Fix commit:** `c65137e`
**Method:** mutate, observe red, `git checkout HEAD -- <file>` unconditionally, observe green.
Verbatim output.

---

## The mechanism, measured before anything was changed

SQLAlchemy 2.0.49's bindparam regex, read from the installed package
(`elements.TextClause._bind_params_regex.pattern`):

```
(?<![:\w\x5c]):(\w+)(?!:)
```

The trailing `(?!:)` is deliberate — it stops `::` casts being read as parameters. It also makes
`:param::type` parse **wrong rather than not at all**, because `\w+` is greedy and backtracks one
character to satisfy the lookahead:

```
'SELECT :window_days::text'          -> bindparams found: ['window_day']
'SELECT :payload::jsonb'             -> bindparams found: ['payloa']
'SELECT :a, :b::int'                 -> bindparams found: ['a']
'SELECT CAST(:window_days AS text)'  -> bindparams found: ['window_days']
```

So the statement acquires a **silently misnamed** parameter. The name the call site passes matches
nothing, the literal `:` survives into the SQL, and Postgres rejects it. Confirmed against the real
local server, both production forms:

```
deployment_service :window_days::text      -> ProgrammingError: (psycopg2.errors.SyntaxError) syntax error at or near ":"
digest :payload::jsonb                     -> ProgrammingError: (psycopg2.errors.SyntaxError) syntax error at or near ":"
```

and four candidate repairs all returning values (`CAST(:p AS text)`, `make_interval(days => :p)`,
`CAST(:p AS jsonb)`, and `:p ::jsonb` — a space defeats the same lookahead). `CAST` was chosen: it
preserves each surrounding expression exactly, unlike `make_interval`, and does not read as a typo,
unlike the space.

**This corrects `1.14` as filed**, which said the parameter is "left unbound". The symptom was right
and the mechanism was wrong, and the difference matters: a truncated-but-present parameter is why
five phases of review read past these — the string looks exactly right.

---

## M7 — `deployment_service.py`: reinstate `:window_days::text`

```
mutated
FAILED tests/unit/test_sql_paramstyle_collisions.py::test_the_blast_radius_queries_bind_window_days
2 failed, 7 passed in 1.62s
--- restored ---
9 passed in 1.59s
```

Two failures, which is the intended shape: the class gate fires **and** the per-site bind assertion
fires. Either alone would be a weaker claim — the gate is a text scan, the per-site test asserts
SQLAlchemy actually binds the name the call site passes.

## M8 — `digest.py`: reinstate `:payload::jsonb`

```
mutated
FAILED tests/unit/test_sql_paramstyle_collisions.py::test_the_digest_insert_binds_payload
2 failed, 7 passed in 1.69s
--- restored ---
9 passed in 1.58s
```

## M9 — the scanner's own false-positive fix

Removed the `\x00` sentinel so f-string `{interpolations}` are dropped again rather than substituted:

```
mutated line 118 - sentinel removed, interpolations dropped again
widget.py:106 widget.py:129 widget.py:147 widget.py:800
FAILED tests/unit/test_sql_paramstyle_collisions.py::test_no_bindparam_abuts_a_cast_anywhere_in_app
1 failed, 8 passed in 1.58s
--- restored ---
9 passed in 1.62s
```

**Proves the sentinel is load-bearing, not decoration.** Dropping interpolations fuses the literals
either side, so `f"rate:config:{client_ip}:{bucket}"` collapses to `rate:config::` and four Redis
key builders in `widget.py` are reported as SQL defects. This is retro **Family F** — an over-broad
mechanical gate producing false positives — and the gate caught it on its own first run, before the
commit rather than after.

## M10 — the real-DB proof, against a real server

```
mutated
E   psycopg2.errors.SyntaxError: syntax error at or near ":"
E   sqlalchemy.exc.ProgrammingError: (psycopg2.errors.SyntaxError) syntax error at or near ":"
1 failed, 2 passed in 0.89s
--- restored ---
3 passed in 0.61s
```

**The most valuable of the four.** It reproduces the *exact* production error —
`syntax error at or near ":"` — the one that has been in the `run_weekly_digest` logs since OPS-04
and in every `run_deployment_checklist` since Phase 18. A static gate proves the shape; only this
proves the query. It is also the proof the pre-existing tests structurally could not have produced,
because they mock the session and a `MagicMock` accepts a string no database would.

---

## A harness trap worth recording, because it wasted two attempts

M9's first two runs failed with `StopIteration`/`AssertionError` — the anchor string was never
found. Cause: in a `<<'PYEOF'` heredoc, `'else "\\x00" for ...'` did **not** reach Python as
backslash-`x00`. The escape collapsed somewhere in the shell/tool layer, so Python built a needle
containing a real NUL byte:

```
needle repr : 'else "\x00" for v in node.values'
found       : False
```

Fixed by constructing the needle without any escape: `'else "' + chr(92) + 'x00" for ...'`.

Worth writing down for the same reason the mutation rule exists: **the first two attempts looked
exactly like "the mutation had no effect"**, which is indistinguishable from "the guard is a
tautology" if you stop there. The rule is to observe red, and neither of those runs observed
anything — they never mutated the file. A proof that does not modify what it claims to modify is not
a weak proof, it is not a proof.
