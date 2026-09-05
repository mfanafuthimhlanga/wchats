"""The claim that stops one turn being run twice at once (#85).

`run_agent_turn` is `acks_late=True`, so a worker that dies mid-turn hands its
message back and a second worker runs the same turn again. The READ guard on
`job_events` cannot see that: the first attempt has not written its
`agent.response` row yet, so the guard is sequential-only and the second attempt
runs a full turn. Duplicate model spend billed to the tenant, a duplicate pair of
`messages` rows the next turn then replays, a duplicate escalation mail, and
duplicate dispatches through the six mutating skills.

WHAT THE CLAIM IS
    A Postgres advisory lock on the CONTROL database, keyed on the turn's job id
    and taken inside a transaction the attempt holds open for its whole life. A
    second attempt asks for the same key, is refused, and returns without calling
    a model. A worker that DIES drops its connection, Postgres rolls the
    transaction back, and the redelivery claims the turn cleanly, which is the
    property a status column cannot give without a heartbeat.

WHY THESE TESTS NEED A REAL DATABASE
    `pg_try_advisory_xact_lock` is the whole mechanism. A mock answers it truthy,
    so a test built on one could never observe the refusal and would be a
    tautology. The connections below are real connections to the local control
    cluster and the refusal is Postgres', not a double's.

Local cluster (CLAUDE.md, "Environment constraints"): PostgreSQL 17.6 on
localhost:5432, database `wchats_control`. The module skips when it is not
reachable rather than failing, and `-rs` names the skip.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text

from tests.agent_loop_doubles import canned_turn_result

LOCAL_CONTROL_DSN = os.environ.get(
    "LOCAL_CONTROL_DSN", "postgresql://wchats:wchats@localhost:5432/wchats_control"
)


def _local_engine():
    """An engine on the local control cluster, or the reason there is none."""
    engine = create_engine(LOCAL_CONTROL_DSN, pool_pre_ping=True)
    with engine.connect() as probe:
        probe.execute(text("SELECT 1"))
    return engine


try:
    _ENGINE = _local_engine()
    _WHY_NOT = ""
except Exception as exc:  # pragma: no cover - environment, not behaviour
    _ENGINE = None
    _WHY_NOT = f"local control cluster unreachable at {LOCAL_CONTROL_DSN}: {exc}"

pytestmark = pytest.mark.skipif(_ENGINE is None, reason=_WHY_NOT)


def _db_on(engine, answered=None):
    """A session stand-in bound to a real engine.

    `get_bind` is what the claim takes its connection from, so it is real. The
    `job_events` READ is scripted, because these tests are about the lock and a
    row in that table is a different guard's subject.
    """
    db = MagicMock()
    db.get_bind.return_value = engine
    db.execute.return_value.fetchone.return_value = answered
    return db


# ---------------------------------------------------------------------------
# The lock itself
# ---------------------------------------------------------------------------


def test_a_second_attempt_at_one_turn_is_refused_while_the_first_holds_it():
    """Two connections, one job id, and only one of them gets to run."""
    from app.worker.tasks.runtime.agent import _claimed_turn

    job_id = str(uuid.uuid4())
    first, first_answer = _claimed_turn(_db_on(_ENGINE), job_id)
    try:
        assert first is not None and first_answer is None, (
            f"the first attempt at a fresh turn must claim it: {first_answer!r}"
        )
        second, second_answer = _claimed_turn(_db_on(_ENGINE), job_id)
        assert second is None, (
            "a second worker claimed a turn the first is still running, which is "
            "a duplicate model call billed to the tenant and a duplicate pair of "
            "messages rows"
        )
        assert second_answer == {"status": "already_running", "job_id": job_id}, (
            f"the refused attempt must say why it stopped: {second_answer!r}"
        )
    finally:
        first.close()


def test_the_claim_is_free_again_once_the_holder_lets_go():
    """A held claim that never released would block the turn's own retry."""
    from app.worker.tasks.runtime.agent import _claimed_turn

    job_id = str(uuid.uuid4())
    first, _ = _claimed_turn(_db_on(_ENGINE), job_id)
    first.close()

    second, second_answer = _claimed_turn(_db_on(_ENGINE), job_id)
    try:
        assert second is not None and second_answer is None, (
            "the holder let go, so the next attempt has to be able to claim: "
            f"{second_answer!r}"
        )
    finally:
        second.close()


