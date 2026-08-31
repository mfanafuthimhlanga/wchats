"""Unit tests for app.services.deployment_service — M8 Pre-deployment Checklist.

Tests:
    TestRunOrchestrator
        the orchestrator's turn, driven against a scripted model client

    TestDeploymentReport
        test_deployment_report_model_construction         — DEP-02
        test_deployment_report_rejects_invalid_recommendation — Pydantic Literal enforcement

    TestBlockingConditions
        test_block_when_deployment_blocked_true           — DEP-03
        test_block_on_low_eval_metric                     — DEP-03: 0.70 threshold

    TestSignalCollectionFunctions
        test_fetch_eval_summary_sync_returns_correct_shape — signal collection shape
        test_fetch_eval_summary_sync_no_runs              — empty / no-runs branch

Mock strategy:
    - the orchestrator's client is a scripted stand-in, injected by patching
      app.services.deployment_service.make_async_client. The loop itself runs.
    - psycopg2.connect patched at app.services.deployment_service.psycopg2.connect
    - No live provider or DB calls in any test
"""

import base64
import os

# Safety: ensure required env vars are present even if conftest is not loaded
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")

import inspect
import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.model_client import LedgerContext, route_for
from app.domain.calibration_status import (
    ABSENT_REASONS,
    STATUS_CALIBRATED,
    STATUS_NOT_CALIBRATED,
    STATUS_NOT_CALIBRATED_YET,
    STATUS_SETUP_ERROR,
    CalibrationStatus,
    Interval,
)
from app.domain.eval_result import (
    Cost,
    DatasetOutcome,
    EvalResult,
    Invocation,
    Measurement,
)
from app.domain.judge_identity import JudgeIdentity
from app.services.calibration_service import SUMMARY_KEYS
from app.services.deployment_service import (
    _DEPLOYMENT_SYSTEM_PROMPT,
    _TOOL_SUBMIT_REPORT,
    _VERDICT_WARNING_CATEGORIES,
    COVERAGE_SOURCE_CURRENT_BUILD,
    COVERAGE_SOURCE_RUN,
    DENOMINATOR_SOURCE_EVAL_RECORD,
    EVAL_QUALITY_UNMEASURED_WARNING_ID,
    EVAL_SIGNAL_AGENT_NOT_INVOKED,
    EVAL_SIGNAL_DID_NOT_FINISH,
    EVAL_SIGNAL_MEASURED,
    EVAL_SIGNAL_NO_RECORD,
    EVAL_SIGNAL_NO_RUNS,
    EVAL_SIGNAL_NO_VALID_SCORES,
    EVAL_SIGNAL_RUN_FAILED,
    EVAL_SIGNAL_UNAVAILABLE,
    EVAL_SUMMARY_UNAVAILABLE_SIGNAL,
    NARRATION_UNAVAILABLE_SUMMARY,
    ORCHESTRATOR_PURPOSE,
    RED_TEAM_SIGNAL_DID_NOT_FINISH,
    RED_TEAM_SIGNAL_MEASURED,
    RED_TEAM_SIGNAL_NO_RUNS,
    RED_TEAM_SIGNAL_RUN_FAILED,
    RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL,
    SUBMIT_REPORT_TOOL_NAME,
    VERDICT_WARNING_CATEGORY_UNMAPPED,
    DeploymentReport,
    DeploymentWarning,
    _compute_envelope_hash_sync,
    _eval_summary,
    _fetch_blast_radius_sync,
    _fetch_eval_summary_sync,
    _fetch_red_team_summary_sync,
    _resolve_blast_radius_thresholds,
    apply_signal_evidence_gate,
    derive_blast_radius_warnings,
    derive_quality_warnings,
    eval_summary_did_not_finish,
    parse_narration,
    poll_terminal_statuses,
    red_team_summary_did_not_finish,
    render_verdict,
    run_orchestrator,
    stored_run_records_agent_invocation,
    verdict_warnings,
)

# ---------------------------------------------------------------------------
# Helper: build a mock psycopg2 connection with controllable cursor
# ---------------------------------------------------------------------------


