r"""Apply and VERIFY tenant migrations against one Neon project, by id.

    .venv/Scripts/python.exe scripts/migrate_tenant.py <project_id> --check
    .venv/Scripts/python.exe scripts/migrate_tenant.py <project_id> --apply

`--check` reads the current revision and reports what an upgrade would do. It
writes nothing. `--apply` runs `alembic upgrade head` through
`run_tenant_migrations`, which is the production path, then re-reads the database
and prints what actually changed.

WHY THIS IS A TRACKED SCRIPT AND NOT A ONE-OFF
    Migration 0017 was applied to the live tenant `mute-dream-53534177` on
    2026-08-18 by a script in a temp directory, which left no reproducible record
    of what ran against a production database. Per-tenant Neon projects mean this
    is not a one-off either: every tenant needs it, and M4 will need it per
    deploy.

WHY IT TAKES A PROJECT ID AND NOT A CONNECTION STRING
    So no credential is ever typed on a command line, pasted into a terminal
    history, or captured in a process list. The connection URI is fetched from
    the Neon API with `NEON_API_KEY` and never printed.

THE POOLED ENDPOINT IS REFUSED
    Alembic through PgBouncer causes SILENT migration failures (RESEARCH.md
    Pitfall 1). The API is asked for `pooled=false` and the host is asserted, so
    a Neon change that starts returning a pooled URI stops this script rather
    than half-migrating a tenant.

    Rule 6: no Docker. Rule 9: per-tenant Neon projects, which is why this is
    parameterised rather than reading CONTROL_DB_URL.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

API_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

NEON_API = "https://console.neon.tech/api/v2"

#: Columns worth asserting after an upgrade, keyed by the revision that adds them.
#: A revision absent here is applied and its revision checked, nothing more.
VERIFIED_COLUMNS = {
    # BACKLOG 7.34. Nullable with NO DEFAULT is the whole point: NULL means the
    # capture recorded nothing and `[]` means a retrieve ran and matched nothing.
    # A DEFAULT would collapse the two, and BACKLOG 5.16 is the cost of that.
    "0017": ("tool_calls", "retrieved_chunks", "jsonb", "YES", None),
    # Ticket #42. NOT NULL with DEFAULT false is the opposite call from 0017, and
    # for the opposite reason. A chunk came from the table path or it did not,
    # there is no third observation, and false is what every pre-0018 row means.
    "0018": ("chunks", "is_table", "boolean", "NO", "false"),
    # Ticket #46. NOT NULL with NO DEFAULT, which neither 0017 nor 0018 asks for.
    # The response hook always knows when the call happened, and pricing reads the
    # CAT peak window off this instant. A DEFAULT now() would price a row that lost
    # its instant at whenever it reached the database.
    "0019": ("model_calls", "at", "timestamp with time zone", "NO", None),
}


def read_env_value(key: str) -> str:
    """One key from apps/api/.env. Returned, never printed."""
    path = API_DIR / ".env"
    if not path.exists():
        raise SystemExit(f"{path} does not exist")
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    raise SystemExit(f"{key} is not set in {path}")


def direct_connection_uri(project_id: str) -> str:
    """The NON-POOLED URI for a project's default database. Never printed."""
    key = read_env_value("NEON_API_KEY")
    request = urllib.request.Request(
        f"{NEON_API}/projects/{project_id}/connection_uri"
        "?database_name=neondb&role_name=neondb_owner&pooled=false",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            uri = json.loads(response.read().decode())["uri"]
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"Neon API refused: HTTP {exc.code} {exc.reason}. NEON_API_KEY may not "
            f"be scoped to project {project_id}; run scripts/probe_environment.py "
            "to see which projects it can reach."
        ) from exc

    host_match = re.search(r"@([^/]+)/", uri)
    host = host_match.group(1) if host_match else "(unparsed)"
    print(f"direct host: {host}")
    if "-pooler." in host:
        raise SystemExit(
            "Neon returned a POOLED endpoint. Alembic through PgBouncer fails "
            "silently, so this refuses rather than half-migrating the tenant."
        )
    return uri


def inspect(uri: str) -> dict:
    """Revision, table count, and the columns VERIFIED_COLUMNS cares about."""
    import psycopg2

    conn = psycopg2.connect(uri, connect_timeout=60)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            revision = row[0] if row else None
            cur.execute("SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public'")
            tables = cur.fetchone()[0]
            columns = {}
            for rev, (table, column, *_expected) in VERIFIED_COLUMNS.items():
                cur.execute(
                    "SELECT data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_name = %s AND column_name = %s",
                    (table, column),
                )
                columns[rev] = cur.fetchone()
    finally:
        conn.close()
    return {"revision": revision, "tables": tables, "columns": columns}


def render(label: str, state: dict) -> None:
    print(f"{label:8} revision={state['revision']}  {state['tables']} tables")
    for rev, found in state["columns"].items():
        table, column, *_ = VERIFIED_COLUMNS[rev]
        print(f"         {rev}: {table}.{column} = {found}")


def verify(before: dict, after: dict) -> bool:
    """Did the upgrade produce the columns their revisions promise?"""
    ok = True
    print(f"\n  revision {before['revision']} -> {after['revision']}")
    for rev, (table, column, data_type, nullable, default) in VERIFIED_COLUMNS.items():
        found = after["columns"].get(rev)
        if found is None:
            print(f"  {rev}: {table}.{column} ABSENT after upgrade")
            ok = False
            continue
        got_type, got_nullable, got_default = found
        for name, got, want in (("data_type", got_type, data_type),
                                ("is_nullable", got_nullable, nullable),
                                ("column_default", got_default, default)):
            mark = "ok " if got == want else "NO "
            print(f"  {mark}{rev}: {table}.{column}.{name} = {got!r} (want {want!r})")
            ok = ok and got == want
    return ok


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1 or not ("--check" in argv or "--apply" in argv):
        print(__doc__)
        return 2
    project_id = args[0]
    applying = "--apply" in argv

    uri = direct_connection_uri(project_id)
    before = inspect(uri)
    render("BEFORE", before)

    if not applying:
        print("\n--check only: nothing was written. Re-run with --apply to upgrade.")
        return 0

    from app.services.migrations import run_tenant_migrations  # noqa: PLC0415

    print("\nrunning alembic upgrade head (production path)...")
    run_tenant_migrations(uri)
    print("done\n")

    after = inspect(uri)
    render("AFTER", after)
    ok = verify(before, after)
    print("\nVERIFIED" if ok else "\nUNEXPECTED STATE - do not rely on this tenant")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
