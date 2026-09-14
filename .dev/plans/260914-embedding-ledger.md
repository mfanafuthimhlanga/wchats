# Embedding calls reach the ledger

Every embedding call this platform makes writes a `model_calls` row, so retrieval cost per
turn and embedding cost per ingest stop being blank. Issue: #265. Evidence:
`.dev/reference/260914-unit-economics.md` (five staging turns, 31 ledger rows, zero of them
embeddings). Related: #60, #257.

## Data shape

One `ModelCall` per provider request.

| field | value |
|---|---|
| `purpose` | `embed_query` for a retrieval-time call, `embed_document` for an ingest one |
| `provider` | `voyage` or `bedrock`, as the price book names them |
| `requested_model` | `voyage-3` (`embedding_service.EMBEDDING_MODEL`) or `settings.BEDROCK_EMBED_MODEL_ID` |
| `served_model` | the same string |
| `model_source` | `unreported` |
| `input_tokens` | Voyage `EmbeddingsObject.total_tokens` for the batch; Bedrock `inputTextTokenCount` for the one text |
| `output_tokens` | 0 |
| `cache_read_tokens`, `cache_creation_tokens` | 0 |
| `tenant_id`, `agent_id`, `job_id` | off the call site's `LedgerContext` |

Two purposes rather than one `embedding`, so a rollup reads a turn's retrieval spend apart
from a corpus's ingest spend. They are the two `input_type` values the services already
pass, so the purpose is derived, never named twice. They live in
`EMBEDDING_PURPOSES` rather than `PURPOSE_ROUTES`, because a route feeds `make_client` and
an embedding builds no chat client; `app/services/embedding_ledger.py` carries the reason
and is the one home for it.

Output and cache counts are zero rather than absent. An embedding returns a vector, and
neither provider publishes an output or cache tariff for one. `model_source` is
`unreported`, and `app/services/embedding_ledger.py` says why.

**One row per provider request.** Voyage embeds up to 128 texts in one `embed` call, so one
batch is one row carrying that request's whole `total_tokens`. Bedrock Titan takes one text
per `invoke_model`, so a 500-chunk ingest leaves 500 rows. Both follow the same rule, which
is what keeps `calls` meaning provider requests everywhere in `tenant_usage_daily` and
`GET /agents/{id}/usage`. Recording inside the Titan loop also means the dimension guard
cannot lose spend, since the texts already invoked are already recorded when it raises.

**A provider that reports no token count writes no row.** `record_embedding` takes
`input_tokens: int | None`, and `None` logs `embedding_ledger.tokens_unreported` and
returns. That is the treatment `model_client` already gives an unreadable `usage` block
(`model_ledger.shape_skipped`). Spend nobody can read is a hole, not a zero.

## Interface

The calling code first. Every call site already holds either a decrypted DSN and the ids,
or a `LedgerContext`.

```python
# app/worker/tasks/runtime/retrieve.py, inside retrieve_and_rank
query_vector = embed_query(query, _ledger_for(agent, job_id, conn_str))

# app/worker/tasks/pipeline/embed.py, inside embed_and_migrate
embeddings = embed_chunks(texts, _ledger_for(job, conn_str))

# app/services/agent_tools.py, retrieve_tool's async body
ledger = _embedding_ledger()          # reads the ContextVars, never inside the executor
query_vector = await loop.run_in_executor(None, lambda: _embed_with_cache(query, ledger))
```

From which the signatures follow. `ledger` is required, never defaulted, for the reason
`rrf_fuse_with_expansion` states. The caller cannot know whether a provider call is coming,
and a client that silently records nothing is the failure the ticket exists to end.

