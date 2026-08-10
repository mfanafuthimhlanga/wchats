"""The docling gate on the ingestion-chain integration tests must skip, and must stop skipping.

Why this exists
---------------
tests/integration/test_ingestion_chain.py used to FAIL its four tests, not skip them.
All four patched `app.services.chunking_service.HybridChunker`, a name that module
never binds — chunking_service.py:64 imports HybridChunker inside the function body
because docling ships only in the pipeline worker image. unittest.mock raises
AttributeError at patch entry, so the four tests could not reach a single assertion in
either world: red where docling is absent, red where it is present. Four red lines that
ought to be four skips is not a cosmetic problem — it is camouflage. Those four sat in
a 10-failure integration summary and made the nine real defects harder to see.

The fix was the convention already used by tests/unit/test_chunking_service.py: patch
`docling.chunking.HybridChunker`, the module the name is imported FROM at call time,
and gate the module with pytest.importorskip.

What this module checks, and why it is not the obvious shape
------------------------------------------------------------
A gate is only worth its line if it can be observed NOT firing. docling is not
installed here, so the natural thing to write — "assert the module skips" — would pass
identically against `pytest.importorskip("no_such_module")`, a permanent skip, and
against a gate correctly tracking a dependency. That is the tautology this repo keeps
catching in its own guards.

So both directions are run, as real pytest collections of the real file, under two
opt-in plugins that force the answer either way (tests/unit/_docling_absent_plugin.py
and _docling_present_plugin.py). Neither depends on what this machine has installed,
so neither goes quietly unobservable when someone installs the `pipeline` extra.

`--collect-only` is the subject on purpose: it executes module-level code, so the gate
runs, but it needs no Postgres and no Redis. This module therefore belongs in the unit
gate rather than the integration one.

What it does NOT claim: that the four tests PASS when docling is present. They do not.
Under _docling_present_plugin all four run and fail in parse_documents, which fetches
upload bytes from S3 (`storage_service.get_bytes`, parse.py:264, PROD-13) while the
fixture still writes them to `gettempdir()/vrd-uploads/...`. Observed 2026-08-10 with
INTEGRATION_DB_URL set: 4 failed, each raising
`ParamValidationError('Invalid bucket name ""')` out of botocore before reaching a
single assertion. That is a separate, real defect the AttributeError was hiding, and
it is filed rather than fixed here. The claim made below is exactly: the gate lets
them run.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_UNIT_DIR = Path(__file__).resolve().parent
_API_ROOT = _UNIT_DIR.parents[1]
_APP_DIR = _API_ROOT / "app"
_GATED = _API_ROOT / "tests" / "integration" / "test_ingestion_chain.py"

_ABSENT_PLUGIN = "tests.unit._docling_absent_plugin"
_PRESENT_PLUGIN = "tests.unit._docling_present_plugin"

# The four tests the gate governs. Named individually rather than counted so that a
# rename shows up as a rename instead of silently keeping the arithmetic right.
_GATED_TESTS = (
    "test_full_chain_runs_in_eager_mode_with_mocks",
    "test_idempotent_chain",
    "test_chain_emits_all_11_m2_event_types",
    "test_chain_no_conn_strings_logged",
)

# Sites in the gated file that patch the chunker. Pinned with a count, in the same
# spirit as test_patch_targets_resolve.py::_KNOWN_BROKEN: adding a fifth test that
# patches the chunker means updating this number in the same commit.
_CHUNKER_TARGET = "docling.chunking.HybridChunker"
_CHUNKER_SITES = 4
_DEAD_CHUNKER_TARGET = "app.services.chunking_service.HybridChunker"


def _source() -> str:
    return _GATED.read_text(encoding="utf-8")


def _collect(*plugins: str) -> subprocess.CompletedProcess:
    """Collect the gated file in a subprocess under *plugins*, returning the result.

    A subprocess, not an in-process pytest run: both plugins mutate import state
    globally (sys.modules, sys.meta_path) and must not leak into the unit gate that
    invoked them.
    """
    argv = [
        sys.executable,
        "-m",
        "pytest",
        str(_GATED),
        "--collect-only",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]
    for plugin in plugins:
        argv += ["-p", plugin]
    return subprocess.run(
        argv,
        cwd=str(_API_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def _summary_line(stdout: str) -> str:
    """pytest's final summary line — the last non-empty line of stdout.

    Read instead of scanning the whole stream, because ``--collect-only -q``
    prints every collected test id and a substring check for "error" over all of
    it fires on any test whose NAME contains those letters. A future
    ``test_chain_error_path`` in the gated file would make this guard go red
    with a message about collection erroring, which is not what happened — a
    false red that reads as a real one is worse than no guard.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _module_level_importorskip_args(source: str) -> list[str]:
    """The module named by each MODULE-LEVEL `pytest.importorskip("...")` call.

    Module level is the point: a gate inside a fixture or a single test would leave the
    other tests to fail exactly as they did before.
    """
    names: list[str] = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func = call.func
        attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if attr != "importorskip" or not call.args:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.append(first.value)
    return names


