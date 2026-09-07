"""Offline eval for the golden drafter (#203): report the keep rate, assert nothing yet.

    GOLDEN_DRAFT_EVAL=1 .venv/Scripts/python.exe tests/evals/golden_draft/run_golden_draft_eval.py [n]

Drafts `n` pairs (default 12) from the four-document fixture corpus in `corpus/`
through the production drafter, with the production model route and credentials
from the environment, and writes them to `drafts.json` in this directory in the
labelling bench's shape. It spends model money, so it refuses to run without
GOLDEN_DRAFT_EVAL=1.

What it reports:

- kept and dropped: how many drafts survived the citation check, which the code
  decides.
- keep rate: how many of the kept drafts a person labelled `keep` in
  `labels.json`, keyed by question text. That file is written by hand after
  reading `drafts.json`; the drafter never writes it. Without it the keep rate
  is reported as unlabelled, never as a number.

The bar is the hand-drafted set of 2026-09-06: 11 of 23 kept as written. Until a
labelled run of this fixture exists there is no baseline, so nothing here fails.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"
DRAFTS = HERE / "drafts.json"
LABELS = HERE / "labels.json"

MIN_CHUNK_CHARS = 300


def chunk_document(path: Path) -> list[dict]:
    """Paragraph blocks of at least MIN_CHUNK_CHARS, the way ingestion chunks prose."""
    document_id = path.stem
    blocks = [b.strip() for b in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")) if b.strip()]
    chunks: list[dict] = []
    current = ""
    for block in blocks:
        current = f"{current}\n\n{block}".strip() if current else block
        if len(current) >= MIN_CHUNK_CHARS:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return [
        {
            "chunk_id": f"{document_id}:{i}",
            "document_id": document_id,
            "ordinal": i,
            "content": content,
            "document": path.name,
        }
        for i, content in enumerate(chunks)
    ]


def main(n: int) -> int:
    if os.environ.get("GOLDEN_DRAFT_EVAL") != "1":
        print("GOLDEN_DRAFT_EVAL=1 is required: this run spends model money.")
        return 2
    sys.path.insert(0, str(HERE.parents[2]))
    from app.core.model_client import LedgerContext
    from app.services.golden_draft_service import draft_golden_pairs, pick_chunks_by_document

    rows = [c for path in sorted(CORPUS.glob("*.md")) for c in chunk_document(path)]
    chunks = pick_chunks_by_document(rows, n)
    spent: list = []
    ledger = LedgerContext(
        tenant_id=str(uuid.uuid4()), agent_id=None, job_id=None, recorder=spent.append
    )
    kept, dropped = draft_golden_pairs(chunks, ledger)

    DRAFTS.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    labels = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.exists() else None

    print(f"documents={len({r['document_id'] for r in rows})} chunks={len(rows)} drafted_from={len(chunks)}")
    print(f"kept={len(kept)} dropped={dropped} model_calls={len(spent)}")
    if labels is None:
        print("keep rate: unlabelled. Read drafts.json, write labels.json {question: keep|drop}, rerun.")
        return 0
    labelled = [d for d in kept if d["question"] in labels]
    keeps = sum(1 for d in labelled if labels[d["question"]] == "keep")
    print(f"keep rate: {keeps} of {len(labelled)} labelled ({len(kept) - len(labelled)} unlabelled). Bar: 11 of 23.")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 12))
