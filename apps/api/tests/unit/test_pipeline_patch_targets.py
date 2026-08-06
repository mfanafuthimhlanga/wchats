"""Makes the docling-gated test modules falsifiable without installing docling.

`test_chunking_service.py` and `test_docling_service.py` are guarded by
`pytest.importorskip`, and no job in `ci.yml` installs the optional `pipeline`
extra. Nothing in this repo has ever executed them. That was survivable while the
skip was read as "unexecuted", but it stopped being survivable once the tests were
also *wrong*: all seven chunking tests patched
`app.services.chunking_service.HybridChunker` and the third docling test patched
`app.services.docling_service.DocumentStream`, neither of which exists. Both
services import those names inside the function body, since docling ships only in
the pipeline worker image. So in every environment where docling IS installed the
eight tests raised `AttributeError` at patch time, and in every environment where
it is not they reported `skipped` — permanently unfalsifiable rather than merely
hidden.

This module runs in the default gate, with no docling installed, and checks the one
property that broke: every patch target in those two files still names something
that exists. An `app.*` target is resolved by import and `getattr`; a third-party
target is matched against the imports the service under test actually performs,
which is exactly what makes patching the source module work at call time.

It does not claim the eight tests pass. It claims they are still aimed at real
objects. That is a smaller statement, and unlike the skip line it is checked.
"""

import ast
import importlib
from pathlib import Path

import pytest

_UNIT_DIR = Path(__file__).resolve().parent
_APP_DIR = _UNIT_DIR.parents[1] / "app"

# The docling-gated modules. Adding a third gated module means adding it here.
_GATED_TEST_MODULES = (
    _UNIT_DIR / "test_chunking_service.py",
    _UNIT_DIR / "test_docling_service.py",
)


def _patch_targets(source: str) -> list[str]:
    """Every dotted string literal handed to a `patch(...)` or `setattr(...)` call.

    Covers `patch("a.b.C")`, `mock.patch("a.b.C")` and
    `monkeypatch.setattr("a.b.c", value)` — the three spellings these two modules use.
    """
    targets: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = node.func
        name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", None)
        if name not in {"patch", "setattr"}:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and "." in first.value:
            targets.append(first.value)
    return targets


def _imported_names_in_app() -> set[tuple[str, str]]:
    """`(module, name)` for every `from <module> import <name>` anywhere under app/.

    `ast.walk` reaches function-local imports too, which is the entire point: the
    docling imports being matched here are deliberately call-time.
    """
    pairs: set[tuple[str, str]] = set()
    for path in _APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    pairs.add((node.module, alias.name))
    return pairs


def _unresolved_reason(target: str, app_imports: set[tuple[str, str]]) -> str | None:
    """Why *target* cannot be patched, or None if it can."""
    module_path, _, attribute = target.rpartition(".")

    if module_path.split(".")[0] == "app":
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:  # pragma: no cover - a broken app module fails louder elsewhere
            return f"{module_path} is not importable ({exc})"
        if not hasattr(module, attribute):
            return (
                f"module {module_path} has no attribute {attribute!r}; unittest.mock "
                f"raises AttributeError. If the service imports {attribute} inside a "
                f"function body, patch it on the module it is imported FROM instead"
            )
        return None

    # Third-party target: unimportable here by construction. It is valid precisely
    # when some module under app/ does `from <module_path> import <attribute>`, so
    # that the call-time import picks the patched object up.
    if (module_path, attribute) not in app_imports:
        return (
            f"no module under app/ does `from {module_path} import {attribute}`, so "
            f"patching {target} would replace an object nothing under test ever looks up"
        )
    return None


@pytest.mark.parametrize("module_path", _GATED_TEST_MODULES, ids=lambda p: p.name)
def test_gated_module_declares_patch_targets(module_path):
    """A gated module with no targets means this guard has lost its subject."""
    assert module_path.is_file(), f"{module_path} no longer exists; re-point or delete this guard"

    targets = _patch_targets(module_path.read_text(encoding="utf-8"))

    assert targets, (
        f"{module_path.name} declares no patch targets — this guard would pass "
        "vacuously over it and must be re-pointed or deleted, not left green"
    )


@pytest.mark.parametrize("module_path", _GATED_TEST_MODULES, ids=lambda p: p.name)
def test_every_patch_target_in_a_gated_module_names_something_real(module_path):
    """The check the permanent `skipped` line cannot make for itself."""
    app_imports = _imported_names_in_app()

    failures = {
        target: reason
        for target in _patch_targets(module_path.read_text(encoding="utf-8"))
        if (reason := _unresolved_reason(target, app_imports)) is not None
    }

    assert not failures, "\n".join(
        f"{module_path.name}: {target} -> {reason}" for target, reason in failures.items()
    )


def test_the_patch_target_guard_rejects_the_regression_it_exists_to_catch():
    """Proves the resolver flags the two targets that were actually broken.

    Without this the guard could be green because it resolves nothing at all.
    """
    app_imports = _imported_names_in_app()

    chunker = _unresolved_reason("app.services.chunking_service.HybridChunker", app_imports)
    stream = _unresolved_reason("app.services.docling_service.DocumentStream", app_imports)

    assert chunker is not None and "has no attribute 'HybridChunker'" in chunker
    assert stream is not None and "has no attribute 'DocumentStream'" in stream

    # And a third-party target nothing imports is rejected for the other reason.
    invented = _unresolved_reason("docling.chunking.NoSuchChunker", app_imports)
    assert invented is not None and "no module under app/" in invented


def test_the_replacement_targets_are_the_ones_the_services_import():
    """The positive half: the new targets resolve, and for the stated reason."""
    app_imports = _imported_names_in_app()

    assert ("docling.chunking", "HybridChunker") in app_imports
    assert ("docling.datamodel.base_models", "DocumentStream") in app_imports
    assert _unresolved_reason("docling.chunking.HybridChunker", app_imports) is None
    assert _unresolved_reason("docling.datamodel.base_models.DocumentStream", app_imports) is None
