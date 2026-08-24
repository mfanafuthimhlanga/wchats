"""BACKLOG 7.34 — the capture records what the worker stored, chunks included.

The rule under test is the one that decides what a judge is shown, and it is the
same rule `_persisted_chunks` applies on the way in. Kept testable on a machine
with no PostgreSQL by shaping the row separately from fetching it.
"""

from __future__ import annotations

from tests.evals.capture_responses import shape_tool_call

CHUNK = "[source: HANDBOOK.pdf | section: Returns]\nUnopened bags, 14 days."


def test_a_retrieve_with_chunks_carries_them_into_the_corpus():
    call = shape_tool_call("retrieve", {"query": "returns"}, [CHUNK])
    assert call["tool_name"] == "retrieve"
    assert call["result"] == {"chunks": [CHUNK]}


def test_a_corpus_miss_is_present_and_empty():
    call = shape_tool_call("retrieve", {"query": "nothing matches"}, [])
    assert call["result"] == {"chunks": []}, (
        "a retrieve that ran and matched nothing is an observation the judge can use"
    )


def test_null_chunks_leave_the_result_absent():
    call = shape_tool_call("escalate_to_human", {"reason": "frustration"}, None)
    assert call["result"] == {}


def test_the_two_empty_shapes_are_distinguishable():
    """The property the column exists for, asserted where the judge will read it."""
    miss = shape_tool_call("retrieve", {"query": "q"}, [])
    absent = shape_tool_call("retrieve", {"query": "q"}, None)
    assert miss["result"] != absent["result"]
    assert bool(miss["result"]) is True and bool(absent["result"]) is False, (
        "validate_corpus keys BLIND on falsiness, so a corpus miss must not read "
        "as an absent chunk"
    )


def test_a_missing_tool_name_becomes_empty_string_not_none():
    """`run_evals.py` compares this to a literal; None would raise rather than miss."""
    call = shape_tool_call(None, None, None)
    assert call["tool_name"] == ""
    assert call["input"] == {}


def test_the_corpus_validator_agrees_with_this_shape():
    """The two halves of 7.34 must read the same rows the same way."""
    from tests.evals import validate_corpus as vc

    good = shape_tool_call("retrieve", {"query": "q"}, [CHUNK])
    blind = shape_tool_call("retrieve", {"query": "q"}, None)
    record = {"response_text": "x" * 200, "tool_calls_log": [good]}
    assert vc.blind_findings("S-101", record) == []
    record = {"response_text": "x" * 200, "tool_calls_log": [blind]}
    assert vc.blind_findings("S-101", record), (
        "an absent chunk must be reported, or the corpus passes validation and "
        "fails grounding silently"
    )


# ---------------------------------------------------------------------------
# BACKLOG 8.1 — a run is an INDEPENDENT attempt, which is what makes reliable@k
# a statement about the agent rather than about one long session
# ---------------------------------------------------------------------------


class TestARunIsIndependent:
    """Asserted on the arguments the client receives, never on the source text.

    `capture_one_run` is the only place that decides whether run 1 is a second
    attempt or turn k+1 of run 0's conversation, and the difference is invisible
    in the recorded corpus: both produce a `response_text` and a `tool_calls_log`.
    """

    def _patched(self, monkeypatch, turns):
        from tests.evals import capture_responses as cr

        minted: list[str] = []
        calls: list[dict] = []

        monkeypatch.setattr(cr, "CONTROL_DB_SYNC_URL", "")
        monkeypatch.setattr(
            cr, "_get_widget_jwt",
            lambda agent_id, api_key, base_url: minted.append(agent_id) or f"jwt-{len(minted)}",
        )

        def _chat(**kwargs):
            calls.append(kwargs)
            return {
                "response_text": f"answer {len(calls)}",
                "tool_calls_log": [],
                "conversation_id": f"conv-{len(minted)}",
                "job_id": "j",
            }

        monkeypatch.setattr(cr, "_call_chat_and_drain_sse", _chat)
        scenario = {"id": "S-101", "turns": turns}
        return cr, scenario, minted, calls

    def test_each_run_mints_its_own_widget_jwt(self, monkeypatch):
        """The token expires 900s after minting and k runs cross that k times sooner."""
        cr, scenario, minted, calls = self._patched(
            monkeypatch, [{"role": "user", "message": "q"}]
        )

        cr.capture_one_run(scenario, "agent-1", "key", "http://localhost:8000", 300)
        cr.capture_one_run(scenario, "agent-1", "key", "http://localhost:8000", 300)

        assert len(minted) == 2, "one JWT per run, not one shared across the capture"
        assert [c["api_key"] for c in calls] == ["jwt-1", "jwt-2"]

    def test_a_run_starts_a_fresh_conversation(self, monkeypatch):
        """Run 1 continuing run 0 would be turn k+1 of one session, not an attempt."""
        cr, scenario, _minted, calls = self._patched(
            monkeypatch, [{"role": "user", "message": "q"}]
        )

        cr.capture_one_run(scenario, "agent-1", "key", "http://localhost:8000", 300)
        cr.capture_one_run(scenario, "agent-1", "key", "http://localhost:8000", 300)

        assert [c["conversation_id"] for c in calls] == [None, None], (
            "the second run carried run 0's conversation_id, so reliable@k over these "
            "would measure one long session rather than two independent attempts"
        )

    def test_turns_within_one_run_stay_in_one_conversation(self, monkeypatch):
        """The other half: a multi-turn scenario is still one conversation."""
        cr, scenario, _minted, calls = self._patched(
            monkeypatch,
            [{"role": "user", "message": "q1"}, {"role": "user", "message": "q2"}],
        )

        cr.capture_one_run(scenario, "agent-1", "key", "http://localhost:8000", 300)

        assert calls[0]["conversation_id"] is None
        assert calls[1]["conversation_id"] == "conv-1", (
            "session_continuity is a judged dimension; turn 2 must see turn 1"
        )

    def test_the_last_turn_is_the_one_recorded(self, monkeypatch):
        cr, scenario, _minted, _calls = self._patched(
            monkeypatch,
            [{"role": "user", "message": "q1"}, {"role": "user", "message": "q2"}],
        )

        run = cr.capture_one_run(scenario, "agent-1", "key", "http://localhost:8000", 300)
        assert run["response_text"] == "answer 2"
