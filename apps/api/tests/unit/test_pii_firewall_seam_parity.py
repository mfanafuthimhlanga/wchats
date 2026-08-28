"""#50: the PII firewall is inside the seam, so no caller of it can skip it.

THE DEFECT THIS PINS. `scan_response` ran in the live Celery task body, after
`run_agent_loop` returned. `app.worker.tasks.runtime.eval` never imported
`app.domain.pii_firewall` at all, so a response carrying a customer's email
address, card number or SA ID was carried into the Ragas sample as
`agent_response` and posted verbatim to a third-party judge API. Meanwhile
`pii_firewall`'s own module docstring said the scan was called unconditionally,
which two of the loop's three callers not calling it made false by construction
(the third is `red_team_probe._build_transactional_probe_fn`).

WHY BOTH PATHS ARE DRIVEN FOR REAL. The claim is about a shared function, and a
test that drove one path and asserted about the other by inspection is how the
gap survived in the first place. So both halves here run the real
`agent_loop.run_agent_loop` over the same scripted client and the same tool:

    live   `run_agent_loop` as `run_agent_turn` calls it. What it returns IS the
           served text now — the task body reads `result["response_text"]` and
           does not filter again.
    eval   `_invoke_agent_for_scenarios` down through the real `_run_one_eval_turn`
           and `_drive_eval_turn`, so the assertion lands on the `agent_response`
           that would reach Ragas.

Only two boundaries are replaced, and neither touches the text: the provider
client (a script of canned completions) and the control-DB session the eval task
opens to read its agent row.

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

from app.core.model_client import route_for
from app.domain.pii_firewall import PII_DEFLECTION
from app.services.agent_loop import (
    MAX_MODEL_CALLS_PER_TURN,
    AgentTurn,
    run_agent_loop,
)
from app.services.agent_tools import _frame_retrieved_context
from app.worker.tasks.runtime import eval as eval_mod

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


def _eval_scored_row(answer: str) -> dict:
    """The Ragas sample the eval path builds, down through the real loop.

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
    ):
        rows, _summary = eval_mod._invoke_agent_for_scenarios(
            agent_id=str(agent.id),
            conn_str=PRODUCTION,
            run_id=RUN_ID,
            scenarios=[scenario],
            prompt_version_id=None,
        )

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
