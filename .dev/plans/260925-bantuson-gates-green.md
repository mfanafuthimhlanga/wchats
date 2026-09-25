# Every pre-deployment gate green for the Bantuson assistant

Agent `ee8087ed-7b5b-4a2c-9fba-9c2a3b29178b` on staging. `decide()` over the newest stored
records (eval `0897e93b`, red team `42175745`) on 2026-09-25 reads `block`:

| rule | observed | cause |
|---|---|---|
| golden_failure | 25 of 60 golden fail | opinion answers flagged; 10 golden retrieved nothing scorable |
| golden_unconfirmed | 2 of 62 golden never scored | the 60-turn ceiling skipped them |
| eval_coverage_below_floor | 60 of 82 scored, 73.2% | the same ceiling skipped 2 golden and all 20 exploratory |
| critical_breach | 1 in run `42175745` | a refusal graded critical on the attacker's word |

The approve route also refuses on 15 open criticals across runs. All 15 are refusals the
pre-#314 grading took on the attacker's word. 5 open mediums from 8 to 15 Sept are real
fabrications on false premises, made before the grounding prompt rules. Calibration (`rule`)
and red-team coverage pass.

## Milestones, in order

1. **The eval ceiling covers what the coverage floor demands.** `AGENT_INVOCATION_MAX_CALLS_PER_RUN`
   is `GOLDEN_SET_SOFT_CEILING + EXPLORATORY_SAMPLE_SIZE`. At 60 the floor of 90% is out of reach
   for any tenant whose golden set plus sample passes 66. The checklist wait already scales
   with the turns the eval will make.
2. **The agent gives its view, the gate checks its facts.** The platform prompt asks for a view
   when the question asks for one, in a paragraph opening `My view:`. `grounding-v4` exempts
   that paragraph from the word-overlap floor and still fails any number the passages lack. Both
   reading aids carry the twin.
3. **An owner re-tests a finding.** A route queues a runtime task that replays the finding's
   recorded attack against the current agent through the real turn path and applies today's
   red-team rules. A finding that no longer lands closes as `resolved` with the re-test's id.
   Re-test runs carry their own kind, so the verdict never reads one as the latest red-team run.
4. **Staging.** Merge, re-test the 20 open findings, run the checklist, read the verdict, fix
   what remains.
5. **Console.** The Adversary panel's re-test action, pixel-reviewed.
