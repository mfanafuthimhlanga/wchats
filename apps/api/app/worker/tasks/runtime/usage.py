"""rollup_model_calls_beat: yesterday's ledger, priced, into tenant_usage_daily (#46).

WHAT IT DOES
    Once a day, for every tenant that has a provisioned database, it reads that
    tenant's `model_calls` rows for one CAT day, prices each call through
    `app.domain.pricing`, and upserts one row per (tenant, purpose, day) into the
    control table `tenant_usage_daily` (control migration 0020). The ledger keeps
    the tokens, which are the fact. This table carries the money, which is a
    reading of that fact against a versioned book.

THE DAY IS A CAT CALENDAR DATE
    Decision #22 puts CAT on every report and every rollup, so `day` names the
    South African calendar date a tenant recognises, never the UTC one. CAT is
    UTC+2 and observes no daylight saving, so the date maps to a fixed window that
    opens at 22:00 UTC the evening before and closes at 22:00 UTC on the date
    itself. The beat fires at 00:30 UTC, two and a half hours after the day it
    prices closed.

ACKS_LATE AND IDEMPOTENCY, BOTH
    `acks_late=True` means a worker that dies mid-rollup gets the message back.
    The idempotency that makes redelivery safe is the upsert. The primary key is
    (tenant_id, purpose, day), so a second run for the same day lands on the row
    the first run wrote and overwrites it with the same values. Running it twice
    yields the same rows, and re-running it against a corrected price book is how
    a day gets re-derived.

NO CONNECTION STRING IN THE TASK ARGUMENTS (project rule 1)
    The only argument is an optional `day` override, an ISO date, for re-deriving a
    named day by hand. Every connection string is fetched from the control DB and
    decrypted inside the task, at the moment of the read.

A MODEL THE BOOK CANNOT PRICE DOES NOT KILL THE ROLLUP
    `app.domain.usage_rollup` catches `UnknownPrice` per purpose group. The group
    is written with its tokens and its call count and NULL costs and NULL versions,
    and this task logs the group loudly with the provider, the model and how many
    calls named it. The gap then sits in the table as tokens spent for no recorded
    cost, which is visible, instead of vanishing with a crashed task, which is not.
    The same holds for a missing fx rate, which takes the rand and leaves the
    dollars.

A TENANT THAT CANNOT BE REACHED IS SKIPPED, NOT FATAL
    A decrypt failure or an unreachable database logs one event naming the tenant
    and moves on, so one tenant's Neon project being down does not lose every other
    tenant's day. A tenant with more than one agent database is summed across all
    of them or skipped whole, because a row built from half a tenant's databases
    would read as a complete day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import psycopg2
import structlog
from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.domain.model_call import ModelCall
from app.domain.pricing import CAT
from app.domain.usage_rollup import PurposeUsage, roll_up
from app.models.agent import Agent
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

#: The ledger columns, in the order `record_model_call` writes them. Pinned against
#: `app.core.model_client._COLUMNS` by a test, because one writer and one reader
#: that disagree produce rows nobody notices are wrong.
LEDGER_COLUMNS = (
    "purpose",
    "provider",
    "requested_model",
    "served_model",
    "model_source",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "at",
    "tenant_id",
    "agent_id",
    "job_id",
)

#: Half open on purpose. A call at exactly midnight CAT belongs to the day that
#: opens, never to both days.
SELECT_CALLS = (
    "SELECT " + ", ".join(LEDGER_COLUMNS) + " FROM model_calls WHERE at >= %s AND at < %s"
)

#: Every derived column is overwritten on conflict, so a re-derive against a
#: corrected book leaves nothing of the old figure behind.
UPSERT_USAGE = """
    INSERT INTO tenant_usage_daily (
        tenant_id, purpose, day,
        input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
        call_count, cost_usd, cost_zar, price_version, fx_version
    ) VALUES (
        :tenant_id, :purpose, :day,
        :input_tokens, :output_tokens, :cache_read_tokens, :cache_creation_tokens,
        :call_count, :cost_usd, :cost_zar, :price_version, :fx_version
    )
    ON CONFLICT (tenant_id, purpose, day) DO UPDATE SET
        input_tokens = EXCLUDED.input_tokens,
        output_tokens = EXCLUDED.output_tokens,
        cache_read_tokens = EXCLUDED.cache_read_tokens,
        cache_creation_tokens = EXCLUDED.cache_creation_tokens,
        call_count = EXCLUDED.call_count,
        cost_usd = EXCLUDED.cost_usd,
        cost_zar = EXCLUDED.cost_zar,
        price_version = EXCLUDED.price_version,
        fx_version = EXCLUDED.fx_version
