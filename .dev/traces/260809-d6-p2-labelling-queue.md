# Trace — D6 P2, the labelling queue

**Branch** `feat/d6-labelling-loop` (off `feat/d1-agent-invocation` @ `4179a5c`, **not `main`**)
**Plan** `.dev/plans/260808-d6-labelling-loop.md` § P2
**Reference** `.dev/reference/d6-p2-labelling-queue.md` (the long form: the unjoinability finding, all
mutation proofs verbatim, and what is unproven)

## What changed

| path | |
|---|---|
| `apps/api/app/api/v1/evals.py` | `GET /agents/{id}/eval-scenarios/unlabelled`, `POST /agents/{id}/eval-scenarios/{sid}/label`, `QUEUE_ORDERING`, `SELECTOR_ELIGIBILITY_PREDICATE`, `_source_priority_order`, `_resolve_agent_tenant_db`, `ScenarioLabelRequest`, `_label_principal`, `_record_label_sync` |
| `apps/api/tests/unit/test_eval_label_queue.py` | new — **54 tests** (83 after the review fixes below) |
| `.dev/BACKLOG.md` | `2.4` and `4.7` narrowed, `6.4` re-scoped, `4.9` and `4.10` added |

> The count read **55** here until 2026-08-09. It was the `55 passed` from §7.1b of the reference
> doc — 54 tests plus the one-test polluting module used for the minimum-size reproduction of
> `BACKLOG 4.6`. 54 is the number that satisfies `1994 + N = 2048`; 55 does not, and a reader
> checking the arithmetic would have been the one to discover it.

**No migration.** P2 adds none and needs none: the queue reads columns 0011 already provides, and the
write goes through P1's `label_service` into 0016's columns.

## Decisions