```python
# app/services/embedding_ledger.py  (new)
EMBED_QUERY = "embed_query"
EMBED_DOCUMENT = "embed_document"
EMBEDDING_PURPOSES = frozenset({EMBED_QUERY, EMBED_DOCUMENT})

def purpose_for(input_type: str) -> str
def record_embedding(
    ledger: LedgerContext,
    *,
    purpose: str,
    provider: str,
    model: str,
    input_tokens: int | None,
    at: datetime | None = None,
) -> None

# app/services/retrieval_service.py
def embed_queries(texts: list[str], ledger: LedgerContext) -> list[list[float]]
def embed_query(query_text: str, ledger: LedgerContext) -> list[float]   # embed_queries([t])[0]

# app/services/embedding_service.py
def embed_chunks(texts: list[str], ledger: LedgerContext) -> list[list[float]]
def _embed_batch(texts: list[str], ledger: LedgerContext) -> list[list[float]]

# app/services/bedrock_embedding_service.py
def embed_texts(texts: list[str], input_type: str, ledger: LedgerContext) -> list[list[float]]
```

`embed_queries` is new and `embed_query` becomes one line over it. The provider branch was
written twice, in `embed_query` and again in `rrf_fuse_with_expansion`, and a second
recording site in the second copy is a second thing to keep in step.

### Where a job id is null

- `reembed_corpus` (`app/worker/tasks/pipeline/reembed.py`) is a backfill, not a job. It
  supplies `tenant_id` and `agent_id` off the Agent row and `job_id=None`.
- `_VoyageRagasEmbedding` (`app/services/eval_service.py`) takes the eval run's ledger, so
  its rows carry the run's `job_id` like every judge call beside them.

Every other embedding call site is inside a task that owns a job.

## Price book

Add one flat row.

```python
# Voyage voyage-3, fetched 2026-09-14 from https://docs.voyageai.com/docs/pricing,
# "Older models": $0.00006 per thousand tokens, $0.06 per million. Input only.
(_VOYAGE, _VOYAGE_3, TokenKind.INPUT): Decimal("0.06"),
```

`price_version` stays `2026-08-23.1`. No figure already in the book changed, so every call
it could price prices identically, which is the rule the Luna addition already followed.

**No Bedrock row.** `https://aws.amazon.com/bedrock/pricing/` renders no Titan Text
Embeddings tariff to a fetch, on 2026-09-14. `amazon.titan-embed-text-v2:0` therefore raises
`UnknownPrice`, and `usage_service` nulls the cost of any group holding one and names the
model in `price_gaps`. `EMBEDDING_PROVIDER` defaults to `bedrock`, so on any deployment left
at the default this turns a reported total into `null` plus a named gap. Staging runs
`voyage` (`apps/api/scripts/railway_staging_wizard.sh`). Closing it is one row and a figure
read off the AWS console.

## Recording is fail open

`record_embedding` catches everything and logs one `embedding_ledger.record_failed` through
`log_failure`, the shape `app/core/log_bounds.py` requires and the static gate counts. A
ledger insert that fails must not break a customer's retrieval or a tenant's ingest, and
`model_client._log_record_failure` already made that trade for chat calls.

## Files

| file | change |
|---|---|
| `app/services/embedding_ledger.py` | new. The two purposes, `purpose_for`, `record_embedding` |
| `app/services/embedding_service.py` | `embed_chunks` and `_embed_batch` take a ledger; one row per Voyage batch |
| `app/services/bedrock_embedding_service.py` | `embed_texts` takes a ledger; `_invoke_one` returns the body so the token count is readable; `_invoke_and_record` writes one row per `invoke_model` |
| `app/services/retrieval_service.py` | `embed_queries` added, `embed_query` delegates, `rrf_fuse_with_expansion` uses it |
| `app/services/agent_tools.py` | `_embed_with_cache` moves to module level and takes a ledger; the cache read, the provider call and the cache write fail independently so a `setex` failure cannot buy the embedding twice |
| `app/services/eval_service.py` | `_VoyageRagasEmbedding` takes the run's ledger |
| `app/worker/tasks/runtime/retrieve.py` | `_ledger_for` helper, passed at the embed call |
| `app/worker/tasks/pipeline/embed.py` | `_ledger_for` helper, passed at the embed call |
| `app/worker/tasks/pipeline/reembed.py` | `_dsn_and_ledger` returns the pooled dsn and a ledger with a null job id |
| `app/core/model_client.py` | four-line pointer on `PURPOSE_ROUTES` to where the embedding purposes live |
| `app/domain/pricing.py` | the voyage-3 row, and the docstring paragraph on what the book knows |
| `scripts/gates.py`, `tests/unit/test_gates.py` | lowered lizard pins, see Risks |

