# D6 P2 — the labelling queue: the finding, the design, and what was actually observed

**Branch** `feat/d6-labelling-loop`, off `feat/d1-agent-invocation` (`4179a5c`), **not `main`**
**Plan** `.dev/plans/260808-d6-labelling-loop.md` § P2
**Trace** `.dev/traces/260809-d6-p2-labelling-queue.md`
**Persisted here per `BACKLOG 2.20`** — the previous branch lost 17 of 48 findings to a temp-directory
journal that did not survive the session.

---

## 1. THE HEADLINE FINDING: judge confidence is not joinable to a scenario

The plan (§P2) and the task brief both ask for **uncertainty ordering**, citing `BACKLOG 6.4`:
`validators.py` emits judge `confidence` into `job_events` and it is discarded for ranking. The
instruction included an escape clause — *"If the confidence signal turns out not to be joinable to a
scenario, say so plainly and order by something you CAN defend."*

**It is not joinable.** Three independent reasons, each traced to a line rather than asserted:

### 1.1 It is in a different database

`validators.py` builds its session with `get_sync_db()` (`app/worker/tasks/runtime/validators.py:37`,
used at `:154`) and passes that session to `emit(job_id, "gatekeeper.complete", {...}, db, _redis)`
(`:203-209`). `emit` (`app/services/events.py:36`) inserts one `job_events` row through that session.
`get_sync_db` is the **control DB**.

`eval_scenarios` is a **tenant** table — `alembic_tenant/versions/0005_verified_qa_eval_scenarios.py`
creates it, and every reader in `app/api/v1/evals.py` reaches it through
`fernet_decrypt(agent.neon_connection_string)`.

Per CLAUDE.md project rule 9 these are **per-tenant Neon projects**, not schemas in one database. No
SQL statement can join across them. This alone rules out an `ORDER BY confidence`.

### 1.2 There is no join key, so application-side correlation does not rescue it

`scenario_service.store_scenarios` (`app/services/scenario_service.py:151-167`) inserts exactly:

```
(id, source, question, reference_answer, retrieved_contexts, scenario_category, created_at)
```

No `job_id`. No `conversation_id`. Not even `origin_trace_id` — the column 0011 added and that
`insert_provenance_scenario` populates for the promote and red-team paths is simply absent from the
mined path's INSERT.

And upstream of that, `mine_production_scenarios` (`:387-399`) selects:

```sql
SELECT DISTINCT je.job_id, je.payload->>'verdict' ...
```

`payload->>'confidence'` is **discarded at the point the event is read**. The number never reaches
the dict that becomes the row. The only field the two sides share is `question` free text — and the
job_events payload does not carry the question either (the function's own docstring says so; it
recovers the question from tenant `messages` via `jobs.conversation_id`).

### 1.3 The one tenant-side confidence column is the wrong population

`verified_qa_candidates.auditor_confidence` (`alembic_tenant/0004`, `FLOAT NOT NULL`) is the only
confidence value persisted in a tenant DB. `run_auditor` writes it only under
`if verdict.verdict == "grounded" and verdict.confidence >= threshold`
(`validators.py:368-376`) — i.e. only for **grounded** turns above threshold. The queue's population
is the complement: `mine_production_scenarios` filters
`payload->>'verdict' IN ('fail','ungrounded','partial')`. **The confidence attached to a failed
judgement is never persisted tenant-side at all.**

### 1.4 What this costs, so `6.4` is no longer under-priced

Making it joinable requires: a key (and ideally the confidence itself) carried onto the scenario row
at mining time, a tenant migration for it, and a change to `mine_production_scenarios`. Even then it
is **retroactively empty for every row already mined**. `BACKLOG 6.4` has been re-scoped from "wire
up an `ORDER BY`" to that, in this commit.

---

## 2. WHAT THE QUEUE IS ORDERED BY INSTEAD — and it is not a disguised proxy

```sql
ORDER BY array_position(%(source_priority)s::text[], source) ASC NULLS LAST,
         created_at ASC,
         id ASC
```

