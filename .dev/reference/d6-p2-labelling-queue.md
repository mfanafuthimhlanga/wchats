# D6 P2 — the labelling queue: the finding, the design, and what was actually observed

**Branch** `feat/d6-labelling-loop`, off `feat/d1-agent-invocation` (`4179a5c`), **not `main`**
**Plan** `.dev/plans/260808-d6-labelling-loop.md` § P2
**Trace** `.dev/traces/260809-d6-p2-labelling-queue.md`
**Persisted here per `BACKLOG 2.20`** — the previous branch lost 17 of 48 findings to a temp-directory
journal that did not survive the session.

---

> **CORRECTED 2026-08-09** after the adversarial review of this phase. The changes are marked
> **[CORRECTED]** in place; the mutation ledger in §7.4 gains fourteen more rows in §7.6. The
> corrections are substantive, not cosmetic — four behaviour mutations survived the 54 tests this
> document reported as proof, and §7.4's "fourteen of fourteen" was a statement about the mutations
> that were CHOSEN, never about coverage.
>
> The review itself is `.dev/reference/d6-p2-adversarial-review.md`; what was done about each of its
> 18 findings, with the deviations, is `.dev/reference/d6-p2-review-fixes.md`. Fixes commit `17a5774`.

## 1. THE HEADLINE FINDING: judge confidence is not joinable to a scenario

**[CORRECTED]** The heading overstates its own evidence, and the correction matters because a later
reader could take "not implementable" as closing the question. What the three legs below establish is
**not implementable without a tenant migration and a change to the miner** — which is exactly what
the re-scoped `BACKLOG 6.4` then says, and P1 of this same plan did write a tenant migration. The
distinction is between *impossible* and *not P2's to spend*, and only the second is proven.

The plan (§P2) and the task brief both ask for **uncertainty ordering**, citing `BACKLOG 6.4`:
`validators.py` emits judge `confidence` into `job_events` and it is discarded for ranking. The
instruction included an escape clause — *"If the confidence signal turns out not to be joinable to a
scenario, say so plainly and order by something you CAN defend."*

**It is not joinable.** Three independent reasons, each traced to a line rather than asserted:

### 1.1 It is in a different database

**[CORRECTED]** — the module path was written as bare `validators.py` and three of the four line
numbers were off by one or two. The substance of all three legs is unchanged; the citations now
resolve. There is no `app/services/validators.py`.

`app/worker/tasks/runtime/validators.py` builds its session with `get_sync_db()` (imported at `:37`,
used at `:155`) and passes that session to `emit(job_id, "gatekeeper.complete", {...}, db, _redis)`
(`:207-213`). `emit` (`app/services/events.py:36`) inserts one `job_events` row through that session.
`get_sync_db` is the **control DB**.

`eval_scenarios` is a **tenant** table — `alembic_tenant/versions/0005_verified_qa_eval_scenarios.py`
creates it, and every reader in `app/api/v1/evals.py` reaches it through
`fernet_decrypt(agent.neon_connection_string)`.

Per CLAUDE.md project rule 9 these are **per-tenant Neon projects**, not schemas in one database. No
SQL statement can join across them. This alone rules out an `ORDER BY confidence`.

### 1.2 There is no join key, so application-side correlation does not rescue it

`scenario_service.store_scenarios` (`app/services/scenario_service.py:153-166`) inserts exactly:

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
(`app/worker/tasks/runtime/validators.py:368-377`) — i.e. only for **grounded** turns above
threshold. The queue's population
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

so a console cannot render this as "the rows the judges were least sure about".

**[CORRECTED]** Two things this section claimed were not true:

- **The key list described a query that does not exist.** It was hand-written as
  `["origin_trust_tier DESC", "created_at ASC", "id ASC"]` — naming a column that is not in
  `eval_scenarios` and a direction the statement does not use. And the statement's own direction was
  pinned by nothing: reversing `array_position(...) ASC NULLS LAST` to `DESC NULLS LAST`, which puts
  `generated` first and `mined` last, passed all 54 tests while the payload went on claiming `DESC`.
  `keys` is now **parsed out of `_UNLABELLED_QUEUE_SQL`** by `_order_by_keys()`, so the payload cannot
  describe an ordering the database is not performing, and a test comparing the parsed list pins every
  key's direction at once. Proof: `M15`.
