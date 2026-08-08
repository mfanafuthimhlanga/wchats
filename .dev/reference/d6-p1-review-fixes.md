# D6 P1 — the adversarial review's findings, and what was done about each

**Branch** `feat/d6-labelling-loop` (off `feat/d1-agent-invocation` @ `4179a5c`, **not `main`**)
**Fix commits** `9e43d80`, `e682106`, `f23930e` (+ this docs commit)
**Reviewed artefact** `4179a5c..aeb949b` — findings in `.dev/reference/d6-p1-adversarial-review.md`
**Written** 2026-08-09. Persisted here per `BACKLOG 2.20`.

Bounded to the 15 findings and 8 unsupported claims in the review. **P2 was not started.**

---

## 1. Gate observations — run, observed, verbatim

```
gate at HEAD (f23930e)
  .venv/Scripts/python.exe -m pytest tests/unit -q \
    --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py
  1994 passed, 12 skipped, 30 warnings in 362.10s (0:06:02)

ignored-new-files control (BACKLOG 2.26) — the same, plus
    --ignore=tests/unit/test_label_provenance.py
    --ignore=tests/unit/test_migration_tenant_0016.py
  1874 passed, 11 skipped, 30 warnings in 354.47s (0:05:54)

same control, plus the ONE test this work added to a PRE-EXISTING test file:
    --deselect tests/unit/test_decision_eval_service.py::TestFixtureDerivation::\
test_a_decision_fixture_does_not_read_as_a_labelled_eval_scenario
  1873 passed, 11 skipped, 1 deselected, 30 warnings in 362.74s (0:06:02)
```

**Read the middle number honestly.** The observed baseline at `4179a5c` is 1873/11. The control
reads **1874**, not 1873, and the +1 is not noise: it is one named test added to
`test_decision_eval_service.py`, a file this phase does not own, because the namespace collision
had to be pinned from both sides. Deselecting exactly that test returns **1873 passed, 11
skipped** — an exact match to baseline, which is the claim the control exists to support. Nothing
else in the 1873 changed status.

Full-gate arithmetic, for completeness (it is arithmetic, not evidence — the control above is the
evidence): 1962 → 1994 = +32 = +28 in `test_label_provenance.py` (59 → 87) +3 in
`test_migration_tenant_0016.py` (30 → 33) +1 in `test_decision_eval_service.py`.

---

## 2. The critical finding, and why the fix is two scans rather than one

**R3 caught a spelling, not a capability**, and it was the only one of the four restrictions
standing between a Celery task and the `label_trust_tier` column. R1 and R4 bind only *callers of*
`record_human_label`; R2 bans *references to* `label_service`; a task issuing raw SQL calls neither.

