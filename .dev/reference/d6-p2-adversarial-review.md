# D6 P2 — adversarial review of the labelling queue

**Reviewed** `4962ff5` (`feat(eval): a queue for the rows nothing could label…`) and `44f0ad5`
(the reference doc), on `feat/d6-labelling-loop`, stacked on unmerged `feat/d1-agent-invocation`.
**Reviewer** tier-1 adversarial, session model. Nothing merged, nothing modified — every mutation
below was restored with `git checkout HEAD -- apps/api/app/api/v1/evals.py` in a `finally:` and the
tree was verified clean (`git status --short` empty, `git diff --stat HEAD` empty) afterwards.

**Persisted here per `BACKLOG 2.20`.**

---

## 0. What I re-ran myself, and what it said

| what | command | observed |
|---|---|---|
| the gate | `.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` | `2048 passed, 12 skipped, 30 warnings in 406.79s (0:06:46)` |
| ignored-new-files control (`BACKLOG 2.26`) | same, plus `--ignore=tests/unit/test_eval_label_queue.py` | `1994 passed, 12 skipped, 28 warnings in 377.35s (0:06:17)` |
| collection of the new file | `pytest tests/unit/test_eval_label_queue.py --collect-only -q` | `54 tests collected in 27.29s` |
| encoding of `evals.py` | codepoint scan | above-ASCII = `U+2014 ×75`, `U+00A7 ×1`; 0 BOM, 0 CR, no mojibake |

Both gate numbers reproduce the implementer's claims exactly. `1994 + 54 = 2048`. The encoding
repair is real.

**What I did not do:** I did not check out `8e3d337` to re-observe the pre-P2 baseline myself. The
control's `1994 passed / 12 skipped` matches both the arithmetic and the number the implementer
recorded for that commit, and it is what the control is for — but the baseline itself is taken on the
implementer's word, not re-observed here.

**No PostgreSQL on this machine.** Nothing in this review executed a query against a database
either. Every SQL judgement below is a judgement about text and about what the test suite can see.

---

## 1. Findings, most severe first

### F1 (high) — the "no second write path" guard is demonstrated only inside its own blind spot

`test_this_module_issues_no_write_of_its_own_to_eval_scenarios` normalises `evals.py` to uppercase
and searches for the literal substrings `UPDATE EVAL_SCENARIOS` / `INSERT INTO EVAL_SCENARIOS`. Its
docstring claims it closes the gap R3 cannot see — a bare `SET reference_answer = …` in the one
module allowlisted to hold the writer, which names no label column and therefore never trips R3.

Mutation M12 in the implementer's table appends exactly the one spelling that scan can see. I ran
four forgery shapes appended at module level in `evals.py`, each against **both**
`test_eval_label_queue.py` and `test_label_provenance.py` (141 tests):

```
### MX6   _ = "UPDATE public.eval_scenarios SET reference_answer = %(a)s, label_trust_tier = %(t)s"
    MUTATED: 1 failed, 140 passed in 31.65s
        FAILED tests/unit/test_label_provenance.py::TestR3TheModelWritersCannotWrite::test_only_the_label_writer_writes_the_label_columns
    RESTORED: 141 passed in 30.02s
### MX6b  _ = "UPDATE public.eval_scenarios SET reference_answer = %(a)s"
    MUTATED: 141 passed in 29.28s
    RESTORED: 141 passed in 29.01s
### MX6c  _ = 'UPDATE "eval_scenarios" SET reference_answer = %(a)s'
    MUTATED: 141 passed in 29.12s
    RESTORED: 141 passed in 29.01s
### MX6d  _TBL = "eval_" + "scenarios";  _ = f"UPDATE {_TBL} SET reference_answer = %(a)s"
    MUTATED: 141 passed in 29.52s
    RESTORED: 141 passed in 28.93s
```