def test_two_different_turns_never_contend_for_one_key():
    """A key collision would refuse a turn nothing is running."""
    from app.worker.tasks.runtime.agent import _claimed_turn

    one, _ = _claimed_turn(_db_on(_ENGINE), str(uuid.uuid4()))
    two, two_answer = _claimed_turn(_db_on(_ENGINE), str(uuid.uuid4()))
    try:
        assert two is not None and two_answer is None, (
            f"two unrelated turns collided on one lock key: {two_answer!r}"
        )
    finally:
        one.close()
        two.close()


def test_a_turn_already_answered_takes_the_cheap_path_without_the_lock():
    """The READ guard stays as the answer for a redelivery that arrives late."""
    from app.worker.tasks.runtime.agent import _claimed_turn

    job_id = str(uuid.uuid4())
    claim, answer = _claimed_turn(_db_on(_ENGINE, answered=(1,)), job_id)

    assert claim is None
    assert answer == {"status": "already_complete", "job_id": job_id}, (
        f"a turn with an agent.response row is finished, not running: {answer!r}"
    )


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------


def _agent():
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = "Test Agent"
    agent.soul_role = "customer service representative"
    agent.soul_voice = "helpful"
    agent.soul_do_list = []
    agent.soul_donot_list = []
    agent.retrieval_strategy = {}
    agent.neon_connection_string = b"encrypted-bytes"
    return agent


def _seam(**kwargs):
    """Stand-in for `build_agent_turn`, in the shape `_release_turn` reads.

    `calls` is the loop's ledger rows and `bound` the tool ContextVars
    `close_turn` hands back; a double without either fails in the release for a
    reason about the double.
    """
    return SimpleNamespace(
        calls=[], ledger=kwargs.get("ledger", lambda call: None), bound=()
    )


def _db_ctx(db):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_a_redelivered_turn_whose_claim_is_held_calls_no_model_at_all():
    """The whole point of the claim, driven through the task itself.

    The lock is held by a claim this test takes first, exactly as a first worker
    still mid-turn would hold it. The task then runs the same job id and has to
    stop before `build_agent_turn` and before the `asyncio.run` bridge, because
    everything past those two costs the tenant money and writes rows the next
    turn replays.
    """
    from app.worker.tasks.runtime.agent import _claimed_turn, run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _agent()
    held, _ = _claimed_turn(_db_on(_ENGINE), job_id)

    db = _db_on(_ENGINE)
    db.get.side_effect = [agent, SimpleNamespace(id=job_id, status="running")]

    try:
        with (
            patch(
                "app.worker.tasks.runtime.agent.get_sync_db",
                return_value=_db_ctx(db),
            ),
            patch(
                "app.worker.tasks.runtime.agent.fernet_decrypt",
                return_value="postgresql://tenant",
            ),
            patch("app.worker.tasks.runtime.agent.psycopg2.connect") as tenant_connect,
            patch("app.worker.tasks.runtime.agent.build_agent_turn") as seam,
            patch("app.worker.tasks.runtime.agent.asyncio.run") as bridge,
            patch("app.worker.tasks.runtime.agent.emit") as emitted,
        ):
            result = run_agent_turn.run(
                job_id=job_id,
                agent_id=str(agent.id),
                message="hello",
                conversation_id=None,
            )
    finally:
        held.close()

    assert bridge.call_count == 0, (
        "a redelivery ran the model while the first attempt still held the turn"
    )
    assert seam.call_count == 0, "the redelivery built a second turn"
    assert emitted.call_count == 0, "the redelivery emitted a second turn's events"
    assert tenant_connect.call_count == 0, (
        "the redelivery opened a tenant connection for a turn it may not run"
    )
    assert result == {"status": "already_running", "job_id": job_id}, (
        f"the refused delivery must say what it did: {result!r}"
    )


# ---------------------------------------------------------------------------
# The release
# ---------------------------------------------------------------------------


def _terminate(connection) -> None:
    """Drop this connection's server backend from a second session.

    What Neon's `idle_in_transaction_session_timeout` does to the claim while a
    turn is running, and what a `pg_terminate_backend` from an operator does,
    reproduced with the second of those because the first takes five minutes.
    """
    pid = connection.execute(text("SELECT pg_backend_pid()")).scalar()
    with _ENGINE.connect() as killer:
        killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        killer.commit()