def _make_psycopg2_conn(fetchone_value=None, fetchall_value=None):
    """Return a mock psycopg2 connection with controllable cursor responses."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = fetchone_value
    mock_cursor.fetchall.return_value = fetchall_value if fetchall_value is not None else []
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


# ---------------------------------------------------------------------------
# Helper: build a mock get_sync_db context manager (control-DB collector)
# ---------------------------------------------------------------------------


def _make_sync_db_ctx(mock_db):
    """Return a patched get_sync_db that yields mock_db when used as 'with get_sync_db() as db'."""
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db

    return _fake_get_sync_db


def _make_scripted_db(script):
    """Build a MagicMock db whose sequential db.execute(...) results are scripted.

    Each item in `script` is a dict with one of "scalar"/"fetchall"/"first"
    mapping to the value db.execute(...).<method>() should return for that
    call, in call order — matching _fetch_blast_radius_sync's /
    _resolve_blast_radius_thresholds's exact query sequence.
    """
    mock_db = MagicMock()
    results = []
    for item in script:
        mock_result = MagicMock()
        if "scalar" in item:
            mock_result.scalar.return_value = item["scalar"]
        if "fetchall" in item:
            mock_result.fetchall.return_value = item["fetchall"]
        if "first" in item:
            mock_result.first.return_value = item["first"]
        results.append(mock_result)
    mock_db.execute.side_effect = results
    return mock_db


def _make_envelope_mock_db(rows):
    """Build a MagicMock db whose db.execute(...).mappings().all() returns rows.

    Matches _fetch_envelope_rows_sync's exact call shape:
    with get_sync_db() as db: db.execute(text(...), {...}).mappings().all().
    """
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = rows
    mock_db.execute.return_value = mock_result
    return mock_db


# ---------------------------------------------------------------------------
# test_envelope_hash_stability (module scope — 18-VALIDATION.md pins this node id)
# ---------------------------------------------------------------------------


def test_envelope_hash_stability():
    """T-18-BLR-02: the sync envelope-hash reader is stable under a no-op
    re-save (row order change), sensitive to a semantic field change, and
    structurally excludes id/agent_id/updated_at at the query layer."""
    rows_a = [
        {
            "skill": "issue_refund",
            "enabled": True,
            "rate_limit": "5/hour",
            "constraints": {"max_amount_cents": 5000},
            "requires_confirmation": False,
            "requires_identity_verification": False,
            "actor_mode": "always-on",
        },
        {
            "skill": "place_order",
            "enabled": True,
            "rate_limit": "10/hour",
            "constraints": {"max_amount_cents": 100000},
            "requires_confirmation": False,
            "requires_identity_verification": False,
            "actor_mode": "always-on",
        },
    ]

    mock_db_1 = _make_envelope_mock_db(rows_a)
    with patch("app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_1)):
        hash_1 = _compute_envelope_hash_sync("agent-a")

    mock_db_2 = _make_envelope_mock_db(rows_a)
    with patch("app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_2)):
        hash_2 = _compute_envelope_hash_sync("agent-a")

    assert hash_1 == hash_2
    assert len(hash_1) == 64
    assert all(c in "0123456789abcdef" for c in hash_1)

    # Row order change (the SELECT is ORDER BY skill, but the canonicaliser
    # is order-independent regardless — assert that independence directly).
    rows_reordered = list(reversed(rows_a))
    mock_db_3 = _make_envelope_mock_db(rows_reordered)
    with patch("app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_3)):
        hash_reordered = _compute_envelope_hash_sync("agent-a")
    assert hash_reordered == hash_1

    # A semantic field change (constraints.max_amount_cents) yields a
    # different hash.
    rows_changed = [dict(rows_a[0]), dict(rows_a[1])]
    rows_changed[0] = dict(rows_changed[0])
    rows_changed[0]["constraints"] = {"max_amount_cents": 9999}
    mock_db_4 = _make_envelope_mock_db(rows_changed)
    with patch("app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_4)):
        hash_changed = _compute_envelope_hash_sync("agent-a")
    assert hash_changed != hash_1

    # Structural Pitfall-2 guard: the SELECT column list contains none of
    # id / agent_id / updated_at (agent_id legitimately appears in the WHERE
    # clause as the filter parameter name — only the SELECT projection is
    # asserted here).
    sql_str = str(mock_db_4.execute.call_args[0][0])
    select_clause = sql_str.upper().split("FROM", 1)[0]
    projected_columns = {
        c.strip() for c in select_clause.replace("SELECT", "", 1).split(",")
    }
    assert "ID" not in projected_columns
    assert "AGENT_ID" not in projected_columns
    assert "UPDATED_AT" not in projected_columns


# ---------------------------------------------------------------------------
# TestEnvelopeHashSync
# ---------------------------------------------------------------------------


class TestEnvelopeHashSync:
    """Tests pinning that _compute_envelope_hash_sync delegates to the single
    shared canonicaliser rather than re-implementing hashing, that its
    signature carries no conn_str, and that an empty envelope hashes to a
    stable, non-empty value."""

    def test_compute_envelope_hash_sync_delegates_to_capability_service(self):
        rows = [
            {
                "skill": "issue_refund",
                "enabled": True,
                "rate_limit": "5/hour",
                "constraints": {},
                "requires_confirmation": False,
                "requires_identity_verification": False,
                "actor_mode": "always-on",
            }
        ]
        mock_db = _make_envelope_mock_db(rows)
        with patch(
            "app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db)
        ), patch(
            "app.services.deployment_service.canonical_envelope_hash"
        ) as mock_hash:
            mock_hash.return_value = "deadbeef"
            result = _compute_envelope_hash_sync("agent-x")

        mock_hash.assert_called_once_with(rows)
        assert result == "deadbeef"

    def test_compute_envelope_hash_sync_takes_no_conn_str(self):
        assert list(inspect.signature(_compute_envelope_hash_sync).parameters) == [
            "agent_id"
        ]

    def test_empty_envelope_rows_hash_is_deterministic(self):
        mock_db_1 = _make_envelope_mock_db([])
        with patch(
            "app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_1)
        ):
            result_1 = _compute_envelope_hash_sync("agent-empty")

        mock_db_2 = _make_envelope_mock_db([])
        with patch(
            "app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_2)
        ):
            result_2 = _compute_envelope_hash_sync("agent-empty")

        assert result_1 is not None
        assert len(result_1) == 64
        assert result_1 == result_2


# ---------------------------------------------------------------------------
# test_fetch_blast_radius_sync (module scope — T-18-BLR-01 pins this node id)
# ---------------------------------------------------------------------------


def test_fetch_blast_radius_sync():
    """T-18-BLR-01 / OD-1: the configured and observed single-action figures
    are reported under their own distinct keys, with deliberately different
    fixture values (5000 vs 9999) so a transposition bug fails this test.
    """
    mock_db = _make_scripted_db(
        [
            {"scalar": 5000},                    # configured_max_row
            {"scalar": 0},                        # unbounded_single_count
            {"fetchall": [("1/hour", "5000")]},   # enabled_rows
            {"scalar": 9999},                     # observed_single_row (!= 5000)
            {"scalar": 15000},                    # observed_hourly_row
            {"first": (None, None)},              # threshold row -> platform defaults
        ]
    )

    with patch(
        "app.services.deployment_service.get_sync_db",
        _make_sync_db_ctx(mock_db),
    ):
        result = _fetch_blast_radius_sync("test-agent")

    assert set(result.keys()) == {
        "configured_max_single_action_cents",
        "configured_max_hourly_aggregate_cents",
        "observed_max_single_action_cents",
        "observed_max_hourly_aggregate_cents",
        "observed_window_days",
        "warn_threshold_single_cents",
        "warn_threshold_hourly_cents",
        "enabled_skill_count",
    }
    assert result["configured_max_single_action_cents"] == 5000
    assert result["observed_max_single_action_cents"] == 9999
    assert (
        result["configured_max_single_action_cents"]
        != result["observed_max_single_action_cents"]
    )
    assert result["observed_window_days"] == settings.BLAST_RADIUS_OBSERVED_WINDOW_DAYS
    assert result["warn_threshold_single_cents"] == settings.BLAST_RADIUS_WARN_SINGLE_CENTS
    assert result["warn_threshold_hourly_cents"] == settings.BLAST_RADIUS_WARN_HOURLY_CENTS
    assert result["enabled_skill_count"] == 1


# ---------------------------------------------------------------------------
# Helper: a scripted model client, in the shape tool_loop.run_tool_loop reads
# ---------------------------------------------------------------------------
#
# The SDK harness took its client from inside itself, so a test could only patch
# the whole loop away. BACKLOG 3.10 caught that, and it is why
# `_run_orchestrator_loop` spent four phases reporting "was never awaited".
# `run_tool_loop` takes the client as an argument, so the loop runs for real here
# and a scripted reply stands in for the model.


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name, arguments, call_id="call_1"):
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _FakeCompletion:
    def __init__(self, choice):
        self.choices = [choice]


class _FakeCompletions:
    def __init__(self, replies, requests):
        self._replies = list(replies)
        self._requests = requests

    async def create(self, **kwargs):
        self._requests.append(kwargs)
        return _FakeCompletion(self._replies.pop(0))


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeAsyncClient:
    """One scripted reply per model call, popped in order.

    `requests` keeps every request body the loop built, which is where the tool
    list, the model and the system prompt are read back. `closed` is how the
    caller's ownership of the client is observed: `run_tool_loop` closes nothing.
    """

    def __init__(self, *replies):
        self.requests = []
        self.chat = _FakeChat(_FakeCompletions(replies, self.requests))
        self.closed = False

    async def close(self):
        self.closed = True


def _reply_calling(name, arguments, call_id="call_1"):
    """An assistant turn that calls one tool."""
    return _FakeChoice(
        _FakeMessage(tool_calls=[_FakeToolCall(name, json.dumps(arguments), call_id)]),
        finish_reason="tool_calls",
    )


def _reply_saying(content):
    """An assistant turn that calls nothing and stops."""
    return _FakeChoice(_FakeMessage(content=content))


_A_REPORT = {"recommendation": "ship", "summary": "All good.", "warnings": []}


def _a_ledger(recorder=None):
    """The three ids each model_calls row carries, and where the row goes."""
    return LedgerContext(
        tenant_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        recorder=recorder if recorder is not None else (lambda call: None),
    )


def _drive(client, ledger=None, container=None):
    """Run the whole orchestrator against `client` and return the container."""
    container = {} if container is None else container
    with patch(
        "app.services.deployment_service.make_async_client", return_value=client
    ) as factory:
        run_orchestrator(
            json.dumps({"eval_summary": {}, "red_team_summary": {}}),
            container,
            ledger=ledger if ledger is not None else _a_ledger(),
        )
    return container, factory


# ---------------------------------------------------------------------------
# TestRunOrchestrator
# ---------------------------------------------------------------------------


class TestRunOrchestrator:
    """The orchestrator turn, run end to end against a scripted client.

    WHAT THESE OBSERVE AND WHAT THEY DO NOT. A scripted client is not a model,
    so nothing here observes the prompt's blocking conditions being obeyed;
    deployment_service.py's own comment above `_TOOL_SUBMIT_REPORT` says why the
    platform never depends on that. What they do observe is the wiring BACKLOG
    1.32 found broken. The tool the prompt names is the tool the model is given,
    and the stop, the billing ids and the client's close are observed with it.
    """

    def test_run_orchestrator_populates_result_container(self):
        """DEP-01 the sync bridge, DEP-02 the report the tool call carried."""
        client = _FakeAsyncClient(_reply_calling(SUBMIT_REPORT_TOOL_NAME, _A_REPORT))

        container, _ = _drive(client)

        assert container["report"]["recommendation"] == "ship"
        assert container["report"]["summary"] == "All good."
        assert container["report"]["warnings"] == []

    def test_the_tool_the_prompt_names_is_the_tool_the_model_is_given(self):
        """BACKLOG 1.32, pinned as a value on the wire rather than as wiring.

        The defect was a tool DESCRIBED in the prompt and REGISTERED nowhere, so
        every checklist failed with "Orchestrator did not produce a report". Both
        halves are asserted here against one name.
        """
        client = _FakeAsyncClient(_reply_calling(SUBMIT_REPORT_TOOL_NAME, _A_REPORT))

        _drive(client)

        assert SUBMIT_REPORT_TOOL_NAME in _DEPLOYMENT_SYSTEM_PROMPT, (
            "the prompt no longer names the tool it tells the model to call"
        )
        sent = [t["function"]["name"] for t in client.requests[0]["tools"]]
        assert sent == [SUBMIT_REPORT_TOOL_NAME], (
            f"the model was given {sent}, so the tool the prompt names is either "
            "missing or joined by one nobody authorised"
        )

    def test_an_unregistered_tool_call_leaves_the_container_empty(self):
        """The tool list is the allowlist. `dispatch` refuses anything else.

        The SDK needed `allowed_tools` and `permission_mode` for this. Here the
        refusal comes back as an error tool result, the loop runs on, and no
        report is written by a name nobody registered.
        """
        client = _FakeAsyncClient(
            _reply_calling("submit_report_v2", _A_REPORT),
            _reply_saying("I could not submit."),
        )

        container, _ = _drive(client)

        assert container == {}

    def test_the_loop_stops_on_the_report_and_pays_for_one_call(self):
        """`stop_after` fires AFTER the handler, so the report is present.

        The second reply is scripted and never consumed: a turn spent reading the
        handler's ack is a turn nobody reads and money nobody gets back.
        """
        client = _FakeAsyncClient(
            _reply_calling(SUBMIT_REPORT_TOOL_NAME, _A_REPORT),
            _reply_saying("a second turn nobody asked for"),
        )

        container, _ = _drive(client)

        assert container["report"] == _A_REPORT
        assert len(client.requests) == 1, (
            f"{len(client.requests)} model calls for one report; the loop did not "
            "stop on submit_report"
        )

    def test_the_first_report_wins(self):
        """Two calls in one turn. The container keeps the first, and stops there."""
        client = _FakeAsyncClient(
            _FakeChoice(
                _FakeMessage(
                    tool_calls=[
                        _FakeToolCall(
                            SUBMIT_REPORT_TOOL_NAME, json.dumps(_A_REPORT), "call_1"
                        ),
                        _FakeToolCall(
                            SUBMIT_REPORT_TOOL_NAME,
                            json.dumps({**_A_REPORT, "recommendation": "block"}),
                            "call_2",
                        ),
                    ]
                ),
                finish_reason="tool_calls",
            )
        )

        container, _ = _drive(client)

        assert container["report"]["recommendation"] == "ship"

    def test_a_turn_that_calls_nothing_leaves_the_container_empty(self):
        """The caller reads the absence and fails the run. It must stay an absence."""
        client = _FakeAsyncClient(_reply_saying("I decline to assess this."))

        container, _ = _drive(client)

        assert container == {}

    def test_the_client_is_closed_whether_or_not_a_report_arrives(self):
        """`run_tool_loop` closes no client, so this loop must, on both paths."""
        reported = _FakeAsyncClient(_reply_calling(SUBMIT_REPORT_TOOL_NAME, _A_REPORT))
        silent = _FakeAsyncClient(_reply_saying("nothing to say"))

        _drive(reported)
        _drive(silent)

        assert reported.closed, "the client of a successful turn was left open"
        assert silent.closed, "the client of a turn that produced no report was left open"

    def test_every_model_call_is_billed_to_the_agents_own_tenant(self):
        """Ticket #47's rule at this call site: no client without a ledger row.

        The purpose is asserted as a value because `route_for` raises on a
        purpose the table does not route, and the rollup groups on this string.
        """
        ledger = _a_ledger()
        client = _FakeAsyncClient(_reply_calling(SUBMIT_REPORT_TOOL_NAME, _A_REPORT))

        _, factory = _drive(client, ledger=ledger)

        purpose = factory.call_args[0][0]
        kwargs = factory.call_args[1]
        assert purpose == ORCHESTRATOR_PURPOSE == "deployment_orchestrator"
        assert kwargs["tenant_id"] == ledger.tenant_id
        assert kwargs["agent_id"] == ledger.agent_id
        assert kwargs["job_id"] == ledger.job_id
        assert kwargs["recorder"] is ledger.recorder

    def test_the_model_and_the_persona_come_from_one_place_each(self):
        """The model is the routing table's, the prompt is the module's.

        `SONNET_MODEL = "claude-sonnet-4-6"` was this module's own until #49, and
        the credential it needed was revoked on 2026-08-26. A model named here
        again would be a second answer to a question the table already answers.
        """
        client = _FakeAsyncClient(_reply_calling(SUBMIT_REPORT_TOOL_NAME, _A_REPORT))

        _drive(client)

        body = client.requests[0]
        assert body["model"] == route_for(ORCHESTRATOR_PURPOSE).model
        assert body["messages"][0] == {
            "role": "system",
            "content": _DEPLOYMENT_SYSTEM_PROMPT,
        }

    def test_the_bridge_swallows_a_failing_turn(self):
        """BACKLOG 1.33. The Celery task has its own handler and its own log line.

        A raise out of this bridge would reach a caller that already treats an
        empty container as the failure, so the failure would be reported twice
        and the second report would be the less specific one.
        """
        container = {}
        with patch(
            "app.services.deployment_service.make_async_client",
            side_effect=RuntimeError("no credential"),
        ):
            run_orchestrator(json.dumps({}), container, ledger=_a_ledger())

        assert container == {}


# ---------------------------------------------------------------------------
# TestDeploymentReport
# ---------------------------------------------------------------------------


class TestDeploymentReport:
    """Tests for DeploymentReport Pydantic model construction."""

    def test_deployment_report_model_construction(self):
        """Construct a full DeploymentReport and verify field values (DEP-02)."""
        warning = DeploymentWarning(
            warning_id="test",
            category="eval_quality",
            message="Low score",
            severity_level="warning",
        )
        r = DeploymentReport(
            recommendation="ship_with_warnings",
            summary="Some warnings.",
            warnings=[warning],
            eval_summary={},
            red_team_summary={},
            verified_qa_stats={},
            corpus_stats={},
        )
        assert r.recommendation == "ship_with_warnings"
        assert len(r.warnings) == 1
        assert r.warnings[0].warning_id == "test"
        assert r.warnings[0].category == "eval_quality"
        assert r.warnings[0].severity_level == "warning"

    def test_deployment_report_rejects_invalid_recommendation(self):
        """Pydantic Literal enforcement: 'invalid_value' must raise ValidationError."""
        with pytest.raises(ValidationError):
            DeploymentReport(
                recommendation="invalid_value",
                summary="Should fail.",
                warnings=[],
                eval_summary={},
                red_team_summary={},
                verified_qa_stats={},
                corpus_stats={},
            )


# ---------------------------------------------------------------------------
# TestBlockingConditions
# ---------------------------------------------------------------------------


def _absent_calibration():
    """How an unread calibration artifact reaches decide(). Never None."""
    from app.domain.calibration_status import CalibrationStatus

    return CalibrationStatus.absent("no_artifact")


class TestBlockingConditions:
    """DEP-03's blocking conditions, asserted where they are now enforced (#54).

    These three used to be pinned as substrings of _DEPLOYMENT_SYSTEM_PROMPT,
    which is where they were also enforced: the model read the numbers and
    returned a `recommendation`. That is issue #36, and criterion 2 of ticket 17
    is that the prompt quotes no threshold at all. So the conditions are
    unchanged and the observation moved to the Python that acts on them.
    """

    def test_the_prompt_quotes_no_threshold_at_all(self):
        """Criterion 2. A threshold in a prompt is a second copy that drifts, and
        the copy a deploy acts on would be whichever the model happened to
        quote."""
        for literal in ("0.85", "0.70", ">= 50", "0.90"):
            assert literal not in _DEPLOYMENT_SYSTEM_PROMPT, (
                f"{literal} is still in the orchestrator prompt. Every number the "
                "decision turns on lives on a constant in app.domain.verdict."
            )

    def test_a_deployment_blocked_result_blocks_in_python(self):
        summary = _measured_red_team(deployment_blocked=True)

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert "red_team_critical_finding" in [w.warning_id for w in warnings]

    def test_an_open_high_finding_blocks_in_python_while_the_setting_says_so(self):
        summary = _measured_red_team(high_count=4)

        with patch.object(settings, "DEP_BLOCK_ON_HIGH_RED_TEAM", True):
            recommendation, warnings = apply_signal_evidence_gate(
                "ship", _measured_eval(), summary
            )

        assert recommendation == "block"
        assert "red_team_high_finding" in [w.warning_id for w in warnings]

    def test_a_low_exploratory_pass_rate_blocks_in_the_rule_table(self):
        """The 0.70 the prompt used to quote is EXPLORATORY_BLOCK_UPPER, and it
        is a Wilson upper bound rather than a point estimate now."""
        from app.domain.verdict import EXPLORATORY_BLOCK_UPPER, Outcome, decide

        record = _record(
            datasets={
                "exploratory": _outcome(
                    attempted=40, valid=40, scored=40, failed=20,
                    faithfulness=0.5, answer_relevancy=0.5,
                )
            }
        )

        verdict = decide(record, None, _absent_calibration(), block_on_high=True)

        assert verdict.outcome is Outcome.BLOCK
        assert "exploratory_ci_blocks" in [r.rule for r in verdict.reasons]
        assert EXPLORATORY_BLOCK_UPPER == 0.70


# ---------------------------------------------------------------------------
# TestSignalCollectionFunctions
# ---------------------------------------------------------------------------


def _measurement(value, observations=10):
    """A metric that was measured, or an unmeasured one when value is None."""
    if value is None:
        return Measurement(value=None, observations=0, measured=False)
    return Measurement(value=value, observations=observations, measured=True)


def _outcome(attempted=30, valid=30, scored=30, *, failed=0, unmeasured=0, **metrics):
    """One dataset's counts, its per-scenario verdicts and whichever metrics it reported.

    Whatever a caller does not call failed or unmeasured passed. The three add up
    to `scored` or `DatasetOutcome` refuses to be built, which is the floor under
    the count the deploy gate reads.
    """
    return DatasetOutcome(
        attempted=attempted,
        valid=valid,
        scored=scored,
        metrics={name: _measurement(value) for name, value in metrics.items()},
        scenarios_passed=scored - failed - unmeasured,
        scenarios_failed=failed,
        scenarios_unmeasured=unmeasured,
    )


def _record(
    datasets=None, *, attempted=30, valid=30, scored=30, failed=0, unmeasured=0, **overrides
):
    """An EvalResult the collector can lift numbers off.

    The default is ONE scoring dataset, exploratory, which is the shape of an
    ordinary tenant with no golden rows designated. A test that wants the
    two-dataset shape passes both, and gets the honest consequence: no run-level
    number anywhere on the payload.
    """
    if datasets is None:
        datasets = {
            "exploratory": _outcome(
                attempted=attempted,
                valid=valid,
                scored=scored,
                failed=failed,
                unmeasured=unmeasured,
                faithfulness=0.92,
                answer_relevancy=0.88,
            )
        }
    fields = {
        "run_id": str(uuid.uuid4()),
        "agent_id": str(uuid.uuid4()),
        # Invocation's `valid` is the DENOMINATOR and `attempted` is the subset
        # the per-run ceiling let run, which is the opposite nesting from
        # DatasetOutcome's. Coherent numbers rather than the dataset's, because a
        # record that would be refused on construction proves nothing about a
        # reader.
        "invocation": Invocation(
            status="measured",
            valid=valid,
            attempted=valid,
            responded=scored,
            scorable=scored,
            failed=valid - scored,
            empty=0,
        ),
        "datasets": datasets,
        "requested_model": "gpt-5.6-luna",
        "cost": Cost(input_tokens=10, output_tokens=5, usd=0.01, zar=0.2, measured=True),
    }
    fields.update(overrides)
    return EvalResult(**fields)


def _make_eval_conn(run_row, record=None, raise_on=None):
    """psycopg2 connection double for _fetch_eval_summary_sync.

    The function issues ONE statement: the latest run, selecting `result` beside
    `config`. The AVG/COUNT pair over `eval_results` went in #51 slice 4 and the
    per-scenario `COUNT(*) FILTER` went in the review pass, so this double serves
    no result rows at all. A test that wants numbers passes a `record`, which is
    where the run's numbers actually live.

    `run_row` is the five-column shape the collector selects: (id, finished_at,
    status, config, result). A three- or four-tuple is accepted and padded, which
    is the pre-0013 / pre-0022 row the fallbacks read.

    `record` is an EvalResult (or a raw payload dict) written into the run row's
    fifth column, unless the caller supplied a five-tuple itself.

    raise_on: substring of the SQL that should raise UndefinedColumn. That is how
    a tenant DB older than the migration presents itself, and the collector
    degrades to a narrower SELECT rather than reporting an outage.

    THE PAD IS STILL NULL, deliberately. A NULL config is a run that recorded no
    invocation claim, which the collector reports as
    EVAL_SIGNAL_AGENT_NOT_INVOKED — so a test that wants any other state has to
    say so with `_invoked_config()`. A NULL result is a run that recorded no
    numbers, which is EVAL_SIGNAL_NO_RECORD for the same reason.

    THE ROW IS AS WIDE AS THE STATEMENT THAT ASKED FOR IT (P3 review). No
    production database answers `SELECT id, finished_at, status` with four
    columns, and a double that does would stay green while a future read of
    run_row[3] on the fallback path failed in production. The row is sliced at
    fetch time against the SQL that was actually executed.
    """
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)

    if run_row is not None and len(run_row) == 4 and record is not None:
        payload = record.payload if isinstance(record, EvalResult) else record
        run_row = (*run_row, payload)
    elif run_row is not None and len(run_row) < 5 and record is not None:
        payload = record.payload if isinstance(record, EvalResult) else record
        run_row = (*run_row, None, payload)[:5]

    executed: list[str] = []
    state = {"run": run_row}

    def _execute(sql, params=None):
        executed.append(sql)
        if raise_on is not None and raise_on in sql:
            raise psycopg2.errors.UndefinedColumn(f"column does not exist: {raise_on}")

    def _fetchone():
        row = state["run"]
        if row is None:
            return None
        sql = executed[-1] if executed else ""
        if "result FROM eval_runs" in sql:
            return tuple(row) if len(row) == 5 else (*row, *([None] * (5 - len(row))))
        if "config FROM eval_runs" in sql:
            return tuple(row[:4]) if len(row) >= 4 else (*row, None)
        return tuple(row[:3])

    cursor.execute.side_effect = _execute
    cursor.fetchone.side_effect = _fetchone
    conn.cursor.return_value = cursor
    conn.executed = executed
    return conn


def _invoked_config(**extra) -> dict:
    """An `eval_runs.config` written by a run that actually invoked the agent.

    Audit D1 / P3: `config["agent_invoked"]` is a precondition of every eval
    state other than the refusal, so a test about denominators or column names or
    in-flight shadowing has to supply one or it is testing the D1 refusal under
    another name.
    """
    return {"agent_invoked": True, **extra}


class TestSignalCollectionFunctions:
    """Tests for _fetch_eval_summary_sync signal collection helper."""

    def test_fetch_eval_summary_sync_returns_correct_shape(self):
        """The payload's numbers are the record's numbers (DEP-01, #51)."""
        run_id = uuid.uuid4()
        run_ts = datetime(2026, 5, 23, 2, 0, 0)

        mock_conn = _make_eval_conn(
            (run_id, run_ts, "complete", _invoked_config()),
            record=_record(),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        assert result["pass_rates"]["faithfulness"] == pytest.approx(0.92)
        assert result["pass_rates"]["answer_relevancy"] == pytest.approx(0.88)
        assert result["pass_rates_dataset"] == "exploratory", (
            "a run-level number nobody attributed is a number a reader will "
            "attribute to the wrong half"
        )
        assert result["scenario_count"] == 30, "attempted"
        assert result["scored_scenario_count"] == 30, "scored"
        assert result["denominator_source"] == DENOMINATOR_SOURCE_EVAL_RECORD
        assert result["last_run_at"] == run_ts.isoformat()
        assert result["last_run_status"] == "complete"

    def test_the_numbers_follow_an_edited_record(self):
        """Edit the stored record, and every number on the payload moves with it.

        This is criterion 1 stated as a test. The collector holds no arithmetic
        of its own, so there is nothing here that could hold the old figure while
        the record says something else.
        """
        edited = _record(
            datasets={
                "exploratory": _outcome(
                    attempted=12, valid=11, scored=7, faithfulness=0.41
                )
            },
            attempted=12,
            valid=11,
            scored=7,
        )
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=edited,
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["pass_rates"] == {"faithfulness": pytest.approx(0.41)}
        assert result["scenario_count"] == 12
        assert result["valid_scenario_count"] == 11
        assert result["scored_scenario_count"] == 7
        assert result["datasets"]["exploratory"]["scenario_count"] == 12
        assert result["metrics"]["answer_relevancy"]["measured"] is False, (
            "a metric the record does not report reads unmeasured, never zero"
        )

    def test_the_measurements_survive_every_result_row_being_deleted(self):
        """No `eval_results` row exists, and every number is still the record's.

        The collector used to derive every figure from those rows, so deleting
        them emptied `pass_rates` and the gate read a measured run as unmeasured.
        It then kept ONE read of them, the per-scenario verdict counts, and that
        read is what this test caught: over an empty table the `COUNT(*) FILTER`
        said nought failing and nought undecided about a run whose record says it
        scored thirty, and the gate shipped on "nothing failed". The counts are
        the run's own now and the deleted rows change none of them.
        """
        # The judge returned context_precision for all thirty rows and neither
        # gated dimension for any of them, which is the run the reviewer built:
        # `scored` is thirty and the evidence a deploy needs is nought.
        record = _record(
            datasets={
                "exploratory": _outcome(
                    attempted=30,
                    valid=30,
                    scored=30,
                    unmeasured=30,
                    context_precision=0.71,
                )
            }
        )
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=record,
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        assert result["pass_rates"] == {"context_precision": pytest.approx(0.71)}
        assert result["datasets"]["exploratory"]["scored_scenario_count"] == 30
        assert result["failing_scenarios"] == 0
        assert result["unmeasured_scenarios"] == 30, (
            "thirty scenarios scored and no gated verdict decided one of them; "
            "the count says thirty undecided rather than nought failing"
        )

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", result, _measured_red_team()
        )
        assert recommendation == "block", (
            "a run that decided no scenario has no quality evidence, and "
            "unknown quality may never approve a deploy"
        )
        assert [w.warning_id for w in warnings] == [
            EVAL_QUALITY_UNMEASURED_WARNING_ID
        ]

    def test_the_collector_reads_nothing_out_of_eval_results(self):
        """Read out of the SQL that ran, not out of the source text.

        Audit D3's `AVG(score) ... GROUP BY metric` was the second derivation of
        one run's figures and the `COUNT(*) FILTER` over `binary_verdict` was the
        last of them. This collector now issues one statement, for the run row,
        and touches `eval_results` not at all.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=_record(),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        joined = " ".join(mock_conn.executed)
        assert "AVG(" not in joined, (
            "the deploy gate is averaging scores again; the run's own record is "
            "the one derivation (#51 criterion 1)"
        )
        assert "GROUP BY metric" not in joined
        assert "COUNT(DISTINCT scenario_id)" not in joined
        assert "eval_results" not in joined, (
            "every figure the deploy gate reads is on eval_runs.result; a "
            "statement against eval_results is a second derivation of it"
        )

    def test_an_in_flight_run_does_not_shadow_the_last_finished_one(self):
        """A run in progress must not block the deploy it is measuring for.

        The selector took the newest eval_runs row with NO status filter, so for
        the whole duration of a run the gate read a 'running' row that has no
        record yet, returned an absent signal, and refused the deploy with "this
        agent's answer quality has not been measured", while a perfectly good
        completed run sat one row below it. That window was minutes before D1/P2
        and is up to ninety per agent per night after it: the nightly beat fires
        at 02:00 UTC and drives up to sixty live turns at 90 s each.

        Asserted on the SQL, because the double cannot express "there is also an
        older row": the filter is the whole behaviour.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=_record(),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        run_selects = [sql for sql in mock_conn.executed if "FROM eval_runs" in sql]
        assert run_selects, "no eval_runs SELECT was issued"
        for sql in run_selects:
            assert "status <> 'running'" in sql, (
                "the deploy gate selects the newest eval_runs row without "
                f"excluding in-flight ones: {sql!r}. For the duration of every "
                "nightly run the owner is told their agent has not been "
                "measured, because its own eval is in progress."
            )

    def test_fetch_eval_summary_sync_no_runs(self):
        """No eval run at all is 'no_runs' with nulls throughout, never zeros."""
        mock_conn = _make_eval_conn(None)

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_NO_RUNS
        assert result["pass_rates"] is None, (
            "an empty dict reads as 'no metric is failing' to anything that "
            "iterates it — audit D3's fail-open"
        )
        assert result["failing_scenarios"] is None
        assert result["scenario_count"] is None, (
            "a zero here asserts that a run covered nothing, and no run exists "
            "to have covered anything"
        )
        assert result["scored_scenario_count"] is None
        assert result["result"] == "absent"


# ---------------------------------------------------------------------------
# TestEvalSummaryD3. The removed query and its distinguishable absence
# ---------------------------------------------------------------------------


class TestEvalSummaryD3:
    """Audit D3: the deploy gate's eval query could not execute.

    `SELECT metric_name, AVG(score) ... WHERE run_id = %s` against a table whose
    columns are `metric` and `eval_run_id` raised UndefinedColumn on every
    invocation. The Celery task caught it, substituted `pass_rates: {}`, and the
    blocking condition "any eval metric pass_rate < 0.70" then evaluated over an
    empty dict, which cannot fire.

    The query itself is gone since #51 slice 4, and with it the column names that
    could be wrong. The discipline it forced is what these tests hold: a read
    that fails produces a DISTINGUISHABLE value and the gate refuses on it.
    """

    def test_a_failing_query_is_unavailable_not_clean(self):
        """The exact D3 failure, reproduced: the read raises.

        The old behaviour returned `pass_rates: {}` from the caller and the gate
        read it as 'nothing is failing'. The behaviour has to be DISTINGUISHABLE
        from a measured-and-clean run, or the fail-open simply moves one layer up.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=_record(),
        )
        mock_conn.cursor.return_value.execute.side_effect = psycopg2.OperationalError(
            "connection reset"
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_UNAVAILABLE
        assert result["pass_rates"] is None
        assert result["failing_scenarios"] is None

        # And the gate refuses to ship on it — the half that makes the
        # distinguishable value worth having.
        recommendation, warnings = apply_signal_evidence_gate(
            "ship", result, _measured_red_team()
        )
        assert recommendation == "block"
        assert any(w.warning_id == "eval_signal_unavailable" for w in warnings)

    def test_a_run_whose_record_measured_nothing_is_unknown_not_passing(self):
        """Every metric unmeasured, which is a judge outage, is 'no_valid_scores'.

        Zero valid observations is unknown quality. Reporting it as an empty
        pass_rates dict would make a run that measured nothing satisfy "all eval
        metrics >= 0.85" vacuously.
        """
        nothing = _record(
            datasets={"exploratory": _outcome(attempted=30, valid=30, scored=0)},
            scored=0,
        )
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=nothing,
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_NO_VALID_SCORES
        assert result["pass_rates"] is None
        assert apply_signal_evidence_gate("ship", result, _measured_red_team())[0] == (
            "block"
        )

    def test_the_run_is_selected_by_kind_so_a_sibling_agent_is_not_read(self):
        """`kind` is 'm6:{agent_id}'; without the filter a second agent in the
        same tenant DB has its run reported as this agent's."""
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=_record(),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            _fetch_eval_summary_sync("agent-42", "postgresql://test/tenant")

        runs_sql = [s for s in mock_conn.executed if "FROM eval_runs" in s]
        assert runs_sql and "kind = %s" in runs_sql[0]

    def test_a_failed_run_is_reported_as_failed(self):
        """Since the P1 persistence split a FAILED run lands a terminal status
        on production, so `last_run_at` alone can describe a run that produced
        nothing. The status travels with it.

        The status is admissibility in its own right. See
        TestFailedRunIsNotEvidence for the case that could not otherwise reach it.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "failed", _invoked_config()),
            record=_record(),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["last_run_status"] == "failed"
        assert result["eval_signal"] == EVAL_SIGNAL_RUN_FAILED


class TestTheRecordIsTheOnlyDenominator:
    """The attempted count is the RUN's, and now there is only one of it (#51).

    `scenario_count` was COUNT(DISTINCT scenario_id) over eval_results, the
    scenarios the judge came BACK about. write_eval_results only ever writes a
    row per score the judge produced, so a scenario the judge dropped entirely
    left no trace there: attempted could not exceed scored except in the all-NULL
    case, and the orchestrator's instruction, "a pass rate over a handful of
    scored scenarios out of many attempted is a weak signal and you must say
    so", compared two numbers derived from the same five rows.

    The P2 fix read `config["dataset"]` when it was there and fell back to the
    results-derived floor when it was not, with a label saying which. Slice 4
    deletes both parsers: the record carries all three counts, so there is one
    source and the label says only that.
    """

    def test_a_partial_judge_outage_is_visible_in_the_denominators(self):
        """The failing input, exactly as filed.

        A run fetches 40 valid scenarios; a judge partial outage returns 5, all
        scored 0.95. Under the old collector the gate saw scenario_count=5,
        scored=5, faithfulness=0.95: a clean measurement of an agent whose other
        35 scenarios were never scored at all.
        """
        outage = _record(
            datasets={
                "exploratory": _outcome(
                    attempted=40, valid=40, scored=5, faithfulness=0.95
                )
            },
            attempted=40,
            valid=40,
            scored=5,
        )
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=outage,
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        assert result["scenario_count"] == 40, (
            "attempted must come from the run's own record of what it covered"
        )
        assert result["valid_scenario_count"] == 40
        assert result["scored_scenario_count"] == 5
        assert result["denominator_source"] == DENOMINATOR_SOURCE_EVAL_RECORD
        assert result["scored_scenario_count"] < result["scenario_count"], (
            "the pair the orchestrator is told to compare must be able to differ"
        )

    def test_a_run_with_no_record_reports_no_counts_at_all(self):
        """A pre-0022 tenant, or a run that died before it wrote its record.

        There is no floor to fall back to any more and that is the honest state:
        the results-derived count this used to report was bounded below by the
        scored count, so its equality with it was an artefact rather than
        evidence of coverage. Nulls say the payload cannot answer.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=None,
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_NO_RECORD
        assert result["result"] == "absent"
        assert result["scenario_count"] is None
        assert result["valid_scenario_count"] is None
        assert result["scored_scenario_count"] is None
        assert result["denominator_source"] is None
        assert result["pass_rates"] is None

    def test_a_recordless_run_makes_the_gate_refuse(self):
        """Criterion (c). A run with no record cannot approve a launch."""
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=None,
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", result, _measured_red_team()
        )
        assert recommendation == "block", (
            "a run that recorded no measurement is unknown quality, and unknown "
            "quality may never approve a deploy"
        )
        assert any(w.warning_id == "eval_signal_unavailable" for w in warnings)

    def test_a_stored_record_that_breaks_a_rule_reads_as_absent(self):
        """A measured=False metric carrying a 0.9. The gate gets no record."""
        broken = _record().payload
        broken["datasets"]["exploratory"]["metrics"]["faithfulness"] = {
            "value": 0.9,
            "measured": False,
            "observations": 0,
        }
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=broken,
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_NO_RECORD
        assert result["pass_rates"] is None

    def test_a_run_that_scored_nothing_still_reports_what_it_attempted(self):
        """40 attempted, nothing measured. 'No valid scores' and 'nothing was
        attempted' are different events and used to report identical zeros."""
        nothing = _record(
            datasets={"exploratory": _outcome(attempted=40, valid=40, scored=0)},
            attempted=40,
            valid=40,
            scored=0,
        )
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=nothing,
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_NO_VALID_SCORES
        assert result["scenario_count"] == 40
        assert result["scored_scenario_count"] == 0
        assert result["pass_rates"] is None

    def test_the_prompt_tells_the_orchestrator_where_the_denominator_came_from(self):
        """A labelled figure the reader cannot see the label of is just a number."""
        assert "denominator_source" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "valid_scenario_count" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "coverage_source" in _DEPLOYMENT_SYSTEM_PROMPT