The review's observation, reproduced from its report: an f-string `UPDATE eval_scenarios SET
reference_answer = %s, {_ADV_TIER_COL} = 'human_authored', labelled_by = ..., labelled_at = NOW()`
appended to `app/worker/tasks/runtime/eval.py` left all 59 tests green. The same forgery as one
plain string constant went red.

**Fix — two scans with different blind spots:**

| | what it does | blind to |
|---|---|---|
| composed-SQL reconstruction | candidate nodes are `Constant` / `JoinedStr` / `BinOp` / `Call`, each flattened to its literal parts in source order, so f-strings, `+`, `%`, `.format`, `.join`, `ON CONFLICT DO UPDATE`, `public.` and quoted identifiers all reassemble | a column name assembled from fragments |
| name-level absence pin | `app/worker/`, the rest of `app/services/`, `scripts/`, `_runlogs/` may not spell `label_trust_tier` / `labelled_by` / `labelled_at` in **any** AST node | the two allowlisted readers, and fragment assembly |

Eight forgery shapes are now permanent fixtures rather than probes someone ran once.

**The claim is now what is true.** Three files said the model-driven producers "physically cannot"
populate a label column. They can. What is asserted instead: *no forgery shape anyone has yet
devised passes unnoticed.* The residual — composing `"label" + "_trust_tier"` inside the allowlisted
`eval_service.py` — is undetectable statically, is written down, and is the argument for R4 being
the last line. `BACKLOG 4.8` carries it.

Two other things fall out of the same work:

- **The allowlist is bounded rather than trusted.** `eval_service.py` may NAME the columns (it
  declares `LABEL_TIER_COLUMN`); `test_the_two_allowlisted_readers_issue_no_eval_scenarios_write`
  asserts it issues no write at all.
- **Docstrings are exempt from both detectors.** Prose is not reachability — a bare string
  expression is bound to `__doc__` and cannot be handed to `cur.execute`. Without the exemption the
  strengthened detectors fired on `eval_service`'s own explanation of what it does not do, which is
  how a wall teaches the next author to delete the wall. Both directions have a control.

---

## 3. Every finding, and its disposition

| # | sev | finding | disposition |
|---|---|---|---|
| 1 | critical | R3 catches a spelling; the f-string forgery is invisible | **fixed** — two scans, 8 forgery fixtures, all three overclaims reworded (`label_service.py`, `test_label_provenance.py`, the reference doc) |
| 2 | high | 0016's CHECK constrains the VALUE, not the AUTHOR; three files claim otherwise | **fixed** — reworded in `0016`'s docstring, `label_service`'s R3 paragraph, `test_migration_tenant_0016.py`, and the reference doc |
| 3 | high | `labelled_by` is caller-asserted free text; a route could forward model prose | **decided and documented, not pinnable today** — see §4 |
| 4 | medium | R2's region is `app/api/`, which includes the anonymous widget surface | **fixed** — region narrowed to `app/api/v1/evals.py`; companion pin that no worker/service imports `app.api`; claim reworded to "a module path" |
| 5 | medium | nothing reads `label_trust_tier`; the vocabulary is write-only | **documented** — P1's stated scope; recorded in §5 so no reader infers protection that is not wired |
| 6 | medium | `decision_eval_service.FIXTURE_LABEL_TRUST_TIER` collides with the new column | **fixed twice** — constant/field/report-key renamed to `FIXTURE_LABEL_PROVENANCE` / `label_provenance` / `fixture_label_provenance`, **and** the resolver refuses a mapping carrying neither `source` nor `reference_answer` |
| 7 | medium | R4's two detectors fail OPEN behind `# pragma: no cover` | **fixed** — `ImportError` (dependency absent, no such context) is silent; any other exception refuses. Both arms now tested and mutated |
| 8 | medium | no `.dev/traces/` entry for P1; BACKLOG not transacted | **fixed** — `260808-d6-p1-label-trust-tier.md`, this trace, and three BACKLOG rows |
| 9 | low | the fixture ban covers 2 files of 159 | **fixed** — all of `tests/` scanned, allowlist is `test_label_provenance.py` alone |
| 10 | low | R2's detector is blind to composed paths; its self-test is all honest spellings | **fixed** — string arm matches any constant mentioning the module or the symbol; 4 evasive arms added to the self-test; the remaining fragment-assembly blind spot is asserted as a documented limit |
| 11 | low | R2/R3 scan `app/` only; `scripts/` and `_runlogs/` are outside every restriction | **fixed** — both scanned. The alembic trees stay excluded, now as a stated decision |
| 12 | low | `is_human_labelled()` is True for a human tier over an empty answer | **fixed twice** — 0016's CHECK gains `AND COALESCE(reference_answer,'') <> ''` inside the human-tier arm, and the resolver downgrades a PRESENT-and-empty answer to `unknown` (a narrow projection is not downgraded) |
| 13 | low | 0016's catalog introspection and DROP are not schema-qualified | **fixed** — `pg_namespace` join + `current_schema()`, `format('ALTER TABLE %I.%I DROP CONSTRAINT %I', ...)`, and the existence guard qualified too. 0011's copy is deployed and is a separate decision |
| 14 | low | the 0015 head assertion was weakened inside the feature commit; the control cannot see that | **partly fixed** — head identity is pinned once in `test_migration_tenant_0016.py` as `heads == {"0016"}`; the reference doc's sentence claiming the control validated the weakening is deleted. **The commit itself cannot be repackaged** — rewriting `c860780` means rewriting history on a branch, and the task forbids rebasing |
| 15 | nit | `"source" not in joined` is a raw substring over the UPDATE SQL | **fixed** — the SET clause's column names are parsed and compared as a set |

