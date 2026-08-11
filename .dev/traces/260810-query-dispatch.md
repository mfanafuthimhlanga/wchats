# Trace — query-route dispatch args (BACKLOG 1.8)

**2026-08-10 · `chore/local-postgres` · commits `4164fe6`, `013cac4`**
Written 2026-08-11 to close the trace gap the adversarial review flagged. Reconstructed from
`.dev/reference/query-route-dispatch-args.md`; a record, not a fresh investigation.

## Verdict: test defect. The product is unchanged.

`test_query_route.py::test_post_query_returns_202` failed with
`agent_id not found in task args: []`. `app/api/v1/query.py:110` has always dispatched
`args=[str(job.id), str(agent.id), body.query]` correctly.

The failing line was an operator-precedence bug in the assertion's own helper:

```python
task_args = call_kwargs[1].get("args") or call_kwargs[0][0] if call_kwargs[0] else []
```

Python binds the conditional looser than `or`, so it parses as
`(kwargs.get("args") or positional[0]) if positional else []`. The route passes `args=` as a
**keyword**, so the positional tuple is `()` — falsy — and the guard short-circuited to `[]`
before ever reading the kwargs dict it was written to read. Reproduced in isolation:

```
call_args[0] (positional) = ()
call_args[1] (kwargs)     = {'args': ['j', 'a', 'q'], 'queue': 'runtime'}
test line 234 evaluates to = []
```

## What changed

- `tests/integration/test_query_route.py` — the extraction reads kwargs first, then positionals,
  with the precedence made explicit.

## Deviation

`1.8` framed the question as "either the assertion reads the wrong surface or dispatch genuinely
drops the arg". It was the first, and the row is now deleted per the maintenance rule.

## Not done, and carried forward

The sweep for other unprefixed `/api/v1` paths was listed as not-done. Two remained
(`test_agent_chat_integration.py`, `test_agent_e2e.py`), both invisible because their modules are
behind env flags. Fixed 2026-08-11 at `d4f65e2`, with `tests/unit/test_test_route_paths_resolve.py`
added so the next one cannot hide behind a skip.
