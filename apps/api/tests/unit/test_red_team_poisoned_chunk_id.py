"""Issue #62 — the content-injection probe's chunk id obeys the corpus rule.

WHAT THE RULE IS

    A chunk in a tenant corpus is identified by
    `deterministic_chunk_id(document_id, ordinal)`. `app.domain.chunk.Chunk`
    derives it and refuses a supplied one, and the ingestion writer upserts
    ON CONFLICT (id) on the strength of it, so re-ingesting a document rewrites
    its own rows instead of duplicating them.

    `seed_poisoned_chunk` wrote a `uuid4()` at ordinal 0 into a LIVE tenant
    corpus, which is the one row in that corpus whose id disagreed with its
    position. `remove_poisoned_chunk` runs from a `finally` and swallows its own
    failures, so a cleanup that did not land leaves that row behind, and nothing
    downstream could ever rederive its id to replace it.

WHY THESE TESTS USE A REAL DATABASE

    The invariant is a claim about a row, and the harm is a claim about what a
    later writer's upsert reaches. Both are settled by seeding, then running the
    writer's own statement against the same table, which a mocked cursor cannot
    answer: it would agree with whatever id the seeder chose.

    Local, disposable, and no credentials, so it runs on every commit rather
    than behind INTEGRATION_TESTS_ENABLED. It skips when the cluster is absent.

WHAT IS NOT HERE

    Constructing the probe's row through `app.domain.chunk.Chunk` and retiring
    the five-column INSERT belong to the Attacker rebuild (#52). This is the id.
"""

from __future__ import annotations

import os

import psycopg2
import pytest

from app.domain.chunk import Chunk
from app.domain.chunk_id import deterministic_chunk_id
from app.services.red_team_service import (
    POISONED_CHUNK_TEXT,
    remove_poisoned_chunk,
    seed_poisoned_chunk,
)

#: The disposable local tenant database CLAUDE.md names, and the same env-var
#: override the integration harnesses read.
PROBE_DB_URL = os.getenv(
    "TEST_TENANT_PROBE_URL",
    os.getenv("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")
    + "/wchats_tenant_probe",
)


@pytest.fixture
def probe_conn():
    """A psycopg2 connection to the probe database, or a skip.

    Skips rather than fails when there is no cluster: a red test on a machine
    with no PostgreSQL says the code is broken when the socket is what is
    missing.
    """
    try:
        conn = psycopg2.connect(PROBE_DB_URL, connect_timeout=5)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"no local wchats_tenant_probe cluster: {type(exc).__name__}")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def seeded(probe_conn):
    """One seeded poisoned chunk, removed however the test ends.

    The cleanup is `remove_poisoned_chunk`, the production path, so a test that
    leaves rows behind is telling the truth about the probe rather than about
    the fixture.
    """
    chunk_id = seed_poisoned_chunk(PROBE_DB_URL)
    try:
        yield chunk_id
    finally:
        remove_poisoned_chunk(PROBE_DB_URL, chunk_id)


def _chunk_row(conn, chunk_id: str):
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT document_id, ordinal, content FROM chunks WHERE id = %s",
            (chunk_id,),
        )
        return cur.fetchone()


def test_the_seeded_chunk_id_is_the_id_the_writer_would_derive(probe_conn, seeded):
    """The invariant, on the row as it lands.

    A `uuid4()` here fails on the first assertion, which is the state issue #62
    records.
    """
    row = _chunk_row(probe_conn, seeded)
    assert row is not None, "the seeder wrote no chunk row at this id"
    document_id, ordinal, content = row

    assert seeded == str(deterministic_chunk_id(str(document_id), ordinal)), (
        "the probe's chunk id must be the one its (document_id, ordinal) names"
    )
    assert ordinal == 0
    assert content == POISONED_CHUNK_TEXT, (
        "and the payload is still written verbatim, sanitiser gap and all (OD-7)"
    )


def test_a_re_ingest_of_that_position_upserts_over_the_probes_row(probe_conn, seeded):
    """The consequence, which is what the invariant is for.

    This runs the ingestion writer's own statement, ON CONFLICT (id) DO UPDATE,
    for position 0 of the probe's document. With the id derived it lands on the
    seeded row and replaces the payload. With a `uuid4()` it inserts a second
    row and the poisoned one stays in the corpus for good.
    """
    document_id = str(_chunk_row(probe_conn, seeded)[0])
    replacement = Chunk(
        document_id=document_id,
        ordinal=0,
        content="Our extended warranty program covers electronics for two years.",
        token_count=10,
        is_table=False,
    )
    assert str(replacement.id) == seeded, (
        "the writer derives the same id from the same position, or the rest of "
        "this test is about two different rows"
    )

    with probe_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chunks (id, document_id, ordinal, content, token_count, is_table)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
                SET content     = EXCLUDED.content,
                    token_count = EXCLUDED.token_count,
                    ordinal     = EXCLUDED.ordinal,
                    is_table    = EXCLUDED.is_table
            """,
            (
                str(replacement.id), document_id, replacement.ordinal,
                replacement.content, replacement.token_count, replacement.is_table,
            ),
        )
        cur.execute(
            "SELECT id, content FROM chunks WHERE document_id = %s", (document_id,)
        )
        rows = cur.fetchall()
    probe_conn.commit()

    assert len(rows) == 1, f"the re-ingest duplicated the position: {rows}"
    assert rows[0][1] == replacement.content, "the poisoned text survived a re-ingest"


def test_remove_poisoned_chunk_still_finds_the_derived_id(probe_conn):
    """Cleanup reaches the row, its embedding and its throwaway document.

    Seeded without the fixture, because the removal is the subject here rather
    than the teardown. The three assertions are separate on purpose: the
    embeddings row goes by cascade and the documents row goes by its own DELETE,
    and only the chunk is named in the call.
    """
    chunk_id = seed_poisoned_chunk(PROBE_DB_URL)
    document_id = str(_chunk_row(probe_conn, chunk_id)[0])

    remove_poisoned_chunk(PROBE_DB_URL, chunk_id)

    probe_conn.rollback()
    with probe_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM chunks WHERE id = %s", (chunk_id,))
        assert cur.fetchone() is None, "the poisoned chunk is still in the corpus"
        cur.execute("SELECT 1 FROM embeddings WHERE chunk_id = %s", (chunk_id,))
        assert cur.fetchone() is None, "its embedding outlived it"
        cur.execute("SELECT 1 FROM documents WHERE id = %s", (document_id,))
        assert cur.fetchone() is None, "its throwaway document row outlived it"


def test_two_probes_do_not_collide(probe_conn):
    """Deriving the id keeps every run its own row.

    `document_id` is still a fresh uuid4 per seed, so the derived ids differ.
    A derivation over a fixed document id would make the second probe of a
    tenant upsert over the first one's row while the first was still reading it.
    """
    first = seed_poisoned_chunk(PROBE_DB_URL)
    second = seed_poisoned_chunk(PROBE_DB_URL)
    try:
        assert first != second
    finally:
        remove_poisoned_chunk(PROBE_DB_URL, first)
        remove_poisoned_chunk(PROBE_DB_URL, second)
