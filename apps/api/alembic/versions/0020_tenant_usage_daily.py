"""Control DB v20 migration for tenant_usage_daily, the priced rollup (ticket #46, issue #22).

Revision ID: 0020
Revises: 0019

Context:
    Control chain position: this migration chains from 0019
    (blast_radius_capability_v2), the control head confirmed by listing
    `alembic/versions` before this file was written. The CONTROL tree numbers its
    revisions independently of `alembic_tenant/versions`, which has its own 0019
    and will have its own 0020.

    WHAT IT HOLDS. One row per (tenant_id, purpose, day). The counts are the day's
    summed tokens and calls for that purpose. The money is DERIVED from those calls
    by `app.domain.pricing` and stamped with the versions that produced it, so a
    report can say which tariff and which rand rate a figure came from.

    WHY MONEY IS HERE WHEN THE LEDGER REFUSES IT. Tenant migration 0019 stores no
    cost, because a cost frozen onto a call row is a figure a corrected book can
    never reach. This table is the other side of that decision: money lands here as
    a reading, carrying `price_version` and `fx_version`, and a re-derive against a
    newer book OVERWRITES the row rather than appending beside it. That is what
    re-pricing at read time means for a rollup.

    WHY THE PRIMARY KEY IS (tenant_id, purpose, day). It is the upsert key, and the
    upsert is `rollup_model_calls`'s idempotency. A second run for the same day
    writes the same three values, lands on the same row, and leaves the table as
    the first run left it. A surrogate id would let a re-run append a duplicate day.

    THE MONEY COLUMNS ARE NULLABLE, THE COUNTS ARE NOT. A purpose group whose model
    the price book refuses is written with its tokens and its call count and NULL
    money, so the gap is visible in the table as tokens spent for no recorded cost.
    The alternative is an UnknownPrice killing the whole rollup, which makes every
    other tenant's day invisible too. A NULL count would understate a tenant's day,
    so the counts take NOT NULL.

    NUMERIC WITH NO PRECISION. One judge call costs a small fraction of a cent.
    Rounding to cents before the report reads the row would report a busy tenant as
    free, which is the failure this ticket exists to end. The report layer rounds
    what it prints.

    BIGINT, NOT INT. A day's summed tokens for one purpose across a tenant outgrows
    INT well before the tenant is large.

    Raw SQL with IF NOT EXISTS guards, the convention every control table has
    followed since 0011. No SQLAlchemy ORM model: the rollup writes through one
    upsert statement and nothing reads this table through the ORM yet.

    APPLIED AND VERIFIED 2026-08-25 against the local `wchats_control` cluster
    through the alembic Python API: 0019 to 0020, the table arrives with twelve
    columns, the composite primary key and the day index, downgrade to 0019 drops
    it and re-upgrade restores it.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_usage_daily (
            tenant_id               UUID NOT NULL,
            purpose                 TEXT NOT NULL,
            day                     DATE NOT NULL,
            input_tokens            BIGINT NOT NULL,
            output_tokens           BIGINT NOT NULL,
            cache_read_tokens       BIGINT NOT NULL,
            cache_creation_tokens   BIGINT NOT NULL,
            call_count              BIGINT NOT NULL,
            cost_usd                NUMERIC,
            cost_zar                NUMERIC,
            price_version           TEXT,
            fx_version              TEXT,
            PRIMARY KEY (tenant_id, purpose, day)
        )
    """)
    op.execute("""
        COMMENT ON TABLE tenant_usage_daily IS
        'Ticket #46. One row per tenant, purpose and day, derived by app.worker.tasks.runtime.usage.rollup_model_calls from the tenant model_calls ledger. The counts are summed facts; the money is a reading of those facts through app.domain.pricing, stamped with price_version and fx_version. NULL money means the price book refused a model in that group, and the tokens beside it say how much went unpriced.'
    """)
    # The key covers a tenant's own reads. A platform report asks what every tenant
    # spent yesterday, which starts at the day and has no tenant to lead with.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tenant_usage_daily_day
        ON tenant_usage_daily (day)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tenant_usage_daily_day")
    op.execute("DROP TABLE IF EXISTS tenant_usage_daily")