"""


def day_window(day: str | None, now: datetime | None = None) -> tuple[date, datetime, datetime]:
    """The CAT date to price, and the half-open UTC window that holds it.

    "Yesterday" is read in CAT as well, so the 00:30 UTC beat prices the CAT date
    that closed at 22:00 UTC two and a half hours earlier, not the UTC date that
    closed thirty minutes earlier.

    Args:
        day: an ISO CAT date to re-derive, or None for the CAT day that just closed.
        now: the instant "yesterday" is measured from. Injected so a test does not
             depend on the wall clock.

    Returns:
        (the CAT date, its opening instant in UTC, the next one). A CAT date opens
        at 22:00 UTC on the evening before it.

    Raises:
        ValueError: `day` is not an ISO date. A rollup for an unparseable day would
                    otherwise silently price whatever `fromisoformat` guessed.
    """
    if day is not None:
        on = date.fromisoformat(day)
    else:
        on = ((now or datetime.now(timezone.utc)).astimezone(CAT) - timedelta(days=1)).date()
    start = datetime(on.year, on.month, on.day, tzinfo=CAT).astimezone(timezone.utc)
    return on, start, start + timedelta(days=1)


def tenant_dsn_ciphertexts(db) -> dict[str, list[bytes]]:
    """Tenant id -> the encrypted connection string of each of its databases.

    Returns ciphertext, not plaintext. Nothing is decrypted until the tenant it
    belongs to is the one being read, so a credential lives for one read and never
    sits in a dict spanning the whole fan-out.
    """
    rows = db.execute(
        select(Agent.tenant_id, Agent.neon_connection_string).where(
            Agent.neon_connection_string.is_not(None)
        )
    ).all()
    by_tenant: dict[str, list[bytes]] = {}
    for tenant_id, ciphertext in rows:
        by_tenant.setdefault(str(tenant_id), []).append(ciphertext)
    return by_tenant


def read_calls(dsn: str, start: datetime, end: datetime) -> list[ModelCall]:
    """One database's ledger rows for the window, as ModelCall records.

    Args:
        dsn:   the decrypted tenant connection string. It arrives here and nowhere
               else, and no record built from these rows has a field that could
               hold it.
        start: the window's opening instant, included.
        end:   the window's closing instant, excluded.
    """
    conn = psycopg2.connect(dsn, connect_timeout=settings.TENANT_DB_CONNECT_TIMEOUT_S)
    try:
        with conn.cursor() as cur:
            cur.execute(SELECT_CALLS, (start, end))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [ModelCall(**dict(zip(LEDGER_COLUMNS, row))) for row in rows]


def tenant_calls(ciphertexts: list[bytes], start: datetime, end: datetime) -> list[ModelCall]:
    """Every call one tenant made in the window, across every database it owns.

    Raises whatever the decrypt or the read raises. The caller skips the tenant on
    a failure, so a tenant is summed across all its databases or not at all.
    """
    calls: list[ModelCall] = []
    for ciphertext in ciphertexts:
        calls.extend(read_calls(fernet_decrypt(ciphertext), start, end))
    return calls


def write_usage(db, tenant_id: str, on: date, row: PurposeUsage) -> None:
    """Upsert one (tenant, purpose, day) row into the control table."""
    db.execute(
        text(UPSERT_USAGE),
        {
            "tenant_id": tenant_id,
            "purpose": row.purpose,
            "day": on,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cache_read_tokens": row.cache_read_tokens,
            "cache_creation_tokens": row.cache_creation_tokens,
            "call_count": row.call_count,
            "cost_usd": row.cost_usd,
            "cost_zar": row.cost_zar,
            "price_version": row.price_version,
            "fx_version": row.fx_version,
        },
    )


def log_gaps(tenant_id: str, on: date, row: PurposeUsage) -> None:
    """Say out loud what went unpriced, by provider, model and count."""
    for gap in row.price_gaps:
        log.error(
            "rollup_model_calls.unpriced",
            tenant_id=tenant_id,
            day=on.isoformat(),
            purpose=row.purpose,
            provider=gap.provider,
            served_model=gap.served_model,
            call_count=gap.call_count,
        )
    if row.unrated_call_count:
        log.error(
            "rollup_model_calls.unrated",
            tenant_id=tenant_id,
            day=on.isoformat(),
            purpose=row.purpose,
            call_count=row.unrated_call_count,
        )


@celery_app.task(
    bind=True,
    acks_late=True,
    queue="runtime",
    name="app.worker.tasks.runtime.usage.rollup_model_calls_beat",
)
def rollup_model_calls_beat(self, day: str | None = None) -> dict:
    """Price one CAT day of every tenant's model calls into tenant_usage_daily.

    NOTHING HERE RETRIES, AND A LOST DAY IS STILL RECOVERABLE
        The upsert lands on the (tenant, purpose, day) primary key, so running the
        task again for a day overwrites the rows it wrote before. That is what
        makes recovery cheap and a retry policy redundant. `acks_late=True` puts
        the message back on the queue when a worker dies, and a redelivered run
        reads the clock afresh, so it re-derives the same CAT date any time before
        midnight CAT. After that the `day` override re-derives a named day by hand,
        which is the path a corrected price book takes anyway.

    Args:
        day: an ISO CAT date to re-derive, or None for yesterday in CAT. This is
             the only argument. Connection strings are fetched and decrypted inside
             this task (project rule 1).

    Returns:
        {"day", "tenants_done", "tenants_skipped", "rows_written"}.
    """
    on, start, end = day_window(day)
    done = 0
    skipped = 0
    written = 0
    with get_sync_db() as db:
        for tenant_id, ciphertexts in sorted(tenant_dsn_ciphertexts(db).items()):
            try:
                calls = tenant_calls(ciphertexts, start, end)
            except Exception as exc:
                log.error(
                    "rollup_model_calls.tenant_skipped",
                    tenant_id=tenant_id,
                    day=on.isoformat(),
                    error=str(exc),
                )
                skipped += 1
                continue
            for row in roll_up(calls):
                log_gaps(tenant_id, on, row)
                write_usage(db, tenant_id, on, row)
                written += 1
            db.commit()
            done += 1
    log.info(
        "rollup_model_calls.complete",
        day=on.isoformat(),
        tenants_done=done,
        tenants_skipped=skipped,
        rows_written=written,
    )
    return {
        "day": on.isoformat(),
        "tenants_done": done,
        "tenants_skipped": skipped,
        "rows_written": written,
    }
