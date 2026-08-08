# D6 P1 — the label trust tier, and the wall around it

**Branch** `feat/d6-labelling-loop` (off `feat/d1-agent-invocation` @ `4179a5c`, **not `main`**)
**Commits** `c860780`, `8bc6f38`, `316ab9a`, `8c956f1`
**Contract** `.dev/plans/260808-d6-labelling-loop.md` § P1
**Written** 2026-08-08. Persisted here per `BACKLOG 2.20` — the previous branch lost 17 of 48
review findings to a temp-directory journal that did not survive the session.

---

## 1. The defect P1 closes

`eval_service.LABEL_TRUST_TIERS` has declared five tiers since D5. Two of them —
`human_verified` (2) and `human_authored` (3) — **nothing in the system could produce**, because
the only tier resolver was `SCENARIO_SOURCE_TRUST_TIER`, which maps all four schema-allowed
scenario sources to `model_generated` or `customer_negative`.

Consequences, both live before this change:

| | waiting on a tier nothing could produce |
|---|---|
| the eval | mined production failures, owner-filed failing traces and contained red-team findings are all stored with `reference_answer=''` and never scored |
| `verified_qa` | `VERIFIED_QA_MIN_TRUST_TIER = "human_verified"`, so `promotable_answer` could never clear the gate — the customer-facing verified-answer path is dead code |

## 2. The design decision that shapes everything else

**The tier is carried by the LABEL, not inferred from the question's origin.**

`eval_scenarios.source` answers *where did this QUESTION come from*. A mined production failure the
owner then answers by hand is `customer_negative` in origin and `human_authored` in label,
simultaneously, and both statements are true. Fusing them into one column produces a column that
means two things, which then gets read as whichever one the reader had in mind — the exact failure
`promotable_answer`'s docstring already warns about.

So: three new nullable columns on `eval_scenarios`, and `source` keeps meaning what it meant.

### The column's presence *is* the human claim

`label_trust_tier` may hold **only NULL or one of the two human tiers** — enforced by 0016's named
CHECK. There is deliberately no value meaning "a model wrote this label", because a model's label
records no claim at all; NULL says that. So `label_trust_tier IS NOT NULL` and "a human wrote this
answer" are the same statement, at the database level, for any caller including one that bypasses
the service layer entirely.

### The fallback direction, and why it is safe

`eval_service.label_trust_tier(scenario)`:

| row state | resolves to | why |
|---|---|---|
| column holds a human tier | that tier | the case the column exists for; label outranks origin |
| column NULL / absent | `scenario_trust_tier(source)` | every row today, and every model-written row |
| column holds anything else | `"unknown"` (rank −1, below `model_generated`) | the CHECK forbids it, so the value arrived by bypassing both the service layer and the constraint |

The middle row is a **downgrade path only**, and that is load-bearing: it is sound exactly while no
schema-allowed source can resolve to a human tier. Pinned by
`test_no_schema_allowed_source_can_produce_a_human_label_tier`, parametrised over the source list
**parsed out of migration 0011**.

The third row matters more than it looks. Laundering a corrupt value back to the source's tier
turns "provenance nobody can account for" into "plausible provenance"; mutation proof 7 shows the
laundering version passing every other test in the class.

## 3. NO MODEL MAY EVER WRITE AT A HUMAN TIER — four restrictions

A trust tier is a claim about *who wrote a string*, and it is worth exactly the difficulty of
forging it. Four independent restrictions, each separately pinned, so removing any one turns a test
red rather than quietly halving the wall.

| | restriction | pinned by | mutation proof |
|---|---|---|---|
| **R1** | the writer has **no tier parameter** — the tier is a module constant it stamps, not something a caller can name | `TestR1NoTierParameter` | #1 |
| **R2** | **only `app/api/`** may reference `label_service` — nothing under `app/worker/` (every Celery task), nothing else under `app/services/` (every agent tool, judge, scenario producer, and `eval_service`, which the tasks import), no conftest fixture | `TestR2ImportBoundary` | #2, #3 |
| **R3** | the **model-driven writers cannot write the columns** — `store_scenarios` and `insert_provenance_scenario` are the only `INSERT INTO eval_scenarios` paths and neither names a label column, so generated suites, mined failures, promoted traces and contained red-team findings physically cannot populate one | `TestR3TheModelWritersCannotWrite` | #4 |
| **R4** | the writer **refuses at runtime** inside a Celery task or an agent tool context, before it opens a cursor | `TestR4RuntimeContextGuard` | #5, #6 |