- **The ordering is NOT by uncertainty, and the response says so in its own payload.** The signal is
  not joinable — `job_events` is a control-DB table (`emit()` takes the `get_sync_db()` session) and
  `eval_scenarios` is in the tenant's Neon project, so no SQL join spans them; and there is no key
  either, because `store_scenarios` writes no `job_id` / `conversation_id` / `origin_trace_id` and
  `mine_production_scenarios` discards `payload->>'confidence'` at the point it reads the event. The
  one tenant-side confidence column (`verified_qa_candidates.auditor_confidence`) is written only for
  **grounded** turns above threshold — the complement of this queue's population. Ordering is origin
  trust tier (from `eval_service`'s tables, never restated) then **oldest-first**, and
  `ordering.by_uncertainty: false` travels on every response with the reason attached.
- **The selector is untouched.** `WHERE NOT (SELECTOR_ELIGIBILITY_PREDICATE)` is the queue; the same
  constant is read back out of `run_eval_suite`'s source by a test, so `unlabelled` here and "will
  never be scored" there cannot drift. `counts.eligible == counts.labelled` is the P2 claim made
  readable from the payload rather than asserted in prose.
- **The author is derived, never submitted** — closing the pin `BACKLOG 4.7` was filed for.
  `ScenarioLabelRequest` is `extra="forbid"` with exactly one field, so a body naming `labelled_by`,
  `label_trust_tier`, `labelled_at` or a tier is a 422 rather than a field silently dropped.
- **`labelled_by` names an ACCOUNT, not a person, and says so with a `tenant:` prefix.**
  `get_current_tenant` returns a `Tenant` and does not report which of its two credential paths ran,
  so `tenant.clerk_user_id` would attribute an API-key write to a Clerk user who may not have made
  it. Recording the account is the strongest claim the auth layer supports. `4.7` narrowed to this.
- **Unknown is not zero.** 0016 has been applied nowhere, so `label_trust_tier` does not exist on any
  tenant DB. The counts query falls back and reports `human_labelled: null` with
  `label_provenance_available: false` — the same shape `datasets.available` already uses for
  pre-0014 `dataset`. The label write on that path is a **503 naming migration 0016**, not a 500.
- **One ownership check, not two.** Both routes call `_resolve_agent_tenant_db`; a test asserts
  neither handler re-implements the comparison, and a second test asserts the shared one still makes
  it. Cross-tenant is 404 (never 403) and is proven to reach neither `fernet_decrypt` nor
  `psycopg2.connect`.

## Deviations from the plan

- **P2 §"Order by uncertainty" was not implemented as written.** The plan's own escape clause was
  taken: the signal is not joinable, so it is stated plainly rather than proxied. `BACKLOG 6.4`
  re-scoped from "wire up an ORDER BY" to "schema change + miner change, retroactively empty".
- No index was added for the queue's `WHERE` + `ORDER BY`. Filed as `4.9` rather than guessed at
  without a query plan, which cannot be obtained here.

## The first gate run failed, and it was `BACKLOG 4.6`

The new test file passed 55/55 in isolation and the full suite came back **11 failed, 2036 passed,
12 skipped**. All 11 were POST-path tests, all with the same cause in the log:
`refused_context ... agent_id='agent-reset-test'` — the `_agent_id_var` that
`tests/unit/test_agent_tools.py:686` leaks across the whole pytest process. The route's R4 guard was
correct on a stale fact; the tests had not established the precondition they claimed to exercise.
Reproduced at minimum size (a two-line polluting module: `11 failed, 43 passed` → `55 passed`), then
fixed with the same autouse fixture `test_label_provenance.py` already needed. The behaviour the
fixture suppresses is pinned separately by
`test_a_REAL_agent_context_refuses_the_label_and_opens_nothing`, which drives the genuine guard
rather than a patched one. `4.6` updated with the evidence — second module, second identical fixture.

## The adversarial review, and what it changed (2026-08-09)

18 findings, in `.dev/reference/d6-p2-adversarial-review.md`. Fixed on the same branch, commit
`17a5774`; what was done about each, with the deviations, is `.dev/reference/d6-p2-review-fixes.md`,
and the 14 new mutation proofs are in the reference doc §7.6.

- **Four behaviour mutations survived the 54 tests this trace reported as proof.** The
  `array_position` sort DIRECTION (reversing it inverts the queue), the `LIMIT`/`OFFSET` binding, the
  `labelled` count FILTER (breaking `unlabelled + labelled == total` in Postgres), and three of four
  spellings of a forged `eval_scenarios` write appended to `evals.py`. All four are pinned now.
- **The write reached any scenario in the agent's DB, not only an unlabelled one.** One POST could
  overwrite a curated golden-set reference answer with no record of what had been there. The UPDATE
  is scoped by the negation of the selector predicate; an already-answered row is a 409 told apart
  from the 404 by an existence probe run only on the zero-row path.
- **A machine credential could stamp `human_authored`.** `get_current_tenant` accepts `X-API-Key` and
  `label_service`'s R1-R4 are all in-process facts. `get_credential_kind` now reports which credential
  resolved and the route refuses anything but a Clerk JWT. This is the phase's central claim finally
  being enforced rather than asserted; `4.7` rewritten accordingly.
- **`str.strip()` does not remove zero-width characters**, so a `"\u200b"` answer was accepted, stamped
  human, and satisfied both the selector and 0016's CHECK. Emptiness is now decided on Unicode
  category — and the check moved onto the request model, so the test named "without touching the
  database" is true for the first time.
- **A soft-deleted agent was still labellable** (`db.get()` cannot filter `deleted_at`).
- `QUEUE_ORDERING["keys"]` described a query that does not exist and is now parsed out of the
  statement; `dict()` was a shallow copy over a nested list; `counts.eligible` is `labelled` by Python
  assignment and the docs claimed a reader could check the identity from the payload.

Gate after the fixes: **2077 passed, 12 skipped** (`1994 + 83`). Ignored-new-files control:
**1994 passed, 12 skipped** — identical to the pre-fix control, so nothing outside the queue module
changed status despite `deps.py` being touched.

## Not proven

No PostgreSQL on this machine. **No query in this phase has been executed by a database.**
`array_position(...) NULLS LAST`, the `FILTER (WHERE ...)` counts and the identity
`unlabelled + labelled == total` are asserted at the SQL-string level and against a recording
cursor; the row order Postgres would produce has never been observed. Migration 0016 has never been
applied, so the 200 path of the label write has never touched a real `label_trust_tier` column —
what was observed is the statement it emits and the parameters it binds. Every `-m integration`
harness skips, and a skip is unobserved.
