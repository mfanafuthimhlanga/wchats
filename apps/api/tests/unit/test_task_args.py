"""
Unit tests for Celery task signatures — CTL-08 verification.

CLAUDE.md rule: "Connection strings never in Celery task args."
This test file is the automated enforcement of that rule.
It inspects the Python function signatures of provision_neon.run and
apply_migrations.run without running Celery.

Tests:
    - test_provision_neon_no_connection_string_arg: provision_neon args contain
      only (tenant_id, agent_id) — no connection_string / conn_string / conn_uri
    - test_apply_migrations_no_connection_string_arg: apply_migrations args contain
      only (result,) — no connection string parameters
    - test_provision_neon_has_required_args: tenant_id and agent_id ARE present
    - test_apply_migrations_has_result_arg: result IS present
    - test_provision_neon_acks_late: task has acks_late=True (CTL-09)
    - test_apply_migrations_acks_late: task has acks_late=True (CTL-09)
"""

import inspect

# conftest.py has already set env vars; importing tasks loads settings, which is fine.
from app.worker.tasks.pipeline.migrations import apply_migrations
from app.worker.tasks.pipeline.provision import provision_neon

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONNECTION_STRING_PARAM_NAMES = {
    "connection_string",
    "conn_string",
    "conn_uri",
    "db_url",
    "database_url",
    "dsn",
    "connection_uri",
    "neon_uri",
}


def _get_task_param_names(task_func) -> set[str]:
    """Extract parameter names from a Celery task's underlying function.

    Celery tasks wrap the function; the original function is accessible via
    task_func.run for bound tasks.
    """
    try:
        sig = inspect.signature(task_func.run)
    except AttributeError:
        sig = inspect.signature(task_func)
    return {
        name
        for name, param in sig.parameters.items()
        if name != "self"
    }


# ---------------------------------------------------------------------------
# CTL-08: provision_neon — no connection string in args
# ---------------------------------------------------------------------------


class TestProvisionNeonTaskArgs:
    def test_provision_neon_no_connection_string_arg(self):
        """provision_neon must NOT accept any connection-string-like parameter."""
        param_names = _get_task_param_names(provision_neon)
        leaked_params = _CONNECTION_STRING_PARAM_NAMES & param_names
        assert not leaked_params, (
            f"CTL-08 VIOLATION: provision_neon accepts connection-string params: "
            f"{leaked_params}. Connection strings must NEVER appear in Celery task args."
        )

    def test_provision_neon_has_tenant_id_arg(self):
        """tenant_id must be present in provision_neon signature."""
        param_names = _get_task_param_names(provision_neon)
        assert "tenant_id" in param_names, (
            f"provision_neon must accept tenant_id; found params: {param_names}"
        )

    def test_provision_neon_has_agent_id_arg(self):
        """agent_id must be present in provision_neon signature."""
        param_names = _get_task_param_names(provision_neon)
        assert "agent_id" in param_names, (
            f"provision_neon must accept agent_id; found params: {param_names}"
        )

    def test_provision_neon_acks_late(self):
        """provision_neon must have acks_late=True (CLAUDE.md requirement)."""
        assert provision_neon.acks_late is True, (
            "provision_neon.acks_late must be True — required by CLAUDE.md "
            "alongside idempotency for every Celery task."
        )

    def test_provision_neon_max_retries(self):
        """provision_neon should have max_retries set for retriable failures."""
        assert provision_neon.max_retries is not None
        assert provision_neon.max_retries > 0


# ---------------------------------------------------------------------------
# CTL-08: apply_migrations — no connection string in args
# ---------------------------------------------------------------------------


class TestApplyMigrationsTaskArgs:
    def test_apply_migrations_no_connection_string_arg(self):
        """apply_migrations must NOT accept any connection-string-like parameter."""
        param_names = _get_task_param_names(apply_migrations)
        leaked_params = _CONNECTION_STRING_PARAM_NAMES & param_names
        assert not leaked_params, (
            f"CTL-08 VIOLATION: apply_migrations accepts connection-string params: "
            f"{leaked_params}. Connection strings must NEVER appear in Celery task args."
        )

    def test_apply_migrations_has_result_arg(self):
        """apply_migrations must accept 'result' (the chained return from provision_neon)."""
        param_names = _get_task_param_names(apply_migrations)
        assert "result" in param_names, (
            f"apply_migrations must accept 'result' dict; found params: {param_names}"
        )

    def test_apply_migrations_acks_late(self):
        """apply_migrations must have acks_late=True (CLAUDE.md requirement)."""
        assert apply_migrations.acks_late is True, (
            "apply_migrations.acks_late must be True — required by CLAUDE.md "
            "alongside idempotency for every Celery task."
        )

    def test_apply_migrations_max_retries(self):
        """apply_migrations should have max_retries set for retriable failures."""
        assert apply_migrations.max_retries is not None
        assert apply_migrations.max_retries > 0