- **`dict(QUEUE_ORDERING)` is a shallow copy and `keys` is a list**, so the "copy" shared the
  constant's list and `body["ordering"]["keys"].append(...)` mutated it for every later request in the
  process. Not reachable over HTTP, where FastAPI serialises the dict — but the comparison drawn to
  `eval_service.VERIFIED_QA_PROMOTION_DECISION` did not hold: that constant is all scalars and has no
  nested mutable to share. Now `copy.deepcopy`, asserted behaviourally rather than by checking the
  handler's source for a substring. Proof: `M26`.

---

## 3. THE SELECTOR IS UNTOUCHED, AND THE PIN IS CROSS-MODULE

`SELECTOR_ELIGIBILITY_PREDICATE = "reference_answer != ''"` is spelled once. **[CORRECTED]** — it now
lives in `app/services/eval_service.py`, not in `evals.py`: `label_service`'s UPDATE needs the same
string to scope itself (see below) and a service may not import `app.api` (R2), so the one module both
sides already import is where it has to be. `evals.py` re-exports the name, so the pin below still
reads it through `evals_module`.

All three queue statements use `NOT (...)` or `(...)` of that constant, and
`test_the_queue_selects_exactly_what_the_eval_selector_excludes` reads the constant back out of
`inspect.getsource(run_eval_suite)`. If the task ever stops filtering on it, this queue's
"unlabelled" silently stops meaning "will never be scored" — and that test is what makes it audible.
`test_the_scenario_is_inert_to_the_eval_selector_by_construction` in `test_promote_trace.py` is
untouched and still green.

`counts.eligible == counts.labelled`, and that identity **is** the P2 claim rather than a redundancy:
writing an answer is the whole of what makes a row eligible, so the selector needs no change.
`eligible` is *"the selector will consider it"*, not *"it will be scored tonight"* — the exploratory
half of a run is a sample of at most `EXPLORATORY_SAMPLE_SIZE` rows.

**[CORRECTED] "Reporting both names lets a reader check it from the payload instead of taking it on
trust" was false, and it is the more dangerous kind of false: it invites a reader to verify something
by looking at a tautology.** `_queue_counts_sync` binds the SAME Python variable to `labelled` and to
`eligible`, so they are equal unconditionally and whatever `run_eval_suite` filters on. There is
nothing in the payload to check. What holds the identity is the cross-module pin — `M9`, a real proof:
replacing `!=` with the semantically identical `<>` still turns it red, because the test is reading
`run_eval_suite`'s literal text. `eligible` is reported so a console has the number under the name the
eval uses; the docstring now says so.

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
  **[CORRECTED] — true by reading the source, and until 2026-08-09 guarded by nothing.** Replacing the
  `labelled` FILTER with `WHERE question != ''`, which makes the identity FALSE in Postgres, passed all
  54 tests: `test_every_count_travels_with_its_denominator` asserts the arithmetic over
  `counts_row=(10, 4, 6, 2)`, numbers the test itself supplies. The two FILTERs are now **counted**
  in both counts statements — exactly one `FILTER (WHERE NOT (<p>))` and exactly one
  `FILTER (WHERE <p>)` — because a presence check for the un-negated form is satisfied by the negated
  form as a substring. Proof: `M17`.
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

**`labelled_by` names an ACCOUNT, not a person, and the prefix says so.** `deployment.py:449` already
uses `str(tenant.id)` for `run.approved_by`; the `tenant:` prefix is added here because a bare UUID
beside a human trust tier reads as a user id.

**[CORRECTED] — THE STANDING TRAP THIS SECTION UNDERSTATED, AND IT IS NOW CLOSED.** The original text
framed the gap as identity granularity: "an account, not a person", with the remedy being a
principal-aware dependency. That framing would have let a later reader close `BACKLOG 4.7` by adding
`clerk_user_id` without touching the real problem, which is different in kind:

> `get_current_tenant` accepts `X-API-Key`, **a machine credential**. Any script, scheduler or
> model-driven pipeline holding a tenant key could POST model prose to this route and have it stored
> as `label_trust_tier='human_authored'` — the tier `VERIFIED_QA_MIN_TRUST_TIER` is defined over. So
> the value of the whole hierarchy was bounded by the secrecy of an API key rather than by any
> human-in-the-loop property. `label_service`'s R1–R4 cannot see it: they read a parameter list, an
> import graph, Celery's thread-local task stack and an `agent_tools` ContextVar, and **all four are
> in-process facts** that an out-of-process caller trips none of.