MX6 is caught — by **R3**, not by the new test. MX6b/c/d are the shape the new test exists for, and
all three are invisible to the whole suite. `public.`, a quoted identifier and a two-fragment
concatenation are the three evasions R3's own AST reconstruction already models; the new test
reimplemented a weaker scan instead of reusing `test_label_provenance._scenario_write_statements`.

Consequence: the module that is the single permitted holder of the human-label write path has no
working guard against growing a second, service-bypassing write to `eval_scenarios`.

### F2 (high) — the queue's headline ordering property is unpinned in the SQL

```
### MX1  array_position(%(source_priority)s::text[], source) ASC NULLS LAST  ->  DESC NULLS LAST
    MUTATED: 54 passed in 25.18s
    RESTORED: 54 passed in 24.04s
```

`DESC` inverts the priority: `generated` (array position 4) sorts first and `mined` (1) last — the
exact opposite of what the commit message, the module comment, the reference doc §2 and
`QUEUE_ORDERING["keys"]` all assert. The suite does not notice.

The only test that checks tier priority, `test_a_customer_negative_origin_outranks_a_model_generated_one`,
exercises the Python helper `_source_priority_order()` and never the statement. M10
(`NULLS LAST → NULLS FIRST`) pins the NULL arm; M4 pins `created_at`; M13 pins the `id` tiebreak.
Nothing pins the direction of the key the whole ordering section is about.

### F3 (medium-high) — the write reaches any scenario in the agent's DB, not just an unlabelled one

`label_service._LABEL_SQL`:

```sql
UPDATE eval_scenarios
SET reference_answer = %(reference_answer)s, label_trust_tier = %(tier)s,
    labelled_by = %(labelled_by)s, labelled_at = NOW()
WHERE id = %(scenario_id)s::uuid
```

No `AND NOT (reference_answer != '')`, no `dataset` predicate, no if-match. The GET only ever returns
unlabelled rows, so the feature's contract is "label a row from the queue" — but the POST accepts any
`scenario_id` in that agent's database. One request therefore silently overwrites:

- an existing human label (another principal's answer, its `labelled_by` and `labelled_at`), with no
  record of what was there;
- a **golden-set** reference answer. `eval.py`'s `_GOLDEN_SQL` runs golden rows in full every night
  precisely so consecutive runs are a paired per-item comparison; mutating one row's
  `reference_answer` breaks that comparison silently and the run report has no way to say so;
- a `generated` row's model-written answer, which then reads `human_authored`.

None of the 54 tests exercises a relabel. `label_service`'s comment ("`labelled_at` moves on a
genuine relabel, which is correct") is the only place the case is considered, and it considers only
the timestamp. The implementer's `not_done` does not mention it.

### F4 (medium) — `labelled` is not pinned to the selector predicate, so the counts identity is unguarded

```
### MX3  COUNT(*) FILTER (WHERE {SELECTOR_ELIGIBILITY_PREDICATE}) AS labelled
         ->  COUNT(*) FILTER (WHERE question != '') AS labelled          [in _QUEUE_COUNTS_SQL]
    MUTATED: 54 passed in 23.57s
    RESTORED: 54 passed in 23.50s
```

Claim 4 of the report — "`unlabelled + labelled == total` is an identity of the SQL rather than a
coincidence" — is **true by reading the source** (`reference_answer` is `NOT NULL` since 0005, so
three-valued logic cannot open a third bucket; verified in
`alembic_tenant/versions/0005_verified_qa_eval_scenarios.py:82`). It is **not** what the cited test
shows. `test_every_count_travels_with_its_denominator` asserts the identity over
`counts_row=(10, 4, 6, 2)` — numbers the test itself supplies — so it passes whatever the SQL says.
`test_the_queue_selects_exactly_what_the_eval_selector_excludes` asserts only that
`NOT (reference_answer != '')` appears; the un-negated `labelled` filter is asserted nowhere (and an
`in` check for the bare predicate would be satisfied by the negated form as a substring anyway).

