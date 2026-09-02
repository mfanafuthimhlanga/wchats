"""The unit suite cannot reach Langfuse (issue #80).

Three modules build a module-level Langfuse client at import time, each behind
`if os.environ.get("LANGFUSE_PUBLIC_KEY")`. While the root conftest exported a key,
those clients were real, and any turn-driving test that forgot to patch one waited
out a live HTTP timeout inside `flush()`. The root conftest now pops the Langfuse
variables before the first app import, which leaves all three clients None.

These tests fail if someone restores the setdefault lines, and they fail if a
developer shell exports a real key, because the conftest pops rather than defaults.
"""

import os

import pytest

from app.services import actor_seam, validation_service
from app.worker.tasks.runtime import agent

LANGFUSE_ENV_VARS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")


@pytest.mark.parametrize("var", LANGFUSE_ENV_VARS)
def test_no_langfuse_variable_survives_into_a_unit_test(var):
    """No Langfuse credential is visible to a unit test, inherited or seeded."""
    assert os.environ.get(var) is None, (
        f"{var} is set during a unit test. The root conftest must pop it, "
        "or the module-level Langfuse clients become real and dial out."
    )


@pytest.mark.parametrize(
    "module",
    [agent, actor_seam, validation_service],
    ids=["worker.tasks.runtime.agent", "services.actor_seam", "services.validation_service"],
)
def test_the_module_level_langfuse_client_is_disabled(module):
    """Every module-level client resolved to None, so its call sites no-op."""
    assert module._langfuse is None, (
        f"{module.__name__}._langfuse is a live client. A unit test that does not "
        "patch it will make a real Langfuse HTTP call."
    )