### R4's known hole, stated rather than papered over

R4 is thread-local. Celery's current-task stack lives in `celery.utils.threads._LocalStack`, and
`agent_tools`' ContextVars do not propagate into `run_in_executor` threads (`agent_tools.py:161`).
A bare thread spawned inside a task would see neither. That is why R4 is the last line and not the
only one — **R2 and R3 do not depend on which thread is asking.**

The same thread-locality has a second consequence, found while running the gate (§6.1): once an
agent turn has run in a worker thread, `_agent_id_var` stays set, so R4's agent arm cannot
distinguish "an agent is driving this call" from "an agent drove a call in this thread earlier".
**That direction is fail-closed** — it refuses more, never less — and the API process, which is the
only place R2 permits the writer to be called from, never runs agent turns.

## 4. What was deliberately NOT done, and why

### 4.1 0011's `source` CHECK was not widened — a deviation from the literal instruction

The task said *"Widen 0011's CHECK the way 0011 itself did"*. **I applied 0011's technique to the
new `label_trust_tier` constraint and left the `source` CHECK untouched.** Two reasons, either
sufficient:

1. Adding a human-flavoured source (`owner_authored`) **re-collapses origin into label** — the
   precise defect the new column exists to separate, reintroduced through the one change that reads
   as obviously correct.
2. `is_promotable_to_verified_qa()` gates on `source`. A schema-allowed source resolving to a human
   tier makes it return `True`, **opening the `verified_qa` write that
   `retrieval_service.verified_qa_lookup` serves to real customers ahead of hybrid search** — which
   the owner settled eval-only on 2026-08-08. It would also have escaped
   `test_no_schema_allowed_source_is_promotable`, which parses migration **0011 only**.