class TestTheTwoDatasetsAreNeverPooled:
    """A run whose halves both scored has no run-level number, and says so."""

    def _both(self):
        return _record(
            datasets={
                "golden": _outcome(
                    attempted=12, valid=12, scored=12,
                    faithfulness=0.94, answer_relevancy=0.90,
                ),
                "exploratory": _outcome(
                    attempted=30, valid=28, scored=25,
                    faithfulness=0.71, answer_relevancy=0.66,
                ),
            },
            attempted=42,
            valid=40,
            scored=37,
        )

    def _summary(self, record):
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=record,
        )
        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            return _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

    def test_a_two_dataset_run_has_no_run_level_number(self):
        """Criterion (d), first half. The golden set is fixed and the
        exploratory sample rotates, so one mean over both moves with the draw
        while looking like a quality change."""
        result = self._summary(self._both())

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        assert result["pass_rates"] is None, (
            "a run-level rate over two datasets is the pooled mean this record "
            "refuses to hold"
        )
        assert result["pass_rates_dataset"] is None
        for reading in result["metrics"].values():
            assert reading["measured"] is False
            assert reading["value"] is None

    def test_the_two_halves_travel_beside_each_other(self):
        """Criterion (d), second half. The gate reads per dataset."""
        result = self._summary(self._both())

        golden = result["datasets"]["golden"]
        exploratory = result["datasets"]["exploratory"]
        assert golden["metrics"]["faithfulness"]["value"] == pytest.approx(0.94)
        assert exploratory["metrics"]["faithfulness"]["value"] == pytest.approx(0.71)
        assert golden["scenario_count"] == 12
        assert exploratory["scenario_count"] == 30
        assert result["scenario_count"] == 42, "the run's total is the sum it stored"

        assert apply_signal_evidence_gate("ship", result, _measured_red_team())[0] == (
            "ship"
        ), (
            "the gate finds its evidence per dataset; refusing here would block "
            "every tenant with a designated golden set over numbers it does hold"
        )

    def test_a_gated_metric_measured_on_no_dataset_refuses(self):
        """The reachable fail-open the pooled mean used to hide.

        `context_precision` came back and the two gated metrics did not, so the
        record reports `scored` above zero and the deploy gate has no quality
        evidence at all. Missing data is never passing data.
        """
        ungated_only = _record(
            datasets={
                "golden": _outcome(
                    attempted=12, valid=12, scored=12, context_precision=0.99
                ),
                "exploratory": _outcome(
                    attempted=30, valid=30, scored=30, context_recall=0.98
                ),
            },
            attempted=42,
            valid=42,
            scored=42,
        )
        result = self._summary(ungated_only)

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        recommendation, warnings = apply_signal_evidence_gate(
            "ship", result, _measured_red_team()
        )
        assert recommendation == "block"
        assert any(w.warning_id == "eval_quality_unmeasured" for w in warnings)

    def test_one_scoring_dataset_names_itself(self):
        """A golden-only run reports the golden numbers AS the run's, named."""
        golden_only = _record(
            datasets={
                "golden": _outcome(
                    attempted=12, valid=12, scored=12, faithfulness=0.94
                ),
                "exploratory": _outcome(attempted=30, valid=0, scored=0),
            },
            attempted=42,
            valid=12,
            scored=12,
        )
        result = self._summary(golden_only)

        assert result["pass_rates"] == {"faithfulness": pytest.approx(0.94)}
        assert result["pass_rates_dataset"] == "golden"


