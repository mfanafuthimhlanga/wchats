# IngestionJob as the chain's seam (#43)

Ticket #43, decision #7 on map #4. On `feat/ingestion-job`, stacked on
`feat/chunk-types` (#42): the seam (`4eb3325`), the review round (`3b795f8`). #43
closes with the seam commit.

## The seam

`app/domain/ingestion_job.py`: `IngestionJob(tenant_id, agent_id, job_id,
document_ids)`, frozen. Construction refuses a falsy id and a missing or `None`
`document_ids` by raising `InvalidJobDict`; a `document_ids` that is present but not a
list or tuple raises `TypeError`, because `tuple("abc")` would otherwise become three
fake ids silently. The wire stays the four-key dict through `to_dict`/`from_dict`
(extras ignored; older hops may add keys).

`app/worker/tasks/pipeline/chain_edge.py`: `job_in_job_out` wraps chunk, metadata,
embed and strategy. The Celery decorator sits outermost so `bind`, `acks_late` and
retries survive; the edge deserializes, calls the typed core, serializes back, and
catches `InvalidJobDict` alone, logging the same `<task>.invalid_result_dict` event and
returning the input untouched, exactly the old defensive path. Everything else fails
the task loudly, as before the seam. parse is the head: it takes the four ids where
they enter, builds the job, and raises on an empty id instead of running with it.

The cores stay on their pinned lizard names under the decorator rather than moving to
`_core` functions, because the baseline pins by (file, function) and never adds; all
four pins shrank instead.

## What review changed

- The edge originally caught `(TypeError, ValueError)`, one case too wide: a wire dict
  with `document_ids: 42` was logged and returned as SUCCESS where the old code crashed
  loudly. `InvalidJobDict` narrows the catch; the 42 and "abc" cases now fail the task,
  red-first.
- The wire had a fifth consumer the ticket did not name: `synthesize_retrieval_strategy`
  still read `result.get(...)` behind its own guard, so a `to_dict` key rename would
  have made it silently no-op. It joined the seam; the drive now runs five hops and
  asserts the embed to strategy join. The red run made the reviewer's point exactly:
  the join assertion passed while strategy forwarded the dict blindly.
- The registered task's annotations claimed `IngestionJob -> IngestionJob` through
  `functools.wraps`; they now say dict. The edge's logger-name claim is pinned by a
  test after measuring that structlog's capture drops logger names.

## Facts recorded, no code change

- An empty id reaching parse is unreachable from the one dispatcher
  (`documents.py:259`, all ids `str(uuid)`); if a future caller manages it, the failure
  lands in issue #63's retry-exhaustion invisibility class.
- `job.complete` is not the run's last event; embed emits it and strategy runs after.
  Commented onto #63.
- AC1's letter says all four tasks take the typed job; parse takes the four ids and
  works in the type from its first line, which is the head shape decision #7 implies.
- Test helpers (`JOB`, the core-seam probe, the invalid-dict case) are triplicated
  across the chunk, metadata and embed test files; each copy pins its task's own event
  name.

## Observed

- Seam commit: `full gates passed in 690.8s.`, whole suite `2458 passed, 13 skipped`.
- Review round: the seven driving files `96 passed`, neighbours `102 passed`,
  `static gates passed in 11.2s.`, collection `2486 tests collected`, inserted dashes 0.
- Baselines: chunk 19/243 to 15/216, embed 19/296 to 15/265, parse 17/257 to 17/252,
  strategy 17/153 to 15/147; nothing added, `PINNED_LIZARD` untouched throughout.

The full tier was not observed end to end at 3b795f8; four background runs were
stopped mid-flight. The owner runs `scripts/gates.py full` before merging if the
composed evidence above is not enough.
