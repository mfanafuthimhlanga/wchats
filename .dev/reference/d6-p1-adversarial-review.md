# D6 P1 — adversarial review of the label trust tier

**Branch** `feat/d6-labelling-loop` (off `feat/d1-agent-invocation` @ `4179a5c`, **not `main`**)
**Reviewed commits** `c860780`, `8bc6f38`, `316ab9a`, `8c956f1`, `aeb949b`
**Implementer's own writeup** `.dev/reference/d6-p1-label-trust-tier.md`
**Reviewed** 2026-08-09. Persisted here per `BACKLOG 2.20` — the previous branch lost 17 of 48
review findings to a temp-directory journal that did not survive the session.

Everything below that says "observed" was run by the reviewer in this session. Everything that says
"read" was established by reading the tree. Nothing was taken from the implementer's report.

---

## 0. What was independently reproduced

| | command | observed |
|---|---|---|
| gate at HEAD | `.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` | `1962 passed, 12 skipped, 28 warnings in 506.11s (0:08:26)` — matches the implementer's `1962/12` |
| new-file count | `pytest tests/unit/test_label_provenance.py tests/unit/test_migration_tenant_0016.py --collect-only -q` | `90 tests collected` → 89 pass + 1 skip, which is exactly `1962−1873` and `12−11`. The arithmetic in the report holds. |
| guard file baseline | `pytest tests/unit/test_label_provenance.py -q` | `59 passed in 113.92s` |
| 0011 source parse | re-ran the `8c956f1` extraction | `['generated','mined','production','red_team']` — the `...` phantom is genuinely gone |
| 0016 vs 0011 technique | read both `DO $$` blocks | 0016 is a faithful structural mirror of 0011: `pg_constraint`/`pg_attribute` lookup, `conname <> <ours>`, `EXECUTE format(... %I ...)`, guarded `ADD`. **Question 3 answered: introspected, not hardcoded.** |

The ignored-new-files control was also re-run by the reviewer; result recorded in §5.

---

## 1. CRITICAL — R3 catches a *spelling*, not a *capability*, and it is the only restriction between a Celery task and the column

**This is the standing trap for the phase, realised.** A Celery task can stamp
`label_trust_tier = 'human_authored'` on an eval scenario today, and all four restrictions plus the
database CHECK stay silent.

### The mutation (mine)

Appended to `apps/api/app/worker/tasks/runtime/eval.py` — a real Celery task module, one of the
modules R2 exists to exclude:

```python
_ADV_TIER_COL = "label_trust_tier"

def _adv_forge_human_label(conn, scenario_id: str, answer: str) -> None:
    """A Celery task module stamping a human trust tier on an eval scenario."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE eval_scenarios SET reference_answer = %s, {_ADV_TIER_COL} = "
            f"'human_authored', labelled_by = 'run_eval_suite', labelled_at = NOW() "
            f"WHERE id = %s::uuid",
            (answer, scenario_id),
        )
```

Selector: `.venv/Scripts/python.exe -m pytest tests/unit/test_label_provenance.py -q`

```
BASELINE (HEAD, unmutated)
...........................................................              [100%]
59 passed in 113.92s (0:01:53)

WITH THE FORGERY IN A CELERY TASK MODULE
...........................................................              [100%]
59 passed in 72.32s (0:01:12)
EXIT=0
```

**No red. R1, R2, R3, R4 and the vocabulary tests are all silent while a task forges a human label.**

### The control that proves this is the blind spot and not a broken harness

Same file, same module, semantically identical forgery, written as one plain string constant:

```python
def _adv_forge_human_label_plain(conn, scenario_id: str, answer: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE eval_scenarios SET reference_answer = %s, label_trust_tier = "
            "'human_authored', labelled_by = 'run_eval_suite' WHERE id = %s::uuid",
            (answer, scenario_id),
        )
```

Selector: `pytest "tests/unit/test_label_provenance.py::TestR3TheModelWritersCannotWrite" -q`

