# D6 P2 — the review fixes: every finding, what was done, and what was observed

**Branch** `feat/d6-labelling-loop`, off `feat/d1-agent-invocation` (`4179a5c`), **not `main`**
**Fixes commit** `17a5774` — `fix(eval): the label queue's guards, demonstrated outside their own blind spots`
**Input** the 2026-08-09 adversarial review of P2 (18 findings, 7 unsupported claims)
**Companion** `.dev/reference/d6-p2-labelling-queue.md` — corrected in place, with §7.6 carrying the
fourteen new mutation proofs
**Persisted here per `BACKLOG 2.20`.** The previous branch lost 17 of 48 findings to a temp-directory
journal that did not survive its session.

---

## 0. The gates, first, because everything below is worth what they are worth

Run from `apps/api`, exactly the form CLAUDE.md specifies:

```
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py

2077 passed, 12 skipped, 28 warnings in 369.61s (0:06:09)
```

The ignored-new-files control (`BACKLOG 2.26`) — the same command with the phase's test module also
ignored, so what is left is only pre-existing tests running against the modified source:

```
  --ignore=tests/unit/test_eval_label_queue.py

1994 passed, 12 skipped, 28 warnings in 387.82s (0:06:27)
```

**1994 is identical to the control observed at `44f0ad5`**, pass for pass and skip for skip. That is
the claim test-count arithmetic cannot make, and it is load-bearing here because these fixes touched
`app/api/deps.py` — the authentication dependency **every** authenticated route in the application
resolves. If adding `request: Request` to `get_current_tenant`, or setting
`request.state.credential_kind`, had broken any of the 40-odd route modules' tests, this run is where
it would show.

`1994 + 83 = 2077`, and `test_eval_label_queue.py` collects exactly 83 (it collected 54).
`test_label_provenance.py` was **modified but not extended** — its recording cursor gained a
`fetchall`, and two assertions were updated for the writer's new `already_labelled` outcome — so its
count is unchanged at 87 and the control's 1994 is comparable to the pre-fix one.

Skips unchanged at 12. **No `-m integration` harness became runnable and none could:** there is no
PostgreSQL server on this machine, and a skip is unobserved, never a pass.

---

## 1. The findings, and what was done about each

### The three highs

**F1 — the second-write-path guard was demonstrated inside its own blind spot.**
`test_this_module_issues_no_write_of_its_own_to_eval_scenarios` uppercased the file and looked for
two literal markers. The review appended a bare `eval_scenarios` write to `evals.py` in four
spellings: the plain one went red, and schema-qualified, quoted-identifier and composed-from-fragments
all passed 141 tests. Fixed by borrowing `test_label_provenance._scenario_write_statements` (the AST
reconstruction that models f-strings, `+`, `%`, `.format`, `.join`, `public.` and quoted identifiers).

**And the review's suggested fix was not sufficient on its own — verified, not assumed.** Run against
`_TBL = "eval_" + "scenarios"` / `f"UPDATE {_TBL} SET ..."`, that detector returns `[]`: it still has
to RECOGNISE THE TABLE, and the composed name reassembles as `UPDATE  SET ...`. So a **second scan**
was added with the opposite blind spot — `_write_verbs_in`, which asks only whether a SQL write verb
is being built at all, needing no model of the table name, and which `evals.py` can satisfy because it
is a read module whose single write is delegated. The division of labour is itself pinned by
`test_the_table_aware_scan_has_this_exact_blind_spot`, so nobody deletes the verb scan as redundant.
Proofs `M18` (composed — verb scan only) and `M19` (schema-qualified — both).

**F2 — the queue's headline ordering property was pinned by nothing.** Reversing
`array_position(...) ASC NULLS LAST` to `DESC` inverts the queue (`generated` first, `mined` last) and
passed all 54 tests. Fixed by parsing the ORDER BY once (`_order_by_keys`) and comparing the whole key
list, so direction, `NULLS LAST`, `created_at ASC` and the `id` tiebreak all read the same parse.
Proof `M15` (three tests red).

**F3 — the write reached any scenario in the agent's database, not only an unlabelled one.**
`_LABEL_SQL`'s WHERE was `id = %(scenario_id)s::uuid` alone, so one POST silently replaced an existing
`reference_answer` and re-stamped its provenance with no record of what had been there. Worst on a
`dataset='golden'` row: `eval.py` runs the golden half in full every night precisely so consecutive
runs are a **paired per-item comparison**, and moving one item's reference answer breaks that
comparison while the run report has no way to say so.

