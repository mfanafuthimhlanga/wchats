"""
Unit tests for app.services.scenario_service (M6 eval scenario generation).

Tests:
    test_generate_scenarios_from_chunks_calls_haiku
        — Claude Haiku API called with forced tool_choice (D-12 LOCKED)
        — returned scenarios tagged source='generated' (D-13 LOCKED)
    test_generate_scenarios_raises_on_no_tool_block
        — ValueError raised when response.content has no tool_use block
    test_store_scenarios_idempotent
        — INSERT uses ON CONFLICT DO NOTHING (idempotency rule)

Mock strategy:
    - the client factory (app.core.model_client.make_client) patched, since
      ticket #47 moved construction there and left no module-level client.
    - psycopg2.connect patched to avoid real DB connections.
    - conftest.py sets all required env vars before any app import.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from tests.model_doubles import ledger

# ---------------------------------------------------------------------------
# test_generate_scenarios_from_chunks_calls_haiku
# ---------------------------------------------------------------------------


class TestGenerateScenariosFromChunks:
    """Tests for the Claude Haiku scenario generator."""

    @patch("app.core.model_client.make_client")
    def test_generate_scenarios_from_chunks_calls_haiku(self, mock_factory):
        """Haiku is called with forced tool_choice and scenarios have source='generated' (D-12/D-13).
        """
        mock_client = mock_factory.return_value
        from app.services.scenario_service import generate_scenarios_from_chunks

        # Build a mock tool_use block
        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.name = "submit_scenarios"
        mock_tool_block.input = {
            "scenarios": [
                {
                    "question": "What is your return policy?",
                    "reference_answer": "Items can be returned within 30 days.",
                    "scenario_category": "factual",
                }
            ]
        }

        mock_response = MagicMock()
        mock_response.content = [mock_tool_block]
        mock_client.messages.create.return_value = mock_response

        scenarios = generate_scenarios_from_chunks(
            [{"content": "Our return policy allows 30-day returns."}], ledger(), n=1
        )

        # D-13 LOCKED: source must be 'generated'
        assert len(scenarios) == 1
        assert scenarios[0]["source"] == "generated", (
            "D-13 violation: generated scenarios must have source='generated'"
        )
        assert scenarios[0]["question"] == "What is your return policy?"
        assert scenarios[0]["reference_answer"] == "Items can be returned within 30 days."
        assert scenarios[0]["scenario_category"] == "factual"
        # #27: the generator names the dataset it produced into. Twenty rows of
        # eval run 29754ceb went in without one, both readers folded NULL to
        # exploratory, and `summarise_run_validity` reported golden 0/0/0 over a
        # set nobody had ever written a row into.
        assert scenarios[0]["dataset"] == "exploratory", (
            "a generated scenario must name its dataset; a model-written "
            "question may never join the golden set"
        )

        # D-12 LOCKED: forced tool_choice must be used
        assert mock_client.messages.create.called
        call_kwargs = mock_client.messages.create.call_args[1]
        tool_choice = call_kwargs.get("tool_choice", {})
        assert tool_choice.get("type") == "tool", (
            "D-12 violation: tool_choice type must be 'tool' for forced structured output"
        )
        assert tool_choice.get("name") == "submit_scenarios", (
            "D-12 violation: tool_choice name must be 'submit_scenarios'"
        )

    @patch("app.core.model_client.make_client")
    def test_generate_scenarios_sends_thinking_disabled(self, mock_factory):
        """The forced tool_choice must ship with thinking off.

        Observed 2026-08-16: DeepSeek's Anthropic-format endpoint, which was the
        default provider then via ANTHROPIC_BASE_URL, rejects a forced tool_choice with
        HTTP 400 "Thinking mode does not support this tool_choice" unless
        thinking is explicitly disabled. The parameter is inert on the real
        Anthropic API, so the flag is provider-neutral.
        """
        mock_client = mock_factory.return_value
        from app.services.scenario_service import generate_scenarios_from_chunks

        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.name = "submit_scenarios"
        mock_tool_block.input = {
            "scenarios": [
                {"question": "Q", "reference_answer": "A", "scenario_category": "factual"}
            ]
        }

        mock_response = MagicMock()
        mock_response.content = [mock_tool_block]
        mock_client.messages.create.return_value = mock_response

        generate_scenarios_from_chunks([{"content": "Some knowledge base content."}], ledger(), n=1)

        call_kwargs = mock_client.messages.create.call_args[1]
        # Precondition: with no forced tool_choice there is nothing to disable
        # thinking for, and the assertion below would pin an unrelated call.
        assert call_kwargs.get("tool_choice", {}).get("type") == "tool"
        assert call_kwargs.get("thinking") == {"type": "disabled"}, (
            f"scenario generation sent thinking={call_kwargs.get('thinking')!r}; the "
            "default provider returns HTTP 400 on a forced tool_choice without it, so "
            "no scenarios would ever be generated in production"
        )

    @patch("app.core.model_client.make_client")
    def test_generate_scenarios_includes_retrieved_contexts(self, mock_factory):
        """Generated scenarios include retrieved_contexts from the input chunks."""
        mock_client = mock_factory.return_value
        from app.services.scenario_service import generate_scenarios_from_chunks

        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.name = "submit_scenarios"
        mock_tool_block.input = {
            "scenarios": [
                {
                    "question": "Q",
                    "reference_answer": "A",
                    "scenario_category": "factual",
                }
            ]
        }

        mock_response = MagicMock()
        mock_response.content = [mock_tool_block]
        mock_client.messages.create.return_value = mock_response

        chunk_text = "Chunk content about return policies."
        scenarios = generate_scenarios_from_chunks([{"content": chunk_text}], ledger(), n=1)

        assert "retrieved_contexts" in scenarios[0]
        assert chunk_text in scenarios[0]["retrieved_contexts"]

    @patch("app.core.model_client.make_client")
    def test_generate_scenarios_raises_on_no_tool_block(self, mock_factory):
        """ValueError raised when response.content has no tool_use block."""
        mock_client = mock_factory.return_value
        from app.services.scenario_service import generate_scenarios_from_chunks

        mock_response = MagicMock()
        mock_response.content = []  # empty — no tool_use block
        mock_client.messages.create.return_value = mock_response

        with pytest.raises(ValueError, match="No tool_use block"):
            generate_scenarios_from_chunks([{"content": "some content"}], ledger(), n=1)

    @patch("app.core.model_client.make_client")
    def test_generate_scenarios_raises_on_wrong_tool_name(self, mock_factory):
        """ValueError raised when tool block has wrong name (not submit_scenarios)."""
        mock_client = mock_factory.return_value
        from app.services.scenario_service import generate_scenarios_from_chunks

        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.name = "wrong_tool"
        mock_tool_block.input = {}

        mock_response = MagicMock()
        mock_response.content = [mock_tool_block]
        mock_client.messages.create.return_value = mock_response

        with pytest.raises(ValueError, match="No tool_use block"):
            generate_scenarios_from_chunks([{"content": "some content"}], ledger(), n=1)


# ---------------------------------------------------------------------------
# test_store_scenarios_idempotent
# ---------------------------------------------------------------------------


class TestStoreScenarios:
    """Tests for the eval_scenarios table persistence helper."""

    @patch("app.services.scenario_service.psycopg2")
    def test_store_scenarios_idempotent(self, mock_psycopg2):
        """INSERT uses ON CONFLICT DO NOTHING for idempotent storage."""
        from app.services.scenario_service import store_scenarios

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        scenarios = [
            {
                "question": "What is your return policy?",
                "reference_answer": "Items can be returned within 30 days.",
                "retrieved_contexts": ["ctx1", "ctx2"],
                "source": "generated",
                "scenario_category": "factual",
                "dataset": "exploratory",
            }
        ]

        inserted = store_scenarios(scenarios, "postgresql://tenant-conn")

        # Should have called cursor.execute once (one scenario)
        assert mock_cursor.execute.called

        # ON CONFLICT DO NOTHING must be in the SQL
        call_args = mock_cursor.execute.call_args
        sql = call_args[0][0]
        assert "ON CONFLICT DO NOTHING" in sql, (
            "store_scenarios SQL must use ON CONFLICT DO NOTHING for idempotency"
        )

        # Should report 1 inserted row
        assert inserted == 1

    @patch("app.services.scenario_service.psycopg2")
    def test_store_scenarios_returns_zero_on_empty_list(self, mock_psycopg2):
        """store_scenarios returns 0 and skips DB call for empty scenario list."""
        from app.services.scenario_service import store_scenarios

        count = store_scenarios([], "postgresql://tenant-conn")
        assert count == 0
        mock_psycopg2.connect.assert_not_called()

    @patch("app.services.scenario_service.psycopg2")
    def test_store_scenarios_assigns_uuid_when_id_missing(self, mock_psycopg2):
        """store_scenarios assigns a new UUID when scenario has no 'id' key."""
        from app.services.scenario_service import store_scenarios

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_psycopg2.connect.return_value = mock_conn

        scenarios = [
            {
                "question": "Q",
                "reference_answer": "A",
                "source": "mined",
                "dataset": "exploratory",
                # no 'id' key — should be auto-generated
            }
        ]
        store_scenarios(scenarios, "postgresql://tenant-conn")

        # The first positional arg to execute (the UUID) should be a valid UUID string
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]  # positional tuple of params
        scenario_id_param = params[0]
        # Should be parseable as UUID
        uuid.UUID(scenario_id_param)  # raises if invalid


# ---------------------------------------------------------------------------
# #27: every writer names its dataset, and store_scenarios refuses a row
#      that does not
# ---------------------------------------------------------------------------


def _cursor_on(mock_psycopg2) -> MagicMock:
    """A doubled psycopg2 connection whose cursor records every execute."""
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_psycopg2.connect.return_value = conn
    return cursor


def _row(**overrides) -> dict:
    row = {
        "question": "Q",
        "reference_answer": "A",
        "source": "generated",
        "dataset": "exploratory",
    }
    row.update(overrides)
    return row


class TestTheDatasetColumnIsWritten:
    """#27. All 20 generated rows of eval run 29754ceb carried dataset = NULL.

    Both readers fold NULL to exploratory, so the golden set was 0 rows by
    construction and `summarise_run_validity` reported `golden 0/0/0` for a
    tenant that had never designated a row either way. Nothing failed and no
    test went red, because the column was nullable and every writer omitted it.
    """

    @patch("app.services.scenario_service.psycopg2")
    def test_a_stored_row_carries_its_dataset(self, mock_psycopg2):
        from app.services.scenario_service import store_scenarios

        cursor = _cursor_on(mock_psycopg2)
        store_scenarios([_row()], "postgresql://tenant-conn")

        sql, params = cursor.execute.call_args[0]
        assert "dataset" in sql, (
            f"the INSERT names no dataset column, so the row lands NULL: {sql}"
        )
        assert "exploratory" in params, (
            f"the INSERT names dataset but binds no value for it: {params}"
        )
        # The column list and the VALUES list have to stay the same length, and
        # a mismatch is the one defect a substring assertion cannot see.
        assert sql.count("%s") == len(params), (
            "the placeholder count and the parameter count disagree: "
            f"{sql.count('%s')} placeholders, {len(params)} parameters"
        )

    @patch("app.services.scenario_service.psycopg2")
    def test_a_row_without_a_dataset_is_refused_and_nothing_is_written(
        self, mock_psycopg2
    ):
        """The refusal runs before the connection opens, so the batch is atomic."""
        from app.services.scenario_service import InvalidScenario, store_scenarios

        row = _row()
        del row["dataset"]

        with pytest.raises(InvalidScenario, match="must name its dataset"):
            store_scenarios([row], "postgresql://tenant-conn")

        mock_psycopg2.connect.assert_not_called()

    @patch("app.services.scenario_service.psycopg2")
    def test_a_dataset_nobody_reports_is_refused(self, mock_psycopg2):
        """'training' is not a bucket any comparison covers, so it is not a row."""
        from app.services.scenario_service import InvalidScenario, store_scenarios

        with pytest.raises(InvalidScenario, match="training"):
            store_scenarios([_row(dataset="training")], "postgresql://tenant-conn")

        mock_psycopg2.connect.assert_not_called()

    @patch("app.services.scenario_service.psycopg2")
    def test_one_bad_row_in_a_batch_writes_none_of_them(self, mock_psycopg2):
        """ALL OR NOTHING, and the ordering is what makes it so.

        The bad row is LAST. A per-row check inside the insert loop would have
        already written the two good ones and committed nothing, leaving a
        half-stored suite whose count says the generation produced two.
        """
        from app.services.scenario_service import InvalidScenario, store_scenarios

        cursor = _cursor_on(mock_psycopg2)
        batch = [_row(question="Q1"), _row(question="Q2"), _row(question="Q3")]
        del batch[-1]["dataset"]

        with pytest.raises(InvalidScenario):
            store_scenarios(batch, "postgresql://tenant-conn")

        assert cursor.execute.call_count == 0, (
            f"{cursor.execute.call_count} row(s) were written before the batch "
            "was refused"
        )
        mock_psycopg2.connect.assert_not_called()

    def test_the_provenance_path_writes_exploratory(self):
        """A promoted trace and a contained red-team finding are exploratory too.

        `insert_provenance_scenario` takes an open connection and never reaches
        `store_scenarios`, so the refusal above cannot cover it. It is the path
        an API handler drives, which makes an omission here a NULL row written
        by a request rather than by a nightly task.
        """
        from app.services.scenario_service import insert_provenance_scenario

        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = cursor

        scenario_id = insert_provenance_scenario(
            conn,
            source="production",
            question="Q",
            reference_answer="A",
            retrieved_contexts=[],
            provenance="trace-1",
            origin_trace_id="trace-1",
        )

        uuid.UUID(scenario_id)
        sql, params = cursor.execute.call_args[0]
        assert "dataset" in sql, f"the provenance INSERT names no dataset: {sql}"
        assert "exploratory" in params, (
            f"the provenance INSERT binds no dataset value: {params}"
        )
        assert "golden" not in params, (
            "a promoted trace joined the golden set, which only the owner may "
            "designate"
        )
        assert sql.count("%s") == len(params), (
            f"{sql.count('%s')} placeholders, {len(params)} parameters"
        )


class TestTheMinerNamesItsDataset:
    """The miner feeds `store_scenarios` directly, so a silent omission here is
    a refused batch, not a NULL row.

    `run_eval_suite` wraps the mine-and-store pair in a best-effort try/except
    that logs `mine_failed` and carries on. A mined row without a dataset would
    therefore turn into one warning line a night and no mined scenarios ever
    again, with every gate green.
    """

    def test_a_mined_row_names_the_exploratory_dataset(self, monkeypatch):
        from app.services import scenario_service

        flagged = MagicMock()
        flagged.job_id = "11111111-1111-1111-1111-111111111111"
        job_row = MagicMock()
        job_row.conversation_id = "22222222-2222-2222-2222-222222222222"

        control_db = MagicMock()
        control_db.execute.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=[flagged])),
            MagicMock(fetchone=MagicMock(return_value=job_row)),
        ]
        monkeypatch.setattr(
            scenario_service,
            "_fetch_messages_for_conversation",
            lambda *a, **kw: [{"role": "user", "content": "why was I charged twice?"}],
        )

        mined = scenario_service.mine_production_scenarios(
            "agent-1", "postgresql://tenant-conn", control_db
        )

        assert [m["source"] for m in mined] == ["mined"]
        assert [m["dataset"] for m in mined] == ["exploratory"]

        # The pair, not each half on its own: what the miner produces is what
        # the writer accepts, and the two used to be checked in different files.
        with patch("app.services.scenario_service.psycopg2") as mock_psycopg2:
            cursor = _cursor_on(mock_psycopg2)
            assert (
                scenario_service.store_scenarios(mined, "postgresql://tenant-conn") == 1
            )
        assert cursor.execute.call_count == 1
