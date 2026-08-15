# The never-executed class: six features that had never run

Phase A of the end-to-end validation plan (E2E-0 through E2E-5) found a defect at every step. Four of
them meant a headline feature had never worked at all, in any environment, since the day it shipped.

This note exists so the next person does not rediscover the pattern one defect at a time.

## The six

| Row | What was broken | How long |
|---|---|---|
| `1.21`, `1.22` | Required settings absent from both `.env.example` files and from the real `.env` | always |
| **`1.26`** | `chunk_documents` read `UPLOADS_DIR/...`, a local path nothing writes | since PROD-13 |
| **`5.14`** | Auditor `max_tokens=512` truncated any verdict carrying real evidence | since the judge shipped |
| **`1.32`** | The deployment orchestrator was never given the `submit_report` tool it is told to call | always |
| **`5.1`** | `approve-deployment` never re-read live findings, and could not: they are in another database | always |
| `1.30` | The orchestrator's timeout logged `str(asyncio.TimeoutError())`, which is `""` | always |

Ingestion, the grounding judge, the deployment checklist and the deploy gate. Four of the product's
five load-bearing claims.

## What they have in common

### 1. The tests could not see it, because they mocked the broken thing

Not "the tests were thin". The tests were structurally incapable of seeing the defect.

- **`1.26`**: `test_ingestion_chain.py:347` writes its fixture to
  `gettempdir()/vrd-uploads/{agent_id}/{doc_id}{ext}`. It does not mock the storage boundary, it
  manufactures the local file that production stopped creating. Had those four tests ever run, they
  would have gone green over a product that could not ingest a single PDF.
- **`1.32`**: every test of the orchestrator loop drives it with a fake SDK, and a fake accepts any
  options object. An agent holding no tools is indistinguishable from one holding the right tool.
- **`5.1`**: the approve route's tests hand it a control-DB mock. "Never queries the tenant DB" is
  invisible when the tenant DB was never part of the test's world.
- **`5.14`**: the Auditor's tests construct verdicts directly, so a `max_tokens` ceiling that only
  bites on real evidence is unreachable.

A mock encodes a claim about a boundary, and nothing forces anyone to evidence that claim. That is
`retro.md` Family I, and Phase A is its seventh through tenth recurrences.

### 2. The failure was silent, and often mis-signposted

- `1.32` produced "Orchestrator did not produce a report", a symptom two layers from its cause.
- `1.30` logged `error=` with nothing after it, because `str()` on a `TimeoutError` is empty.
- `5.14` reported `citation_spans Field required`, which reads as a model ignoring its schema and
  sends the reader to the prompt. The real remedy was the token budget.
- `1.28` logged `.complete` and `succeeded` after its core call failed.

**Rule:** when a structured-output call can be cut off by a token ceiling, check `stop_reason` before
validating. Otherwise every budget failure is reported in the vocabulary of a quality failure.

**Rule:** `str(exc)` is a silently plausible default for any exception carrying its meaning in its
type. Same shape as `getattr(x, "name", "unknown")`, which is how two earlier defects hid.

### 3. Fixing one layer exposed the next, every time

`retro.md` Family J, and it held without exception:

- `1.30` (timeout) fixed, which made `1.32` (no tool registered) visible.
- `dc67d37` (the judge finally receives evidence), then `5.14` (the budget cannot hold that
  evidence), then `5.16` (the evidence it receives is half of what the agent saw).
- `1.26` (chunk reads disk) fixed, then `1.27` (the S3 key's extension case disagrees between writer
  and readers), found only by comparing all three derivation sites.

A first green is not a finish when the code under repair had never run. Re-run, and expect a
different error rather than a pass.

### 4. The same defect existed twice, a milestone apart

`1.32` is audit defect **D4**, "5 of 7 red-team attackers were never given their tools, so they
reported clean". It was already found and fixed in `red_team_service`, whose own comment describes
the bug. It survived in `deployment_service` because nothing had ever executed the orchestrator.

**Rule:** when an audit finds a defect, grep for its shape across every sibling module before closing
it. The pin that now covers both is a structural scan (`test_sdk_tools_are_registered.py`), not an
assertion about one call site.

## The cheapest detector

Every one of these was found by running the thing once, end to end, against real infrastructure. Not
by review, since several had been reviewed. Not by the suite, since 2,206 tests were green
throughout.

Three greps that would have found them:

- **A tool schema defined at module scope and never referenced again is a dead contract.**
  `_TOOL_SUBMIT_REPORT` was referenced exactly once in the entire repository: its own definition.
- **When a storage or transport boundary moves, the migration is not complete until a scan for the
  old accessor returns zero live consumers**, and that scan lands as a test. Pinning the new call
  site proves the site. Pinning the absence of the old one proves the migration.
- **A value derived independently at both ends of a contract will drift.** The S3 key's extension was
  computed in three places and disagreed in two of them.

## What Phase A did not establish

Recorded because the closure reads stronger than it is.

- **No `ship` verdict has ever been earned.** Every one came from seeded eval and red-team signals.
  The eval has never been observed invoking the agent, and red-team has never run 7/7 with tools.
- **Every stored grounding verdict is biased** (`5.16`). The judge is shown roughly 1800 characters
  against 962 retrieved tokens, so it marks claims unsupported that the corpus in fact supports.
- **`citation_coverage` has never once been non-NULL.**
- MinIO is not S3. The ingestion chain is proven, AWS compatibility is not.