- **origin trust tier, descending.** `_source_priority_order()` sorts `SCENARIO_SOURCE_TRUST_TIER` by
  `trust_tier_rank(scenario_trust_tier(source))` — `eval_service`'s own tables, never restated. It
  yields `['mined', 'production', 'red_team', 'generated']`: the three `customer_negative` sources
  (a question a real customer asked and the agent got wrong) ahead of `model_generated` (a generated
  row that came out without an answer). A source in 0011's CHECK but absent from the tier table
  drops out of the array, `array_position` returns NULL, and `NULLS LAST` sorts it **last** — an
  unclassified origin is never promoted to the front of the owner's queue.
- **`created_at ASC` — oldest first.** The plan's one explicit prohibition is recency; this is its
  opposite, not a dressed-up version of it. The oldest unlabelled row has been unmeasurable the
  longest, and newest-first starves the tail of the queue permanently.
- **`id ASC`** makes it a **total** order. Without it, two rows sharing a source and a `created_at`
  have no defined relative position and `LIMIT/OFFSET` paging can show one row twice and never show
  another.

**The honesty is on the wire, not only in a comment.** Every queue response carries:

```json
"ordering": {"by_uncertainty": false, "keys": [...], "reason": "..."}
```

so a console cannot render this as "the rows the judges were least sure about". `QUEUE_ORDERING` is
copied at the use site (`dict(QUEUE_ORDERING)`), matching `eval_service.VERIFIED_QA_PROMOTION_DECISION`.

---

## 3. THE SELECTOR IS UNTOUCHED, AND THE PIN IS CROSS-MODULE

`SELECTOR_ELIGIBILITY_PREDICATE = "reference_answer != ''"` is spelled once in `evals.py`. All three
queue statements use `NOT (...)` or `(...)` of that constant, and
`test_the_queue_selects_exactly_what_the_eval_selector_excludes` reads the constant back out of
`inspect.getsource(run_eval_suite)`. If the task ever stops filtering on it, this queue's
"unlabelled" silently stops meaning "will never be scored" — and that test is what makes it audible.
`test_the_scenario_is_inert_to_the_eval_selector_by_construction` in `test_promote_trace.py` is
untouched and still green.

`counts.eligible == counts.labelled`, and that identity **is** the P2 claim rather than a redundancy:
writing an answer is the whole of what makes a row eligible, so the selector needs no change.
Reporting both names lets a reader check it from the payload instead of taking it on trust.
`eligible` is *"the selector will consider it"*, not *"it will be scored tonight"* — the exploratory
half of a run is a sample of at most `EXPLORATORY_SAMPLE_SIZE` rows.

---

## 4. THE COUNTS, AND THEIR DENOMINATOR

```json
"counts": {"total": N, "unlabelled": U, "labelled": L, "eligible": L,
           "human_labelled": H|null, "label_provenance_available": bool}
```

