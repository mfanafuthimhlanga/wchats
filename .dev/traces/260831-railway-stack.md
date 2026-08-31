# Trace: railway-stack (ticket 18, #55)

Branch `feat/railway-stack` off `9869f09`. Solo in-session implementation (owner
instruction 2026-08-31: no large workflows), one adversarial review subagent before the
PR. Closes #32 on merge; #55 stays open for the owner-side staging observation.

## Commits

- `cb72db0` the nightly eval beat fans out to `is_deployed` only, matching the four other
  fan-outs (decision #6.5, closes #32); the test pins the rendered predicate.
- `fe40e97` the endpoint seam's flat production refusal becomes an allowlist:
  `.r2.cloudflarestorage.com` and `.backblazeb2.com` on the parsed hostname (decision
  #14.6), https only, credentials-in-URL refused.
- `b92eb76` `deploy/terraform`, `deploy/systemd`, `deploy/caddy` deleted; ADR 0005;
  PRODUCTION-READINESS 3.1 rewritten to the Railway target.
- `40bef98` `scripts/railway_staging_wizard.sh`: ten stages from project to a /health
  observation the wizard performs itself.
- `4bf33e2` the tier-1 review applied: 22 of 29 findings fixed, three filed
  (#133 account-level endpoint pin, #134 is_deployed within ready, #135 the widget
  bundle's serving story), the rest prose brought back to the tree's truth. Highlights:
  ENVIRONMENT validates at boot (a typo used to disable four production guards silently),
  the wizard's variable roster matches the derived required set (nine, not ten), the four
  tomls get the contract test #122 claimed and never wrote, `smoke_vm.sh` goes with its
  era, and the widget README stops resting the dual-location gate on deleted files.

## Decisions and deviations

- ADR 0005 records two deviations as deviations: the worker split preceding #60's
  numbers, and "the code does not change" not surviving contact (the old seam refused
  every production endpoint).
- `ENVIRONMENT=production` on staging: staging is production-shaped (decision #14.5);
  the wizard says exactly why, and the validator makes the word typo-proof.
- The predicate pin reads `str(stmt.whereclause)`, which renders `is_deployed = true`
  directly; compiling the full statement and splitting on WHERE was the test's own bug.
- The first slice-commit attempt swept the staged tree deletions in under the eval
  message; the branch was unpushed, so history was rebuilt as clean slices.

## Observed

- Guard mutations, each restored from HEAD after the red: beat filter reverted to
  `status='ready'` -> `1 failed, 1 passed`; allowlist suffix skipped -> `3 failed, 10
  passed`; userinfo refusal skipped -> `1 failed, 12 passed`; scheme requirement skipped
  and beat predicate inverted -> recorded in the PR with their restores.
- `test_storage_endpoint_seam.py` + `TestRunEvalSuiteBeat`: 19 passed at the fix head;
  the two new files (`test_railway_config.py`, `test_config_environment.py`): 14 passed.
- `scripts/gates.py fast` 145.9s; intermediate `gates.py full` exit 0 in 2091.2s at the
  pre-review-fix tree; the final-head full run and mypy-vs-main are quoted in the PR.

## Left for the owner (the wizard carries it)

Railway project, the $20 hard limit (skippable stage when plan-gated), four services
pointed at their checked-in tomls, Redis, a SEPARATE staging Neon control project, R2
bucket and token, the variable set, and the staging observation: `/health` plus worker
and beat log lines showing ready. #135 must land before the widget snippet on staging
points anywhere real.
