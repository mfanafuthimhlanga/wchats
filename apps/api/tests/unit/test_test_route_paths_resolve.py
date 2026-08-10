"""Every URL path an integration/E2E test posts to must exist on the app.

Why this guard exists
---------------------
Eight assertions in the integration suite addressed routes without the
``/api/v1`` prefix that ``app/main.py`` mounts them under. They were fixed on
2026-08-10, and on 2026-08-11 two more were found in the same shape —
``test_agent_chat_integration.py`` and ``test_agent_e2e.py``, both posting to
``/agents/{id}/chat`` while ``main.py:175`` mounts ``agent_chat.router`` at
``/api/v1``.

The two survivors are the whole argument for a scan rather than another sweep.
Both sit behind ``INTEGRATION_TESTS_ENABLED`` / ``AGENT_E2E_ENABLED``, so they
have never been collected on any run, and a grep pass that fixes what it can see
cannot see them. Worse, a wrong path is not always a loud failure: a test
asserting a 4xx passes on the routing 404 it gets instead — that is exactly how
``test_post_query_agent_not_found`` read green while addressing nothing.

This module needs no database, no Redis and no env flag, so it runs in the unit
gate and covers the skipped files as well as the running ones.

What is compared
----------------
Path SHAPES, not paths. A test's ``f"/api/v1/agents/{agent_id}/chat"`` becomes
``/api/v1/agents/{}/chat``; the app's ``/api/v1/agents/{agent_id}/chat`` becomes
the same. Interpolated segments therefore match any parameter name, which is the
right strictness: the test cannot know the route's parameter names, only its
shape.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[1].parent
_SCANNED_DIRS = (
    _API_ROOT / "tests" / "integration",
    _API_ROOT / "tests" / "e2e",
)

#: HTTP-client call names whose first path-shaped argument is a URL.
_REQUEST_METHODS = {
    "get", "post", "put", "patch", "delete", "head", "options", "request", "stream",
}

#: Paths that are deliberately not app routes. Each needs a reason, and the
#: entry is a pin: a fixed path must be REMOVED from here in the same commit.
_NOT_APP_ROUTES: dict[str, str] = {}

_PARAM = re.compile(r"\{[^}]*\}")

#: Call sites the scan found on 2026-08-11, across both directories. A floor
#: rather than an exact count: it catches the scanner going blind (an AST shape
#: it stops recognising, a directory that moves) without failing every time a
#: request is added. Lower it only alongside the tests that were deleted.
_MIN_SCANNED_CALL_SITES = 9


def _shape(path: str) -> str:
    """Normalise a path to its shape: query stripped, parameters anonymised."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 1:
        path = path.rstrip("/")
    return _PARAM.sub("{}", path)


def _literal_path(node: ast.AST) -> str | None:
    """The path shape of *node* if it is a path-looking string, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if node.value.startswith("/") else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{}")
            else:  # pragma: no cover — no other JoinedStr member type exists
                return None
        joined = "".join(parts)
        return joined if joined.startswith("/") else None
    return None


def _requested_paths() -> list[tuple[Path, int, str]]:
    """(file, line, shape) for every URL an HTTP client call is given."""
    found: list[tuple[Path, int, str]] = []
    for directory in _SCANNED_DIRS:
        for path in sorted(directory.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr not in _REQUEST_METHODS:
                    continue
                for arg in node.args:
                    literal = _literal_path(arg)
                    if literal is not None:
                        found.append((path, node.lineno, _shape(literal)))
                        break
    return found


def _app_route_shapes() -> set[str]:
    from app.main import app

    return {_shape(route.path) for route in app.routes if hasattr(route, "path")}


def test_the_scan_finds_the_paths_it_is_supposed_to_govern():
    """A scanner that matches nothing would pass over any breakage at all."""
    paths = _requested_paths()
    assert len(paths) >= _MIN_SCANNED_CALL_SITES, (
        f"only {len(paths)} client paths found across {[str(d) for d in _SCANNED_DIRS]}; "
        "the AST scan has stopped seeing the call sites it exists to check"
    )
    assert any(shape.startswith("/api/v1/") for _, _, shape in paths), (
        "no /api/v1 path found at all — the scan is not reaching the prefixed routes"
    )


def test_every_requested_path_exists_on_the_app():
    routes = _app_route_shapes()
    unresolved = [
        (path, lineno, shape)
        for path, lineno, shape in _requested_paths()
        if shape not in routes and shape not in _NOT_APP_ROUTES
    ]
    assert not unresolved, "\n".join(
        [
            "These test call sites address paths the app does not mount. A "
            "request to one is a 404, which fails loudly where a 2xx is asserted "
            "and passes SILENTLY where a 4xx is:",
            *(
                f"  {p.relative_to(_API_ROOT)}:{ln}  {shape}"
                for p, ln, shape in unresolved
            ),
            "Fix the path, or add it to _NOT_APP_ROUTES with the reason it is "
            "deliberately not an app route.",
        ]
    )


@pytest.mark.parametrize("shape", sorted(_NOT_APP_ROUTES))
def test_pinned_non_routes_are_still_not_routes(shape):
    """An exemption that has become resolvable must be deleted, not carried."""
    assert shape not in _app_route_shapes(), (
        f"{shape} now exists on the app; remove it from _NOT_APP_ROUTES — a "
        "stale exemption hides the next real breakage behind it"
    )
