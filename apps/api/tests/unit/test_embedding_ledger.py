"""Every embedding call leaves a priced, attributable ledger row (issue #265).

The claim under test is the gap `.dev/reference/260914-unit-economics.md` measured
on staging: five turns, 31 ledger rows, zero of them embeddings. So the assertions
follow one call from the provider response to the row, to the money, to the rollup.

EVERY EXPECTED FIGURE IS COMPUTED A SECOND WAY, the rule
`tests/unit/test_usage_service.py` states. The money assertions state the
arithmetic as a literal against the fetched tariff rather than reading the figure
back out of the output they are checking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from app.core.model_client import PURPOSE_ROUTES, LedgerContext, UnknownPurpose, route_for
from app.domain.model_call import ModelCall, ModelSource
from app.domain.pricing import UnknownPrice, cost_usd
from app.services.embedding_ledger import (
    EMBED_DOCUMENT,
    EMBED_QUERY,
    EMBEDDING_PURPOSES,
    VOYAGE_PROVIDER,
    purpose_for,
    record_embedding,
)
from app.services.usage_service import LedgerRow, summarise_usage
from tests.model_doubles import AGENT_ID, JOB_ID, TENANT_ID, ledger

#: The two purposes, spelled out here rather than read out of the module, so a
#: renamed constant fails this file. The same rule `EVERY_PURPOSE` follows in
#: tests/unit/test_model_client.py.
EVERY_EMBEDDING_PURPOSE = ["embed_query", "embed_document"]

#: voyage-3, $0.06 per million input tokens, fetched 2026-09-14 from
#: https://docs.voyageai.com/docs/pricing.
VOYAGE_3 = "voyage-3"
VOYAGE_3_USD_PER_MILLION = Decimal("0.06")
TITAN = "amazon.titan-embed-text-v2:0"

MIDDAY_UTC = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

#: An ingest run, which is a different job from the turn `tests.model_doubles` names.
INGEST_JOB_ID = "44444444-4444-4444-4444-444444444444"


def voyage_client(total_tokens: int, vectors: list[list[float]]):
    """A double shaped like the Voyage SDK client `_get_vo()` hands back.

    `EmbeddingsObject` carries `embeddings` and `total_tokens` and nothing else
    (`.venv/Lib/site-packages/voyageai/object/embeddings.py`), so the double
    carries those two and nothing else either.
    """
    client = MagicMock()
    client.embed.return_value = MagicMock(embeddings=vectors, total_tokens=total_tokens)
    return client


def _titan_body(input_tokens: int | None, dim: int = 1024) -> dict:
    """One Titan invoke_model response. No key at all when nothing was reported."""
    import json

    body: dict = {"embedding": [0.1] * dim}
    if input_tokens is not None:
        body["inputTextTokenCount"] = input_tokens
    response_body = MagicMock()
    response_body.read.return_value = json.dumps(body).encode()
    return {"body": response_body}


def bedrock_client(input_tokens: int | None, dim: int = 1024):
    """A double shaped like the boto3 bedrock-runtime client, one body for every call."""
    client = MagicMock()
    client.invoke_model.return_value = _titan_body(input_tokens, dim)
    return client


def _empty_fusion(query: str):
    """The RrfFusion `rrf_fuse` returns when nothing matched."""
    from app.domain.retrieved_context import RetrievedContext
    from app.services.retrieval_service import RrfFusion

    empty = lambda strategy: RetrievedContext(query=query, chunks=(), strategy=strategy)  # noqa: E731
    return RrfFusion(
        fused=empty("rrf"), vector_candidates=empty("vector"), bm25_candidates=empty("bm25")
    )


def embedding_call(
    purpose: str,
    *,
    provider: str = VOYAGE_PROVIDER,
    served_model: str = VOYAGE_3,
    input_tokens: int = 1000,
    job_id: str | None = JOB_ID,
) -> ModelCall:
    """One embedding row as `record_embedding` writes it."""
    return ModelCall(
        purpose=purpose,
        provider=provider,
        requested_model=served_model,
        served_model=served_model,
        model_source=ModelSource.UNREPORTED,
        input_tokens=input_tokens,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        at=MIDDAY_UTC,
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        job_id=job_id,
    )


# ---------------------------------------------------------------------------
# The purposes, and why they are not routes
# ---------------------------------------------------------------------------


class TestTheTwoPurposes:
    def test_the_module_holds_exactly_these_two(self):
        assert sorted(EMBEDDING_PURPOSES) == sorted(EVERY_EMBEDDING_PURPOSE)

    @pytest.mark.parametrize(
        ("input_type", "purpose"), [("query", EMBED_QUERY), ("document", EMBED_DOCUMENT)]
    )
    def test_the_input_type_picks_the_purpose(self, input_type, purpose):
        assert purpose_for(input_type) == purpose

    def test_an_unknown_input_type_raises_rather_than_defaulting(self):
        """Defaulting would file a retrieval call as an ingest one, and telling
        the two apart is the whole reason there are two purposes."""
        with pytest.raises(ValueError, match="No embedding purpose"):
            purpose_for("passage")

    @pytest.mark.parametrize("purpose", EVERY_EMBEDDING_PURPOSE)
    def test_an_embedding_purpose_is_not_a_chat_route(self, purpose):
        """A row in PURPOSE_ROUTES is a chat completion's provider, model and
        effort, and `route_for` feeds `make_client`. An embedding asks a chat
        model nothing, so the table must not answer for it."""
        assert purpose not in PURPOSE_ROUTES
        with pytest.raises(UnknownPurpose):
            route_for(purpose)


# ---------------------------------------------------------------------------
# A retrieval turn
# ---------------------------------------------------------------------------


class TestARetrievalTurnLeavesOneRow:
    def test_one_row_with_the_turns_job_and_the_providers_token_count(self):
        from app.services.retrieval_service import embed_query

        rows: list[ModelCall] = []
        with patch("app.services.retrieval_service.settings") as settings:
            settings.EMBEDDING_PROVIDER = "voyage"
            with patch(
                "app.services.retrieval_service._get_vo",
                return_value=voyage_client(11, [[0.1] * 1024]),
            ):
                vector = embed_query("what is the refund policy?", ledger(rows))

        assert len(vector) == 1024
        assert len(rows) == 1, f"Expected one ledger row, got {len(rows)}"
        row = rows[0]
        assert row.purpose == EMBED_QUERY
        assert row.job_id == JOB_ID
        assert row.agent_id == AGENT_ID
        assert row.tenant_id == TENANT_ID
        assert row.input_tokens == 11, "The count is the provider's total_tokens"
        assert (row.output_tokens, row.cache_read_tokens, row.cache_creation_tokens) == (0, 0, 0)
        assert row.provider == VOYAGE_PROVIDER
        assert row.served_model == VOYAGE_3
        assert row.model_source is ModelSource.UNREPORTED

    def test_query_expansion_records_its_variant_embedding(self, monkeypatch):
        """Driven through `rrf_fuse_with_expansion`, not the seam underneath it.

        That function carried its own copy of the provider branch until #265, and
        a test that called `embed_queries` directly would stay green the day
        somebody put the copy back.
        """
        import app.services.retrieval_service as retrieval_service
        from app.services.retrieval_service import RetrievalStrategy, rrf_fuse_with_expansion

        rows: list[ModelCall] = []
        client = voyage_client(30, [[0.1] * 1024, [0.2] * 1024, [0.3] * 1024])
        strategy = RetrievalStrategy.model_validate({"query_expansion": True})
        monkeypatch.setattr(retrieval_service.settings, "EMBEDDING_PROVIDER", "voyage")
        monkeypatch.setattr(retrieval_service, "_get_vo", lambda: client)
        monkeypatch.setattr(
            retrieval_service, "_expand_query", lambda text, led: ["a", "b", "c"]
        )
        monkeypatch.setattr(
            retrieval_service,
            "rrf_fuse",
            lambda conn, vector, text, strat: _empty_fusion(text),
        )

        rrf_fuse_with_expansion("conn", [0.0] * 1024, "q", strategy, ledger(rows))

        assert [(r.purpose, r.input_tokens) for r in rows] == [(EMBED_QUERY, 30)], (
            "The whole variant set is one Voyage request, so it is one row"
        )

    def test_a_bedrock_query_leaves_one_row_per_invoke(self, monkeypatch):
        """Titan takes one text per invoke_model, so three texts are three
        provider requests and three rows. `calls` means provider requests for
        every other purpose in the rollup and must mean it here too."""
        import app.services.bedrock_embedding_service as bedrock_svc
        from app.services.bedrock_embedding_service import embed_texts

        rows: list[ModelCall] = []
        monkeypatch.setattr(bedrock_svc.settings, "BEDROCK_EMBED_MODEL_ID", TITAN)
        monkeypatch.setattr(bedrock_svc, "_bedrock", bedrock_client(input_tokens=4))

        embed_texts(["a", "b", "c"], "query", ledger(rows))

        assert [(r.purpose, r.input_tokens, r.served_model) for r in rows] == [
            (EMBED_QUERY, 4, TITAN)
        ] * 3


# ---------------------------------------------------------------------------
# An ingest
# ---------------------------------------------------------------------------


class TestAnIngestLeavesOneRowPerBatch:
    def test_two_hundred_chunks_leave_two_embed_document_rows(self):
        """BATCH_SIZE is 128, the Voyage per-request hard limit, so 200 chunks
        are two requests and two rows."""
        from app.services.embedding_service import embed_chunks

        rows: list[ModelCall] = []
        client = MagicMock()
        client.embed.side_effect = [
            MagicMock(embeddings=[[0.1] * 1024] * 128, total_tokens=1280),
            MagicMock(embeddings=[[0.1] * 1024] * 72, total_tokens=720),
        ]
        with patch("app.services.embedding_service.settings") as settings:
            settings.EMBEDDING_PROVIDER = "voyage"
            with patch("app.services.embedding_service._get_vo", return_value=client):
                vectors = embed_chunks(["chunk"] * 200, ledger(rows))

        assert len(vectors) == 200
        assert [(r.purpose, r.input_tokens) for r in rows] == [
            (EMBED_DOCUMENT, 1280),
            (EMBED_DOCUMENT, 720),
        ]
        assert {r.job_id for r in rows} == {JOB_ID}


# ---------------------------------------------------------------------------
# Fail open, and the gap that is not a zero
# ---------------------------------------------------------------------------


class TestRecordingNeverBreaksTheCall:
    def test_a_recorder_that_raises_does_not_reach_the_caller(self):
        """A customer's retrieval does not die because telemetry could not be
        written. The row is lost and `embedding_ledger.record_failed` says so."""
        from app.services.retrieval_service import embed_query

        def explode(call):
            raise RuntimeError("tenant database refused the connection")

        exploding = LedgerContext(tenant_id=TENANT_ID, agent_id=AGENT_ID, job_id=JOB_ID,
                                  recorder=explode)
        with patch("app.services.retrieval_service.settings") as settings:
            settings.EMBEDDING_PROVIDER = "voyage"
            with patch(
                "app.services.retrieval_service._get_vo",
                return_value=voyage_client(11, [[0.4] * 1024]),
            ):
                vector = embed_query("still answers", exploding)

        assert vector == [0.4] * 1024

    def test_the_failure_is_logged_by_name_through_log_failure(self):
        """Fail open means the row is lost, so `embedding_ledger.record_failed`
        is the only thing that says a tenant's spend went unrecorded. A silent
        except would make a quiet ledger indistinguishable from a cheap day."""
        def explode(call):
            raise RuntimeError("tenant database refused the connection")

        exploding = LedgerContext(tenant_id=TENANT_ID, agent_id=AGENT_ID, job_id=JOB_ID,
                                  recorder=explode)
        with capture_logs() as logs:
            record_embedding(
                exploding,
                purpose=EMBED_QUERY,
                provider=VOYAGE_PROVIDER,
                model=VOYAGE_3,
                input_tokens=7,
            )

        failures = [entry for entry in logs if entry["event"] == "embedding_ledger.record_failed"]
        assert len(failures) == 1, f"Expected one record_failed line, got {logs}"
        assert failures[0]["error_type"] == "RuntimeError"
        assert failures[0]["job_id"] == JOB_ID
        assert failures[0]["purpose"] == EMBED_QUERY

    def test_a_provider_reporting_no_token_count_writes_no_row(self):
        """A row of zero would read as a free call in every rollup. The gap is
        logged by name instead, the treatment model_client gives an unreadable
        usage block."""
        from app.services.bedrock_embedding_service import embed_texts

        rows: list[ModelCall] = []
        with patch(
            "app.services.bedrock_embedding_service._bedrock",
            bedrock_client(input_tokens=None),
        ):
            vectors = embed_texts(["a"], "document", ledger(rows))

        assert len(vectors) == 1, "The embedding still succeeds"
        assert rows == [], "No row, rather than a row claiming zero tokens"

    def test_one_unreported_text_costs_only_its_own_row(self, monkeypatch):
        """Recording per invoke means a silent response loses one row, not the
        batch's whole figure."""
        import app.services.bedrock_embedding_service as bedrock_svc
        from app.services.bedrock_embedding_service import embed_texts

        rows: list[ModelCall] = []
        bodies = [_titan_body(4), _titan_body(None), _titan_body(4)]
        client = MagicMock()
        client.invoke_model.side_effect = bodies
        monkeypatch.setattr(bedrock_svc, "_bedrock", client)

        embed_texts(["a", "b", "c"], "document", ledger(rows))

        assert [r.input_tokens for r in rows] == [4, 4], (
            "Two responses reported a count and one did not"
        )

    def test_an_unknown_input_type_is_refused_before_any_provider_call(self, monkeypatch):
        """Defaulting would file retrieval spend as ingest spend."""
        import app.services.bedrock_embedding_service as bedrock_svc
        from app.services.bedrock_embedding_service import embed_texts

        client = MagicMock()
        monkeypatch.setattr(bedrock_svc, "_bedrock", client)

        with pytest.raises(ValueError, match="No embedding purpose"):
            embed_texts(["a"], "passage", ledger([]))

        assert client.invoke_model.call_count == 0


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