def test_a_release_over_a_dead_claim_lets_the_retry_it_was_given_escape():
    """The turn's own `Retry` has to survive the release that runs beneath it.

    `_release_turn` runs from the task's `finally`, so an exception raised THERE
    replaces whatever the task was raising. The claim is a connection held idle
    in transaction for the whole turn, which is exactly what a server kills:
    Neon's `idle_in_transaction_session_timeout` defaults to five minutes and a
    slow turn outlives it. `close()` on a connection whose backend is gone raises
    `OperationalError` out of the `finally`, the `Retry` never reaches Celery, no
    attempt is ever scheduled, and the customer's widget waits on a job that will
    never complete.
    """
    from celery.exceptions import Retry

    from app.worker.tasks.runtime.agent import _claimed_turn, _release_turn

    job_id = str(uuid.uuid4())
    claim, _ = _claimed_turn(_db_on(_ENGINE), job_id)
    _terminate(claim)

    with pytest.raises(Retry):
        try:
            raise Retry("the turn asked for another attempt")
        finally:
            _release_turn(None, None, claim, job_id)


def test_one_resource_that_will_not_close_never_strands_the_other_two():
    """The claim is released even when the turn ahead of it fails to close.

    The three closes are a ledger of debts, not a sequence: the ContextVars go
    back, PROD-05's tenant connection goes back to the pool, and the claim frees
    the turn for its own retry. One raising used to skip the two behind it, and
    the claim is last, so the resource with the longest reach was the one most
    easily stranded.
    """
    from app.worker.tasks.runtime.agent import _release_turn

    tenant_conn, claim = MagicMock(), MagicMock()
    turn = MagicMock()

    with patch(
        "app.worker.tasks.runtime.agent.close_turn",
        side_effect=RuntimeError("the tool ContextVars would not reset"),
    ):
        _release_turn(turn, tenant_conn, claim, str(uuid.uuid4()))

    assert tenant_conn.close.call_count == 1, (
        "a turn that would not close leaked PROD-05's tenant connection"
    )
    assert claim.close.call_count == 1, (
        "a turn that would not close stranded the claim, so every redelivery of "
        "this turn is refused until the pool drops the connection"
    )


# ---------------------------------------------------------------------------
# The server-side clock the claim has to outlive
# ---------------------------------------------------------------------------


def test_the_claim_overrides_a_server_side_idle_in_transaction_timeout():
    """A turn longer than the server's idle limit still holds its lock.

    The claim opens a transaction, takes the lock and then does NOTHING on that
    connection until the turn ends, so for the turn's whole life it is idle in
    transaction. Neon ships `idle_in_transaction_session_timeout` at five
    minutes, and a turn that runs longer has its claim connection killed by the
    server: the transaction rolls back, the advisory lock goes with it, and a
    redelivery claims a turn that is still running. Nothing raises where anyone
    would see it, because the connection is not touched again until release.

    The local cluster runs the parameter at 0, which cannot show the override, so
    the session default is set to a minute FIRST and on the very connection the
    claim will get: one pooled slot, no overflow, so `connect()` hands back the
    same backend. What the claim reads inside its own transaction is the
    `SET LOCAL`, not the session default it was handed.
    """
    from sqlalchemy.pool import QueuePool

    from app.worker.tasks.runtime.agent import _claimed_turn

    engine = create_engine(
        LOCAL_CONTROL_DSN, poolclass=QueuePool, pool_size=1, max_overflow=0
    )
    claim = None
    try:
        with engine.connect() as seeded:
            seeded.execute(text("SET idle_in_transaction_session_timeout = '1min'"))
            seeded.commit()

        claim, _ = _claimed_turn(_db_on(engine), str(uuid.uuid4()))
        in_force = claim.execute(
            text("SHOW idle_in_transaction_session_timeout")
        ).scalar()
    finally:
        if claim is not None:
            claim.close()
        engine.dispose()

    assert in_force == "0", (
        "the claim runs under a server-side idle limit of "
        f"{in_force!r}; a turn slower than that loses its advisory lock mid-turn "
        "and a redelivery runs the same turn again"
    )


# ---------------------------------------------------------------------------
# Every exit from the claimed stretch
# ---------------------------------------------------------------------------


