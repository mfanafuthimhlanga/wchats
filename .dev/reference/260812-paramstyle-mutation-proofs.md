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

---

# Addendum — `1.15`, the deploy gate test (commit `d839100`)

Different defect, same file of proofs because it was the same session and the same layered shape.

## M11 — remove the containment step

```
mutated line 425 - the critical finding is never contained
AssertionError: Expected non-block recommendation after containment, got
  {'recommendation': 'block',
   'red_team_summary': {'deployment_blocked': True, 'critical_count': 1, ...},
   'eval_summary': {'eval_signal': 'measured', 'agent_invoked': True,
                    'pass_rates': {'faithfulness': 0.95, ...}}}
1 failed in 17.97s
--- restored ---
1 passed in 17.20s
```

**Proves the test observes the red-team transition** rather than passing because the eval seed made
everything green. Note in the dump that the eval half reads `measured` in BOTH the mutated and clean
runs — so the only thing moving the recommendation is the red-team half, which is the test's subject.

### The first attempt at M11 was invalid, and it is recorded rather than quietly redone

`s.replace(old, new, 1)` replaced the **first** `_contain_finding(tenant_db_url, finding_id)` in the
file — line 182, which belongs to `test_fetch_red_team_summary_unblocks_after_containment`, a
different test. The targeted test was untouched and reported `1 passed`.

That result is indistinguishable from "the guard is a tautology", and it would have been written up
as one. It is the *second* time in two days a proof failed by not mutating what it claimed (the
first was the `\x00` heredoc escape, above). Both were caught only because the expected direction was
stated before the run: M11 was *supposed* to go red, and a green demanded an explanation. **A
mutation proof needs its expected direction written down first; otherwise a no-op reads as evidence.**

## M12 — the seeded run stops claiming the agent was invoked

```
mutated: the seeded run no longer claims the agent was invoked
FAILED ...::test_deploy_gate_blocks_then_unblocks_on_contain
1 failed in 18.60s
--- restored ---
1 passed in 18.60s
```

## M13 — every seeded score is NULL

```
mutated: every seeded score is NULL
FAILED ...::test_deploy_gate_blocks_then_unblocks_on_contain
1 failed in 17.40s
--- restored ---
1 passed in 18.56s
```

M12 and M13 together prove the eval seed is **load-bearing rather than decoration**: remove the
invocation claim and the gate refuses via `agent_not_invoked` (audit D1); remove the scores and it
refuses via `no_valid_scores`. Both are the gate working, and both would previously have been
invisible because the run never got past `no_runs`.

## Incidental confirmation of `1.14` from a different test

M11's failure dump carries `'blast_radius': {..., 'configured_max_hourly_aggregate_cents': 0, ...}`.
Before `c65137e` every key in that payload was `None`, because the query raised and the caller
substituted a fallback. A real value there — arrived at through a test that knows nothing about
paramstyles — is independent evidence that the blast-radius fetch now executes. (`observed_max_*`
remain `None`, correctly: this ephemeral agent has no `tool_calls_audit` rows, which is the honest
empty state the fix restores the ability to distinguish.)
