# 260830, the calibration record and its loader (#53)

Branch `feat/calibration-status` off `main` at 8933c71. Execution plan on #53, comment
"Execution plan, 2026-08-30".

The calibration harness reaches a verdict on a Judge and nothing on the deploy path can call
it. It lives under `tests/`, costs one judge call per labelled row, runs by hand, and
`apps/api/.dockerignore` excludes the tree it sits in. So its answer travels as data.

## Slice 1

```
apps/api/app/domain/calibration_status.py            new, Interval + CalibrationStatus
apps/api/app/services/calibration_service.py         new, load_calibration_status
apps/api/app/core/config.py                          CALIBRATION_ARTIFACT_PATH + resolver
apps/api/tests/unit/test_calibration_status_type.py  new, 29 tests
apps/api/tests/unit/test_calibration_service.py      new, 24 tests
```

Two commits. `9e0fca7` the record with its test, `ce8d2f3` the loader, the setting and theirs.

### Fields, and the harness key each maps from

Sources are `tests/evals/calibration/compute_correlation.py` result dict (1111-1127) and the
`gate` it embeds from `agreement.calibration_verdict` (415).

| field | harness key |
|---|---|
| `status` | `status` (1136, 1149-1155), spelled its way |
| `reason` | slice 2 picks from `errors` / `gate["reasons"]`; the loader stamps an `ABSENT_REASONS` token |
| `judge_identity` | not in the result dict. Slice 2 supplies it from `judge_identity_for` per metric, run-level rule in `eval_service.run_judge_identity` |
| `judge_interval` | `judge_interval` |
| `ceiling_interval` | `ceiling_interval` |
| `difference_interval` | `difference_interval` |
| `beats_chance` | `gate["beats_chance"]` |
| `ceiling_beats_chance` | `gate["ceiling_beats_chance"]` |
| `reaches_ceiling` | `gate["reaches_ceiling"]` |
| `kappa` | `kappa` |
| `matthews` | `matthews` |
| `scored_pairs` | `scored_pairs` |
| `pairs` | `pairs` |
| `attempted` | `attempted` |
| `valid` | `valid` |
| `labelled_at` | not in the result dict. Slice 2, from the sheets' mtime or the run clock |
| `harness_version` | not in the result dict. Slice 2, from the script |
| `artifact_version` | this module's constant, 1 |

`Interval` takes four of the seven keys `agreement._interval` returns: `low`, `high`, `point`,
`usable`. `undefined_fraction`, `coverage_of_total_mass` and `spans_the_whole_range` are the
harness's working, and the conclusion drawn from them is already on the record as `usable` and
as the three verdict parts.

Harness keys deliberately not carried: `cells` and `table` are the report card a human reads at
the terminal, `rho` and `pair_rate` are reported and not gated, and `gate["calibrated"]` is
`status == "calibrated"` by the harness's own branch, so the `calibrated` property is the single
derivation.

### Rules on the record

- Four statuses, the harness's own. `calibrated` is true for exactly one.
- `calibrated` costs the most. It needs a `judge_identity` and needs both `beats_chance` and
  `reaches_ceiling` to be True. `None` is refused as hard as `False`, because an unevaluated
  part is an absence and a deploy shipping on an absence ships on a measurement nobody made.
- `scored_pairs <= pairs`. Every row that appends to `human_scores` first appended to
  `binary_pairs` (`compute_correlation.py` around 1004), so Spearman's denominator is a subset
  of the gate's.
- Coefficients and interval bounds are finite or None. The harness converts NaN to None at
  1113; a NaN reaching the record would break the round trip silently, because NaN compares
  unequal to itself.
- `low > high` is refused. Bounds the wrong way round read as a narrow interval.
- Every shape failure in `from_payload` leaves as `InvalidCalibrationStatus`, including
  `JudgeIdentity(**mapping)` raising `TypeError` over an extra or missing key. The loader
  catches this module's refusal alone, so an unwrapped `TypeError` would escape a function
  documented never to raise.

### Loader failure paths

`load_calibration_status(path, identity)` returns `not_calibrated_yet` carrying a reason, and
never raises.

| path | reason | logged |
|---|---|---|
| `identity` is None | `no_single_judge_identity` | no |
| file missing | `no_artifact` | no |
| unreadable, or not JSON | `unreadable` | `calibration_artifact_unreadable` |
| `from_payload` refuses | `invalid` | `calibration_artifact_invalid` |
| identity differs | `identity_mismatch` | `calibration_identity_mismatch` |

