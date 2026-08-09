# TRACE — D1/P3 review fixes

**Branch:** `feat/d1-agent-invocation` · **Commits:** `8b124d4`, `9106412` ·
**Plan:** `.dev/plans/260807-d1-agent-invocation.md` (P3) · **Input:** the
tier-2 read of P3 at `5011f97` — 11 findings (1 high, 3 medium, 4 low, 2 nit)
and 6 unsupported claims.

**Gate, observed (apps/api, the exact command in CLAUDE.md):**

```
before:  1839 passed, 11 skipped, 28 warnings in 384.06s (0:06:24)   # 5011f97, clean tree
after:   1873 passed, 11 skipped, 30 warnings in 369.98s (0:06:09)   # 8b124d4
final:   1873 passed, 11 skipped, 30 warnings in 358.80s (0:05:58)   # whole branch, final tree
```

`+34`, `0 failed`. The +34 are 34 added tests and nothing pre-existing moved:
16 in `test_deployment_service.py`, 7 in `test_deployment_routes.py`, and 11 in
`test_deployment_task.py` — 6 written plus the 5 `TestEvidenceGateWiring`
re-runs the new subclass inherits, the same arithmetic `TestDayOneEvalPath`
already contributes. `mypy app` -> `Success: no issues found in 132 source
files`; `uvx ruff@latest check app tests` -> `All checks passed!` (ruff is not
in `apps/api/.venv`).

Mutation proofs: `.dev/reference/p3-review-mutation-proofs.md` — 11 guards run
red and green with `sha256` printed on both sides of the restore, plus three of
P3's own re-run against the current tree.

---

## The four that change behaviour

**The gate never reached the artifact the approve route reads.** This is the
whole of the high finding and it is the phase's own deliverable landing one
layer short. `apply_signal_evidence_gate` has exactly one caller — the checklist
Celery task — and `agent.is_deployed` has exactly one writer: `POST
/approve-deployment`, which validates status, recommendation, warning
acknowledgement and envelope drift. `recommendation` is FROZEN by whatever gate
ran the day the row was written, so refusing an uninvoked eval at checklist time
closed nothing for the runs that already exist: complete, 'ship', warnings
inapplicable, envelope hash unmoved, `{"deployed": true}`. Nothing on
`checklist_runs` expires — no TTL, no gate-version column — so the evidence has
to be re-read rather than aged out. The route now re-reads
`report["eval_summary"]["agent_invoked"]` with the same `is True`, fails closed
on every unreadable shape, and sits behind the three shipped validations and
**ahead of** the envelope check: a fresh eval is a step "re-run the checklist"
does not mention.

**The owner-facing message narrated a cause it did not observe** — in the phase
whose subject is exactly that. One warning_id is not one sentence. A below-floor
P2 run invoked the agent, scored nothing at all (`run_eval_suite` skips the
scorer below the floor, so zero `eval_results` rows exist) and involved no
pre-written answers anywhere; every owner in that state was told their check
"scored a set of pre-written model answers" and that the new numbers would be
"lower than the old ones", of which there are none. The console renders nothing
else — a grep of `apps/admin` for `agent_invoked` or `eval_signal` returns
nothing — so that sentence IS the owner-visible account. The message branches on
the payload now, and the ABSENT branch offers the tautology as an explanation
for the coming drop rather than asserting it as this run's history, because
absence is also a pre-0013 tenant DB and a failed config patch.

**A run recorded as `failed` was still evidence, and P2's ordering makes that
shape ordinary.** The invocation claim is patched into `eval_runs.config` BEFORE
scoring (`eval.py:1082`, deliberately — the invocation is the expensive,
unrepeatable half). `summarise_run_validity` runs at `:1155`, one line AFTER
`update_eval_run_status('complete')`, and anything raising from there lands in
the `except` whose `_mark_failed_on_production` writes `status='failed'` over a
row already carrying `agent_invoked=true` and a full set of high pass_rates. The
collector returned `EVAL_SIGNAL_MEASURED` and the gate shipped it.
`last_run_status` had travelled on the payload since P1 and nothing anywhere
gated on it. Sixth state, `EVAL_SIGNAL_RUN_FAILED`, checked ahead of the
invocation claim because a run that did not reach the end of its own body has no
reliable account of any of its claims. An allow-list of one, so an unrecognised
terminal status fails closed too.

