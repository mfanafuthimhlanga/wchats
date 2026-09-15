# The relevance judge

Ragas answer relevancy generates questions from the response, embeds them, and measures
cosine similarity to the question asked. It never reads the question and the response as a
pair and decides whether one answers the other. On run `0a99f7ab` it failed 34 of 41 rows
where the owner failed 18 of 46 (#270). A threshold move cannot fix an instrument that
measures the wrong quantity, so this plan replaces the instrument behind the gate and keeps
the Ragas figure as a reported number.

Issue: #274. Related: #58, #60, #270. ADR: `docs/adr/0013-the-gated-relevance-judge.md`.

## Data shape

```python
# app/services/relevance_judge.py
@dataclass(frozen=True)
class RelevanceVerdict:
    verdict: str   # "pass" | "fail" | "unknown"
    reason: str    # one sentence; "pass" and "fail" come from the model, "unknown" names
                   # the error class that produced it
    @property
    def score(self) -> float | None:   # 1.0 | 0.0 | None
```

`unknown` carries score `None`, which reaches `JudgeRecord.scored` as a NULL score beside a
real threshold, so `verdict_for` returns None and the row's `binary_verdict` is NULL.
Measurement honesty holds by construction: a provider outage writes an undecided row, never
a passing one.

The model returns the other two through one forced tool call validated by a pydantic model
with `Literal["pass", "fail"]`. The model has no way to say `unknown`; only this module does.

```python
# the metric vocabulary, app/services/eval_service.py and app/domain/eval_result.py
METRIC_KEYS = (
    "faithfulness",             # ragas, gated, threshold EVAL_FAITHFULNESS_THRESHOLD 0.80
    "answer_relevancy",         # THE RELEVANCE JUDGE, gated, 1.0 / 0.0 / NULL
    "context_precision",        # ragas, not gated
    "context_recall",           # ragas, not gated
    "ragas_answer_relevancy",   # ragas relevancy, reported, threshold_for returns None
)
```

The gated key does not move. The owner's 46 labels, both calibration sheets and
`calibrate_run.GATED_METRICS` name `answer_relevancy`, and every one of them keeps working
without an edit. What changes is which instrument writes that row, and the row says so
itself: `eval_results.judge_identity` carries `prompt_version = "relevance-judge-v1"` where
a Ragas row carries `ragas-<version>`.

```sql
-- tenant migration 0030
ALTER TABLE eval_runs ADD COLUMN source_run_id UUID;
```

NULL on every run that measured an agent. Set on a rejudge run, naming the run whose stored
samples it rescored. A column rather than a key in `config` because the idempotency check
and the calibration join both read it, and a jsonb path is the wrong index for either.

## The two or three calling lines

```python
# app/services/eval_service.py, inside _score_samples.score_one, one call per sample
verdict = await asyncio.to_thread(judge_relevance, resolved or sample.user_input,
                                  sample.response, ledger=ledger)
row["answer_relevancy"] = verdict.score
```

```python
# app/worker/tasks/runtime/rejudge.py
samples = read_eval_samples(source_run_id, conn_str)
write_eval_samples(new_run_id, samples, conn_str)
results = run_ragas_eval(judged, ledger, metric_keys=REJUDGE_METRIC_KEYS)
write_eval_results(new_run_id, results["judge_records"], conn_str)
```

Those two lines settle the signatures. `judge_relevance(question, response, *, ledger)`
mirrors `resolve_question`, because both are one forced tool call billed to a purpose and
both refuse to raise into a scoring pass. `run_ragas_eval` gains one keyword argument,
`metric_keys`, defaulting to every key, so the rejudge asks for two metrics and pays for
two.

## Files

| File | What changes |
|---|---|
| `app/services/relevance_judge.py` | New. The judge, its rubric, its tool, its purpose. |
| `app/core/model_client.py` | `judge_relevance` on `_JUDGE`; its key row in `PURPOSE_KEY_SETTINGS`. |
| `app/core/config.py` | `EVAL_FAITHFULNESS_THRESHOLD` 0.90 to 0.80; `OPENAI_API_KEY_JUDGE_RELEVANCE`. |
| `app/services/eval_service.py` | Fifth metric key, the judge inside the scoring pass, `metric_keys`, `read_eval_samples`, the rejudge run writer. |
| `app/domain/eval_result.py` | Fifth metric key, so a `DatasetOutcome` may report it. |
| `app/worker/tasks/runtime/rejudge.py` | New. The Celery task. |
| `app/api/v1/evals.py` | `POST /agents/{id}/eval-runs/{run_id}/rejudge`. |
| `app/api/mcp.py` | `rejudge_eval_run`, the twenty-first tool. |
| `alembic_tenant/versions/0030_eval_run_source_run.py` | New. `eval_runs.source_run_id`. |
| `docs/adr/0013-the-gated-relevance-judge.md` | New. |
| `docs/guides/mcp.md` | Twenty tools to twenty-one, and the `kind` a rejudge is polled under. |
| `app/domain/calibration_status.py` | `dimensions`, and a `calibrated` envelope earns it from its parts. |
| `app/services/calibration_service.py` | The loader matches one Judge per gated dimension. |
| `tests/evals/calibration/compute_correlation.py` | Per-dimension identity, `combined_status`, the envelope writer. |
| `tests/evals/calibration/calibrate_run.py` | One figure per gated dimension, and `--labels-from`. |
| `app/services/deployment_service.py`, `app/worker/tasks/runtime/deployment.py` | The gate reads `judge_identities`, and the block claim is corrected. |
| `apps/api/.env.example` | The new judge key, and the gate's new number. |
| `scripts/gates.py` | `run_ragas_eval`'s lizard pin, lowered. |

## Risks

**The calibration artifact was measured on the old instrument.** #58's figure and the 46
labels describe a Ragas relevancy verdict. The labels transfer, because a human labelling
"does this answer the question" is labelling the new judge's rubric more closely than the
old one's. The verdicts do not. Nothing in this branch claims a calibration; the rejudge run
and `calibrate_run.py --score` produce the new figure, and the ADR says the old one is
superseded rather than carried.

**A run's relevancy scores stop being comparable across the change.** A pre-change run's
`answer_relevancy` is a similarity in [0, 1]; a post-change run's is 1.0 or 0.0. Any series
chart over both reads a step change that is the instrument, not the agent. The
`judge_identity` on each row is what tells the two apart, and it is on every row from #47
onwards.

**The threshold setting can invert the verdict.** `threshold_for("answer_relevancy")` still
returns `settings.EVAL_RELEVANCY_THRESHOLD`, and a value of 0.0 would make a FAIL clear its
gate. A unit test pins the setting into `(0, 1]` with that sentence as its reason.

**The reason is produced and discarded.** The judge asks for a one-sentence reason because a
judge made to state one decides better, and the reason is logged bounded rather than stored:
storing it needs a column or a revival of `eval_results.detail`, which 0023 emptied on
purpose, and #274 does not ask for one. A reader wanting to know why a row failed has the
log line and not the row.

**`--score` read its sheets from `runs/<run_id>/`, and a rejudge has none.** Closed in the
review pass: `--labels-from <source_run_id>` reads the sheets from the run that was labelled
while reading the verdicts from the run that was scored. The scenario ids survive the sample
copy, so the join is exact and no file moves.

## Tests

Each one observed, in `tests/unit/test_relevance_judge.py`, `test_rejudge_task.py`,
`test_migration_tenant_0030.py`, and additions to `test_eval_routes.py`,
`test_eval_service.py`, `test_eval_task.py`, `test_mcp_routes.py`,
`test_resolved_question_scoring.py`, `test_calibrate_run.py` and
`test_calibration_harness.py`.

- a typed pass and a typed fail come back as `RelevanceVerdict` with score 1.0 and 0.0
- a reply carrying no tool call, a truncated one, and a provider error each yield `unknown`
  with the error class in the reason, and no raise
- one call is billed to `judge_relevance`, asserted on the factory argument
- the rubric text reaches the model verbatim
- `EVAL_FAITHFULNESS_THRESHOLD` is 0.80 and `threshold_for` returns it
- `threshold_for("ragas_answer_relevancy")` is None, and the key is absent from
  `GATED_METRIC_KEYS`
- the identity on an `answer_relevancy` row names `relevance-judge-v1`
- the rejudge task writes a new run carrying `source_run_id`, copies the samples, and
  leaves the source run's `eval_results` and `eval_samples` byte-identical
- a second call for the same source and the same INSTRUMENT returns the existing run and
  makes no model call, and a threshold move alone is a different instrument
- the route 404s on an agent belonging to another tenant
- `rejudge_eval_run` is in the MCP tool table and the count pin reads 21
- the default listing asks for `m6:{agent_id}` and `?kind=rejudge` asks for the other, an
  unnamed kind is a 400 before any SQL, and a rejudge row carries its `source_run_id`
- a forged `QUESTION:` or `RESPONSE:` header inside a response stays inside its block and
  the verdict still comes off the tool call
- a reason past 300 characters is `unknown` rather than a paragraph in the log
- a tenant behind 0030 reports `tenant_behind_0030` and is not retried
- a scoring failure lands the NEW run `failed` and propagates
- a two-instrument run names both Judges and reports each kappa, one failing dimension makes
  the envelope uncalibrated, and an artifact silent about a gated dimension is not read as
  covering it
- `--labels-from` scores a rejudge against the source run's sheets with no file copied

## What the adversarial review changed

The first pass built the Judge, the gate move and the re-judge path. The review found
seven things wrong with them and two gaps in what the calibration harness could say.

**The console would have read a rejudge as the agent's current quality.**
`_LIST_EVAL_RUNS_SQL` had no `kind` filter, so it returned every row in `eval_runs` and a
rejudge is the newest one the moment it is dispatched: no `result`, two metrics, no agent
turn. Every other reader of a run was already kind-scoped. The listing takes
`?kind=eval|rejudge`, defaults to `eval`, refuses anything else with a 400 rather than
passing a string to SQL, and a rejudge row carries `source_run_id` so a reader can join the
second opinion to the measurement it is about.

**The idempotency key was one Judge where the rejudge pays for two and writes against two
gates.** It is `rejudge_instrument()` now: every `REJUDGE_METRIC_KEYS` identity and every
`threshold_for`, compared as one jsonb block. A gate move alone is a different measurement
and is paid for.

**The calibration harness could not name a Judge for a run scored by two.** That was
issue #275, and it is closed rather than deferred. `calibration.json` is an envelope,
ARTIFACT_VERSION 2, carrying one `CalibrationStatus` per gated dimension with its own
kappa, intervals and Judge; the envelope's status is the worst of its parts and it names no
Judge of its own. `judge_identity_for_run` takes a dimension, `score_run` bundles rows per
dimension and reaches the harness's unchanged floors and ceiling for each,
`EvalResult.judge_identities` carries one identity per gated metric, and
`load_calibration_status` matches them pairwise. `calibrate_run.py --score <rejudge>
--labels-from <source>` joins a rejudge to the source run's sheets with no file copied.

**Four places said the calibration gate blocks a deploy. It does not.**
`apply_signal_evidence_gate` does not read the key and `decide` carries it without gating
on it (#54). Corrected in `eval_service.run_judge_identity`, `deployment_service._calibration_block`,
`deployment.py`'s `_awaited_records` caller, ADR 0013 and the two-instrument test.

**The judge's sections were labels a response could forge.** A stored answer containing its
own `RESPONSE:` line read as the start of a second section. Both sections are wrapped in the
BEGIN and END grammar `app.domain.context_frame` uses on retrieved chunks, and the system
prompt names the markers as the boundary.

Also: `_rejudge` answers `tenant_behind_0030` without retrying, because three attempts find
the same missing column three times; the reason is capped at 300 characters and logged for
fail and unknown only; `question_resolution_provenance` counts the gated column alone;
migration 0030 cites `eval_samples` as the no-foreign-key precedent, which is the table that
actually lacks one; and two fixtures that passed faithfulness at 0.8 against the new 0.80
gate now use 0.79, so the stated reason is the operative one.

## Mutations

Run 2026-09-15, each restored byte-exact from a copy taken before the edit and re-observed
green. Nine mutations over six files, applied and restored by one script so the restore
cannot drift from the backup.

| # | Mutation | Observed red |
|---|---|---|
| a | `_unknown(...)` becomes `RelevanceVerdict("pass", ...)` on a provider error | `E AssertionError: assert 'pass' == 'unknown'` |
| b | `ledger.client(...)` becomes a bare `openai.OpenAI()` | `E AssertionError: the verdict was billed to [], not 'judge_relevance'` |
| c | threshold back to 0.90 | `E AssertionError: assert 0.9 == 0.8` |
| d | `_score_into(source_run_id, ...)` | `E AssertionError: assert {'aaaaaaaa-00...'} == {'89569ddc-21...'}` |
| e | `existing = None` | `E AssertionError: {'status': 'complete'} != {'status': 'already_rejudged'}` |
| f | the `kind` filter leaves `_LIST_EVAL_RUNS_SQL` | `E AssertionError: ['evals._LIST_EVAL_RUNS_SQL'] read eval_runs with no kind filter` |
| g | `thresholds` leaves the idempotency key | `E AssertionError: a gate move left the idempotency key unchanged` |
| h | `judge_identity_for_run` drops the dimension filter | `E AssertionError: assert None == JudgeIdentity(..., prompt_version='ragas-0.4.3')` |
| i | the section delimiters leave the judge prompt | `E AssertionError: assert 'BEGIN QUESTION' in 'QUESTION:\nDo you ship...'` |

Full tails:

```
(a) FAILED tests/unit/test_relevance_judge.py::test_a_provider_error_is_unknown_never_pass
    1 failed, 22 deselected in 17.02s

(b) FAILED tests/unit/test_relevance_judge.py::test_the_spend_is_billed_to_its_own_purpose
    FAILED tests/unit/test_relevance_judge.py::test_the_row_the_judge_bills_carries_the_run_as_its_job
    2 failed, 21 deselected in 29.31s

(c) FAILED tests/unit/test_eval_service.py::TestTheThresholdIsDefinedOnce::test_the_faithfulness_gate_is_zero_point_eight
    FAILED tests/unit/test_rejudge_task.py::TestTheKeyIsEveryJudgeAndEveryGate::test_the_key_carries_the_gate_each_dimension_writes_against
    2 failed, 177 deselected in 64.07s

(d) FAILED tests/unit/test_rejudge_task.py::TestTheRejudgeWritesItsOwnRun::test_the_rejudge_never_writes_into_the_source_run
    FAILED tests/unit/test_rejudge_task.py::TestTheRejudgeWritesItsOwnRun::test_the_samples_are_copied_so_the_new_run_can_be_labelled
    2 failed, 1 passed, 24 deselected in 37.85s

(e) FAILED tests/unit/test_rejudge_task.py::TestASecondRejudgeCostsNothing::test_a_second_rejudge_returns_the_first
    1 failed, 26 deselected in 48.75s

(f) FAILED tests/unit/test_eval_routes.py::TestTheListingIsScopedToOneKind::test_the_default_listing_asks_only_for_this_agents_eval_runs
    FAILED tests/unit/test_rejudge_task.py::TestARejudgeIsNotTheAgentsCurrentReading::test_every_run_reader_filters_a_kind_and_none_of_them_match_a_rejudge
    2 failed, 81 deselected in 48.52s

(g) FAILED tests/unit/test_rejudge_task.py::TestTheKeyIsEveryJudgeAndEveryGate::test_the_key_carries_the_gate_each_dimension_writes_against
    FAILED tests/unit/test_rejudge_task.py::TestTheKeyIsEveryJudgeAndEveryGate::test_a_threshold_move_alone_is_a_different_instrument
    2 failed, 25 deselected in 94.88s

(h) FAILED tests/unit/test_calibration_harness.py::TestTheIdentityIsAskedPerDimension::test_each_dimension_names_its_own_judge
    FAILED tests/unit/test_calibration_harness.py::TestTheIdentityIsAskedPerDimension::test_a_row_the_judge_never_scored_does_not_name_the_judge
    2 failed, 2 passed, 105 deselected in 5.53s

(i) FAILED tests/unit/test_relevance_judge.py::TestAResponseCannotForgeASection::test_each_section_is_wrapped_in_a_begin_and_an_end_marker
    FAILED tests/unit/test_relevance_judge.py::TestAResponseCannotForgeASection::test_a_forged_header_inside_the_response_stays_inside_the_response
    2 failed, 2 passed, 19 deselected in 57.44s
```

**(h) went green on its first attempt and the test was wrong, not the code.** `score_run`
bundles rows per dimension and hands each bundle its own table, so the filter inside
`judge_identity_for_run` was doing nothing observable through that loop: a mutation removing
it passed. `TestTheIdentityIsAskedPerDimension` drives the function with the pooled table
that makes it matter, and (h) is red against that.

## Focused tests

Every file this branch touches, run together after the mutations were restored:

```
1418 passed, 1 warning in 796.91s (0:13:16)
```

`gates.py fast` over the whole tree, which is static plus mypy plus collection:

```
5256 tests collected in 129.94s (0:02:09)

==============================================================================
fast gates passed in 289.4s.
```

## Migration round trip

0030 up, down to 0029, up again, through `migrations.run_tenant_migrations` against the
local `wchats_tenant_probe` cluster on `localhost:5432`, with `command.downgrade` on the
same injected connection for the way down. Re-observed 2026-09-15 after the review:

```
before                     revision=0030 column=('uuid', 'YES', None) index=yes comment=yes
after upgrade head         revision=0030 column=('uuid', 'YES', None) index=yes comment=yes
after downgrade 0029       revision=0029 column=None index=no comment=no
after re-upgrade           revision=0030 column=('uuid', 'YES', None) index=yes comment=yes
```

The first line reads 0030 because the cluster was left at head by the earlier run; the
downgrade and the re-upgrade are the round trip.

## Lizard

Measured, not asserted: `lizard app -C 15 -L 60 -a 11 --warnings_only` reports **111
warning lines against 111 baseline entries**, and `gates.py static` reports
`complexity: clean against the 111 pinned function(s)`.

Two pins moved, both DOWN, and no entry was added.

- `eval_service.run_ragas_eval` 11/153 to 6/125. It gained an instrument to assemble, so the
  metric building went to `_judge_samples` and the attribution to `_attributed`.
- `api/v1/evals.list_eval_runs` 13/87 to 12/84. It gained a kind filter and a three-rung
  read ladder, so the ledger round trip went to `_fetch_ledger` and the rendering to
  `_rendered_runs`.

Four functions were split to land under the standard without a pin:
`judge_relevance` gave the wire to `_ask` and the parsing to `_verdict_of`;
`rejudge_eval_run` kept the retry and gave the work to `_rejudge`, `_score_into`,
`_run_row_written`, `_already_rejudged` and `_mark_failed`;
`EvalResult.from_payload` gave its field reading to `_stored_fields` and its identity check
to `_require_identities`.

## What this branch does not close

**The gate is decided by a model label with no calibration figure.** The rubric is the
owner's and the instrument it replaced was measured failing, which is a reason to believe
this one is better and not a reason to believe it is right. The figure comes from a rejudge
of run `0a99f7ab` scored against the 44 doubly labelled rows now in the tree, and that score
is the merge condition, whatever it says. ADR 0013 states it.

**`ragas_answer_relevancy` is reported, not drawn.** It reaches the API payload on both eval
routes. The console draws four channels and does not draw it, and no frontend work is in
this branch (UI comes last).
