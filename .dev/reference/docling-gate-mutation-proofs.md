# The docling gate on `test_ingestion_chain.py` — evidence, not intention

**Date:** 2026-08-10 · **Branch:** `chore/local-postgres` · **Commit:** `357f5b9`
**Closes:** BACKLOG `1.6`, the 4-site half of `4.1` · **Opens:** BACKLOG `1.10`

Everything below was run on this machine. Where a number appears it came from the run quoted
beside it. Nothing here is inferred from a previous session's scroll-back.

---

## 1. The defect

All four tests in `apps/api/tests/integration/test_ingestion_chain.py` patched
`app.services.chunking_service.HybridChunker`. That module never binds the name —
`chunking_service.py:64` does the import **inside the function body**:

```python
from docling.chunking import HybridChunker   # lazy — only available in pipeline worker
from docling_core.types.doc import TableItem  # noqa: F811
```

`unittest.mock` resolves a patch target by `getattr` at patch *entry*, so all four raised
`AttributeError` before reaching a single assertion. That makes them red in **both** worlds —
red where docling is absent, red where it is present — which is why the pin in
`test_patch_targets_resolve.py::_KNOWN_BROKEN` called it a real defect rather than an exemption.

The cost was not the four lines. It was that four red lines sat inside a 10-failure integration
summary and made the nine genuine defects harder to read. A failing test that ought to skip is
camouflage.

## 2. The fix, and why this shape

Two changes, both reusing conventions already in the tree — no second convention was invented:

| Change | Precedent followed |
|---|---|
| Patch `docling.chunking.HybridChunker` | `tests/unit/test_pipeline_patch_targets.py::test_the_replacement_targets_are_the_ones_the_services_import` already pins this as the correct target |
| `pytest.importorskip("docling.chunking")` + `("docling_core")` at module scope | `tests/unit/test_chunking_service.py`, the existing docling gate |

Patching the module the name is imported **from** works precisely because the import is
call-time: `chunk_document` re-looks-up `HybridChunker` on `docling.chunking` every call, so it
picks up whatever the patch put there.

`docling_core` is gated too, not just `docling.chunking`. `chunk_document` calls
`isinstance(item, TableItem)` on a bare name the mocks never reach, so `TableItem` must be a real
class for the test to get anywhere — a `MagicMock` raises `TypeError` as isinstance's second
argument.

## 3. Why a plain "it skips" assertion would have been worthless

docling is **not installed on this machine** (verified: `importlib.util.find_spec` returns None
for both `docling` and `docling_core`). So the natural guard — "assert the module skips" — would
pass identically against `pytest.importorskip("no_such_module_at_all")`, a permanent skip. A
guard observed to fire in one direction only is indistinguishable from a guard that always fires.

Both directions are therefore run as **real pytest collections of the real file**, in a
subprocess, under two opt-in plugins that force the answer regardless of what is installed:

- `tests/unit/_docling_absent_plugin.py` — a `sys.meta_path` finder that raises
  `ModuleNotFoundError` for `docling*`, after purging any already-imported copy.
- `tests/unit/_docling_present_plugin.py` — registers stand-in modules for exactly the five names
  `app/` imports at call time.

Neither is a `conftest.py`, deliberately: each must apply only to the subprocess that opts in,
never to the suite that invokes it. `--collect-only` is the subject because it executes
module-level code (so the gate runs) while needing no Postgres and no Redis — which is why this
guard lives in the **unit** gate.

### Observed, directly, not via the guard's own assertion

```
$ .venv/Scripts/python.exe -m pytest tests/integration/test_ingestion_chain.py \
    --collect-only -q --no-header -p no:cacheprovider -p tests.unit._docling_absent_plugin

no tests collected in 2.80s
```

```
$ .venv/Scripts/python.exe -m pytest tests/integration/test_ingestion_chain.py \
    --collect-only -q --no-header -p no:cacheprovider -p tests.unit._docling_present_plugin

tests/integration/test_ingestion_chain.py::test_full_chain_runs_in_eager_mode_with_mocks
tests/integration/test_ingestion_chain.py::test_idempotent_chain
tests/integration/test_ingestion_chain.py::test_chain_emits_all_11_m2_event_types
tests/integration/test_ingestion_chain.py::test_chain_no_conn_strings_logged

4 tests collected in 1.66s
```

