# RedTeamFinding comes down to the domain rung (#52, ticket 15, criterion 3)

Branch `feat/red-team-finding-domain` off `main` at `a737914`. Three commits. Criterion 3
of ticket 15, "Findings keep severity, vector, probe and response; the result is frozen",
which PR #111 left open because the finding type sat two rungs above the frozen record.
The third commit answers an adversarial review of the first two.

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

`a5c2cb9` fix(redteam): the record refuses findings its counts disagree with, and the
column is read back

- `app/domain/red_team_result.py`: `_require_findings_agree`, called last in
  `__post_init__`. `RedTeamResult(k=3, vectors=<seven rows, zero breaches>,
  findings=[one high finding])` used to construct, and `payload` then wrote
  `breaches=0, max_severity="none"` into the column beside that finding.
- `app/domain/red_team_finding.py`: `extra="forbid"`. The `attack_vector` docstring
  rewritten (see below).
- `app/services/red_team_service.py`: a comment on `_classify_reported_findings` saying
  why it names all six keys. `VectorAttempts.findings` stopped citing an `all_findings`
  list that no longer exists.
- `tests/unit/test_red_team_result_type.py`: the agreement test rebuilt as a refusal, plus
  the reverse direction, the count clause, and the seventh-key refusal. 52 tests to 55.
- `tests/unit/test_red_team_task.py`: the deploy gate's critical case beside its high one,
  and the stored `result` column read for its findings. 25 tests to 27.
- `tests/unit/test_red_team_service.py`: an invented key survives the boundary as a
  finding, and refuses a splat. 79 tests to 81.

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

**`attack_vector` is not checked against `RED_TEAM_VECTORS`.** The attacker model picks
it. `_TOOL_REPORT_FINDING` puts no enum on the field and `_classify_reported_findings`
reads `raw.get("attack_vector")` ahead of `session.attack_vector`, so a finding carries
whatever the model typed. `run_identity_bypass_agent` dispatches as `identity_bypass` and
its findings say `identity_verification_bypass`, which is the pair a reader meets first.
A roster check here would throw away the probe and the response over one word.

The cost is real and is recorded in the module docstring: `run_red_team` upserts one
`red_team_strategies` row per distinct `attack_vector`, so an invented name becomes a
strategy row, and `RedTeamResult.coverage` reads the vector rows alone and never matches a
finding to a row. The first version of this bullet cited `prompt_injection` as a name the
M7 findings still carry. No shipped runner has emitted it since the SEC-03 split;
`run_conversation_injection_agent` passes `attack_vector="conversation_injection"`, with
the old default recorded in a comment above the call.

## The two loose ends PR #111 named

**The name collision is gone.** Commit `46e20e1` above.

**Nothing reads `red_team_runs.result`, and nothing does after this either.** Left open
deliberately. Criterion 3 is about what the column holds, and a reader is a separate
piece of work with its own surface (which route, what shape, whose gate). What changed is
that the column is now worth reading on its own: it carried counts and no findings, so a
reader would have had to join `red_team_runs.findings` and trust one pass wrote both.
That join is what this commit removes. Belongs on a new ticket, not opened here.

## Observed

Every number below was measured at `a5c2cb9`, in this working tree, by the command above
it. The first version of this section carried three that do not reproduce, and one output
attributed to a command that cannot produce it. Both are recorded under the mutations.

```
$ .venv/Scripts/python.exe scripts/gates.py static

ruff: clean against the 0 pinned baseline violation(s).
Analyzed 226 files, 1202 dependencies.
app layers run one way                 KEPT
no import cycles outside app.services  KEPT
the provider SDKs have one home        KEPT
Contracts: 3 kept, 0 broken.
app\worker\tasks\runtime\red_team.py:416: warning: run_red_team has 249 NLOC,
    30 CCN, 1174 token, 2 PARAM, 463 length, 0 ND
complexity: clean against the 115 pinned function(s).
source assertions: clean against the 44 pinned file(s), 113 site(s).
static gates passed in 8.1s.
```

`run_red_team` sits on its exact pin, 30 CCN and 463 lines, unmoved. The deploy-gate test
this commit adds needed no change to the task, so there was nothing to move it with.

```
$ pytest tests/unit/test_red_team_result_type.py tests/unit/test_red_team_task.py \
         tests/unit/test_red_team_service.py tests/unit/test_red_team_probe.py \
         tests/unit/test_transactional_tools.py -q

316 passed in 33.19s
```

The wider unit set is 23 files. The rule is every `tests/unit/test_*.py` naming
`red_team_service`, `red_team_probe`, `red_team_result` or `red_team_finding`, plus
`test_transactional_tools.py`, which FM-014 requires in the same process as
`test_red_team_probe.py`. Naming the rule and not the files is what let the earlier
"24 unit files" stand unchecked, so here are the 23:

