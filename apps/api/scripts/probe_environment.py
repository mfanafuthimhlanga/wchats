r"""What this environment ACTUALLY allows. Run it instead of quoting a constraint.

    .venv/Scripts/python.exe scripts/probe_environment.py

This exists because of BACKLOG 7.36. CLAUDE.md said "no PostgreSQL server on this
machine, confirmed repeatedly" for eight days after one was installed, and that
sentence was copied into a migration docstring, its test file, two BACKLOG rows,
HANDOFF, a plan and a trace inside a single session before anyone opened a
socket. On 2026-08-18 the same thing happened again with `7.32`: "control DB
credential rejected, nothing can run" was quoted all session, and when finally
probed the credential really was rejected but the work did not need that database
at all.

An environment limit is a measurement with a date on it. This script is the
measurement.

WHAT IT DOES NOT DO
    No writes. No migrations. No LLM calls. No money. It is safe to run at the
    start of any session, and it is deliberately NOT a test: it lives under
    scripts/ so pytest never collects it, because it needs the network and
    CLAUDE.md keeps network-dependent checks out of every gate.

WHAT IT NEVER PRINTS
    No credential value, ever. Key names, whether they are set, string lengths,
    and hostnames only. Any exception text is passed through a mask that strips
    `://user:password@`.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import socket
import sys
import urllib.error
import urllib.request

API_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent

#: Read from .env, reported by name only. The list is what a live run needs; see
#: HANDOFF's "Running anything locally".
KEYS = [
    "CONTROL_DB_URL", "CONTROL_DB_SYNC_URL", "AGENT_ID", "API_KEY",
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "NEON_API_KEY",
    "NEON_ENCRYPTION_KEY", "PLATFORM_CREDENTIAL_KEY", "REDIS_URL",
    "EMBEDDING_PROVIDER", "VOYAGE_API_KEY", "S3_ENDPOINT_URL", "S3_UPLOADS_BUCKET",
]

#: The local cluster CLAUDE.md documents. Disposable, fsync=off.
LOCAL_DBS = ("wchats_control", "wchats_tenant_probe")

_CREDENTIAL_IN_URL = re.compile(r"://[^@\s/]*@")


def mask(text: object) -> str:
    """Strip `://user:password@` from anything before it is printed."""
    return _CREDENTIAL_IN_URL.sub("://***@", str(text))


def read_env(path: pathlib.Path) -> dict[str, str]:
    """Parse a .env into a dict. Values are returned but must not be printed."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def report_env_files() -> dict[str, str]:
    """Which keys each .env carries. Returns the file pydantic will actually load.

    Two tracked examples and two real files exist, and WHICH ONE LOADS IS
    POSITIONAL: `_find_env_file()` walks up from app/core/config.py and stops at
    the first `.env`, so `apps/api/.env` wins permanently once it exists
    (BACKLOG 1.21). Both are reported because a key present in the loser reads as
    configured and is not.
    """
    winner: dict[str, str] = {}
    for label, path in (("apps/api/.env", API_DIR / ".env"), ("repo-root/.env", REPO_ROOT / ".env")):
        env = read_env(path)
        state = "exists" if path.exists() else "MISSING"
        print(f"\n  {label}  ({state}, {len(env)} keys)")
        for key in KEYS:
            value = env.get(key)
            print(f"    {key:26} {'set, len ' + str(len(value)) if value else 'ABSENT'}")
        if not winner and env:
            winner = env
    return winner


def report_exported() -> None:
    """`.env` is not `os.environ`, and the difference has cost four debug cycles.

    Pydantic loads `.env` into Settings; the Anthropic client reads os.environ. A
    worker started without ANTHROPIC_API_KEY exported loses every direct-API
    call, and at least one task reports success anyway (BACKLOG 1.28).
    """
    print("\n  exported into os.environ (NOT the same as present in .env):")
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        print(f"    {key:26} {'exported' if os.environ.get(key) else 'NOT exported'}")


def probe_postgres(label: str, dsn: str, timeout: int = 20) -> str | None:
    """Connect, report the alembic revision and table count. Returns the revision."""
    try:
        import psycopg2
    except ImportError:
        print(f"    {label:22} psycopg2 not installed")
        return None

    sync = (dsn.replace("postgresql+asyncpg://", "postgresql://")
               .replace("postgresql+psycopg://", "postgresql://"))
    try:
        conn = psycopg2.connect(sync, connect_timeout=timeout)
    except Exception as exc:
        print(f"    {label:22} REFUSED  {type(exc).__name__}: {mask(exc).strip()[:110]}")
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            revision = row[0] if row else "none"
            cur.execute("SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public'")
            tables = cur.fetchone()[0]
        print(f"    {label:22} UP       alembic={revision}, {tables} tables")
        return revision
    finally:
        conn.close()


