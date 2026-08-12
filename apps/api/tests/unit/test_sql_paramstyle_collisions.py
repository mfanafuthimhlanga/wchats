r""":param::type is a BUG CLASS. This module is the gate that ends it.

Eight instances across this repo's history: five in tests (BACKLOG 1.1) and three
in production code, found 2026-08-11/12 (BACKLOG 1.14) —
`deployment_service.py` x2 and `digest.py` x1. Counting a ninth by hand is not a
strategy, so this scans instead.

THE MECHANISM, which is worse than "the parameter is not bound".

SQLAlchemy's bindparam regex is `(?<![:\w\x5c]):(\w+)(?!:)`. The trailing `(?!:)`
is there deliberately, to avoid mistaking PostgreSQL's `::` cast operator for a
parameter. Against `:window_days::text` the greedy `\w+` matches `window_days`,
fails the lookahead on the first `:` of `::`, and **backtracks one character** —
so SQLAlchemy binds a parameter named `window_day`. The name the call site
actually passes then matches nothing, the literal `:` survives into the SQL, and
Postgres raises `syntax error at or near ":"`.

    'SELECT :window_days::text'         -> ['window_day']   trailing 's' eaten
    'SELECT :payload::jsonb'            -> ['payloa']        trailing 'd' eaten
    'SELECT :b::int'                    -> []                matches nothing
    'SELECT CAST(:window_days AS text)' -> ['window_days']   correct

A silently misnamed parameter is why five phases of review read past these: the
string looks exactly right.

WHY NO EXISTING TEST CAUGHT IT: every test of those paths mocks the session, and
a MagicMock accepts any string. `test_digest_service.py` has four tests, and the
one that reaches the INSERT region seeds `fetchone` to return a row so the
function returns *early* — the INSERT never executes. A SQL string that no
database ever parses is not a query, it is a comment. (retro.md Family I.)

The fix everywhere is `CAST(:p AS type)`. A space (`:p ::type`) also works, by
defeating the same lookahead, but it reads as a typo and invites re-breaking, so
the gate below rejects the bare form and the codebase uses CAST.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import text

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: The defect shape: a :bindparam immediately followed by a :: cast.
COLLISION_RE = re.compile(r":[A-Za-z_][A-Za-z0-9_]*::")


# ---------------------------------------------------------------------------
# 1. The characterization tests — WHY the gate exists.
#
# These assert SQLAlchemy's actual behaviour. If a future SQLAlchemy fixes the
# regex, these fail, and the gate below can be relaxed DELIBERATELY rather than
# quietly rotting into a rule nobody remembers the reason for.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT :window_days::text", ["window_day"]),
        ("SELECT :payload::jsonb", ["payloa"]),
        ("SELECT :a, :b::int", ["a"]),
        ("SELECT CAST(:window_days AS text)", ["window_days"]),
        ("SELECT CAST(:payload AS jsonb)", ["payload"]),
    ],
)
def test_sqlalchemy_truncates_a_bindparam_that_abuts_a_cast(sql: str, expected: list[str]) -> None:
    assert sorted(text(sql)._bindparams) == sorted(expected)


def test_the_truncated_name_is_not_the_name_the_call_site_passes() -> None:
    """The whole defect in one assertion.

    The call site passes `window_days`. SQLAlchemy is looking for `window_day`.
    Nothing errors at construction time — it fails only when Postgres sees it.
    """
    bound = set(text("SELECT :window_days::text")._bindparams)
    assert "window_days" not in bound
    assert bound == {"window_day"}


# ---------------------------------------------------------------------------
# 2. THE GATE.
# ---------------------------------------------------------------------------


def _python_files() -> list[Path]:
    return [p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def _string_literals_with_lines(tree: ast.AST) -> list[tuple[int, str]]:
    """Every string constant in the module, with its line number.

    Scanning raw source text would trip on comments and docstrings that *discuss*
    the pattern — including the ones this fix deliberately added next to each
    repaired statement. BACKLOG 4.x / retro Family F record over-broad mechanical
    gates producing false positives on their own prose; this reads only the
    strings that can actually reach a database.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            # Substitute a SENTINEL for each {interpolation} rather than dropping
            # it. Dropping fuses the literals either side, and the first version
            # of this scan did exactly that: `f"rate:config:{client_ip}:{bucket}"`
            # collapsed to `rate:config::` and reported four Redis key builders in
            # widget.py as SQL defects. That is retro Family F — a mechanical gate
            # producing false positives — caught here by the gate's own first run.
            # \x00 cannot appear in an identifier and is not ':', so it cannot
            # create or destroy a match.
            joined = "".join(
                v.value if isinstance(v, ast.Constant) else "\x00" for v in node.values
            )
            if joined:
                out.append((node.lineno, joined))
    return out


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Line numbers of docstrings, which are prose rather than SQL."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc and node.body and isinstance(node.body[0], ast.Expr):
                lines.add(node.body[0].lineno)
    return lines