0 with docling blocked, 4 with docling importable. The gate tracks the dependency.

## 4. Mutation proofs

Protocol: mutate, **run**, observe red, restore with `git checkout HEAD -- <path>` (unconditional
and exact — the fix was committed first so HEAD holds it), **run** again, observe green.
`git status --porcelain` was empty after every restore.

### M1 — the anti-tautology proof (the important one)

**Mutation:** `pytest.importorskip("docling.chunking")` → `pytest.importorskip("no_such_module_at_all")`
in `tests/integration/test_ingestion_chain.py`. This simulates precisely the failure mode this
repo keeps finding: a gate that always skips.

**Selector:**
```
tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_does_not_skip_when_docling_is_importable
tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_skips_when_docling_is_absent
tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_names_only_modules_app_actually_imports
```

**RED (verbatim tail):**
```
E       AssertionError: test_ingestion_chain.py gates on ['no_such_module_at_all'], which no
        module under app/ imports. The gate must track the imports that actually make these
        tests unrunnable - chunking_service.py:64-65 - or it is just a skip with a plausible label.
E       assert not ['no_such_module_at_all']

tests\unit\test_ingestion_chain_docling_gate.py:203: AssertionError
=========================== short test summary info ===========================
FAILED tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_does_not_skip_when_docling_is_importable
FAILED tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_names_only_modules_app_actually_imports
2 failed, 1 passed in 19.08s
```

**Read the `1 passed`.** Under a purely tautological gate,
`test_the_gate_skips_when_docling_is_absent` stayed **green**. That is the proof that the
skip-direction assertion, alone, is worth nothing — and the reason the present-plugin exists.

**GREEN after `git checkout HEAD --`:**
```
...                                                                      [100%]
3 passed in 18.65s
```

### M2 — the gate is load-bearing

**Mutation:** delete both `pytest.importorskip(...)` calls from
`tests/integration/test_ingestion_chain.py`.

**Selector:**
```
tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_skips_when_docling_is_absent
tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_is_module_level_and_names_docling
```

**RED (verbatim tail):**
```
E       AssertionError: test_ingestion_chain.py has no module-level pytest.importorskip. Its four
        tests reach chunk_document, which imports docling at call time, so without the gate they
        fail rather than skip wherever the `pipeline` extra is not installed.
E       assert []

tests\unit\test_ingestion_chain_docling_gate.py:183: AssertionError
=========================== short test summary info ===========================
FAILED tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_skips_when_docling_is_absent
FAILED tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_is_module_level_and_names_docling
2 failed in 10.97s
```

**GREEN after `git checkout HEAD --`:**
```
..                                                                       [100%]
2 passed in 8.66s
```

### M3 — the patch-target pin and its exact count

**Mutation:** revert **one** of the four sites (`test_chain_no_conn_strings_logged`) to
`patch("app.services.chunking_service.HybridChunker")`.

**Selector:**
```
tests/unit/test_ingestion_chain_docling_gate.py::test_the_chunker_is_patched_where_it_is_imported_from
tests/unit/test_patch_targets_resolve.py::test_every_app_patch_target_is_bound_at_module_level
tests/unit/test_patch_targets_resolve.py::test_known_broken_site_counts_are_exact
```

**RED (verbatim tail):**
```
E       AssertionError: 2 unresolvable sites, but the pins account for 1. Every unresolvable site
        must be pinned with its count.
E       assert 2 == 1

tests\unit\test_patch_targets_resolve.py:237: AssertionError
=========================== short test summary info ===========================
FAILED tests/unit/test_ingestion_chain_docling_gate.py::test_the_chunker_is_patched_where_it_is_imported_from
FAILED tests/unit/test_patch_targets_resolve.py::test_every_app_patch_target_is_bound_at_module_level
FAILED tests/unit/test_patch_targets_resolve.py::test_known_broken_site_counts_are_exact
3 failed in 12.15s
```