class TestScenarioVerdictCounts:
    """`failing_scenarios` and `unmeasured_scenarios` are the RUN's own counts.

    They were `sum(1 for v in rates.values() if v < 0.70)` over the metric
    averages, then a `COUNT(*) FILTER` over `eval_results` at deploy time. The
    second still counted at read time, so it answered a question the run had
    already answered and answered it over rows that can move. The run counts its
    scenarios once, from the JudgeRecords it built, and stores the three counts
    per dataset.
    """

    def _summary(self, record):
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
            record=record,
        )
        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            return _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

    def test_the_failures_are_the_ones_the_record_counted(self):
        result = self._summary(_record(failed=2))
        assert result["failing_scenarios"] == 2
        assert result["unmeasured_scenarios"] == 0
        assert result["scored_scenario_count"] == 30

    def test_an_undecided_scenario_is_unmeasured_not_failed(self):
        """None-first, the ordering `get_eval_run_results` renders `passed` with.

        "Nobody decided" reported as "it failed" is what turns a judge outage
        into an apparent quality collapse and an owner-initiated rollback. The
        record holds the two counts apart; nothing downstream can merge them.
        """
        result = self._summary(_record(failed=0, unmeasured=2))
        assert result["unmeasured_scenarios"] == 2
        assert result["failing_scenarios"] == 0, (
            "a scenario with an undecided gated metric is counted once, as "
            "unmeasured, and never also as a failure"
        )

    def test_the_two_datasets_add_their_counts_and_never_their_rates(self):
        """Counts add. Rates do not, and the payload still refuses to pool them."""
        result = self._summary(
            _record(
                datasets={
                    "golden": _outcome(
                        attempted=12, valid=12, scored=12, failed=1, faithfulness=0.94
                    ),
                    "exploratory": _outcome(
                        attempted=30, valid=30, scored=30, failed=2, unmeasured=3,
                        faithfulness=0.81,
                    ),
                }
            )
        )
        assert result["failing_scenarios"] == 3
        assert result["unmeasured_scenarios"] == 3
        assert result["pass_rates"] is None, (
            "two scored datasets have no run-level rate, and adding counts is "
            "not a licence to average the halves"
        )

    def test_a_record_that_counted_no_verdicts_is_refused_rather_than_read(self):
        """A stored payload with no verdict counts over 30 scored rows.

        That is a record written by a build that did not count its own verdicts,
        and reading it would mean deriving the counts here, which is the
        derivation they exist to replace. It reads as no record at all, and a run
        with no record cannot ship.
        """
        payload = _record().payload
        for name in ("scenarios_passed", "scenarios_failed", "scenarios_unmeasured"):
            payload["datasets"]["exploratory"].pop(name)
        result = self._summary(payload)

        assert result["eval_signal"] == EVAL_SIGNAL_NO_RECORD
        assert result["failing_scenarios"] is None
        assert result["unmeasured_scenarios"] is None
        assert apply_signal_evidence_gate("ship", result, _measured_red_team())[0] == (
            "block"
        ), "a verdict count nobody wrote down is not a count of zero"


# ---------------------------------------------------------------------------
# TestEvidenceGate — 'ship' is not available over an absent signal
# ---------------------------------------------------------------------------


def _measured_eval(record=None) -> dict:
    """An admissible eval signal, built through the collector's own constructor.

    Hand-writing this dict is how a gate test comes to assert against a payload
    shape production never produces, so it goes through `_eval_summary` and the
    tests below see exactly what `_fetch_eval_summary_sync` emits.

    D1/P3: `agent_invoked` is True because these tests are about the OTHER
    refusals, and the gate blocks every one of them without it. Before P2 the key
    did not exist and this fixture WAS the shape of a tautological run: measured,
    clean, 0.92 faithfulness, and no agent anywhere near it.
    """
    return _eval_summary(
        EVAL_SIGNAL_MEASURED,
        last_run_at="2026-05-23T02:00:00",
        last_run_status="complete",
        record=record if record is not None else _record(),
        agent_invoked=True,
    )


def _measured_red_team(
    coverage_complete=True,
    *,
    deployment_blocked=False,
    high_count=0,
    medium_count=0,
) -> dict:
    return {
        "signal": RED_TEAM_SIGNAL_MEASURED,
        "signal_detail": None,
        "last_run_at": "2026-05-23T03:00:00",
        "deployment_blocked": deployment_blocked,
        "critical_count": 0,
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": 0,
        "vectors_attempted": 7,
        "vectors_valid": 7 if coverage_complete else 3,
        "invalid_vectors": [] if coverage_complete else ["hallucination"],
        "coverage_complete": coverage_complete,
        "coverage_source": COVERAGE_SOURCE_RUN,
    }


class TestEvidenceGate:
    """apply_signal_evidence_gate — deterministic, one-way, fail-closed.

    The gate exists because the blocking conditions live in an LLM prompt and a
    gate that depends on a model correctly reading a state field is a gate that
    fails open the first time the model is confident and wrong. Same division of
    labour as derive_blast_radius_warnings: the orchestrator narrates, the
    platform decides.
    """

    @pytest.mark.parametrize(
        ("signal", "warning_id"),
        [
            # 'never evaluated' and 'could not be read' block identically and
            # are reported differently: the remedies are different, and telling
            # a day-1 owner their results "could not be read" describes a
            # transient outage where the truth is a permanent absence.
            (EVAL_SIGNAL_NO_RUNS, "eval_never_run"),
            (EVAL_SIGNAL_NO_VALID_SCORES, "eval_signal_unavailable"),
            (EVAL_SIGNAL_UNAVAILABLE, "eval_signal_unavailable"),
        ],
    )
    def test_ship_is_refused_over_any_absent_eval_signal(self, signal, warning_id):
        summary = _measured_eval()
        summary["eval_signal"] = signal
        summary["pass_rates"] = None

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == [warning_id]

    def test_ship_with_warnings_is_also_refused(self):
        """ship_with_warnings is a SHIPPABLE state — the approve route lets it
        through once the owner acknowledges — so routing an unmeasured agent
        there would still permit the deploy."""
        summary = _measured_eval()
        summary["eval_signal"] = EVAL_SIGNAL_UNAVAILABLE

        recommendation, _ = apply_signal_evidence_gate(
            "ship_with_warnings", summary, _measured_red_team()
        )
        assert recommendation == "block"

    def test_a_missing_state_field_fails_closed(self):
        """A summary dict built by hand without the state key must not ship.

        The absence of a claim is not the claim. This is the shape of every
        future caller that constructs a signal payload and forgets a field.
        """
        recommendation, warnings = apply_signal_evidence_gate(
            "ship", {"pass_rates": {"faithfulness": 0.99}}, {"critical_count": 0}
        )
        assert recommendation == "block"
        assert {w.warning_id for w in warnings} == {
            "eval_signal_unavailable",
            "red_team_signal_unavailable",
        }

    def test_a_measured_signal_leaves_the_recommendation_alone(self):
        """The gate is a floor, not a second opinion. With both signals
        measured, the orchestrator's verdict is untouched — including a verdict
        the gate would never itself produce."""
        for verdict in ("ship", "ship_with_warnings", "block"):
            recommendation, warnings = apply_signal_evidence_gate(
                verdict, _measured_eval(), _measured_red_team()
            )
            assert recommendation == verdict
            assert warnings == []

    def test_the_gate_never_upgrades_a_block(self):
        """One-way. A block over a missing signal stays a block, and so does a
        block the orchestrator reached on evidence."""
        summary = _measured_eval()
        summary["eval_signal"] = EVAL_SIGNAL_UNAVAILABLE
        assert apply_signal_evidence_gate("block", summary, _measured_red_team())[0] == (
            "block"
        )
        assert apply_signal_evidence_gate(
            "block", _measured_eval(), _measured_red_team()
        )[0] == "block"

    def test_an_unreadable_red_team_signal_also_refuses_to_ship(self):
        """Zeros nobody read are not zeros. The substituted red-team fallback
        carries deployment_blocked=False, which on its own reads as 'no critical
        findings' — the identical fail-open shape D3 had on the eval side."""
        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), dict(RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL)
        )
        assert recommendation == "block"
        assert any(w.warning_id == "red_team_signal_unavailable" for w in warnings)

    def test_a_run_that_recorded_incomplete_coverage_refuses_to_ship(self):
        """P4 review. This used to warn and ship, and the warning could not fire.

        `red_team_coverage_incomplete` was the only deterministic Python-side
        coverage control in the system, and it was fed by red_team_coverage(),
        which has returned complete=True for every run in every environment
        since SDK_ATTACKERS_CAN_PROBE was flipped. Now the run records its own
        coverage — and a clean result over 3 of 7 vectors is not a clean result.
        The remedy is in the owner's hands (run the check again), which is what
        makes refusing actionable rather than a dead end.
        """
        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), _measured_red_team(coverage_complete=False)
        )
        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_coverage_incomplete"]
        assert "3 of 7" in warnings[0].message

    def test_a_run_short_of_its_attempts_refuses_to_ship(self):
        """The second way a run can be incomplete (ticket 15, #52).

        Every vector observed the agent, so vectors_valid equals
        vectors_attempted and `invalid_vectors` is empty — and the run still did
        not test what it set out to, because a vector did not make all k of its
        independent attempts. `coverage_complete` is the only field that carries
        that, which is why the gate reads it rather than comparing the two
        counts, and why the warning may not name a vector shortfall as the cause.
        """
        summary = _measured_red_team()
        summary["coverage_complete"] = False

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_coverage_incomplete"]
        assert summary["vectors_valid"] == summary["vectors_attempted"] == 7
        assert "attempt" in warnings[0].message, (
            "the message said only that some attack types went untested, which "
            "is false about a run where all seven reported"
        )

    def test_unrecorded_coverage_warns_rather_than_blocking(self):
        """'current_build' means no run-level figure exists at all — a tenant DB
        provisioned before migration 0015, or a run written before the task
        stored it. Nothing the owner does produces one, so blocking would be a
        permanent unfixable refusal. It is still not evidence, and it says so."""
        summary = _measured_red_team()
        summary["coverage_source"] = COVERAGE_SOURCE_CURRENT_BUILD

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "ship"
        assert [w.warning_id for w in warnings] == ["red_team_coverage_unrecorded"]

    def test_a_null_coverage_flag_fails_the_same_way_as_false(self):
        """`is not True`, not `is False`: a summary that carries no coverage
        claim has not made one."""
        summary = _measured_red_team()
        summary["coverage_complete"] = None

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_coverage_incomplete"]

    def test_an_open_critical_finding_refuses_to_ship(self):
        """`red_team_summary.deployment_blocked == True` was the first blocking
        condition in the orchestrator prompt and was enforced in no Python at
        all — the gate returned 'ship' over an open critical finding."""
        summary = _measured_red_team()
        summary["critical_count"] = 1
        summary["deployment_blocked"] = True

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_critical_finding"]

    def test_open_high_findings_refuse_to_ship_while_the_flag_is_set(self):
        """DEP_BLOCK_ON_HIGH_RED_TEAM occurred exactly twice in the codebase:
        its definition in config.py and one sentence of a system prompt. The
        four `high` INVALID findings a transport-less run produces went straight
        past it."""
        summary = _measured_red_team()
        summary["high_count"] = 4

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_high_finding"]
        assert "4 serious" in warnings[0].message

    def test_the_high_block_honours_its_flag(self):
        """The flag is a real switch, not decoration — otherwise this gate would
        be enforcing something the config says is optional."""
        summary = _measured_red_team()
        summary["high_count"] = 4

        with patch.object(settings, "DEP_BLOCK_ON_HIGH_RED_TEAM", False):
            recommendation, warnings = apply_signal_evidence_gate(
                "ship", _measured_eval(), summary
            )

        assert recommendation == "ship"
        assert warnings == []

    def test_containing_the_findings_does_not_clear_the_coverage_refusal(self):
        """The scenario the P4 review reconstructed end to end.

        Four `high` findings whose console fields name no vulnerability lead the
        owner to contain them (PATCH /red-team/findings/{id}, open -> contained).
        The counts then read 0/0 — but containment does not make the four
        vectors run, and the run's own coverage row still says they did not.
        """
        summary = _measured_red_team(coverage_complete=False)
        summary["critical_count"] = 0
        summary["high_count"] = 0

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_coverage_incomplete"]

    def test_the_unavailable_substitute_cannot_be_mistaken_for_a_clean_run(self):
        """The module constant itself, not a hand-built dict: this is the value
        the Celery task substitutes when the collector raises."""
        assert EVAL_SUMMARY_UNAVAILABLE_SIGNAL["pass_rates"] is None
        assert EVAL_SUMMARY_UNAVAILABLE_SIGNAL["failing_scenarios"] is None
        assert (
            apply_signal_evidence_gate(
                "ship", dict(EVAL_SUMMARY_UNAVAILABLE_SIGNAL), _measured_red_team()
            )[0]
            == "block"
        )

    def test_the_prompt_states_the_evidence_rule_it_no_longer_enforces(self):
        """The prompt still has to SAY it, or the model's summary contradicts
        the recommendation the platform imposed."""
        assert "eval_signal" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "'measured'" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "scored_scenario_count" in _DEPLOYMENT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# TestAgentInvokedGate (audit D1, P3) — the gate learns to refuse a tautology
# ---------------------------------------------------------------------------


