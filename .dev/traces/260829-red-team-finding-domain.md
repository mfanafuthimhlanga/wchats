# RedTeamFinding comes down to the domain rung (#52, ticket 15, criterion 3)

Branch `feat/red-team-finding-domain` off `main` at `a737914`. Two commits. Criterion 3
of ticket 15, "Findings keep severity, vector, probe and response; the result is frozen",
which PR #111 left open because the finding type sat two rungs above the frozen record.

## What changed

`46e20e1` refactor(redteam): the dead pydantic RedTeamResult gives up the name

- `app/services/red_team_service.py`: the pydantic `RedTeamResult` (run_id, findings,
  max_severity, deployment_blocked, critical_count, high_count) deleted. A comment stands
  where it was, so the next author reaching for an aggregate type finds the record.
- `tests/unit/test_red_team_service.py`: `TestRedTeamResult` and its two tests deleted
  with it. They built one and asserted pydantic had stored what pydantic was handed, so
  they proved the library.

`03e6582` feat(redteam): the findings come down to the record's rung, frozen

- `app/domain/red_team_finding.py`: new. `RedTeamFinding`, still pydantic,
  `model_config = ConfigDict(frozen=True)`. Imports pydantic and nothing else, so the
  edge runs `red_team_result -> red_team_finding` and no further.
- `app/domain/red_team_result.py`: `RedTeamResult` gains `findings`, defaulting to none,
  copied to a tuple by `_as_findings`, and written into `payload` under a `findings` key.
- `app/services/red_team_service.py`: imports the type, keeps serving it under its shipped
  name. Same arrangement `RED_TEAM_VECTORS` already had, so its readers are unaffected.
- `app/worker/tasks/runtime/red_team.py`: `_attempt_every_vector` returns the record alone
  instead of `(findings, result)`; `run_red_team` reads `run_result.findings` at the nine
  sites that read the local.
- `tests/unit/test_red_team_result_type.py`: 17 tests over the finding's four fields, its
  frozen-ness, the grade drift pin, and the json round trip into `red_team_runs.result`.

## Three decisions worth the words

**Still pydantic, unlike every other frozen type on this rung.** The stored shape IS
`model_dump()`, at two write sites. The criterion is about what a finding keeps, so the
shape is the one thing that may not move. `frozen=True` raises `ValidationError` rather
than `FrozenInstanceError`; the test says so out loud.

**severity stays four Literal strings, not the `Severity` enum.** `Severity` has a fifth
member, `none`, which a `VectorOutcome` needs to say a vector breached nothing. A finding
IS a breach, so typing the field as that enum would make `severity="none"` constructible.
A test pins the four Literal args against the enum's four graded members, because two
lists of four strings in one package is one list that can drift.

**`attack_vector` is not checked against `RED_TEAM_VECTORS`.** It does not name the
dispatch vector. `run_identity_bypass_agent` dispatches as `identity_bypass` and reports
`identity_verification_bypass`; the M7 conversational findings still say
`prompt_injection`. A roster check here would refuse findings the shipped runners produce
today. The check belongs on `VectorOutcome.vector`, which is what the completeness rule
reads, and it is there.

## The two loose ends PR #111 named

**The name collision is gone.** Commit `46e20e1` above.

**Nothing reads `red_team_runs.result`, and nothing does after this either.** Left open
deliberately. Criterion 3 is about what the column holds, and a reader is a separate
piece of work with its own surface (which route, what shape, whose gate). What changed is
that the column is now worth reading on its own: it carried counts and no findings, so a
reader would have had to join `red_team_runs.findings` and trust one pass wrote both.
That join is what this commit removes. Belongs on a new ticket, not opened here.

## Observed

```
static gates passed in 9.8s.            baseline at a737914, before any edit
static gates passed in 7.2s.            at 03e6582

Analyzed 226 files, 1202 dependencies.
app layers run one way                 KEPT
no import cycles outside app.services  KEPT
the provider SDKs have one home        KEPT
Contracts: 3 kept, 0 broken.

ruff: clean against the 0 pinned baseline violation(s).
complexity: clean against the 115 pinned function(s).
  run_red_team  249 NLOC, 30 CCN, 463 length   pin (30, 463) unmoved
source assertions: clean against the 44 pinned file(s), 113 site(s).

917 passed, 1 skipped in 183.85s        24 unit files: every file importing
                                        red_team_service, red_team_probe or the domain
                                        record, with test_red_team_probe.py and
                                        test_transactional_tools.py in the one
                                        invocation (FM-014). 900 before the 17 new tests.
13 tests collected in 0.28s             the five gated integration files, collect only
```

## Mutations, each restored with `git checkout HEAD --`

```
mutation: import app.services.red_team_service, appended to red_team_finding.py
  app layers run one way BROKEN
  no import cycles outside app.services BROKEN
  Contracts: 1 kept, 2 broken.
  app.domain is not allowed to import app.services:
  - app.domain.red_team_finding -> app.services.red_team_service (l.78)
restored                                      Contracts: 3 kept, 0 broken.

  app.worker.celery_app, app.api.deps and app.core.config were each appended the
  same way first and each turned it BROKEN, naming that edge. app.core is the rung
  directly above, so the finding sits below it rather than beside it.

mutation: ConfigDict(frozen=False)
  Failed: DID NOT RAISE ValidationError       1 failed, 51 passed
restored                                      52 passed

mutation: _as_findings' wrong-type list is always empty
  Failed: DID NOT RAISE InvalidRedTeamResult  1 failed, 51 passed
restored                                      52 passed

mutation: payload writes "findings": []
  Right contains one more item: 'high'        2 failed, 50 passed
restored                                      52 passed

mutation: _attempt_every_vector returns RedTeamResult(k=k, vectors=outcomes)
  findings_count=0 with breaches=6 in the run's own completion log
                                              5 failed, 45 passed
restored                                      50 passed
```

The last one is the one that matters most: it proves the task actually hands its findings
to the record, rather than the record merely being able to hold some.

## Deviations from the ticket

- No reader of `red_team_runs.result` was added, for the reason above.
- The nine `all_findings` sites in `run_red_team` were repointed to `run_result.findings`
  rather than keeping the local. Introducing `all_findings = run_result.findings` as its
  own line would have taken the function to 464 lines and broken its exact lizard pin of
  463, and the pin may only move down.
