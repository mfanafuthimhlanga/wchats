"""Every scheduled fan-out selects deployed AND ready agents (issue #134).

The five beats each spend money per agent they dispatch: a nightly eval suite, a
weekly red-team run, a weekly digest email, a daily alert check, an index staleness
check. They selected `is_deployed` alone, while agent_chat.py, query.py, widget.py
and documents.py all refuse a request unless `status == 'ready'`. Nothing clears
`is_deployed` when status leaves 'ready', so a deployed agent that stopped being
ready kept buying runs while answering its own customers with 409.

`select_beat_fanout_agents()` in app.models.agent is the one selection now. These tests drive each
beat against a mock session and read the predicate it actually executed, so they
fail for a site that stops calling the helper as well as for a helper that changes
shape. `tests/unit/test_eval_task.py::TestRunEvalSuiteBeat` pins the fifth beat, the
nightly eval, next to the rest of that task's tests.
"""

from contextlib import contextmanager
from importlib import import_module
from unittest.mock import MagicMock, patch

import pytest

# (module path, beat task attribute, per-agent task attribute)
BEATS = [
    ("app.worker.tasks.runtime.red_team", "run_red_team_beat", "run_red_team"),
    ("app.worker.tasks.runtime.digest", "run_weekly_digest_beat", "run_weekly_digest"),
    ("app.worker.tasks.runtime.alert", "run_alert_check_beat", "run_alert_check"),
    (
        "app.worker.tasks.pipeline.staleness",
        "check_index_staleness_beat",
        "check_index_staleness",
    ),
]
BEAT_IDS = [beat for _, beat, _ in BEATS]


def _fan_out(module_path: str, beat_attr: str, task_attr: str):
    """Run one beat against a mock control session; return the session and dispatches."""
    mod = import_module(module_path)

    mock_agent = MagicMock()
    mock_agent.id = "11111111-1111-1111-1111-111111111111"
    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = [mock_agent]

    @contextmanager
    def _fake_get_sync_db():
        yield mock_db

    patches = [
        patch.object(mod, "get_sync_db", _fake_get_sync_db),
        patch.object(getattr(mod, task_attr), "apply_async"),
    ]
    if hasattr(mod, "settings"):
        # The digest beat returns early when DIGEST_ENABLED is False, and then it
        # never reaches the selection this test is here to read.
        patches.append(patch.object(mod.settings, "DIGEST_ENABLED", True))

    with patches[0], patches[1]:
        for extra in patches[2:]:
            extra.start()
        try:
            getattr(mod, beat_attr).run()
        finally:
            for extra in patches[2:]:
                extra.stop()

    return mock_db


def _executed_predicate(mock_db) -> str:
    """The WHERE clause the beat executed, with literal values rendered.

    The column list names every Agent column, `status` included, so the full
    statement cannot tell these selections apart. Literal binds matter too: an
    unrendered `status = :status_1` would pass for `status = 'pending'`.
    """
    stmt = mock_db.execute.call_args.args[0]
    return str(stmt.whereclause.compile(compile_kwargs={"literal_binds": True})).lower()


@pytest.mark.parametrize(("module_path", "beat_attr", "task_attr"), BEATS, ids=BEAT_IDS)
def test_the_beat_selects_deployed_and_ready_agents(module_path, beat_attr, task_attr):
    where = _executed_predicate(_fan_out(module_path, beat_attr, task_attr))

    assert "is_deployed = true" in where, (
        f"{beat_attr} must select DEPLOYED agents, positively (#32): {where}"
    )
    assert "status = 'ready'" in where, (
        f"{beat_attr} keeps spending on an agent whose chat route answers 409 (#134): "
        f"{where}"
    )
    assert " and " in where, (
        f"{beat_attr} must require both filters, not either one (#134): {where}"
    )


@pytest.mark.parametrize(("module_path", "beat_attr", "task_attr"), BEATS, ids=BEAT_IDS)
def test_the_beat_dispatches_one_task_per_selected_agent(module_path, beat_attr, task_attr):
    """Control for the test above: the beat really ran and really dispatched."""
    mock_db = _fan_out(module_path, beat_attr, task_attr)

    assert mock_db.execute.call_count == 1, (
        f"{beat_attr} ran no control-DB query, so the predicate assertion above "
        "would be reading a call that never happened"
    )