class TestAgentInvokedGate:
    """`agent_invoked is not True` refuses the deploy. BACKLOG 2.2.

    The defect these close: `eval.py:374-375` set

        "agent_response": row[3],   # row[3] IS reference_answer

    so Ragas scored each scenario's own reference answer against the contexts
    that answer was written from. Faithfulness and AnswerRelevancy approached
    1.0 by construction, the agent was never invoked, and this gate read the
    result as a measured signal with excellent pass rates and shipped on it.
    The signal was PRESENT and it measured nothing — which is why the four
    pre-existing absent-signal states could not catch it.

    THE THREE CASES ARE FALSE, ABSENT AND TRUE, and absent is the one that
    decides whether this is worth anything. Every eval run persisted before
    this branch was produced by the tautology and carries no such key, so a
    gate refusing only an explicit `false` would keep shipping on the whole of
    history — the exact shape of BACKLOG 3.1, where pre-P4 red-team runs still
    read signal='measured' with clean findings because nobody had recorded the
    absence. Settled by the owner 2026-08-07; the accepted consequence is that
    every pre-D1 run fails closed until a fresh eval runs.
    """

    def test_the_gate_refuses_a_run_that_recorded_no_invocation(self):
        """`agent_invoked: False` — a run that looked and said no.

        P2 inserts every eval_runs row with this value and patches the observed
        one in afterwards, so False is also what a run that died between its
        row and its first turn leaves behind, and what a run below
        MIN_RESPONSE_RATE records on purpose.
        """
        summary = _measured_eval()
        summary["agent_invoked"] = False

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )

        assert recommendation == "block", (
            "the gate shipped on an eval run that recorded it never invoked the "
            "agent — the scores are about the dataset, not about the agent"
        )
        assert [w.warning_id for w in warnings] == ["eval_agent_not_invoked"]

    def test_the_gate_refuses_a_run_that_records_nothing_about_invocation(self):
        """THE ONE THAT MATTERS: the key is absent, which is all of history.

        Not a hypothetical. Every eval_runs row written before this branch has
        a config with no `agent_invoked` in it — 0013 added the column, and
        nothing wrote this key into it until P2. A gate that treated absence as
        assent would refuse nothing that exists.
        """
        summary = _measured_eval()
        summary.pop("agent_invoked")
        assert "agent_invoked" not in summary

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )

        assert recommendation == "block", (
            "the gate shipped on a 'measured' signal that makes no claim to "
            "have invoked the agent — i.e. on every run stored before D1"
        )
        assert [w.warning_id for w in warnings] == ["eval_agent_not_invoked"]

    def test_the_gate_accepts_a_run_that_recorded_the_agent_was_invoked(self):
        """The refusal has to be able to NOT fire, or it is a permanent block
        wearing a gate's name.

        `agent_invoked: True` is written by eval_service.invocation_provenance
        only when the run both invoked the agent AND enough scenarios answered
        to constitute a measurement — the conjunction lives on the writing
        side, and the gate must not try to second-guess either half.
        """
        summary = _measured_eval()
        assert summary["agent_invoked"] is True

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )

        assert recommendation == "ship", (
            "a run that invoked the agent and measured it cannot ship — the "
            "gate is refusing everything, which is not a gate"
        )
        assert warnings == []

    def test_a_non_boolean_claim_is_not_a_claim(self):
        """`bool("false")` is True. A config patched by hand or by an external
        tool is the plausible route in, and coercing would turn the string
        "false" into a shipping signal."""
        for value in ("true", "false", 1, [], {"invoked": True}):
            summary = _measured_eval()
            summary["agent_invoked"] = value

            recommendation, warnings = apply_signal_evidence_gate(
                "ship", summary, _measured_red_team()
            )

            assert recommendation == "block", (
                f"agent_invoked={value!r} was accepted as an invocation claim"
            )
            assert [w.warning_id for w in warnings] == ["eval_agent_not_invoked"]

    def test_the_refusal_survives_a_block_and_never_upgrades_one(self):
        """One-way, like every other arm of this gate."""
        summary = _measured_eval()
        summary["agent_invoked"] = False
        assert (
            apply_signal_evidence_gate("block", summary, _measured_red_team())[0]
            == "block"
        )
        assert (
            apply_signal_evidence_gate(
                "ship_with_warnings", summary, _measured_red_team()
            )[0]
            == "block"
        ), (
            "ship_with_warnings is a SHIPPABLE state — the approve route lets it "
            "through once the owner acknowledges — so it is not a safe landing "
            "place for a run that measured nothing"
        )

    def test_the_collector_state_and_the_gate_arm_reach_the_same_warning(self):
        """Two routes into the refusal, one remedy.

        In production the collector has already turned this run into
        EVAL_SIGNAL_AGENT_NOT_INVOKED; the bare-`measured` arm exists for a
        payload assembled somewhere else. A reader of the warnings must not be
        able to tell which fired, because the owner's next action is identical.

        THIS USED TO BE TRUE BY CONSTRUCTION (P3 review) and is now an
        observation. `_agent_not_invoked_warning` ignored its argument and
        returned a literal, so any two call sites of it produced identical
        payloads for every possible pair of inputs — including inputs that
        SHOULD differ. The message now branches on `agent_invoked` and
        `eval_dispatched`, so this assertion fails if either route stops
        handing the payload through, which is the property the name claims.
        """
        by_state = _measured_eval()
        by_state["eval_signal"] = EVAL_SIGNAL_AGENT_NOT_INVOKED
        by_state["pass_rates"] = None
        by_state["agent_invoked"] = False

        by_field = _measured_eval()
        by_field["agent_invoked"] = False

        _, state_warnings = apply_signal_evidence_gate(
            "ship", by_state, _measured_red_team()
        )
        _, field_warnings = apply_signal_evidence_gate(
            "ship", by_field, _measured_red_team()
        )

        assert [w.warning_id for w in state_warnings] == ["eval_agent_not_invoked"]
        assert [w.model_dump() for w in state_warnings] == [
            w.model_dump() for w in field_warnings
        ]

    def test_the_warning_does_not_promise_the_new_numbers_will_look_better(self):
        """The scores WILL fall — from ~1.0 by construction to whatever is true
        — and an owner who reads the drop as a regression will be wrong. The
        message says so, because nothing else in the product will.

        Driven from the ABSENT payload since the P3 review. It used to be
        driven from `agent_invoked=False`, where there are no old numbers to
        fall from: the below-floor run writes no eval_results at all. The
        sentence belongs to the historical population, which is the one that
        has a 0.99 on the record.
        """
        summary = _measured_eval()
        summary.pop("agent_invoked")
        _, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )
        message = warnings[0].message.lower()
        assert "lower" in message, (
            "the owner is about to watch faithfulness fall from 0.99 to "
            "something real and be told nothing about why"
        )
        assert "eval" not in message.replace("evaluation", ""), (
            "'eval' is jargon for the non-technical owner this message is for"
        )

    def test_the_false_case_is_not_narrated_as_the_tautology(self):
        """THE MESSAGE MAY NOT NARRATE A CAUSE IT DID NOT OBSERVE (P3 review).

        One warning_id is not one sentence. A below-floor P2 run DID invoke the
        agent, scored nothing at all (run_eval_suite skips the scorer entirely
        below the floor, so zero eval_results rows exist), and involved no
        pre-written answers anywhere — yet every owner in that state was told
        their check "scored a set of pre-written model answers" and that the
        new numbers would be "lower than the old ones", of which there are
        none. Four false claims in one sentence, in the phase whose subject is
        exactly that failure, and the console renders nothing else: a grep of
        apps/admin for `agent_invoked` returns nothing, so this IS the
        owner-visible account.
        """
        summary = _measured_eval()
        summary["agent_invoked"] = False
        _, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )
        message = warnings[0].message.lower()

        assert "pre-written" not in message, (
            "a run that scored nothing was described as having scored "
            "pre-written answers"
        )
        assert "lower" not in message, (
            "there are no old numbers for the new ones to be lower than — the "
            "below-floor run wrote no eval_results at all"
        )
        assert "replies" in message, (
            "the message must still say what was missing: the agent's own "
            "replies"
        )
        assert "eval" not in message.replace("evaluation", "")

    def test_the_absent_case_does_not_assert_the_tautology_as_fact(self):
        """Absence is not only the pre-D1 tautology. It is also a pre-0013
        tenant DB with no `config` column, and a P2 run whose config patch
        failed. The message may name the historical cause and must not claim
        it happened here."""
        summary = _measured_eval()
        summary.pop("agent_invoked")
        _, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )
        message = warnings[0].message.lower()

        assert "does not record" in message, (
            "the observed fact is that the run recorded nothing; that is what "
            "the first sentence has to say"
        )
        assert "if this was one of those" in message, (
            "the tautology is offered as an explanation for the coming drop, "
            "not asserted as this run's history"
        )

    def test_a_dispatched_rerun_tells_the_owner_to_wait(self):
        """Same wait-vs-find-a-page split the eval_never_run warning makes. The
        checklist starts a fresh run for the historical population (task step
        4b), and a message that then names a page the onboarding flow never
        routes to is the wall that step exists to remove."""
        summary = _measured_eval()
        summary.pop("agent_invoked")
        summary["eval_dispatched"] = True
        _, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )

        assert "started" in warnings[0].message.lower()
        assert "Evaluation page" not in warnings[0].message

    def test_the_unavailable_substitute_carries_the_field(self):
        """Key-for-key parity with the collector's payload.

        The substitute is a hand-written literal rather than a call to
        _eval_summary, so a field added to one and not the other drifts
        silently — and the field that drifts is the one the gate reads.
        """
        assert "agent_invoked" in EVAL_SUMMARY_UNAVAILABLE_SIGNAL
        assert EVAL_SUMMARY_UNAVAILABLE_SIGNAL["agent_invoked"] is None, (
            "None, not False: the collector raised, so no run was asked"
        )

    def test_the_prompt_states_the_condition_the_platform_enforces(self):
        """DRIFT PROTECTION OVER A STRING, AND NOT A CONTROL (P3 review
        downgrades what this test is cited for).

        It asserts two substrings are present in a module-level constant.
        TestRunOrchestrator drives the real loop since #49, but it drives it
        against a scripted client, so no test anywhere observes the MODEL obeying
        any prose blocking condition, and this one cannot support a claim that
        the narration is prevented from contradicting the verdict.
        deployment_service.py's own comment makes the argument: a gate that
        depends on an LLM correctly reading a state field fails open the first
        time the model is confident and wrong.

        What actually constrains the narration is the SUPPRESSION — _eval_summary
        puts no pass_rates on the payload outside EVAL_SIGNAL_MEASURED, so the
        model cannot narrate a number it was never given. Keep this pin, cheap
        as it is, and read it as consistency.
        """
        assert "agent_invoked" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "agent_not_invoked" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "run_failed" in _DEPLOYMENT_SYSTEM_PROMPT