Fixed the first way the review offered — scope the write to the queue's own population — because the
second (allow relabelling, refuse golden, record the superseded answer) is a feature with its own
product questions and no owner decision behind it. The UPDATE now carries
`AND NOT (SELECTOR_ELIGIBILITY_PREDICATE)`; zero rows then has two causes and `record_human_label`
runs a `SELECT 1` probe **only on that path** to tell them apart, returning `already_labelled`; the
route maps it to a **409** distinguishable from the 404. Proofs `M20`, `M27`.

Consequence worth stating: `SELECTOR_ELIGIBILITY_PREDICATE` moved from `evals.py` to `eval_service.py`.
`label_service` needs the same string and may not import `app.api` (R2), so the module both sides
already import is the only place one spelling can serve all three. `evals.py` re-exports the name.

### The four mediums

**F4 — the counts identity had no test behind it.** Replacing the `labelled` FILTER with
`WHERE question != ''` — which makes `unlabelled + labelled == total` FALSE in Postgres — passed
everything, because the test asserting the identity does so over numbers it supplies itself. Both
FILTERs are now **counted** (exactly one negated, exactly one plain) in both counts statements;
counting rather than checking presence matters because the negated form contains the plain form's
text as a substring. Proof `M17`.

**F5 — `LIMIT` and `OFFSET` could be swapped** while the response went on reporting the caller's
requested bounds. Pinned literally. Proof `M16`.

**F6 — `str.strip()` does not remove Cf**, so a zero-width answer was accepted, stamped
`human_authored`, and satisfied both `run_eval_suite`'s `reference_answer != ''` and 0016's CHECK —
re-inerting the row while marking it labelled, which is exactly the state the guard exists to prevent.
Emptiness is now decided on Unicode general category (`Cc`/`Cf`/`Zl`/`Zp`/`Zs`) in
`label_service.visible_answer`, one function used by both the request model and the writer. The rule
is "carries at least one visible character", not "is free of invisible ones" — a negative control
pins that a real answer padded with a pasted `U+200B` is still stored. Proof `M22`.

**F7 — the phase's central claim was not enforceable at the auth layer.** `get_current_tenant`
accepts `X-API-Key`, a machine credential, and `label_service`'s R1-R4 read a parameter list, an
import graph, Celery's thread-local task stack and an `agent_tools` ContextVar — **all in-process
facts** that an out-of-process automation trips none of. So any script or scheduler holding a tenant
key could store model prose as `human_authored`, the tier `VERIFIED_QA_MIN_TRUST_TIER` is defined
over.

**The structural fix was taken, not the documentation-only interim the finding offered as a
fallback.** `get_current_tenant` records which path resolved on `request.state.credential_kind`;
`get_credential_kind` is a dependency **of** `get_current_tenant` (so the ordering is a property of
the dependency graph, not of parameter order in whichever handler declares both) that reports it; and
`label_eval_scenario` refuses anything but `CREDENTIAL_CLERK_JWT` with a **403**, `CREDENTIAL_UNKNOWN`
included, because "cannot tell" must never resolve to "human". The GET is deliberately not gated:
reading the queue asserts nothing about who is reading. Proof `M21`.

Design notes, because this touched shared auth:
- The kind is **attached**, not returned, so no existing caller's type changed and no other route was
  affected — confirmed by the 1994 control.
- Every test driving the POST now declares which credential it simulates (`_override_auth`). That is
  not boilerplate: leaving it to default gives CREDENTIAL_UNKNOWN and a fail-closed 403, so a test
  that does not say is a test asserting against a precondition it never established — the same
  mistake `BACKLOG 4.6`'s fixture was added to stop making.
- `get_current_tenant` is exercised directly for BOTH credential paths with a hand-built `Request`, so
  `get_credential_kind` cannot be reading a value nothing ever writes.

### The six lows

**F8 — `counts.eligible` is `counts.labelled` by Python assignment**, so the doc's "lets a reader
check it from the payload instead of taking it on trust" invited verification by tautology. The field
is kept (a console wants the number under the name the eval uses) and the docstring and reference doc
now say plainly that the **cross-module pin**, not the payload, is what holds the identity. `M9`
remains the real proof of it. No test added: nothing about the behaviour changed.

**F9 — `QUEUE_ORDERING["keys"]` described a query that does not exist**, naming `origin_trust_tier`
(not a column) and `DESC` (not the direction used). Now derived from the statement by the same parse
that fixes F2, so the payload cannot drift from the query at all.