def _patch_targets(source: str) -> list[str]:
    """Every dotted string literal handed to a `patch(...)` call in *source*.

    AST rather than a substring scan: the file explains in prose, at four sites, which
    name it deliberately does NOT patch and why. A `"x" not in source` check reads those
    comments as violations, which would make the comment the bug.
    """
    targets: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in {"patch", "setattr"}:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            targets.append(first.value)
    return targets


def _call_time_import_roots() -> set[str]:
    """Every module app/ does `from <module> import <name>` against, at any nesting.

    ast.walk reaches function-local imports, which is the whole subject: the docling
    imports being gated are deliberately call-time.
    """
    modules: set[str] = set()
    for path in _APP_DIR.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
    return modules


# ---------------------------------------------------------------------------
# The gate's shape
# ---------------------------------------------------------------------------


def test_the_gated_file_still_exists():
    """Every assertion below reads this file; if it moved, they would all pass vacuously."""
    assert _GATED.is_file(), f"{_GATED} is gone — re-point or delete this guard, do not leave it green"


def test_the_gate_is_module_level_and_names_docling():
    """A gate inside one test would leave the other three failing as before."""
    gated_modules = _module_level_importorskip_args(_source())

    assert gated_modules, (
        f"{_GATED.name} has no module-level pytest.importorskip. Its four tests reach "
        "chunk_document, which imports docling at call time, so without the gate they "
        "fail rather than skip wherever the `pipeline` extra is not installed."
    )
    assert any(m.split(".")[0] in {"docling", "docling_core"} for m in gated_modules), (
        f"the gate names {gated_modules}, none of which is docling — a gate on some "
        "other dependency would skip for a reason that has nothing to do with why "
        "these tests cannot run"
    )


def test_the_gate_names_only_modules_app_actually_imports():
    """Gating on a module nothing imports is a permanent skip wearing a dependency's name."""
    app_imports = _call_time_import_roots()
    unbacked = [
        module
        for module in _module_level_importorskip_args(_source())
        if not any(imported == module or imported.startswith(module + ".") for imported in app_imports)
    ]
    assert not unbacked, (
        f"{_GATED.name} gates on {unbacked}, which no module under app/ imports. "
        "The gate must track the imports that actually make these tests unrunnable — "
        "chunking_service.py:64-65 — or it is just a skip with a plausible label."
    )


def test_the_chunker_is_patched_where_it_is_imported_from():
    """The regression itself: the service module never binds HybridChunker."""
    targets = _patch_targets(_source())

    assert _DEAD_CHUNKER_TARGET not in targets, (
        f"{_GATED.name} patches {_DEAD_CHUNKER_TARGET}, which chunking_service.py does "
        "not bind — it imports HybridChunker inside chunk_document. unittest.mock raises "
        "AttributeError at patch entry, so the test fails before it tests anything."
    )
    assert targets.count(_CHUNKER_TARGET) == _CHUNKER_SITES, (
        f"expected {_CHUNKER_SITES} sites patching {_CHUNKER_TARGET}, found "
        f"{targets.count(_CHUNKER_TARGET)}. The count is part of the pin: adding or "
        "removing a chunker-patching test means updating it here."
    )


# ---------------------------------------------------------------------------
# The gate's behaviour, in both directions
# ---------------------------------------------------------------------------


def test_the_gate_skips_when_docling_is_absent():
    """Direction 1: no docling, no collection — and no failures either."""
    result = _collect(_ABSENT_PLUGIN)

    # Exit 2 is pytest's "collection/internal error"; 5 is "no tests collected",
    # which is the correct outcome of a module-level skip under --collect-only.
    assert result.returncode != 2, (
        "collection errored rather than skipping under a blocked docling "
        f"(exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
    )
    assert "error" not in _summary_line(result.stdout).lower(), (
        "pytest's summary line reports an error rather than a clean skip:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    for name in _GATED_TESTS:
        assert name not in result.stdout, (
            f"{name} was collected with docling blocked — the gate did not fire, so "
            f"it will fail at patch time instead of skipping:\n{result.stdout}"
        )


def test_the_gate_does_not_skip_when_docling_is_importable():
    """Direction 2, the one that makes direction 1 mean anything.

    Without this, `pytest.importorskip("anything_at_all")` would satisfy the test above.
    Asserts the four tests are collected as runnable items — not that they pass; they do
    not (see this module's docstring on the S3 fixture defect).
    """
    result = _collect(_PRESENT_PLUGIN)

    missing = [name for name in _GATED_TESTS if name not in result.stdout]
    assert not missing, (
        f"docling was importable and {missing} still did not collect. The gate is "
        f"skipping for a reason other than docling's absence, which makes it a "
        f"permanent skip:\n{result.stdout}\n{result.stderr}"
    )


@pytest.mark.parametrize("plugin", [_ABSENT_PLUGIN, _PRESENT_PLUGIN])
def test_both_forcing_plugins_actually_load(plugin):
    """A mistyped `-p` name would make both directions above pass for the wrong reason.

    pytest exits 4 (usage error) on an unimportable plugin, and its stderr — not the
    collection summary — is where that shows up. Direction 1 asserts an absence, so it
    would happily read a plugin that never loaded as a successful skip.
    """
    result = _collect(plugin)

    assert result.returncode != 4, (
        f"pytest could not load -p {plugin} (exit 4):\n{result.stderr}"
    )
    assert "usage error" not in result.stderr.lower(), result.stderr