def probe_local_cluster() -> None:
    print("\n  local PostgreSQL (localhost:5432, disposable, fsync=off):")
    for name in LOCAL_DBS:
        probe_postgres(name, f"postgresql://wchats:wchats@localhost:5432/{name}", timeout=10)


def probe_control_db(env: dict[str, str]) -> None:
    """The .env control DB. This is the one `7.32` is about."""
    dsn = env.get("CONTROL_DB_SYNC_URL") or env.get("CONTROL_DB_URL", "")
    print("\n  control DB named in .env:")
    if not dsn:
        print("    (no CONTROL_DB_URL or CONTROL_DB_SYNC_URL)")
        return
    host = re.search(r"@([^/]+)/", dsn)
    print(f"    host                   {host.group(1) if host else '(unparsed)'}")
    probe_postgres("control", dsn, timeout=30)


def neon_get(key: str, path: str, timeout: int = 90) -> dict:
    request = urllib.request.Request(
        f"https://console.neon.tech/api/v2{path}",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def probe_neon(env: dict[str, str]) -> None:
    """What NEON_API_KEY can see. A key that sees one project cannot fix another.

    Per-tenant Neon projects mean the platform holds many; this key is scoped,
    and the scope is the thing worth knowing before deciding something is
    blocked. Read-only: no branch is created, nothing is reset.
    """
    key = env.get("NEON_API_KEY", "")
    print("\n  Neon API:")
    if not key:
        print("    NEON_API_KEY absent")
        return

    print("    TCP 443 console.neon.tech ", end="")
    try:
        with socket.create_connection(("console.neon.tech", 443), timeout=15):
            print("open")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}")
        return

    try:
        projects = neon_get(key, "/projects").get("projects", [])
    except urllib.error.HTTPError as exc:
        print(f"    GET /projects -> HTTP {exc.code} {exc.reason}")
        return
    except Exception as exc:
        print(f"    GET /projects -> {type(exc).__name__}: {exc}")
        return

    print(f"    the key sees {len(projects)} project(s):")
    for project in projects:
        print(f"      {project['id']:26} name={project.get('name','')!r} "
              f"region={project.get('region_id')}")
    if projects:
        print("    (a tenant connection URI for any of these is retrievable with "
              "GET /projects/{id}/connection_uri)")


def _redis_ping(label: str, host: str, port: int) -> bool:
    """PING, not just a TCP connect.

    A TCP connect to a Neon or Upstash proxy succeeds while the thing behind it
    is asleep or gone, which is why HANDOFF's pre-flight says "TCP connect lies".
    A `+PONG` is the server answering.
    """
    print(f"    {label:34} ", end="")
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(b"PING\r\n")
            reply = sock.recv(64).decode(errors="ignore").strip()
        print(f"open, reply {reply!r}")
        return reply.startswith("+PONG")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}")
        return False


def probe_redis(env: dict[str, str]) -> None:
    """Both the Redis named in .env and the local one the overlay uses.

    Reporting only the .env one turns a working environment into a blocker: on
    2026-08-18 the Upstash host stopped resolving while local Redis answered
    PING, and HANDOFF's own overlay points every local run at localhost anyway.
    Queue DEPTH is out of scope: it needs a client this script will not import,
    and draining the `runtime` queue before a costed run is a separate step.
    """
    print("\n  Redis:")
    url = env.get("REDIS_URL", "")
    if not url:
        print("    REDIS_URL absent from .env")
    else:
        match = re.search(r"@?([^@/:]+):(\d+)", url.split("//", 1)[-1])
        if match:
            _redis_ping(f".env host {match.group(1)}", match.group(1), int(match.group(2)))
        else:
            print("    (.env REDIS_URL host unparsed)")
    _redis_ping("localhost:6379 (the overlay's)", "127.0.0.1", 6379)


def main() -> int:
    print("W Chats environment probe. Read-only. Prints no credential values.")
    print("=" * 78)
    print("\nENV FILES")
    env = report_env_files()
    report_exported()
    print("\n" + "=" * 78)
    print("\nDATABASES")
    probe_local_cluster()
    probe_control_db(env)
    print("\n" + "=" * 78)
    print("\nEXTERNAL")
    probe_neon(env)
    probe_redis(env)
    print("\n" + "=" * 78)
    print("\nEvery line above is a measurement taken just now. Quote it with today's")
    print("date, or re-run this script rather than repeating it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
