# Chunk and ChunkMetadata as frozen types (#42)

Ticket #42, decision #7 on map #4. On `feat/chunk-types`, stacked on
`chore/domain-package` (#40): slice 1 (`4244049`, the Chunk), slice 2 (`2511689`,
ChunkMetadata and the rule that closes #23), a prose round (`8402b7f`). Issue #23 closes
with slice 2.

## The types and their seams

- `app/domain/chunk.py`: `Chunk(document_id, ordinal, content, token_count, is_table)`,
  frozen stdlib dataclass. `id` is `field(init=False)` computed in `__post_init__` via
  `deterministic_chunk_id`, so a passed id is rejected by the generated `__init__` and
  no caller can build a chunk whose id disagrees with its position. Chosen over pydantic
  because the rejection falls out of the dataclass for free and Chunk validates nothing.
- `chunking_service.chunk_document -> tuple[Chunk, ...]`; `content` replaces the `text`
  key. The chunk task INSERT persists `is_table` into the column migration 0018 adds
  (`0018_chunks_is_table.py`, table-then-column convention).
- `app/domain/chunk_metadata.py`: `ChunkMetadata(chunk_id, summary, keywords,
  questions, entities)`, frozen, all fields required. `entities` is `list[Any]` because
  the domain rung cannot name `app.services.metadata_service.EntityExtraction`; the
  docstring names it.
- The #23 rule in `metadata.py`: `chunks_seen > 0` with `chunks_enriched == 0` marks the
  job failed, emits `job.failed` and raises, counts in the reason. Partial enrichment
  and empty documents keep today's behaviour; both escapes were mutation-proofed (each
  removed, its test red, restored green). A re-run whose remaining chunks wholly fail
  marks the job failed even when earlier attempts enriched most rows; fail-closed,
  per-run semantics, chosen deliberately.

## Evidence, observed

- The #23 regression test reproduced the 2026-08-22 log before the fix
  (`batch_extraction_failed` then `complete chunks_enriched=0`, task returned) and
  failed on exactly that; green after.
- Migration round trip on the local probe with a seeded pre-0018 row:
  up (`is_table boolean NOT NULL DEFAULT false`, existing row False), down (column
  gone), re-up. Neon untouched.
- `full gates passed in 758.7s.`, exit 0, at `2511689`; after the prose round
  `static gates passed in 15.8s.`, the seven driving test files `78 passed`, collection
  `2421 tests collected`. Whole unit suite at slice 2: `2408 passed, 13 skipped`.
- The literal-UUID pins caught `chunk_id.py`'s comment claiming `NAMESPACE_URL`; the
  constant is `NAMESPACE_DNS`. Prose corrected, value untouched because changing it
  rekeys every chunk row. `test_chunk_id.py`'s own namespace test builds its expected
  value from the constant it checks and now says so; `test_chunk_type.py` holds the
  literal pin.

## AC4, stated honestly

"Same chunks" is held by content and token pins in the rewritten chunker tests; the
literals are derivable from the fixtures and the unchanged `len(content.split())`
formula, and the pre-change suite was green before the interface change, but no single
commit shows the same literals passing against the dict version. "Same embeddings" is
held by inspection: `embed.py` reads `chunks.content` from the database and no `"text"`
key reader survives anywhere in `app/`. These tests sit behind docling importorskip and
CI never runs them; they ran locally, none skipped.

## What review sent elsewhere

- #62: the red-team poisoned-chunk probe INSERTs a uuid4 chunk outside the type; fix
  belongs to the Attacker rebuild (#52).
- #63: a pipeline task that exhausts retries dies with no `job.failed` and no job-row
  update (pre-existing, shared by chunk and metadata), plus the complete-then-failed
  event ordering.
- #64: tenant migrations run only at provision, so pre-0018 tenants break the new
  INSERT; the deploy story (#55) needs a fleet migration step.

## Baselines

`chunk_document` lowered 102 to 98; `generate_metadata` split below the floor and its
pin deleted on `found gone`; never-add held everywhere and `PINNED_LIZARD` needed no
edit for shrinks, by design.
