"""The checklist's wait grows with the eval it starts (#213).

OBSERVED 2026-09-07 on staging: 31 scenarios scored in 2693 s at four in flight
and 2676 s at eight, and the constant 2700 s ceiling expired at 2710 s with the
eval seconds from done. The ceiling is sized once when the wait opens, from the
rows the eval will score, and carried on the state so every continuation waits
against the same number.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from app.core.config import settings
from app.services import deployment_service
from app.services.eval_service import AGENT_INVOCATION_MAX_CALLS_PER_RUN
from app.worker.tasks.runtime import deployment as task_mod
from app.worker.tasks.runtime.eval import GENERATED_SUITE_SIZE, GENERATION_SKIP_AT_ROWS


@pytest.fixture(autouse=True)
def _known_settings(monkeypatch):
    monkeypatch.setattr(settings, "CHECKLIST_WAIT_CEILING_S", 2700)
    monkeypatch.setattr(settings, "CHECKLIST_WAIT_PER_SCENARIO_S", 120)


class TestTheRowsTheEvalWillScore:
    def test_a_tenant_above_the_generation_line_scores_what_it_holds(self):
        assert task_mod.rows_the_eval_will_score(31) == 31
        assert task_mod.rows_the_eval_will_score(GENERATION_SKIP_AT_ROWS) == GENERATION_SKIP_AT_ROWS

    def test_a_tenant_under_the_line_scores_its_rows_plus_the_generated_suite(self):
        assert task_mod.rows_the_eval_will_score(0) == GENERATED_SUITE_SIZE
        assert task_mod.rows_the_eval_will_score(8) == 8 + GENERATED_SUITE_SIZE

    def test_nothing_scores_more_than_the_invocation_cap(self):
        assert task_mod.rows_the_eval_will_score(500) == AGENT_INVOCATION_MAX_CALLS_PER_RUN
        assert task_mod.rows_the_eval_will_score(-5) == GENERATED_SUITE_SIZE


class TestTheCeilingIsTheFloorOrTheEvalsSize:
    def test_a_small_golden_set_waits_the_floor(self):
        assert task_mod.checklist_wait_ceiling_s(10) == 2700
        assert task_mod.checklist_wait_ceiling_s(22) == 2700

    def test_thirty_one_scenarios_wait_longer_than_the_run_that_expired(self):
        assert task_mod.checklist_wait_ceiling_s(31) == 3720

    def test_a_tenant_under_the_line_is_sized_on_the_rows_generation_adds(self):
        assert task_mod.checklist_wait_ceiling_s(9) == (9 + GENERATED_SUITE_SIZE) * 120

    def test_hundreds_of_rows_wait_for_the_cap_not_for_hours(self):
        assert task_mod.checklist_wait_ceiling_s(300) == AGENT_INVOCATION_MAX_CALLS_PER_RUN * 120


class TestTheWaitOpensWithItsCeilingAndCarriesIt:
    def test_open_wait_sizes_the_ceiling_from_the_tenants_count(self):
        with patch.object(
            task_mod, "_dispatch_moment", return_value=datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)
        ), patch.object(task_mod, "_scenario_count", return_value=31) as count, patch.object(
            task_mod, "_dispatch_eval_run", return_value=True
        ), patch.object(task_mod, "_dispatch_red_team_run", return_value=True):
            state = task_mod._open_wait("run-1", "agent-1", "postgresql://tenant")

        assert state["ceiling_s"] == 3720
        assert count.call_args.args == ("postgresql://tenant",)

    def test_a_continuation_waits_against_the_ceiling_it_opened_with(self):
        state = {"ceiling_s": 3720}

        assert task_mod._wait_continues(["eval"], 2800.0, task_mod._ceiling_of(state))
        assert not task_mod._wait_continues(["eval"], 3720.0, task_mod._ceiling_of(state))

    def test_a_state_without_a_ceiling_gets_the_floor(self):
        assert task_mod._ceiling_of({}) == 2700
        assert not task_mod._wait_continues(["eval"], 2800.0, task_mod._ceiling_of({}))

    def test_a_ceiling_this_build_never_wrote_is_refused(self):
        base = {
            "run_id": "run-1",
            "since": "2026-09-07T09:00:00+00:00",
            "started_at": "2026-09-07T09:00:01+00:00",
            "statuses": {"eval": None, "red_team": None},
            "eval_dispatched": True,
            "red_team_dispatched": True,
            "pass_no": 1,
        }
        assert task_mod._require_wait_state({**base, "ceiling_s": 3720})["ceiling_s"] == 3720
        for bad in ("3720", 0, -1, True, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                task_mod._require_wait_state({**base, "ceiling_s": bad})


class TestTheCountReadFailsShort:
    def test_a_count_that_cannot_be_read_is_zero(self):
        with patch.object(
            deployment_service.psycopg2, "connect", side_effect=psycopg2.OperationalError("refused")
        ):
            assert deployment_service._scenario_count("postgresql://tenant") == 0

    def test_the_count_is_the_evals_own_selection_of_scorable_rows(self):
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = (31,)
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch.object(deployment_service.psycopg2, "connect", return_value=conn):
            assert deployment_service._scenario_count("postgresql://tenant") == 31
        sql = cursor.execute.call_args.args[0]
        assert "eval_scenarios" in sql
        assert deployment_service.SELECTOR_ELIGIBILITY_PREDICATE in sql
        conn.close.assert_called_once()