A single reverted site out of four is caught — by the new module's count pin **and** by the
existing scanner's unpinned-site arithmetic. Three-of-four is not silently green.

**GREEN after `git checkout HEAD --`:**
```
...                                                                      [100%]
3 passed in 11.25s
```

## 5. Suite results, measured

```
integration  6 failed, 9 passed, 22 skipped, 24 deselected in 204.49s (0:03:24)
             INTEGRATION_DB_URL=postgresql://wchats:wchats@localhost:5432/wchats_control
             REDIS_URL=redis://localhost:6379/0
             baseline was 10 failed, 9 passed, 21 skipped, 24 deselected

unit         2120 passed, 12 skipped, 28 warnings in 474.18s (0:07:54)
             baseline was 2112 passed, 11-12 skipped, 0 failed

patch scan   targets_scanned 1283 · unresolvable_sites 1 · pinned_targets 1
```

**`skipped` rises by 1, not 4, and that is correct.** `pytest.importorskip` at module scope is a
single collection-time skip for the whole module; it does not report four. The arithmetic
`10 − 4 = 6` failures and `21 + 1 = 22` skips is fully accounted for.

The 6 remaining integration failures are the pre-existing ones (`1.7`–`1.9`), untouched:
`test_chain.py` ×2, `test_provision.py` ×2, `test_query_route.py` ×1, `test_sse.py` ×1.

## 6. What this does NOT claim

**The four tests do not pass when docling is present.** Run under the present-plugin against the
live local Postgres, all four RUN and all four fail:

```
E   celery.exceptions.Retry: Retry in 1s: ParamValidationError('Parameter validation failed:
    Invalid bucket name "": Bucket name must match the regex "^[a-zA-Z0-9.\-_]{1,255}$" ...')
[error] parse_documents.error  error_type=ParamValidationError
4 failed in 25.88s
```

`parse_documents` reads upload bytes from **S3** (`storage_service.get_bytes`, `parse.py:264`,
PROD-13) while the fixture still writes them to `gettempdir()/vrd-uploads/{agent_id}/{doc_id}{ext}`
and no bucket is configured for tests. The module docstring's "Infra requirements" section is
stale — it describes a local-disk contract the task stopped honouring at PROD-13.

That is a **second, independent defect the AttributeError was hiding**, and it is filed as
BACKLOG `1.10`, not fixed here. Fixing it means changing the fixture (stub `get_bytes` alongside
the other boundary mocks, or configure a local object store), which is a different change from
the one this commit makes, and folding it in would have made neither reviewable.

The claim this work supports is exactly: **the gate lets them run, and skips them honestly when
it cannot.**

## 7. Neon safety

No Neon project was created or deleted. The API was listed before and after the integration run
(which includes two `test_provision.py` tests that exercise the provisioning path):

```
[BEFORE] HTTP 200  project_count=8   MISSING_FROM_LIVE=[]  EXTRA_NOT_IN_BASELINE=[]  BASELINE_INTACT=yes
[AFTER]  HTTP 200  project_count=8   MISSING_FROM_LIVE=[]  EXTRA_NOT_IN_BASELINE=[]  BASELINE_INTACT=yes
```

All 8 baseline projects from `C:/Users/Bantu/pg-setup/neon-baseline.txt` present, with identical
ids before and after. The provision tests fail before reaching a create call — `conftest.py:58`
sets a placeholder `NEON_API_KEY`, which is `1.7`.

## 8. Files

```
apps/api/tests/integration/test_ingestion_chain.py        4 patch targets repointed + module gate
apps/api/tests/unit/test_ingestion_chain_docling_gate.py  NEW — 8 tests, both directions
apps/api/tests/unit/_docling_absent_plugin.py             NEW — meta_path blocker
apps/api/tests/unit/_docling_present_plugin.py            NEW — stand-in modules
apps/api/tests/unit/test_patch_targets_resolve.py         _KNOWN_BROKEN loses the 4-site pin
.dev/BACKLOG.md                                           1.6 closed · 1.10 opened · 1.1/4.1/4.4 updated
```
