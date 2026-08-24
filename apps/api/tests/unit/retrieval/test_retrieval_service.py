"""
Unit tests for retrieval_service primitives.

All external dependencies (psycopg2, voyageai, cohere) are mocked so these
tests run without any live DB or API.

Ticket #44: the search, fusion and rerank functions return RetrievedContext, so
the assertions read fields rather than dict keys. Each engine's own number
arrives as `chunk.score` under the `strategy` that names the engine, and
`chunk.rank` is its 1-based position in that ranking.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.domain.retrieved_context import RetrievedChunk, RetrievedContext
from app.services.retrieval_service import (
    RetrievalStrategy,
    bm25_search,
    build_trace,
    embed_query,
    rerank,
    rrf_fuse,
    vector_search,
    verified_qa_lookup,
)

# ---------------------------------------------------------------------------
# RetrievalStrategy defaults
# ---------------------------------------------------------------------------

class TestRetrievalStrategy:
    def test_defaults_from_empty_dict(self):
        s = RetrievalStrategy.model_validate({})
        assert s.vector_k == 20
        assert s.bm25_k == 20
        assert s.final_k == 5
        assert s.rerank_threshold == 0.0
        assert s.query_expansion is False
        assert s.metadata_filters == []

    def test_partial_override(self):
        s = RetrievalStrategy.model_validate({"vector_k": 10, "final_k": 3})
        assert s.vector_k == 10
        assert s.bm25_k == 20  # default unchanged
        assert s.final_k == 3

    def test_extra_fields_ignored(self):
        # extra="ignore" — unknown keys must not raise
        s = RetrievalStrategy.model_validate({"unknown_future_field": "x", "vector_k": 7})
        assert s.vector_k == 7


# ---------------------------------------------------------------------------
# embed_query — input_type must be "query" not "document"
# ---------------------------------------------------------------------------

class TestEmbedQuery:
    """Voyage-branch semantics of the P13-02 provider seam.

    EMBEDDING_PROVIDER defaults to "bedrock" (config.py), so these tests MUST pin
    the provider to "voyage" — without the pin, embed_query takes the Bedrock
    branch, ignores the _get_vo patch, and issues a real boto3 InvokeModel call.
    Bedrock-branch routing is covered by test_embedding_bedrock.py (tests 8/9).
    """

    @pytest.fixture(autouse=True)
    def _force_voyage_provider(self, monkeypatch):
        import app.services.retrieval_service as retrieval_service
        monkeypatch.setattr(retrieval_service.settings, "EMBEDDING_PROVIDER", "voyage")

    @patch("app.services.retrieval_service._get_vo")
    def test_uses_query_input_type(self, mock_get_vo):
        mock_vo = MagicMock()
        mock_vo.embed.return_value.embeddings = [[0.1] * 1024]
        mock_get_vo.return_value = mock_vo

        result = embed_query("what is the refund policy?")

        mock_vo.embed.assert_called_once_with(
            ["what is the refund policy?"],
            model="voyage-3",
            input_type="query",
        )
        assert result == [0.1] * 1024

    @patch("app.services.retrieval_service._get_vo")
    def test_returns_first_embedding(self, mock_get_vo):
        vec_a = [0.1] * 1024
        vec_b = [0.9] * 1024
        mock_vo = MagicMock()
        mock_vo.embed.return_value.embeddings = [vec_a, vec_b]
        mock_get_vo.return_value = mock_vo

        result = embed_query("query")
        assert result == vec_a  # always index [0]


# ---------------------------------------------------------------------------
# vector_search — SQL shape + psycopg2 pattern
# ---------------------------------------------------------------------------

class TestVectorSearch:
    def _make_psycopg2_mock(self, rows):
        """Return a mock psycopg2 connection that yields the given rows."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    @patch("app.services.retrieval_service.psycopg2")
    def test_returns_a_vector_context(self, mock_psycopg2):
        import uuid
        chunk_id = uuid.uuid4()
        doc_id = uuid.uuid4()
        mock_conn = self._make_psycopg2_mock([
            (chunk_id, "some content", doc_id, 0.92, 1),
        ])
        mock_psycopg2.connect.return_value = mock_conn

        context = vector_search("conn://fake", [0.1] * 10, 5, "refund policy")

        assert isinstance(context, RetrievedContext)
        assert context.query == "refund policy"
        assert context.strategy == "vector"
        assert len(context.chunks) == 1
        chunk = context.chunks[0]
        assert chunk.chunk_id == str(chunk_id)
        assert chunk.content == "some content"
        assert chunk.document_id == str(doc_id)
        assert chunk.score == 0.92  # the cosine score, under the one name
        assert chunk.rank == 1

    @patch("app.services.retrieval_service.psycopg2")
    def test_connection_closed_in_finally(self, mock_psycopg2):
        mock_conn = self._make_psycopg2_mock([])
        mock_psycopg2.connect.return_value = mock_conn

        vector_search("conn://fake", [0.1], 3, "q")

        mock_conn.close.assert_called_once()

    @patch("app.services.retrieval_service.psycopg2")
    def test_connection_closed_on_exception(self, mock_psycopg2):
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.execute.side_effect = RuntimeError("DB error")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_psycopg2.connect.return_value = mock_conn

        with pytest.raises(RuntimeError):
            vector_search("conn://fake", [0.1], 3, "q")

        mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# bm25_search — uses tsvector ts_rank_cd, returns bm25_score
