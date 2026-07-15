"""Unit tests for the failure-triage bench (OPS-09/10).

Tests:
    Service layer (app/services/bench_service.py) — select with `-k service`:
        - list_failing_traces sources conversation_id from the agent.response
          event payload (control-DB job_events), NEVER from a `jobs` table
          (Pitfall 5 / must_haves prohibition).
        - grade_trace refuses to write a second grade once a trace is 'filed'
          (TERRARIUM law — irrevocable).
        - grade_trace validates the grade enum and the trace's agent ownership.
        - bench_tally treats 'filed' as terminal (never overwritten by a later row).

    Route layer tests (app/api/v1/traces.py) are added in Task 2 of this plan.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services import bench_service


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _Row:
    """Namespace object mimicking a SQLAlchemy Row's attribute access (row.col_name)."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _mock_result(rows: list | None = None, one=None) -> MagicMock:
    """Build a fake `Result` object returned by `await AsyncSession.execute(...)`."""
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows or [])
    result.fetchone = MagicMock(return_value=one)
    return result


# ---------------------------------------------------------------------------
# Service tests (Task 1) — select via `-k service`
# ---------------------------------------------------------------------------


class TestListFailingTracesService:
    """Service-level tests for bench_service.list_failing_traces (select with -k service)."""

    async def test_list_failing_traces_service_sources_conversation_id_from_agent_response(self):
        """conversation_id + agent_turn text come from the agent.response event
        payload, NEVER from a `jobs` table query (Pitfall 5 / must_haves)."""
        job_id = str(uuid4())
        agent_id = str(uuid4())

        flagged_row = _Row(job_id=job_id, verdict="ungrounded", reason="missing citation", created_at=None)

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(rows=[flagged_row]),  # _FLAGGED_EVENTS_SQL
                _mock_result(rows=[]),  # bench_tally's _ALL_GRADED_EVENTS_FOR_AGENT_SQL
                _mock_result(
                    one=_Row(payload={"conversation_id": "conv-123", "text": "agent answer"})
                ),  # _AGENT_RESPONSE_SQL
            ]
        )

        with patch(
            "app.services.bench_service.asyncio.to_thread",
            new=AsyncMock(return_value="customer question"),
        ) as mock_to_thread:
            result = await bench_service.list_failing_traces(
                control_db, "postgresql://fake/tenantdb", agent_id
            )

        assert len(result["traces"]) == 1
        trace = result["traces"][0]
        assert trace["conversation_id"] == "conv-123"
        assert trace["agent_turn"] == "agent answer"
        assert trace["customer_turn"] == "customer question"
        assert trace["verdict"] == "ungrounded"
        assert trace["judge_rationale"] == "missing citation"
        mock_to_thread.assert_called_once()

        # Source assertion (mirrors acceptance_criteria grep check): no query in
        # this call sequence ever references a `jobs` table.
        for call in control_db.execute.call_args_list:
            sql_text = str(call.args[0])
            assert "FROM jobs" not in sql_text

    async def test_list_failing_traces_service_skips_trace_without_agent_response_event(self):
        """A flagged job_id with no matching agent.response row is skipped, not
        surfaced with a hollow/empty turn."""
        job_id = str(uuid4())
        agent_id = str(uuid4())
        flagged_row = _Row(job_id=job_id, verdict="fail", reason="off-topic", created_at=None)

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(rows=[flagged_row]),
                _mock_result(rows=[]),
                _mock_result(one=None),  # no agent.response row for this job_id
            ]
        )

        result = await bench_service.list_failing_traces(
            control_db, "postgresql://fake/tenantdb", agent_id
        )

        assert result["traces"] == []

    async def test_list_failing_traces_service_dedupes_by_job_id(self):
        """Gatekeeper + Auditor can both flag the same job_id — only one trace
        row is returned per job_id (most recent verdict wins)."""
        job_id = str(uuid4())
        agent_id = str(uuid4())
        rows = [
            _Row(job_id=job_id, verdict="fail", reason="gatekeeper reason", created_at=None),
            _Row(job_id=job_id, verdict="ungrounded", reason="auditor reason", created_at=None),
        ]

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(rows=rows),
                _mock_result(rows=[]),
                _mock_result(one=_Row(payload={"conversation_id": "conv-1", "text": "answer"})),
            ]
        )

        with patch(
            "app.services.bench_service.asyncio.to_thread",
            new=AsyncMock(return_value="question"),
        ):
            result = await bench_service.list_failing_traces(
                control_db, "postgresql://fake/tenantdb", agent_id
            )

        assert len(result["traces"]) == 1
        # The first (most recent, since query orders DESC) verdict wins.
        assert result["traces"][0]["verdict"] == "fail"

    async def test_list_failing_traces_service_includes_tally(self):
        """Response includes a tally dict alongside the traces list."""
        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(rows=[]),  # no flagged events
                _mock_result(rows=[]),  # tally
            ]
        )

        result = await bench_service.list_failing_traces(
            control_db, "postgresql://fake/tenantdb", str(uuid4())
        )

        assert result["traces"] == []
        assert result["tally"] == {"filed": 0, "held": 0, "dismissed": 0}