```
E         Left contains 1 more item:
E         {'app\\worker\\tasks\\runtime\\eval.py': ['label_trust_tier', 'labelled_by']}
E         Use -v to get more diff

tests\unit\test_label_provenance.py:574: AssertionError
=========================== short test summary info ===========================
FAILED tests/unit/test_label_provenance.py::TestR3TheModelWritersCannotWrite::test_only_the_label_writer_writes_the_label_columns
1 failed, 3 passed in 51.83s
```

### Restored

```
git checkout HEAD -- app/worker/tasks/runtime/eval.py
git status --short   ->  (empty)
git diff HEAD --stat ->  (empty)

pytest "tests/unit/test_label_provenance.py::TestR3TheModelWritersCannotWrite" -q
....                                                                     [100%]
4 passed in 34.54s
```

### Why the other three restrictions do not cover this

- **R1** (no `tier` parameter) binds only callers of `record_human_label`. The forging task calls nothing.
- **R2** (import boundary) bans *references to `label_service`*. The forging task references neither
  the module nor the symbol.
- **R4** (runtime context guard) lives inside `record_human_label`. Never invoked.
- **0016's CHECK** admits `'human_authored'` from any writer — see §2.

R3 is therefore load-bearing alone for this route, and it is a substring scan over `ast.Constant`
nodes (`test_label_provenance.py:539-550`).

### Enumerated blind spots (probed, with controls)

`_scenario_write_statements` collects a constant only if that *single* constant contains
`INSERT INTO EVAL_SCENARIOS` / `UPDATE EVAL_SCENARIOS` after whitespace collapse, then substring-matches
the label column names inside that same constant. Probe results:

| write shape | R3 |
|---|---|
| f-string with the column interpolated | **INVISIBLE** |
| explicit `+` concatenation across constants | **INVISIBLE** |
| `INSERT INTO public.eval_scenarios` | **INVISIBLE** |
| `INSERT INTO "eval_scenarios"` | **INVISIBLE** |
| `ON CONFLICT DO UPDATE SET {col} = 'human_authored'` in an f-string | **INVISIBLE** |
| implicit adjacent-literal concatenation | CAUGHT (Python folds it into one Constant) |
| plain single constant naming the column | CAUGHT *(control)* |

The implementer's mutation proof #4 ("extended `store_scenarios`' INSERT with `label_trust_tier` in
the column list") is the *last* row of that table. The red was real; it demonstrated the guard
inside the complement of its own blind spot.

### What is actually true today

`scenario_service`'s two INSERTs (`:153`, `:292`) are plain constants and name no label column, so
the *code as it stands* satisfies R3. What is false is the stated property. Three places assert the
strong form:

- `label_service.py:33-40` — "those producers **physically cannot** populate one"
- `test_label_provenance.py:554-562` — same wording in the test docstring
- `.dev/reference/d6-p1-label-trust-tier.md` §3, R3 row — "**physically cannot** populate one"

They can. The test notices one spelling.

### Suggested fix

Make R3 structural rather than lexical. Either (a) walk `ast.JoinedStr`, `ast.BinOp(+)` and
`ast.Call(str.join/format)` and reconstruct the statement from all of its constant parts before
matching, and normalise `public.` / quoted identifiers; or better (b) invert the test: assert that
`app/worker/**` and `app/services/**` (minus `label_service`) contain **no occurrence of the three
label column names at all**, in any node — a name-level absence pin has no SQL-shape blind spot.
(b) is one line and cannot be evaded by composition. Add the f-string forgery above as a permanent
negative-control fixture.

---

## 2. HIGH — "the column's presence IS the human claim, at the database level" is false

0016's CHECK is:

```sql
CHECK (label_trust_tier IS NULL
       OR label_trust_tier IN ('human_verified', 'human_authored'))
```

It constrains the **value**, never the **author**. It refuses `'model_generated'` — the one value a
forging model or a bypassing caller would never choose — and accepts `'human_authored'` from anyone
with a connection.

The overclaim appears in three places:

