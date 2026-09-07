"""draft_golden_scenarios hands drafts back as job events and writes no row (#203)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.worker.tasks.runtime import golden_draft as task_mod


def _draft(document_id: str) -> dict:
    return {
        "question": "q", "reference_answer": "a", "citation": "a",
        "source_document_id": document_id, "source_document": f"doc {document_id}",
        "source_chunk_id": f"{document_id}-0",
    }


def _chunks(*document_ids: str) -> list[dict]:
    return [
        {"chunk_id": f"{d}-0", "document_id": d, "ordinal": 0, "content": "a", "document": f"doc {d}"}
        for d in document_ids
    ]


def _db_with(job, agent, already_complete=False):
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (1,) if already_complete else None
    db.get.side_effect = lambda model, pk: job if model is task_mod.Job else agent

    @contextmanager
    def ctx():
        yield db

    return db, ctx


def _run(db_ctx, kept, dropped, chunks):
    events: list[tuple[str, dict]] = []
    with patch.object(task_mod, "get_sync_db", db_ctx), patch.object(
        task_mod, "fernet_decrypt", return_value="postgresql://tenant"
    ), patch.object(task_mod, "fetch_chunks_by_document", return_value=chunks), patch.object(
        task_mod, "draft_golden_pairs", return_value=(kept, dropped)
    ) as drafter, patch.object(
        task_mod, "emit", side_effect=lambda job_id, et, payload, db, r: events.append((et, payload))
    ):
        result = task_mod.draft_golden_scenarios.run(
            job_id=str(uuid.uuid4()), agent_id=str(uuid.uuid4()), n=10
        )
    return result, events, drafter


class TestTheDraftsTravelAsEvents:
    def test_one_pair_event_per_kept_draft_and_a_complete_event_with_the_counts(self):
        job = MagicMock()
        agent = MagicMock(neon_connection_string=b"x", tenant_id=uuid.uuid4())
        db, ctx = _db_with(job, agent)

        result, events, _ = _run(ctx, [_draft("a"), _draft("b")], 1, _chunks("a", "b", "c"))

        assert [e for e, _ in events] == [
            task_mod.EVENT_STARTED, task_mod.EVENT_PAIR, task_mod.EVENT_PAIR, task_mod.EVENT_COMPLETE,
        ]
        assert events[1][1] == _draft("a")
        assert events[-1][1] == {"kept": 2, "dropped": 1, "documents": 3}
        assert result == {"kept": 2, "dropped": 1, "documents": 3}
        assert job.status == "complete"
        assert job.finished_at is not None

    def test_the_drafter_is_billed_to_the_job_and_writes_through_the_tenant_dsn(self):
        job = MagicMock()
        agent = MagicMock(neon_connection_string=b"x", tenant_id=uuid.uuid4())
        _db, ctx = _db_with(job, agent)

        _, _, drafter = _run(ctx, [], 0, _chunks("a"))

        ledger = drafter.call_args.args[1]
        assert ledger.tenant_id == str(agent.tenant_id)
        assert ledger.job_id is not None

    def test_nothing_is_written_to_eval_scenarios(self):
        job = MagicMock()
        agent = MagicMock(neon_connection_string=b"x", tenant_id=uuid.uuid4())
        db, ctx = _db_with(job, agent)

        _run(ctx, [_draft("a")], 0, _chunks("a"))

        statements = [str(c.args[0]) for c in db.execute.call_args_list]
        assert all("eval_scenarios" not in s for s in statements)


class TestRedeliveryAndFailure:
    def test_a_job_that_already_completed_is_skipped(self):
        job = MagicMock()
        agent = MagicMock(neon_connection_string=b"x", tenant_id=uuid.uuid4())
        _db, ctx = _db_with(job, agent, already_complete=True)

        result, events, drafter = _run(ctx, [_draft("a")], 0, _chunks("a"))

        assert result == {}
        assert events == []
        assert drafter.call_count == 0

    def test_a_corpus_read_that_raises_fails_the_job_on_the_last_attempt(self):
        job = MagicMock()
        agent = MagicMock(neon_connection_string=b"x", tenant_id=uuid.uuid4())
        _db, ctx = _db_with(job, agent)
        events: list[tuple[str, dict]] = []
        with patch.object(task_mod, "get_sync_db", ctx), patch.object(
            task_mod, "fernet_decrypt", return_value="postgresql://tenant"
        ), patch.object(
            task_mod, "fetch_chunks_by_document", side_effect=RuntimeError("tenant db down")
        ), patch.object(
            task_mod, "emit", side_effect=lambda job_id, et, payload, db, r: events.append((et, payload))
        ), patch.object(task_mod.draft_golden_scenarios, "max_retries", 0):
            result = task_mod.draft_golden_scenarios.run(
                job_id=str(uuid.uuid4()), agent_id=str(uuid.uuid4()), n=10
            )

        assert result == {}
        assert events[-1] == (task_mod.EVENT_FAILED, {"error_type": "RuntimeError"})
        assert job.status == "failed"
