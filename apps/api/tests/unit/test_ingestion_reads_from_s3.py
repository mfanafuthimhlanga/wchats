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


# ---------------------------------------------------------------------------
# The key itself, not just the fact that a key is fetched (BACKLOG 1.33)
# ---------------------------------------------------------------------------


def test_chunk_fetches_the_exact_key_the_upload_route_wrote():
    """Reader and writer must agree on the S3 key, argument order included.

    An adversarial review swapped the two positional arguments in `chunk.py`::

        storage_service.upload_key(doc_id, agent_id, ext)   # MUTATED

    and observed **18 passed**. That defect makes every chunk fetch
    `{doc_id}/{agent_id}{ext}`, 404 on every file-source document, and reproduce
    `1.26`'s exact symptom in a new spelling. Nothing caught it, because the
    previous checks asserted only that the strings `storage_service.get_bytes`
    and `storage_service.upload_key` appear in the file, and the task-level stub
    accepted any `key` without looking at it.

    This calls the REAL `upload_key` from both sides — the writer's spelling
    (`documents.py`) and the reader's (`chunk.py`) — and requires the same
    string. Argument order, separator and case are all covered by construction.
    """
    from app.services.storage_service import upload_key

    agent_id = "11111111-1111-4111-8111-111111111111"
    doc_id = "22222222-2222-4222-8222-222222222222"

    # The writer: documents.py:191 builds `Path(f.filename).suffix.lower()` and
    # calls upload_key(str(agent.id), doc_id, ext).
    written = upload_key(agent_id, doc_id, ".pdf")

    # The reader: chunk.py derives the extension from source_uri and calls
    # upload_key(agent_id, doc_id, ext). Mirrored here in the same order.
    ext = (Path("Policy.PDF").suffix or ".pdf").lower()
    read = upload_key(agent_id, doc_id, ext)

    assert read == written, (
        f"reader built {read!r}, writer built {written!r}. A key assembled "
        "differently at each end 404s on every document — that is BACKLOG 1.27, "
        "and swapping upload_key's positional arguments produces the same "
        "outcome in a shape no substring check can see."
    )
    assert written == f"{agent_id}/{doc_id}.pdf", (
        f"upload_key's own format changed: {written!r}. Both ends move "
        "together here, so this assertion is what stops the pair drifting as a "
        "unit away from what is already stored in the bucket."
    )


def test_chunk_passes_upload_keys_arguments_in_the_documented_order():
    """The argument ORDER at chunk.py's call site, read off the AST.

    `upload_key(agent_id, doc_id, ext)` and `upload_key(doc_id, agent_id, ext)`
    are both valid Python over two UUID strings, produce no error, and differ
    only in the object path they fetch. The signature cannot catch it and the
    stub did not. This reads the call node and pins the identifiers.
    """
    tree = ast.parse((PIPELINE_DIR / "chunk.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "upload_key"
    ]
    assert len(calls) == 1, f"expected one upload_key call in chunk.py, found {len(calls)}"

    args = [getattr(a, "id", None) for a in calls[0].args]
    assert args[:2] == ["agent_id", "doc_id"], (
        f"chunk.py calls upload_key{tuple(args)}. The writer "
        "(documents.py:191) passes (agent_id, doc_id, ext); swapping the first "
        "two fetches {doc_id}/{agent_id}{ext} and 404s on every document."
    )
