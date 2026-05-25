"""Wave-0 xfail stubs for app.services.strategy_service — M9 Retrieval Strategy Synthesis.

All tests are marked xfail(strict=True) — they are expected to fail until
implemented in Phase 09-03. A passing test in this file would itself be a
test-suite failure (strict mode).

Coverage targets (to be wired in 09-03):
    test_corpus_signals_shape              — _fetch_corpus_signals_sync returns correct keys/types
    test_strategy_validate_string_inputs   — run_strategist tolerates non-numeric corpus signals
    test_run_strategist_calls_asyncio_run  — asyncio.run is called with a wait_for coroutine
    test_expand_query_returns_three        — _expand_query returns [original] + 2 variants
    test_expansion_calls_rrf_fuse_per_variant — rrf_fuse called once per query variant
"""

import pytest


# ---------------------------------------------------------------------------
# Corpus signal collection
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")
def test_corpus_signals_shape():
    """_fetch_corpus_signals_sync returns a dict with all required keys and correct types."""
    assert False, "not yet implemented"


# ---------------------------------------------------------------------------
# Strategist orchestration
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")
def test_strategy_validate_string_inputs():
    """run_strategist handles non-numeric or unexpected corpus signal values gracefully."""
    assert False, "not yet implemented"


@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")
def test_run_strategist_calls_asyncio_run():
    """run_strategist calls asyncio.run with asyncio.wait_for wrapping _run_strategist_loop."""
    assert False, "not yet implemented"


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")
def test_expand_query_returns_three():
    """_expand_query returns a list of [original_query] + up to 2 generated variants."""
    assert False, "not yet implemented"


@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")
def test_expansion_calls_rrf_fuse_per_variant():
    """rrf_fuse_with_expansion calls rrf_fuse once per query variant when query_expansion=True."""
    assert False, "not yet implemented"