A missing artifact logs nothing. It is the normal state of a container, and a warning per
deploy summary trains an operator to ignore the log.

The mismatch line carries four fields and no others, pinned by a test:
`run_model`, `run_prompt_version`, `artifact_model`, `artifact_prompt_version`.
**Superseded in slice 2**: it carries six, both identities whole, so a mismatch on
`reasoning_effort` alone shows which field moved.

`CALIBRATION_ARTIFACT_PATH` resolves off `config.py` via `parents[2]`, the way `_find_env_file`
resolves off the same file, so the answer does not vary with the working directory. It defaults
to `apps/api/tests/evals/calibration/calibration.json`. A container reads `no_artifact` there,
which is correct until slice 2 ships an artifact somewhere a container can read. The setting has
a default, so `test_env_example_covers_required_settings.py` does not demand a `.env.example`
row, and none was added.

### Observed

```
$ .venv/Scripts/python.exe -m pytest tests/unit/test_calibration_status_type.py \
    tests/unit/test_calibration_service.py tests/unit/test_judge_identity_type.py \
    tests/unit/test_env_example_covers_required_settings.py \
    tests/unit/test_config_error_redaction.py -q
81 passed in 4.23s

$ .venv/Scripts/python.exe -m mypy app/domain/calibration_status.py \
    app/services/calibration_service.py app/core/config.py \
    --ignore-missing-imports --strict-optional
Success: no issues found in 3 source files

$ .venv/Scripts/python.exe scripts/gates.py static
ruff: clean against the 0 pinned baseline violation(s).
complexity: clean against the 115 pinned function(s).
source assertions: clean against the 44 pinned file(s), 113 site(s).
static gates passed in 9.5s.
```

No lizard pin moved, and no new function crosses CCN 15 or 60 lines. `__post_init__` delegates
to three module-level checks (`_require_members`, `_require_counts`,
`_require_calibrated_is_earned`) to stay under both.

### The guard observed red

Both mutations ran after `ce8d2f3`, restored with
`git checkout HEAD -- apps/api/app/services/calibration_service.py`.

**1. The `FileNotFoundError` clause deleted.** This does not raise: `FileNotFoundError` is an
`OSError`, so a missing file falls into the `unreadable` branch. Three tests still went red,
which is the reason token being distinguished rather than the raise being caught.

```
FAILED TestTheFailurePaths::test_a_missing_file_reads_no_artifact
FAILED TestTheFailurePaths::test_every_reason_the_loader_can_return_is_a_declared_absence
FAILED TestTheMismatchLog::test_a_missing_artifact_logs_nothing
3 failed, 21 passed in 2.71s
```

**2. The clause body replaced with `raise`.** A missing artifact now leaves the loader as an
exception, and the never-raises test is the one that catches it.

```
E       FileNotFoundError: [Errno 2] No such file or directory: '...\\gone.json'
FAILED TestTheFailurePaths::test_a_missing_file_reads_no_artifact
FAILED TestTheFailurePaths::test_every_reason_the_loader_can_return_is_a_declared_absence
FAILED TestItNeverRaises::test_no_failure_path_raises
FAILED TestTheMismatchLog::test_a_missing_artifact_logs_nothing
4 failed, 20 passed in 2.53s
```

Restored, `53 passed in 1.89s`.

### Deviations from the plan

- **`harness_version` is `str | None`, not required text. The plan was wrong.** The plan lists
  it as text beside `labelled_at` as "ISO text or None". `absent()` builds a record no harness
  produced, so a required field would force a string to stand in for a harness that never ran.
  None says no harness produced this record, which is every record `absent` builds.
- **`ABSENT_REASONS` holds five, not four.** The plan's build step lists four
  (`no_artifact`, `unreadable`, `invalid`, `identity_mismatch`) and its loader step adds
  `no_single_judge_identity`; its test step then says "the four failure paths plus the identity
  mismatch". Five is the reconciliation.
- **`identity is None` is checked before the file is opened**, where the plan lists it fourth.
  A run with no single Judge cannot match any artifact whatever the file says, so no I/O is
  needed to answer. This only changes which reason wins when a run has no single Judge and no
  artifact exists; every named test drives one failure and sees the same reason either way.

### Open, for slice 2 and the reviewer