class TestBenchTallyService:
    """Service-level tests for bench_service.bench_tally (select with -k service)."""

    async def test_bench_tally_service_treats_filed_as_terminal(self):
        """A later row for the same job_id must never overwrite a 'filed' grade
        (belt-and-suspenders — grade_trace() already refuses to write this)."""
        job_id = str(uuid4())
        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            return_value=_mock_result(
                rows=[
                    _Row(job_id=job_id, grade="filed"),
                    _Row(job_id=job_id, grade="held"),
                ]
            )
        )

        tally = await bench_service.bench_tally(control_db, str(uuid4()))

        assert tally["counts"]["filed"] == 1
        assert tally["counts"]["held"] == 0
        assert tally["graded_by_job"][job_id] == "filed"

    async def test_bench_tally_service_counts_distinct_job_ids(self):
        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            return_value=_mock_result(
                rows=[
                    _Row(job_id=str(uuid4()), grade="filed"),
                    _Row(job_id=str(uuid4()), grade="held"),
                    _Row(job_id=str(uuid4()), grade="dismissed"),
                ]
            )
        )

        tally = await bench_service.bench_tally(control_db, str(uuid4()))

        assert tally["counts"] == {"filed": 1, "held": 1, "dismissed": 1}


class TestGradeTraceService:
    """Service-level tests for bench_service.grade_trace (select with -k service)."""

    async def test_grade_trace_service_raises_on_second_filed_grade(self):
        """TERRARIUM law: a trace already graded 'filed' cannot be re-graded."""
        agent_id = str(uuid4())
        trace_id = str(uuid4())

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(one=_Row()),  # owner check passes
                _mock_result(rows=[_Row(grade="filed")]),  # already filed
            ]
        )
        control_db.add = MagicMock()
        control_db.commit = AsyncMock()

        with pytest.raises(bench_service.TraceAlreadyFiledError):
            await bench_service.grade_trace(control_db, agent_id, trace_id, "held")

        control_db.add.assert_not_called()
        control_db.commit.assert_not_called()

    async def test_grade_trace_service_validates_grade_enum(self):
        """An invalid grade value raises InvalidGradeError before any DB call."""
        control_db = AsyncMock()

        with pytest.raises(bench_service.InvalidGradeError):
            await bench_service.grade_trace(control_db, str(uuid4()), str(uuid4()), "banana")

        control_db.execute.assert_not_called()

    async def test_grade_trace_service_raises_not_found_for_foreign_agent_trace(self):
        """T-21-05-01: a trace_id with no flagged event owned by agent_id is refused."""
        control_db = AsyncMock()
        control_db.execute = AsyncMock(side_effect=[_mock_result(one=None)])  # owner check fails

        with pytest.raises(bench_service.TraceNotFoundError):
            await bench_service.grade_trace(control_db, str(uuid4()), str(uuid4()), "held")

    async def test_grade_trace_service_writes_job_event_and_returns_tally(self):
        """Happy path: inserts a 'trace.graded' job_events row and returns the tally."""
        agent_id = str(uuid4())
        trace_id = str(uuid4())

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(one=_Row()),  # owner check passes
                _mock_result(rows=[]),  # no existing grades
                _mock_result(rows=[_Row(job_id=trace_id, grade="held")]),  # post-commit tally read
            ]
        )
        control_db.add = MagicMock()
        control_db.commit = AsyncMock()

        result = await bench_service.grade_trace(control_db, agent_id, trace_id, "held")

        assert result["trace_id"] == trace_id
        assert result["grade"] == "held"
        assert result["tally"]["held"] == 1
        control_db.add.assert_called_once()
        control_db.commit.assert_awaited_once()

        added_event = control_db.add.call_args.args[0]
        assert added_event.event_type == "trace.graded"
        assert added_event.payload["grade"] == "held"
        assert added_event.payload["agent_id"] == agent_id