**F10 — `dict(QUEUE_ORDERING)` is shallow and `keys` is a list**, so the "copy" shared it. Deep-copied,
and asserted **behaviourally** — the handler is awaited directly, because driving it over HTTP would
serialise the response and hide exactly the aliasing in question. Proof `M26`.

**F11 — `test_an_empty_answer_is_rejected_without_touching_the_database` touched the database.** The
check moved onto `ScenarioLabelRequest` as a `field_validator`, so a refused body never decrypts a
connection string. **The test's assertion was also strengthened, and it had to be:** it asserted "no
write statement executed", which holds even when a connection IS opened, because
`record_human_label` raises before opening a cursor. The recording connection now counts `connects`,
and the refusal paths (empty, oversized, wrong credential) assert `connects == 0`. Proof `M23` — seven
of the eight parametrisations red, all but `""`, which `min_length=1` still catches at the boundary.

**F12 — a soft-deleted agent was still labellable.** `db.get()` cannot express the filter, so
`DELETE /agents/{id}` followed by a label POST decrypted a deleted agent's connection string and wrote
into its tenant database — against the invariant `agents.py:226` states for the whole API surface. Now
`select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))`. The test asserts both halves:
the 404, **and** that the statement issued really carries `deleted_at IS NULL` — against a mock, which
returns whatever it is told to, the second assertion is the only one that means anything. The three
older read routes in the module share the gap; fixing them is a separate decision, deliberately not
taken. Proof `M24`.

**F13 — M8's record could not be replayed.** `except psycopg2.errors.UndefinedColumn` occurs three
times in `evals.py` and the row recorded only `1 failed`. The `label_eval_scenario` occurrence — the
one the row's title is about — was re-run with the failing identity captured, as `M8b`.

### The four nits

**F14** — the trace said the module was 55 tests; it collected 54. 55 was the `55 passed` from §7.1b,
which is 54 plus the one-test polluting module used for the `BACKLOG 4.6` reproduction. Corrected,
with the explanation, because 54 is the number that satisfies `1994 + N = 2048`.

**F15** — `HANDOFF.md` had not been updated for D6 P1 or P2 and still described the state `2.4` has
narrowed. A D6 block was added at the top: routes landed, 0016 applied nowhere so every label attempt
is a 503 in fact, P3/P4 unstarted, stacked on unmerged D1.

**F16** — `reference_answer` had no `max_length`. Bounded at 8000. This is not generic input hygiene:
it is the one stored field `run_eval_suite` interpolates into a paid judge's prompt on **every**
nightly run for as long as the row lives, so an oversized label is a recurring cost, not a one-off
write. Proof `M25`.

**F17** — the unjoinability evidence cited bare `validators.py` (there is no `app/services/validators.py`)
with four line numbers off by one or two. Corrected to `app/worker/tasks/runtime/validators.py` with
`:37`, `:155`, `:207-213`, `:368-377` re-checked against the file. The substance of all three legs was
correct.

---

## 2. The unsupported claims

Six of the seven correspond to findings above and were corrected in place in the reference doc, each
marked **[CORRECTED]**. Two deserve their own note:

**"THE HEADLINE FINDING — the plan's uncertainty ordering is NOT implementable."** The three legs are
each correct; the conclusion is stronger than they support. What they establish is *not implementable
without a tenant migration and a change to the miner* — which is what the re-scoped `BACKLOG 6.4` then
says, and P1 of this same plan did write a tenant migration. Corrected: the distinction is between
*impossible* and *not P2's to spend*, and only the second is proven. **This one was outside the
bounded findings list** and is recorded as a deviation below.

**"14/14 mutation proofs red-then-green" as evidence that the phase's guards are pinned.** Each of
the fourteen is honest — the review reproduced them and found no fabricated red and no missing
restore. But 14/14 is a statement about the mutations that were **chosen**. Four mutations the review
chose instead survived, and they covered the three properties the report's prose leaned on hardest.
That framing is now recorded in §7.4 of the reference doc, next to the original claim, because it is
the most transferable lesson here: *a mutation ledger measures the ledger's author's imagination, not
the suite's coverage.*

---

## 3. Mutation proofs

Fourteen, all red-then-green, restored **from `HEAD` unconditionally** in a `finally:`. Verbatim
pytest lines, FAILED identities, exact selectors and the harness's own safety properties are in
`.dev/reference/d6-p2-labelling-queue.md` §7.6. Summary:

