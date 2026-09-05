r"""The API service's `preDeployCommand`: the control DB first, then the fleet.

    python scripts/predeploy.py            # migrate the control DB, then every tenant
    python scripts/predeploy.py --list     # read both sets of revisions, write nothing

WHY THIS EXISTS
    `railway.api.toml` ran `scripts/migrate_all_tenants.py` and nothing else, so
    every release brought the TENANT databases to head and left the CONTROL
    database wherever the last hand-run put it. A merge carrying a control
    migration therefore shipped code against a schema the control DB did not
    have. Observed 2026-09-04: staging's control DB sat at 0020 while `main` was
    at 0022, and the owner ran the upgrade by hand to close the gap. Nothing at
    deploy time would ever have closed it.

WHY THE CONTROL DB GOES FIRST
    The control DB is where the fleet is read FROM. `fleet_rows()` queries
    `agents`, so a column a control migration adds is a column that query may
    name; running the walk first would read the old schema with new code. The
    walk does not start at all when the control migration failed, because a
    control DB that is behind makes every tenant result unreliable.

WHY IT EXITS NONZERO
    Railway aborts a deployment whose pre-deploy command fails, and the old code
    keeps serving. Old code fits the old schema, so a release that could not
    migrate the control DB is a release that must not ship.

WHY NO CONNECTION STRING IS EVER LOGGED
    `CLAUDE.md` rule 1, and T-03-02: a release log outlives the deployment. The
    lines below carry the revision the control DB moved between and the class of
    any exception, which is what an operator acts on. The DSN, the host and the
    role appear in none of them.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import structlog

API_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

log = structlog.get_logger(__name__)

#: The fleet walk, loaded by path because `scripts/` is not a package.
TENANT_WALK_PATH = API_DIR / "scripts" / "migrate_all_tenants.py"


# ---------------------------------------------------------------------------
# The three seams. Thin on purpose: a test replaces them, so the ORDER and the
# exit codes below can be asserted without a control database and without a Neon
# project per tenant. `run_control_migrations` against a live database is covered
# by tests/integration/test_migrations.py.
# ---------------------------------------------------------------------------


def _control_dsn() -> str:
    from app.core.config import settings  # noqa: PLC0415

    return settings.CONTROL_DB_SYNC_URL


def _current_revision(dsn: str) -> str | None:
    from app.services.migrations import get_current_alembic_revision  # noqa: PLC0415

    return get_current_alembic_revision(dsn)


def _run_control_migrations(dsn: str) -> None:
    from app.services.migrations import run_control_migrations  # noqa: PLC0415

    run_control_migrations(dsn)


def _tenant_walk(argv: list[str]) -> int:
    """Run `scripts/migrate_all_tenants.py`'s `main` in this process.

    The module goes into `sys.modules` before it executes because `@dataclass`
    resolves annotations through `sys.modules[cls.__module__]`.
    """
    spec = importlib.util.spec_from_file_location(
        "migrate_all_tenants", TENANT_WALK_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load the fleet walk at {TENANT_WALK_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return int(module.main(argv))


def migrate_control_db(*, apply: bool = True) -> int:
    """Bring the control DB to head. 0 when it is at head, 1 otherwise.

    Args:
        apply: False reads the revision and writes nothing, for `--list`.

    Returns:
        An exit code. Every failure is caught here so the caller can decide
        that the tenant walk does not run, rather than a traceback deciding it.
    """
    before: str | None = None
    try:
        dsn = _control_dsn()
        before = _current_revision(dsn)
        if not apply:
            log.info("predeploy.control_read", revision=before)
            return 0
        _run_control_migrations(dsn)
        after = _current_revision(dsn)
    except Exception as exc:
        # Only the class. The message from psycopg2 names the host and the role
        # that opens the control database, and this line lands on a release log.
        log.error(
            "predeploy.control_failed",
            error_type=type(exc).__name__,
            revision_before=before,
            rollback=(
                "the upgrade ran in one transaction, so the control DB is at "
                "revision_before and is unchanged"
            ),
        )
        print(
            "The control database did not reach head, so no tenant was "
            "migrated. The deployment is refused and the old code keeps "
            f"serving. Failure class: {type(exc).__name__}. Check "
            "CONTROL_DB_SYNC_URL and whether that Neon compute is awake."
        )
        return 1

    log.info(
        "predeploy.control_migrated",
        revision_before=before,
        revision_after=after,
    )
    return 0


def main(argv: list[str]) -> int:
    apply = "--list" not in argv

    log.info("predeploy.starting", apply=apply)

    control = migrate_control_db(apply=apply)
    if control != 0:
        return control

    return _tenant_walk(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
