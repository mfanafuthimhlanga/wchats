"""draft_golden_scenarios hands drafts back as job events and writes no scenario row (#203)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.services import golden_draft_service
from app.worker.tasks.runtime import golden_draft as task_mod
from tests.model_doubles import completion, tool_call


def _draft(document_id: str) -> dict:
    return {
        "question": "q", "reference_answer": "a", "citation": "a",
        "source_document_id": document_id, "source_document": f"doc {document_id}",
        "source_chunk_id": f"{document_id}-0",
    }


def _index(*document_ids: str) -> list[dict]:
    return [
        {"chunk_id": f"{d}-0", "document_id": d, "ordinal": 0, "document": f"doc {d}"}
        for d in document_ids
    ]


def _db_with(job, agent, already_complete=False, drafted=()):
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (1,) if already_complete else None
    db.execute.return_value.fetchall.return_value = [(chunk_id,) for chunk_id in drafted]
    db.get.side_effect = lambda model, pk: job if model is task_mod.Job else agent

    @contextmanager
    def ctx():
        yield db

    return db, ctx


def _job_and_agent():
    return MagicMock(), MagicMock(neon_connection_string=b"x", tenant_id=uuid.uuid4())


def _run(db_ctx, kept, dropped, index, **extra):
    events: list[tuple[str, dict]] = []
    patches = dict(
        fetch_chunk_index=patch.object(task_mod, "fetch_chunk_index", return_value=index),
        fetch_chunk_content=patch.object(
            task_mod, "fetch_chunk_content", side_effect=lambda dsn, chunks: [{**c, "content": "a"} for c in chunks]
        ),
    )
    with patch.object(task_mod, "get_sync_db", db_ctx), patch.object(
        task_mod, "fernet_decrypt", return_value="postgresql://tenant"
    ), extra.get("index_patch", patches["fetch_chunk_index"]), patches["fetch_chunk_content"] as content, patch.object(
        task_mod, "draft_golden_pairs", return_value=(kept, dropped)
    ) as drafter, patch.object(
        task_mod, "emit", side_effect=lambda job_id, et, payload, db, r: events.append((et, payload))
    ):
        result = task_mod.draft_golden_scenarios.run(
            job_id=str(uuid.uuid4()), agent_id=str(uuid.uuid4()), n=10
        )
    return result, events, drafter, content


class TestTheDraftsTravelAsEvents:
    def test_one_pair_event_per_kept_draft_and_a_complete_event_with_the_counts(self):
        job, agent = _job_and_agent()
        _db, ctx = _db_with(job, agent)

        result, events, _, _ = _run(ctx, [_draft("a"), _draft("b")], 1, _index("a", "b", "c"))

        assert [e for e, _ in events] == [
            task_mod.EVENT_STARTED, task_mod.EVENT_PAIR, task_mod.EVENT_PAIR, task_mod.EVENT_COMPLETE,
        ]
        assert events[1][1] == _draft("a")
        assert events[-1][1] == {"kept": 2, "dropped": 1, "documents": 3}
        assert result == {"kept": 2, "dropped": 1, "documents": 3}
        assert job.status == "complete"
        assert job.finished_at is not None

    def test_the_drafter_is_billed_to_the_job_and_writes_through_the_tenant_dsn(self):
        job, agent = _job_and_agent()
        _db, ctx = _db_with(job, agent)

        _, _, drafter, _ = _run(ctx, [], 0, _index("a"))

        ledger = drafter.call_args.args[1]
        assert ledger.tenant_id == str(agent.tenant_id)
        assert ledger.job_id is not None

    def test_content_is_fetched_for_the_picked_chunks_only(self):
        job, agent = _job_and_agent()
        _db, ctx = _db_with(job, agent)

        _, _, drafter, content = _run(ctx, [], 0, _index("a", "b"))

        assert [c["chunk_id"] for c in content.call_args.args[1]] == ["a-0", "b-0"]
        assert [c["content"] for c in drafter.call_args.args[0]] == ["a", "a"]


class TestNoScenarioRowIsWritten:
    @patch("app.core.model_client.make_client")
    def test_every_statement_the_draft_path_sends_the_tenant_db_is_a_select(self, mock_factory):
        """The whole path runs, with the model and the tenant connection as doubles,
        and the tenant database sees reads only. Only the golden registration
        route writes a scenario row."""
        chunk_id, document_id = str(uuid.uuid4()), str(uuid.uuid4())
        passage = (
            "Delivery costs R35 for orders under R300 and is free for orders of R300 or more. "
            "We do not deliver on Sundays or public holidays."
        )
        mock_factory.return_value.chat.completions.create.return_value = completion(
            tool_calls=[tool_call("submit_golden_draft", {
                "question": "How much is delivery?",
                "reference_answer": "Delivery costs R35 for orders under R300 and is free for orders of R300 or more.",
                "citation": "Delivery costs R35 for orders under R300 and is free for orders of R300 or more.",
            })],
            finish_reason="tool_calls",
        )
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchall.side_effect = [
            [(chunk_id, document_id, 0, "Delivery", None)],
            [(chunk_id, passage)],
        ]
        tenant_conn = MagicMock()
        tenant_conn.cursor.return_value = cursor
        job, agent = _job_and_agent()
        _db, ctx = _db_with(job, agent)
        events: list[tuple[str, dict]] = []

        with patch.object(task_mod, "get_sync_db", ctx), patch.object(
            task_mod, "fernet_decrypt", return_value="postgresql://tenant"
        ), patch.object(golden_draft_service.psycopg2, "connect", return_value=tenant_conn), patch.object(
            task_mod, "emit", side_effect=lambda job_id, et, payload, db, r: events.append((et, payload))
        ):
            result = task_mod.draft_golden_scenarios.run(
                job_id=str(uuid.uuid4()), agent_id=str(uuid.uuid4()), n=10
            )

        statements = [str(c.args[0]).strip().upper() for c in cursor.execute.call_args_list]
        assert statements, "the tenant database was never read, so this proves nothing"
        assert all(s.startswith("SELECT") for s in statements), statements
        assert result["kept"] == 1
        assert [e for e, _ in events].count(task_mod.EVENT_PAIR) == 1


class TestRedeliveryAndFailure:
    def test_a_job_that_already_completed_is_skipped(self):
        job, agent = _job_and_agent()
        _db, ctx = _db_with(job, agent, already_complete=True)

        result, events, drafter, _ = _run(ctx, [_draft("a")], 0, _index("a"))

        assert result == {}
        assert events == []
        assert drafter.call_count == 0

    def test_a_redelivery_drafts_only_the_chunks_without_a_pair_event(self):
        """The worker stopped after pair a was emitted. The redelivery bills b only,
        emits b only, and the complete count includes a."""
        job, agent = _job_and_agent()
        _db, ctx = _db_with(job, agent, drafted=("a-0",))

        result, events, drafter, _ = _run(ctx, [_draft("b")], 0, _index("a", "b"))

        assert [c["chunk_id"] for c in drafter.call_args.args[0]] == ["b-0"]
        assert [e for e, _ in events] == [task_mod.EVENT_PAIR, task_mod.EVENT_COMPLETE]
        assert events[0][1] == _draft("b")
        assert result == {"kept": 2, "dropped": 0, "documents": 2}

    def test_a_corpus_read_that_raises_fails_the_job_on_a_fresh_session(self):
        job, agent = _job_and_agent()
        db, ctx = _db_with(job, agent)
        events: list[tuple[str, dict]] = []
        with patch.object(task_mod, "get_sync_db", ctx), patch.object(
            task_mod, "fernet_decrypt", return_value="postgresql://tenant"
        ), patch.object(
            task_mod, "fetch_chunk_index", side_effect=RuntimeError("tenant db down")
        ), patch.object(
            task_mod, "emit", side_effect=lambda job_id, et, payload, db, r: events.append((et, payload))
        ):
            result = task_mod.draft_golden_scenarios.run(
                job_id=str(uuid.uuid4()), agent_id=str(uuid.uuid4()), n=10
            )

        assert result == {}
        assert events[-1] == (task_mod.EVENT_FAILED, {"error_type": "RuntimeError"})
        assert job.status == "failed"
        assert db.commit.call_count >= 1

    def test_a_failure_write_that_raises_is_logged_and_does_not_escape(self):
        job, agent = _job_and_agent()
        _db, ctx = _db_with(job, agent)
        with patch.object(task_mod, "get_sync_db", ctx), patch.object(
            task_mod, "fernet_decrypt", return_value="postgresql://tenant"
        ), patch.object(
            task_mod, "fetch_chunk_index", side_effect=RuntimeError("tenant db down")
        ), patch.object(task_mod, "emit", side_effect=RuntimeError("redis down")):
            result = task_mod.draft_golden_scenarios.run(
                job_id=str(uuid.uuid4()), agent_id=str(uuid.uuid4()), n=10
            )

        assert result == {}

    def test_the_task_has_no_celery_retry(self):
        assert task_mod.draft_golden_scenarios.max_retries == 0
        assert task_mod.draft_golden_scenarios.acks_late is True
