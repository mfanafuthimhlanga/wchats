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
from collections import Counter
from functools import lru_cache
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2]
_TESTS = _API_ROOT / "tests"

# ---------------------------------------------------------------------------
# Targets that do NOT resolve today. Every entry is pre-existing — none was
# introduced by the lint work — and every entry is a real defect, not an
# exemption. Keyed by (test file, target) so it survives line renumbering, and
# valued by (number of call sites, reason) so the phase record's headline count
# is checked by the suite instead of being retyped from a scroll-back.
# test_known_broken_targets_are_still_broken fails if one of these starts
# resolving, so the list cannot rot into a permanent excuse;
# test_known_broken_site_counts_are_exact fails if a count drifts either way.
# ---------------------------------------------------------------------------
_KNOWN_BROKEN: dict[tuple[str, str], tuple[int, str]] = {
    (
        "tests/integration/test_actor_latency.py",
        "app.services.transactional.tools.get_adapter",
    ): (
        1,
        "app/services/transactional/tools.py:89 imports `get_adapter_for_skill`. "
        "`get_adapter` is the older name and was never re-pointed. The test would "
        "raise AttributeError at patch time; it has never run.",
    ),
    (
        "tests/integration/test_eval_e2e.py",
        "app.services.eval_service.evaluate",
    ): (
        2,
        "BACKLOG 7.33. `evaluate` is the Ragas 0.3 entry point. 7.18 moved scoring "
        "to the 0.4.x `ascore` API and the name left eval_service with it; these two "
        "sites were never re-pointed, because the integration suite has no "
        "PostgreSQL to run against and so cannot fail. Same shape as the pin above "
        "and the same reason it survived: nothing executes it.",
    ),
}
# FIXED 2026-08-10 (chore/local-postgres): the 4-site
# ("tests/integration/test_ingestion_chain.py",
#  "app.services.chunking_service.HybridChunker") pin is gone because the sites are
# gone, not because the pin was dropped. All four now patch
# `docling.chunking.HybridChunker` — the module chunk_document imports the name FROM
# at call time — and the module is gated by pytest.importorskip("docling.chunking"),
# matching tests/unit/test_chunking_service.py. Re-measured with this module's
# __main__ in the same commit: targets_scanned 1283, unresolvable_sites 1,
# pinned_targets 1. The scanner only inspects `app.*` targets, so the repointed sites
# leave its field of view entirely; tests/unit/test_pipeline_patch_targets.py is what
# keeps `docling.chunking.HybridChunker` honest from here on.


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


def _measure() -> dict[str, int]:
    """The three counts this module is the authority for.

    Anything written about those counts in a commit message or a phase record should be
    produced by running this, not retyped from a terminal scroll-back:

        cd apps/api && .venv/Scripts/python.exe tests/unit/test_patch_targets_resolve.py
    """
    unresolved = _unresolved()
    return {
        "targets_scanned": sum(
            len(_app_patch_targets(f.read_text(encoding="utf-8", errors="replace")))
            for f in _TESTS.rglob("*.py")
        ),
        "unresolvable_sites": len(unresolved),
        "pinned_targets": len({(rel, target) for rel, target, _, _ in unresolved}),
    }


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


def test_known_broken_site_counts_are_exact():
    """How MANY sites each pin covers is part of the pin, not a number someone remembers.

    Without this, `_KNOWN_BROKEN` says only *that* a (file, target) pair is broken. A
    later phase that fixes three of the four `HybridChunker` sites would leave both
    assertions above green while the defect is still live, and a phase that reads the
    record's headline count has nothing to check it against — which is exactly how the
    P2 log came to claim six unresolvable targets against a tree that has five.
    """
    live = Counter((rel, target) for rel, target, _, _ in _unresolved())
    drifted = [
        f"  {rel}  {target}  pinned {expected} site(s), found {live.get((rel, target), 0)}"
        for (rel, target), (expected, _) in sorted(_KNOWN_BROKEN.items())
        if live.get((rel, target), 0) != expected
    ]
    assert not drifted, (
        "Pinned site counts no longer match the suite. Fixing sites is good — update the "
        "count (or drop the entry) in the same commit:\n" + "\n".join(drifted)
    )

    expected_total = sum(count for count, _ in _KNOWN_BROKEN.values())
    measured = _measure()
    assert measured["unresolvable_sites"] == expected_total, (
        f"{measured['unresolvable_sites']} unresolvable sites, but the pins account for "
        f"{expected_total}. Every unresolvable site must be pinned with its count."
    )
    assert measured["pinned_targets"] == len(_KNOWN_BROKEN), (
        f"{measured['pinned_targets']} distinct (file, target) pairs are unresolvable, "
        f"but _KNOWN_BROKEN has {len(_KNOWN_BROKEN)} entries."
    )


def test_the_scan_actually_finds_targets():
    """A silent scan of zero files would make both assertions above vacuous."""
    total = _measure()["targets_scanned"]
    assert total > 500, f"expected the suite's ~1150 app.* patch targets, scanned {total}"


if __name__ == "__main__":  # pragma: no cover - a re-measurement entry point, not a test
    for _name, _value in _measure().items():
        print(f"{_name:20} {_value}")