## Risks

- **Every rollup's total goes null on a Bedrock deployment.** Named above. It is the honest
  state and `price_gaps` names the model, but it is a visible change to
  `GET /agents/{id}/usage`.
- **Lizard pins.** Two entries go stale and are lowered in both `scripts/gates.py` and
  `tests/unit/test_gates.py`: `retrieve_tool` 244 to 227, and `rrf_fuse_with_expansion`
  8/68 to 7/62. No pin is raised. The three other pinned call sites
  (`retrieve_and_rank` 12/212, `embed_and_migrate` 13/238, `reembed_corpus` 13/169) take
  the ledger on a line they already had, so neither their length nor their complexity
  moves, which is what keeps this off the "never add an entry" rule.
- **A required `ledger` argument is a signature break** at every call site and in the tests
  that drive these functions directly. That is the point; a default would let a new call
  site record nothing and stay green.
- **A provider call that raises leaves no row**, because the recording happens after the
  response is in hand. The chat hook has the same property, and a quiet ledger is the
  failure mode both accept in exchange for never breaking the call.

- **Embedding spend is outside the per-turn dollar ceiling.** `agent_loop._over_budget`
  sums `turn.calls`, which the httpx hook fills, and an embedding never passes through
  that hook. So a turn's ceiling still measures the chat calls alone and the rollup now
  measures more than the ceiling does. Left alone here, because moving the ceiling is a
  change to what stops a turn and belongs in its own ticket.

- **Redelivery.** `embed_and_migrate` selects only chunks with no embedding row, so a
  redelivery after the upsert commits embeds nothing and records nothing, which is right.
  A redelivery before the commit re-embeds the same chunks and records a second set of
  rows, and both sets are true: the provider was paid twice. The ledger is a record of
  spend, not of work completed, so it should say so.

- **The eval fans out.** Ragas `AnswerRelevancy` issues `strictness` simultaneous
  `embed_text` calls per sample, and every row each one records opens its own tenant
  connection through `ledger_recorder`. A scoring pass therefore opens more short-lived
  tenant connections than it did, on top of the judge calls already doing the same.

## Tests

`tests/unit/test_embedding_ledger.py`, 23 tests, mirroring
`tests/unit/test_usage_service.py`:

- a retrieval turn's embedding leaves one row with purpose `embed_query`, the turn's
  `job_id` and the provider's token count
- query expansion, driven through `rrf_fuse_with_expansion` rather than the seam beneath
  it, records its variant embedding
- a Bedrock call leaves one row per `invoke_model`, and an unreported count costs that
  one row rather than the batch's whole figure
- an unknown `input_type` is refused before any provider call
- an ingest of 200 chunks leaves two `embed_document` rows, one per 128-text Voyage batch
- a recorder that raises does not break the embedding call, and logs
  `embedding_ledger.record_failed` by name
- a provider reporting no token count writes no row
- `cost_usd` prices a `voyage / voyage-3` call at `Decimal("0.00009")` for 1500 tokens, and
  raises `UnknownPrice` naming `amazon.titan-embed-text-v2:0`
- `summarise_usage` shows both purposes in `by_purpose`, counts the query row into
  `cost_per_turn_usd` and not the ingest row, which carries its own job id