def test_no_bindparam_abuts_a_cast_anywhere_in_app() -> None:
    """No `:param::type` in any string literal under app/.

    This is the durable half of the BACKLOG 1.14 fix. Fixing three call sites
    fixes three call sites; this fixes the class.
    """
    offenders: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken module fails elsewhere
            continue
        docstrings = _docstring_nodes(tree)
        for lineno, literal in _string_literals_with_lines(tree):
            if lineno in docstrings:
                continue
            for match in COLLISION_RE.finditer(literal):
                rel = path.relative_to(APP_ROOT.parent)
                offenders.append(f"{rel}:{lineno}: {match.group(0)}")

    assert not offenders, (
        "SQL bindparam(s) immediately followed by a `::` cast:\n  "
        + "\n  ".join(offenders)
        + "\n\nSQLAlchemy's bindparam regex is (?<![:\\w\\x5c]):(\\w+)(?!:). Against "
        "`:window_days::text` it backtracks one character and binds `window_day`, so "
        "the name the call site passes matches NOTHING, the literal ':' reaches "
        "Postgres, and the statement raises `syntax error at or near \":\"`.\n"
        "Write CAST(:param AS type) instead. This exact defect shipped three times "
        "in production code (BACKLOG 1.14): the deploy checklist's entire "
        "blast-radius section returned None on every run, and run_weekly_digest "
        "raised on every run so no digest was ever sent."
    )


# ---------------------------------------------------------------------------
# 3. Per-site proofs — the three statements BACKLOG 1.14 names, by construction.
#
# The gate above is a text scan; these assert the repaired statements actually
# bind the names their call sites pass. Both halves are the claim.
# ---------------------------------------------------------------------------


def _statement_texts(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "text":
            if node.args:
                try:
                    out.append(ast.literal_eval(node.args[0]))
                except (ValueError, SyntaxError):
                    continue
    return [s for s in out if isinstance(s, str)]


def test_the_blast_radius_queries_bind_window_days() -> None:
    """BACKLOG 1.14: both blast-radius statements, by their real source."""
    path = APP_ROOT / "services" / "deployment_service.py"
    stmts = [s for s in _statement_texts(path) if "tool_calls_audit" in s and "window_days" in s]
    assert len(stmts) == 2, f"expected the 2 blast-radius statements, found {len(stmts)}"
    for sql in stmts:
        bound = set(text(sql)._bindparams)
        assert "window_days" in bound, (
            f"blast-radius statement does not bind `window_days` (bound: {sorted(bound)}). "
            "Its call site passes window_days=..., so this raises at the database."
        )
        assert "agent_id" in bound


def test_the_digest_insert_binds_payload() -> None:
    """BACKLOG 1.14: the WR-02 idempotency anchor, which raised on every run."""
    path = APP_ROOT / "worker" / "tasks" / "runtime" / "digest.py"
    stmts = [s for s in _statement_texts(path) if "digest_runs" in s and "INSERT" in s]
    assert len(stmts) == 1, f"expected 1 digest_runs INSERT, found {len(stmts)}"
    bound = set(text(stmts[0])._bindparams)
    assert bound == {"agent_id", "payload"}, (
        f"the digest INSERT binds {sorted(bound)}; its call site passes agent_id and "
        "payload. A mismatch here means run_weekly_digest raises on every run and "
        "send_digest_email is never reached."
    )