All eight **unsupported claims** are addressed by the same edits: each was a sentence in
`label_service.py`, `test_label_provenance.py`, `0016`'s docstring or the P1 reference doc, and each
has been rewritten to the narrower statement the evidence supports. The `rows_updated` /
"denominator" non-sequitur is dropped rather than defended.

---

## 4. Finding 3 — the decision, and what is honestly not pinned

`record_human_label` authenticates the CALL SITE. It cannot authenticate the CONTENT of
`reference_answer` or the identity in `labelled_by`. An `app/api` route that has a model draft an
answer and forwards it with `labelled_by='owner@…'` produces a `human_authored` row of model prose
and trips none of R1–R4.

**Decided now so P2 inherits it rather than inventing it** (written into `label_service`'s module
docstring, § *What the four restrictions do not cover*):

1. `labelled_by` is **derived from the authenticated principal inside the handler**, never read
   from the request body, and no route may accept it as a field.
2. `reference_answer` must arrive **on the authenticated request** as text the principal submitted.
3. A machine-drafted candidate a human approves is `human_verified`, not `human_authored`, and needs
   its own writer recording who approved what.

**Not pinned, and this is the honest part.** The route does not exist. A test asserting a property
of a module nobody has written passes vacuously, which is the exact defect this whole review is
about. It is `BACKLOG 4.7`, against P2.

---

## 5. Finding 5 — what P1 did NOT close, stated so nobody infers it

`HUMAN_LABEL_TIERS`, `LABEL_TIER_COLUMN`, `is_human_label_tier()`, `label_trust_tier()` and
`is_human_labelled()` **have no callers outside `eval_service` and the tests.** The eval selector
(`eval.py:767-788`) projects `id, source, question, reference_answer, retrieved_contexts, dataset`
— the tier is not selected, so it cannot reach scoring. What makes a row enter the eval is still
`WHERE reference_answer != ''`, and `scenario_service.store_scenarios` writes `reference_answer`
freely for `source='generated'`, whose answers Haiku wrote.

**So a Haiku-written answer and an owner-written answer are indistinguishable to every consumer
that exists today.** P1 built the vocabulary and the wall; it did not close the "model prose reaches
the eval" gap, and nothing in this branch should be read as claiming it did. P3 owes the projection
and at least one consumer.

---

## 6. Mutation proofs — 11, every one run, red observed, restored from HEAD, green observed

Restore was `git checkout HEAD -- <path>` unconditionally after every proof; `git status --short`
and `git diff HEAD --stat` were both empty afterwards (apart from the untracked review document).
Verbatim tails.

### #1 — R3 vs. the exact forgery the review used
Selector `tests/unit/test_label_provenance.py::TestR3TheModelWritersCannotWrite`
Mutation: the review's f-string `UPDATE` appended to `app/worker/tasks/runtime/eval.py`.
```
RED    E  AssertionError: a module that may not label a row names a label-provenance column:
          {'app\\worker\\tasks\\runtime\\eval.py': ['label_trust_tier (Constant)',
                                                    'labelled_at (Constant)',
                                                    'labelled_by (Constant)']}
       FAILED ...::test_only_the_label_writer_writes_the_label_columns
       FAILED ...::test_no_model_driven_module_names_a_label_column_at_all
       2 failed, 14 passed in 20.11s
GREEN  16 passed in 17.63s
```
**Both arms fired** — the reconstruction saw `labelled_by`/`labelled_at` inside the f-string, and
the name pin saw all three columns. This is the proof that matters: the identical mutation was
green before this work.

