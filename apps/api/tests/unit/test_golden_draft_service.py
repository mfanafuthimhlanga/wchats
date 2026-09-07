"""golden_draft_service drafts pairs and checks them in code before anyone sees them (#203).

The model is a double throughout: `tests.model_doubles` builds the forced tool
reply the drafter reads. Nothing here opens a socket.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services import golden_draft_service as svc
from tests.model_doubles import completion, ledger, tool_call

PASSAGE = (
    "Delivery costs R35 for orders under R300 and is free for orders of R300 or more.\n"
    "Same-day delivery is available for orders placed on WhatsApp before 10:00, for an\n"
    "extra R25. We do not deliver on Sundays or public holidays."
)
CITATION = "Delivery costs R35 for orders under R300 and is free for orders of R300 or more."
ANSWER = "Delivery costs R35 for orders under R300 and is free for orders of R300 or more."


def _chunk(document_id: str, ordinal: int, content: str = PASSAGE) -> dict:
    return {
        "chunk_id": f"{document_id}-{ordinal}",
        "document_id": document_id,
        "ordinal": ordinal,
        "content": content,
        "document": f"doc {document_id}",
    }


def _draft(question="How much is delivery?", answer=ANSWER, citation=CITATION):
    return completion(
        tool_calls=[tool_call("submit_golden_draft", {
            "question": question, "reference_answer": answer, "citation": citation,
        })],
        finish_reason="tool_calls",
    )


class TestTheCitationMustBeInTheChunk:
    def test_a_citation_lifted_from_the_chunk_passes(self):
        assert svc.citation_in_chunk(CITATION, PASSAGE)

    def test_whitespace_differences_do_not_fail_it(self):
        assert svc.citation_in_chunk(CITATION.replace(" and ", "\n  and "), PASSAGE)

    def test_a_citation_not_in_the_chunk_fails(self):
        assert not svc.citation_in_chunk(CITATION.replace("R35", "R45"), PASSAGE)

    def test_a_citation_too_short_to_bind_anything_fails(self):
        assert not svc.citation_in_chunk("Delivery costs R35", PASSAGE)
        assert not svc.citation_in_chunk("   ", PASSAGE)


class TestTheAnswerMustBeGroundedInTheChunk:
    def test_an_answer_lifted_from_the_chunk_passes(self):
        assert svc.answer_grounded_in_chunk(ANSWER, PASSAGE)

    def test_an_answer_composed_from_elsewhere_fails(self):
        assert not svc.answer_grounded_in_chunk(
            "Delivery is free everywhere in Gauteng and takes two working days by courier.", PASSAGE
        )

    def test_an_empty_answer_fails(self):
        assert not svc.answer_grounded_in_chunk("", PASSAGE)


class TestCoverageIsByDocument:
    def test_every_document_is_picked_from_before_any_repeats(self):
        rows = [_chunk("a", i) for i in range(6)] + [_chunk("b", i) for i in range(6)] + [_chunk("c", 0)]

        picked = svc.pick_chunks_by_document(rows, n=3)

        assert [p["document_id"] for p in picked] == ["a", "b", "c"]

    def test_the_second_round_comes_from_deeper_in_each_document(self):
        rows = [_chunk("a", i) for i in range(6)] + [_chunk("b", i) for i in range(6)]

        picked = svc.pick_chunks_by_document(rows, n=4)

        assert [(p["document_id"], p["ordinal"]) for p in picked] == [("a", 0), ("b", 0), ("a", 3), ("b", 3)]

    def test_it_stops_at_n_and_skips_a_document_that_ran_out(self):
        rows = [_chunk("a", i) for i in range(3)] + [_chunk("b", 0)]

        picked = svc.pick_chunks_by_document(rows, n=3)

        assert [(p["document_id"], p["ordinal"]) for p in picked] == [("a", 0), ("b", 0), ("a", 1)]

    def test_a_short_document_does_not_shrink_the_pick(self):
        rows = [_chunk("a", i) for i in range(6)] + [_chunk("b", 0)]

        picked = svc.pick_chunks_by_document(rows, n=4)

        assert len(picked) == 4
        assert len({p["chunk_id"] for p in picked}) == 4
        assert [p["document_id"] for p in picked].count("b") == 1

    def test_n_beyond_the_corpus_picks_every_chunk_once(self):
        rows = [_chunk("a", i) for i in range(2)] + [_chunk("b", i) for i in range(2)]

        picked = svc.pick_chunks_by_document(rows, n=30)

        assert sorted(p["chunk_id"] for p in picked) == sorted(r["chunk_id"] for r in rows)

    def test_nothing_from_nothing(self):
        assert svc.pick_chunks_by_document([], n=10) == []
        assert svc.pick_chunks_by_document([_chunk("a", 0)], n=0) == []


class TestOneDraftPerChunk:
    @patch("app.core.model_client.make_client")
    def test_the_drafter_forces_its_tool_and_bills_its_own_purpose(self, mock_factory):
        mock_factory.return_value.chat.completions.create.return_value = _draft()

        draft = svc.draft_pair_from_chunk(_chunk("a", 0), ledger())

        assert draft["question"] == "How much is delivery?"
        assert draft["source_document_id"] == "a"
        assert draft["source_chunk_id"] == "a-0"
        assert draft["source_document"] == "doc a"
        kwargs = mock_factory.return_value.chat.completions.create.call_args.kwargs
        assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "submit_golden_draft"}}
        assert mock_factory.call_args.args[0] == "golden_draft"

    @patch("app.core.model_client.make_client")
    def test_no_tool_call_is_no_draft(self, mock_factory):
        mock_factory.return_value.chat.completions.create.return_value = completion(
            content="I cannot", finish_reason="stop"
        )

        assert svc.draft_pair_from_chunk(_chunk("a", 0), ledger()) is None

    @patch("app.core.model_client.make_client")
    def test_a_reply_with_a_missing_or_non_string_field_is_no_draft(self, mock_factory):
        """The tool reader checks that the arguments are an object, not their types."""
        mock_factory.return_value.chat.completions.create.side_effect = [
            completion(tool_calls=[tool_call("submit_golden_draft", {
                "question": "q", "reference_answer": ANSWER, "citation": None,
            })], finish_reason="tool_calls"),
            completion(tool_calls=[tool_call("submit_golden_draft", {
                "question": "q", "reference_answer": ANSWER,
            })], finish_reason="tool_calls"),
        ]

        assert svc.draft_pair_from_chunk(_chunk("a", 0), ledger()) is None
        assert svc.draft_pair_from_chunk(_chunk("a", 0), ledger()) is None


class TestTheBatchKeepsOnlyVouchedDrafts:
    @patch("app.core.model_client.make_client")
    def test_a_draft_whose_citation_is_not_in_its_chunk_is_dropped(self, mock_factory):
        mock_factory.return_value.chat.completions.create.side_effect = [
            _draft(),
            _draft(citation=CITATION.replace("R35", "R45")),
        ]

        kept, dropped = svc.draft_golden_pairs([_chunk("a", 0), _chunk("b", 0)], ledger())

        assert [k["source_document_id"] for k in kept] == ["a"]
        assert dropped == 1

    @patch("app.core.model_client.make_client")
    def test_a_draft_whose_answer_is_not_in_its_chunk_is_dropped(self, mock_factory):
        mock_factory.return_value.chat.completions.create.side_effect = [
            _draft(answer="Delivery is free everywhere in Gauteng and takes two working days by courier."),
            _draft(),
        ]

        kept, dropped = svc.draft_golden_pairs([_chunk("a", 0), _chunk("b", 0)], ledger())

        assert [k["source_document_id"] for k in kept] == ["b"]
        assert dropped == 1

    @patch("app.core.model_client.make_client")
    def test_a_model_call_that_raises_drops_that_chunk_only(self, mock_factory):
        mock_factory.return_value.chat.completions.create.side_effect = [
            RuntimeError("provider down"),
            _draft(),
        ]

        kept, dropped = svc.draft_golden_pairs([_chunk("a", 0), _chunk("b", 0)], ledger())

        assert [k["source_document_id"] for k in kept] == ["b"]
        assert dropped == 1

    @patch("app.core.model_client.make_client")
    def test_a_check_that_raises_drops_that_chunk_only(self, mock_factory):
        """A malformed reply that slips past the field check must not take the batch down."""
        mock_factory.return_value.chat.completions.create.side_effect = [_draft(), _draft()]
        chunks = [_chunk("a", 0, content=None), _chunk("b", 0)]  # type: ignore[arg-type]

        kept, dropped = svc.draft_golden_pairs(chunks, ledger())

        assert [k["source_document_id"] for k in kept] == ["b"]
        assert dropped == 1

    @patch("app.core.model_client.make_client")
    def test_the_batch_drafts_nothing_from_no_chunks(self, mock_factory):
        assert svc.draft_golden_pairs([], ledger()) == ([], 0)
        assert mock_factory.return_value.chat.completions.create.call_count == 0
