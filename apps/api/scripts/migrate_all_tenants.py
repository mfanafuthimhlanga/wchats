r"""Run `alembic upgrade head` against EVERY tenant database in the fleet.

    python scripts/migrate_all_tenants.py            # migrate the fleet
    python scripts/migrate_all_tenants.py --list     # read revisions, write nothing

`scripts/predeploy.py` is the API service's `preDeployCommand`
(`railway.api.toml`) and this walk is its second half, so it runs once per
release, after the image is built, after the control DB has reached head, and
before any of the four services serve the new code.

WHY A RELEASE STEP AND NOT A TASK
    `apply_migrations` runs exactly once per tenant, at provision. Nothing has
    re-run it since. A tenant provisioned before revision 0018 therefore sits at
    0017 forever, and the first request that reaches the new code finds a column
    that is not there: `chunk.py`'s INSERT raises `UndefinedColumn`, retries
    three times and dies, and `evals.py` answers 409 for a pre-0024 tenant
    because there was nowhere else to put the diagnosis (#64).

    A release step is the only place the whole fleet can be brought to head
    BEFORE the code that assumes head is serving anybody.

WHY ONE TENANT'S FAILURE DOES NOT END THE WALK
    Neon computes scale to zero and a suspended endpoint can refuse a
    connection. A loop that raises on the first failure migrates a prefix of the
    fleet and reports an exception, which is the defect above wearing a
    traceback. Every tenant is attempted; the failures are counted and named.

WHY IT STILL EXITS NONZERO
    Railway aborts a deployment whose pre-deploy command fails, and the old code
    keeps serving. Old code fits the old schema, so a release that could not
    migrate a tenant is a release that must not ship. The walk finishes first,
    so one run names every tenant that needs attention rather than the first.

WHY NO CONNECTION STRING IS EVER AN ARGUMENT
    `CLAUDE.md` rule 1. The control DB hands out Fernet ciphertext; this script
    decrypts each tenant's DIRECT (non-pooled) URI at the point of use and never
    logs it. Alembic through PgBouncer fails silently, which is why the direct
    URI is the one read (RESEARCH.md Pitfall 1).
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import structlog

API_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

log = structlog.get_logger(__name__)

#: How much of an exception message reaches the release log. Long enough for
#: psycopg2's first sentence, which is the diagnosis, and short enough that a
#: driver dumping a query or a certificate chain cannot fill the log.
ERROR_MESSAGE_MAX_CHARS = 200

#: The ceiling Railway puts on a service's optional pre-deploy timeout, in
#: seconds. Unset means no timeout at all; set, it cannot exceed this.
RAILWAY_PRE_DEPLOY_TIMEOUT_MAX_S = 3600

#: Values shorter than this are left alone when redacting. A one-character role
#: name replaced everywhere would destroy the message it was meant to keep
#: readable, and nothing that short identifies a tenant anyway.
_MIN_REDACTABLE_CHARS = 4


def _redact(message: str, dsn: str) -> str:
    """The first line of *message*, with everything *dsn* identifies removed.

    psycopg2's OperationalError for a suspended or unreachable Neon compute
    reads:

        connection to server at "ep-...-.aws.neon.tech" (52.0.0.1), port 5432
        failed: FATAL:  password authentication failed for user "neondb_owner"

    which names one customer's database and the role that opens it, on a
    release log that outlives the deployment (T-03-02). The host, user and
    password are all parsed out of the DSN this walk already holds, so removing
    them needs no pattern matching against what a driver happens to print.

    What survives is the class of failure and what the server said about it,
    which is the part an operator acts on.
    """
    parsed = urlsplit(dsn)
    for value, placeholder in (
        (parsed.hostname, "<host>"),
        (parsed.username, "<user>"),
        (parsed.password, "<password>"),
    ):
        if value and len(value) >= _MIN_REDACTABLE_CHARS:
            message = message.replace(value, placeholder)
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    return first_line[:ERROR_MESSAGE_MAX_CHARS]

#: (agent_id, tenant_id, encrypted direct DSN). The third element is what the
#: control DB stores, never the plaintext.
TenantRow = tuple[str, str, bytes | None]


class MissingTenantDsn(RuntimeError):
    """An agent carries a Neon project but no encrypted direct connection URI.

    Half-provisioned: `provision_neon` wrote the project id and did not reach
    the line that writes the credential. There is no database to migrate and
    nothing this script can do about it, so it is a named failure rather than an
    `AttributeError` from inside the decrypt.
    """


@dataclass
class FleetReport:
    """What one walk did, in the shape a release log and an exit code need."""

    migrated: int = 0
    failed: int = 0
    #: (agent_id, tenant_id, exception class name) per tenant that did not move.
    failures: list[tuple[str, str, str]] = field(default_factory=list)

    def exit_code(self) -> int:
        return 1 if self.failed else 0


# ---------------------------------------------------------------------------
# The three seams. Thin on purpose: a test replaces them, so the walk above can
# be asserted without a Neon project per tenant. The real behaviour of
# run_tenant_migrations against a live database is covered by
# tests/integration/test_migrations.py.
# ---------------------------------------------------------------------------


def _decrypt(encrypted: bytes) -> str:
    from app.core.security import fernet_decrypt, require_ciphertext  # noqa: PLC0415

    return fernet_decrypt(
        require_ciphertext(encrypted, "agents.neon_direct_connection_string")
    )


def _current_revision(dsn: str) -> str | None:
    from app.services.migrations import get_current_alembic_revision  # noqa: PLC0415

    return get_current_alembic_revision(dsn)


def _run_migrations(dsn: str) -> None:
    from app.services.migrations import run_tenant_migrations  # noqa: PLC0415

    run_tenant_migrations(dsn)


def migrate_fleet(rows: list[TenantRow], *, apply: bool = True) -> FleetReport:
    """Walk every tenant, migrate it, and keep going when one of them fails.

    Args:
        rows:  (agent_id, tenant_id, encrypted direct DSN) for each tenant.
        apply: False reads each revision and writes nothing, for `--list`.

    Returns:
        FleetReport. One log line per tenant either way, carrying the revision
        it moved between or the class of the exception that stopped it. Neither
        line carries the DSN.
    """
    report = FleetReport()
    for agent_id, tenant_id, encrypted in rows:
        # Bound before the try: the failure handler reads both, and a tenant
        # whose ciphertext is missing fails before either is assigned.
        dsn = ""
        before: str | None = None
        try:
            if not encrypted:
                raise MissingTenantDsn(
                    f"agent {agent_id} has a Neon project and no encrypted "
                    f"direct connection string"
                )
            dsn = _decrypt(encrypted)
            before = _current_revision(dsn)
            if not apply:
                log.info(
                    "migrate_all.tenant_read",
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    revision=before,
                )
                report.migrated += 1
                continue
            _run_migrations(dsn)
            after = _current_revision(dsn)
            log.info(
                "migrate_all.tenant_migrated",
                agent_id=agent_id,
                tenant_id=tenant_id,
                revision_before=before,
                revision_after=after,
            )
            report.migrated += 1
        except Exception as exc:
            # `run_tenant_migrations` runs the whole `upgrade head` inside one
            # `engine.begin()`, and PostgreSQL DDL is transactional, so a tenant
            # that failed is back at `revision_before` rather than stopped
            # part-way through. The line says so, because the next question
            # after a failed release is what schema the tenant is on, and the
            # only other way to answer it is another connection to the database
            # that just refused one.
            log.error(
                "migrate_all.tenant_failed",
                agent_id=agent_id,
                tenant_id=tenant_id,
                error_type=type(exc).__name__,
                error=_redact(str(exc), dsn),
                revision_before=before,
                rollback=(
                    "the upgrade ran in one transaction, so this tenant rolled "
                    "back to revision_before and is unchanged"
                ),
            )
            report.failed += 1
            report.failures.append((agent_id, tenant_id, type(exc).__name__))
    return report


def fleet_rows() -> list[TenantRow]:
    """Every tenant database the control DB knows about, oldest first.

    `neon_project_id IS NOT NULL` is the filter that makes "tenant database"
    mean something: an agent still queued for provisioning has no database, so
    it is not behind on migrations and must not fail a release. `deleted_at IS
    NULL` drops tenants whose Neon project is gone.
    """
    from app.core.database import get_sync_db  # noqa: PLC0415
    from app.models.agent import Agent  # noqa: PLC0415

    with get_sync_db() as db:
        rows = (
            db.query(
                Agent.id, Agent.tenant_id, Agent.neon_direct_connection_string
            )
            .filter(Agent.neon_project_id.isnot(None))
            .filter(Agent.deleted_at.is_(None))
            .order_by(Agent.created_at)
            .all()
        )
    return [(str(agent_id), str(tenant_id), encrypted) for agent_id, tenant_id, encrypted in rows]


def main(argv: list[str]) -> int:
    apply = "--list" not in argv

    try:
        rows = fleet_rows()
    except Exception as exc:
        # The control DB is one connection, made before any tenant is known, and
        # Neon suspends that compute like every other. A traceback out of a
        # preDeployCommand tells the operator the release failed somewhere
        # inside psycopg2; this says the fleet was never read, which also says
        # no tenant was touched and nothing needs undoing.
        log.error(
            "migrate_all.control_db_unreachable",
            error_type=type(exc).__name__,
        )
        print(
            "The control database could not be read, so the fleet is unknown "
            "and no tenant was migrated. The deployment is refused and the old "
            f"code keeps serving. Failure class: {type(exc).__name__}. Check "
            "CONTROL_DB_SYNC_URL and whether that Neon compute is awake."
        )
        return 1

    log.info("migrate_all.starting", tenants=len(rows), apply=apply)
    # The operator sets Railway's pre-deploy timeout by hand, and the number
    # that decides it is the fleet size: every tenant is one connection, and a
    # suspended Neon compute answers the first one only after it wakes.
    log.info(
        "migrate_all.timeout_hint",
        tenants=len(rows),
        railway_pre_deploy_timeout_max_s=RAILWAY_PRE_DEPLOY_TIMEOUT_MAX_S,
        hint=(
            f"this walk opens {len(rows)} tenant connection(s) in series, each "
            "of which may wait on a suspended Neon compute waking. Railway's "
            "pre-deploy timeout is optional and, when set, must be between 1 "
            f"and {RAILWAY_PRE_DEPLOY_TIMEOUT_MAX_S} seconds; set it above "
            f"{len(rows)} wakes plus the migrations themselves, or leave it "
            "unset so the walk is never cut in half"
        ),
    )

    report = migrate_fleet(rows, apply=apply)

    log.info(
        "migrate_all.complete",
        tenants=len(rows),
        migrated=report.migrated,
        failed=report.failed,
        failures=[f"{agent_id}:{error}" for agent_id, _tenant, error in report.failures],
    )
    if report.failed:
        print(
            f"{report.failed} of {len(rows)} tenant(s) did not reach head. The "
            f"deployment is refused: the new code assumes a schema those "
            f"tenants do not have. Named above by agent id."
        )
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