Both are pinned as absence tests (`test_the_source_check_is_not_touched`,
`test_the_new_source_values_are_not_snuck_in_as_scenario_sources`, mutation proof #9) and the
reasoning is written into 0016's docstring under *"What this migration deliberately does NOT do"*.

**If the owner wants `source='owner_authored'` anyway, it is additive** — but it must land together
with re-pointing the verified_qa gate at the *label* tier and requiring an explicit human label
rather than an inferred one. That is P3 work, not P1.

### 4.2 A CHECK constraint, where 0014 and 0015 banned one

0014's docstring argues against CHECK constraints, citing 0011's archaeology. The lesson is about
**unnamed inline** CHECKs on a column live INSERTs already write. 0016's is explicitly named
(`eval_scenarios_label_trust_tier_check_v1`), on a brand-new column no existing row can violate and
no existing INSERT names, and discovered-not-assumed on re-run. Argued in the migration docstring
and in `test_the_only_check_is_on_the_new_column`.

### 4.3 No API route, no selector change

P1 is migration plus vocabulary plus the write path. `GET` unlabelled / `POST` a label is P2; what
a label does downstream is P3. The eval selector (`eval.py:768-789`) is untouched — a labelled row
becomes eligible to it with no change to it, which is the plan's stated intent.

---

## 5. Files

| path | |
|---|---|
| `apps/api/alembic_tenant/versions/0016_eval_scenario_label_provenance.py` | new — three nullable columns + the named CHECK |
| `apps/api/app/services/label_service.py` | new — the one human-tier write, and the four restrictions |
| `apps/api/app/services/eval_service.py` | `HUMAN_LABEL_TIERS`, `LABEL_TIER_COLUMN`, `is_human_label_tier()`, `label_trust_tier()`, `is_human_labelled()` |
| `apps/api/tests/unit/test_label_provenance.py` | new — vocabulary, R1–R4, the write, the absence pins |
| `apps/api/tests/unit/test_migration_tenant_0016.py` | new |
| `apps/api/tests/unit/test_migration_tenant_0015.py` | head assertion relaxed `heads == {"0015"}` → `len(heads) == 1`, matching 0013/0014 |

## 6. Gate observations — all run, all observed

```
gate at HEAD (8c956f1)
  .venv/Scripts/python.exe -m pytest tests/unit -q \
    --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py
  1962 passed, 12 skipped, 28 warnings in 394.57s (0:06:34)

ignored-new-files control (BACKLOG 2.26) — the same command, plus
    --ignore=tests/unit/test_label_provenance.py
    --ignore=tests/unit/test_migration_tenant_0016.py
  1873 passed, 11 skipped, 30 warnings in 433.60s (0:07:13)
```

The control reads **exactly the observed baseline at `4179a5c`** — 1873 passed, 11 skipped. The
delta is exactly the two new files: +89 tests, +1 skip (0016's integration roundtrip, which cannot
run here).

### 6.1 The control's first run caught a failure — the known flake, not a regression

```
1 failed, 1872 passed, 11 skipped, 30 warnings in 491.78s (0:08:11)
FAILED tests/unit/test_services.py::TestWaitForNeonReady::test_wait_for_neon_ready_retries_then_succeeds
```

That is **`BACKLOG 1.3`** — "failed 1 in 11 identical runs", diagnosed as patching the shared `time`
module attribute while Langfuse daemon threads run. Evidence it is not mine: the run that *included*
my files passed it, the run that excluded them failed it, it passes in isolation
(`3 passed in 0.66s`), and it did not recur on the second control run. **I did not capture a
traceback**, so BACKLOG 1.3's stated next step — confirm which assertion fails — is still open.

---

## 7. Mutation proofs

Every guard mutated, run, observed red; restored with `git checkout HEAD -- <path>`
unconditionally; run again, observed green. Verbatim tails below.

### #1 — R1, the writer has no tier parameter
Selector `tests/unit/test_label_provenance.py::TestR1NoTierParameter`
Mutation: added `tier: str = HUMAN_AUTHORED_TIER` to `record_human_label`'s signature.
```
RED    FAILED ...::TestR1NoTierParameter::test_the_writer_has_no_tier_parameter
       1 failed, 2 passed in 31.70s
GREEN  3 passed in 22.15s
```

### #2 — R2, the import boundary
Selector `tests/unit/test_label_provenance.py::TestR2ImportBoundary`
Mutation: `from app.services.label_service import record_human_label` added to
`app/worker/tasks/runtime/eval.py`.
```
RED    FAILED ...::test_no_model_driven_module_may_import_the_human_label_writer
       1 failed, 3 passed in 4.37s
GREEN  4 passed in 3.36s
```

### #3 — R2's detector is not vacuous *(this one failed first, see §8.1)*
Selector `tests/unit/test_label_provenance.py::TestR2ImportBoundary`
Mutation: misspelled the detector's watched-name set (`labell_service`, `recordd_human_label`).
```
FIRST ATTEMPT, before the test was strengthened:
       4 passed in 3.79s          <-- NO RED. The wall's detector had four arms nobody
                                      had ever seen fire.
AFTER 8bc6f38:
RED    FAILED ...::test_the_boundary_detector_sees_every_route_to_the_writer[from-import of the module-...]
       FAILED ...::test_the_boundary_detector_sees_every_route_to_the_writer[attribute call with no import in the file-...]
       2 failed, 8 passed in 5.56s
GREEN  10 passed in 5.23s
```

### #4 — R3, the model-driven writers cannot write the label columns
Selector `tests/unit/test_label_provenance.py::TestR3TheModelWritersCannotWrite`
Mutation: `store_scenarios`' INSERT extended with `label_trust_tier` / `'human_authored'`.
```
RED    FAILED ...::test_only_the_label_writer_writes_the_label_columns
       FAILED ...::test_the_scenario_service_insert_paths_name_no_label_column
       2 failed, 2 passed in 40.83s
GREEN  4 passed in 24.58s
```

### #5 — R4, the Celery-task arm
Selector `tests/unit/test_label_provenance.py::TestR4RuntimeContextGuard`
Mutation: `if task is not None:` → `if False:`.
```
RED    ---------------------------- Captured stdout call -----------------------------
       2026-08-08 23:08:53 [info     ] label_service.human_label_recorded
         label_trust_tier=human_authored labelled_by=owner@example.com rows_updated=1
         scenario_id=11111111-1111-1111-1111-111111111111
       FAILED ...::test_a_celery_task_context_refuses_the_human_label
       FAILED ...::test_a_task_context_refuses_even_with_a_perfectly_valid_label
       2 failed, 3 passed in 22.97s
GREEN  5 passed in 19.26s
```
The captured stdout is the point: with that one arm removed, a Celery task stamped
`human_authored` successfully.

### #6 — R4, the agent-tool arm
Selector `tests/unit/test_label_provenance.py::TestR4RuntimeContextGuard`
Mutation: `if agent_id:` → `if False:`.
```
RED    E       AssertionError: the guard let the call reach the database before refusing
       FAILED ...::test_an_agent_tool_context_refuses_the_human_label
       1 failed, 4 passed in 20.15s
GREEN  5 passed in 18.35s
```

### #7 — a CHECK-forbidden tier value fails closed
Selector `tests/unit/test_label_provenance.py::TestLabelTierVocabulary`
Mutation: `return "unknown"` → `return scenario_trust_tier(scenario.get("source"))`.
```
RED    FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[model_generated]
       FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[customer_negative]
       FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[unknown]
       FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[HUMAN_AUTHORED]
       FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[human]
       FAILED ...::test_a_value_the_check_forbids_fails_closed_to_unknown[7]
       6 failed, 10 passed in 20.19s
GREEN  16 passed in 32.35s
```

### #8 — the migration's columns are nullable with no DEFAULT *(this one failed first, see §8.2)*
Selector `tests/unit/test_migration_tenant_0016.py`
Mutation: `label_trust_tier TEXT` → `label_trust_tier TEXT` + newline + `NOT NULL DEFAULT
'human_authored'`.
```
FIRST ATTEMPT, before the test was strengthened:
RED    FAILED ...::test_upgrade_is_strictly_additive_and_nullable[DEFAULT]
       1 failed, 29 passed, 1 skipped in 17.96s
       <-- only the blanket DEFAULT ban fired. The per-column nullability test had a
           line-boundary blind spot and would have missed a bare NOT NULL entirely.
AFTER 316ab9a:
RED    FAILED ...::test_every_added_column_is_the_bare_alter_and_nothing_else
       FAILED ...::test_upgrade_is_strictly_additive_and_nullable[DEFAULT]
       2 failed, 28 passed, 1 skipped in 28.17s
GREEN  30 passed, 1 skipped in 23.19s
```

### #9 — 0011's `source` CHECK is not touched
Selector `tests/unit/test_migration_tenant_0016.py`
Mutation: 0016 given a `DROP CONSTRAINT eval_scenarios_source_check_v2` + a v3 CHECK adding
`'owner_authored'`.
```
RED    FAILED ...::test_the_only_check_is_on_the_new_column
       FAILED ...::test_the_constraint_name_is_discovered_not_assumed
       FAILED ...::test_the_source_check_is_not_touched
       FAILED ...::test_the_new_source_values_are_not_snuck_in_as_scenario_sources
       4 failed, 26 passed, 1 skipped in 18.99s
GREEN  30 passed, 1 skipped in 15.14s
```

### #10 — P1 opened no customer-facing door
Selector `tests/unit/test_label_provenance.py::TestP1OpenedNoCustomerFacingDoor`
Mutation: `is_promotable_to_verified_qa` → `return True`.
```
RED    FAILED ...::test_a_human_labelled_scenario_is_still_not_promoted_to_verified_qa
       FAILED ...::test_no_source_became_promotable[generated]
       FAILED ...::test_no_source_became_promotable[mined]
       FAILED ...::test_no_source_became_promotable[production]
       FAILED ...::test_no_source_became_promotable[red_team]
       5 failed, 2 passed in 36.65s
GREEN  7 passed in 21.36s
```

---

## 8. Incidental findings — three tests that were weaker than they read

These are the mutation exercise earning its keep. All three were **green before the mutation and
green after**, i.e. they read as verification and were not.

### 8.1 The import-boundary detector had four arms nobody had seen fire (`8bc6f38`)
`_references_label_writer` catches five routes to the writer. Misspelling the `watched` set —
which covers `from app.services import label_service`, a bare
`label_service.record_human_label(...)` attribute call, and a from-import of the symbol — left the
whole class green, because every reference the vacuity check had ever been shown arrived through
the *module-path* arm. Each route now has its own synthetic file and its own assertion, plus a
negative control (reading `label_trust_tier` must not fire, or the next author weakens the detector
rather than obeying it).

### 8.2 The migration nullability test matched only to end-of-line (`316ab9a`)
`re.findall(r"ADD COLUMN IF NOT EXISTS \w+ ([^\n]*)")` captures the remainder of the *same line*, so
a `NOT NULL DEFAULT 'human_authored'` wrapped onto the next line sailed through. Only the separate
blanket `DEFAULT` ban caught it — and **a bare `NOT NULL` with no DEFAULT would have passed both**,
which fails the ALTER outright on any tenant whose `eval_scenarios` has rows. Now read out of the
SQL string literals with `ast` and compared for **equality** against the three bare ALTERs; an
equality has no blind spot for a clause on the next line.

### 8.3 My source-list parser invented a scenario source (`8c956f1`)
I copied `_schema_allowed_scenario_sources()` from `test_eval_service.py` but split the CHECK clause
on commas instead of extracting quoted literals. Migration 0011's *docstring* quotes the shape of
the constraint it replaces — `CHECK (source IN (...))` — and the clause regex matches prose as
happily as SQL, so `...` became a fifth "schema-allowed source" across five parametrised cases. It
was harmless (not promotable, no human tier, every assertion held), which is exactly why it would
have sat there indefinitely inflating what those tests looked like they covered.

### 8.4 An agent ContextVar leaks across the whole pytest process — **not fixed, needs a BACKLOG row**
`agent_tools.build_tool_server()` sets `_agent_id_var` and never clears it (correctly — it is
setting up a turn). `tests/unit/test_agent_tools.py:686` calls it with `agent_id='agent-reset-test'`,
so **that value is live for every subsequent test in the process**. My R4 tests passed in isolation
and failed in the full suite until I gave the module an autouse fixture that establishes its own
precondition.

This is a live hygiene defect in the suite, not something my fixture fixes: any future test that
reads `_agent_id_var` gets a stale agent id, and `agent_tools.py:152` claims ContextVars exist to
prevent exactly this bleed. I did not touch `test_agent_tools.py` — out of P1's scope and it would
have churned a file the control is meant to hold still. **Proposed BACKLOG row:** *tests calling
`build_tool_server` must run it inside `contextvars.copy_context()`, or reset the vars they set.*

---

## 9. What cannot be proven here — stated plainly

- **Migration 0016 has never been applied.** There is no PostgreSQL server on this machine; every
  `-m integration` harness skips and **a skip is unobserved, never a pass**. No `ALTER TABLE` in
  0016 has executed against any database. `CONTROL_DB_URL` points at live Neon production and was
  not used. The source-level assertions in `test_migration_tenant_0016.py` are the only evidence
  that exists for it, which is why they are written as constraints on what the migration is
  *allowed to contain* rather than as checks on what it did.
- **The CHECK constraint has never rejected anything.** `test_migration_tenant_0016_db_roundtrip` is
  written out in full — including the `model_generated` rejection and the `human_authored`
  acceptance — and it **skips**.
- **No real `eval_scenarios` row has ever been labelled.** `record_human_label` has only ever run
  against a recording cursor.
- **R4 has never been exercised in a real Celery worker.** It was exercised against Celery's real
  `_state` task stack in-process, which is the same stack a worker sets, but that is not the same as
  a worker.
- Standing debt shared with `BACKLOG 0.2`, `2.14`, `3.5`.

## 10. Hand-off to P2 / P3

1. **The queue write goes in `app/api/v1/evals.py`** — that is the only tree R2 permits to import
   `label_service`, and R2's test will fail the build if P2 puts it anywhere else.
2. **`label_trust_tier(scenario)` takes the whole row**, deliberately, so a caller cannot pass the
   source where the label tier belongs. P2's `GET` should select the three new columns and P3's
   readers should call this rather than reading `scenario["label_trust_tier"]` raw — the raw read
   skips the fail-closed branch.
3. **P3 owes a decision on the verified_qa gate.** `is_promotable_to_verified_qa(source)` still
   reasons about origin, which is now demonstrably the wrong axis. If it is ever re-pointed at the
   label, it must require an **explicit** human label and never the origin fallback — otherwise
   §4.1's second failure mode returns by a different door.
4. **`VERIFIED_QA_PROMOTION_DECISION`'s `reason` string is now partly stale.** It says "no row is
   promotable until a correction UI produces human-verified answers". A human-*authored* label is
   now producible; what is still true is that no *source* clears the gate. P3 should reword it as it
   records the disablement on the run.
5. **Only `human_authored` is produced.** 0016's CHECK admits `human_verified` too, so adding the
   "a human confirmed a drafted candidate" act later is a code change, not a migration.
6. **P2's counts must travel with their denominator** — `(unlabelled, labelled, eligible)` as
   counts, never a rate. `record_human_label` returns `rows_updated` rather than raising on a
   missing row for exactly this reason.