# ---------------------------------------------------------------------------

class TestBM25Search:
    def _make_psycopg2_mock(self, rows):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    @patch("app.services.retrieval_service.psycopg2")
    def test_returns_a_bm25_context(self, mock_psycopg2):
        import uuid
        chunk_id = uuid.uuid4()
        doc_id = uuid.uuid4()
        mock_conn = self._make_psycopg2_mock([
            (chunk_id, "keyword content", doc_id, 0.45, 1),
        ])
        mock_psycopg2.connect.return_value = mock_conn

        context = bm25_search("conn://fake", "keyword", bm25_k=5)

        assert isinstance(context, RetrievedContext)
        assert context.query == "keyword"
        assert context.strategy == "bm25"
        assert len(context.chunks) == 1
        chunk = context.chunks[0]
        assert chunk.chunk_id == str(chunk_id)
        assert chunk.content == "keyword content"
        assert chunk.document_id == str(doc_id)
        assert chunk.score == 0.45  # the ts_rank_cd score, under the one name
        assert chunk.rank == 1

    @patch("app.services.retrieval_service.psycopg2")
    def test_connection_closed_in_finally(self, mock_psycopg2):
        mock_conn = self._make_psycopg2_mock([])
        mock_psycopg2.connect.return_value = mock_conn

        bm25_search("conn://fake", "query", bm25_k=3)

        mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# rrf_fuse — returns dict with 3 keys; RRF math via full CTE mock
# ---------------------------------------------------------------------------

class TestRRFFuse:
    def _make_psycopg2_mock(self, rows):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    def _empty(self, strategy: str) -> RetrievedContext:
        return RetrievedContext(query="query", chunks=(), strategy=strategy)

    @patch("app.services.retrieval_service.bm25_search")
    @patch("app.services.retrieval_service.vector_search")
    @patch("app.services.retrieval_service.psycopg2")
    def test_returns_three_contexts_one_per_engine(self, mock_psycopg2, mock_vec, mock_bm25):
        import uuid
        cid = uuid.uuid4()
        did = uuid.uuid4()
        mock_conn = self._make_psycopg2_mock([
            (cid, "content", did, 0.032, 0.9, 0.4, 1, 2),
        ])
        mock_psycopg2.connect.return_value = mock_conn
        mock_vec.return_value = RetrievedContext(
            query="query",
            chunks=(RetrievedChunk(str(cid), str(did), "c", 0.9, 1),),
            strategy="vector",
        )
        mock_bm25.return_value = RetrievedContext(
            query="query",
            chunks=(RetrievedChunk(str(cid), str(did), "c", 0.4, 1),),
            strategy="bm25",
        )

        strategy = RetrievalStrategy()
        result = rrf_fuse("conn://fake", [0.1] * 10, "query", strategy)

        assert set(result) == {"fused", "vector_candidates", "bm25_candidates"}
        assert result["fused"].strategy == "rrf"
        assert result["vector_candidates"].strategy == "vector"
        assert result["bm25_candidates"].strategy == "bm25"
        assert result["vector_candidates"].chunks[0].score == 0.9
        assert result["bm25_candidates"].chunks[0].score == 0.4

    @patch("app.services.retrieval_service.bm25_search")
    @patch("app.services.retrieval_service.vector_search")
    @patch("app.services.retrieval_service.psycopg2")
    def test_fused_chunk_carries_the_rrf_score_and_its_position(
        self, mock_psycopg2, mock_vec, mock_bm25
    ):
        """The per-engine columns stay in the SQL; the chunk carries one score."""
        import uuid
        cid = uuid.uuid4()
        did = uuid.uuid4()
        mock_conn = self._make_psycopg2_mock([
            (cid, "content text", did, 0.032522, 0.9, 0.4, 1, 2),
            (uuid.uuid4(), "second", did, 0.016393, None, 0.2, None, 1),
        ])
        mock_psycopg2.connect.return_value = mock_conn
        mock_vec.return_value = self._empty("vector")
        mock_bm25.return_value = self._empty("bm25")

        strategy = RetrievalStrategy()
        result = rrf_fuse("conn://fake", [0.1], "query", strategy)

        fused = result["fused"]
        assert fused.query == "query"
        assert len(fused.chunks) == 2
        assert fused.chunks[0].chunk_id == str(cid)
        assert fused.chunks[0].content == "content text"
        assert fused.chunks[0].document_id == str(did)
        assert fused.chunks[0].score == 0.032522
        assert [chunk.rank for chunk in fused.chunks] == [1, 2]

    @patch("app.services.retrieval_service.bm25_search")
    @patch("app.services.retrieval_service.vector_search")
    @patch("app.services.retrieval_service.psycopg2")
    def test_rrf_math_k60_formula(self, mock_psycopg2, mock_vec, mock_bm25):
        """Verify the RRF formula constant k=60 appears in the SQL (via source check)."""
        import inspect

        import app.services.retrieval_service as rs_module
        src = inspect.getsource(rs_module)
        # k=60 must appear as 60.0 in the SQL literal (not a parameter)
        assert "60.0" in src
        # The parameter dict must NOT contain a key named 'k' for the RRF constant
        assert '"k"' not in src.replace("final_k", "").replace("vector_k", "").replace("bm25_k", "")


