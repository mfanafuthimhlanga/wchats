# Query-route dispatch args: the assertion was the defect, not the product

Task C, branch `chore/local-postgres`, 2026-08-10. Commit `4164fe6`.
Selector: `apps/api/tests/integration/test_query_route.py::test_post_query_returns_202`.

## Verdict: TEST DEFECT. The product is correct.

`POST /api/v1/agents/{id}/query` failed at `test_query_route.py:235` with
`agent_id not found in task args: []`. The dispatch never dropped `agent_id`.
`app/api/v1/query.py:110` has always been right:

```python
retrieve_and_rank.apply_async(
    args=[str(job.id), str(agent.id), body.query],
    queue="runtime",
)
```

The failing line was:

```python
task_args = call_kwargs[1].get("args") or call_kwargs[0][0] if call_kwargs[0] else []
```

Python binds the conditional expression looser than `or`, so this parses as:

```python
task_args = (call_kwargs[1].get("args") or call_kwargs[0][0]) if call_kwargs[0] else []
```

The route passes `args=` as a **keyword**, so `call_args[0]` (the positional
tuple) is `()` — falsy. The guard short-circuited to `[]` *before ever reading
the kwargs dict it was written to read*. Reproduced in isolation:

```
call_args[0] (positional) = ()
call_args[1] (kwargs)     = {'args': ['j', 'a', 'q'], 'queue': 'runtime'}
test line 234 evaluates to = []
```

The captured stdout in the failure report — `query_agent.dispatched
agent_id=38987b05-... job_id=36944dba-...` — was the tell: the route knew the
agent_id and put it in the args; the test was looking at the wrong tuple.

## Why this cost a whole branch

The expression had a **silent empty default**. `... else []` turned "I could not
read this call" into "the call carried nothing", which reads identically to a
product that dropped the field. A test-harness bug wore the costume of a
product bug. The replacement helper `_celery_task_args()` **raises** when it
cannot find task args in either position. Rule worth generalising: *an
extractor in a test must never have a falsy default* — fail loudly or the
diagnosis points at the wrong file.

## What replaced it

`_celery_task_args()` (test_query_route.py:141) reads `call.kwargs["args"]`
first, falls back to `call.args[0]`, raises otherwise. Assertions are now
**positional against the task signature** `retrieve_and_rank(self, job_id,
agent_id, query)` rather than `in task_args`. This matters: `job_id` and
`agent_id` are both UUID strings, so a membership check passes even when the
two are transposed and the worker loads the wrong row — mutation B below is
the case the original assertion could not have caught *even had it evaluated
correctly*. Also pins `queue="runtime"` and asserts no arg carries
`postgresql://` (project rule 1: connection strings never in Celery task args).

## Sibling defect found in the same file: a 404 passing for the wrong reason

`test_post_query_agent_not_found` posted to `/agents/{id}/query` with **no
`/api/v1` prefix**. It was in the "9 passed" column, but it was passing on a
Starlette routing 404 (`{"detail":"Not Found"}`), not on the agent-ownership
404. It would have passed with the `Agent.tenant_id == tenant.id` filter
deleted from `post_agent_query` outright — a cross-tenant read guard with no
test behind it. Fixed the path and pinned `detail == "Agent not found"`.

This is the second instance on this branch of a missing `/api/v1` prefix
producing a **false green** rather than a red. Worth grepping for: any
integration test asserting a 4xx against an unprefixed path is suspect.

## Mutation proofs (all run, verbatim)

Guard 1 — dispatch carries agent_id.
Selector `::test_post_query_returns_202`.

- **A: drop `agent_id`** — `args=[str(job.id), body.query]` in query.py:111.
  RED: `AssertionError: retrieve_and_rank takes (job_id, agent_id, query);
  dispatch carried 2 args: ['35f435a1-adeb-40e4-8bbc-b8c9fd165d95', 'What is
  the refund policy?']` / `assert 2 == 3` — `1 failed in 42.42s`.
  Restored `git checkout HEAD -- app/api/v1/query.py` → `1 passed in 45.27s`.
- **B: transpose job_id/agent_id** — `args=[str(agent.id), str(job.id),
  body.query]`. RED: `AssertionError: task arg 0 should be the response job_id
  fe659f5e-a8ea-486a-a82c-5a2e73715d72, got '8f34b812-d4e0-48fb-a9d6-c7105e385805'`
  — `1 failed in 43.78s`.
  Restored from HEAD → `1 passed in 43.55s`.

Guard 2 — the 404 is the ownership 404.
Selector `::test_post_query_agent_not_found`.

- **C: revert path to unprefixed** `/agents/{uuid}/query` (the pre-existing
  state). RED: `AssertionError: 404 must originate from the agent-ownership
  check, not a routing miss; got body: {"detail":"Not Found"}` /
  `assert 'Not Found' == 'Agent not found'` — `1 failed in 81.51s`.
  Restored `git checkout HEAD -- apps/api/tests/integration/test_query_route.py`
  → `1 passed in 36.29s`.

## Suite results observed (not asserted)

- Integration, full: `1 failed, 14 passed, 22 skipped, 24 deselected in 219.31s
  (0:03:39)`. Baseline for this task was 10F/9P/21S — the improvement is mostly
  other tasks' commits landing on the branch concurrently, not this change
  alone; this change accounts for `test_query_route.py` going 1F/2P → 3P.
  Sole remaining failure: `test_sse.py::test_sse_receives_live_events_after_replay`
  (asyncio `TimeoutError`), untouched by this work and outside its scope.
- Unit gate: `2127 passed, 12 skipped, 30 warnings in 640.29s (0:10:40)`, zero
  failures. Baseline was 2112 passed; the delta is tests added by concurrent
  tasks. No regression.
- Neon: **zero projects created, zero deleted.** All 8 baseline projects in
  `C:/Users/Bantu/pg-setup/neon-baseline.txt` verified present via
  `GET https://console.neon.tech/api/v2/projects` — live count 8, missing NONE,
  extra NONE. Nothing in this change path touches Neon.