- `0016_eval_scenario_label_provenance.py:69-72`: "it is the one guard in this stack that holds even
  for a caller that bypasses the service layer entirely. A raw `UPDATE eval_scenarios SET
  label_trust_tier = 'model_generated'` is refused by the database itself." — the second sentence is
  true and the first does not follow from it.
- `label_service.py` docstring, restriction 3's failure-mode paragraph.
- `.dev/reference/d6-p1-label-trust-tier.md` §2: "So `label_trust_tier IS NOT NULL` and 'a human wrote
  this answer' are the same statement, at the database level, **for any caller including one that
  bypasses the service layer entirely**."

Combined with §1 this is the whole answer to question 1: **yes, something that is not a human can
reach a human-tier write**, and the database will not stop it.

**Suggested fix:** reword all three to what the CHECK actually buys — "no *non-human* tier can be
stored, so the column has no value meaning 'a model wrote this'; who wrote a *human* value is
enforced in Python, by R1–R4, not by the database." Then §1's fix is the thing carrying the claim.

---

## 3. HIGH — `labelled_by` is caller-asserted, which is exactly the defect R1 forbids for the tier

R1's argument, verbatim from `label_service.py:184-189`: *"A caller able to name the tier is a caller
able to name `human_authored` from anywhere, which is the whole thing the hierarchy is defending
against."*

The signature that argument protects is:

```python
def record_human_label(conn, *, scenario_id: str, reference_answer: str, labelled_by: str) -> dict:
```

`labelled_by` is free text, validated only for non-emptiness (`:230-235`). The tier is not nameable;
**the human is**. The row therefore records "a string claimed a human wrote this", and nothing binds
that string to an authenticated principal — no test asserts it must, and the module docstring
presents `labelled_by` as the provenance the log line exists to carry (`:258-266`).

Equally: `reference_answer` is whatever the caller passes. An `app/api/` route that asks a model to
draft an answer and then forwards it with `labelled_by="owner@example.com"` produces a
`human_authored` row containing model prose, and **none of R1–R4 fires** — no task, no agent context,
no `label_service` import problem, no SQL scan hit. The wall authenticates the *call site*, never the
*content* or the *principal*. P2 is precisely the phase that builds that route.

**Suggested fix:** before P2 lands a route, decide that `labelled_by` is derived from the request
principal inside the handler and is never accepted from the request body, and pin it — the same
shape as R1: if it cannot be named, it cannot be forged.

---

## 4. Everything else, by severity

### MEDIUM

**M1 — R2's boundary is a module path, not a human.** The allowed region is
`os.path.join(APP_DIR, "api") + os.sep` (`test_label_provenance.py:412`). That region contains
`app/api/v1/widget.py`, whose own header (`:14-16`) records `/config` as *"no auth"* and
`/events` as *"no auth; job_id UUID4 entropy is the access token"*, with the chat routes behind a
short-lived widget JWT issued to an anonymous website visitor. It also contains `agent_chat.py` and
`query.py`. So R2 admits the entire public HTTP surface. Nothing there imports `label_service` today
(verified), so this is a claim defect rather than a live hole: `label_service.py:26-29` says the
writer is *"reachable from an authenticated HTTP request and from nowhere else in the tree"*, and the
test asserts only the second half. Note also that no test forbids a worker from importing an
`app/api/` module that re-exports the writer; today no `app/worker/**` or `app/services/**` module
imports `app.api` at all (verified by grep), so the transitive route is closed by accident, not by a pin.