The credential is the only evidence about the caller that survives a process boundary, so the check
had to go at the auth layer. `get_current_tenant` now records which path resolved on
`request.state.credential_kind`; `get_credential_kind` is a dependency that reports it; and
`label_eval_scenario` refuses anything but `CREDENTIAL_CLERK_JWT` with a **403** — including
`CREDENTIAL_UNKNOWN`, because "cannot tell" must never resolve to "human". The GET is deliberately
**not** gated: reading the queue asserts nothing about who is reading. Proof: `M21`.

Two facts that narrowed the original exposure and are worth keeping on the record: `tenant.api_key_hash`
is argon2, so nothing in `app/worker` or `app/services` can recover a usable key from the control DB;
and R2 pins that no worker or service module imports `app.api`.

What remains of `BACKLOG 4.7` is now genuinely the person, not the machine: knowing a JWT
authenticated the request is not knowing which human sent it, because the tenant is looked up BY that
claim and nothing in the schema forbids a second user against one tenant. That needs the principal
carried out of the dependency, not re-derived from the tenant row.

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
| `rows_updated == 0`, row absent | 404 | no such row **in this tenant's DB** — also the cross-tenant outcome, and the two must be indistinguishable |
| `rows_updated == 0`, row present | **409** | **[CORRECTED]** already answered; the scoped UPDATE skipped it. See below |
| visibly-empty answer | 422 | **[CORRECTED]** a `field_validator` on the request model, so no connection is opened |
| over `MAX_REFERENCE_ANSWER_CHARS` | 422 | **[CORRECTED]** 8000 chars; the stored value is fed to a paid judge every night |
| credential is not a Clerk JWT | **403** | **[CORRECTED]** an API key authenticates an account, not a person |
| `HumanLabelRefused` | 500 | the API process believes a model is driving it; a server fault, not the caller's |
| `psycopg2.errors.UndefinedColumn` | 503 | **the state of every tenant DB today** — the detail names migration 0016 rather than surfacing a traceback |

**[CORRECTED] THE WRITE'S REACH WAS WIDER THAN THE FEATURE IT SERVES, AND THIS DOCUMENT DID NOT
MENTION IT.** `_LABEL_SQL`'s WHERE was `id = %(scenario_id)s::uuid` alone. The GET only ever returns
unlabelled rows, but the POST reached **any** scenario in the agent's database: one request silently
replaced an existing `reference_answer` and re-stamped `labelled_by` / `labelled_at`, with no record
of what had been there and no test covering it. On a `dataset='golden'` row that is worse than losing
an answer — `eval.py` runs the golden half in full every night precisely so consecutive runs are a
**paired per-item comparison**, and moving one item's reference answer breaks the comparison while the
run report has no way to say so. On a `generated` row it restamps model output as `human_authored`.

The UPDATE is now scoped by `AND NOT (<selector predicate>)`, so its reach is exactly the queue's own
population and the two are defined by the same string. Zero rows then has two causes, and
`record_human_label` runs a `SELECT 1` probe **only on that path** to tell them apart, returning
`already_labelled`. Relabelling is refused rather than silently performed: if a correction path is
wanted it is an explicit second act — which answer is superseded, by whom, and whether a golden row may
move at all — not a side effect of the queue's write. Proofs: `M20`, `M27`.

**[CORRECTED] The empty-answer guard was `str.strip()`, which does not remove Cf.** A zero-width
answer (`U+200B`, `U+FEFF`, `U+200C`) was accepted, stamped `human_authored`, and satisfied both
`run_eval_suite`'s `reference_answer != ''` and 0016's `COALESCE(reference_answer,'') <> ''` CHECK —
re-inerting the row while marking it labelled, which is the exact state the guard exists to prevent.
The realistic origin is a stray character from a rich-text paste, not an attacker. Emptiness is now
decided on Unicode general category in `label_service.visible_answer`, used by both the request model
and the writer. Proof: `M22`.

**[CORRECTED] And that check now runs at the boundary.** `test_an_empty_answer_is_rejected_without_
touching_the_database` was false for `"   "` and `"\n\t "`: Pydantic's `min_length=1` passed them, so
`_resolve_agent_tenant_db` decrypted and `psycopg2.connect` ran before `record_human_label` rejected
them. The test held only because it asserted "no write statement executed", and `record_human_label`
raises before opening a cursor. The check is a `field_validator` now, the recording connection counts
`connects`, and the property the route advertises for a refused CONTEXT is finally the property it has
for refused CONTENT. Proof: `M23`.

