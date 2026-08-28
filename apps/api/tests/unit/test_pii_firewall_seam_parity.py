"""#50: the PII firewall is inside the seam, so no caller of it can skip it.

THE DEFECT THIS PINS. `scan_response` ran in the live Celery task body, after
`run_agent_loop` returned. `app.worker.tasks.runtime.eval` never imported
`app.domain.pii_firewall` at all, so a response carrying a customer's email
address, card number or SA ID was carried into the Ragas sample as
`agent_response` and posted verbatim to a third-party judge API. Meanwhile
`pii_firewall`'s own module docstring said the scan was called unconditionally,
which two of the loop's three callers not calling it made false by construction
(the third is `red_team_probe._build_transactional_probe_fn`, pinned in
tests/unit/test_red_team_probe.py section 17, beside the `_drive` harness
that already stands its victim turn up).

WHY BOTH PATHS ARE DRIVEN FOR REAL. The claim is about a shared function, and a
test that drove one path and asserted about the other by inspection is how the
gap survived in the first place. So both halves here run the real
`agent_loop.run_agent_loop` over the same scripted client and the same tool:

    live   `run_agent_loop` as `run_agent_turn` calls it. What it returns IS the
           served text now, and the pair at the BOTTOM of this file drives
           `run_agent_turn` itself to prove it, asserting on the `agent.response`
           payload the widget renders rather than on a return value.
    eval   `_invoke_agent_for_scenarios` down through the real `_run_one_eval_turn`
           and `_drive_eval_turn`, so the assertion lands on the `agent_response`
           that would reach Ragas.

Only two boundaries are replaced, and neither touches the text: the provider
client (a script of canned completions) and the control-DB session the eval task
opens to read its agent row. The served pair at the bottom replaces three more,
all of them a database the test has no server for, and none of them the text.

THE CONTROL IS THE SECOND TEST. A pair that only ever observes the deflection
cannot tell parity from a constant, and "both paths always return
PII_DEFLECTION" is a way this could pass while being wrong. The clean answer
therefore runs through the identical harness and must come back untouched on
both paths.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from structlog.testing import capture_logs

from app.core.model_client import route_for
from app.domain.pii_firewall import PII_DEFLECTION
from app.services.agent_loop import (
    MAX_MODEL_CALLS_PER_TURN,
    AgentTurn,
    run_agent_loop,
)
from app.services.agent_tools import _frame_retrieved_context
from app.worker.tasks.runtime import eval as eval_mod
from app.worker.tasks.runtime.agent import run_agent_turn

RUN_ID = "55555555-5555-5555-5555-555555555555"
JOB_ID = "33333333-3333-3333-3333-333333333333"
PRODUCTION = "postgresql://production/tenant"
QUESTION = "Did my refund go through?"

#: The corpus chunk this turn retrieves. It carries NO email address, so nothing
#: here can exempt the one in the answer below — the BACKLOG 7.29 allowlist is
#: covered by tests/unit/test_pii_firewall_published_context.py and would make
#: this test pass for the wrong reason.
POLICY_CHUNK = "Refunds are returned to the original payment method within 5 working days."

#: A customer's own address, which no tenant publishes and the firewall deflects.
CUSTOMER_ADDRESS = "jane.smith@gmail.example"
LEAKING_ANSWER = f"Yes, the confirmation went to {CUSTOMER_ADDRESS} this morning."
CLEAN_ANSWER = "Yes, it was returned to your original payment method."


# ---------------------------------------------------------------------------
# Doubles: the provider client, and the one tool this turn calls.
# ---------------------------------------------------------------------------


def _completion(content=None, tool_calls=(), finish_reason="stop"):
    message = SimpleNamespace(content=content, tool_calls=list(tool_calls) or None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


class _Client:
    """Two scripted replies: ask for `retrieve`, then answer."""

    def __init__(self, answer: str) -> None:
        replies = [
            _completion(
                tool_calls=[
                    SimpleNamespace(
                        id="call-1",
                        type="function",
                        function=SimpleNamespace(
                            name="retrieve", arguments='{"query": "refund status"}'
                        ),
                    )
                ],
                finish_reason="tool_calls",
            ),
            _completion(content=answer),
        ]
        self.replies = replies
        self.sent = 0
        self.chat = SimpleNamespace(completions=self)
        self.closed = 0

    async def create(self, **kwargs):
        reply = self.replies[min(self.sent, len(self.replies) - 1)]
        self.sent += 1
        return reply

    async def close(self) -> None:
        self.closed += 1


async def _retrieve_handler(args):
    """The wire `retrieve_tool` returns: the framed text, and the ride-along."""
    chunks = [
        {
            "content": POLICY_CHUNK,
            "chunk_id": "chunk-0",
            "document_id": "ACME-HANDBOOK.pdf",
            "score": 0.88,
        }
    ]
    return {
        "content": [
            {"type": "text", "text": _frame_retrieved_context(json.dumps(chunks))}
        ],
        "_retrieved_context": {"chunks": chunks},
    }


def _turn_saying(answer: str) -> AgentTurn:
    """A fresh assembled turn. Fresh per path: the loop closes the client it owns."""
    return AgentTurn(
        client=_Client(answer),
        route=route_for("agent_turn"),
        system_prompt="you are a refunds specialist",
        tools=(
            SimpleNamespace(
                name="retrieve",
                description="search the knowledge base",
                input_schema={"type": "object", "properties": {}},
                handler=_retrieve_handler,
            ),
        ),
        max_model_calls=MAX_MODEL_CALLS_PER_TURN,
        max_budget_usd=1.0,
        calls=[],
        ledger=lambda call: None,
    )


# ---------------------------------------------------------------------------
# The two paths
# ---------------------------------------------------------------------------


def _live_response(answer: str) -> dict:
    """`run_agent_loop` as `run_agent_turn` drives it. Its text is the served text."""
    with patch("app.services.agent_loop.emit", lambda *a, **k: None):
        return asyncio.run(
            run_agent_loop(
                QUESTION,
                history=[],
                turn=_turn_saying(answer),
                job_id=JOB_ID,
                db=MagicMock(),
                redis=MagicMock(),
            )
        )


def _eval_invocation(answer: str) -> tuple[list[dict], dict, list[dict]]:
    """One whole invocation phase: (scored rows, the run's observation, the logs).

    `build_agent_turn` is replaced rather than `_run_one_eval_turn`, so
    `_drive_eval_turn` and `run_agent_loop` both run for real — the firewall lives
    below both of them, and doubling either would step over the thing under test.
    """
    agent = MagicMock()
    agent.id = uuid.uuid4()
    db = MagicMock()
    db.get.return_value = agent

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)

    scenario = {
        "id": "s0",
        "source": "generated",
        "question": QUESTION,
        "reference_answer": "It is returned to the original payment method.",
        "stored_retrieved_contexts": ["STORED CONTEXT"],
        "dataset": "exploratory",
    }

    with (
        patch.object(eval_mod, "get_sync_db", lambda: ctx),
        patch("app.services.agent_loop.emit", lambda *a, **k: None),
        patch(
            "app.services.agent_loop.build_agent_turn",
            return_value=_turn_saying(answer),
        ),
        capture_logs() as logs,
    ):
        rows, summary = eval_mod._invoke_agent_for_scenarios(
            agent_id=str(agent.id),
            conn_str=PRODUCTION,
            run_id=RUN_ID,
            scenarios=[scenario],
            prompt_version_id=None,
        )
    return rows, summary, logs


def _eval_scored_row(answer: str) -> dict:
    """The Ragas sample the eval path builds, down through the real loop."""
    rows, _summary, _logs = _eval_invocation(answer)
    assert rows, (
        "the eval scored nothing, so this proves nothing about what it scores. "
        "A row reaches the scorer only when the turn responded AND retrieved."
    )
    return rows[0]


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


def test_a_leaking_response_is_the_deflection_on_both_paths() -> None:
    """THE PARITY. Same seam, same answer, same deflection — served and scored."""
    live = _live_response(LEAKING_ANSWER)
    scored = _eval_scored_row(LEAKING_ANSWER)

    assert live["response_text"] == PII_DEFLECTION, (
        f"the live path served {live['response_text']!r}"
    )
    assert live["pii_detector"] == "email"
    assert scored["agent_response"] == PII_DEFLECTION, (
        f"the eval scored {scored['agent_response']!r}. A response the customer "
        "would never have seen was handed to Ragas and posted to the judge API "
        "with the address still in it."
    )
    assert scored["agent_response"] == live["response_text"]
    assert CUSTOMER_ADDRESS not in scored["agent_response"]
    assert CUSTOMER_ADDRESS not in live["response_text"]


def test_a_clean_response_is_the_agents_own_text_on_both_paths() -> None:
    """The control: the harness is not returning the deflection regardless.

    Without this, "both paths deflect" is satisfied by a firewall that deflects
    everything, and the parity above would read the same.
    """
    live = _live_response(CLEAN_ANSWER)
    scored = _eval_scored_row(CLEAN_ANSWER)

    assert live["response_text"] == CLEAN_ANSWER
    assert live["pii_detector"] is None
    assert scored["agent_response"] == CLEAN_ANSWER
    assert scored["retrieved_contexts"] == [POLICY_CHUNK], (
        "the eval scored contexts the agent did not retrieve, so the turn under "
        "test is not the turn that ran"
    )


# ---------------------------------------------------------------------------
# The live path, driven as a customer drives it (#50 follow-up)
#
# `_live_response` above calls `run_agent_loop` directly and calls that "live".
# It is the right function, but nothing between it and the customer's browser is
# under test there, and the sentence at the top of this file, "the task body
# reads `result['response_text']` and does not filter again", was asserted by
# reading the task rather than by running it. The failure that leaves open: a
# diagnostics ticket adds `pii_original_text` to the seam's dict, a well-meaning
# line in the task body reads it back into the emit, and every test in this file
# stays green while the address reaches the widget.
#
# So this pair drives `run_agent_turn` itself, over the same scripted client and
# the same real loop, and asserts on the payload `emit` is handed for
# `agent.response`. That payload IS what the customer's browser renders; a return
# value is not.
# ---------------------------------------------------------------------------

TENANT_DSN = "postgresql://tenant/served"
SERVED_CONVERSATION_ID = "00000000-0000-0000-0000-0000000000f1"
SERVED_MESSAGE_ID = "test-assistant-msg-id-pii-parity"


def _deflection_lines(logs: list[dict]) -> list[dict]:
    """The `pii_firewall.response_deflected` lines in one captured run."""
    return [line for line in logs if line.get("event") == "pii_firewall.response_deflected"]


def _db_ctx(db: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _control_db_for(agent) -> MagicMock:
    """The control session the task reads its agent and its job row from."""
    job = MagicMock()
    job.id = JOB_ID
    job.status = "running"
    job.finished_at = None

    db = MagicMock()
    db.execute.return_value.fetchone.return_value = None  # no idempotency row
    db.get.side_effect = [agent, job]
    return db


def _served_turn(answer: str) -> dict:
    """Drive `run_agent_turn` for real; return what the widget and the table get.

    The five replaced boundaries are the ones test_agent_task.py already replaces
    for this task, and none of them touches the response text: the control-DB
    session, the tenant DSN's decryption, the tenant connection, the conversation
    INSERT and the message INSERT. `asyncio.run`, `asyncio.wait_for` and
    `run_agent_loop` are all left real, which is the whole point: the seam runs,
    the firewall inside it runs, and the task body does whatever it does with what
    comes back.
    """
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = "Refunds Agent"
    agent.soul_role = "customer service representative"
    agent.soul_voice = "helpful"
    agent.soul_do_list = []
    agent.soul_donot_list = []
    agent.retrieval_strategy = {}
    agent.neon_connection_string = b"encrypted-bytes"

    emitted: list[tuple[str, dict]] = []
    persisted: list[dict] = []

    def _record_emit(_jid, event_type, payload, _db, _redis):
        emitted.append((event_type, payload or {}))

    def _record_persist(**kwargs):
        persisted.append(kwargs)
        return SERVED_MESSAGE_ID

    task = "app.worker.tasks.runtime.agent."
    with (
        patch(task + "get_sync_db", return_value=_db_ctx(_control_db_for(agent))),
        patch(task + "fernet_decrypt", return_value=TENANT_DSN),
        patch(task + "psycopg2.connect"),
        patch(task + "_create_conversation_row", return_value=SERVED_CONVERSATION_ID),
        patch(task + "_persist_messages", side_effect=_record_persist),
        patch(task + "build_agent_turn", side_effect=lambda **_kw: _turn_saying(answer)),
        patch(task + "emit", side_effect=_record_emit),
        patch("app.services.agent_loop.emit", lambda *a, **k: None),
        capture_logs() as logs,
    ):
        run_agent_turn.run(
            job_id=JOB_ID,
            agent_id=str(agent.id),
            message=QUESTION,
            conversation_id=None,
        )

    responses = [payload for event_type, payload in emitted if event_type == "agent.response"]
    assert len(responses) == 1, (
        f"expected exactly one agent.response, got {[e for e, _ in emitted]}. "
        "A turn that emitted none proves nothing about what it serves."
    )
    assert len(persisted) == 1, f"expected one _persist_messages call, got {persisted}"
    return {
        "payload": responses[0],
        "assistant_msg": persisted[0]["assistant_msg"],
        "logs": logs,
    }


def test_the_served_agent_response_payload_carries_the_deflection() -> None:
    """What reaches the browser, not what the loop returned to its caller.

    The task is free to read any key off the seam's dict; only `response_text` is
    the served one. This asserts on the SSE payload, so a body that read the
    pre-scan text back from a diagnostics key would fail here while every
    return-value assertion above stayed green.
    """
    served = _served_turn(LEAKING_ANSWER)

    assert served["payload"]["text"] == PII_DEFLECTION, (
        f"the widget was sent {served['payload']['text']!r}. The customer's own "
        "address left the system in the terminal SSE event."
    )
    assert CUSTOMER_ADDRESS not in served["payload"]["text"]
    assert CUSTOMER_ADDRESS not in served["assistant_msg"], (
        f"the pre-scan text was written to `messages` as {served['assistant_msg']!r}, "
        "so it is at rest in the tenant database and replays into the next turn's "
        "history whatever the SSE event said."
    )
    assert served["payload"]["citations"] == [], "a deflection cites nothing"

    # The live path's own log line, which nothing asserted on before #103 moved
    # its SHAPE into agent_loop.log_pii_firewall. The eval and the red-team probe
    # call the same function with their own ids.
    deflected = _deflection_lines(served["logs"])
    assert len(deflected) == 1, f"the served turn logged {len(deflected)} line(s)"
    assert deflected[0]["detector"] == "email"
    assert deflected[0]["job_id"] == JOB_ID
    assert deflected[0]["conversation_id"] == SERVED_CONVERSATION_ID
    assert CUSTOMER_ADDRESS not in str(deflected[0])


def test_the_served_agent_response_payload_is_the_agents_own_clean_text() -> None:
    """The control. Without it, an emit hardcoded to the deflection reads as a pass."""
    served = _served_turn(CLEAN_ANSWER)

    assert served["payload"]["text"] == CLEAN_ANSWER, (
        f"a clean answer was altered on the way to the widget: "
        f"{served['payload']['text']!r}"
    )
    assert served["assistant_msg"] == CLEAN_ANSWER
    assert _deflection_lines(served["logs"]) == [], (
        "the live path logged a deflection for a turn the firewall left alone"
    )


# ---------------------------------------------------------------------------
# The eval says how many answers it substituted, and logs each one (#103)
#
# #50 deleted `pii_firewall_applied=False` and put nothing in its place, so a run
# whose Faithfulness fell because three answers were deflected was byte-identical
# to a run where the model was wrong three times. The two tests below drive the
# same real harness as the parity pair above — one leaking answer, one clean —
# and the CONTROL is what makes the count a reading rather than a constant: a
# clean run through the identical code must report zero.
# ---------------------------------------------------------------------------


def test_the_eval_counts_and_logs_the_answer_the_firewall_substituted() -> None:
    """The run's observation says one of its scored answers was not the agent's."""
    rows, summary, logs = _eval_invocation(LEAKING_ANSWER)

    assert rows and rows[0]["agent_response"] == PII_DEFLECTION
    assert summary["responses_deflected"] == 1, (
        f"the run reported {summary['responses_deflected']} deflected answer(s) "
        "for a scenario the firewall substituted. Faithfulness for this run was "
        "computed over the firewall's sentence and the run cannot say so."
    )
    assert summary["scored_responses_deflected"] == 1, (
        "the count beside `scorable`, the denominator the metrics were actually "
        f"computed over, is {summary['scored_responses_deflected']}"
    )
    assert summary["deflection_detectors"] == {"email": 1}, (
        f"the run named {summary['deflection_detectors']} as what was caught"
    )

    deflected = _deflection_lines(logs)
    assert len(deflected) == 1, (
        f"the eval path logged {len(deflected)} deflection line(s). The live task "
        "carried the only copy of this line, so the eval substituted in silence."
    )
    assert deflected[0]["detector"] == "email"
    assert deflected[0]["scenario_id"] == "s0"
    assert deflected[0]["run_id"] == RUN_ID
    assert deflected[0]["original_length"] == len(LEAKING_ANSWER)
    assert CUSTOMER_ADDRESS not in str(deflected[0]), (
        "the log line carries the text it exists to report the removal of"
    )


def test_a_run_the_firewall_left_alone_counts_and_logs_nothing() -> None:
    """THE CONTROL. Without it, a hardcoded 1 and a hardcoded log line pass above."""
    rows, summary, logs = _eval_invocation(CLEAN_ANSWER)

    assert rows and rows[0]["agent_response"] == CLEAN_ANSWER
    assert summary["responses_deflected"] == 0, (
        "a clean answer was counted as substituted, so the number above is a "
        "constant and says nothing about the run"
    )
    assert summary["scored_responses_deflected"] == 0
    assert summary["deflection_detectors"] == {}
    assert _deflection_lines(logs) == [], (
        "the deflection line fires on a turn the firewall left alone"
    )