**The convergence mechanism did not fire for the population P3 creates.** Step
4b dispatched only on `EVAL_SIGNAL_NO_RUNS`, and no existing tenant is in that
state — they have runs, produced by the tautology, which now report
`agent_not_invoked`. The wall `_dispatch_first_eval_run` exists to remove had
simply moved to the far larger population, with the warning naming the same page
the onboarding flow reaches from nowhere.

## The asymmetry in the dispatch, which is a decision and not an oversight

It fires for the ABSENT half only.

- `agent_invoked is None` is the historical population and it **converges**: a
  fresh run on a 0013+ tenant writes the key either way, so the state cannot
  recur and the dispatch is one-shot per agent, exactly like the day-1 case.
- `agent_invoked is False` is a run that looked and said no. A broken or
  unreachable agent produces it again every night, so firing on it would buy up
  to `AGENT_INVOCATION_MAX_CALLS_PER_RUN` live SDK turns on every readiness
  check the owner runs and leave the state unchanged. That is a spend loop with
  a button on it, not convergence. Same for `run_failed`.
- The residual is a pre-0013 tenant DB, which cannot record the key at all, so
  absence recurs there and the dispatch repeats. BACKLOG `2.18`.

Renamed `_dispatch_eval_run` / `eval_dispatched`: "first" is now false for most
of what it dispatches, and a field named `first_eval_dispatched` on an agent
with forty prior evals is the kind of name that survives into a later reader's
reasoning.

## Three claims corrected rather than defended

- **"Two independent points, and both are needed."** They are not, today. The
  collector is the only producer of a 'measured' payload in the tree, and the
  reviewer reproduced that neutering the gate arm alone leaves every collector
  test green. The arm is an invariant for a payload source that does not exist
  yet, which is worth keeping and is not a second live layer. Docstring rewritten
  to say so.
- **"The prompt was updated so the narration cannot contradict the verdict."**
  Nothing in the repo executes `run_orchestrator` (BACKLOG `3.10`), so no test
  observes the model obeying any prose condition. What constrains the narration
  is the suppression: the model cannot narrate a number it was not given. The
  prompt tests are drift protection over a string, and the module comment and
  the test docstring now both say that.
- **"The scores are suppressed twice, structurally and by state."** Executed
  exhaustively on the sibling state: structural-only -> `6 passed`, state-only ->
  `6 passed`, both -> `1 failed`. One falsifiable property, no single
  load-bearing layer. `9106412` writes that into the test's own docstring.

## Test-double fidelity

`_make_eval_conn` padded a 3-tuple to four elements before the double was built,
so the pre-0013 test — whose whole subject is that the WIDE select raises and
the NARROW three-column one answers — got a four-element row from `SELECT id,
finished_at, status`. No database can do that. It changed no outcome, because
the collector indexes only `[0..2]` on that path, and that is exactly the
problem: a future read of `run_row[3]` on the fallback would be green here and
wrong in production. The row is sliced at fetch time against the SQL actually
executed, and `TestNarrowRowWidth` pins both widths.

## What is NOT proven

- **No end-to-end readiness check.** No PostgreSQL on this machine; every
  `-m integration` harness skips, and a skip is unobserved, never a pass. The
  approve route's new refusal has never been exercised against a real
  `checklist_runs` row, and the new `run_failed` state has never been derived
  from a real `eval_runs` row.
- **The `failed`-with-scores shape was reasoned from `eval.py`'s ordering and
  driven through the collector with a connection double.** No run has been
  observed taking that path.
- **No `ALTER TABLE` ran and none was written.** No schema change was needed:
  the approve-route read is over `checklist_runs.report`, JSONB that already
  exists, and the sixth signal state is derived from `eval_runs.status`.
- **The gate-version alternative was not built.** Stamping a version integer on
  `checklist_runs` at write time and refusing any run below the current one
  would close the whole stored-artifact class rather than D1's slice of it. It
  needs a control-DB migration that cannot be applied here, and BACKLOG `2.19`
  carries it beside `5.1`, which is the same hole on the red-team half.
- **`_dispatch_eval_run` was not observed dispatching anything.** The helper is
  driven against a fake `celery.chain`, as it was in P2.
