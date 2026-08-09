# P3 review fixes — mutation proofs (2026-08-08)

Guards added or repaired while closing the tier-2 findings against
`feat/d1-agent-invocation` P3 (`5011f97`). Every one was **run** in both
directions:

1. apply the mutation, run the guard, record the output;
2. `git checkout HEAD -- <file>` **unconditionally**, run again, record the
   output, and print `sha256` before and after so the restore is verifiable
   rather than assumed.

A negative test never observed to fail is indistinguishable from a tautology.

Driver: `scratchpad/mutate.py` (session-local, not versioned). Selectors run
with `-p no:randomly`, so a red is the mutation and not an ordering. The code
under test was committed (`8b124d4`) before the first mutation, so the
unconditional restore has something to restore to — that is why the commit
precedes this file rather than following it.

**One property here is NOT falsifiable by any single mutation** and is recorded
as such rather than presented as two defences. See
`failed-run-scores-do-not-travel` below; P3's own commit message drew the
stronger conclusion from the identical shape and the tier-2 read was right to
reject it.

---

### approve-route-does-not-read-the-stored-run

`app/api/v1/deployment.py` — the HIGH finding. Without this the pre-P3 'ship'
frozen on every historical checklist run is still approvable.

```
if not stored_run_records_agent_invocation(run.report):  ->  if False:
```

- selector: `tests/unit/test_deployment_routes.py::TestApproveRefusesAnUninvokedRun`
- **observed RED:** `5 failed, 2 passed in 48.95s`
- **observed GREEN (after `git checkout HEAD -- apps/api/app/api/v1/deployment.py`):** `7 passed in 25.77s`
- sha256 before=`70e1dd3c61d8986e` after=`70e1dd3c61d8986e` identical=True

The 2 that stay green under the mutation are the two that must: the run that
DOES record the invocation still approves, and the blocked run still reports
its own more severe 422.

### stored-run-helper-accepts-absent

`app/services/deployment_service.py` — absence must fail exactly as falsehood
does, at the approve route too. `is not False` is the gate that would have
refused nothing that exists.

```
return eval_summary.get("agent_invoked") is True  ->  ... is not False
```

- selector: `TestStoredRunEvidence` + `TestApproveRefusesAnUninvokedRun`
- **observed RED:** `5 failed, 7 passed in 26.80s`
- **observed GREEN:** `12 passed in 23.89s`
- sha256 before=`71443e905144322c` after=`71443e905144322c` identical=True

### failed-run-is-admissible

`app/services/deployment_service.py` — the fail-open the review found by
driving the collector directly.

```
if last_run_status != EVAL_RUN_STATUS_COMPLETE:  ->  if False:
```

- selector: `TestFailedRunIsNotEvidence` + `TestSignalCollectionFunctions`
- **observed RED:** `4 failed, 5 passed in 3.09s`
- **observed GREEN:** `9 passed in 2.03s`
- sha256 before=`71443e905144322c` after=`71443e905144322c` identical=True

### failed-run-check-is-a-deny-list

Same file. An allow-list of one, not `== "failed"`: a terminal status this code
has not heard of must fail closed, the same reasoning the selector's
`status <> 'running'` uses in the other direction.

```
if last_run_status != EVAL_RUN_STATUS_COMPLETE:  ->  if last_run_status == "failed":
```

- selector: `TestFailedRunIsNotEvidence`
- **observed RED:** `1 failed, 5 passed in 2.74s`
- **observed GREEN:** `6 passed in 2.07s`
- sha256 before=`71443e905144322c` after=`71443e905144322c` identical=True

### failed-run-yields-to-the-invocation-claim

Same file, the ORDERING. A run that failed AND recorded nothing is in two
absent states at once; the coarser question is answered first.

```
if last_run_status != EVAL_RUN_STATUS_COMPLETE:
  ->  if last_run_status != EVAL_RUN_STATUS_COMPLETE and agent_invoked is True:
```

- selector: `TestFailedRunIsNotEvidence`
- **observed RED:** `1 failed, 5 passed in 2.83s`
- **observed GREEN:** `6 passed in 2.01s`
- sha256 before=`71443e905144322c` after=`71443e905144322c` identical=True

### failed-run-scores-do-not-travel — NO SINGLE MUTATION FALSIFIES THIS

The RUN_FAILED payload is suppressed twice and **neither layer alone is
load-bearing**, which is the honest form of the claim P3 made about its own
mutation 5. All three runs were executed:

