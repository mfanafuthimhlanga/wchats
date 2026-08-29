# Gates that fail open

Three of this repo's five CI jobs were reporting something other than what a reader took
them to mean. Type-check reported one error while checking one file of 150. Integration
reported one failure while `-x` hid eight tests. Eval reported success over zero
assertions. This note records what each one printed, why the printed line was misread, and
the one property that separates a gate you can trust from a gate you cannot. Lint and Unit
are not in it; both were reporting exactly what they found.

Read it before adding a gate, and before believing one.

## The property

**A gate prints a numerator and a denominator, and only the numerator gets read.**

- mypy printed `Found 1 error in 1 file`. The denominator was `1 file`, out of 150.
- The Eval job printed `1 skipped, 1 deselected in 0.08s` and a green check.
- The Integration job printed one failure. The denominator was `-x`, which stopped it
  after the first of nine.

`scripts/gates.py` already solves this for three of its four steps. `RUFF_BASELINE`,
`LIZARD_BASELINE` and `SOURCE_ASSERTION_BASELINE` each pin counts and fail three ways: on
something new, on something that grew, and on an entry that has gone stale. The gates
without a pinned denominator are the ones that went dark.

This is the measurement rule from `CLAUDE.md` applied one layer up. A metric over zero
valid observations is `unknown`, never `pass`. A gate over one checked file out of 150 is
`unknown`, never `1 error`.

Logged as FM-013 in `.dev/failure-modes.jsonl`.

## mypy checked one file for seventeen days

`dc67d37` (2026-08-11) landed a prose comment inside an `if` block in
`app/services/red_team_probe.py`. It landed at line 379 and drifted to 434 by the time it
was deleted, which is the number mypy reported. The line read:

```
                            # type:"user" entries. The ToolResultBlock branch below
```

mypy reads any comment beginning `# type:` as a PEP 484 type comment. It tried to parse
`"user" entries. The ToolResultBlock branch below` as a type, failed, and stopped:

```
app/services/red_team_probe.py:434: error: Expected an indented block after 'if' statement on line 428  [syntax]
Found 1 error in 1 file (errors prevented further checking)
```

Exit code 2. CPython compiles the file without complaint, so no test could go red.

The five-line repro, falsified in both directions:

```python
# repro.py
def f(msg: object) -> None:
    if isinstance(msg, str):
        # prose that happens to start with the two words mypy reserves:
        # type:"user" entries. The branch below
        print(msg)
```

```
$ mypy repro.py
repro.py:4: error: Expected an indented block after 'if' statement on line 2  [syntax]
Found 1 error in 1 file (errors prevented further checking)

$ mypy repro_control.py          # identical, colon moved one character
Success: no issues found in 1 source file
```

`a4a03fb` had driven mypy to zero on 2026-08-06, five days before the comment landed.
Between `dc67d37` and the merge that removed it, 70 non-merge commits touched
`apps/api/app`. When `772f9cf` deleted the SDK block carrying the comment (issue #49, which
reached main as PR #87), mypy parsed the tree again:

```
Found 148 errors in 12 files (checked 150 source files)
```

63 in `validation_service.py`, 28 in `red_team_service.py`, 21 each in `strategy_service.py`
and `actor_seam.py`, 6 in `model_client.py`. The dominant shape is
`Item "OpenAI" of "Anthropic | OpenAI" has no attribute "messages"`, which is issue #88
seen from the type checker. Tracked as **#92**.

**What to do about it.** Pin `checked N source files` at or above the file count. Exit
code 2 also deserves its own message, separately from a nonzero error count, but that
half only catches mypy. Pinning the denominator catches the class.

## The Eval job passes over zero assertions

After the lockfile fix (#94) the job runs to completion and reports success:

```
collected 2 items / 1 deselected / 1 selected
tests/evals/run_evals.py::test_deterministic_dimensions_d5_d6_d7 SKIPPED [100%]
1 skipped, 1 deselected in 0.08s
```

The test itself is honest. `run_evals.py:419` already carries the reasoning:

> Nothing checked is not everything passing. With responses/ empty this test exercised no
> scenario and asserted over three empty sets, and both this version and the pre-8.1 one
> reported that as a pass. A skip is unobserved and reads as unobserved; a pass reads as
> evidence.

The workflow then converts that skip into a green check, which restores the false reading
one layer up. The job is named for five checks (D3, D5, D6, D7, G-06) and runs none.
Tracked as **#102**.

G-06 is worse than skipped. `_check_escalation_rate_gate` is called from
`run_evals.py:544`, inside `test_llm_judged_dimensions_d1_d2_d3_d4_d8`, which `-k
deterministic` deselects unconditionally. It would not run in that job with a full
`responses/` directory either. One fifth of the job's name is structural rather than
circumstantial.

Two `pytest.skip` calls can produce that line, `run_evals.py:403` (no built widget bundle)
and `:424` (no recorded responses), and the step runs without `-rs`, so the log does not
say which. Add `-rs` before asking any further question about it.

## `-x` hides the denominator too

The Integration job runs `pytest tests/integration -x`. Until 2026-08-28 it failed on the
first test, because the service image was `postgres:17-alpine` and
`alembic_tenant/versions/0001_tenant_v1_schema.py:41` runs
`CREATE EXTENSION IF NOT EXISTS vector`. One failure was visible. Behind it were eight
more tests, and giving the service `pgvector/pgvector:pg17` surfaced them:

```
tests/integration/test_chain.py ..            <- both pass, migration reaches schema_version=0020
tests/integration/test_provision.py .F
1 failed, 8 passed, 19 skipped, 26 deselected in 27.75s
```

`test_provision_neon_stores_encrypted_connection_string` fails on
`cryptography.fernet.InvalidToken`, which had never executed in CI. Tracked as **#101**.

The unit job's own workflow comment already states the principle for its own step:
*"No -x: halting at the first failure reports one defect and hides the rest, which is the
opposite of what a gate is for."* The integration job did not get the same treatment.

## The two lockfiles

`apps/widget` carried `pnpm-lock.yaml`, which every change updates, and
`package-lock.json`, untouched since 2026-06-01. CI read the second. `e264850` added
`vitest` on 2026-08-18 and `npm ci` refused to run from then on, so that job was dark for
ten days. The npm lockfile is now deleted and both workflows install with pnpm, which is
what `CLAUDE.md` already required.

pnpm 11 needs Node 22.13 and dies reaching for `node:sqlite` below it, so the workflows
pin Node 22. A local verification on a different Node than the job pins is not a
verification of the job.

## How long the gate had been dark

Every CI run GitHub still retains concluded failure. That is 93 runs as of 2026-08-28,
the oldest dated 2026-07-27, and that date is the end of the retention window rather than
a point where something changed. There is no evidence of a green run at any time inside it,
and no evidence of one before it either way. No branch in that window was gated by CI,
whatever its checks said.

Before trusting a gate's verdict, read when it last passed.
