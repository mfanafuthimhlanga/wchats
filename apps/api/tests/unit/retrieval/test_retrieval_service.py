"""
Unit tests for retrieval_service primitives.

All external dependencies (psycopg2, voyageai, cohere) are mocked so these
tests run without any live DB or API.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import pytest

from app.services.retrieval_service import (
    RetrievalStrategy,
    embed_query,
    vector_search,
    bm25_search,
    rrf_fuse,
    rerank,
    build_trace,
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
    def test_returns_cosine_scored_dicts(self, mock_psycopg2):
        import uuid
        chunk_id = uuid.uuid4()
        doc_id = uuid.uuid4()
        mock_conn = self._make_psycopg2_mock([
            (chunk_id, "some content", doc_id, 0.92, 1),
        ])
        mock_psycopg2.connect.return_value = mock_conn

        results = vector_search("conn://fake", [0.1] * 10, vector_k=5)

        assert len(results) == 1
        r = results[0]
        assert r["chunk_id"] == str(chunk_id)
        assert r["content"] == "some content"
        assert r["document_id"] == str(doc_id)
        assert r["cosine_score"] == 0.92
        assert r["rank"] == 1

    @patch("app.services.retrieval_service.psycopg2")
    def test_connection_closed_in_finally(self, mock_psycopg2):
        mock_conn = self._make_psycopg2_mock([])
        mock_psycopg2.connect.return_value = mock_conn

        vector_search("conn://fake", [0.1], vector_k=3)

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
            vector_search("conn://fake", [0.1], vector_k=3)

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
    def test_returns_bm25_scored_dicts(self, mock_psycopg2):
        import uuid
        chunk_id = uuid.uuid4()
        doc_id = uuid.uuid4()
        mock_conn = self._make_psycopg2_mock([
            (chunk_id, "keyword content", doc_id, 0.45, 1),
        ])
        mock_psycopg2.connect.return_value = mock_conn

        results = bm25_search("conn://fake", "keyword", bm25_k=5)

        assert len(results) == 1
        r = results[0]
        assert r["chunk_id"] == str(chunk_id)
        assert r["content"] == "keyword content"
        assert r["document_id"] == str(doc_id)
        assert r["bm25_score"] == 0.45
        assert r["rank"] == 1

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

    @patch("app.services.retrieval_service.bm25_search")
    @patch("app.services.retrieval_service.vector_search")
    @patch("app.services.retrieval_service.psycopg2")
    def test_returns_three_key_dict(self, mock_psycopg2, mock_vec, mock_bm25):
        import uuid
        cid = uuid.uuid4()
        did = uuid.uuid4()
        mock_conn = self._make_psycopg2_mock([
            (cid, "content", did, 0.032, 0.9, 0.4, 1, 2),
        ])
        mock_psycopg2.connect.return_value = mock_conn
        mock_vec.return_value = [{"chunk_id": str(cid), "content": "c", "document_id": str(did), "cosine_score": 0.9, "rank": 1}]
        mock_bm25.return_value = [{"chunk_id": str(cid), "content": "c", "document_id": str(did), "bm25_score": 0.4, "rank": 1}]

        strategy = RetrievalStrategy()
        result = rrf_fuse("conn://fake", [0.1] * 10, "query", strategy)

        assert "fused" in result
        assert "vector_candidates" in result
        assert "bm25_candidates" in result

    @patch("app.services.retrieval_service.bm25_search")
    @patch("app.services.retrieval_service.vector_search")
    @patch("app.services.retrieval_service.psycopg2")
    def test_fused_row_structure(self, mock_psycopg2, mock_vec, mock_bm25):
        import uuid
        cid = uuid.uuid4()
        did = uuid.uuid4()
        mock_conn = self._make_psycopg2_mock([
            (cid, "content text", did, 0.032522, 0.9, 0.4, 1, 2),
        ])
        mock_psycopg2.connect.return_value = mock_conn
        mock_vec.return_value = []
        mock_bm25.return_value = []

        strategy = RetrievalStrategy()
        result = rrf_fuse("conn://fake", [0.1], "query", strategy)

        assert len(result["fused"]) == 1
        row = result["fused"][0]
        assert row["chunk_id"] == str(cid)
        assert row["content"] == "content text"
        assert row["document_id"] == str(did)
        assert "rrf_score" in row
        assert "cosine_score" in row
        assert "bm25_score" in row
        assert "vector_rank" in row
        assert "bm25_rank" in row

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
        return [
            {
                "chunk_id": f"chunk-{i}",
                "content": f"content {i}",
                "document_id": f"doc-{i}",
                "rrf_score": 0.03 - i * 0.001,
            }
            for i in range(n)
        ]

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
        assert len(result) == 2
        assert result[0]["rerank_score"] == 0.95

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

        assert len(result) == 1
        assert result[0]["rerank_score"] == 0.95

    @patch("app.services.retrieval_service._cohere_rerank")
    @patch("app.services.retrieval_service._get_vo")
    def test_cohere_fallback_on_voyage_exception(self, mock_get_vo, mock_cohere_rerank):
        mock_vo = MagicMock()
        mock_vo.rerank.side_effect = RuntimeError("Voyage API down")
        mock_get_vo.return_value = mock_vo

        cohere_result = [{"chunk_id": "c0", "content": "x", "document_id": "d0", "rerank_score": 0.7}]
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

        scores = [r["rerank_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    @patch("app.services.retrieval_service._get_vo")
    def test_rerank_score_added_to_dict(self, mock_get_vo):
        mock_vo = MagicMock()
        reranking = MagicMock()
        reranking.results = [self._make_reranking_result(0, 0.88)]
        mock_vo.rerank.return_value = reranking
        mock_get_vo.return_value = mock_vo

        candidates = self._make_candidates(1)
        strategy = RetrievalStrategy(final_k=1)

        result = rerank("q", candidates, strategy)

        assert "rerank_score" in result[0]
        assert result[0]["rerank_score"] == 0.88
        # Original keys preserved
        assert result[0]["chunk_id"] == "chunk-0"


# ---------------------------------------------------------------------------
# build_trace — truncation and structure
# ---------------------------------------------------------------------------

class TestBuildTrace:
    def _make_cands(self, n=2, content_len=300):
        return [
            {
                "chunk_id": f"c{i}",
                "content": "x" * content_len,
                "document_id": f"d{i}",
            }
            for i in range(n)
        ]

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
            bm25_candidates=[],
            fused_candidates=[],
            reranked_candidates=[],
        )
        assert len(trace["vector_candidates"][0]["content"]) == 200

    def test_short_content_not_truncated(self):
        trace = build_trace(
            vector_candidates=self._make_cands(1, content_len=50),
            bm25_candidates=[],
            fused_candidates=[],
            reranked_candidates=[],
        )
        assert len(trace["vector_candidates"][0]["content"]) == 50

    def test_custom_max_content(self):
        trace = build_trace(
            vector_candidates=self._make_cands(1, content_len=300),
            bm25_candidates=[],
            fused_candidates=[],
            reranked_candidates=[],
            max_content=100,
        )
        assert len(trace["vector_candidates"][0]["content"]) == 100

    def test_original_candidates_not_mutated(self):
        cands = self._make_cands(1, content_len=400)
        original_content = cands[0]["content"]
        build_trace(
            vector_candidates=cands,
            bm25_candidates=[],
            fused_candidates=[],
            reranked_candidates=[],
        )
        # The original list should be unchanged
        assert cands[0]["content"] == original_content