- **A mismatch on `reasoning_effort` alone prints two identical-looking log lines.** The plan
  pins the line to both identities' model and prompt version, "never anything else", and effort
  is the third field of the identity. The `identity_mismatch` reason is still correct and the
  test `test_a_mismatch_on_reasoning_effort_alone_is_still_a_mismatch` covers the behaviour; it
  is the operator-facing line that cannot say which field moved.
- **The record enforces `scored_pairs <= pairs` and nothing else about the counts.**
  `pairs <= valid <= attempted` looks true of the harness and was not traced through every
  early return, so it is not enforced.
- **`from_harness` is slice 2's**, beside the writer. `payload` is one field per key in
  declaration order so that mapping is a copy rather than a translation.

## Slice 2

```
apps/api/app/domain/calibration_status.py             from_harness + four mapping helpers
apps/api/app/services/calibration_service.py          the mismatch log carries both identities whole
apps/api/tests/evals/calibration/compute_correlation.py  the writer, and one key set per return
apps/api/tests/unit/test_calibration_status_type.py   +26 tests, 55 in the file
apps/api/tests/unit/test_calibration_harness.py       +15 tests, 88 in the file
apps/api/tests/unit/test_calibration_service.py       +1 test, 25 in the file
```

Two commits. `5715c7e` `from_harness` with its tests, `5228d6d` the writer, the loader's
log line and theirs.

### The dimension does not map to a metric, and the harness cannot name its Judge

This is the finding the slice turns on, and the plan assumed the opposite.

`judge_identity_for(metric)` takes one of `eval_service.METRIC_KEYS` (`faithfulness`,
`answer_relevancy`, `context_precision`, `context_recall`) and indexes
`JUDGE_PURPOSE_BY_METRIC` with it. The calibration harness's `dimension` column holds the
five AI-SPEC 5.2 rubric names in `tests/evals/judge.py:JUDGE_RUBRICS`
(`grounding_fidelity`, `escalation_accuracy`, `prompt_injection_resistance`,
`session_continuity`, `knowledge_gap_honesty`). `judge_identity_for` raises `KeyError` on
every one of them. Nothing in this repo joins the two vocabularies, and writing a table
that joined them here would assert an equivalence nobody measured: `grounding_fidelity` is
a rubric a different judge reads, not a spelling of `faithfulness`.

The captured responses record nothing about a judge either. `tests/evals/responses/S-*.json`
holds `{scenario_id, runs}`, which is the agent's turns; the judge is called live inside
`compute_correlation` at run time. So there is no recorded identity to recover.

And the judge that runs cannot be identified at the grain `JudgeIdentity` keys on.
`tests/evals/judge.py` builds its own `anthropic.Anthropic()` client at a model literal,
requests no reasoning effort, and reads a rubric authored in this repo and versioned
nowhere. Two of the three fields would have to be invented, and `judge_identity.py` exists
because an invented field widens the key until two Judges group under one figure.

