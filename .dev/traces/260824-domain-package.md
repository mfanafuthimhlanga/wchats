# The domain package and its import rung (#40)

Ticket #40, decision #7 on map #4. On `chore/domain-package`, stacked on
`chore/harness-cut` (#39): the mechanical move (`0640002`, 49 files, +114/-106 under
rename detection) and a review round of prose fixes.

## What changed

`app/domain/` exists as the layers contract's new bottom rung in `pyproject.toml`; a
domain module imports the standard library, third-party packages and domain siblings,
nothing above. The five modules that already qualified moved in unchanged, four of them
byte-identical (R100 blobs, verified by the Spec review): `chunk_id`, `pii_firewall`,
`context_frame`, `docling_service`, and `transactional/schemas.py` renamed to
`transactional_schemas.py` because `app.domain.schemas` beside the `app.schemas` rung
would mislead. Thirty-seven import sites repointed, patch and monkeypatch strings
included; no shims remain in `app/utils/` or `app/services/`.

## The rung, proven

Five mutations, each an `import` appended to `app/domain/chunk_id.py`, each run through
the `lint-imports` console script, each restored from HEAD before the next:

- `app.models`, `app.services`, `app.worker`, `app.api`: BROKEN observed, the exact
  `app.domain.chunk_id -> <target>` edge named, exit 1.
- `app.core` (the rung directly above): BROKEN observed the same way, so the domain sits
  below the old bottom rung, not beside it.

Unmutated: `Contracts: 2 kept, 0 broken.` The observed line is recorded beside the
contract in `pyproject.toml`, matching the file's convention.

## A baseline shrink nobody predicted

`agent_tools.py`'s pinned I001 existed only because `app.utils.context_frame` sorted
above `app.services.*` in its import block. The `app.domain` path sorts correctly, the
violation dissolved, and the ruff gate's stale arm forced the entry out of
`RUFF_BASELINE` and `PINNED_RUFF`. One pinned I001 remains (`chunk.py`), and the gate's
worked example in `scripts/gates.py` now cites it instead of the un-pinned pair.

## Review findings, fixed

Both axes passed the mechanical claims and returned prose: five comments and docs still
naming the old paths (`pending_confirmations.py`, `transactional/tools.py` twice, the
admin deploy page, the tool-author guide twice), the old pyproject sentence still
calling `models | core | utils` "the bottom rung" beside the new rung's sentence, three
em-dashes re-inserted on edited lines, and two history retro-edits where the move
rewrote what BACKLOG 5.19 and a recorded incident actually did at their old addresses.
All fixed; the incident narrative in `test_pipeline_patch_targets.py` again names the
old path as history while its live guard asserts the new one.

## Observed

- `static gates passed in 19.1s.` after the fixes, exit 0, measured while the full tier
  ran concurrently; 13.1s uncontended after the move.
- Driving tests after the move: `148 passed`; transactional callers `242 passed`;
  collection `2367 tests collected`, no errors.

After the review round, observed at 8add505: `full gates passed in 686.9s.`, exit 0.
