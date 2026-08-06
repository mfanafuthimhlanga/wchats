"""Every `patch("app...")` string in the test suite must name something app/ actually binds.

Why this exists
---------------
The lint branch deleted 149 unused imports. An import can be "unused" to a linter and
still be load-bearing, and the sharpest form of that in this repo is the patch target:
`mock.patch("a.b.C")` and `monkeypatch.setattr("a.b.C", v)` both raise AttributeError
when `C` is not bound on the module. Delete the import, and four green tests turn red
— but only in a job that runs them. Three of the four sites that protected
`app.worker.tasks.pipeline.parse.parse_document` live in tests/integration/, which has
never executed on the dev machine (no Postgres, no Redis) and had never reached a test
in CI either. Grepping caught it that time. This module makes the check permanent.

What it checks, and what it deliberately does not
-------------------------------------------------
Resolution is STATIC — the AST of the target module is read and its module-level
bindings collected. Nothing is imported. That is the point: importing
`app.worker.tasks.runtime.agent` needs claude_agent_sdk, and this repo has a recorded
defect where the order in which test modules install a fake SDK decides whether 18
tests pass. A guard that could be broken by its own collection position would be worth
less than the bug it guards.

The cost of staying static is that it verifies the FIRST segment past the module only.
For `app.api.v1.evals.run_eval_suite.apply_async` it checks that `run_eval_suite` is
bound in `app/api/v1/evals.py`; it says nothing about `apply_async`. It also honours
`create=True` / `raising=False`, which are the two spellings that explicitly ask for a
name that does not exist yet.

tests/unit/test_pipeline_patch_targets.py is the complement: narrower subject (the two
docling-gated modules) but resolved by real import and getattr, so it catches what the
AST cannot.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2]
_TESTS = _API_ROOT / "tests"

# ---------------------------------------------------------------------------
# Targets that do NOT resolve today. Every entry is pre-existing — none was
# introduced by the lint work — and every entry is a real defect, not an
# exemption. Keyed by (test file, target) so it survives line renumbering.
# test_known_broken_targets_are_still_broken below fails if one of these starts
# resolving, so the list cannot rot into a permanent excuse.
# ---------------------------------------------------------------------------
_KNOWN_BROKEN: dict[tuple[str, str], str] = {
    (
        "tests/integration/test_actor_latency.py",
        "app.services.transactional.tools.get_adapter",
    ): (
        "app/services/transactional/tools.py:89 imports `get_adapter_for_skill`. "
        "`get_adapter` is the older name and was never re-pointed. The test would "
        "raise AttributeError at patch time; it has never run."
    ),
    (
        "tests/integration/test_ingestion_chain.py",
        "app.services.chunking_service.HybridChunker",
    ): (
        "4 sites. Identical to the defect fixed in tests/unit/test_chunking_service.py: "
        "chunking_service.py imports HybridChunker inside the function body, so the "
        "module has no such attribute. The unit copy was corrected; this integration "
        "copy was not, and integration has never reached a test."
    ),
}


@lru_cache(maxsize=None)
def _module_level_names(path: Path) -> frozenset[str]:
    """Names bound at module scope in `path`, including inside `if`/`try` wrappers."""
    names: set[str] = set()
    pending = list(ast.parse(path.read_text(encoding="utf-8", errors="replace")).body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.If, ast.Try)):
            # Conditional and guarded imports still bind at module scope.
            pending.extend(node.body)
            pending.extend(node.orelse)
            for handler in getattr(node, "handlers", []):
                pending.extend(handler.body)
            pending.extend(getattr(node, "finalbody", []))
        elif isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def _module_file(dotted: str) -> Path | None:
    """Path of the app module named by `dotted`, or None if it is not one."""
    parts = dotted.split(".")
    if parts[0] != "app":
        return None
    candidate = _API_ROOT.joinpath(*parts).with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = _API_ROOT.joinpath(*parts) / "__init__.py"
    return package if package.is_file() else None


def _app_patch_targets(source: str) -> list[tuple[str, int]]:
    """Every `app.*` dotted string handed to patch(...) or setattr(...), minus opt-outs.

    `create=True` (mock) and `raising=False` (monkeypatch) are explicit requests for a
    name that is not there, so they are not violations.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = node.func
        fn = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", None)
        if fn not in {"patch", "setattr"}:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        if not first.value.startswith("app."):
            continue
        opted_out = any(
            (kw.arg == "create" and getattr(kw.value, "value", None) is True)
            or (kw.arg == "raising" and getattr(kw.value, "value", None) is False)
            for kw in node.keywords
        )
        if not opted_out:
            found.append((first.value, node.lineno))
    return found


def _unresolved() -> list[tuple[str, str, int, str]]:
    """(relative test file, target, line, reason) for every target that does not resolve."""
    problems: list[tuple[str, str, int, str]] = []
    for test_file in sorted(_TESTS.rglob("*.py")):
        source = test_file.read_text(encoding="utf-8", errors="replace")
        rel = test_file.relative_to(_API_ROOT).as_posix()
        for target, lineno in _app_patch_targets(source):
            parts = target.split(".")
            module_path = None
            leaf_index = 0
            for i in range(len(parts) - 1, 0, -1):
                module_path = _module_file(".".join(parts[:i]))
                if module_path is not None:
                    leaf_index = i
                    break
            if module_path is None:
                problems.append((rel, target, lineno, "no app module matches any prefix"))
                continue
            leaf = parts[leaf_index]
            if leaf not in _module_level_names(module_path):
                problems.append(
                    (
                        rel,
                        target,
                        lineno,
                        f"{leaf!r} is not bound at module level in "
                        f"{module_path.relative_to(_API_ROOT).as_posix()}",
                    )
                )
    return problems


def test_every_app_patch_target_is_bound_at_module_level():
    """A patch target that names nothing is a test that raises instead of testing."""
    problems = [p for p in _unresolved() if (p[0], p[1]) not in _KNOWN_BROKEN]
    assert not problems, "Unresolvable patch targets:\n" + "\n".join(
        f"  {rel}:{line}  {target}  -> {why}" for rel, target, line, why in problems
    )


def test_known_broken_targets_are_still_broken():
    """The pin list must shrink by being fixed, never by being forgotten."""
    live = {(rel, target) for rel, target, _, _ in _unresolved()}
    healed = sorted(key for key in _KNOWN_BROKEN if key not in live)
    assert not healed, (
        "These patch targets now resolve — delete them from _KNOWN_BROKEN:\n"
        + "\n".join(f"  {rel}  {target}" for rel, target in healed)
    )


def test_the_scan_actually_finds_targets():
    """A silent scan of zero files would make both assertions above vacuous."""
    total = sum(
        len(_app_patch_targets(f.read_text(encoding="utf-8", errors="replace")))
        for f in _TESTS.rglob("*.py")
    )
    assert total > 500, f"expected the suite's ~1150 app.* patch targets, scanned {total}"
