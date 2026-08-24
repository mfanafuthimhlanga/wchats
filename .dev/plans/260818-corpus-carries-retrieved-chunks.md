# 7.34 · corpus-carries-no-retrieved-chunks

**Goal:** a captured response carries the chunks its answer was grounded in, so
`grounding_fidelity` can return PASS. Owner decision 2026-08-18: keep the corpus on the SERVED
path, so it measures what a customer receives with the PII firewall applied.

**Why it is not a patch.** The untruncated chunks exist only on the worker's in-process
`tool_calls_log` under `RETRIEVE_JUDGE_CHUNKS_KEY`. `retrieved_context_json` is a Celery task
argument and a char-count log line. The customer SSE carries `summary[:200]`, a repr, and widening
it would ship corpus content to the browser. So the chunks have to be written down before anything
outside the worker can read them.

## Design

```
worker turn ──> _persist_messages ──> tool_calls.retrieved_chunks (new jsonb column)
                                              │
capture ──SSE──> response_text                │
      └────────── control DB ──> decrypt ──> tenant DB ──> merge into result
                                              │
                                    responses/S-0NN.json
```

**`RETRIEVE_JUDGE_CHUNKS_KEY`, not `RETRIEVE_CHUNKS_KEY`.** The offline judge is asked whether a
claim is supported, and BACKLOG 5.18 is the finding that a claim naming a document or section cannot
be supported by a context containing neither. Ragas scores text against text and wants the other
rendering; this consumer is a judge.

**A new column, not the existing `result`.** `result` is the 1800-char audit capture, which the
module's own comment says is below one full chunk on any realistic corpus. Nothing in app code
SELECTs `tool_calls` today (the readers named in comments read `tool_calls_audit`, a different
table), so adding a column is additive and breaks no reader.

## Files

- Create `alembic_tenant/versions/0017_tool_calls_retrieved_chunks.py` — `retrieved_chunks jsonb`,
  nullable. Chain head is `0016`.
- Modify `app/worker/tasks/runtime/agent.py::_persist_messages` — write the judge chunks per tool
  call. Non-retrieve calls write NULL, not `[]`: "this tool retrieves nothing" and "this retrieve
  returned nothing" are different observations and `5.16` is what happens when they collapse.
- Modify `tests/evals/capture_responses.py` — after the SSE drain, read the turn's chunks back and
  put them on `tool_calls_log[*]["result"]`, which is where the judge prompt and
  `validate_corpus.py` both look.
- Tests: `tests/unit/test_persist_retrieved_chunks.py`, plus a capture-merge test.

## Risks

- ~~**The migration cannot be run here.**~~ **FALSE, corrected 2026-08-18 (`7.36`).** A local
  PostgreSQL 17.6 has been running since 2026-08-10; the risk was written by quoting a stale
  CLAUDE.md line rather than testing a socket. `0017` applied and round-tripped against
  `wchats_tenant_probe`. The live tenant still needs it applied before a capture.
- **The capture gains a DB dependency.** It is an operator script run beside the services and it
  already needs `AGENT_ID` and the tenant API key; it now also needs `CONTROL_DB_SYNC_URL`. It
  degrades loudly rather than silently: no DB reachable means the merge is skipped, and
  `validate_corpus.py` then reports BLIND, which is exactly the state the run is in.

## Tests

1. `_persist_messages` writes the judge chunks for a retrieve call.
2. It writes NULL, not `[]`, for a non-retrieve call.
3. It writes NULL for a retrieve whose capture could not be parsed.
4. The capture merges fetched chunks onto the matching tool call and leaves others alone.
5. A capture that cannot reach the DB leaves `result` empty and the validator reports BLIND.
6. `validate_corpus.py` reports CLEAN once chunks are present.

**Mutation proof:** swap `RETRIEVE_JUDGE_CHUNKS_KEY` for `RETRIEVE_CHUNKS_KEY` in the write and
observe the provenance test go red.

## Exit

`validate_corpus.py` reports CLEAN against a re-captured corpus. Blocked behind `7.32` for the live
half; the code half is provable here.
