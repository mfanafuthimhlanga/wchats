"""Tenant DB v16 migration — eval_scenarios label provenance (D6 P1).

Revision ID: 0016
Revises: 0015

Context:
    `eval_service.LABEL_TRUST_TIERS` has always declared five tiers, two of
    which nothing in the system can produce: `human_verified` (2) and
    `human_authored` (3). The only thing that resolved a tier was
    `SCENARIO_SOURCE_TRUST_TIER`, and every source the schema allows maps to
    `model_generated` or `customer_negative`. So the vocabulary anticipated a
    human label and the schema had nowhere to put one.

    THE TIER BELONGS TO THE LABEL, NOT TO THE ROW'S ORIGIN.
        `eval_scenarios.source` says where the QUESTION came from. A mined
        production failure whose answer the owner then writes by hand is
        `source='mined'` — `customer_negative` in origin — and `human_authored`
        in label, simultaneously and correctly. Storing the human tier by
        widening `source` would fuse the two into one column, and a column that
        means two things gets read as whichever one the reader had in mind:
        that is exactly how a model-written string ends up admitted on a human
        tier, the failure `eval_service.promotable_answer`'s docstring already
        warns about. Hence a separate column, and hence THIS migration does NOT
        touch 0011's `source` CHECK — see "What this migration deliberately does
        not do" below.

    The three columns (all nullable, no DEFAULT, no backfill):

    label_trust_tier TEXT
        NULL on every row that exists today and on every row any model-driven
        producer writes. The CHECK below admits nothing but NULL and the two
        human tiers, so **there is no value of this column that means "a model
        wrote this"** — a model's label records no claim at all, which is what
        NULL says. `eval_service.label_trust_tier()` resolves NULL to the row's
        source-derived tier, which can never be a human tier (pinned by
        test_no_schema_allowed_source_can_produce_a_human_label_tier).

        WHAT THE CHECK DOES **NOT** SAY, corrected 2026-08-09. It constrains the
        VALUE, never the AUTHOR. An earlier version of this docstring claimed
        that "the column is set" and "a human wrote this" were the same
        statement at the database level for any caller including one bypassing
        the service layer. They are not: the constraint refuses
        'model_generated' — the one value a forging writer would never choose —
        and accepts 'human_authored' from anyone holding a tenant connection.
        WHO may write a human value is enforced in Python by
        `label_service`'s four restrictions, not here.

    labelled_by TEXT
        Who. NULL when label_trust_tier is NULL.

    labelled_at TIMESTAMPTZ
        When. NULL when label_trust_tier is NULL. NOT `NOW()` as a DEFAULT — a
        default would stamp a labelling time on every unlabelled row ever
        inserted, which is a claim about an event that did not happen.

    The CHECK, and why this one is safe where 0005's was not:
        0005 wrote `source TEXT NOT NULL CHECK (source IN (...))` inline and
        unnamed, and 0011 then had to discover Postgres' auto-generated name at
        apply time in order to widen it. 0014's docstring drew the lesson as
        "do not repeat that". The lesson is about UNNAMED and about constraining
        a column live INSERTs already write — not about CHECK constraints as
        such. This one is:

          - explicitly named (`eval_scenarios_label_trust_tier_check_v1`), so a
            future widening is `DROP CONSTRAINT <that name>`, not an archaeology
            expedition;
          - on a column that is brand new, so no existing row can violate it and
            no existing INSERT statement mentions it — it cannot break a live
            tenant on apply;
          - discovered rather than assumed on re-run: the DO block below
            introspects pg_constraint/pg_attribute for whatever CHECK currently
            governs `label_trust_tier` and drops only a DIFFERENTLY-named one,
            the same technique 0011 used and for the same reason (never hardcode
            a name you did not choose). On a second run ours is already present,
            the introspection matches nothing, and the ADD is skipped — the
            whole block is idempotent.

        And it is load-bearing rather than decorative, in the narrow sense that
        is actually true: a raw `UPDATE eval_scenarios SET label_trust_tier =
        'model_generated'` is refused by the database itself, so the column
        cannot come to hold a non-human vocabulary. A raw `... = 'human_authored'`
        is NOT refused. The database bounds the vocabulary; it does not
        authenticate the writer.

        The second arm — a human tier requires a non-empty reference_answer —
        closes the pairing the tier is a claim about. `label_trust_tier =
        'human_authored'` beside `reference_answer = ''` asserts that a person
        authored nothing, on a row the eval selector's `WHERE reference_answer
        != ''` then never scores. `label_service.record_human_label` already
        refuses to create it; this stops a direct write, a partial restore, or a
        downgrade-and-re-upgrade from leaving one behind. It cannot break an
        existing row: every row's tier is NULL, so every row satisfies the first
        arm and the second is never evaluated.

    What this migration deliberately does NOT do:
        It does not widen 0011's `source` CHECK. Adding a human-flavoured source
        value (e.g. 'owner_authored') would:
          1. re-collapse origin into label, the precise defect this column
             exists to separate; and
          2. make `eval_service.is_promotable_to_verified_qa(source)` return
             True for a schema-allowed source — opening the customer-facing
             `verified_qa` write that `retrieval_service.verified_qa_lookup`
             serves AHEAD of retrieval. The owner settled that question
             eval-only on 2026-08-08 (`.dev/plans/260808-d6-labelling-loop.md`).
        A human label is therefore recorded on the label columns and the row's
        `source` is left saying exactly what it said before: where the question
        came from.

Cannot be applied on this machine:
    There is no PostgreSQL server here — every `-m integration` harness skips,
    and a skip is UNOBSERVED, never a pass. No ALTER TABLE in this file has been
    executed against any database. The source-level assertions in
    tests/unit/test_migration_tenant_0016.py are the only observed evidence that
    exists for it, and they constrain what this migration is ALLOWED to contain:
    additive columns only, nullable only, no DEFAULT, no backfill, no
    pre-existing object touched, and a downgrade that drops only what upgrade
    added.

    Follows the established raw-SQL convention (mirrors 0009-0015) — no
    SQLAlchemy ORM model, consistent with every other tenant-DB table.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Explicit and stable. The v1 suffix is the affordance 0005 lacked: widening
# this later is a DROP by name, not a pg_constraint lookup.
_LABEL_TIER_CONSTRAINT_NAME = "eval_scenarios_label_trust_tier_check_v1"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1 — the three label-provenance columns. Nullable, no DEFAULT,
    # no backfill: every row that exists predates human labelling and the
    # honest value for "did a human label this?" on those rows is NULL.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS label_trust_tier TEXT
    """)
    op.execute("""
        ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS labelled_by TEXT
    """)
    op.execute("""
        ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS labelled_at TIMESTAMPTZ
    """)

    # ------------------------------------------------------------------
    # Step 2 — constrain the new column to the two tiers that assert a
    # human, mirroring 0011's technique: introspect pg_constraint /
    # pg_attribute for whatever CHECK currently governs the column and drop
    # only a differently-named one (never hardcode a name Postgres chose),
    # then ADD ours under a stable explicit name if it is not already there.
    # Both halves are guarded, so a re-run is a no-op.
    #
    # NULL passes: an unlabelled row makes no claim. Any non-human value is
    # rejected by the database, so the column has no vocabulary for "a model
    # wrote this" — which is a bound on the VALUE and not on the writer.
    #
    # SCHEMA-QUALIFIED, unlike 0011's copy of this block. 0011 filters on
    # `rel.relname = 'eval_scenarios'` with no pg_namespace join and then
    # EXECUTEs a DROP against an unqualified table name: if a tenant DB ever
    # carried eval_scenarios in more than one schema, the name would be
    # discovered from one table and the DROP applied to whichever the
    # search_path resolves — dropping a constraint governing a different
    # table's column. 0016 has never been applied anywhere, so fixing the
    # inherited gap here is free; 0011's copy is deployed and is a separate
    # decision.
    # ------------------------------------------------------------------
    op.execute(f"""
        DO $$
        DECLARE
            con_name text;
            con_schema text;
        BEGIN
            SELECT con.conname, nsp.nspname INTO con_name, con_schema
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            JOIN pg_attribute att
                ON att.attrelid = rel.oid AND att.attnum = ANY(con.conkey)
            WHERE rel.relname = 'eval_scenarios'
              AND nsp.nspname = current_schema()
              AND con.contype = 'c'
              AND att.attname = 'label_trust_tier'
              AND con.conname <> '{_LABEL_TIER_CONSTRAINT_NAME}'
            LIMIT 1;

            IF con_name IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE %I.%I DROP CONSTRAINT %I',
                    con_schema, 'eval_scenarios', con_name
                );
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                WHERE con.conname = '{_LABEL_TIER_CONSTRAINT_NAME}'
                  AND rel.relname = 'eval_scenarios'
                  AND nsp.nspname = current_schema()
            ) THEN
                ALTER TABLE eval_scenarios
                    ADD CONSTRAINT {_LABEL_TIER_CONSTRAINT_NAME}
                    CHECK (
                        label_trust_tier IS NULL
                        OR (
                            label_trust_tier IN ('human_verified', 'human_authored')
                            AND COALESCE(reference_answer, '') <> ''
                        )
                    );
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Reverse order: the constraint, then the columns it constrained. IF
    # EXISTS throughout so a downgrade against a DB that never received 0016
    # is a no-op rather than an error.
    #
    # Rolling back LOSES every human label recorded since this revision. That
    # is stated rather than mitigated: a downgrade that tried to preserve them
    # would have to park them somewhere, and a human label parked outside the
    # column that means "a human wrote this" is a label whose provenance the
    # next reader has to guess.
    op.execute(f"""
        ALTER TABLE eval_scenarios
        DROP CONSTRAINT IF EXISTS {_LABEL_TIER_CONSTRAINT_NAME}
    """)
    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS labelled_at")
    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS labelled_by")
    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS label_trust_tier")
