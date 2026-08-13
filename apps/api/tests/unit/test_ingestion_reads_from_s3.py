"""No ingestion task may read document bytes from local disk.

BACKLOG `1.26`. PROD-13 moved document storage to S3 and migrated
`parse_documents`. `chunk_documents` was left behind: it kept a
`_resolve_local_path()` helper whose own docstring called it a "Mirror of
parse_documents path-resolution", pointing at
``UPLOADS_DIR/{agent_id}/{doc_id}{ext}``. Nothing in `app/` writes a file to
disk any more — there is not one `write_bytes`, `open(..., "wb")` or
`shutil.copy` in the whole package — so **every file-source document failed at
chunking with `FileNotFoundError` and retried to exhaustion, in every
environment**. Only URL sources, which re-fetch over HTTP, ever completed.

Observed 2026-08-13 by running the real chain (E2E-2):

    chunk_documents.error  document_id=13fa9b5e-...  error_type=FileNotFoundError
    Retry in 4s: Retry(Retry(...), FileNotFoundError(2, 'No such file or directory'), 4)

Why this is an absence pin and not a call-site test
---------------------------------------------------
Fixing the one call site would leave the *class* open — that is `1.14`'s lesson,
where scanning for the shape rather than the instance found two more sites. The
statement worth pinning is "the S3 migration is complete", and its
machine-checkable form is: no pipeline task references `UPLOADS_DIR`, and none
reads a path off the filesystem.

Why the existing integration tests could never have caught it
-------------------------------------------------------------
`tests/integration/test_ingestion_chain.py:347` writes its fixture to
``gettempdir()/vrd-uploads/{agent_id}/{doc_id}{ext}`` — it *manufactures the
local file production never creates*. Those four tests have never run in repo
history (`4.4`), and had they run they would have passed, because the fixture
recreates the contract the product abandoned. Retro Family I, sharpest instance
so far: the fixture is not merely a claim about a boundary, it is a claim about
a boundary that no longer exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[2] / "app" / "worker" / "tasks" / "pipeline"

#: Tasks that legitimately never touch document bytes are still scanned — the
#: pin is cheap and a new task inheriting the old pattern is exactly the risk.
PIPELINE_MODULES = sorted(p for p in PIPELINE_DIR.glob("*.py") if p.name != "__init__.py")


def test_the_pipeline_directory_was_found():
    """Guards the guard: a wrong path would make every scan below vacuous."""
    assert PIPELINE_MODULES, f"no pipeline modules found under {PIPELINE_DIR}"
    names = {p.name for p in PIPELINE_MODULES}
    assert {"parse.py", "chunk.py"} <= names, f"expected parse.py and chunk.py in {names}"


@pytest.mark.parametrize("module", PIPELINE_MODULES, ids=lambda p: p.name)
def test_no_pipeline_task_references_uploads_dir(module: Path):
    """`UPLOADS_DIR` is back-compat config; the hot path is S3 (config.py:164)."""
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "UPLOADS_DIR"
    ]
    assert not offenders, (
        f"{module.name} reads settings.UPLOADS_DIR at line(s) {offenders}. "
        "Document bytes live in S3 since PROD-13 and nothing writes them to "
        "disk, so this path cannot exist at runtime. Use "
        "storage_service.get_bytes(storage_service.upload_key(...)) — see "
        "BACKLOG 1.26."
    )


def test_chunk_documents_fetches_bytes_from_storage():
    """The positive half: chunk.py must actually call the S3 helpers."""
    source = (PIPELINE_DIR / "chunk.py").read_text(encoding="utf-8")
    assert "storage_service.get_bytes" in source, (
        "chunk_documents no longer reads from disk, but it does not read from "
        "storage_service either. It must fetch the same bytes parse_documents "
        "fetched."
    )
    assert "storage_service.upload_key" in source, (
        "the S3 key must be built with upload_key(), not hand-assembled — a "
        "second spelling of the key is how the reader and writer drift apart."
    )


def test_the_dead_local_path_helper_is_gone():
    """A dead helper naming a plausible contract is how it gets re-adopted."""
    source = (PIPELINE_DIR / "chunk.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_resolve_local_path" not in defined, (
        "_resolve_local_path() is back in chunk.py. It resolved "
        "UPLOADS_DIR/{agent_id}/{doc_id}{ext}, which nothing writes."
    )


# ---------------------------------------------------------------------------
# The key must be spelled the same way by the writer and every reader (1.27)
# ---------------------------------------------------------------------------


def test_readers_lowercase_the_extension_like_the_writer():
    """`documents.py` stores the key with `.suffix.lower()`.

    A reader using bare `.suffix` fetches ".PDF" for a key written as ".pdf",
    which is a NoSuchKey that fires only for an uppercase extension — and no
    fixture in this repo has one, so nothing would ever have shown it.
    """
    writer = (
        Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "documents.py"
    ).read_text(encoding="utf-8")
    assert 'Path(f.filename or "").suffix.lower()' in writer, (
        "the writer no longer lowercases the extension; this test's premise "
        "about which side is authoritative needs rechecking"
    )

    for name in ("parse.py", "chunk.py"):
        source = (PIPELINE_DIR / name).read_text(encoding="utf-8")
        if "storage_service.upload_key" not in source:
            continue
        assert 'f".{source_type}").lower()' in source or ".suffix.lower()" in source, (
            f"{name} builds an S3 key extension without .lower(), but the "
            "writer lowercases it (documents.py:191). An uppercase extension "
            "would 404. See BACKLOG 1.27."
        )