# ---------------------------------------------------------------------------
# rerank — Voyage primary, Cohere fallback, threshold filtering
# ---------------------------------------------------------------------------

class TestRerank:
    def _make_candidates(self, n=3):
        return RetrievedContext(
            query="query",
            chunks=tuple(
                RetrievedChunk(
                    chunk_id=f"chunk-{i}",
                    document_id=f"doc-{i}",
                    content=f"content {i}",
                    score=0.03 - i * 0.001,
                    rank=i + 1,
                )
                for i in range(n)
            ),
            strategy="rrf",
        )

    def _make_reranking_result(self, index: int, score: float):
        r = MagicMock()
        r.index = index
        r.relevance_score = score
        return r

    @patch("app.services.retrieval_service._get_vo")
    def test_voyage_rerank_called_with_correct_args(self, mock_get_vo):
        mock_vo = MagicMock()
        reranking = MagicMock()
        reranking.results = [
            self._make_reranking_result(0, 0.95),
            self._make_reranking_result(1, 0.80),
        ]
        mock_vo.rerank.return_value = reranking
        mock_get_vo.return_value = mock_vo

        candidates = self._make_candidates(2)
        strategy = RetrievalStrategy(final_k=2, rerank_threshold=0.0)

        result = rerank("my query", candidates, strategy)

        mock_vo.rerank.assert_called_once_with(
            query="my query",
            documents=["content 0", "content 1"],
            model="rerank-2",
            top_k=2,
            truncation=True,
        )
        assert result.strategy == "rerank"
        assert result.query == "my query"
        assert len(result.chunks) == 2
        assert result.chunks[0].score == 0.95

    @patch("app.services.retrieval_service._get_vo")
    def test_rerank_threshold_filters_results(self, mock_get_vo):
        mock_vo = MagicMock()
        reranking = MagicMock()
        reranking.results = [
            self._make_reranking_result(0, 0.95),
            self._make_reranking_result(1, 0.30),  # below threshold
        ]
        mock_vo.rerank.return_value = reranking
        mock_get_vo.return_value = mock_vo

        candidates = self._make_candidates(2)
        strategy = RetrievalStrategy(final_k=2, rerank_threshold=0.5)

        result = rerank("query", candidates, strategy)

        assert len(result.chunks) == 1
        assert result.chunks[0].score == 0.95

    @patch("app.services.retrieval_service._cohere_rerank")
    @patch("app.services.retrieval_service._get_vo")
    def test_cohere_fallback_on_voyage_exception(self, mock_get_vo, mock_cohere_rerank):
        mock_vo = MagicMock()
        mock_vo.rerank.side_effect = RuntimeError("Voyage API down")
        mock_get_vo.return_value = mock_vo

        cohere_result = RetrievedContext(
            query="query",
            chunks=(RetrievedChunk("c0", "d0", "x", 0.7, 1),),
            strategy="rerank",
        )
        mock_cohere_rerank.return_value = cohere_result

        candidates = self._make_candidates(2)
        strategy = RetrievalStrategy()

        result = rerank("query", candidates, strategy)

        mock_cohere_rerank.assert_called_once()
        assert result == cohere_result

    @patch("app.services.retrieval_service._get_vo")
    def test_result_sorted_descending_by_rerank_score(self, mock_get_vo):
        mock_vo = MagicMock()
        reranking = MagicMock()
        # Return in reverse order to confirm sort
        reranking.results = [
            self._make_reranking_result(2, 0.70),
            self._make_reranking_result(0, 0.95),
            self._make_reranking_result(1, 0.80),
        ]
        mock_vo.rerank.return_value = reranking
        mock_get_vo.return_value = mock_vo

        candidates = self._make_candidates(3)
        strategy = RetrievalStrategy(final_k=3, rerank_threshold=0.0)

        result = rerank("query", candidates, strategy)

        scores = [chunk.score for chunk in result.chunks]
        assert scores == sorted(scores, reverse=True)
        assert [chunk.rank for chunk in result.chunks] == [1, 2, 3], (
            "rank is the position in the reranked order, not the position it arrived in"
        )

    @patch("app.services.retrieval_service._get_vo")
    def test_the_rerank_score_replaces_the_fusion_score(self, mock_get_vo):
        mock_vo = MagicMock()
        reranking = MagicMock()
        reranking.results = [self._make_reranking_result(0, 0.88)]
        mock_vo.rerank.return_value = reranking
        mock_get_vo.return_value = mock_vo

        candidates = self._make_candidates(1)
        strategy = RetrievalStrategy(final_k=1)

        result = rerank("q", candidates, strategy)

        assert result.chunks[0].score == 0.88
        # Identity and text carried through from the fused candidate.
        assert result.chunks[0].chunk_id == "chunk-0"
        assert result.chunks[0].document_id == "doc-0"
        assert result.chunks[0].content == "content 0"

    @patch("app.services.retrieval_service._get_vo")
    def test_the_candidates_passed_in_are_not_changed(self, mock_get_vo):
        """The context is frozen, so reranking builds a new one."""
        mock_vo = MagicMock()
        reranking = MagicMock()
        reranking.results = [self._make_reranking_result(0, 0.88)]
        mock_vo.rerank.return_value = reranking
        mock_get_vo.return_value = mock_vo

        candidates = self._make_candidates(1)
        rerank("q", candidates, RetrievalStrategy(final_k=1))

        assert candidates.chunks[0].score == 0.03
        assert candidates.strategy == "rrf"