### #2 — R2 sees a composed importlib path
Selector `tests/unit/test_label_provenance.py::TestR2ImportBoundary`
Mutation: `importlib.import_module('app.services.' + 'label_service')` in a Celery task module.
```
RED    E  AssertionError: ... {'app\\worker\\tasks\\runtime\\eval.py':
          ["string containing 'label_service'"]}
       FAILED ...::test_only_the_one_named_api_module_may_reference_the_writer
       1 failed, 17 passed in 8.21s
GREEN  18 passed in 5.88s
```

### #3 — R2's region is one module, not all of `app/api/`
Selector `tests/unit/test_label_provenance.py::TestR2ImportBoundary`
Mutation: `from app.services.label_service import record_human_label` in `app/api/v1/agents.py`.
```
RED    E  {'app\\api\\v1\\agents.py': ['from app.services.label_service import ...',
                                       'from ... import record_human_label',
                                       'name record_human_label']}
       FAILED ...::test_only_the_one_named_api_module_may_reference_the_writer
       1 failed, 17 passed in 6.22s
GREEN  18 passed in 5.47s
```

### #4 — no worker/service module imports the API layer
Selector `...::TestR2ImportBoundary::test_no_worker_or_service_module_imports_the_api_layer`
Mutation: `from app.api.v1 import evals` inside a function in `app/worker/tasks/runtime/eval.py`.
```
RED    E  {'app\\worker\\tasks\\runtime\\eval.py': ['app.api.v1']}
       1 failed in 2.87s
GREEN  1 passed in 2.19s
```

### #5 — the fixture ban covers every test module
Selector `...::TestR2ImportBoundary::test_no_test_module_outside_this_one_may_reference_the_writer`
Mutation: a helper importing `record_human_label` appended to `tests/unit/test_eval_service.py`.
```
RED    E  {'tests\\unit\\test_eval_service.py': ['from app.services.label_service import ...',
                                                 'from ... import record_human_label',
                                                 'name record_human_label']}
       1 failed in 4.69s
GREEN  1 passed in 3.85s
```

### #6 — the resolver refuses a mapping that is not a scenario
Selector `tests/unit/test_label_provenance.py::TestLabelTierVocabulary`
Mutation: `if not _is_an_eval_scenario(scenario): return "unknown"` deleted.
```
RED    E  AssertionError: assert 'human_authored' == 'unknown'
       FAILED ...::test_a_mapping_that_is_not_a_scenario_never_reads_as_human_labelled
       1 failed, 19 passed in 22.28s
GREEN  20 passed in 18.96s
```

### #7 — a human tier over an empty answer fails closed
Selector `tests/unit/test_label_provenance.py::TestLabelTierVocabulary`
Mutation: the empty-answer downgrade deleted.
```
RED    E  AssertionError: assert 'human_authored' == 'unknown'
       FAILED ...::test_a_human_tier_over_an_empty_answer_fails_closed
       1 failed, 19 passed in 18.95s
GREEN  20 passed in 17.19s
```

### #8 — R4's Celery detector refuses on malfunction
Selector `tests/unit/test_label_provenance.py::TestR4RuntimeContextGuard`
Mutation: the `raise HumanLabelRefused` restored to `except Exception: return None`.
```
RED    E  Failed: DID NOT RAISE HumanLabelRefused
       ---------------------------- Captured stdout call ------------------------------
       2026-08-09 01:18:41 [info  ] label_service.human_label_recorded
         label_trust_tier=human_authored labelled_by=owner@example.com rows_updated=1
         scenario_id=11111111-1111-1111-1111-111111111111
       FAILED ...::test_a_broken_celery_detector_refuses_rather_than_proceeding
       1 failed, 8 passed in 14.52s
GREEN  9 passed in 13.15s
```
The captured stdout is the point: with the detector failing open, a wedged current-task stack let
the label be stamped.