```
structural only:  add `pass_rates=pass_rates` to the RUN_FAILED return
state only:       rates = pass_rates if measured else None
                    ->  ... if measured or signal == EVAL_SIGNAL_RUN_FAILED else None
both:             the two edits together
```

- selector: `TestFailedRunIsNotEvidence`
- **observed, structural only — RED:** `6 passed in 2.46s` (i.e. no red)
- **observed, state only — RED:** `6 passed in 2.03s` (i.e. no red)
- **observed, both — RED:** `1 failed, 5 passed in 2.80s`
- **observed GREEN after each restore:** `6 passed in 2.01s` / `6 passed in 2.05s` / `6 passed in 1.97s`
- sha256 before=`71443e905144322c` after=`71443e905144322c` identical=True (all three)

So: one falsifiable property, no single load-bearing layer. The test's docstring
says exactly this (`9106412`).

### warning-does-not-branch-on-the-payload

`app/services/deployment_service.py` — the fix for the message that narrated a
cause it did not observe. Freezing the read reproduces the shipped behaviour.

```
invoked = eval_summary.get("agent_invoked")  ->  invoked = None
```

- selector: `TestAgentInvokedGate`
- **observed RED:** `1 failed, 11 passed in 2.80s`
- **observed GREEN:** `12 passed in 1.98s`
- sha256 before=`71443e905144322c` after=`71443e905144322c` identical=True

The one red is `test_the_false_case_is_not_narrated_as_the_tautology`, which is
the finding.

### collector-refuses-only-false

`app/services/deployment_service.py` — the mutation-blind-spot the review
found. `test_absence_and_falsehood_are_distinguishable_on_the_payload` SURVIVED
this before the fix, because it never asserted either state was refused.

```
if agent_invoked is not True:  ->  if agent_invoked is False:
```

- selector: `TestAgentInvokedCollector::test_absence_and_falsehood_are_distinguishable_on_the_payload`
- **observed RED:** `1 failed in 2.70s`
- **observed GREEN:** `1 passed in 2.02s`
- sha256 before=`71443e905144322c` after=`71443e905144322c` identical=True

### no-dispatch-for-the-historical-population

`app/worker/tasks/runtime/deployment.py` — reverting step 4b to the shipped
`no_runs`-only condition, which is the state P3 left the whole existing tenant
population in.

```
if eval_signal == EVAL_SIGNAL_NO_RUNS or (
    eval_signal == EVAL_SIGNAL_AGENT_NOT_INVOKED
    and eval_summary.get("agent_invoked") is None
):
  ->  if eval_signal == EVAL_SIGNAL_NO_RUNS:
```

- selector: `tests/unit/test_deployment_task.py::TestExistingTenantEvalPath`
- **observed RED:** `2 failed, 11 passed in 15.01s`
- **observed GREEN:** `13 passed in 14.08s`
- sha256 before=`80dac80367860715` after=`80dac80367860715` identical=True

### dispatch-fires-for-an-explicit-false-too

Same file, the other direction — the spend bound. Firing on a recorded `false`
buys up to `AGENT_INVOCATION_MAX_CALLS_PER_RUN` live SDK turns on every
readiness check for a state that recurs.

```
... ->  if eval_signal in (EVAL_SIGNAL_NO_RUNS, EVAL_SIGNAL_AGENT_NOT_INVOKED):
```

- selector: `tests/unit/test_deployment_task.py::TestExistingTenantEvalPath`
- **observed RED:** `1 failed, 12 passed in 14.91s`
- **observed GREEN:** `13 passed in 14.05s`
- sha256 before=`80dac80367860715` after=`80dac80367860715` identical=True

### double-pads-the-narrow-row

`tests/unit/test_deployment_service.py` — the test-double fidelity nit. The
mutation restores the shipped behaviour: pad every run row to four columns,
including the one the pre-0013 three-column SELECT asked for.

```
if "config FROM eval_runs" in (executed[-1] if executed else ""):
    return row if len(row) == 4 else (*row, None)
return tuple(row[:3])
  ->  return row if len(row) == 4 else (*row, None)
```

- selector: `tests/unit/test_deployment_service.py::TestNarrowRowWidth`
- **observed RED:** `1 failed, 1 passed in 3.08s`
- **observed GREEN:** `2 passed in 2.25s`
- sha256 before=`145ed874021021a0` after=`145ed874021021a0` identical=True

**Recorded honestly:** the first run of this mutation reported
`identical=False`, because an uncommitted docstring edit was live in the same
file and the unconditional `git checkout HEAD --` correctly discarded it. That
is the restore working, not failing. The edit was re-applied and committed
(`9106412`) and the mutation re-run clean; the figures above are the clean run.