**So a captured-then-scored artifact cannot honestly be `calibrated` today**, and
`JUDGE_IDENTITY_BY_DIMENSION` is an empty table that says why. `judge_identity_for_run`
reads it, returns the one identity every dimension shares, and returns None when they
differ, when any dimension names none, and when the run reached no rows. The day this judge
moves onto `app.core.model_client` (issue #88's family), that table is the one place that
changes.

### What the writer does with that

`calibration_record` holds one judgement and it is named: a run whose dimensions name no
single Judge leaves `CalibrationStatus.absent("no_single_judge_identity")`, stamped with
`harness_version` and `labelled_at`, **even when the gate passed**. A kappa with no Judge
attached is a number about a judge nobody can name, and a deploy acting on it would ship on
a Judge nobody measured. `no_single_judge_identity` is already in `ABSENT_REASONS`, so the
writer and the loader stamp one vocabulary.

Every other status is written as the harness reached it, `setup_error` included.
`judge_identity: null` on those is honest rather than disqualifying: a run that could not
read its own inputs states a fact about the inputs, not about any Judge.

### Deviations from the plan, and what was added

- **`from_harness` cannot take the identity from `judge_identity_for`, because that
  function refuses the harness's dimensions.** The brief's fallback assumed it accepted
  one. The resolution above replaces it.
- **The four early returns in `compute_correlation` gained `scored_pairs: 0` and
  `difference_interval: None`.** They carried fourteen keys where the full result carries
  sixteen, so a `setup_error` could only be mapped by defaulting two of them, and a default
  is how a record comes to report a figure the harness never stated. All five return paths
  now carry one key set and the mapping requires every key it reads. No existing test pins a
  result key set, and `_print_agreement` already read `scored_pairs` through `.get`.
- **`reason` comes from `gate["reasons"]` joined, as the brief says, and a run that reached
  no gate carries `reason: null`.** The `errors` list is not carried. It is the terminal's
  report, one entry per row on some paths, and the record's `status` already says a partial
  run was partial. A `setup_error` artifact therefore names no sentence; open the terminal
  or re-run `--check` for that.
- **The write happens before the report is printed**, so a run that dies formatting its own
  output still leaves the record. An `OSError` there prints a line and does not cost the
  operator the report they paid for, or replace a real exit code with 1.

### Observed

```
$ .venv/Scripts/python.exe -m pytest tests/unit/test_calibration_harness.py \
    tests/unit/test_calibration_status_type.py tests/unit/test_calibration_service.py \
    tests/unit/test_agreement_threshold.py tests/unit/test_agreement_statistics.py -q
217 passed in 90.44s (0:01:30)

$ .venv/Scripts/python.exe -m mypy app/domain/calibration_status.py \
    app/services/calibration_service.py --ignore-missing-imports --strict-optional
Success: no issues found in 2 source files

$ .venv/Scripts/python.exe -m ruff check tests/evals/calibration/compute_correlation.py
All checks passed!

$ .venv/Scripts/python.exe scripts/gates.py static
ruff: clean against the 0 pinned baseline violation(s).
complexity: clean against the 115 pinned function(s).
source assertions: clean against the 44 pinned file(s), 113 site(s).
static gates passed in 10.6s.
```

The source-assertion count is unchanged. The writer is pinned by behaviour, and the tests
read the artifact through `Path.open`, so `test_calibration_harness.py` still holds its
three sites and the `csv.writer` / `csv.DictWriter` / `HUMAN_SCORES_CSV.write` pin stays
green: the writer emits JSON and names none of them.

### The guards observed red

**1. `from_harness` drops `matthews`.** Run after `5715c7e`, restored with
`git checkout HEAD -- apps/api/app/domain/calibration_status.py`. Two tests caught it: the
one that reads every mapped key back off the record, and the parametrised refusal, which
stops refusing a result dict that is missing the key the mapping no longer reads.

```
E       Failed: DID NOT RAISE InvalidCalibrationStatus
FAILED TestFromHarnessMapsEveryKey::test_every_mapped_key_reaches_its_field
FAILED TestFromHarnessRefusesWhatItCannotMap::test_a_result_missing_any_mapped_key_is_refused[matthews]
2 failed, 53 passed in 2.94s
```

Restored, `55 passed in 1.31s`.

**2. The writer returns early on `setup_error`.** Run after `5228d6d`, restored with
`git checkout HEAD -- apps/api/tests/evals/calibration/compute_correlation.py`.

```
E       FileNotFoundError: [Errno 2] No such file or directory:
        '...\\test_a_setup_error_writes_a_re0\\calibration.json'
FAILED TestEveryScoringRunLeavesARecord::test_a_setup_error_writes_a_record_saying_so
1 failed, 15 passed, 72 deselected in 20.43s
```

Restored, `16 passed, 72 deselected in 16.56s`.

### A defect this slice introduced and closed

`5228d6d` put the writer on `CALIBRATION_ARTIFACT_JSON`, resolved from
`CALIBRATION_DIR` at import. `calibration_tree` redirects the scenarios, the responses and
both sheets, and not that constant, so the three tests in the file that drive `cc.main([])`
each wrote a calibration record into the directory the owner labels. One was sitting
untracked in the working tree after the push, which is what found it. The follow-up commit,
`fix(calibration): the test fixture points the artifact at tmp, like the sheets`, moves the
redirect into the fixture at fixture level, where it holds for a test that never calls
`build`, and pins it: the artifact's directory is not `CALIBRATION_DIR`. Pinned that way
rather than on the real file's absence, because slice 3 may ship an artifact there and a
test that went red on that would be pinning the tree's contents instead of the fixture's job.

The plan did not anticipate it, and the reason is worth naming: the writer's target was
reviewed as "a parameter, so tests can redirect it", and it is. The three tests that broke
do not call the writer. They call `main`, which supplies the parameter itself.

### Open, for slice 3 and the reviewer

- **No artifact is committed, so `CALIBRATION_ARTIFACT_PATH` still resolves to a file that
  does not exist.** The loader reads `no_artifact`, which is correct, and slice 3's deploy
  summary will carry that. Writing one costs a judge call per labelled row and the sheet is
  unlabelled, so nothing here could produce it.
- **A `setup_error` artifact carries `reason: null`.** See the deviation above. If ticket
  17's block message needs the sentence, `errors` is where it lives and carrying its first
  entry is the smallest change.
- **`judge_identity_for_run` reads `result["table"]`, which lists every row the run touched
  including the ones that errored.** That is deliberate: a dimension whose rows all failed
  still tells you which Judges the run was pointed at. It also means a run is refused a
  single identity by a dimension that contributed no pair.

## Slice 3

```
apps/api/app/services/calibration_service.py       SUMMARY_KEYS + summary_of
apps/api/app/services/deployment_service.py        _calibration_block, the key, the prompt
apps/api/tests/unit/test_deployment_service.py     +10 tests, one class
apps/api/tests/unit/test_calibration_service.py    +4 tests, 29 in the file
```

One commit, `5635697`.

### Where the key hangs, and why not in the collector

The plan puts the loader call in `_fetch_eval_summary_sync`, or in a small function it
calls. It is in `_eval_summary`, two calls further down, and the lizard pins are the
reason.

`_fetch_eval_summary_sync` returns through `_eval_summary` at eight sites. Attaching the
key at each costs eight lines against a pin of 204 that may only go down. Wrapping the
collector in a thinner function that attaches once is worse: a pin is keyed on
(file, function name), so renaming the implementation makes the pinned name stop being
reported, which fails, and introduces an over-threshold function with no entry, which
fails too. Entries may never be added.

`_eval_summary` already takes `record` and all eight paths run through it. Deriving the
identity there also covers `no_runs` and `unavailable`, where no record is ever read.
Those two report `no_single_judge_identity`, the same answer the plan specifies for a run
with no record, and for the same reason: there is no Judge for an artifact to be about.

### The pin arithmetic, and the line the docstring gave back

Lizard's `length` is physical lines, so a pinned function cannot gain one. The
`agent_invoked` paragraph in `_eval_summary`'s docstring gave up one wrapped line and
both its em-dashes, and no content with them. Measured after:

```
app/services/deployment_service.py:697: warning: _eval_summary has 25 NLOC, 2 CCN, 150 token, 6 PARAM, 63 length, 0 ND
app/services/deployment_service.py:902: warning: _fetch_eval_summary_sync has 116 NLOC, 12 CCN, 430 token, 2 PARAM, 204 length, 0 ND
app/services/deployment_service.py:1915: warning: apply_signal_evidence_gate has 111 NLOC, 15 CCN, 370 token, 3 PARAM, 251 length, 0 ND
```

`_calibration_block` is 20 lines at CCN 2, under both thresholds, so it needs no entry.

### What the summary carries

Eleven keys, selected off `CalibrationStatus.payload` by `summary_of`: `status`,
`reason`, `judge_identity`, `judge_interval`, `ceiling_interval`, `kappa`, `matthews`,
`scored_pairs`, `pairs`, `labelled_at`, `harness_version`.

Seven fields stay off. `difference_interval` and the three verdict parts are the harness's
working, and `attempted`, `valid` and `artifact_version` answer nothing a deploy report
asks. Ticket 17 reads the record itself, so nothing downstream needs them on the dict.

`calibrated` is not a key. The record derives it from `status` and a consumer reads
`status`, so the summary holds no second answer to one question.

### The refusal is not in force, and one test says so by name

`apply_signal_evidence_gate` is untouched.
`test_the_calibration_refusal_is_not_in_force_yet_ticket_17` drives all four statuses
through the gate over a fully measured, all-passed run and asserts `ship` with no
warnings. It goes red the day #54 lands, so the refusal arrives as a rewrite of a named
test rather than as a behaviour change nobody was watching for.

The prompt paragraph is prose only. It names `eval_summary.calibration`, names
`not_calibrated_yet` as the state the model will see and which reason distinguishes
`no_artifact` from `no_single_judge_identity`, and says no recommendation moves over any
of it. Three substrings are pinned, and that pin is drift protection over a string rather
than evidence about the model.

### The routes, and the admin

Nothing changed and nothing needed to. `_fetch_eval_summary_sync` reaches the admin one
way, through `run_deployment_checklist` into `DeploymentReport.eval_summary`, typed
`dict` in pydantic, so a new key rides along unvalidated. `api/v1/evals.py` surfaces eval
runs and never this summary.

`apps/admin/app/agents/[id]/deploy/page.tsx` is the only reader: line 108 declares
`eval_summary?: {pass_rates?, failing_scenarios?}` and lines 2452-2453 read those two.
The interface is an optional non-exact subset over parsed JSON, so an extra key is not a
type error. No TypeScript changed, so `npx tsc --noEmit` was not run.

### Observed

```
$ .venv/Scripts/python.exe -m pytest tests/unit/test_deployment_service.py \
    tests/unit/test_deployment_task.py tests/unit/test_calibration_service.py \
    tests/unit/test_gates.py -q
222 passed in 129.41s (0:02:09)

$ .venv/Scripts/python.exe -m mypy app/services/deployment_service.py \
    app/services/calibration_service.py --ignore-missing-imports --strict-optional
Found 61 errors in 6 files (checked 2 source files)
```

61 before and 61 after, 3 of them in `deployment_service` and 0 in `calibration_service`
both times. The three are the `tool()` argument types this branch inherited; they moved
from lines 419-421 to 434-436 with the new import and helper above them.

```
$ .venv/Scripts/python.exe scripts/gates.py static
ruff: clean against the 0 pinned baseline violation(s).
complexity: clean against the 115 pinned function(s).
source assertions: clean against the 44 pinned file(s), 113 site(s).
static gates passed in 14.3s.
```

### The guard observed red

`_calibration_block` made to ignore the loader and hard-code the absence, run after
`5635697`, restored with `git checkout HEAD -- apps/api/app/services/deployment_service.py`.

```python
    return summary_of(CalibrationStatus.absent('no_artifact'))
```

```
E       AssertionError: assert 'no_artifact' == 'no_single_judge_identity'
FAILED TestTheCalibrationBlock::test_a_matching_calibrated_artifact_reaches_the_summary
FAILED TestTheCalibrationBlock::test_the_loader_is_asked_about_the_judge_the_run_used
FAILED TestTheCalibrationBlock::test_a_run_with_no_record_has_no_identity_to_ask_about
3 failed, 7 passed, 119 deselected in 44.58s
```

Restored, `10 passed, 119 deselected in 37.56s`.

The four that stayed green are the ones a hard-coded absence satisfies: the missing
artifact, the key set, the gate, the prompt. The three that went red are the three that
say the answer came from the loader and was asked about this run's Judge.

### Deviations from the plan

- **The loader is called from `_eval_summary`, not from the collector.** See above. The
  plan's escape hatch was a selection living in `calibration_service`, which is where
  `summary_of` is; the call site moved one level further for the pin.
- **One docstring paragraph in `_eval_summary` was rewrapped** to pay for the key's line.
  It is the only edit in this slice that exists because of a gate.

## Open after three slices

Everything slices 1 and 2 left open, and where it belongs now.

**#58, the harness-judge prerequisite.**

- No artifact is committed, so `CALIBRATION_ARTIFACT_PATH` resolves to a file that does
  not exist and every deploy summary reads `not_calibrated_yet` / `no_artifact`. That is
  the correct reading, and it stays until a calibration run against a nameable Judge
  exists.
- `JUDGE_IDENTITY_BY_DIMENSION` is empty, so every artifact this harness writes today
  carries `judge_identity: null` and `no_single_judge_identity`. `tests/evals/judge.py`
  builds its own client at a model literal, requests no reasoning effort and reads a
  rubric versioned nowhere, so two of the identity's three fields would have to be
  invented. The table is the one place that changes when the judge moves onto
  `app.core.model_client`.
- `judge_identity_for_run` reads `result["table"]`, which lists every row the run
  touched, so a dimension whose rows all errored still refuses the run a single identity.
  Deliberate, and it stops mattering once the table above is filled.

**#54, the refusal.**

- "ship refused without a calibrated Judge" is not implemented here.
  `test_the_calibration_refusal_is_not_in_force_yet_ticket_17` is the test it rewrites.
- A `setup_error` artifact carries `reason: null`, because the harness's `errors` list is
  the terminal's report and the record's `status` already says the run was partial. If
  the block message needs a sentence, carrying `errors[0]` is the smallest change.

**Owned by neither.**

- The record enforces `scored_pairs <= pairs` and nothing else about the counts.
  `pairs <= valid <= attempted` looks true of the harness and was not traced through
  every early return, so no rule asserts it. No issue holds this; open one before any
  reader starts relying on the ordering.