### F5 (medium) — LIMIT and OFFSET can be swapped without a test noticing

```
### MX2  LIMIT %(limit)s OFFSET %(offset)s  ->  LIMIT %(offset)s OFFSET %(limit)s
    MUTATED: 54 passed in 23.31s
    RESTORED: 54 passed in 23.55s
```

`test_the_page_reports_its_own_bounds` asserts the contents of the bound params dict, never their
role in the statement. `?limit=5&offset=10` would return 10 rows starting at row 5 while the response
kept reporting `{"limit": 5, "offset": 10}`.

### F6 (medium) — an invisible-character answer defeats the empty-label guard and enters the eval at a human tier

`record_human_label` rejects an empty answer with `str.strip()`, which removes U+00A0 but not the
zero-width characters. **Driven through the real ASGI route** (throwaway probe module, written into
`tests/unit/`, run, and deleted — `TEMP FILE REMOVED: True`):

```
PROBE ascii-space:  status=422 writes=0 stored=None      tier=None            selector_eligible=False
PROBE zwsp-U+200B:  status=200 writes=1 stored='​'  tier='human_authored' selector_eligible=True
PROBE bom-U+FEFF:   status=200 writes=1 stored='﻿'  tier='human_authored' selector_eligible=True
PROBE zwnj-U+200C:  status=200 writes=1 stored='‌'  tier='human_authored' selector_eligible=True
5 passed in 27.57s
```

A zero-width answer passes Pydantic `min_length=1`, passes the strip guard, is bound into the UPDATE
beside `tier='human_authored'`, satisfies `run_eval_suite`'s `WHERE reference_answer != ''` and
satisfies 0016's `COALESCE(reference_answer,'') <> ''` CHECK. The row enters the nightly eval with an
effectively empty reference answer at the highest trust tier — the precise outcome the strip guard
and 0016's second CHECK arm both exist to prevent. A stray zero-width space from a rich-text paste is
the realistic way this arrives, not an attacker. (` ` is correctly rejected; `str.strip()`
covers it.)

### F7 (medium) — the standing trap's residue: `human_authored` is stamped on an X-API-Key request

`get_current_tenant` (`app/api/deps.py:49`) authenticates by Clerk JWT **or** `X-API-Key` — a machine
credential — and does not report which path ran. `assert_human_context()` inspects only in-process
Celery task state and the `agent_tools` ContextVar, so any **out-of-process** caller holding a tenant
API key (a script, a scheduler, a model-driven pipeline) writes `label_trust_tier='human_authored'`
and no restriction in R1–R4 can see it.

This is disclosed — `label_service`'s module docstring names the shape exactly, `_label_principal`'s
docstring names the credential, deviation 4 and `BACKLOG 4.7` record it — but the disclosure is
framed as *"names an account, not a person"* rather than *"a machine credential may stamp a human
tier"*, which is the stronger and more load-bearing statement.

Two things genuinely narrow it, and both should be recorded beside the risk rather than left implicit:
`tenant.api_key_hash` is argon2, so nothing in `app/worker/` or `app/services/` can recover a usable
key from the control DB; and R2 pins that no worker or service module imports `app.api`. The
remaining exposure is an owner-operated automation, and the only structural fix is a principal-aware
dependency that carries `credential_kind` and refuses `human_authored` on the API-key path.

### F8 (low) — `counts.eligible` is `counts.labelled` by assignment, so the payload proves nothing