class TestPricingAnEmbeddingCall:
    def test_a_voyage_call_prices_at_the_fetched_tariff(self):
        usd, version = cost_usd(embedding_call(EMBED_QUERY, input_tokens=1_000_000))

        assert usd == VOYAGE_3_USD_PER_MILLION
        assert version == "2026-08-23.1"

    def test_a_smaller_call_prices_exactly_and_is_not_rounded(self):
        usd, _ = cost_usd(embedding_call(EMBED_DOCUMENT, input_tokens=1500))

        assert usd == Decimal("0.00009"), "1500 tokens at $0.06 per million"

    def test_titan_raises_rather_than_reporting_a_free_call(self):
        """No Titan Text Embeddings tariff rendered on
        https://aws.amazon.com/bedrock/pricing/ on 2026-09-14, so the book
        carries no row and every rollup names the gap."""
        call = embedding_call(EMBED_DOCUMENT, provider="bedrock", served_model=TITAN)

        with pytest.raises(UnknownPrice, match=TITAN):
            cost_usd(call)


# ---------------------------------------------------------------------------
# The rollup
# ---------------------------------------------------------------------------


class TestTheRollupSeparatesRetrievalFromIngest:
    def test_both_purposes_reach_by_purpose_with_their_own_money(self):
        rows = [
            LedgerRow(embedding_call(EMBED_QUERY, input_tokens=1_000_000), "conv-1"),
            LedgerRow(embedding_call(EMBED_DOCUMENT, input_tokens=2_000_000), None),
        ]

        summary = summarise_usage(rows, window_days=7)

        by_purpose = {row["purpose"]: row for row in summary["by_purpose"]}
        assert sorted(by_purpose) == sorted(EVERY_EMBEDDING_PURPOSE)
        assert by_purpose[EMBED_QUERY]["input_tokens"] == 1_000_000
        assert by_purpose[EMBED_QUERY]["cost_usd"] == float(VOYAGE_3_USD_PER_MILLION)
        assert by_purpose[EMBED_DOCUMENT]["cost_usd"] == float(2 * VOYAGE_3_USD_PER_MILLION)
        assert summary["total"]["unpriced_calls"] == 0

    def test_the_query_row_counts_into_the_turn_and_the_document_row_does_not(self):
        """`turns` is the calls whose job was a turn, so a turn's retrieval
        spend is in `cost_per_turn_usd` and an ingest's is not.

        The ingest row carries its OWN job id. Sharing the turn's would leave
        `turns.count` at 1 whether or not the grouping worked, and the figure
        would pass on the strength of the fixture.
        """
        rows = [
            LedgerRow(embedding_call(EMBED_QUERY, input_tokens=1_000_000), "conv-1"),
            LedgerRow(
                embedding_call(EMBED_DOCUMENT, input_tokens=2_000_000, job_id=INGEST_JOB_ID),
                None,
            ),
        ]

        summary = summarise_usage(rows, window_days=7)

        assert summary["turns"]["count"] == 1
        assert summary["turns"]["cost_per_turn_usd"] == float(VOYAGE_3_USD_PER_MILLION)
        assert summary["total"]["calls"] == 2, "The ingest row is still in the window"

    def test_an_unpriced_titan_row_nulls_the_group_and_names_the_model(self):
        rows = [
            LedgerRow(embedding_call(EMBED_QUERY, input_tokens=1_000_000), "conv-1"),
            LedgerRow(
                embedding_call(
                    EMBED_DOCUMENT, provider="bedrock", served_model=TITAN, input_tokens=5
                ),
                None,
            ),
        ]

        summary = summarise_usage(rows, window_days=7)

        assert summary["total"]["cost_usd"] is None
        assert summary["total"]["unpriced_calls"] == 1
        assert summary["total"]["price_gaps"] == [
            {"provider": "bedrock", "served_model": TITAN, "call_count": 1}
        ]