**[CORRECTED] A soft-deleted agent was still labellable.** `_resolve_agent_tenant_db` used
`db.get(Agent, agent_id)`, which cannot express a filter, so `DELETE /agents/{id}` followed by a label
POST decrypted a deleted agent's connection string and wrote into its tenant database — contradicting
the invariant `agents.py:226` states for the whole API surface. It now issues
`select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))`. The three older read routes
in this module share the gap; fixing them is a separate decision, deliberately not taken here. Proof:
`M24`.

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

**[CORRECTED] — and "fourteen of fourteen" is a statement about the mutations that were CHOSEN, not
about coverage.** Each of the fourteen is honest; the review reproduced them and found no fabricated
red and no missing restore. But four mutations it chose instead survived the whole suite — the
`array_position` sort direction, the `LIMIT`/`OFFSET` binding, the `labelled` FILTER predicate, and
three of four spellings of a forged write — and those cover the three properties this document's prose
leans on hardest. They are `M15`, `M16`, `M17`, `M18` and `M19` in §7.6.

**[CORRECTED] M8 could not be replayed as recorded.** `except psycopg2.errors.UndefinedColumn` occurs
three times in `evals.py` — in `list_eval_runs`, in `_queue_counts_sync` and in `label_eval_scenario` —
and the row recorded only `1 failed`, so a reader could not tell which guard had been demonstrated.
Mutating `_queue_counts_sync` reds
`test_human_labelled_is_unknown_not_zero_before_migration_0016`; mutating `label_eval_scenario` reds
`test_a_tenant_database_without_0016_says_which_migration_is_missing`; both produce exactly `1 failed`.
The `label_eval_scenario` occurrence — the one the row's title is about — was re-run on 2026-08-09 with
the failing identity captured, as `M8b` in §7.6.

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

### 7.6 Mutation proofs for the review fixes (2026-08-09)

Same discipline: mutate, RUN, observe red, restore **from `HEAD` unconditionally**
(`git checkout HEAD -- <path>`, in a `finally:`), RUN again, observe green. Verbatim final pytest
lines and the FAILED identities of every red run. The harness is
`scratchpad/mutate.py`; it refuses an anchor that matches other than exactly once, and it refuses a
mutation that changes nothing, so a silently-no-op "proof" cannot be recorded.

Selectors, run from `apps/api`:

```
A: .venv/Scripts/python.exe -m pytest tests/unit/test_eval_label_queue.py -q
B: .venv/Scripts/python.exe -m pytest tests/unit/test_eval_label_queue.py tests/unit/test_label_provenance.py -q
```

Baseline for A is `83 passed`; for B, `170 passed`.

