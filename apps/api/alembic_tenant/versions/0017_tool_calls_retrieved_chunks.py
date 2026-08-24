"""Tenant DB v17 migration — tool_calls.retrieved_chunks (BACKLOG 7.34).

Revision ID: 0017
Revises: 0016

Context:
    The offline calibration judge scores `grounding_fidelity` against a rubric
    that reads, in full: "every factual claim is traceable to a retrieved chunk
    PROVIDED IN THE TOOL_CALLS LOG". Nothing outside the worker could provide
    one. The untruncated chunks live on the in-process `tool_calls_log` under
    `RETRIEVE_JUDGE_CHUNKS_KEY`, `retrieved_context_json` is a Celery task
    argument and a char-count log line, and the customer SSE carries only
    `summary[:200]`. So the rubric's PASS branch was unreachable and every
    grounding verdict had to FAIL whatever the answer said.

    This column is where the chunks are written down.

    WHY NOT WIDEN `tool_calls.result`. That column holds the audit capture,
    `str(block.content)[:RETRIEVE_RESULT_CAPTURE_CHARS]` — a Python repr cut at
    1800 characters, which `agent.py`'s own constant docstring records as below
    ONE full chunk on any realistic corpus. Reusing it would mean one column
    holding either a repr or a chunk list depending on when the row was written,
    and a column that means two things gets read as whichever the reader had in
    mind. 0016's rationale for a separate column applies unchanged here.

    NULL AND `[]` ARE DIFFERENT OBSERVATIONS, and keeping them apart is the
    whole point of the column being nullable with no default:

        NULL   this tool call retrieves nothing (escalate, clarify), or its
               capture could not be decoded
        []     this retrieve ran and the corpus had no match

    BACKLOG 5.16 is what collapsing those two costs: a judge shown an empty
    context marks every claim unsupported, so silence about a decode failure
    manufactures an `ungrounded` verdict that is about the decoder rather than
    the answer.

    Additive and safe on a live tenant: nullable, no DEFAULT, no backfill, and
    nothing in app code SELECTs `tool_calls` today. The readers named in
    surrounding comments read `tool_calls_audit`, a different table.

    APPLIED AND VERIFIED 2026-08-18 against the local `wchats_tenant_probe`
    cluster, through the production path (`migrations.run_tenant_migrations`):
    0016 -> 0017, column present as `jsonb`, nullable, no DEFAULT, COMMENT
    landed, and NULL / `[]` / a populated array all stored and distinguishable.
    Downgrade to 0016 drops it and re-upgrade restores it.

    The first draft of this docstring said "NOT RUN ON THIS MACHINE, there is no
    PostgreSQL here". That was quoted from CLAUDE.md, which had been stale since
    2026-08-10. Nobody opened a socket to check. Test the constraint.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS so a re-run is a no-op, matching 0016's guarded halves.
    op.execute("""
        ALTER TABLE tool_calls
        ADD COLUMN IF NOT EXISTS retrieved_chunks jsonb
    """)
    op.execute("""
        COMMENT ON COLUMN tool_calls.retrieved_chunks IS
        'BACKLOG 7.34. One rendered chunk per retrieved passage, content plus the provenance the agent saw, for the offline grounding judge. NULL means this call retrieves nothing or its capture could not be decoded; [] means a retrieve ran and matched nothing.'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tool_calls DROP COLUMN IF EXISTS retrieved_chunks")