class TestAgentInvokedCollector:
    """_fetch_eval_summary_sync derives the state from `eval_runs.config`.

    THIS IS THE ENFORCEMENT (P3 review corrects the original claim, which said
    the collector and the gate arm were two live layers). Neuter the gate arm
    alone and every test in this class stays green, because the collector is
    the only producer of a 'measured' payload in the tree and it has already
    downgraded the run. The arm guards a payload shape that does not exist yet
    — a hand-built summary, a second collector added later — which is worth
    keeping and is not a second layer under today's code.
    """

    def _conn(self, config, **kw):
        return _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", config),
            record=kw.get(
                "record",
                _record(
                    datasets={
                        "exploratory": _outcome(
                            attempted=30, valid=30, scored=30, faithfulness=0.99
                        )
                    }
                ),
            ),
        )

    def _collect(self, mock_conn):
        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            return _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

    def test_a_run_that_invoked_the_agent_is_measured(self):
        result = self._collect(self._conn(_invoked_config()))

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        assert result["agent_invoked"] is True
        assert result["pass_rates"] == {"faithfulness": pytest.approx(0.99)}

    def test_the_historical_tautology_shape_is_refused(self):
        """A config with a dataset composition and no invocation claim, and a
        near-perfect score over thirty scenarios. This is what every eval run
        on the platform looks like today, and it used to be indistinguishable
        from a measurement."""
        result = self._collect(self._conn({"dataset": {"attempted": 30, "valid": 30}}))

        assert result["eval_signal"] == EVAL_SIGNAL_AGENT_NOT_INVOKED
        assert result["agent_invoked"] is None
        assert apply_signal_evidence_gate("ship", result, _measured_red_team())[0] == (
            "block"
        )

    def test_a_recorded_false_is_refused_and_says_which_it_was(self):
        result = self._collect(self._conn({"agent_invoked": False}))

        assert result["eval_signal"] == EVAL_SIGNAL_AGENT_NOT_INVOKED
        assert result["agent_invoked"] is False
        assert "was not invoked" in result["signal_detail"]

    def test_absence_and_falsehood_are_distinguishable_on_the_payload(self):
        """They block identically and they are not the same event. 'The run
        said no' and 'no run said anything' have the same remedy and different
        diagnoses, and the diagnosis is what a trace is read for.

        THE REFUSAL IS ASSERTED HERE TOO, since the P3 review (BACKLOG 3.3's
        pattern). As first written this test survived the collector mutation it
        appears to guard: under `if agent_invoked is False`, the absent case
        fell through to the EVAL_SIGNAL_MEASURED return, which still passes
        agent_invoked=None onto the payload — so all three assertions held
        while absence had quietly become shippable, and a test named
        'distinguishable' stayed green through absence becoming
        indistinguishable at the gate.
        """
        said_no = self._collect(self._conn({"agent_invoked": False}))
        said_nothing = self._collect(self._conn({}))

        assert said_no["agent_invoked"] is False
        assert said_nothing["agent_invoked"] is None
        assert said_no["signal_detail"] != said_nothing["signal_detail"]

        for payload in (said_no, said_nothing):
            assert payload["eval_signal"] == EVAL_SIGNAL_AGENT_NOT_INVOKED
            assert (
                apply_signal_evidence_gate("ship", payload, _measured_red_team())[0]
                == "block"
            )

    def test_the_scores_of_a_tautology_do_not_travel(self):
        """The refusal suppresses pass_rates for the same reason the other four
        states do — and this is the only state where suppression discards a
        number that EXISTS.

        Letting 0.99 through while the recommendation blocks would put "answer
        quality is excellent" in the orchestrator's owner-facing summary above
        a refusal, which is BACKLOG 5.4 one layer down: the gate closes and the
        prose stays open.
        """
        result = self._collect(self._conn({}))

        assert result["pass_rates"] is None, (
            "a tautology's scores reached the orchestrator, which will narrate "
            "them"
        )
        assert result["failing_scenarios"] is None

    def test_the_denominators_still_travel_on_the_refusal(self):
        """A blocked run the owner cannot see the size of is a dead end. The
        counts are how anyone works out what happened."""
        result = self._collect(
            self._conn(
                {"dataset": {"attempted": 40, "valid": 38}},
                record=_record(
                    datasets={
                        "exploratory": _outcome(
                            attempted=40, valid=38, scored=5, faithfulness=0.99
                        )
                    },
                    attempted=40,
                    valid=38,
                    scored=5,
                ),
            )
        )

        assert result["eval_signal"] == EVAL_SIGNAL_AGENT_NOT_INVOKED
        assert result["scenario_count"] == 40
        assert result["valid_scenario_count"] == 38
        assert result["scored_scenario_count"] == 5
        assert result["denominator_source"] == DENOMINATOR_SOURCE_EVAL_RECORD
        assert result["last_run_status"] == "complete"
        assert result["datasets"]["exploratory"]["scenario_count"] == 40, (
            "the per-dataset counts travel on a refusal too; only the numbers "
            "the orchestrator would narrate are withheld"
        )

    def test_a_pre_0013_tenant_has_no_config_column_and_so_fails_closed(self):
        """The sharpest edge of the settled decision, stated where it bites.

        Tenant DBs are migrated at PROVISION time only, so a tenant older than
        alembic_tenant 0013 has no `config` column at all: the wide SELECT
        raises UndefinedColumn, the narrow fallback answers, and no invocation
        claim can exist for any run on that database. Such a tenant cannot
        deploy until its DB is re-migrated AND a fresh eval runs. That is the
        accepted cost of refusing an absent claim.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete"),
            raise_on="status, config",
        )
        result = self._collect(mock_conn)

        assert result["eval_signal"] == EVAL_SIGNAL_AGENT_NOT_INVOKED
        assert result["agent_invoked"] is None
        assert apply_signal_evidence_gate("ship", result, _measured_red_team())[0] == (
            "block"
        )

    def test_the_root_cause_is_reported_ahead_of_the_symptom(self):
        """A below-floor P2 run is in two absent states at once: it records
        agent_invoked=false AND (because run_eval_suite skips the scorer) it
        has written no eval_results. 'no_valid_scores' would send the owner
        after a judge that was never the problem."""
        result = self._collect(self._conn({"agent_invoked": False}, record=None))

        assert result["eval_signal"] == EVAL_SIGNAL_AGENT_NOT_INVOKED, (
            "reported as a judge failure when the agent was the thing that "
            "did not run"
        )

    def test_an_invoking_run_that_scored_nothing_is_still_a_judge_failure(self):
        """The converse, so the ordering above is not just always-D1."""
        result = self._collect(
            self._conn(
                _invoked_config(),
                record=_record(
                    datasets={"exploratory": _outcome(attempted=30, valid=30, scored=0)},
                    scored=0,
                ),
            )
        )

        assert result["eval_signal"] == EVAL_SIGNAL_NO_VALID_SCORES
        assert result["agent_invoked"] is True

    def test_a_recordless_run_is_reported_after_the_invocation_claim(self):
        """Ordering, stated where it bites. A pre-D1 run predates migration 0022
        too, so it is in both absent states at once, and the invocation claim is
        the one that names what is wrong: its scores are about the dataset's own
        reference answers, and a fresh eval is what fixes it. Reporting the
        missing record instead would send the owner after the writer."""
        result = self._collect(self._conn({}, record=None))

        assert result["eval_signal"] == EVAL_SIGNAL_AGENT_NOT_INVOKED
        assert result["result"] == "absent"


class TestNarrowRowWidth:
    """The double must not hand back a row the SQL did not select (P3 review).

    `_make_eval_conn` padded a three-tuple to four elements before the double
    was built, so the pre-0013 test — whose whole subject is that the WIDE
    select raises and the NARROW three-column one answers — got a four-element
    row back from `SELECT id, finished_at, status`. No database can do that.
    It changed no outcome, because the collector indexes only [0..2] on that
    path, and that is precisely the problem: a future read of run_row[3] on the
    fallback would be green here and IndexError in production.
    """

    def _rows_seen(self, raise_on):
        seen = []
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete"),
            record=_record(),
            raise_on=raise_on,
        )
        real_cursor = mock_conn.cursor.return_value
        real_fetchone = real_cursor.fetchone.side_effect

        def _spy():
            row = real_fetchone()
            seen.append(row)
            return row

        real_cursor.fetchone.side_effect = _spy
        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")
        return seen

    def test_the_pre_0013_fallback_receives_exactly_three_columns(self):
        """Both wider SELECTs name `config`, so both raise and only the narrow
        `SELECT id, finished_at, status` answers."""
        seen = self._rows_seen(raise_on="status, config")

        assert len(seen[-1]) == 3, (
            f"the narrow 'SELECT id, finished_at, status' was answered with "
            f"{len(seen[-1])} columns: {seen[-1]!r}"
        )

    def test_the_pre_0022_fallback_receives_exactly_four(self):
        """A tenant at 0013 through 0021 has `config` and no `result`. It keeps
        its invocation claim, which is what stops the whole population failing
        closed for a reason it cannot fix."""
        seen = self._rows_seen(raise_on="config, result")

        assert len(seen[-1]) == 4

    def test_the_wide_select_still_receives_five(self):
        seen = self._rows_seen(raise_on=None)

        assert len(seen[0]) == 5


class TestFailedRunIsNotEvidence:
    """A run whose own terminal status is not 'complete' cannot ship (P3
    review). `last_run_status` has travelled on this payload since P1 and
    nothing anywhere gated on it.

    THE REACHABLE SHAPE IS ORDINARY, NOT EXOTIC, AND P2 IS WHAT MADE IT SO.
    run_eval_suite patches the invocation claim into eval_runs.config BEFORE
    scoring (eval.py:1082-1083, deliberately — the invocation is the expensive,
    unrepeatable half), scores, writes eval_results, and marks the run
    'complete' at :1146. `summarise_run_validity` then runs at :1155, one line
    AFTER that write, and anything raising from there to the end of the body
    lands in the except at :1222, whose `_mark_failed_on_production`
    unconditionally writes status='failed' over the row.

    The result is a run carrying agent_invoked=true, a full set of high
    pass_rates, and status='failed' — which reached the collector as
    EVAL_SIGNAL_MEASURED and shipped. The one pre-existing failed-run test
    could not catch it: it passes no metric rows, so it landed in
    no_valid_scores before the question was ever asked.
    """

    def _collect(self, status, config=None, record=None):
        mock_conn = _make_eval_conn(
            (
                uuid.uuid4(),
                datetime(2026, 5, 23, 2, 0, 0),
                status,
                config if config is not None else _invoked_config(),
            ),
            record=(
                record
                if record is not None
                else _record(
                    datasets={
                        "exploratory": _outcome(
                            attempted=30, valid=30, scored=30, faithfulness=0.90
                        )
                    }
                )
            ),
        )
        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            return _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

    def test_a_failed_run_with_a_full_set_of_scores_does_not_ship(self):
        """The exact shape: status='failed', agent_invoked=true, real scores.

        Metric rows AND a failed status, which is what the pre-existing test
        could not express. Without the admissibility check this returns
        EVAL_SIGNAL_MEASURED and apply_signal_evidence_gate answers 'ship'.
        """
        result = self._collect("failed")

        assert result["eval_signal"] == EVAL_SIGNAL_RUN_FAILED, (
            "a run recorded as FAILED reported itself as a measurement because "
            "some of its scores survived"
        )
        assert result["last_run_status"] == "failed"
        assert apply_signal_evidence_gate("ship", result, _measured_red_team())[0] == (
            "block"
        )

    def test_the_failed_run_s_scores_do_not_travel(self):
        """Same suppression as every other absent state. A 0.90 beside a
        refusal is what the orchestrator narrates.

        THERE IS ONE LAYER NOW AND IT IS `_readings` (#51 slice 4). The refusal
        used to omit the `pass_rates=` argument AND _eval_summary nulled the
        rates outside EVAL_SIGNAL_MEASURED, so either layer alone kept this test
        green and only removing both turned it red. Recorded as one mutation in
        `.dev/reference/p3-review-mutation-proofs.md` rather than dressed up as
        two defences. The record now travels on every state, because the COUNTS
        have to, so suppression is a single decision taken in one place against
        the signal.
        """
        result = self._collect("failed")

        assert result["pass_rates"] is None
        assert result["failing_scenarios"] is None

    def test_the_denominators_still_travel(self):
        """A blocked run whose size the owner cannot see is a dead end."""
        result = self._collect(
            "failed",
            record=_record(
                datasets={
                    "exploratory": _outcome(
                        attempted=40, valid=38, scored=30, faithfulness=0.90
                    )
                },
                attempted=40,
                valid=38,
                scored=30,
            ),
        )

        assert result["scenario_count"] == 40
        assert result["valid_scenario_count"] == 38
        assert result["scored_scenario_count"] == 30
        assert result["denominator_source"] == DENOMINATOR_SOURCE_EVAL_RECORD

    def test_an_unrecognised_terminal_status_also_fails_closed(self):
        """An allow-list of one, not a deny-list containing 'failed'. The
        selector already argues that a status this code has not heard of is
        still terminal; the same unknown must not be read as a completion."""
        result = self._collect("cancelled")

        assert result["eval_signal"] == EVAL_SIGNAL_RUN_FAILED
        assert result["last_run_status"] == "cancelled"

    def test_a_complete_run_is_unaffected(self):
        """The refusal has to be able to NOT fire, or it is a permanent block
        wearing a gate's name."""
        result = self._collect("complete")

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        assert result["pass_rates"] == {"faithfulness": pytest.approx(0.90)}

    def test_the_status_is_asked_before_the_invocation_claim(self):
        """Ordering, stated where it bites. A pre-D1 run that also failed is in
        two absent states at once; the coarser question — did this run reach
        the end of its own body — is answered first, because a run that did not
        has no reliable account of ANY of its claims, the invocation one
        included."""
        result = self._collect("failed", config={})

        assert result["eval_signal"] == EVAL_SIGNAL_RUN_FAILED
        assert result["agent_invoked"] is None, (
            "the claim still travels on the payload for whoever reads the trace"
        )


class TestStoredRunEvidence:
    """POST /approve-deployment reads a FROZEN recommendation, so the gate does
    not reach a checklist run that already completed (P3 review).

    apply_signal_evidence_gate has exactly one caller — run_deployment_checklist
    — and `agent.is_deployed` has exactly one writer: the approve route, which
    validates status, recommendation, warning acknowledgement and envelope
    drift, none of which moves when the gate's rules change. Every readiness
    check completed before this release therefore carries a 'ship' computed by
    the pre-P3 gate over a tautological eval and stays approvable indefinitely:
    checklist_runs has no TTL and no gate-version column.

    That is BACKLOG 3.1's shape applied to the artifact the approve decision is
    actually taken from — and 3.1 is the argument P3's own commit message used
    to justify refusing an absent claim.
    """

    def test_a_report_that_records_the_invocation_is_admissible(self):
        assert stored_run_records_agent_invocation(
            {"eval_summary": {"agent_invoked": True}}
        )

    def test_the_historical_report_shape_is_refused(self):
        """No `agent_invoked` key at all: every checklist run written before
        this branch, because nothing produced the key until P2."""
        assert not stored_run_records_agent_invocation(
            {
                "eval_summary": {
                    "eval_signal": "measured",
                    "pass_rates": {"faithfulness": 0.99},
                    "scenario_count": 30,
                },
                "recommendation": "ship",
            }
        )

    def test_a_recorded_false_is_refused(self):
        assert not stored_run_records_agent_invocation(
            {"eval_summary": {"agent_invoked": False}}
        )

    def test_a_non_boolean_claim_is_refused(self):
        """`bool("false")` is True, and a JSONB payload is exactly where a
        string arrives from."""
        for value in ("true", "false", 1, 0, [], {}, None):
            assert not stored_run_records_agent_invocation(
                {"eval_summary": {"agent_invoked": value}}
            ), f"agent_invoked={value!r} was accepted as an invocation claim"

    def test_an_unreadable_report_is_refused(self):
        """A run that never reached step 6 has report NULL; a payload of some
        other shape is a caller this function has not met. A gate that cannot
        read its evidence has not been satisfied."""
        for report in (None, {}, {"eval_summary": None}, {"eval_summary": []}, "ship", 3):
            assert not stored_run_records_agent_invocation(report), (
                f"an unreadable report shape was treated as evidence: {report!r}"
            )


# ---------------------------------------------------------------------------
# TestBlastRadiusCollector
# ---------------------------------------------------------------------------


class TestBlastRadiusCollector:
    """Tests for _fetch_blast_radius_sync's honest-empty behaviour, its
    unbounded-configuration handling, its per-skill hourly derivation, its
    tenant-vs-platform threshold resolution, and its no-conn_str signature.
    """

    def test_no_qualifying_audit_rows_yields_none_not_zero(self):
        """T-18-BLR-01: NULL observed queries yield None, never 0 (OD-1)."""
        mock_db = _make_scripted_db(
            [
                {"scalar": None},        # configured_max_row: no enabled rows
                {"scalar": 0},           # unbounded_single_count
                {"fetchall": []},        # enabled_rows: none
                {"scalar": None},        # observed_single_row: no qualifying rows
                {"scalar": None},        # observed_hourly_row: no qualifying rows
                {"first": (None, None)},
            ]
        )
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _fetch_blast_radius_sync("test-agent")

        assert result["observed_max_single_action_cents"] is None
        assert result["observed_max_hourly_aggregate_cents"] is None
        assert result["observed_max_single_action_cents"] != 0
        assert result["observed_max_hourly_aggregate_cents"] != 0

    def test_unbounded_enabled_skill_forces_configured_none(self):
        """T-18-BLR-01: one unbounded enabled row makes the whole configured
        ceiling honestly None, even when other enabled rows are bounded
        (a partially-bounded configuration is not a ceiling)."""
        mock_db = _make_scripted_db(
            [
                {"scalar": 5000},   # configured_max_row: max of the bounded rows
                {"scalar": 1},      # unbounded_single_count: one enabled row has no max
                {"fetchall": [("1/hour", "5000"), ("2/hour", None)]},
                {"scalar": None},
                {"scalar": None},
                {"first": (None, None)},
            ]
        )
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _fetch_blast_radius_sync("test-agent")

        assert result["configured_max_single_action_cents"] is None

    def test_configured_hourly_aggregate_sums_per_skill_ceiling_times_rate(self):
        """5000 cents at 2/hour + 10000 cents at 5/hour = 10000 + 50000 = 60000."""
        mock_db = _make_scripted_db(
            [
                {"scalar": 10000},
                {"scalar": 0},
                {"fetchall": [("2/hour", "5000"), ("5/hour", "10000")]},
                {"scalar": None},
                {"scalar": None},
                {"first": (None, None)},
            ]
        )
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _fetch_blast_radius_sync("test-agent")

        assert result["configured_max_hourly_aggregate_cents"] == 60000

    def test_configured_hourly_none_when_any_rate_limit_null(self):
        """A NULL rate_limit on any enabled skill forces the hourly ceiling to None."""
        mock_db = _make_scripted_db(
            [
                {"scalar": 10000},
                {"scalar": 0},
                {"fetchall": [("2/hour", "5000"), (None, "10000")]},
                {"scalar": None},
                {"scalar": None},
                {"first": (None, None)},
            ]
        )
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _fetch_blast_radius_sync("test-agent")

        assert result["configured_max_hourly_aggregate_cents"] is None

    def test_threshold_resolution_prefers_tenant_column_over_platform_default(self):
        mock_db = _make_scripted_db([{"first": (12345, 67890)}])
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _resolve_blast_radius_thresholds("test-agent")

        assert result == (12345, 67890)

    def test_threshold_resolution_falls_back_to_settings_when_null(self):
        mock_db = _make_scripted_db([{"first": (None, None)}])
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _resolve_blast_radius_thresholds("test-agent")

        assert result == (
            settings.BLAST_RADIUS_WARN_SINGLE_CENTS,
            settings.BLAST_RADIUS_WARN_HOURLY_CENTS,
        )

    def test_collector_takes_no_conn_str(self):
        """A future refactor must not quietly reintroduce a connection string
        into this control-DB-only collector (CLAUDE.md rule 4)."""
        assert list(inspect.signature(_fetch_blast_radius_sync).parameters) == ["agent_id"]


# ---------------------------------------------------------------------------
# TestBlastRadiusWarnings
# ---------------------------------------------------------------------------


class TestBlastRadiusWarnings:
    """Tests for derive_blast_radius_warnings — pure, no DB, no LLM (OD-1b)."""

    def test_zero_enabled_skills_returns_empty_list(self):
        """An agent with no enabled transactional skill has no blast radius to warn about."""
        assert derive_blast_radius_warnings({"enabled_skill_count": 0}) == []

    def test_no_ceiling_configured_warning(self):
        blast_radius = {
            "enabled_skill_count": 3,
            "configured_max_single_action_cents": None,
            "configured_max_hourly_aggregate_cents": None,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        result = derive_blast_radius_warnings(blast_radius)
        assert len(result) == 1
        assert result[0].warning_id == "blast_radius_no_ceiling_configured"

    def test_single_action_above_threshold_warning(self):
        blast_radius = {
            "enabled_skill_count": 1,
            "configured_max_single_action_cents": 60000,
            "configured_max_hourly_aggregate_cents": 100000,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        result = derive_blast_radius_warnings(blast_radius)
        warning = next(
            w for w in result if w.warning_id == "blast_radius_single_action_above_threshold"
        )
        assert "600.00" in warning.message

    def test_hourly_aggregate_above_threshold_warning(self):
        blast_radius = {
            "enabled_skill_count": 1,
            "configured_max_single_action_cents": 10000,
            "configured_max_hourly_aggregate_cents": 250000,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        result = derive_blast_radius_warnings(blast_radius)
        ids = {w.warning_id for w in result}
        assert "blast_radius_hourly_aggregate_above_threshold" in ids

    def test_both_above_threshold_warnings_fire_together(self):
        blast_radius = {
            "enabled_skill_count": 2,
            "configured_max_single_action_cents": 60000,
            "configured_max_hourly_aggregate_cents": 250000,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        result = derive_blast_radius_warnings(blast_radius)
        ids = {w.warning_id for w in result}
        assert ids == {
            "blast_radius_single_action_above_threshold",
            "blast_radius_hourly_aggregate_above_threshold",
        }

    def test_at_threshold_boundary_emits_no_warning(self):
        """Strictly-exceeds semantics: equal-to-threshold is not a warning."""
        blast_radius = {
            "enabled_skill_count": 1,
            "configured_max_single_action_cents": 50000,
            "configured_max_hourly_aggregate_cents": 200000,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        assert derive_blast_radius_warnings(blast_radius) == []

    def test_high_observed_maximum_with_within_threshold_configured_ceiling_emits_no_warning(self):
        """History never drives a warning — only the configured ceiling does."""
        blast_radius = {
            "enabled_skill_count": 1,
            "configured_max_single_action_cents": 1000,
            "configured_max_hourly_aggregate_cents": 1000,
            "observed_max_single_action_cents": 999999999,
            "observed_max_hourly_aggregate_cents": 999999999,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        assert derive_blast_radius_warnings(blast_radius) == []

    def test_no_warning_derived_from_observed_figures(self):
        """T-18-BLR-01: the derivation source must never reference an
        observed_max_ key — no warning is derived from history."""
        source = inspect.getsource(derive_blast_radius_warnings)
        assert "observed_max_" not in source


# ---------------------------------------------------------------------------
# TestRedTeamSummarySignal (P2) — the security half carries its own state
# ---------------------------------------------------------------------------


class TestRedTeamSummarySignal:
    """The collector's payload must say it was READ, and how much it covers."""

    def _fetch(
        self,
        run_row=(datetime(2026, 5, 23, 3, 0, 0), "complete", None),
        raise_on=None,
    ):
        """psycopg2 double for _fetch_red_team_summary_sync.

        `run_row` is (started_at, status, coverage) since migration 0015 — the
        run's own terminal status and its own record of how much of the attack
        surface it covered. None for coverage is a run written before 0015.
        """
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)

        def _execute(sql, params=None):
            if raise_on is not None and raise_on in sql:
                raise psycopg2.errors.UndefinedColumn(f"no column: {raise_on}")

        cursor.execute.side_effect = _execute
        # The pre-0015 fallback selects started_at alone, so the double answers
        # with the narrower row once the wide SELECT has raised.
        cursor.fetchone.side_effect = lambda: (
            None
            if run_row is None
            else (run_row[:2] if raise_on == "coverage" else run_row)
        )
        cursor.fetchall.return_value = [("medium", 2)]
        conn.cursor.return_value = cursor

        with patch(
            "app.services.deployment_service.psycopg2.connect", return_value=conn
        ):
            return _fetch_red_team_summary_sync("test-agent", "postgresql://test/t")

    def test_a_read_signal_is_marked_measured(self):
        result = self._fetch()
        assert result["signal"] == RED_TEAM_SIGNAL_MEASURED
        assert result["medium_count"] == 2
        assert result["deployment_blocked"] is False

    def test_the_coverage_denominator_travels_with_the_counts(self):
        """Zero open findings is not a result on its own.

        The same row set means "seven vectors probed and none succeeded" or
        "three probed and four could not" (audit D4), and only the denominator
        separates them.
        """
        from app.services.red_team_service import red_team_coverage

        result = self._fetch()
        coverage = red_team_coverage()

        assert result["vectors_attempted"] == coverage["vectors_attempted"]
        assert result["vectors_valid"] == coverage["vectors_valid"]
        assert result["coverage_complete"] is coverage["complete"]
        assert result["invalid_vectors"] == coverage["invalid_vectors"]
        assert result["coverage_source"] == COVERAGE_SOURCE_CURRENT_BUILD, (
            "a run that recorded no coverage must say the figures describe "
            "today's build, not the run"
        )

    def test_an_agent_that_was_never_attacked_is_not_measured(self):
        """THE DAY-1 LIE (P2 review).

        The failing input: a brand-new agent. red_team_runs is empty and
        red_team_findings is empty, so every count is zero — which is also what
        a genuinely clean run produces. The collector logged
        `red_team_summary.no_runs` and then returned signal='measured' anyway,
        so apply_signal_evidence_gate (which refuses only a signal that is not
        'measured') let `ship` through, and the platform asserted the security
        surface had been measured on the one day it certainly had not.
        """
        result = self._fetch(run_row=None)

        assert result["signal"] == RED_TEAM_SIGNAL_NO_RUNS
        assert result["last_run_at"] is None
        for key in ("critical_count", "high_count", "medium_count", "low_count"):
            assert result[key] is None, (
                f"{key} is a count of findings from zero runs — a zero nobody "
                "measured is not a zero"
            )
        assert result["vectors_attempted"] is None
        assert result["coverage_source"] is None

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), result
        )
        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_never_run"]

    def test_a_run_reports_the_coverage_it_had_not_the_readers(self):
        """Stored coverage wins, and says so.

        The failing input is time-shifted: P4 flips SDK_ATTACKERS_CAN_PROBE and
        every stored three-of-seven run is suddenly described to the deploy gate
        as seven-of-seven, because red_team_coverage() only ever describes the
        code that is running now. A run that recorded its own numbers must be
        read back with those numbers.
        """
        stored = {
            "vectors_attempted": 7,
            "vectors_valid": 3,
            "invalid_vectors": ["hallucination"],
            "complete": False,
        }
        result = self._fetch(run_row=(datetime(2026, 5, 23, 3, 0, 0), "complete", stored))

        assert result["vectors_valid"] == 3
        assert result["invalid_vectors"] == ["hallucination"]
        assert result["coverage_complete"] is False
        assert result["coverage_source"] == COVERAGE_SOURCE_RUN

    def test_a_pre_0015_tenant_degrades_to_the_current_build_and_labels_it(self):
        """UndefinedColumn on `coverage` costs the run's own figures, nothing
        else — and the substitution is named rather than silent."""
        result = self._fetch(raise_on="coverage")

        assert result["signal"] == RED_TEAM_SIGNAL_MEASURED
        assert result["coverage_source"] == COVERAGE_SOURCE_CURRENT_BUILD
        assert result["vectors_attempted"] is not None

    def test_a_malformed_stored_coverage_is_absent_not_partial(self):
        """A payload missing a key is not a coverage claim.

        Half a denominator would be worse than none: `vectors_valid` without
        `vectors_attempted` is a numerator wearing a denominator's name.
        """
        result = self._fetch(
            run_row=(datetime(2026, 5, 23, 3, 0, 0), "complete", {"vectors_valid": 3})
        )

        assert result["coverage_source"] == COVERAGE_SOURCE_CURRENT_BUILD

    def test_the_unavailable_substitute_is_not_a_clean_run(self):
        """deployment_blocked=False in the fallback is 'we could not ask', and
        the signal field is what stops it reading as 'no critical findings'."""
        assert RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL["deployment_blocked"] is False
        assert RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL["critical_count"] is None
        assert (
            apply_signal_evidence_gate(
                "ship", _measured_eval(), dict(RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL)
            )[0]
            == "block"
        )


class TestAnUnfinishedRedTeamRunIsNotAResult:
    """The security half of the #54 review: three ways a run is not a measurement."""

    def _fetch(self, run_row, executed=None):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.execute.side_effect = lambda sql, params=None: (
            executed.append(sql) if executed is not None else None
        )
        cursor.fetchone.side_effect = lambda: run_row
        cursor.fetchall.return_value = [("medium", 2)]
        conn.cursor.return_value = cursor
        with patch(
            "app.services.deployment_service.psycopg2.connect", return_value=conn
        ):
            return _fetch_red_team_summary_sync("test-agent", "postgresql://test/t")

    def test_the_query_excludes_the_row_a_running_job_inserts(self):
        """The in-flight row used to satisfy 'a run exists'.

        `run_red_team` INSERTs status='running' before it attacks anything. With
        no status filter the collector read that row, found zero open findings
        because nothing had been attacked, and reported MEASURED. An agent
        nothing had ever probed came back clean, which is the exact shape audit
        D4 named on the eval half.
        """
        executed = []
        self._fetch((datetime(2026, 5, 23, 3, 0, 0), "complete", None), executed)

        assert any("status <> 'running'" in sql for sql in executed), (
            "the newest-run query must exclude the row a running job inserts: "
            f"{executed}"
        )

    def test_a_run_that_did_not_complete_reports_run_failed(self):
        """`run_red_team` writes 'failed' from its own except handler.

        The open-finding query counts findings across ALL runs, so a run that
        died on its second attack vector produced a signal claiming the surface
        had been probed over counts belonging to whatever ran before it.
        """
        result = self._fetch((datetime(2026, 5, 23, 3, 0, 0), "failed", None))

        assert result["signal"] == RED_TEAM_SIGNAL_RUN_FAILED
        assert result["critical_count"] is None, (
            "a run that fell out of its body has no admissible counts"
        )
        assert "failed" in result["signal_detail"]

    def test_a_failed_run_blocks_with_its_own_warning(self):
        result = self._fetch((datetime(2026, 5, 23, 3, 0, 0), "failed", None))

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), result
        )
        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_run_failed"]


class TestTheCeilingExpirySubstitutes:
    """A job the wait never saw finish is a named absence, not a stale summary."""

    def test_the_eval_substitute_carries_no_number_and_names_the_wait(self):
        payload = eval_summary_did_not_finish(2700.4)

        assert payload["eval_signal"] == EVAL_SIGNAL_DID_NOT_FINISH
        assert payload["pass_rates"] is None
        assert payload["scenario_count"] is None
        assert "2700" in payload["signal_detail"], (
            "the observed wait separates a slow run from an unreachable tenant "
            f"DB, and both end here: {payload['signal_detail']}"
        )

    def test_the_red_team_substitute_carries_no_count(self):
        payload = red_team_summary_did_not_finish(2700.4)

        assert payload["signal"] == RED_TEAM_SIGNAL_DID_NOT_FINISH
        for key in ("critical_count", "high_count", "medium_count", "low_count"):
            assert payload[key] is None

    def test_both_substitutes_block_with_a_come_back_later_warning(self):
        """Three remedies, three warning ids. 'Could not be read' sends the
        owner looking for a broken thing; the run is simply still going."""
        recommendation, warnings = apply_signal_evidence_gate(
            "ship",
            eval_summary_did_not_finish(2700.0),
            red_team_summary_did_not_finish(2700.0),
        )

        assert recommendation == "block"
        assert sorted(w.warning_id for w in warnings) == [
            "eval_did_not_finish",
            "red_team_did_not_finish",
        ]
        for warning in warnings:
            assert "again" in warning.message.lower()


class TestPollTerminalStatuses:
    """One look, driven directly. The loop it replaces held the worker slot."""

    def test_a_terminal_status_is_recorded_and_a_running_one_is_not(self):
        statuses = poll_terminal_statuses(
            {},
            {"eval": lambda: "complete", "red_team": lambda: "running"},
        )

        assert statuses == {"eval": "complete", "red_team": None}

    def test_a_failed_run_is_terminal(self):
        """'failed' ENDS a run. Waiting past it would wait forever, and the
        record it left is what decide() reads as absent."""
        statuses = poll_terminal_statuses({}, {"eval": lambda: "failed"})

        assert statuses == {"eval": "failed"}

    def test_an_unrecognised_status_is_not_terminal(self):
        """A name this build cannot interpret is not evidence that a run ended."""
        statuses = poll_terminal_statuses({}, {"eval": lambda: "cancelled"})

        assert statuses == {"eval": None}

    def test_a_run_already_known_terminal_is_never_polled_again(self):
        """Its status is the answer. A later look could only find a NEWER run
        that something else started."""
        calls = []

        def _eval():
            calls.append(1)
            return "failed"

        statuses = poll_terminal_statuses({"eval": "complete"}, {"eval": _eval})

        assert calls == [], "a settled run was polled again"
        assert statuses == {"eval": "complete"}

    def test_a_name_absent_from_known_starts_unobserved(self):
        statuses = poll_terminal_statuses({"eval": "complete"}, {"red_team": lambda: None})

        assert statuses == {"red_team": None}, (
            "the returned mapping is keyed by the fetchers, never by whatever "
            "an older state happened to carry"
        )


def _reason(rule="golden_failure", outcome=None, **over):
    from app.domain.verdict import Outcome, Reason

    fields = {
        "rule": rule,
        "signal": "the fixed golden scenario set",
        "observed": "3 of 12 golden scenarios did not pass",
        "threshold": "every golden scenario must pass before a deploy ships",
        "outcome": outcome if outcome is not None else Outcome.BLOCK,
    }
    fields.update(over)
    return Reason(**fields)


class TestAVerdictReachesTheOwnerAsWarnings:
    """Criterion 4: 'block' never arrives unexplained."""

    def test_a_reason_renders_its_slug_its_observation_and_its_bar(self):
        """All three, because a refusal missing any one of them is unactionable.
        The slug is what a console groups and acknowledges on; the observation is
        what happened; the bar is what would have to change."""
        from app.domain.verdict import Outcome, Verdict

        verdict = Verdict(outcome=Outcome.BLOCK, reasons=[_reason()])

        warnings = verdict_warnings(verdict)

        assert len(warnings) == 1
        warning = warnings[0]
        assert warning.warning_id == "golden_failure", (
            "the warning_id IS the rule slug, so grouping on the rule and "
            "acknowledging the warning are the same key"
        )
        assert "the fixed golden scenario set" in warning.message
        assert "3 of 12 golden scenarios did not pass" in warning.message
        assert "every golden scenario must pass before a deploy ships" in warning.message
        assert warning.category == "eval_quality"
        assert warning.severity_level == "warning"

    def test_a_warning_reason_is_carried_too_not_only_a_blocking_one(self):
        """A ship_with_warnings whose reasons were dropped is a launch approved
        over concerns nobody was shown."""
        from app.domain.verdict import Outcome, Verdict

        verdict = Verdict(
            outcome=Outcome.SHIP_WITH_WARNINGS,
            reasons=[
                _reason(
                    rule="exploratory_ci_inconclusive",
                    outcome=Outcome.SHIP_WITH_WARNINGS,
                    provisional=True,
                )
            ],
        )

        assert [w.warning_id for w in verdict_warnings(verdict)] == [
            "exploratory_ci_inconclusive"
        ]

    def test_every_rule_the_table_can_produce_has_a_category(self):
        """A security rule filed under 'eval_quality' would read as a quality
        finding whatever it was about, so the gap has to be visible instead."""
        from app.domain import verdict as verdict_module

        slugs = set()
        for name in dir(verdict_module):
            if name.startswith("_rule_"):
                slugs.add(name[len("_rule_"):])
        # The rule table's function names are not all one-to-one with slugs
        # (`_rule_exploratory_ci` emits two), so the assertion is the other way
        # round: every mapped slug is real, and nothing maps to the unmapped
        # placeholder.
        assert VERDICT_WARNING_CATEGORY_UNMAPPED not in set(
            _VERDICT_WARNING_CATEGORIES.values()
        )
        assert len(_VERDICT_WARNING_CATEGORIES) == 12, (
            "RULE_VERSION 2 has twelve slugs across eleven rule functions"
        )
        assert slugs, "the rule table stopped being discoverable by name"

    def test_a_rule_this_build_does_not_know_lands_somewhere_visible(self):
        from app.domain.verdict import Outcome, Verdict

        verdict = Verdict(
            outcome=Outcome.BLOCK, reasons=[_reason(rule="a_rule_from_the_future")]
        )

        assert verdict_warnings(verdict)[0].category == (
            VERDICT_WARNING_CATEGORY_UNMAPPED
        )


class TestTheVerdictHandedToTheNarration:
    """render_verdict: the outcome and the reason sentences, and no numbers to
    re-derive."""

    def test_it_carries_the_outcome_and_each_reason_in_words(self):
        from app.domain.verdict import Outcome, Verdict

        rendered = render_verdict(
            Verdict(outcome=Outcome.BLOCK, reasons=[_reason()])
        )

        assert rendered["outcome"] == "block"
        assert rendered["rule_version"] >= 1
        assert rendered["reasons"] == [
            {
                "signal": "the fixed golden scenario set",
                "observed": "3 of 12 golden scenarios did not pass",
                "threshold": "every golden scenario must pass before a deploy ships",
                "outcome": "block",
                "provisional": False,
            }
        ]

    def test_it_leaves_the_rule_slug_off(self):
        """The slug is a machine key. In a model's context it is one more token
        to quote at an owner."""
        from app.domain.verdict import Outcome, Verdict

        rendered = render_verdict(Verdict(outcome=Outcome.BLOCK, reasons=[_reason()]))

        assert "rule" not in rendered["reasons"][0]

    def test_it_is_json_the_signals_blob_can_carry(self):
        from app.domain.verdict import Outcome, Verdict

        rendered = render_verdict(Verdict(outcome=Outcome.SHIP))

        assert json.loads(json.dumps(rendered))["outcome"] == "ship"


class TestTheReportToolTakesNoDecision:
    """#54 criterion 2, on the tool rather than the prose."""

    def test_submit_report_has_no_recommendation_field(self):
        """A field the model can fill is a field the model can fill wrongly, and
        the checklist would then hold two answers to one question."""
        schema = _TOOL_SUBMIT_REPORT["input_schema"]

        assert "recommendation" not in schema["properties"], (
            "the deploy decision is decide()'s, and the tool must not offer the "
            "model a place to put a different one"
        )
        assert "recommendation" not in schema["required"]
        assert sorted(schema["required"]) == ["summary", "warnings"]


class TestTheNarrationIsReadBeforeItIsTrusted:
    """parse_narration: the tool loop validates nothing, so this does.

    `build_report_tools` stores submit_report's arguments verbatim, so every
    shape below is a shape the model can actually put in front of the report.
    Each of them used to raise a pydantic ValidationError one step later, inside
    the persist block, where it cost a verdict the platform had already computed.
    """

    def _warning(self, warning_id="narrated_note"):
        return {
            "warning_id": warning_id,
            "category": "eval_quality",
            "message": "Worth a look before launch.",
            "severity_level": "info",
        }

    def test_a_well_formed_report_passes_through_untouched(self):
        narration = parse_narration(
            {"summary": "Two things to read first.", "warnings": [self._warning()]}
        )

        assert narration.summary == "Two things to read first."
        assert narration.warnings == [DeploymentWarning(**self._warning())]
        assert narration.dropped_warnings == 0
        assert narration.summary_replaced is False

    def test_a_warning_missing_its_required_fields_is_dropped(self):
        narration = parse_narration(
            {"summary": "ok", "warnings": [{"warning_id": "x"}]}
        )

        assert narration.warnings == []
        assert narration.dropped_warnings == 1
        assert narration.summary == "ok", "the readable half is still the prose"

    def test_one_bad_item_does_not_discard_the_readable_ones_beside_it(self):
        narration = parse_narration(
            {"summary": "ok", "warnings": [{"warning_id": "x"}, self._warning()]}
        )

        assert [w.warning_id for w in narration.warnings] == ["narrated_note"]
        assert narration.dropped_warnings == 1

    def test_a_warnings_value_that_is_not_a_list_is_refused_whole(self):
        narration = parse_narration({"summary": "ok", "warnings": "none"})

        assert narration.warnings == []
        assert narration.dropped_warnings == 1, (
            "the whole value was refused, and the count says one refusal"
        )

    def test_absent_warnings_are_not_counted_as_refused(self):
        """A turn that submitted no warnings wrote a report about a clean run.
        Nothing was refused, so nothing is logged as malformed."""
        narration = parse_narration({"summary": "All clear."})

        assert narration.warnings == []
        assert narration.dropped_warnings == 0
        assert narration.summary_replaced is False

    @pytest.mark.parametrize("summary", [None, "", "   ", 7, {"text": "ok"}])
    def test_a_summary_that_is_not_prose_falls_back(self, summary):
        narration = parse_narration({"summary": summary, "warnings": []})

        assert narration.summary == NARRATION_UNAVAILABLE_SUMMARY
        assert narration.summary_replaced is True

    def test_a_report_that_is_not_a_mapping_reads_as_no_narration(self):
        narration = parse_narration(["summary", "warnings"])

        assert narration.summary == NARRATION_UNAVAILABLE_SUMMARY
        assert narration.warnings == []
        assert narration.summary_replaced is True

    def test_what_it_returns_is_what_the_report_accepts(self):
        """The point of the whole function, asserted where it lands: the report
        constructs rather than raising, on arguments that used to raise."""
        narration = parse_narration(
            {"summary": None, "warnings": [{"warning_id": "x"}, self._warning()]}
        )

        report = DeploymentReport(
            recommendation="block",
            summary=narration.summary,
            warnings=narration.warnings,
            eval_summary={},
            red_team_summary={},
            verified_qa_stats={},
            corpus_stats={},
        )

        assert report.summary == NARRATION_UNAVAILABLE_SUMMARY
        assert [w.warning_id for w in report.warnings] == ["narrated_note"]


class TestTheTwoWarningsDecideCannotSee:
    """derive_quality_warnings: ported from prompt prose, deterministic, never
    blocking."""

    def test_a_thin_verified_corpus_warns(self):
        warnings = derive_quality_warnings({"row_count": 12}, _measured_red_team())

        assert [w.warning_id for w in warnings] == ["verified_qa_low_count"]
        assert "12" in warnings[0].message
        assert warnings[0].category == "knowledge_depth"

    def test_a_full_verified_corpus_does_not(self):
        assert (
            derive_quality_warnings({"row_count": 50}, _measured_red_team()) == []
        ), "the floor is 'fewer than', so exactly the floor is enough"

    def test_open_medium_findings_over_the_line_warn(self):
        warnings = derive_quality_warnings(
            {"row_count": 60}, _measured_red_team(medium_count=3)
        )

        assert [w.warning_id for w in warnings] == ["red_team_medium_findings"]
        assert "3" in warnings[0].message

    def test_exactly_the_line_does_not_warn(self):
        assert (
            derive_quality_warnings(
                {"row_count": 60}, _measured_red_team(medium_count=2)
            )
            == []
        )

    def test_a_medium_count_from_an_unmeasured_run_is_not_read(self):
        """The counts are null outside 'measured', and `None > 2` is not a
        comparison anyone meant to make. A run nobody read has no findings to
        be under a line."""
        unmeasured = dict(RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL)
        unmeasured["medium_count"] = 9

        assert derive_quality_warnings({"row_count": 60}, unmeasured) == []

    def test_a_row_count_that_is_not_a_number_is_not_read_as_a_small_one(self):
        assert derive_quality_warnings({"row_count": None}, _measured_red_team()) == []
        assert derive_quality_warnings({}, _measured_red_team()) == []


# ---------------------------------------------------------------------------
# TestTheCalibrationBlock - the Judge's own calibration status, on the summary
# ---------------------------------------------------------------------------


def _judge(**overrides) -> JudgeIdentity:
    fields = {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "none",
        "prompt_version": "ragas-0.4.1",
    }
    fields.update(overrides)
    return JudgeIdentity(**fields)


def _calibrated_for(identity: JudgeIdentity) -> CalibrationStatus:
    """The record a calibration run leaves when the Judge earned it."""
    return CalibrationStatus(
        status=STATUS_CALIBRATED,
        judge_identity=identity,
        judge_interval=Interval(low=0.41, high=0.83, point=0.62, usable=True),
        ceiling_interval=Interval(low=0.55, high=0.91, point=0.74, usable=True),
        difference_interval=Interval(low=-0.09, high=0.24, point=0.12, usable=True),
        beats_chance=True,
        ceiling_beats_chance=True,
        reaches_ceiling=True,
        kappa=0.62,
        matthews=0.64,
        scored_pairs=24,
        pairs=30,
        attempted=34,
        valid=32,
        labels_made_at="2026-08-29T10:15:00+00:00",
        harness_version="compute_correlation-2026-08-29",
    )


def _collect(record=None):
    """Run the collector over one complete, agent-invoking run.

    `record=None` is a run that wrote no `eval_runs.result`, which is
    EVAL_SIGNAL_NO_RECORD and, for calibration, a run with no Judge to ask
    about.
    """
    conn = _make_eval_conn(
        (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", _invoked_config()),
        record=record,
    )
    with patch("app.services.deployment_service.psycopg2.connect", return_value=conn):
        return _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")


class TestTheCalibrationBlock:
    """The deploy summary carries what is known about the Judge behind its numbers.

    The harness that answers this cannot be called from the deploy path, so its
    verdict arrives as a file and `load_calibration_status` reads it for the one
    Judge the run actually used. Nothing here gates on the answer. Ticket 17
    (#54) adds the refusal; this slice makes the status visible so that ticket
    has something to read and the orchestrator can name it.
    """

    def test_a_matching_calibrated_artifact_reaches_the_summary(self):
        """The record's own figures, not a re-derivation of them."""
        identity = _judge()

        with patch(
            "app.services.deployment_service.load_calibration_status",
            return_value=_calibrated_for(identity),
        ):
            result = _collect(record=_record(judge_identity=identity))

        calibration = result["calibration"]
        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        assert calibration["status"] == STATUS_CALIBRATED
        assert calibration["reason"] is None
        assert calibration["judge_identity"] == {
            "model": "gpt-5.6-luna",
            "reasoning_effort": "none",
            "prompt_version": "ragas-0.4.1",
        }
        assert calibration["judge_interval"] == {
            "low": 0.41,
            "high": 0.83,
            "point": 0.62,
            "usable": True,
        }
        assert calibration["ceiling_interval"] == {
            "low": 0.55,
            "high": 0.91,
            "point": 0.74,
            "usable": True,
        }
        assert calibration["kappa"] == pytest.approx(0.62)
        assert calibration["matthews"] == pytest.approx(0.64)
        assert calibration["scored_pairs"] == 24
        assert calibration["pairs"] == 30
        assert calibration["labels_made_at"] == "2026-08-29T10:15:00+00:00"
        assert calibration["harness_version"] == "compute_correlation-2026-08-29"

    def test_the_loader_is_asked_about_the_judge_the_run_used(self):
        """The identity is lifted off the record, never off the artifact.

        An artifact answering for whichever Judge it happens to hold would report
        yesterday's agreement over today's Judge, which is the alignment decay
        the three-field key exists to catch.
        """
        identity = _judge(prompt_version="ragas-0.4.2")
        seen: dict = {}

        def _spy(path, judge):
            seen["path"], seen["judge"] = path, judge
            return CalibrationStatus.absent("no_artifact")

        with patch("app.services.deployment_service.load_calibration_status", _spy):
            _collect(record=_record(judge_identity=identity))

        assert seen["judge"] == identity
        assert seen["path"] == settings.CALIBRATION_ARTIFACT_PATH

    def test_a_run_with_no_record_has_no_identity_to_ask_about(self):
        """`no_single_judge_identity`, and the real loader reaches it.

        Not doubled here. With an identity and no artifact the reason would be
        `no_artifact`, so the two answers are distinguishable and this one says
        the collector handed over None.
        """
        result = _collect(record=None)

        assert result["eval_signal"] == EVAL_SIGNAL_NO_RECORD
        assert result["calibration"]["status"] == STATUS_NOT_CALIBRATED_YET
        assert result["calibration"]["reason"] == "no_single_judge_identity"

    def test_a_missing_artifact_says_so_and_carries_no_figures(self):
        """The state every deploy is in until a calibration run ships (#58)."""
        with patch(
            "app.services.deployment_service.load_calibration_status",
            return_value=CalibrationStatus.absent("no_artifact"),
        ):
            result = _collect(record=_record(judge_identity=_judge()))

        calibration = result["calibration"]
        assert calibration["status"] == STATUS_NOT_CALIBRATED_YET
        assert calibration["reason"] == "no_artifact"
        assert calibration["judge_identity"] is None
        assert calibration["kappa"] is None
        assert calibration["judge_interval"] is None

    def test_the_key_set_is_the_service_selection(self):
        """One selection, pinned in one place.

        A key added to `SUMMARY_KEYS` and not to the record, or the other way
        round, goes red here rather than reaching an orchestrator prompt that
        names a field nobody writes.
        """
        result = _collect(record=_record(judge_identity=_judge()))

        assert tuple(result["calibration"]) == SUMMARY_KEYS

    @pytest.mark.parametrize(
        "status",
        [
            STATUS_CALIBRATED,
            STATUS_NOT_CALIBRATED,
            STATUS_NOT_CALIBRATED_YET,
            STATUS_SETUP_ERROR,
        ],
    )
    def test_the_calibration_refusal_is_not_in_force_yet_ticket_17(self, status):
        """A fully measured, all-passed run still ships on any calibration status.

        Ticket 17 (#54) adds the refusal "ship without a calibrated Judge". Until
        it lands, an uncalibrated Judge is narrated and never enforced, and this
        test says so out loud. The day the refusal arrives this goes red, and #54
        rewrites it rather than discovering the change by accident.
        """
        summary = _measured_eval()
        summary["calibration"] = {**summary["calibration"], "status": status}

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )

        assert recommendation == "ship"
        assert warnings == []

    def test_the_prompt_names_the_key_and_the_state_it_will_see(self):
        """Drift protection over a string, and read as consistency only.

        No test anywhere observes the model obeying prose, so this pins that the
        narration names the field the payload carries and the state that field
        holds today.
        """
        assert "eval_summary.calibration" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "not_calibrated_yet" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "no_single_judge_identity" in _DEPLOYMENT_SYSTEM_PROMPT

    def test_the_prompt_names_every_reason_the_summary_can_carry(self):
        """The paragraph says which statuses reach a deploy summary today, so a
        reason added to the loader and not to the prose leaves the model reading
        a token nothing explained."""
        for reason in ABSENT_REASONS:
            assert reason in _DEPLOYMENT_SYSTEM_PROMPT, reason