| # | guard | RED |
|---|---|---|
| M15 | the priority key sorts the best origin first | 3 failed, 80 passed |
| M16 | limit binds to LIMIT, offset to OFFSET | 1 failed, 82 passed |
| M17 | the `labelled` FILTER is the selector predicate | 1 failed, 82 passed |
| M18 | no second write path — composed spelling | 1 failed, 169 passed |
| M19 | no second write path — schema-qualified spelling | 2 failed, 168 passed |
| M20 | the UPDATE is scoped to an unlabelled row | 2 failed, 168 passed |
| M21 | only a Clerk JWT may stamp a human tier | 3 failed, 80 passed |
| M22 | emptiness is decided on Unicode category | 4 failed, 166 passed |
| M23 | the emptiness check is at the boundary | 7 failed, 76 passed |
| M24 | a soft-deleted agent is not resolvable | 2 failed, 81 passed |
| M25 | the reference answer is bounded | 1 failed, 82 passed |
| M26 | QUEUE_ORDERING is deep-copied | 1 failed, 82 passed |
| M27 | the probe tells a relabel from a missing row | 1 failed, 82 passed |
| M8b | the 503 naming 0016, `label_eval_scenario` occurrence | 1 failed, 82 passed |

The harness (`mutate.py`) refuses an anchor matching other than exactly once and refuses a mutation
that changes nothing, so a silently-no-op "proof" cannot be recorded. After every run
`git status --short` showed only untracked review notes and `git diff --stat HEAD` was empty.

---

## 4. Deviations

1. **F1's suggested fix was extended, not just applied.** Reusing `_scenario_write_statements` alone
   still leaves the composed-table-name spelling invisible — demonstrated, not assumed — so a second
   verb-level scan was added and the first scan's blind spot was written down as its own test.
2. **F3 was fixed by scoping, not by making relabelling explicit.** The finding offered both. The
   second is a feature with product questions (which answer is superseded, may a golden row move, who
   confirms) and no owner decision behind it; the first restores the invariant the route was
   documented as having. Relabelling stays unavailable rather than becoming half-designed.
3. **F7 was fixed structurally rather than by restating the residue in stronger prose.** The finding
   offered documentation as the interim. Leaving D6's central claim unenforced for a documentation fix
   seemed the wrong trade, and the 1994 control is the evidence that touching shared auth cost
   nothing elsewhere.
4. **F8 kept the field.** The finding offered "compute it independently" or "drop it and fix the
   docstring". Computing it independently from the same predicate string would be a second tautology;
   dropping it removes a number a console will want. The prose was corrected instead, in three places.
5. **One unsupported claim outside the bounded findings list was corrected** — the "NOT implementable"
   headline (§2 above). It is a one-paragraph correction in a document already being corrected for
   F13 and F17, and leaving a known overstatement in place while editing around it would have been
   the odd choice.
6. **`test_label_provenance.py` was modified.** Not scope creep: `record_human_label`'s return value
   and statement changed, so its own tests had to follow. It was deliberately **not extended** — no
   test added or removed — so the ignored-new-files control's 1994 stays comparable.
7. **`SELECTOR_ELIGIBILITY_PREDICATE` moved to `eval_service`.** Required by F3: the writer needs the
   predicate and may not import `app.api`.

---

## 5. What is still not proven

- **No PostgreSQL server on this machine, so nothing below has been executed by a database.** Every
  `-m integration` harness skips, and a skip is unobserved, never a pass. `CONTROL_DB_URL` points at
  live Neon production and is never a substitute.
- **No migration was written and none could have been applied.** 0016 remains applied to no database,
  so on a real tenant today every label attempt is still the 503.
- **The scoped UPDATE has never been planned or executed.** In particular the two-statement
  write-then-probe is not `FOR UPDATE`; under READ COMMITTED a losing concurrent labeller's UPDATE
  re-evaluates the predicate and matches nothing, giving the intended 409 — but that is reading the
  manual, not an observation. Filed as `BACKLOG 4.11`.
- **`request.state.credential_kind` has never been read in a real ASGI process.** It is exercised via
  `get_current_tenant` called directly with a hand-built `Request`, and via dependency overrides in
  the route tests. A genuine Clerk JWT arriving over HTTP needs a live JWKS.
- **`MAX_REFERENCE_ANSWER_CHARS = 8000` is a judgement, not a measurement.** Nothing measured what a
  real reference answer costs in a Ragas prompt; the bound is generous enough that a wrong guess
  surfaces as a 422 rather than as silent truncation.
- **The 403 changes the contract for API-key callers of this route.** There are none — the route is
  days old and P4 is unstarted — so nothing was broken. If an automation is later wanted here the
  answer is `human_verified` and its own writer (0016's CHECK already admits the value), not widening
  this gate.
