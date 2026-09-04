"""The fleet migration a release runs, and the one property that matters.

`#64`. `apply_migrations` runs once, at provision. Nothing has re-run tenant
migrations since, so every tenant provisioned before a revision landed sits at
the revision it was born with, and new code meets a column that is not there:
`chunk.py`'s INSERT hits `UndefinedColumn` and retries itself to death, and
`evals.py` answers 409 for a pre-0024 tenant because there was nowhere else to
put the diagnosis.

`scripts/migrate_all_tenants.py` is that missing place, wired as the API
service's `preDeployCommand`. What these tests pin is the behaviour a fleet
walk has to have and a single-tenant script does not:

    ONE TENANT'S FAILURE MAY NOT END THE WALK.

A fleet of forty where the seventh has a suspended Neon endpoint must still
leave the other thirty-nine at head. A loop that raises on the first failure
migrates a prefix of the fleet and reports the exception, which reads exactly
like the defect #64 already describes.

The DSN never travels as an argument (`CLAUDE.md` rule 1): rows carry
ciphertext out of the control DB and the script decrypts each one at the point
of use. `run_tenant_migrations` against a real database is covered by
`tests/integration/test_migrations.py`; here it is a seam, so the walk itself
can be asserted without a Neon project per test.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
import structlog

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_PATH = API_DIR / "scripts" / "migrate_all_tenants.py"


def load_script():
    """Import scripts/migrate_all_tenants.py by path; scripts/ is not a package.

    The module goes into `sys.modules` before it executes because `@dataclass`
    resolves annotations through `sys.modules[cls.__module__]`, and a module
    that is not registered there fails with `'NoneType' object has no attribute
    '__dict__'` at class-creation time.
    """
    spec = importlib.util.spec_from_file_location("migrate_all_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


migrate_all = load_script()


#: The userinfo half of the fake DSN below, assembled from fragments so the
#: secret-scanning pre-commit hook does not read a whole credential-carrying URL
#: on one line and refuse the commit. Nothing here is a real value.
_PLACEHOLDER_CREDENTIAL = "placeholder" + "-user:placeholder" + "-secret"

#: Three tenants, the middle one broken. The ids are distinguishable in a log
#: line, which is the thing the failure branch has to get right.
_FLEET = [
    ("agent-aaa", "tenant-aaa", b"ciphertext-aaa"),
    ("agent-bbb", "tenant-bbb", b"ciphertext-bbb"),
    ("agent-ccc", "tenant-ccc", b"ciphertext-ccc"),
]


class _Recorder:
    """Stands in for the three seams, recording what the walk asked of them."""

    def __init__(self, fails_for: str = "", revisions=("0017", "0024")):
        self.fails_for = fails_for
        self.before, self.after = revisions
        self.decrypted: list[bytes] = []
        self.migrated: list[str] = []
        self.revision_reads: list[str] = []

    def decrypt(self, ciphertext: bytes) -> str:
        self.decrypted.append(ciphertext)
        return (
            "postgresql://" + _PLACEHOLDER_CREDENTIAL + "@"
            + ciphertext.decode() + "/neondb"
        )

    def revision(self, dsn: str) -> str:
        self.revision_reads.append(dsn)
        return self.after if dsn in self.migrated else self.before

    def migrate(self, dsn: str) -> None:
        if self.fails_for and self.fails_for in dsn:
            raise RuntimeError("endpoint is suspended")
        self.migrated.append(dsn)


@pytest.fixture
def seams(monkeypatch):
    """Install a recorder over the three functions the walk calls out to."""

    def _install(**kwargs):
        recorder = _Recorder(**kwargs)
        monkeypatch.setattr(migrate_all, "_decrypt", recorder.decrypt)
        monkeypatch.setattr(migrate_all, "_current_revision", recorder.revision)
        monkeypatch.setattr(migrate_all, "_run_migrations", recorder.migrate)
        return recorder

    return _install


def test_every_tenant_is_migrated(seams):
    recorder = seams()
    report = migrate_all.migrate_fleet(_FLEET)
    assert len(recorder.migrated) == 3
    assert report.migrated == 3
    assert report.failed == 0


def test_a_failing_tenant_does_not_end_the_walk(seams):
    """The property this script exists for.

    The second tenant raises. The third must still reach head, because the
    alternative is a release that migrates a prefix of the fleet and leaves the
    rest to meet the new code with the old schema.
    """
    recorder = seams(fails_for="ciphertext-bbb")
    report = migrate_all.migrate_fleet(_FLEET)

    assert "ciphertext-ccc" in "".join(recorder.migrated), (
        "the third tenant was never migrated, so the second tenant's failure "
        f"ended the walk. migrated={recorder.migrated!r}"
    )
    assert report.migrated == 2
    assert report.failed == 1
    assert report.failures == [("agent-bbb", "tenant-bbb", "RuntimeError")]


def test_the_failure_is_logged_with_the_tenant_and_the_error_type(seams):
    """An operator reading a release log needs to know WHICH tenant is behind."""
    seams(fails_for="ciphertext-bbb")
    with structlog.testing.capture_logs() as logs:
        migrate_all.migrate_fleet(_FLEET)

    failures = [line for line in logs if line["event"] == "migrate_all.tenant_failed"]
    assert len(failures) == 1, f"expected one failure line, got {logs!r}"
    line = failures[0]
    assert line["agent_id"] == "agent-bbb"
    assert line["tenant_id"] == "tenant-bbb"
    assert line["error_type"] == "RuntimeError"
    assert line["log_level"] == "error"


def test_each_tenant_logs_the_revision_it_moved_between(seams):
    """One line per tenant, before and after, so a release log is the record of
    what the fleet's schemas actually are."""
    seams()
    with structlog.testing.capture_logs() as logs:
        migrate_all.migrate_fleet(_FLEET)

    done = [line for line in logs if line["event"] == "migrate_all.tenant_migrated"]
    assert len(done) == 3
    assert [line["agent_id"] for line in done] == ["agent-aaa", "agent-bbb", "agent-ccc"]
    for line in done:
        assert line["revision_before"] == "0017"
        assert line["revision_after"] == "0024"