### #9 — R4's agent detector refuses on malfunction
Selector `tests/unit/test_label_provenance.py::TestR4RuntimeContextGuard`
Mutation: the same, on `_current_agent_id`.
```
RED    E  Failed: DID NOT RAISE HumanLabelRefused
       ---------------------------- Captured stdout call ------------------------------
       2026-08-09 01:19:32 [info  ] label_service.human_label_recorded
         label_trust_tier=human_authored labelled_by=owner@example.com rows_updated=1
       FAILED ...::test_a_broken_agent_detector_refuses_rather_than_proceeding
       1 failed, 8 passed in 14.71s
GREEN  9 passed in 12.57s
```

### #10 — 0016's CHECK requires a non-empty answer
Selector `tests/unit/test_migration_tenant_0016.py`
Mutation: `AND COALESCE(reference_answer, '') <> ''` removed from the CHECK.
```
RED    E  AssertionError: 0016's CHECK must require a non-empty reference_answer whenever a
          human tier is present
       FAILED ...::test_the_check_refuses_a_human_tier_on_an_empty_answer
       1 failed, 32 passed, 1 skipped in 12.02s
GREEN  33 passed, 1 skipped in 11.21s
```

### #11 — 0016's introspection and DROP are schema-qualified
Selector `tests/unit/test_migration_tenant_0016.py`
Mutation: `pg_namespace` join, `current_schema()` filter and `%I.%I` reverted to 0011's shape.
```
RED    E  AssertionError: 0016's catalog lookup must join pg_namespace so it discovers a
          constraint on the table it is about to alter
       FAILED ...::test_the_catalog_lookup_and_the_drop_are_schema_qualified
       1 failed, 32 passed, 1 skipped in 11.90s
GREEN  33 passed, 1 skipped in 10.95s
```

### #12 — the tenant head is 0016
Selector `tests/unit/test_migration_tenant_0016.py`
Mutation: `revision: str = "0016"` → `"0016b"`.
```
RED    FAILED ...::test_migration_revision
       FAILED ...::test_0016_is_the_sole_child_of_0015_and_the_tree_is_unforked
       FAILED ...::test_0016_is_the_tenant_head
       3 failed, 30 passed, 1 skipped in 11.96s
GREEN  33 passed, 1 skipped in 11.29s
```
Three tests fire, not one — the mutation moves the revision id, so parentage and identity both
react. The identity pin is the one that did not exist before.

---

## 7. What cannot be proven here — unchanged, and stated plainly

- **Migration 0016 has still never been applied.** There is no PostgreSQL server on this machine.
  Every `-m integration` harness **skips**, and a skip is unobserved, never a pass. Neither the new
  `reference_answer` arm of the CHECK nor the schema qualification has executed against any
  database, and neither can be from here. `CONTROL_DB_URL` points at live Neon production and was
  not used.
- **The CHECK has still never rejected anything.** `test_migration_tenant_0016_db_roundtrip` now
  also asserts the empty-answer refusal and writes the tier and the answer together. It skips.
- **No real `eval_scenarios` row has been labelled.** `record_human_label` has only ever run against
  a recording cursor.
- **R4 has still never run in a real Celery worker** — only against Celery's real `_state` stack
  in-process, which is the same stack a worker sets and is not the same as a worker.
- Standing debt shared with `BACKLOG 0.2`, `2.14`, `3.5`.

## 8. Hand-off

P2's contract is unchanged except that it now inherits three decisions rather than three open
questions: the route goes in `app/api/v1/evals.py` (R2's test fails the build otherwise),
`labelled_by` is derived from the principal (`BACKLOG 4.7`), and counts travel with their
denominator. P3 still owes the selector projection and a consumer (§5).