```
test_agent_loop               test_migration_0012        test_red_team_task
test_agent_options_seam       test_model_client          test_redteam_findings
test_confirmation_resolution  test_pii_firewall_seam_parity  test_redteam_programme
test_deployment_routes        test_recorded_side_effects test_tool_loop_agents_are_given_tools
test_deployment_service       test_red_team_probe        test_transactional_tools
test_gates                    test_red_team_result_type  test_ver01_demo_tenant
test_idv_message_verdict_pin  test_red_team_rtx_runners  test_ver01_harness_probes
test_judgement_temperature    test_red_team_service
```

```
$ pytest <the 23 files above> -q

905 passed, 1 skipped in 202.24s
```

The same 23 files with this commit's six rolled back to `03e6582` give 898 passed,
1 skipped in 184.00s. The 7 new tests are the whole difference.

The five gated integration files are the same rule over `tests/integration/`:
`test_act07_resolve_live.py`, `test_aud03_audit_gap.py`, `test_deploy_gate_redteam.py`,
`test_red_team_rtx.py`, `test_ver01_adversarial_harness.py`. They need
`INTEGRATION_TESTS_ENABLED`, so they are collected and not run.

```
$ pytest <the five files above> -q --collect-only

13 tests collected in 0.25s
```

## Mutations, each restored with `git checkout HEAD --`

```
mutation: import app.services.red_team_service, appended to red_team_finding.py

  $ scripts/gates.py static
  app\domain\red_team_finding.py:101:1: E402 Module level import not at top of file
  app\domain\red_team_finding.py:101:8: F401 `app.services.red_team_service` imported
      but unused
  ruff: 2 violation(s) outside the pinned baseline
  FAILED at step 1 (ruff) after 0.2s, exit 1.

  The gate stops at ruff and never reaches import-linter, so it cannot show a broken
  contract for an appended import. The first version of this trace printed one under
  this mutation. lint-imports is what shows it:

  $ .venv/Scripts/lint-imports.exe
  Contracts: 1 kept, 2 broken.
  app.domain is not allowed to import app.services:
  - app.domain.red_team_finding -> app.services.red_team_service (l.101)
  no import cycles outside app.services: .domain -> .services (1 import)

restored                                       Contracts: 3 kept, 0 broken.

mutation: ConfigDict(frozen=False, extra="forbid")
  test_a_finding_cannot_be_reassigned          1 failed, 54 passed
restored                                       55 passed

mutation: extra="forbid" dropped
  test_a_seventh_key_is_refused_rather_than_carried
  test_splatting_the_same_dict_is_what_the_boundary_avoids
                                               2 failed, 134 passed
restored                                       136 passed
  (test_red_team_result_type.py and test_red_team_service.py in one invocation)

mutation: _as_findings wrong-type list is always empty
  test_a_row_that_is_not_a_finding_is_refused  1 failed, 54 passed
restored                                       55 passed

mutation: payload writes "findings": []
  test_severity_vector_probe_and_response_survive_the_column
  test_the_stored_result_carries_the_worst_grade_and_the_breach_count
                                               2 failed, 80 passed
restored                                       82 passed
  (test_red_team_result_type.py and test_red_team_task.py in one invocation)

mutation: _require_findings_agree not called from __post_init__
  test_the_stored_findings_agree_with_the_counts_beside_them
  test_a_grade_the_findings_never_reached_is_refused
  test_more_breaches_than_findings_is_refused  3 failed, 52 passed
restored                                       55 passed

mutation: deployment_blocked = (max_severity == "high")
  test_run_red_team_complete
  test_a_critical_finding_blocks_the_deployment
  test_a_high_finding_does_not_block_the_deployment
                                               3 failed, 24 passed
restored                                       27 passed

mutation: _attempt_every_vector returns RedTeamResult(k=k, vectors=outcomes)
  run_red_team.agents_failed error="RedTeamResult holds 0 finding(s) whose worst grade
  is 'none', beside vector rows reporting 'critical'. One run has one worst finding."
                                               5 failed, 22 passed
restored                                       27 passed
```

The last one changed character with this commit. The earlier trace recorded it as
"5 failed, 45 passed" over a scope it never named. Rolled back to `03e6582` and run over
`test_red_team_task.py`, the same mutation is 2 failed, 23 passed of 25, and the run it
mutates COMPLETES: it stores `findings_count=0` beside `breaches=6` and reads as clean.
The record now refuses the construction, so the run fails at Step 5 with the disagreement
named in the log, and three more tests see it.

## Deviations from the ticket

- No reader of `red_team_runs.result` was added, for the reason above.
- The nine `all_findings` sites in `run_red_team` were repointed to `run_result.findings`
  rather than keeping the local. Introducing `all_findings = run_result.findings` as its
  own line would have taken the function to 464 lines and broken its exact lizard pin of
  463, and the pin may only move down.
- `_require_findings_agree` is a module-level function rather than more lines inside
  `__post_init__`. Inline it and `__post_init__` crosses lizard's 60-line standard, which
  puts a new entry in LIZARD_BASELINE, and entries may never be added.
- The invariant is checked over the run's totals, never per vector. Matching a finding to
  its row needs `attack_vector` to name the dispatch vector, and it names whatever the
  attacker model typed.