- `total` is the denominator. A rate must not be constructible from this response without it.
- `unlabelled` is written as `COUNT(*) FILTER (WHERE NOT (<selector predicate>))` — the **negation of
  the selector's own predicate**, not a separately hand-written `= ''`. So `unlabelled + labelled ==
  total` is an identity of the SQL rather than two hand-written conditions agreeing by luck.
  (`reference_answer` has been `NOT NULL` since 0005, so no row falls into a third bucket.)
- **`human_labelled` is `null`, never `0`, when the column does not exist.** Migration 0016 has been
  applied to **no** database, so `label_trust_tier` exists nowhere yet; the counts query raises
  `UndefinedColumn`, falls back, and reports `null` beside `label_provenance_available: false`. Zero
  would assert "no human has labelled anything", which is a measurement this route did not make. Same
  shape `datasets.available` already uses for the pre-0014 `dataset` column.

---

## 5. THE WRITE, AND WHAT IT REFUSES TO BE TOLD

`POST /agents/{agent_id}/eval-scenarios/{scenario_id}/label`

**Closes `BACKLOG 4.7`'s pin.** P1's `label_service` docstring settled that `labelled_by` is derived
from the authenticated principal and never read from a body, and recorded that nothing could pin it
because the route did not exist. It exists now:

- `ScenarioLabelRequest` is `model_config = ConfigDict(extra="forbid")` with **exactly one field**,
  `reference_answer`. A body carrying `labelled_by`, `label_trust_tier`, `labelled_at`, `tier` or
  `source` is a **422**, not a field silently dropped. With `extra` at its default the request would
  have succeeded and the caller would have had every reason to think it had named the author.
- There is no tier field and there must never be one — the same argument as `record_human_label`'s
  absent tier parameter, one level up the stack.
- `_label_principal(tenant)` returns `f"tenant:{tenant.id}"`.

**`labelled_by` names an ACCOUNT, not a person, and the prefix says so.** `get_current_tenant`
resolves to a `Tenant` by either a Clerk JWT (behind which there is one specific human) or an
`X-API-Key` (a machine credential with no human behind it at all), and **it does not report which
path ran**. Reading `tenant.clerk_user_id` would therefore attribute an API-key write to a Clerk user
who may not have made it — a false authorship claim stamped beside `human_authored`, in the one place
in the system where authorship claims are the entire point. Recording the account is the strongest
claim this auth layer supports. `deployment.py:449` already uses `str(tenant.id)` for
`run.approved_by`; the `tenant:` prefix is added here because a bare UUID beside a human trust tier
reads as a user id. Narrowing it to a person needs a principal-aware dependency in
`app/api/deps.py`. `BACKLOG 4.7` narrowed to exactly that residue.

**The row's `source` is not touched.** `test_a_label_is_recorded_at_the_human_authored_tier` parses
the emitted SET clause and asserts it assigns exactly
`{reference_answer, label_trust_tier, labelled_by, labelled_at}` and never `source` — fusing the
question's origin with the answer's provenance is the defect D6 P1 exists to prevent.

**R4's early-out.** `assert_human_context()` runs as the handler's first statement, before the
ownership check and before `_record_label_sync` opens a connection. `record_human_label` re-asserts
it, but by then a connection exists — so the route check is what keeps P1's stated property (*a
refused context must not be able to reach the database at all*) true across the `asyncio.to_thread`
hop. The refusal is a 500 with a fixed detail; the internal reason goes to the log, not the response.

**Error mapping**, all pinned:

| condition | status | why |
|---|---|---|
| `rows_updated == 0` | 404 | no such row **in this tenant's DB** — also the cross-tenant outcome, and the two must be indistinguishable |
| `LabelRejected` (empty/whitespace answer) | 422 | Pydantic `min_length=1` catches `""`; the service catches `"   "` |
| `HumanLabelRefused` | 500 | the API process believes a model is driving it; a server fault, not the caller's |
| `psycopg2.errors.UndefinedColumn` | 503 | **the state of every tenant DB today** — the detail names migration 0016 rather than surfacing a traceback |

---

## 6. TENANT ISOLATION

`_resolve_agent_tenant_db` is the single ownership check both routes use. The task called a
cross-tenant labelling route a critical defect, so the mechanism is stated and tested rather than
assumed:

1. the agent is fetched from the **control** DB and 404s (never 403) unless
   `agent.tenant_id == tenant.id`, so a foreign agent id is indistinguishable from a nonexistent one;
2. the only database a queue route opens is the one behind **that agent's** encrypted connection
   string, so a `scenario_id` from another tenant is not a row here and the write matches nothing.

`test_a_cross_tenant_request_is_404_and_opens_no_database[GET|POST]` asserts the status **and** that
neither `fernet_decrypt` nor `psycopg2.connect` was ever called — a status code alone would not prove
that nothing was decrypted. `test_the_two_queue_routes_use_the_one_ownership_check` asserts neither
handler re-implements the comparison, and `test_the_ownership_check_still_compares_the_tenant`
asserts the shared one still makes it — delegation is worth nothing if the delegate stopped checking.

---

## 7. WHAT WAS OBSERVED

### 7.1 Baseline

`4179a5c` (branch point) is documented as 1873 passed / 11 skipped. **That is not this phase's
baseline** — P1 landed 10 commits on top of it. Baseline re-observed at `8e3d337` (HEAD before P2):

```
1994 passed, 12 skipped, 28 warnings in 394.37s (0:06:34)
```

### 7.1b THE FIRST GATE RUN FAILED, AND THAT IS THE MOST USEFUL THING IN THIS DOCUMENT

The new test file passed **55/55 in isolation** and the full-suite run came back:

```
11 failed, 2036 passed, 12 skipped, 30 warnings in 383.20s (0:06:23)
```

All 11 were POST-path tests in `test_eval_label_queue.py`. The cause, from the failing run's own log
line:

```
label_eval_scenario.refused_context ... reason="a human trust tier may not be stamped from
inside an agent tool context (agent_id='agent-reset-test'); ..."
```

That is **`BACKLOG 4.6`**: `agent_tools.build_tool_server()` sets `_agent_id_var` and never clears it
(correctly — it is setting up a turn), and `tests/unit/test_agent_tools.py:686` calls it with
`agent_id='agent-reset-test'`, so the value is live for the rest of the pytest process. The label
route's R4 guard was **behaving exactly as specified, on a stale fact**, and the tests were asserting
against a precondition they never established.

Reproduced deliberately, at minimum size, before fixing anything — a two-line module that sets the
var, run ahead of the queue tests:

```
11 failed, 43 passed in 28.03s     # before the fixture
55 passed in 24.05s                # after it
```

Three things follow, and none of them is "add a fixture and move on":

1. **The direction of the failure is fail-CLOSED.** A stale agent context makes the route refuse
   more, never less. It produced 500s, not silently forged `human_authored` rows. That is the guard
   earning its place.
2. **The fixture would have hidden a real behaviour, so the behaviour is now pinned separately.**
   `test_a_REAL_agent_context_refuses_the_label_and_opens_nothing` sets `_agent_id_var` the way
   `build_tool_server` does and lets the genuine guard decide — as opposed to
   `test_a_model_driven_context_is_refused_before_a_connection_opens`, which patches
   `assert_human_context` and therefore only proves the route handles the exception.
3. **`BACKLOG 4.6` is now costing its second identical fixture** (`test_label_provenance.py` paid it
   first). Every module that touches the human-label path will pay it until it is fixed at the
   source. The row has been updated with this evidence.

In a real ASGI process each request runs in its own asyncio Task, whose context is a copy, so a
`set()` in one request does not propagate into the next — this is a pytest-process problem, not a
demonstrated production one. That is an argument about where to fix it, not a reason to let a unit
test assert against a precondition it never established.

### 7.2 Gate after P2

Command, exactly as CLAUDE.md specifies, from `apps/api`:

```
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py
```

```
2048 passed, 12 skipped, 30 warnings in 363.89s (0:06:03)
```

`1994 + 54 = 2048`, and `test_eval_label_queue.py` collects exactly 54. Skips unchanged at 12 — no
`-m integration` harness became runnable and none was expected to; a skip is unobserved, never a pass.

> An earlier run of this same command returned `2048 passed, 12 skipped ... 379.16s` **before** a
> follow-up encoding repair (see §7.5). The number above is the one that corresponds to the committed
> tree, and it is the one reported.

### 7.3 The ignored-new-files control (`BACKLOG 2.26`)

The same command with the new test file **also** ignored, so the only thing left in the run is
pre-existing tests exercised against the modified `evals.py`:

```
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py \
  --ignore=tests/unit/test_eval_label_queue.py