# ---------------------------------------------------------------------------
# build_trace — truncation and structure
# ---------------------------------------------------------------------------

class TestBuildTrace:
    def _make_cands(self, n=2, content_len=300):
        return RetrievedContext(
            query="query",
            chunks=tuple(
                RetrievedChunk(
                    chunk_id=f"c{i}",
                    document_id=f"d{i}",
                    content="x" * content_len,
                    score=0.5,
                    rank=i + 1,
                )
                for i in range(n)
            ),
            strategy="rrf",
        )

    def _empty(self):
        return RetrievedContext(query="query", chunks=(), strategy="rrf")

    def test_returns_four_key_dict(self):
        trace = build_trace(
            vector_candidates=self._make_cands(2),
            bm25_candidates=self._make_cands(2),
            fused_candidates=self._make_cands(1),
            reranked_candidates=self._make_cands(1),
        )
        assert set(trace.keys()) == {
            "vector_candidates", "bm25_candidates",
            "fused_candidates", "reranked_candidates",
        }

    def test_content_truncated_to_200_chars(self):
        trace = build_trace(
            vector_candidates=self._make_cands(1, content_len=500),
            bm25_candidates=self._empty(),
            fused_candidates=self._empty(),
            reranked_candidates=self._empty(),
        )
        assert len(trace["vector_candidates"][0]["content"]) == 200

    def test_short_content_not_truncated(self):
        trace = build_trace(
            vector_candidates=self._make_cands(1, content_len=50),
            bm25_candidates=self._empty(),
            fused_candidates=self._empty(),
            reranked_candidates=self._empty(),
        )
        assert len(trace["vector_candidates"][0]["content"]) == 50

    def test_custom_max_content(self):
        trace = build_trace(
            vector_candidates=self._make_cands(1, content_len=300),
            bm25_candidates=self._empty(),
            fused_candidates=self._empty(),
            reranked_candidates=self._empty(),
            max_content=100,
        )
        assert len(trace["vector_candidates"][0]["content"]) == 100

    def test_a_trace_row_carries_the_chunk_fields(self):
        trace = build_trace(
            vector_candidates=self._make_cands(1, content_len=10),
            bm25_candidates=self._empty(),
            fused_candidates=self._empty(),
            reranked_candidates=self._empty(),
        )
        assert trace["vector_candidates"][0] == {
            "chunk_id": "c0",
            "document_id": "d0",
            "content": "x" * 10,
            "score": 0.5,
            "rank": 1,
        }

    def test_original_candidates_not_mutated(self):
        cands = self._make_cands(1, content_len=400)
        original_content = cands.chunks[0].content
        build_trace(
            vector_candidates=cands,
            bm25_candidates=self._empty(),
            fused_candidates=self._empty(),
            reranked_candidates=self._empty(),
        )
        # Truncation writes a copy; the context it read stays whole.
        assert cands.chunks[0].content == original_content


