# 260810 — the docling gate: four failures become one honest skip

**Branch:** `chore/local-postgres` · **Commit:** `357f5b9` · Closes BACKLOG `1.6` + the 4-site half
of `4.1` · Opens `1.10`

Full evidence, verbatim red/green output and the Neon before/after:
**`.dev/reference/docling-gate-mutation-proofs.md`**. This is the terse record.

## What changed

- `tests/integration/test_ingestion_chain.py` — 4× `patch("app.services.chunking_service.HybridChunker")`
  → `patch("docling.chunking.HybridChunker")`, plus a module-level
  `pytest.importorskip("docling.chunking")` and `("docling_core")`.
- `tests/unit/test_ingestion_chain_docling_gate.py` — **new**, 8 tests. Proves the gate in both
  directions by collecting the real file in a subprocess under two forcing plugins.
- `tests/unit/_docling_absent_plugin.py` / `_docling_present_plugin.py` — **new**. Opt-in `-p`
  plugins, deliberately not conftests.
- `tests/unit/test_patch_targets_resolve.py` — `_KNOWN_BROKEN` loses the 4-site `HybridChunker`
  pin; the sites are gone, so the pin is gone.

## Decisions

- **No new convention.** The patch target came from `test_pipeline_patch_targets.py`, which
  already pinned `docling.chunking.HybridChunker` as correct; the gate shape came from
  `test_chunking_service.py`. Both were already in the tree.
- **`docling_core` is gated too.** `chunk_document` calls `isinstance(item, TableItem)` on a bare
  name — a MagicMock raises `TypeError` as isinstance's 2nd argument, so the module must be real.
- **The forcing plugins are not conftests.** They mutate `sys.modules` / `sys.meta_path` globally
  and must not leak into the unit gate that invokes them; hence subprocess + `-p`.
- **`--collect-only` is the subject.** It executes module-level code, so the gate runs, but needs
  no Postgres or Redis — which is why this guard belongs in the unit gate, not the integration one.
- **The S3 defect was filed, not fixed.** Folding a fixture rewrite into a patch-target fix would
  have made neither reviewable. See `1.10`.

## Measured

```
integration  6 failed, 9 passed, 22 skipped, 24 deselected, 0 errors in 204.49s   (was 10F/9P/21S)
unit         2120 passed, 12 skipped in 474.18s                                   (was 2112/12/0)
patch scan   targets_scanned 1283 · unresolvable_sites 1 · pinned_targets 1
gate         0 tests collected with docling blocked · 4 collected with it importable
```

`skipped` rises by **1, not 4** — a module-level `importorskip` is one collection-time skip for the
whole module. `10 − 4 = 6` failures, `21 + 1 = 22` skips. Fully accounted for.

## Deviation from the task brief

The brief said "Run BOTH suites. Commit." The commit landed **before** the mutation proofs, so
that `git checkout HEAD -- <path>` would be a real unconditional restore rather than a re-typing
from memory — the guard cannot be restored from HEAD if HEAD does not contain it. BACKLOG and
these notes were then folded into the same commit by amend, per the transactional-backlog rule.

## Surprise worth keeping

Under mutation M1 (gate repointed at a nonexistent module — i.e. a permanent skip),
`test_the_gate_skips_when_docling_is_absent` stayed **green** while the two anti-tautology guards
went red. That is the whole argument for the present-plugin, observed rather than reasoned: on
this machine, where docling is genuinely absent, a skip-direction assertion alone certifies
nothing.
