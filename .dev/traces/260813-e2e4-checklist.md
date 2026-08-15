# TRACE — E2E-4 · the checklist and the gate. IT HAS NEVER RUN BEFORE, AND NOW IT DOES

**The deployment checklist completed for the first time in the project's history**, 79.7s,
`recommendation=block`, and `POST /approve-deployment` refused it with 422. Getting there took two
fixes, and the second is the one that matters.

## Why it had never run

`3.10` recorded it plainly: *"`_run_orchestrator_loop` reports 'was never awaited' — `run_orchestrator`
is never executed anywhere, so every claim about the prompt's prose blocking conditions is
untested."* E2E-4 is the first execution. It failed twice before it worked, and each failure hid the
next.

### Layer 1 — `1.30`: the timeout erased its own diagnosis

```
run_deployment_checklist.orchestrator_failed  error=
run_deployment_checklist.no_report
run_deployment_checklist.failed  error='Orchestrator did not produce a report'
```

Three lines naming no cause. `error=str(exc)`, and the exception was `asyncio.TimeoutError`, whose
`str()` is **the empty string**. CLI spawned 08:28:30, failed 08:30:37 — **127s against an inline
`timeout=120.0`**. Fixed: log `error_type`, `str(exc) or repr(exc)`, and the ceiling itself;
`ORCHESTRATOR_TIMEOUT_S = 300.0`, named so it can appear in the line at all.

**The general rule this earns:** `str(exc)` is a silently-plausible default for any exception that
carries its meaning in its *type*. Same family as `getattr(x, "name", "unknown")`.

### Layer 2 — `1.32`: the orchestrator was never given the tool it is told to call

With the ceiling raised it stopped timing out and failed *differently*: 94s, clean return, **no
report**. Cause:

- `_TOOL_SUBMIT_REPORT` was referenced **exactly once in the whole repository — its own definition**.
- `deployment_service.py` contained **zero** occurrences of `create_sdk_mcp_server`, `mcp_servers`,
  `allowed_tools`.

The orchestrator is instructed to "call submit_report" while holding no such tool. It can never emit
`ToolUseBlock(name='submit_report')`, the loop always falls through, **so every checklist run failed
and always would have.**

**This is audit defect D4 exactly** — *"5 of 7 red-team attackers were never given their tools, so
they reported clean"* — already found and fixed in `red_team_service`, whose own comment at `:240`
describes this bug. Two modules, one defect, found a milestone apart. It survived here only because
nothing had ever executed the orchestrator.

Fixed by mirroring the pattern `red_team_service` already proved, plus `tools=[]` and
`strict_mcp_config=True` so the orchestrator cannot inherit the CLI's Bash/Read/Edit built-ins on the
worker's filesystem. The loop now matches `SUBMIT_REPORT_TOOL_NAME` as well as the bare name — the
SDK rewrites in-process tools to `mcp__{server}__{tool}`, so registering the tool while still
matching the bare name would have reproduced the same silent no-report one layer later. That
three-way agreement is precisely what `3.7` records as unpinned.

## The observed result

```
run_deployment_checklist.complete  recommendation=block   79.687s
run_id 7282945a-2fbc-4780-a130-68dbf07f252e

eval_summary      eval_signal="no_runs"  pass_rates=null  agent_invoked=null
                  signal_detail="no eval run has ever been recorded for this agent"
red_team_summary  signal="no_runs"  critical_count=null  vectors_valid=null
warning           "BLOCKING: ... An absent measurement is unknown quality, never
                   acceptable quality."
summary           "'no findings' from zero tests is not a clean result, it is an
                   absence of measurement."

POST /approve-deployment -> 422 "Cannot approve a blocked deployment"
```

**This is the measurement-honesty rule working end to end**, not as prose but as a refusal: CLAUDE.md
says a metric over zero valid observations is `unknown`, never `pass`, and the gate blocked on
exactly that. The orchestrator's own summary states the principle unprompted.

## `1.31` — a third defect, observed rather than reasoned about

Killing the worker mid-checklist left `checklist_runs` row `e120cf18` in `status='running'` forever.
The guard (`deployment.py:216`) skips when any row for the agent is `running` and younger than 60
minutes, so **every subsequent `POST /checklist-runs` returned `{"status": "already_running"}` and did
no work.**

The interaction is the defect, not either half: **`acks_late=True` redelivers the killed task, and the
redelivered task then reads its own orphaned row and no-ops.** The redelivery guarantee CLAUDE.md
rule 2 exists to provide is silently cancelled by the guard, and a crash becomes a 60-minute outage
rather than a retry. Confirmed directly — the successful run above carries `task_id=b1fff425`, the
*original* task from the first attempt, redelivered.

Worked around with `UPDATE checklist_runs SET status='failed' WHERE status='running'`, which is
exactly the manual reclaim a production operator would have no way to know to perform. Needs a
liveness signal, not a status+age check. `run_eval_suite` has the same shape at `window_s=6000`.

## What E2E-4 does NOT establish

- **The eval was never observed invoking the agent, and red-team never ran 7/7 with tools.** Both
  reported `no_runs`, which is why the verdict is `block`. The plan's wording ("assert the eval
  actually invokes the agent, red-team runs 7/7") is **not** satisfied — the gate correctly reported
  their absence, which is a different and weaker claim.
- **No `ship` verdict has ever been produced**, so the approve route's success path is still
  unexercised. Only its refusal path is observed.
- Whether 300s is the right ceiling: this run took 79.7s, so the ceiling was not approached. 120
  would have sufficed *for this run*; the first attempt's 127s says it does not always.