# ---------------------------------------------------------------------------
# verified_qa_lookup — D-24/D-25/D-26/D-27
# ---------------------------------------------------------------------------

class TestVerifiedQALookup:
    """Unit tests for verified_qa_lookup (D-24 through D-27).

    All DB interactions mocked via psycopg2 patch. Tests validate:
      - HIT path: correct SELECT + UPDATE executed; dict returned with required keys
      - MISS path: returns None without executing UPDATE
      - psycopg2 connection closed in finally block on both hit and miss
      - str() cast applied to query_vector (::vector cast pattern)
    """

    def _make_psycopg2_mock(self, fetchone_return):
        """Return a mock psycopg2 connection that yields the given fetchone result."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = fetchone_return
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn, mock_cur

    @patch("app.services.retrieval_service.psycopg2")
    def test_hit_returns_dict_with_required_keys(self, mock_psycopg2):
        """D-26: cache hit returns dict with answer, citations, similarity, source."""
        import uuid
        row_id = uuid.uuid4()
        mock_conn, mock_cur = self._make_psycopg2_mock(
            fetchone_return=(row_id, "The answer text.", [{"doc": "x"}], 0.97)
        )
        mock_psycopg2.connect.return_value = mock_conn

        result = verified_qa_lookup("conn://fake", [0.1] * 1024, threshold=0.93)

        assert result is not None
        assert result["answer"] == "The answer text."
        assert result["citations"] == [{"doc": "x"}]
        assert result["similarity"] == 0.97
        assert result["source"] == "verified_qa_cache"

    @patch("app.services.retrieval_service.psycopg2")
    def test_hit_updates_last_used_at_and_use_count(self, mock_psycopg2):
        """D-26: UPDATE executed after hit to bump last_used_at + use_count."""
        import uuid
        row_id = uuid.uuid4()
        mock_conn, mock_cur = self._make_psycopg2_mock(
            fetchone_return=(row_id, "answer", [], 0.95)
        )
        mock_psycopg2.connect.return_value = mock_conn

        verified_qa_lookup("conn://fake", [0.1] * 1024, threshold=0.93)

        # Should have called execute twice: once for SELECT, once for UPDATE
        assert mock_cur.execute.call_count == 2
        # The second call must be the UPDATE
        second_call_sql = mock_cur.execute.call_args_list[1][0][0]
        assert "last_used_at" in second_call_sql
        assert "use_count = use_count + 1" in second_call_sql
        assert "WHERE id = %(row_id)s" in second_call_sql

    @patch("app.services.retrieval_service.psycopg2")
    def test_hit_commits_transaction(self, mock_psycopg2):
        """D-26: connection.commit() called after hit to persist the UPDATE."""
        import uuid
        mock_conn, _ = self._make_psycopg2_mock(
            fetchone_return=(uuid.uuid4(), "answer", [], 0.95)
        )
        mock_psycopg2.connect.return_value = mock_conn

        verified_qa_lookup("conn://fake", [0.1] * 1024, threshold=0.93)

        mock_conn.commit.assert_called_once()

    @patch("app.services.retrieval_service.psycopg2")
    def test_miss_returns_none(self, mock_psycopg2):
        """D-27: cache miss returns None — falls through to hybrid search."""
        mock_conn, _ = self._make_psycopg2_mock(fetchone_return=None)
        mock_psycopg2.connect.return_value = mock_conn

        result = verified_qa_lookup("conn://fake", [0.1] * 1024, threshold=0.93)

        assert result is None

    @patch("app.services.retrieval_service.psycopg2")
    def test_miss_does_not_execute_update(self, mock_psycopg2):
        """D-27: UPDATE must NOT be called when no row matches (miss path)."""
        mock_conn, mock_cur = self._make_psycopg2_mock(fetchone_return=None)
        mock_psycopg2.connect.return_value = mock_conn

        verified_qa_lookup("conn://fake", [0.1] * 1024, threshold=0.93)

        # Only the SELECT should have been called (once), never the UPDATE
        assert mock_cur.execute.call_count == 1

    @patch("app.services.retrieval_service.psycopg2")
    def test_connection_closed_in_finally_on_hit(self, mock_psycopg2):
        """psycopg2 try/finally/close pattern — connection closed after hit."""
        import uuid
        mock_conn, _ = self._make_psycopg2_mock(
            fetchone_return=(uuid.uuid4(), "answer", [], 0.95)
        )
        mock_psycopg2.connect.return_value = mock_conn

        verified_qa_lookup("conn://fake", [0.1] * 1024, threshold=0.93)

        mock_conn.close.assert_called_once()

    @patch("app.services.retrieval_service.psycopg2")
    def test_connection_closed_in_finally_on_miss(self, mock_psycopg2):
        """psycopg2 try/finally/close pattern — connection closed after miss."""
        mock_conn, _ = self._make_psycopg2_mock(fetchone_return=None)
        mock_psycopg2.connect.return_value = mock_conn

        verified_qa_lookup("conn://fake", [0.1] * 1024, threshold=0.93)

        mock_conn.close.assert_called_once()

    @patch("app.services.retrieval_service.psycopg2")
    def test_connection_closed_on_exception(self, mock_psycopg2):
        """psycopg2 finally block — connection closed even when execute raises."""
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.execute.side_effect = RuntimeError("DB error")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_psycopg2.connect.return_value = mock_conn

        with pytest.raises(RuntimeError):
            verified_qa_lookup("conn://fake", [0.1] * 1024, threshold=0.93)

        mock_conn.close.assert_called_once()

    @patch("app.services.retrieval_service.psycopg2")
    def test_query_vector_stringified_for_cast(self, mock_psycopg2):
        """D-25: query_vector passed as str(query_vector) for ::vector cast pattern."""
        import uuid
        query_vec = [0.1, 0.2, 0.3]
        mock_conn, mock_cur = self._make_psycopg2_mock(
            fetchone_return=(uuid.uuid4(), "answer", [], 0.94)
        )
        mock_psycopg2.connect.return_value = mock_conn

        verified_qa_lookup("conn://fake", query_vec, threshold=0.93)

        # The first execute call (SELECT) should have "qv" param as str(query_vec)
        first_call_params = mock_cur.execute.call_args_list[0][0][1]
        assert first_call_params["qv"] == str(query_vec)
        assert first_call_params["threshold"] == 0.93

    @patch("app.services.retrieval_service.psycopg2")
    def test_similarity_returned_as_float(self, mock_psycopg2):
        """similarity must be float in returned dict (not Decimal or other DB type)."""
        import uuid
        from decimal import Decimal
        # Simulate psycopg2 returning a Decimal from NUMERIC column
        mock_conn, _ = self._make_psycopg2_mock(
            fetchone_return=(uuid.uuid4(), "answer", [], Decimal("0.9512"))
        )
        mock_psycopg2.connect.return_value = mock_conn

        result = verified_qa_lookup("conn://fake", [0.1] * 1024, threshold=0.93)

        assert isinstance(result["similarity"], float)
        assert abs(result["similarity"] - 0.9512) < 1e-6

    @patch("app.services.retrieval_service.psycopg2")
    def test_lookup_sql_filters_invalidated_at_null(self, mock_psycopg2):
        """D-25: SELECT must filter WHERE invalidated_at IS NULL."""
        mock_conn, mock_cur = self._make_psycopg2_mock(fetchone_return=None)
        mock_psycopg2.connect.return_value = mock_conn

        verified_qa_lookup("conn://fake", [0.1], threshold=0.93)

        first_call_sql = mock_cur.execute.call_args_list[0][0][0]
        assert "invalidated_at IS NULL" in first_call_sql
