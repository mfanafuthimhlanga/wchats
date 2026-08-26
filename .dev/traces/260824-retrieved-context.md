# RetrievedContext as retrieval's one return type (#44)

Ticket #44, decision #7 on map #4. On `feat/retrieved-context` off main (unstacked;
its only blocker #40 is merged): the type (`cc01cc1`), the review round (`471316d`).
Runs parallel to PR #70 (#43); no shared files.

## The type and the narrowed interface

`app/domain/retrieved_context.py`: `RetrievedChunk(chunk_id, document_id, content,
score, rank)` and `RetrievedContext(query, chunks, strategy)`, frozen. One score, one
rank on the interface; the per-engine scores (cosine, bm25, rrf, rerank) stay inside
`retrieval_service`'s internals and its trace. Construction refuses non-chunk elements
and coerces score to float and rank to int, so no reader downstream ever meets a
Decimal from psycopg2. `to_json`/`from_json` round-trip through a real string in the
tests; `from_json` refuses wrong-typed fields (`{"score": "0.9"}` raises
`InvalidRetrievedContext`), never building a silently wrong context.

The model-facing string narrowed from nine keys to five. Byte-identity with the old
string was unreachable once the interface narrowed, so the pin is the new string as a
hand-authored literal plus a test running `ast.literal_eval` over it exactly as
`runtime/agent.py:242` does. That file is untouched; the repr wire swap belongs to the
owned-loop ticket.

Public surface after review: search, fusion and rerank return typed values
(`rrf_fuse` and `rrf_fuse_with_expansion` return module-local frozen composites, not
string-keyed dicts). `build_trace` stays a dict because the trace is the decision's
sanctioned diagnostic payload, and `verified_qa_lookup` stays a dict because it
returns a QA hit, not retrieval; each says so in one docstring line.

## Behaviour deltas, all deliberate and recorded

- Judge chunk records gain the `score` their code always looked for and never found.
- The Ragas context text shifts with the same string on the eval and the live path,
  which is the #16 parity decision holding.
- `vector_search` gained `query_text`; a vector cannot say what was asked.
- A fused row's NULL score would raise instead of ranking as zero; unreachable through
  the COALESCE in `_RRF_SQL`. `chunks.content` is `TEXT NOT NULL` since 0001, so the
  unconditional slices are safe; the guarantee is a comment on the type.
- Historical `job_events` rows hold nine-key chunk payloads beside new five-key ones;
  any future replay consumer sees both shapes (cache-hit rows already diverged).

## Evidence, observed

- Red-first at every stage; the wrong-type payload cases were `11 failed, 41 passed`
  before the loud `from_json`, and element garbage was `5 failed` before validation.
- rrf maths: `_RRF_SQL` byte-identical old vs new, numeric literals unchanged, the new
  1/61 literal correct. Caveat for a later ticket: those tests drive mocks and pin the
  k=60 formula by source text (baselined site); no test computes RRF end to end.
- `full gates passed in 596.8s.` at `cc01cc1` and `full gates passed in 598.1s.` at the
  review round, whole suite `2470 passed, 13 skipped`.
- Baselines only fell: retrieve_tool 35/251 to 28/248, rrf_fuse 6/73 to 3/65,
  rrf_fuse_with_expansion 10/71 to 8/69, mirrored in the snapshots; one docstring cost
  was paid by folding prose rather than raising a pin.