**M2 — nothing reads `label_trust_tier`; the column is write-only.** `HUMAN_LABEL_TIERS`,
`LABEL_TIER_COLUMN`, `is_human_label_tier()`, `label_trust_tier()` and `is_human_labelled()` have
**zero callers** outside `eval_service` itself and the new test file (grep across `app/` and `tests/`
excluding the new files returns only two hits, both `decision_eval_service`'s unrelated constant).
The eval selector at `eval.py:767-788` selects `id, source, question, reference_answer,
retrieved_contexts, dataset` — the tier is not in the projection, so it cannot reach scoring. What
actually makes a row enter the eval is `WHERE reference_answer != ''`, and
`scenario_service.store_scenarios` writes `reference_answer` freely for `source='generated'`, whose
answers Haiku wrote. So the wall is around the *claim* and the eval still turns on the *field*. That
is P1's stated scope and not a bug — but it means none of the trust machinery is load-bearing yet,
and a reader of the reference doc would not infer that.

**M3 — namespace collision with `decision_eval_service.FIXTURE_LABEL_TRUST_TIER` (question 1's named trap).**
`decision_eval_service.py:289` defines `FIXTURE_LABEL_TRUST_TIER = "human_authored"`;
`DecisionFixture.label_trust_tier` defaults to it (`:527`); `score_decision_run()` returns it under the
key `"label_trust_tier"` (`:1772`). The new resolver reads that key from *any* dict. Observed:

```
F6  decision-eval REPORT dict     -> human_authored | is_human_labelled: True
    DecisionFixture asdict (23)   -> human_authored | is_human_labelled: True
                                     has source key: False | has reference_answer key: False
```

So `is_human_labelled()` returns `True` for objects that are not eval scenarios, carry no
`reference_answer`, and whose "human_authored" means something else entirely ("these fixtures were
hand-written"). No caller does this today — it is a confusion route, not a live defect — but
`decision_eval_service` is not walled off by R2/R3/R4, and the two meanings now share a key name with
nothing recording that they are different. **Suggested fix:** rename the decision-eval constant
(`FIXTURE_LABEL_PROVENANCE`) or have `label_trust_tier()` require a scenario-shaped mapping
(e.g. refuse a dict with no `source` and no `reference_answer` key), and add a test naming the collision.

**M4 — R4's two context detectors fail OPEN, and both fallbacks are `# pragma: no cover`.**
`_current_celery_task()` (`label_service.py:112-119`) and `_current_agent_id()` (`:128-133`) wrap the
import *and* the read in `try/except Exception` and return `None` / `""`. Any failure to *detect* the
context makes the guard silent rather than refusing. The comment justifies the import guard ("a guard
that raises on import is a guard that gets deleted"), which is reasonable, but the chosen failure
direction is open, in the one function whose whole job is to fail closed, and neither branch is
covered or coverable — `# pragma: no cover` declares them unmeasured. **Suggested fix:** distinguish
"celery is not installed in this process" (safe → None) from "celery is installed and
`get_current_task()` raised" (unsafe → raise `HumanLabelRefused`), and mutate/observe each.

**M5 — no `.dev/traces/` entry, and `BACKLOG.md` was not transacted.** `git diff --name-only
4179a5c..aeb949b -- .dev/` returns only `plans/260808-d6-labelling-loop.md`,
`reference/d6-p1-label-trust-tier.md`, `workflows/d6-labelling-loop.workflow.js`. CLAUDE.md: *"No task
is done without its trace"* and *"`BACKLOG.md` is the queue and it is maintained transactionally … a
phase that discovers work adds a row."* `.dev/traces/` shows per-phase traces are the established
pattern (`260808-d1-p2-invoke.md`, `260808-d1-p3-gate.md`). The implementer's own `not_done` says the
`_agent_id_var` leak *"wants a BACKLOG row"*; it did not get one —
`grep -niE "agent_id_var|contextvar|build_tool_server" .dev/BACKLOG.md` returns nothing. Two rows are
owed: the ContextVar leak, and (from §1) the R3 blind spot.

### LOW

**L1 — the fixture ban covers 2 files out of 159.**
`test_no_conftest_fixture_may_import_the_human_label_writer` filters on
`os.path.basename(path) != "conftest.py"`. There are **2** conftest.py files and **157** other test
modules, 16 of which already define `@pytest.fixture`. A fixture in any of them may call
`record_human_label` freely — R4 is silent in a unit test (no task, no agent context). The test
docstring is accurate; the reference doc's R2 summary ("no conftest fixture") is read as "no fixture".

**L2 — R2's detector is blind to composed-path routes, and its five-arm vacuity self-test only
exercises honest spellings.** Probed invisible (controls in brackets all SEEN):
`getattr(getattr(s, "label" + "_service"), "record_human_label")`;
`importlib.import_module("app.services." + "label_service")`;
`importlib.import_module("app.services.%s" % "label_service")`;
`sys.modules["app.services." + "label_service"]`.
[SEEN: plain from-import; re-export through `app/api`; aliased from-import.] The detector docstring
claims it covers *"string constants that spell the module's import path (the importlib back door)"* —
it covers the single-literal spelling only. `test_the_boundary_detector_sees_every_route_to_the_writer`
enumerates five routes, all of them honest; the vacuity self-test therefore also lives inside the
detector's non-blind region. Lower than §1 because composing a module path is a deliberate act,
whereas f-string SQL is how people ordinarily write SQL.

**L3 — R2/R3 scan `app/` only.** `_python_files(APP_DIR)`. Not scanned: `scripts/` (1 file),
`_runlogs/` (2 files — `_runlogs/run_eval_prod.py:27` already queries `eval_scenarios` and per its name
has been pointed at production), `alembic/`, `alembic_tenant/`. A `_runlogs/` script that stamps a
human tier sits outside every restriction.

**L4 — `is_human_labelled()` is `True` for a human tier with an empty `reference_answer`.** Observed:
`{'source':'mined','reference_answer':'','label_trust_tier':'human_authored'}` →
`label_trust_tier: human_authored | is_human_labelled: True`. Nothing at the DB level ties
`label_trust_tier IS NOT NULL` to `reference_answer <> ''`. `record_human_label` refuses an empty
answer so no shipped path creates that row — but a downgrade/re-upgrade, a partial restore or any
direct write can, and the resolver then asserts a human authored an empty string.
`CHECK (label_trust_tier IS NULL OR reference_answer <> '')` was available in 0016 at zero cost and
was not taken.

**L5 — 0016's catalog introspection is not schema-qualified.** The `DO $$` block filters on
`rel.relname = 'eval_scenarios'` with no `pg_namespace` join, and the
`EXECUTE format('ALTER TABLE eval_scenarios DROP CONSTRAINT %I', con_name)` target is unqualified too.
If a tenant DB ever carries `eval_scenarios` in more than one schema, the name is discovered from one
table and the DROP applied to whichever `search_path` resolves. **0011 has the identical shape**
(verified: no `nspname` anywhere in its DO block), so this is inherited rather than introduced — the
claim "mirrors 0011's technique" is exactly right, gap included. Unobservable here: no migration can run.

**L6 — the 0015 head assertion was weakened inside the feature commit.** `c860780` changed
`heads == {"0015"}` → `len(heads) == 1`. The reasoning is sound and disclosed, and 0013/0014 use the
same form. Two notes: it landed in the feature commit rather than a separate `test(migration):` commit
like the implementer's own three fixes; and the ignored-new-files control confirms that test still
runs and still passes — it cannot see that an assertion got weaker, so *"Test count unchanged, which
the ignored-new-files control confirms"* is a slightly stronger sentence than the control supports.
Net effect: no test now pins which revision is the tenant head, only that there is one.

**L7 (nit) — `test_the_human_write_does_not_touch_the_questions_origin` asserts `"source" not in joined`**
as a raw substring over the UPDATE SQL. It passes today, but it will fire on any future column or alias
containing the substring (`resource_id`), which teaches the next author to edit the test rather than obey it.

---

## 5. The ignored-new-files control, re-run by the reviewer — REPRODUCED

```
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py \
  --ignore=tests/unit/test_label_provenance.py \
  --ignore=tests/unit/test_migration_tenant_0016.py

1873 passed, 11 skipped, 30 warnings in 456.72s (0:07:36)
```

Exactly the observed baseline at `4179a5c` (1873/11), and exactly the implementer's reported control.
`BACKLOG 1.3`'s flake did **not** recur on this run. Together with the gate at HEAD
(`1962 passed, 12 skipped`, reproduced) and the 90 tests collected from the two new files, the delta
is confirmed as exactly the two new test files: **+89 passed, +1 skipped**, no pre-existing test
changed status.

Caveat worth stating: the control proves no pre-existing test changed *status*. It cannot see a
pre-existing assertion getting *weaker* — which is precisely what happened to
`test_migration_tenant_0015.py` in `c860780` (L6). The control's blind spot and that edit intersect.

---

## 6. Claims from the report that did NOT survive

| claim | status |
|---|---|
| "No model may write at a human tier — four independent structural restrictions" | **Refuted for the direct-SQL route.** A Celery task writing an f-string UPDATE is invisible to all four (§1, observed). |
| R3: model-driven producers "physically cannot populate one" | **Refuted** (§1). They can; the test notices one spelling. |
| "`label_trust_tier IS NOT NULL` and 'a human wrote this' are the same statement at the database level, for any caller" | **Refuted** (§2). The CHECK constrains the value, not the author. |
| "the writer is reachable from an authenticated HTTP request and from nowhere else in the tree" | **Overstated** (M1). The test asserts module paths; `app/api/` includes unauthenticated widget routes. |
| "no conftest fixture may" (reported as R2 covering fixtures) | **Narrower than it reads** (L1): 2 of 159 test modules. |
| R2's detector "sees every route to the writer" | **Overstated** (L2): four composed-path routes invisible; the self-test's five arms are all honest spellings. |

## 7. Claims that DID survive

- The tier is genuinely carried by the label, not the source — `label_trust_tier()` returns
  `human_authored` for `{source:'mined', label_trust_tier:'human_authored'}` while
  `scenario_trust_tier('mined')` still returns `customer_negative` and `row['source']` is untouched.
  `record_human_label`'s UPDATE names neither `source` nor `dataset`. **Question 2: not collapsed.**
- The NULL fallback cannot manufacture a human claim: the 0011 parse yields exactly
  `['generated','mined','production','red_team']` and none maps to a human tier.
- A CHECK-forbidden value fails closed to `unknown` (rank −1, below `model_generated`), not to the
  source's tier.
- **Question 3: the constraint name is discovered, not hardcoded** — `pg_constraint`/`pg_attribute` +
  `EXECUTE format(%I)`, `conname <> <ours>`, guarded `ADD`; structurally identical to 0011.
- 0011's `source` CHECK is untouched and no source value was added. The deviation from the literal
  instruction is correct and its reasoning is right: widening `source` would have re-collapsed origin
  into label *and* opened `is_promotable_to_verified_qa`.
- P1 opened no customer-facing door: `is_promotable_to_verified_qa` still gates on `source`,
  `select_promotion_candidates` still refuses a top-scoring human-labelled row,
  `VERIFIED_QA_PROMOTION_DECISION["enabled"]` is still `False`.
- The migration was **not applied** and cannot be. There is no PostgreSQL on this machine; the
  integration roundtrip skips and a skip is unobserved. The report says so plainly and does not imply
  otherwise. The 0016 unit tests are correctly written as constraints on what the migration is
  *allowed to contain*.
- The three self-caught weak tests (`8bc6f38`, `316ab9a`, `8c956f1`) are real fixes, honestly
  reported, and the `8c956f1` parse fix verifiably works.

## 8. Answers to the four questions

1. **Can anything that is not a human reach the human_authored write path?** **Yes** — any module in
   `app/worker/**` or `app/services/**` can write the column directly with composed SQL and no test
   fires (§1, observed red/green). The database accepts it (§2). Additionally, even *through*
   `record_human_label`, the "human" is a caller-supplied string (§3). `FIXTURE_LABEL_TRUST_TIER` is
   not a write route but is a live confusion route (M3, observed).
2. **Is label provenance separate from source provenance?** **Yes, genuinely.** This is the part of
   P1 that is well built (§7).
3. **Does the migration introspect the constraint name?** **Yes**, faithfully mirroring 0011,
   including 0011's missing schema qualification (L5).
4. **Is the guard structural or one spelling?** **R1 and R4 are structural. R2 is structural for
   honest spellings and lexical otherwise (L2). R3 is purely lexical (§1) — and R3 is the only one
   covering the direct-SQL route.**