`_queue_counts_sync` binds `labelled_count` to both keys. The reference doc §3 says "Reporting both
names lets a reader check it from the payload instead of taking it on trust" — there is nothing to
check: if `run_eval_suite`'s predicate changed tomorrow, `eligible` would still equal `labelled` in
every response. The real guard is the cross-module pin (M9, which is legitimate — replacing `!=` with
the semantically identical `<>` still goes red because the pin reads `run_eval_suite`'s literal text).

### F9 (low) — `ordering.keys` describes a query that does not exist

`QUEUE_ORDERING["keys"] == ["origin_trust_tier DESC", "created_at ASC", "id ASC"]`. There is no
`origin_trust_tier` column, and the statement sorts `array_position(...) **ASC**`. A console rendering
the declared keys renders something the database is not doing. (Semantically equivalent, literally
wrong — and F2 shows nothing would catch the two drifting apart.)

### F10 (low) — `dict(QUEUE_ORDERING)` is a shallow copy over a nested list

`test_the_ordering_record_cannot_be_mutated_through_the_response` asserts the string
`"dict(QUEUE_ORDERING)"` is present. That copy is shallow and `keys` is a list, so the constant's
list is still shared. Not reachable over HTTP (FastAPI serialises), but the comparison drawn to
`VERIFIED_QA_PROMOTION_DECISION` does not hold: that constant has no nested mutable.

### F11 (low) — `test_an_empty_answer_is_rejected_without_touching_the_database` does touch the database

For `"   "` and `"\n\t "` Pydantic's `min_length=1` passes, the handler runs, and
`_record_label_sync` calls `psycopg2.connect(...)` **before** `record_human_label` validates and
raises `LabelRejected`. The assertion (no write statement executed) holds; the name does not. Moving
the strip check onto `ScenarioLabelRequest` would make the name true and save a connection.

### F12 (low) — a soft-deleted agent is still labellable

`_resolve_agent_tenant_db` uses `db.get(Agent, agent_id)` with no `Agent.deleted_at.is_(None)`
filter, while `agents.py:226` documents "All read routes already filter on `deleted_at IS NULL`, so a
soft-deleted agent disappears from the API surface." The three older routes in `evals.py` share the
gap; P2 extends it to a **write**.

### F13 (low) — the GET has no fallback for a pre-0011 tenant DB

`_UNLABELLED_QUEUE_SQL` projects `provenance` and `origin_trace_id` (migration 0011) with no
`UndefinedColumn` handler, unlike `_QUEUE_COUNTS_SQL` (0016) and `_LIST_EVAL_RUN_DATASETS_SQL`
(0014). Documented as a deviation and consistent with `_LEDGER_SQL`, but a pre-0011 tenant gets a 500
rather than the "degradation, not an outage" shape the module argues for elsewhere.

### F14 (low) — M8's record cannot be replayed

`except psycopg2.errors.UndefinedColumn` occurs three times in `evals.py` (`list_eval_runs`,
`_queue_counts_sync`, `label_eval_scenario`). M8 names the 503 path, records `1 failed`, and does not
say which occurrence was mutated. Each occurrence is covered by exactly one test, so the proof is
sound for whichever it was — but the row is not reproducible from what it records.

### F15 (nit) — the trace's test count is off by one

`.dev/traces/260809-d6-p2-labelling-queue.md` says "new — 55 tests". The module collects **54**
(observed: `54 tests collected in 27.29s`). The `55 passed` in §7.1b is 54 plus the one-test polluting
module used for the minimum-size reproduction. It is the number a reader would use to check
`1994 + N = 2048`.

### F16 (nit) — `.dev/HANDOFF.md` was not updated for D6

Last touched at `4179a5c`. Its D6 line still reads "mined scenarios are inert by construction", which
`BACKLOG 2.4` now narrows. CLAUDE.md makes HANDOFF the first thing the next session reads.

### F17 (nit) — no upper bound on `reference_answer`

`ScenarioLabelRequest.reference_answer` has `min_length=1` and no `max_length`. Consistent with the
rest of `app/api/v1` (no request model in the tree sets one), but this string is stored and later
interpolated into Ragas judge prompts, so it is the one field where an unbounded body has a
per-token cost.

### F18 (nit) — line references in the reference doc are off by 1–2 and omit the module path

The module is `app/worker/tasks/runtime/validators.py`, not `validators.py`. `get_sync_db` is
imported at `:36` (doc says `:37`), the session opens at `:155` (`:154`), `emit` is called at
`:207-213` (`:203-209`), the `verified_qa` write is at `:368-377` (`:368-376`). **The substance of
all three legs is confirmed correct** — see §2.

---

## 2. The five questions the task asked

**1. Tenant isolation — can one tenant list or label another tenant's scenarios?**
No path found. `_resolve_agent_tenant_db` fetches the agent from the **control** DB and 404s (never
403) on `agent.tenant_id != tenant.id` before decrypting anything; the only database either route
opens is the one behind that agent's own `neon_connection_string`. M2 is a legitimate proof — deleting
the comparison lets the fake agent through to `fernet_decrypt`, and three tests go red.

The obvious second worry — that `SELECT … FROM eval_scenarios` carries no agent predicate — is not a
defect: `provision_neon` calls `create_neon_project(agent_id)` and writes
`agent.neon_connection_string` (`app/worker/tasks/pipeline/provision.py:284`), so the Neon project is
**per agent**, and an agent's connection reaches only its own rows. Two agents of one tenant cannot
see each other's queue either.

Residual, and it is not tenant-crossing: a soft-deleted agent is still reachable (F12), and the
credential that authorises the write may be a machine key (F7).

**2. Is the ordering actually uncertainty, or a proxy presented as one?**
Neither — and that is the right answer. It is declared not-uncertainty in the payload, not only in a
comment, and the declaration is pinned: flipping `by_uncertainty` to `True` goes red
(`1 failed, 53 passed`, my MX7). The three-part unjoinability argument is **confirmed line by line**:
`emit()` receives the `get_sync_db()` session so `job_events` is control-DB
(`validators.py:155`, `:207-213`); `store_scenarios` inserts
`(id, source, question, reference_answer, retrieved_contexts, scenario_category, created_at)` and
nothing else (`scenario_service.py:151-166`); `mine_production_scenarios` selects
`je.job_id, je.payload->>'verdict'` only and sets `scenario_category='production_failure'` for every
row, so no verdict and no confidence survives onto the row (`:387-455`); and
`verified_qa_candidates.auditor_confidence` is written only under
`if verdict.verdict == "grounded" and verdict.confidence >= threshold` (`validators.py:368-377`),
the complement of the `('fail','ungrounded','partial')` population the queue is built from.

One framing correction: the headline "the plan's uncertainty ordering is **NOT implementable**" is
stronger than the evidence. It is not implementable **without a tenant migration and a change to the
miner** — which is what BACKLOG 6.4 now says, and P1 in this same plan did write a migration. The
distinction matters because it is the difference between "impossible" and "not P2's to spend".

What the ordering *is* is undefended in the SQL (F2).

**3. Can the POST write an empty or whitespace-only answer, re-inerting the row while marking it
labelled?**
`""` is a 422 from Pydantic; `"   "` and `"\n\t "` are a 422 from `record_human_label` — but the
connection is already open by then (F11). **`"​"` and `"﻿"` get through** (F6) and produce
exactly the described state: labelled, `human_authored`, selector-eligible, effectively empty.
A label that equals the agent's own failing answer is not preventable and not detectable here; note
that the queue response carries no agent turn, so it cannot be copied out of the queue itself.

**4. Does the selector still exclude unlabelled rows — is its pin still green and still meaningful?**
Yes. `run_eval_suite` still filters `WHERE reference_answer != ''` in all three of its scenario
queries (`eval.py:770`, `:777`, `:785`). `test_the_scenario_is_inert_to_the_eval_selector_by_construction`
in `test_promote_trace.py` is untouched by this branch (last modified at `c9d3ec4`) and green in the
2048. The new cross-module pin is meaningful: M9 replaces `!=` with the semantically identical `<>`
and still goes red, which is what shows it reads the task's literal source.

**5. Are the three counts derivable without their denominator anywhere?**
No. `total` is present in every `counts` object on both routes, and both log lines
(`list_unlabelled_scenarios.ok`, `label_eval_scenario.recorded`) carry `unlabelled` **and** `total`.
M14 (deleting `total`) goes red across 14 tests. The weakness is not the denominator, it is that
`labelled` is not pinned to the selector (F4) and `eligible` is a copy (F8).

---

## 3. What the implementer claimed that I could not fault

- The gate and the ignored-new-files control both reproduce exactly. `1994 + 54 = 2048`, skips
  unchanged at 12, and the control is pass-for-pass identical to the stated `8e3d337` baseline under
  a 582-line change to `evals.py`.
- The encoding repair is real and complete.
- Tenant isolation (question 1) — no defect found.
- The unjoinability finding is correct on all three legs.
- The `human_labelled: null` / `label_provenance_available: false` split, and the 503 naming 0016,
  are the right shape and are properly pinned (M3, M8).
- `extra="forbid"` with a single field, and `labelled_by` derived from the principal, do close the
  pin `BACKLOG 4.7` was filed for (M1, M7).
- The failed first gate run is diagnosed correctly and the fixture does not hide the behaviour it
  suppresses — `test_a_REAL_agent_context_refuses_the_label_and_opens_nothing` drives the genuine
  guard through real ContextVar state, not a patch.
- 12 of the 14 mutation proofs I did not re-run; the 2 I probed adjacent to (M10, M12) behaved as
  described for the mutation as written. M12's *scope* is the problem (F1), not its honesty.

## 4. Mutation ledger for this review

Selector for MX1–MX5, MX7: `.venv/Scripts/python.exe -m pytest tests/unit/test_eval_label_queue.py -q`
Selector for MX6*: the same plus `tests/unit/test_label_provenance.py` (141 tests).
Restore for every row: `git checkout HEAD -- apps/api/app/api/v1/evals.py` inside a `finally:`.

| # | mutation | MUTATED | RESTORED | verdict |
|---|---|---|---|---|
| MX1 | `array_position(...) ASC NULLS LAST` → `DESC NULLS LAST` | `54 passed in 25.18s` | `54 passed in 24.04s` | **survives — F2** |
| MX2 | `LIMIT %(limit)s OFFSET %(offset)s` → params swapped | `54 passed in 23.31s` | `54 passed in 23.55s` | **survives — F5** |
| MX3 | `labelled` FILTER → `WHERE question != ''` | `54 passed in 23.57s` | `54 passed in 23.50s` | **survives — F4** |
| MX4 | `_source_priority_order` sort key sign flipped | `2 failed, 52 passed in 25.93s` | `54 passed in 23.48s` | caught |
| MX5 | `"eligible": labelled_count` → `unlabelled` | `2 failed, 52 passed in 25.74s` | `54 passed in 23.45s` | caught |
| MX6 | `UPDATE public.eval_scenarios … label_trust_tier` appended | `1 failed, 140 passed in 31.65s` (R3, not the P2 test) | `141 passed in 30.02s` | caught by R3 only |
| MX6b | `UPDATE public.eval_scenarios SET reference_answer` appended | `141 passed in 29.28s` | `141 passed in 29.01s` | **survives — F1** |
| MX6c | `UPDATE "eval_scenarios" SET reference_answer` appended | `141 passed in 29.12s` | `141 passed in 29.01s` | **survives — F1** |
| MX6d | table name composed from two fragments | `141 passed in 29.52s` | `141 passed in 28.93s` | **survives — F1** |
| MX7 | `"by_uncertainty": False` → `True` | `1 failed, 53 passed in 25.29s` | `54 passed in 23.50s` | caught |

Tree verified clean after the last restore: `git status --short` and `git diff --stat HEAD` both
empty.
