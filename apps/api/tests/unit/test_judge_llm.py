"""The one judge builder, and the provider rule it carries (#198).

OBSERVED 2026-09-05 on staging: every Ragas metric call was refused with
`Unsupported parameter: 'max_tokens' is not supported with this model. Use
'max_completion_tokens' instead.`, so an eval that had the agent answer all 20
scenarios scored none of them. ragas 0.4.3 renames the field only for a model its
detector parses as `gpt-<int>`, and `int("5.6")` raises.

Nothing here touches a socket. The wire test records the body the SDK built.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel
from ragas.llms import InstructorLLM

from app.core.model_client import LedgerContext
from app.services.judge_llm import LunaInstructorLLM, build_judge_llm

TENANT = "11111111-1111-1111-1111-111111111111"


class _Verdict(BaseModel):
    score: int


def _tool_call_body() -> dict:
    return {
        "id": "chatcmpl-judge",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-5.6-luna",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "_Verdict", "arguments": json.dumps({"score": 1})},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def _judge_through(sent: dict, monkeypatch: pytest.MonkeyPatch, purpose: str = "judge_faithfulness"):
    """A judge whose async httpx client answers canned bytes and records the request."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=_tool_call_body(), headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handler)

    class _Pinned(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Pinned)
    # The api key comes from cached Settings, not the environment, and it never
    # leaves the process: every request lands in the MockTransport above.
    return build_judge_llm(purpose, LedgerContext(tenant_id=TENANT, recorder=lambda call: None))


class TestTheJudgeSendsWhatTheProviderAccepts:
    def test_max_tokens_goes_out_as_max_completion_tokens(self, monkeypatch):
        """The exact field staging refused, read off the bytes the judge sent."""
        sent: dict = {}
        asyncio.run(_judge_through(sent, monkeypatch).agenerate("score this", _Verdict))

        assert "max_tokens" not in sent, f"the judge still sends max_tokens: {sent.get('max_tokens')!r}"
        assert sent["max_completion_tokens"] == 1024

    def test_the_rest_of_the_body_is_unchanged_by_the_rename(self, monkeypatch):
        """Only the rename. Ragas's reasoning-model branch would also force
        temperature to 1.0 and drop top_p, and neither is wanted (BACKLOG 8.2a, 8.10)."""
        sent: dict = {}
        asyncio.run(_judge_through(sent, monkeypatch).agenerate("score this", _Verdict))

        assert sent["model"] == "gpt-5.6-luna"
        assert sent["reasoning_effort"] == "none"
        assert sent["temperature"] == 0
        assert sent["top_p"] == 0.1
        assert sent["tools"], "instructor's forced tool is what the provider checks the effort against"

    def test_the_mapping_renames_only_when_max_tokens_is_present(self):
        llm = LunaInstructorLLM(client=object(), model="gpt-5.6-luna", provider="openai", temperature=0)

        mapped = llm._map_provider_params()

        assert "max_tokens" not in mapped
        assert mapped["max_completion_tokens"] == 1024
        assert mapped["temperature"] == 0

    def test_ragas_own_mapping_would_have_sent_max_tokens(self):
        """The defect, pinned so a ragas upgrade that fixes it is noticed: when
        this fails, the subclass can go."""
        llm = InstructorLLM(client=object(), model="gpt-5.6-luna", provider="openai", temperature=0)

        mapped = llm._map_provider_params()

        assert "max_tokens" in mapped
        assert "max_completion_tokens" not in mapped


class TestBothConsumersBuildTheSameJudge:
    def test_eval_service_builds_it_here(self):
        from app.services.eval_service import _build_instructor_llm

        llm = _build_instructor_llm("judge_faithfulness", LedgerContext(tenant_id=TENANT, recorder=lambda call: None))

        assert isinstance(llm, LunaInstructorLLM)
        assert llm.is_async is True

    def test_the_retrieval_task_builds_it_here(self):
        from app.worker.tasks.runtime import retrieval_eval as mod

        llm = mod._build_instructor_llm(mod.JUDGE_PURPOSE, LedgerContext(tenant_id=TENANT, recorder=lambda call: None))

        assert isinstance(llm, LunaInstructorLLM)
        assert llm.is_async is True
