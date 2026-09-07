# Draft golden scenarios (#203)

A path that drafts golden pairs from an agent's corpus for the owner to label, and writes
nothing to `eval_scenarios`. The owner keeps, edits or drops each draft; only a keep reaches
`register_golden_scenarios`, which stays the single writer of golden rows. The bar is the
hand-drafted set of 2026-09-06: 11 of 23 kept as written.

## Data shape

```python
DraftPair = {
    "question": str,           # one a customer would ask the business
    "reference_answer": str,   # two or three sentences lifted from one passage
    "citation": str,           # the passage, a substring of the chunk, checked in code
    "source_document_id": str,
    "source_document": str,    # documents.title, else source_uri
    "source_chunk_id": str,
}
```

A draft whose citation is not found in its chunk is dropped before anyone sees it. The
count of drops travels with the result so a run that dropped most of its drafts is visible
as one.

## Interface

```python
# worker, runtime queue
draft_golden_scenarios.apply_async(kwargs={"agent_id": ..., "job_id": ..., "n": 15}, queue="runtime")

# service, pure apart from the model call
chunks = pick_chunks_by_document(fetch_chunk_index(dsn), n=15)   # round robin, coverage by document
chunks = fetch_chunk_content(dsn, chunks)                        # content for the picked ids only
draft  = draft_pair_from_chunk(chunk, ledger)                    # one forced tool call, or None
ok     = citation_in_chunk(draft["citation"], chunk["content"])  # 40+ chars, whitespace aside
ok    &= answer_grounded_in_chunk(draft["reference_answer"], chunk["content"])  # 80% of words
```

The route creates a `Job` of kind `golden_draft` and dispatches the task, the same shape as
`upload_documents`. The task emits one `golden_draft.pair` event per kept draft and a
terminal `golden_draft.complete` event carrying `{kept, dropped, documents}`. `get_job`
already returns the last 100 events with payloads, so the MCP caller reads the drafts from
the job. What a run writes: the job row, the events, and a tenant ledger row per model
call. No scenario row. `n` is 10 to 30, under the 100 event window.

## Files

- `app/services/golden_draft_service.py`: the three functions above, the tool schema
  `submit_golden_draft`, the prompt with the five rules.
- `app/worker/tasks/runtime/golden_draft.py`: the task. `acks_late=True`, no Celery retry.
  Idempotent in two parts: a job with a complete event is skipped, and a job with pair
  events already on it drafts only the chunks that have none, so a redelivery resumes
  rather than re-billing.
- `app/api/v1/evals.py`: `POST /agents/{agent_id}/golden-scenarios/drafts`, body `{n}`,
  202 with `job_id`.
- `app/schemas/eval.py`: `GoldenDraftRequest`, `GoldenDraftResponse`.
- `app/api/mcp.py`: `draft_golden_scenarios` tool, described with the poll instruction.
- `app/core/model_client.py` `PURPOSE_ROUTES`: `golden_draft`, on the same row as
  `scenario_generation`.

## The prompt's five rules

1. One document per pair, named.
2. The reference answer is near verbatim from one passage. Two or three sentences, facts
   only, no synthesis across documents.
3. The question is one a customer or visitor would ask of the business, not a quiz about
   the text.
4. Numbers, commands and names copied exactly.
5. The citation is the exact passage the answer was lifted from.

Coverage (rule 4 of the issue) is code, not prompt: the round robin picks from every
document before repeating one.

## Tests

- Unit, `tests/unit/test_golden_draft_service.py`: the citation check drops a pair whose
  citation is not in its chunk and keeps one whose whitespace differs; the round robin
  picks from every document before repeating one and stops at `n`; a draft call that
  returns no tool call yields None; the ledger purpose is `golden_draft`.
- Unit, `tests/unit/test_golden_draft_task.py`: the task emits one pair event per kept
  draft and a complete event with the counts; a redelivery drafts only the chunks without
  a pair event; a corpus read failure fails the job on a fresh session.
- Unit, route: 404 on a foreign agent; 202 with a job id; `n` outside 10 to 30 is 422.
- Offline eval, `tests/evals/golden_draft/`: a four-document fixture corpus and a
  hand-labelled keep/drop set. Reports the keep rate; asserts nothing until a baseline
  exists. 11 of 23 is the number to beat.

## Playbook steps

1. Name the data shape first. Done above.
2. Sketch the interface. Done above.
3. Design it twice. The repo holds a precedent (`generate_scenarios_from_chunks`), so one
   sketch. skip: precedent exists.
4. Implement against the sketch. A parameter the sketch did not anticipate is surfaced,
   not bolted on.
5. Scrap the sketch on repeat friction.
6. Backend first, proved by gate. No UI in this issue; the labelling page is the bench
   already in use.