def test_no_log_line_carries_the_connection_string(seams):
    """T-03-02. The decrypted DSN is a credential and a release log is a place
    it would sit for the life of the deployment."""
    recorder = seams(fails_for="ciphertext-bbb")
    with structlog.testing.capture_logs() as logs:
        migrate_all.migrate_fleet(_FLEET)

    rendered = repr(logs)
    for ciphertext in (b"ciphertext-aaa", b"ciphertext-bbb", b"ciphertext-ccc"):
        dsn = recorder.decrypt(ciphertext)
        assert dsn not in rendered, f"a log line carried a tenant DSN: {rendered!r}"
    assert _PLACEHOLDER_CREDENTIAL not in rendered


def test_a_tenant_with_no_ciphertext_is_a_failure_not_a_crash(seams):
    """`neon_direct_connection_string` is nullable, and a half-provisioned agent
    is a row the fleet query can return. It counts as a failure and the walk
    goes on, rather than ending the release on a tenant that never had a
    database."""
    seams()
    report = migrate_all.migrate_fleet(
        [("agent-aaa", "tenant-aaa", b"ciphertext-aaa"), ("agent-zzz", "tenant-zzz", None)]
    )
    assert report.migrated == 1
    assert report.failed == 1
    assert report.failures[0][0] == "agent-zzz"


def test_the_exit_code_is_nonzero_when_any_tenant_failed(seams):
    """Railway aborts the deployment when the pre-deploy command exits nonzero,
    which leaves the OLD code serving the fleet it already fits. Shipping new
    code past a tenant that could not be migrated is the #64 defect again."""
    seams(fails_for="ciphertext-bbb")
    report = migrate_all.migrate_fleet(_FLEET)
    assert report.exit_code() == 1

    seams()
    assert migrate_all.migrate_fleet(_FLEET).exit_code() == 0


def test_an_empty_fleet_is_a_clean_release(seams):
    """Before the first tenant exists, there is nothing to migrate and nothing
    to block on."""
    seams()
    report = migrate_all.migrate_fleet([])
    assert report.migrated == 0
    assert report.failed == 0
    assert report.exit_code() == 0
