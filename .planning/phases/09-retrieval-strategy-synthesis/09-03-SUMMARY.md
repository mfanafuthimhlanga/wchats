# Phase 09-03 Summary — De-xfail Unit Tests

## Objective
De-xfail and implement all 9 Wave-0 unit test stubs for M9 Retrieval Strategy Synthesis.

## Test Results

### M9 tests (combined run)
```
9 passed, 0 failed, 0 xfailed
```

### test_strategy_service.py (5 tests)
| Test | Result |
|------|--------|
| test_corpus_signals_shape | PASSED |
| test_strategy_validate_string_inputs | PASSED |
| test_run_strategist_calls_asyncio_run | PASSED |
| test_expand_query_returns_three | PASSED |
| test_expansion_calls_rrf_fuse_per_variant | PASSED |

### test_strategy_task.py (4 tests)
| Test | Result |
|------|--------|
| test_strategy_written_to_db | PASSED |
| test_receives_embed_result_dict | PASSED |
| test_idempotency_skip | PASSED |
| test_resynthesis_flag_bypasses_guard | PASSED |

## Commit SHAs

| Commit | SHA |
|--------|-----|
| 5 service-layer tests | `2334599` |
| 4 task-layer tests | `a28c34d` |
| Regression verification | `bd220ce` |

## Broader Unit Suite Outcome

```
354 passed, 49 failed, 23 warnings in 80.79s
```

### New failures traceable to Phase 9 changes
**None.** Zero failures in `test_strategy_service.py`, `test_strategy_task.py`, or any module
touched by strategy_service.py, retrieval_service.py, or strategy.py.

### Pre-existing failures (out of scope per plan)
All 49 failures are pre-existing from Phase 8 and earlier:
- `test_agent_chat_routes` (8 failures)
- `test_agent_task` (4 failures)
- `test_agent_tools` (11 failures)
- `test_agents_list_and_widget_config` (1 failure)
- `test_agents_patch` (5 failures)
- `test_chunking_service` (7 failures)
- `test_docling_service` (1 failure)
- `test_eval_routes` (1 failure)
- `test_jobs_routes` (2 failures)
- `test_parse_task` (2 failures)
- `test_services` (2 failures)
- `test_tenants_route` (4 failures)
- `test_strategy_service` — 0 failures
- `test_strategy_task` — 0 failures

## Implementation Notes

### Service-layer tests (test_strategy_service.py)
- `test_corpus_signals_shape`: Used a multi-cursor mock factory (`_make_psycopg2_conn_multi`)
  to return different `fetchone` values per cursor call, matching the 4-query structure of
  `_fetch_corpus_signals_sync`. Includes defensive coercion check (None → 0.0).
- `test_strategy_validate_string_inputs`: Verified Pydantic coerces `"30"` → `30` (int) and
  `"true"` → `True` (bool) via `RetrievalStrategy.model_validate`.
- `test_run_strategist_calls_asyncio_run`: Patched `app.services.strategy_service.asyncio.run`
  at module boundary; confirmed `assert_called_once()`.
- `test_expand_query_returns_three`: Injected mock `anthropic` module into `sys.modules` to
  intercept the lazy `import anthropic` inside `_expand_query`; restored after test.
- `test_expansion_calls_rrf_fuse_per_variant`: Patched `_expand_query`, `rrf_fuse`, and
  `_get_vo` at `app.services.retrieval_service`; confirmed `rrf_fuse.call_count == 3`.

### Task-layer tests (test_strategy_task.py)
- All patches target `app.worker.tasks.pipeline.strategy.*` (module boundary), following
  the exact pattern from `test_deployment_task.py`.
- `asyncio.run` patched with `side_effect=lambda coro: coro.close()` to suppress
  `RuntimeWarning: coroutine was never awaited` while allowing the task to proceed to
  the Pydantic validation / DB write steps with an empty `result_container`.
- `test_strategy_written_to_db`: Confirms `agent.retrieval_strategy` is set to RetrievalStrategy
  defaults (non-empty dict) and `db.commit()` is called.
- `test_receives_embed_result_dict`: Part A validates chain pass-through; Part B validates
  that missing `agent_id` causes early return without DB commit.
- `test_idempotency_skip`: Confirms neither `_fetch_corpus_signals_sync` nor `asyncio.run`
  is called when strategy is already populated and flag is False.
- `test_resynthesis_flag_bypasses_guard`: Confirms `asyncio.run` IS called when
  `strategy_resynthesis_flagged=True`, and that the flag is cleared to `False` after.

## Issues
None. All 9 tests pass cleanly. Runtime warnings (`coroutine was never awaited`) are
expected side-effects of the `coro.close()` mock pattern and do not indicate test failures.