- the eval scorer's real `_VoyageRagasEmbedding.embed_text` leaves one row billed to the run
- the two purposes are spelled out in the test file, disjoint from `PURPOSE_ROUTES`, and
  `route_for` raises `UnknownPurpose` on each

`tests/unit/test_agent_tools.py` drives `retrieve_tool` with the ContextVars bound: a cache
miss records one row for the turn against the tenant dsn, a cache hit records none, and a
`setex` failure still leaves exactly one.

`tests/unit/retrieval/test_retrieve_task.py` and `tests/unit/test_embed_task.py` each gain
one test that the ledger reaching the embed call carries the turn's or the job's ids, the
first also asserting the recorder was bound to the decrypted dsn.
`tests/unit/test_reembed_task.py` asserts the backfill's ledger carries the tenant and the
agent and a null `job_id`.

## Mutation proofs

Run on 2026-09-14, each mutation applied to the file named, the test run, the first `E`
line copied here, then the file restored byte-exact and the test re-run green. A guard
never observed to fail is indistinguishable from a tautology, so these are the record.

**a. Drop the ledger argument at the retrieval call site.**
`app/worker/tasks/runtime/retrieve.py`, `embed_query(query, _ledger_for(...))` to
`embed_query(query)`. Ran
`tests/unit/retrieval/test_retrieve_task.py::test_retrieve_and_rank_bills_the_embedding_to_this_turn`.

```
E   TypeError: test_retrieve_and_rank_bills_the_embedding_to_this_turn.<locals>.fake_embed_query() missing 1 required positional argument: 'ledger'
1 failed in 7.47s
```

**b. Drop it at the ingest call site.** `app/worker/tasks/pipeline/embed.py`,
`embed_chunks(texts, _ledger_for(job, conn_str))` to `embed_chunks(texts)`. Ran
`tests/unit/test_embed_task.py::test_embed_and_migrate_bills_the_embedding_to_this_job`.

```
E   IndexError: tuple index out of range
1 failed in 6.91s
```

**c. The recorder swallows the token count.** `app/services/embedding_ledger.py`,
`_embedding_row` writes `input_tokens=0`. Ran `test_embedding_ledger.py`,
`test_agent_tools.py` and `retrieval/test_retrieval_service.py`.

```
E   AssertionError: The count is the provider's total_tokens
E   AssertionError: The whole variant set is one Voyage request, so it is one row
E   AssertionError: assert [('embed_quer...d-text-v2:0')] == [('embed_quer...d-text-v2:0')]
E   AssertionError: assert [('embed_docu...document', 0)] == [('embed_docu...cument', 720)]
E   AssertionError: Two responses reported a count and one did not
E   AssertionError: assert 0 == 21
E   AssertionError: The count is the provider's total_tokens
E   AssertionError: assert [('embed_query', 0)] == [('embed_query', 8)]
8 failed, 84 passed in 33.43s
```

**d. Remove the fail-open try/except.** `app/services/embedding_ledger.py`,
`record_embedding` calls the recorder bare. Ran `test_embedding_ledger.py`.

```
E   RuntimeError: tenant database refused the connection
E   RuntimeError: tenant database refused the connection
2 failed, 21 passed in 19.33s
```

**e. Delete the log call from the fail-open handler.** `app/services/embedding_ledger.py`,
`except Exception as exc: log_failure(...)` to `except Exception: pass`. Ran
`test_embedding_ledger.py`.

```
E   AssertionError: Expected one record_failed line, got []
1 failed, 22 passed in 18.81s
```

**f. Restore the inline provider branch in `rrf_fuse_with_expansion`.**
`app/services/retrieval_service.py`, `embed_queries(variants, ledger)` back to the old
if/else that called `_get_vo().embed(...)` directly. Ran `test_embedding_ledger.py`.

```
E   AssertionError: The whole variant set is one Voyage request, so it is one row
1 failed, 22 passed in 19.23s
```