```

```
1994 passed, 12 skipped, 28 warnings in 371.40s (0:06:11)
```

**Identical to the `8e3d337` baseline**, pass for pass and skip for skip. This is the claim
test-count arithmetic cannot make: no pre-existing test changed status — none newly failed, none
newly skipped, none silently stopped being collected — under the 582-line change to `evals.py`.

### 7.5 The encoding repair

`evals.py` was extended by appending a prepared block with PowerShell. `Get-Content` without
`-Encoding` in Windows PowerShell 5.1 reads with the system ANSI codepage, so the appended block's
em-dashes arrived as the mojibake `â€”` — 27 of them, all inside comments and docstrings plus two
inside runtime strings (`QUEUE_ORDERING["reason"]` and the 503 detail). Detected by scanning the file
for `[ÂÃâ][-¿]{1,2}`, repaired in place, and re-verified: only `U+2014` and
a pre-existing `U+00A7` remain above ASCII, with no BOM and no CR. The definitive gate above was
re-run afterwards so the reported number belongs to the committed tree.

### 7.4 Mutation proofs

*A negative test never observed to fail is indistinguishable from a tautology.* Every guard below was
mutated, RUN, observed red, restored **from `HEAD` unconditionally** (`git checkout HEAD --
app/api/v1/evals.py`), and RUN again to observe green. Verbatim final pytest lines, not intentions.

Selector, identical for every row, run from `apps/api`:

```
.venv/Scripts/python.exe -m pytest tests/unit/test_eval_label_queue.py -q
```

Restore was unconditional (a `finally:` around the red run), and the tree was verified clean
afterwards: `git diff --stat HEAD -- apps/api/app/api/v1/evals.py` is empty.

| # | guard | mutation | RED | GREEN |
|---|---|---|---|---|
| M1 | `ScenarioLabelRequest` forbids extra fields — the body cannot name the author | `ConfigDict(extra="forbid")` → `ConfigDict(extra="ignore")` | `6 failed, 48 passed in 35.77s` | `54 passed in 26.29s` |
| M2 | `_resolve_agent_tenant_db` 404s when the agent belongs to another tenant | delete `if agent.tenant_id != tenant.id: raise HTTPException(404)` | `3 failed, 51 passed in 29.56s` | `54 passed in 24.36s` |
| M3 | `human_labelled` is null, not 0, when the column does not exist | `... else None` → `... else 0` | `1 failed, 53 passed in 26.23s` | `54 passed in 23.98s` |
| M4 | the queue is ordered oldest-first, not by recency | `created_at ASC,` → `created_at DESC,` in the ORDER BY | `1 failed, 53 passed in 26.30s` | `54 passed in 23.72s` |
| M5 | a write that matched no row is a 404 | `if result["rows_updated"] == 0:` → `< 0:` | `1 failed, 53 passed in 26.29s` | `54 passed in 27.04s` |
| M6 | the runtime context guard runs before a connection is opened | `assert_human_context()` → `pass` | `3 failed, 51 passed in 31.75s` | `54 passed in 24.63s` |
| M7 | `labelled_by` is derived from the authenticated principal | `return f"tenant:{tenant.id}"` → `return "owner"` | `2 failed, 52 passed in 27.19s` | `54 passed in 26.81s` |
| M8 | a tenant DB without 0016 gets a 503 naming the migration | `except psycopg2.errors.UndefinedColumn:` → `UndefinedTable:` | `1 failed, 53 passed in 31.23s` | `54 passed in 50.89s` |
| M9 | the queue's WHERE is the eval selector's own predicate, read cross-module | `SELECTOR_ELIGIBILITY_PREDICATE = "reference_answer != ''"` → `"reference_answer <> ''"` | `1 failed, 53 passed in 44.84s` | `54 passed in 30.62s` |
| M10 | an unclassified source sorts LAST, never first | `ASC NULLS LAST` → `ASC NULLS FIRST` | `1 failed, 53 passed in 27.32s` | `54 passed in 24.41s` |
| M11 | the priority order covers every source the schema allows | drop `red_team` from `_source_priority_order()` | `3 failed, 51 passed in 26.29s` | `54 passed in 23.43s` |
| M12 | the route module issues no `eval_scenarios` write of its own | append `_ADVERSARIAL = "UPDATE eval_scenarios SET reference_answer = %(a)s"` | `1 failed, 53 passed in 26.37s` | `54 passed in 25.05s` |
| M13 | the ordering is a total order — `id` is the final key | delete `id ASC` from the ORDER BY | `1 failed, 53 passed in 25.81s` | `54 passed in 24.37s` |
| M14 | every count travels with its denominator | delete `"total": int(total or 0),` from the counts dict | `14 failed, 40 passed in 33.74s` | `54 passed in 24.22s` |

**Fourteen of fourteen went red. None was a tautology.** M9 is worth singling out: the mutation
replaces `!=` with `<>`, which is the *same operator in SQL* — the test still went red, which is what
proves the cross-module pin is reading `run_eval_suite`'s actual text and not merely satisfying
itself.

M1, M2 and M3 were re-run capturing failing test identities rather than counts, because they are the
three the task named as critical:

```
### M1   RED: 6 failed, 48 passed in 27.78s
    FAILED ...::TestTheLabelWrite::test_the_body_may_not_name_the_author
    FAILED ...::TestTheLabelWrite::test_no_other_provenance_field_may_be_submitted_either[label_trust_tier]
    FAILED ...::TestTheLabelWrite::test_no_other_provenance_field_may_be_submitted_either[labelled_at]
    FAILED ...::TestTheLabelWrite::test_no_other_provenance_field_may_be_submitted_either[tier]
    FAILED ...::TestTheLabelWrite::test_no_other_provenance_field_may_be_submitted_either[source]
    FAILED ...::TestTheRouteShape::test_the_request_model_forbids_extra_fields
         GREEN: 54 passed in 24.43s

