# Retro — regression and planning-defect log

Append-only. A regression reaching `main` is a planning defect: record what the plan failed to
anticipate, not just what the code did wrong. Recurring families raise planning depth.

---

## Family A — "a measurement that cannot fail"

**Recurrences: 4 (as of 2026-08-05).**

1. **Eval target leakage.** `eval.py:200` set `agent_response = reference_answer`, so Ragas scored
   the label against the contexts the label was generated from. Scores approach 1.0 by construction.
   Shipped in M6 as scaffolding ("to test the eval harness"), never revisited across 17 phases.
2. **Red-team tests patch out the code under test.** `test_red_team_service.py:165/195/221` patch
   `asyncio.run` with a canned return, so `_run_agent_loop` — which contains D4's unregistered-tool
   defect — never executes. 1199 green tests over a loop that cannot work.
3. **Negative tests never observed to fail.** Phase 22 caught this prospectively and made
   guard-removal demonstrations mandatory (`22-01`: mutate → assert red → restore from `HEAD` →
   assert green). That discipline exists *because* of this family and must not be dropped.
4. **Judge calibration harness with zero labels.** `compute_correlation.py` gates judge trust at
   Spearman ≥ 0.75 against human scores; `human_scores.csv` has 10 rows, every score cell empty. The
   instrument reads "not calibrated" as "no scored rows yet — exit 0, informational."

**What the plans failed to anticipate:** every one of these passes its own acceptance criteria. The
criteria asked "does the code run and do the tests pass", never "could this test fail if the
behaviour were deleted". A green suite is evidence about the suite, not about the system.

**Standing rule:** for any guard, absence pin, or fail-closed path — mutate it, observe red, restore
from `HEAD` unconditionally, observe green, record the observed output.

## Family B — "missing data treated as passing data"

**Recurrences: 2.**

1. **Deploy gate eval fetch.** `deployment_service.py:201` queries columns that do not exist; the
   error is caught at `deployment.py:157` and substituted with `pass_rates: {}`. The blocking
   condition "any pass_rate < 0.70" cannot fire over an empty dict. Fails **open**. The same file's
   `_fetch_verified_qa_stats_sync` has exactly the right defensive shape — it was simply not applied
   here.
2. **Zero findings from zero probes.** Five red-team attackers return `[]` because they were never
   given their tools; the run reports **clean**. Indistinguishable from a genuinely clean run.

**What the plans failed to anticipate:** an exception handler that supplies a default is a *decision
about what missing data means*, and it was made in passing, inside a `try/except`, without anyone
writing down which way it should fail.

**Standing rule:** a metric over zero valid observations is `unknown`, never `pass`. Every run reports
`(attempted, valid, findings)` — a rate without its denominator is not a measurement. `unknown` and
`pass` must never render the same on screen.

**Prior art in this repo, already correct:** `red_team_service.py:1076` treats
`provider_not_configured` as a finding because the run was *"INVALID, not clean."* One place got it
right; it never became a system rule.

## Family C — "an in-memory or ephemeral marker advanced before/instead of a durable write"

**Recurrences: 2.**

1. **Eval results written to a branch that is then deleted.** `eval.py:281-283` writes results,
   promotions and the terminal status to `branch_conn_str`; `:313` deletes the branch in `finally`.
   Production keeps a row stuck at `running` forever. Every eval observation the system has ever
   produced is gone.
2. **Dispatch-after-claim window** (`T-22-ACT-09`, OD-6): the confirmation claim commits before the
   Celery task is enqueued; an enqueue failure leaves a `resolved` row whose task never ran.
   Deliberately accepted, not closed.

**What the plans failed to anticipate:** D-10 ("never evaluate against production") was applied to
*all* writes rather than to tenant-data writes only. Observations about a run are not tenant data.
The isolation rule was right; its blast radius was never scoped.

## Family D — "the seam between two units belongs to neither"

**Recurrences: 2.**

1. **Wave-crossing seam, Phase 21.** `21-05` shipped in Wave 1 before `21-06` created
   `promote_trace_to_scenario` in Wave 3; grading a trace never dispatched the promotion. Caught by
   the phase verifier. Lesson recorded: *"when a seam crosses waves, the LATER plan must own the
   wiring."*
2. **Phase-crossing seam, milestone v1.2.** Phase 20 shipped six ops-room regions with honest empty
   states; Phase 21 was explicitly *"backend-only, no frontend artifacts"*; neither owned the seam.
   A grep for the six endpoints across `apps/admin` + `apps/widget` returned **zero files**. 13 of 24
   requirements passed phase acceptance while being unreachable by a user. Phase 23 exists to close
   it.

**What the plans failed to anticipate:** the wave-level lesson was recorded and did not generalize to
phases. Phase 23's answer — each region plan creates *and mounts* its component in the same plan, so
no seam crosses a wave at all — is the stronger form and should be the default.

**Standing rule:** a seam is owned by the unit that makes it reachable by a user, and that unit ships
both sides.

## Family E — "a human signal stored as its own opposite"

**Recurrences: 1.**

1. **Bench flywheel label inversion.** `traces.py:84` lists *failing* traces. The operator grades one
   `filed`. `bench.py:147` stores `reference_answer=agent_turn` — the agent's own failing answer
   becomes the ground truth for that question.

**What the plan failed to anticipate:** "promote a filed trace into a scenario" is ambiguous about
*which field is the label*, and nobody asked. The plan reviewed as sensible because the sentence is
sensible.

**Standing rule:** when a human supplies a signal, write down what the signal *means* before writing
where it is stored. A label whose polarity is unstated will eventually be stored backwards.

## Family F — "over-broad mechanical gates produce false positives on their own prose"

**Recurrences: 3** (`22-04`, `22-05`, `23-09`).

Negative-assertion greps (e.g. "the committed diff may contain no line mentioning
`PLATFORM_CAPABILITY_DEFAULTS`") repeatedly tripped on the explanatory comment or docstring that was
written to *document* the rule. Each was resolved by rewording the prose, which is correct but
recurring.

**Standing rule:** scope a diff-content gate to code lines, or accept that the gate's own
documentation is part of its input and write the needle to be unique to the mechanism.
