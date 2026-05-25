"""Wave-0 xfail stubs for app.worker.tasks.pipeline.strategy — M9 Retrieval Strategy Synthesis.

All tests are marked xfail(strict=True) — they are expected to fail until
implemented in Phase 09-03. A passing test in this file would itself be a
test-suite failure (strict mode).

The module app.worker.tasks.pipeline.strategy does not exist yet. Each test
guards its import INSIDE the test function body so that the ImportError is the
expected xfail, rather than a collection-time error.

Coverage targets (to be wired in 09-03):
    test_strategy_written_to_db          — synthesize_retrieval_strategy persists JSONB to agents table
    test_receives_embed_result_dict      — task correctly unpacks embed result dict from Wave 1
    test_idempotency_skip                — task returns early when retrieval_strategy already set
    test_resynthesis_flag_bypasses_guard — force_resynthesize=True bypasses idempotency guard
"""

import pytest


# ---------------------------------------------------------------------------
# Happy-path persistence
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")
def test_strategy_written_to_db():
    """synthesize_retrieval_strategy writes the strategy JSONB to agents.retrieval_strategy."""
    from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy  # noqa: F401
    assert False, "not yet implemented"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")
def test_receives_embed_result_dict():
    """Task correctly unpacks the embed result dict produced by the Wave 1 embed task."""
    from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy  # noqa: F401
    assert False, "not yet implemented"


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")
def test_idempotency_skip():
    """synthesize_retrieval_strategy returns early when retrieval_strategy is already populated."""
    from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy  # noqa: F401
    assert False, "not yet implemented"


@pytest.mark.xfail(strict=True, reason="Wave 0 stub — implemented in 09-03")
def test_resynthesis_flag_bypasses_guard():
    """force_resynthesize=True causes the task to run even when retrieval_strategy is set."""
    from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy  # noqa: F401
    assert False, "not yet implemented"