| # | guard | mutation | sel | RED | GREEN |
|---|---|---|---|---|---|
| M15 | the priority key sorts the best origin FIRST | `array_position(...) ASC NULLS LAST,` → `DESC NULLS LAST,` | A | `3 failed, 80 passed in 30.31s` | `83 passed in 24.84s` |
| M16 | limit binds to LIMIT and offset to OFFSET | `LIMIT %(limit)s OFFSET %(offset)s` → `LIMIT %(offset)s OFFSET %(limit)s` | A | `1 failed, 82 passed in 26.87s` | `83 passed in 25.01s` |
| M17 | the `labelled` FILTER is the selector predicate | `FILTER (WHERE {SELECTOR_ELIGIBILITY_PREDICATE}) AS labelled,` → `FILTER (WHERE question != '') AS labelled,` | A | `1 failed, 82 passed in 26.58s` | `83 passed in 24.17s` |
| M18 | no second write path — **composed** spelling | append `_ADV_TBL = "eval_" + "scenarios"` / `_ADVERSARIAL = f"UPDATE {_ADV_TBL} SET reference_answer = %(a)s"` | B | `1 failed, 169 passed in 32.30s` | `170 passed in 29.83s` |
| M19 | no second write path — **schema-qualified** spelling | append `_ADVERSARIAL = "UPDATE public.eval_scenarios SET reference_answer = %(a)s"` | B | `2 failed, 168 passed in 34.69s` | `170 passed in 30.57s` |
| M20 | the label UPDATE is scoped to an unlabelled row | delete `AND NOT ({SELECTOR_ELIGIBILITY_PREDICATE})` from `_LABEL_SQL` | B | `2 failed, 168 passed in 32.91s` | `170 passed in 29.93s` |
| M21 | only a Clerk JWT may stamp a human tier | `if credential_kind != CREDENTIAL_CLERK_JWT:` → `if credential_kind == 'never-this':` | A | `3 failed, 80 passed in 26.67s` | `83 passed in 25.81s` |
| M22 | emptiness is decided on Unicode category | drop `"Cf"` from `_INVISIBLE_CATEGORIES` | B | `4 failed, 166 passed in 33.15s` | `170 passed in 30.93s` |
| M23 | the emptiness check is at the BOUNDARY | `answer = visible_answer(value)` → `answer = value` in the field validator | A | `7 failed, 76 passed in 27.21s` | `83 passed in 24.20s` |
| M24 | a soft-deleted agent is not resolvable | delete `Agent.deleted_at.is_(None)` from the agent SELECT | A | `2 failed, 81 passed in 27.21s` | `83 passed in 26.94s` |
| M25 | the reference answer is bounded | delete `max_length=MAX_REFERENCE_ANSWER_CHARS,` | A | `1 failed, 82 passed in 30.12s` | `83 passed in 25.59s` |
| M26 | QUEUE_ORDERING is deep-copied | `copy.deepcopy(QUEUE_ORDERING)` → `dict(QUEUE_ORDERING)` | A | `1 failed, 82 passed in 28.03s` | `83 passed in 24.82s` |
| M27 | the probe distinguishes a relabel from a missing row | `already_labelled = bool(cur.fetchall())` → `= False` | A | `1 failed, 82 passed in 27.30s` | `83 passed in 25.16s` |
| M8b | the 503 naming 0016, **`label_eval_scenario` occurrence** | that occurrence's `except psycopg2.errors.UndefinedColumn:` → `UndefinedTable:` | A | `1 failed, 82 passed in 27.88s` | `83 passed in 24.83s` |

**Fourteen more, fourteen red.** The FAILED identities, so each row can be replayed against a named
guard rather than a count:

```
### M15   3 failed
    FAILED ...::TestQueueOrdering::test_the_ordering_is_exactly_these_keys_in_this_direction
    FAILED ...::TestQueueOrdering::test_the_priority_key_sorts_the_best_origin_first_not_last
    FAILED ...::TestQueueOrdering::test_the_response_states_that_this_is_not_an_uncertainty_ordering

### M16   1 failed
    FAILED ...::TestQueueOrdering::test_the_page_takes_its_limit_and_offset_in_that_order

### M17   1 failed
    FAILED ...::TestTheSelectorIsUntouched::test_the_two_count_filters_are_the_predicate_and_its_exact_negation

### M18   1 failed          <- THE ONE THAT MATTERS MOST
    FAILED ...::TestTheSelectorIsUntouched::test_this_module_issues_no_write_statement_of_any_kind

### M19   2 failed
    FAILED ...::TestTheSelectorIsUntouched::test_this_module_issues_no_write_of_its_own_to_eval_scenarios
    FAILED ...::TestTheSelectorIsUntouched::test_this_module_issues_no_write_statement_of_any_kind

### M20   2 failed
    FAILED ...::TestTheLabelWrite::test_a_scenario_that_already_has_an_answer_is_a_409_not_an_overwrite
    FAILED ...::TestTheLabelWrite::test_the_label_write_is_scoped_to_an_unlabelled_row

### M21   3 failed
    FAILED ...::TestOnlyAHumansCredentialMayStampAHumanTier::test_an_api_key_may_not_record_a_human_label
    FAILED ...::TestOnlyAHumansCredentialMayStampAHumanTier::test_an_unrecorded_credential_is_refused_too
    FAILED ...::TestOnlyAHumansCredentialMayStampAHumanTier::test_the_route_declares_the_credential_dependency

### M22   4 failed
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\u200b]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\ufeff]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\u200c]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\u200b\u200c\ufeff]

### M23   7 failed   (all eight parametrisations except `""`, which min_length=1 still catches)
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[   ]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\n\t ]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\xa0]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\u200b]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\ufeff]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\u200c]
    FAILED ...::TestTheLabelWrite::test_an_empty_answer_is_rejected_without_touching_the_database[\u200b\u200c\ufeff]

### M24   2 failed
    FAILED ...::TestTenantIsolation::test_a_soft_deleted_agent_is_gone_from_both_routes[GET]
    FAILED ...::TestTenantIsolation::test_a_soft_deleted_agent_is_gone_from_both_routes[POST]

### M25   1 failed
    FAILED ...::TestTheLabelWrite::test_an_oversized_answer_is_rejected_at_the_boundary

### M26   1 failed
    FAILED ...::TestQueueOrdering::test_the_ordering_record_cannot_be_mutated_through_the_response

### M27   1 failed
    FAILED ...::TestTheLabelWrite::test_a_scenario_that_already_has_an_answer_is_a_409_not_an_overwrite

### M8b   1 failed          <- the identity the original M8 row could not supply
    FAILED ...::TestTheLabelWrite::test_a_tenant_database_without_0016_says_which_migration_is_missing
```

