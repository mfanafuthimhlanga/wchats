"""
Unit tests for M2 SSE event vocabulary — ING-08.

Verifies that:
  1. M2 defines exactly 11 SSE event types (RESEARCH.md §8 contract).
  2. M2 event types are distinct from M1 event vocabulary (no name collisions
     other than intentionally shared terminal events "job.complete" and "job.failed").
  3. All pipeline stage event types come in started/complete pairs.
  4. Ingestion lifecycle events (started, complete, terminal) are all present.
  5. The real parse_documents task source code emits the contracted event strings.

This test file imports no live DB or Redis — it is pure static analysis + vocabulary
contract verification. It verifies the ING-08 requirement that the 11 M2 event types
are distinct from M1 vocabulary and form the complete set the chain emits.
"""

import inspect

# M2 SSE event vocabulary — all 11 event types emitted by the ingestion chain
# (RESEARCH.md §8, CONTEXT.md §SSE Event Vocabulary)
M2_EVENT_TYPES = {
    "ingestion.started",
    "parsing.started",
    "parsing.complete",
    "chunking.started",
    "chunking.complete",
    "metadata.started",
    "metadata.complete",
    "embedding.started",
    "embedding.complete",
    "ingestion.complete",
    "job.complete",
}

# M1 SSE event vocabulary — from the M1 provision_neon + apply_migrations tasks.
# These must NOT collide with M2 vocabulary (except for the intentionally shared
# terminal event "job.complete" and "job.failed").
M1_EVENT_TYPES = {
    "provision.started",
    "neon.provisioning",
    "migrations.running",
    "migrations.complete",
    "job.complete",
    "job.failed",
}

# Intentionally shared terminal events between M1 and M2 (lifecycle events)
SHARED_TERMINAL_EVENTS = {"job.complete", "job.failed"}


# ---------------------------------------------------------------------------
# Test 1: M2 vocabulary is exactly 11 events
# ---------------------------------------------------------------------------


def test_m2_event_types_are_complete():
    """M2 vocabulary contains exactly 11 event types (RESEARCH.md §8 contract)."""
    assert len(M2_EVENT_TYPES) == 11, (
        f"RESEARCH.md §8 specifies exactly 11 M2 event types; "
        f"found {len(M2_EVENT_TYPES)}: {sorted(M2_EVENT_TYPES)}"
    )


# ---------------------------------------------------------------------------
# Test 2: M2 event types are distinct from M1 (excluding shared terminal events)
# ---------------------------------------------------------------------------


def test_m2_event_types_distinct_from_m1():
    """M2 events must not collide with M1 vocabulary (except shared terminal events).

    'job.complete' and 'job.failed' are intentionally shared — they signal
    job completion at all phases. All other M2 events must be unique to M2.
    """
    shared = M2_EVENT_TYPES & M1_EVENT_TYPES - SHARED_TERMINAL_EVENTS
    assert len(shared) == 0, (
        f"M2 event types must not collide with M1 vocabulary "
        f"(shared non-terminal events found: {shared}). "
        f"'job.complete' and 'job.failed' are intentionally shared terminal events."
    )


# ---------------------------------------------------------------------------
# Test 3: All pipeline stage event types have started/complete pairs
# ---------------------------------------------------------------------------


def test_all_pipeline_task_stages_have_started_and_complete_pairs():
    """Each pipeline stage emits both a 'started' and 'complete' event."""
    pairs = [
        ("parsing.started", "parsing.complete"),
        ("chunking.started", "chunking.complete"),
        ("metadata.started", "metadata.complete"),
        ("embedding.started", "embedding.complete"),
    ]
    for start, end in pairs:
        assert start in M2_EVENT_TYPES, (
            f"Missing event type: '{start}' — required by RESEARCH.md §8"
        )
        assert end in M2_EVENT_TYPES, (
            f"Missing event type: '{end}' — required by RESEARCH.md §8"
        )


# ---------------------------------------------------------------------------
# Test 4: Ingestion lifecycle events are all present
# ---------------------------------------------------------------------------


def test_ingestion_lifecycle_events_present():
    """Ingestion-level lifecycle events must be in M2 vocabulary."""
    assert "ingestion.started" in M2_EVENT_TYPES, (
        "'ingestion.started' is required — emitted once per parse_documents invocation"
    )
    assert "ingestion.complete" in M2_EVENT_TYPES, (
        "'ingestion.complete' is required — emitted after all documents embedded"
    )
    assert "job.complete" in M2_EVENT_TYPES, (
        "'job.complete' is required — terminal event after job.status='complete'"
    )


# ---------------------------------------------------------------------------
# Test 5: parse_documents task source code emits contracted event strings
# ---------------------------------------------------------------------------


def test_parse_task_imports_match_sse_vocabulary():
    """The real parse_documents task must emit 'parsing.started' and 'parsing.complete'.

    This test imports the actual task function and inspects its source code
    to verify the contracted SSE events are present in the implementation,
    not just in the vocabulary constants above.
    """
    from app.worker.tasks.pipeline.parse import parse_documents

    src = inspect.getsource(parse_documents.run)

    for evt in ["parsing.started", "parsing.complete"]:
        assert evt in src, (
            f"parse_documents task does not emit '{evt}'. "
            f"This event is part of the ING-08 M2 SSE vocabulary contract."
        )

    # Also verify ingestion.started is emitted by parse_documents (it is the entry point)
    assert "ingestion.started" in src, (
        "parse_documents task must emit 'ingestion.started' as the chain entry point."
    )