def _claim_is_free(job_id: str) -> bool:
    """Whether a fresh attempt at this turn would be granted the lock right now."""
    from app.worker.tasks.runtime.agent import _claimed_turn

    claim, _ = _claimed_turn(_db_on(_ENGINE), job_id)
    if claim is None:
        return False
    claim.close()
    return True


def test_a_decrypt_that_raises_hands_the_claim_back_before_it_leaves():
    """The stretch between the claim and the `try` had two ways out and no release.

    `db.get(Job, ...)` and the Fernet decrypt both sit after the turn is claimed,
    and both can raise. The claim was a live connection holding an advisory lock
    in an open transaction, left for the garbage collector: until the pool
    noticed, every redelivery of this turn was refused with `already_running` for
    a turn no worker was running. The traceback holds the frame, so on the path
    that matters the collector does not come.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _agent()
    db = _db_on(_ENGINE)
    db.get.side_effect = [agent, SimpleNamespace(id=job_id, status="running")]

    with pytest.raises(Exception):
        with (
            patch(
                "app.worker.tasks.runtime.agent.get_sync_db",
                return_value=_db_ctx(db),
            ),
            patch(
                "app.worker.tasks.runtime.agent.fernet_decrypt",
                side_effect=RuntimeError("the ciphertext will not decrypt"),
            ),
            patch("app.worker.tasks.runtime.agent.emit"),
        ):
            run_agent_turn.run(
                job_id=job_id,
                agent_id=str(agent.id),
                message="hello",
                conversation_id=None,
            )

    assert _claim_is_free(job_id), (
        "the turn failed before its `try` and kept the claim, so every "
        "redelivery of it is refused for a turn nothing is running"
    )


def test_a_turn_answered_between_the_read_and_the_lock_runs_no_model():
    """The second READ guard is what closes the finish-and-release race.

    The first attempt can write its `agent.response` row and release the claim in
    the gap between this attempt's first read and its lock. The lock is then
    granted, correctly, because nothing holds it, and without the read AFTER it
    this attempt runs a full second turn on a question already answered: a
    duplicate model call billed to the tenant, a duplicate pair of `messages`
    rows the next turn replays, and a second answer on a widget that has one.

    The window is inside one function call, so the two reads are driven directly:
    not answered when the guard looks, answered by the time the lock is held.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _agent()
    db = _db_on(_ENGINE)
    db.get.side_effect = [agent, SimpleNamespace(id=job_id, status="running")]
    # The claim needs a REAL connection out of the pool, and the patch below
    # replaces the driver's `connect` that the pool itself would call to make a
    # new one. One checkout first leaves a live connection in the pool to hand
    # back.
    with _ENGINE.connect() as warmed:
        warmed.execute(text("SELECT 1"))

    with (
        patch(
            "app.worker.tasks.runtime.agent.get_sync_db", return_value=_db_ctx(db)
        ),
        patch(
            "app.worker.tasks.runtime.agent._answered_already",
            side_effect=[False, True],
        ),
        patch(
            "app.worker.tasks.runtime.agent.fernet_decrypt",
            return_value="postgresql://tenant",
        ),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as tenant_connect,
        patch(
            "app.worker.tasks.runtime.agent._create_conversation_row",
            return_value="00000000-0000-0000-0000-0000000000ab",
        ),
        patch("app.worker.tasks.runtime.agent._persist_messages", return_value="m1"),
        patch(
            "app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam
        ) as seam,
        patch(
            "app.worker.tasks.runtime.agent.asyncio.run",
            return_value=canned_turn_result("Answered twice, which is the defect."),
        ) as bridge,
        patch("app.worker.tasks.runtime.agent.emit") as emitted,
    ):
        result = run_agent_turn.run(
            job_id=job_id,
            agent_id=str(agent.id),
            message="hello",
            conversation_id=None,
        )

    assert tenant_connect.call_count == 0, (
        "the turn was answered while this attempt waited for the lock and it "
        "opened a tenant connection anyway"
    )
    assert bridge.call_count == 0, "a second model call was made on an answered turn"
    assert seam.call_count == 0, "a second turn was built on an answered turn"
    assert emitted.call_count == 0, "a second turn's events reached the widget"
    assert result == {"status": "already_complete", "job_id": job_id}, (
        f"the attempt must say the turn was already answered: {result!r}"
    )
    assert _claim_is_free(job_id), (
        "the attempt stopped on the second read guard and kept the claim"
    )