**M18 and M19 are the pair worth reading together.** M19 (schema-qualified) reds BOTH scans; M18
(table name composed from fragments) reds only the verb scan, because the statement scan still has to
recognise the table and reassembles that forgery as `UPDATE  SET ...`. That division of labour is
itself pinned — `test_the_table_aware_scan_has_this_exact_blind_spot` asserts the first scan finds
nothing there and the second does — so nobody deletes the verb scan as redundant. Neither scan claims
forgery is impossible; between them, no spelling anyone has yet devised passes unnoticed.

After every mutation the tree was verified clean: `git status --short` shows only untracked review
notes and `git diff --stat HEAD` is empty.

### 7.7 Gates after the review fixes

```
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py

2077 passed, 12 skipped, 28 warnings in 369.61s (0:06:09)
```

`1994 + 83 = 2077`, and `test_eval_label_queue.py` collects exactly 83 (was 54). Skips unchanged at
12 — no `-m integration` harness became runnable and none could: there is still no PostgreSQL server
on this machine.

The ignored-new-files control (`BACKLOG 2.26`), with `test_eval_label_queue.py` also ignored:

```
1994 passed, 12 skipped, 28 warnings in 387.82s (0:06:27)
```

**Identical to the control observed at `44f0ad5`**, pass for pass and skip for skip — under changes to
`app/api/deps.py` (which every authenticated route in the application resolves),
`app/api/v1/evals.py`, `app/services/eval_service.py`, `app/services/label_service.py` and
`tests/unit/test_label_provenance.py`. `test_label_provenance.py` was modified but not extended: its
recording cursor gained a `fetchall`, and two assertions were updated for the writer's new
`already_labelled` outcome, so its count is unchanged at 87 and the control's 1994 is comparable.

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

**[ADDED 2026-08-09] What the review fixes did NOT prove:**

- **The scoped UPDATE has never been planned or executed.** `AND NOT (reference_answer != '')` is
  asserted at the SQL-string level and against a recording cursor. Whether Postgres would use an
  index for it, and whether the row-level race between two concurrent labellers resolves the way the
  409 implies, are both unobserved. The existence probe is a SECOND statement in the same transaction
  and is not `FOR UPDATE`, so two simultaneous POSTs against the same unlabelled row could in
  principle both see rows_updated == 1 or both report 409 depending on isolation level — with the
  default READ COMMITTED, the loser's UPDATE re-evaluates the predicate against the committed row and
  matches nothing, which gives the intended 409, but that is reasoning about the manual, not an
  observation.
- **`request.state.credential_kind` has never been read in a real ASGI process.** It is exercised
  through `get_current_tenant` called directly with a hand-built `Request`, and through dependency
  overrides in the route tests. The one thing not covered end to end is a genuine Clerk JWT arriving
  over HTTP and the label route seeing `clerk_jwt` — that needs a live JWKS.
- **The 403 changes the contract for any existing API-key caller of this route.** There is none today
  (the route is four days old and P4, the console, is unstarted), so no caller was broken. If an
  automation is later wanted here, the answer is a new tier and a new writer — `human_verified`
  already exists in 0016's CHECK for exactly that reason — not widening this gate.
- **`MAX_REFERENCE_ANSWER_CHARS = 8000` is a judgement, not a measurement.** Nothing measured what a
  real reference answer costs in a Ragas prompt. The bound is generous enough that the guess being
  wrong is visible as a 422 rather than as silent truncation.
