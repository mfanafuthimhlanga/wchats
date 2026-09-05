# Reading CI from this box

`gh api` reads GitHub Actions check runs and job logs here, with the auth `gh` already
has. Nothing extra is needed. Use it before recording an observation as owed to a future
run.

## The three commands

```bash
sha=$(gh api repos/mfanafuthimhlanga/wchats/pulls/<pr>/ --jq .head.sha)

# every check on a head, with its conclusion
gh api repos/mfanafuthimhlanga/wchats/commits/$sha/check-runs \
  --jq '.check_runs[] | "\(.name): \(.status) \(.conclusion // "")"'

# the failing job's id, then its full log
gh api repos/mfanafuthimhlanga/wchats/commits/$sha/check-runs \
  --jq '.check_runs[] | select(.conclusion=="failure") | .id'
gh api repos/mfanafuthimhlanga/wchats/actions/jobs/<id>/logs > job.log
```

Logs carry `\r`; pipe through `sed 's/\r//'`. Historic scheduled runs read the same way
through `actions/runs`, which is how a claim about the nightly gets settled.

Secrets and variables are readable as names, which settles whether a workflow can reach
anything:

```bash
gh api repos/mfanafuthimhlanga/wchats/actions/secrets --jq .total_count
gh api "repos/mfanafuthimhlanga/wchats/environments/wchats%20%2F%20staging/secrets" \
  --jq '.secrets[].name'
```

The environment name carries a space and a slash, so URL-encode it.

## What this settles that a local run cannot

CI runs the full unit suite in about 8 minutes on a clean Ubuntu runner. The same suite
takes 17 to 29 minutes here and competes with everything else on 4 GB. For a head that is
already pushed, reading the CI result is faster and cleaner than running it locally, and
it is the only way to observe the Integration job, which needs Redis and a live worker.

It also observes the two things a local run never can: whether a workflow edit does what
it claims, and what a job's conclusion actually is.

## Two things it settled on 2026-09-04

Both had been recorded as observations owed to a future run.

**An eval step that asserts nothing now reports red.** Run 33894268623 on PR #173:

```
1 skipped, 1 deselected in 0.05s
NOTHING PASSED. eval-deterministic.xml records 1 tests: 1 skipped, 0 failed, 0 errored.
##[error]Process completed with exit code 1.
```

The same job reported `success` over the same skip before that commit.

**The InvalidToken failure has one cause and one fix.** Every open PR fails the
Integration job on the same test with the same counts:

```
FAILED tests/integration/test_provision.py::test_provision_neon_stores_encrypted_connection_string
=========== 1 failed, 8 passed, 19 skipped, 26 deselected ============
```

PR #173, which carries the collection-order fix, is the only branch green there. That is
a controlled comparison across branches, not a reading of the code.

## The trap

A constraint inherited from a handoff is the one nobody tests, because inheritance reads
as authority. "CI is not observable from this box" travelled from a handoff into four
commit messages, each of which then deferred its own verification. The commands above
take seconds. Run them before writing that a thing cannot be checked.