### M2   RED: 3 failed, 51 passed in 26.94s
    FAILED ...::TestTenantIsolation::test_a_cross_tenant_request_is_404_and_opens_no_database[GET]
    FAILED ...::TestTenantIsolation::test_a_cross_tenant_request_is_404_and_opens_no_database[POST]
    FAILED ...::TestTheRouteShape::test_the_ownership_check_still_compares_the_tenant
         GREEN: 54 passed in 23.74s

### M3   RED: 1 failed, 53 passed in 25.72s
    FAILED ...::TestQueueCounts::test_human_labelled_is_unknown_not_zero_before_migration_0016
         GREEN: 54 passed in 23.70s
```

---

## 8. WHAT IS NOT PROVEN, PLAINLY

- **No query in this phase has been executed by a database.** There is no PostgreSQL server on this
  machine; every `-m integration` harness skips and a skip is unobserved, never a pass.
  `CONTROL_DB_URL` points at live Neon production and is never a substitute.
- **`array_position(...) ASC NULLS LAST` has never been planned or executed.** The ordering is
  asserted at the SQL-string level and against a recording cursor. The row order Postgres would
  actually produce is unobserved. Filed as `BACKLOG 4.9`, together with the absence of any index
  supporting the queue's `WHERE` + `ORDER BY`.
- **Migration 0016 has never been applied anywhere.** The 200 path of the label write has therefore
  never touched a real `label_trust_tier` column. What was observed is the statement `record_human_label`
  emits and the parameters it binds, against `_RecordingCursor`. On a real tenant DB today, every
  label attempt returns the 503.
- **`counts` has never been computed by Postgres.** `FILTER (WHERE ...)`, `= ANY(...::text[])` and
  the `unlabelled + labelled == total` identity are asserted against canned tuples.
- **No mined row has ever been seen.** `mine_production_scenarios` `continue`s past every job whose
  `jobs.conversation_id` is absent, and its own docstring admits the emit payload carries neither
  `conversation_id` nor `question`. Whether this queue would be empty in production is unknown and
  is filed as `BACKLOG 4.10` — a finding about the miner, not about the queue, and the reason P4 (the
  console) stays unstarted.
- **No migration was written by P2, and none could have been applied if it had been.**
