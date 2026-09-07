"""The offline eval's fixture corpus chunks into something the drafter can cover (#203).

No model call: this pins the fixture and the chunker, so a labelled baseline
later measures the drafter and not a corpus that silently collapsed to one chunk.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.services.golden_draft_service import pick_chunks_by_document

RUNNER = Path(__file__).resolve().parents[1] / "evals" / "golden_draft" / "run_golden_draft_eval.py"


def _runner():
    spec = importlib.util.spec_from_file_location("run_golden_draft_eval", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_four_documents_each_chunk_into_at_least_two_passages():
    runner = _runner()
    per_document = {
        path.stem: runner.chunk_document(path) for path in sorted(runner.CORPUS.glob("*.md"))
    }

    assert len(per_document) == 4
    assert all(len(chunks) >= 2 for chunks in per_document.values()), {
        k: len(v) for k, v in per_document.items()
    }


def test_twelve_drafts_come_from_every_document_at_least_twice():
    runner = _runner()
    rows = [c for path in sorted(runner.CORPUS.glob("*.md")) for c in runner.chunk_document(path)]

    picked = pick_chunks_by_document(rows, 12)

    counts: dict[str, int] = {}
    for chunk in picked:
        counts[chunk["document_id"]] = counts.get(chunk["document_id"], 0) + 1
    assert len(picked) == 12
    assert len(counts) == 4 and min(counts.values()) >= 2, counts
    assert len({c["chunk_id"] for c in picked}) == 12, "a chunk was drafted from twice"