# ---------------------------------------------------------------------------
# The row itself
# ---------------------------------------------------------------------------


class TestTheRowRecordEmbeddingWrites:
    def test_the_instant_is_injectable_so_a_row_lands_in_a_known_window(self):
        rows: list[ModelCall] = []
        record_embedding(
            ledger(rows),
            purpose=EMBED_QUERY,
            provider=VOYAGE_PROVIDER,
            model=VOYAGE_3,
            input_tokens=7,
            at=MIDDAY_UTC,
        )

        assert rows[0].at == MIDDAY_UTC


# ---------------------------------------------------------------------------
# The eval path
# ---------------------------------------------------------------------------


class TestTheEvalScorerRecordsItsEmbeddings:
    def test_embed_text_leaves_one_row_billed_to_the_run(self, monkeypatch):
        """AnswerRelevancy embeds through `_VoyageRagasEmbedding`, which is a
        Voyage call per scored sample and was the third unrecorded path.

        The real class, not the fake `tests/unit/test_eval_service.py` installs.
        A fake that only stored the ledger proved the argument arrived and
        nothing about the row.
        """
        import app.services.eval_service as eval_service

        rows: list[ModelCall] = []
        monkeypatch.setattr(
            eval_service, "_get_vo", lambda: voyage_client(21, [[0.7] * 1024])
        )

        embedder = eval_service._VoyageRagasEmbedding(ledger(rows))
        vector = embedder.embed_text("did that answer the question?")

        assert vector == [0.7] * 1024
        assert len(rows) == 1, f"Expected one row per embed_text call, got {len(rows)}"
        assert rows[0].purpose == EMBED_QUERY
        assert rows[0].job_id == JOB_ID, "billed to the eval run, like the judges beside it"
        assert rows[0].input_tokens == 21
        assert rows[0].provider == VOYAGE_PROVIDER
