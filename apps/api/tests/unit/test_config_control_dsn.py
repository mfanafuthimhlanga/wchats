"""A control DSN that SQLAlchemy cannot parse refuses to boot, naming the field.

The Railway staging API crash-looped at import: `create_async_engine` in
`app/core/database.py` raised `sqlalchemy.exc.ArgumentError: Could not parse
SQLAlchemy URL from given URL string`. Three different malformed pastes give a
byte-identical traceback, and it names neither the variable nor the fault, so
the only way to tell an env-file line from a psql wrapper from an unresolved
`${{Service.VAR}}` reference was to open the dashboard and read every value.

The validator moves that failure to config load and puts the diagnosis in the
message. `hide_input_in_errors=True` means the message is the whole signal —
pydantic will not echo the value — so these tests assert the field name is in
it, and that the password is not.

The second failure mode is quieter, because the URL parses. asyncpg 0.31.0 and
psycopg2 2.9.12 are mirror images on the ssl query params: asyncpg takes `ssl=`
and raises TypeError on `sslmode=`/`channel_binding=`, psycopg2 takes those two
and calls `ssl=` an invalid dsn option. SQLAlchemy passes query params through
untranslated, so the wrong pair boots the process and then kills every
connection. Neon hands out `?sslmode=require&channel_binding=require`, which is
right for the sync URL and fatal for the async one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

GOOD_ASYNC = "postgresql+asyncpg://user:your-password@db.example.invalid/neondb?ssl=require"
GOOD_SYNC = "postgresql://user:your-password@db.example.invalid/neondb?sslmode=require&channel_binding=require"


def test_the_async_control_dsn_boots_and_is_returned_unchanged():
    assert Settings(CONTROL_DB_URL=GOOD_ASYNC).CONTROL_DB_URL == GOOD_ASYNC


def test_the_sync_control_dsn_boots_and_is_returned_unchanged():
    assert Settings(CONTROL_DB_SYNC_URL=GOOD_SYNC).CONTROL_DB_SYNC_URL == GOOD_SYNC


@pytest.mark.parametrize(
    "mispaste",
    [
        pytest.param(f"CONTROL_DB_URL={GOOD_ASYNC}", id="env-file-line"),
        pytest.param(f"psql '{GOOD_ASYNC}'", id="neon-psql-tab"),
        pytest.param(f'"{GOOD_ASYNC}"', id="double-quoted"),
        pytest.param(f"'{GOOD_ASYNC}'", id="single-quoted"),
        pytest.param("${{Postgres.DATABASE_URL}}", id="unresolved-railway-ref"),
        pytest.param("", id="empty"),
    ],
)
def test_a_mispaste_refuses_to_boot_and_the_error_names_the_field(mispaste):
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL=mispaste)
    assert "CONTROL_DB_URL" in str(exc_info.value)


def test_surrounding_whitespace_refuses_rather_than_being_stripped_silently():
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL=f" {GOOD_ASYNC} ")
    rendered = str(exc_info.value)
    assert "CONTROL_DB_URL" in rendered
    assert "whitespace" in rendered


def test_a_trailing_newline_from_a_dashboard_paste_refuses():
    """The line-break check answers this one before the whitespace check does."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL=f" {GOOD_ASYNC}\n")
    assert "CONTROL_DB_URL" in str(exc_info.value)


def test_the_sync_dsn_in_the_async_slot_refuses_and_names_the_driver_found():
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL=GOOD_SYNC)
    rendered = str(exc_info.value)
    assert "CONTROL_DB_URL" in rendered
    assert "'postgresql'" in rendered


def test_the_async_dsn_in_the_sync_slot_refuses_and_names_the_driver_found():
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_SYNC_URL=GOOD_ASYNC)
    rendered = str(exc_info.value)
    assert "CONTROL_DB_SYNC_URL" in rendered
    assert "'postgresql+asyncpg'" in rendered


def test_the_error_echoes_the_scheme_prefix_and_never_the_password():
    """Credentials sit after "://", which is why only the prefix is quoted back."""
    password = "s3cr3t-do-not-leak"
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL=f'"postgresql+asyncpg://u:{password}@ep-x.aws.neon.tech/neondb"')
    rendered = str(exc_info.value)
    assert "CONTROL_DB_URL" in rendered
    assert password not in rendered, (
        "the DSN's password reached the ValidationError text, which lands in "
        f"stderr and the Railway deploy log.\nrendered error:\n{rendered}"
    )


@pytest.mark.parametrize(
    "sync_dsn",
    [
        "postgresql+psycopg2://user:your-password@db.example.invalid/neondb",
        "postgresql+psycopg://user:your-password@db.example.invalid/neondb",
    ],
)
def test_both_psycopg_spellings_are_accepted_for_the_sync_engine(sync_dsn):
    """Alembic runs on psycopg2 today; `scripts/probe_environment.py` already
    counts `postgresql+psycopg` as a sync form, so refusing it here would make
    the two disagree."""
    assert Settings(CONTROL_DB_SYNC_URL=sync_dsn).CONTROL_DB_SYNC_URL == sync_dsn


def test_neons_string_with_only_the_scheme_swapped_refuses_for_the_async_engine():
    """The next failure after fixing the paste, and it parses cleanly.

    Copying Neon's connection string and changing `postgresql://` to
    `postgresql+asyncpg://` is the obvious move, and it leaves `sslmode` and
    `channel_binding` behind for a driver that raises TypeError on both.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL=GOOD_SYNC.replace("postgresql://", "postgresql+asyncpg://"))
    rendered = str(exc_info.value)
    assert "CONTROL_DB_URL" in rendered
    assert "sslmode" in rendered


def test_the_asyncpg_ssl_param_refuses_for_the_sync_engine():
    """psycopg2 calls `ssl` an invalid dsn option, so it may not reach the sync URL."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_SYNC_URL=GOOD_SYNC.split("?")[0] + "?ssl=require")
    rendered = str(exc_info.value)
    assert "CONTROL_DB_SYNC_URL" in rendered
    assert "ssl" in rendered


def test_each_engines_own_ssl_params_still_boot():
    """Positive control: the query gate refuses the wrong pair, not every pair.

    `GOOD_ASYNC` carries `ssl=require` and `GOOD_SYNC` carries
    `sslmode=require&channel_binding=require`, which is what each driver wants.
    """
    assert Settings(CONTROL_DB_URL=GOOD_ASYNC).CONTROL_DB_URL == GOOD_ASYNC
    assert Settings(CONTROL_DB_SYNC_URL=GOOD_SYNC).CONTROL_DB_SYNC_URL == GOOD_SYNC


def test_an_embedded_line_break_refuses_instead_of_landing_in_the_database_name():
    """make_url ACCEPTS this one: host stays 'h', database becomes 'db\\nEXTRA=1'.

    A raw multi-line editor paste produces it, and nothing downstream complains
    until a query hits a database that does not exist.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL=f"{GOOD_ASYNC}\nEXTRA=1")
    rendered = str(exc_info.value)
    assert "CONTROL_DB_URL" in rendered
    assert "line break" in rendered

    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL=f"\r\n{GOOD_ASYNC}")
    rendered = str(exc_info.value)
    assert "CONTROL_DB_URL" in rendered
    assert "line break" in rendered


def test_a_non_numeric_port_refuses_and_the_error_still_names_the_field():
    """make_url raises ValueError here, not ArgumentError.

    A guard catching only ArgumentError lets this one past and surfaces
    SQLAlchemy's own `invalid literal for int()`, which names nothing.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL="postgresql+asyncpg://user:your-password@db.example.invalid:notaport/neondb")
    assert "CONTROL_DB_URL" in str(exc_info.value)


def test_a_truncated_dsn_with_no_host_refuses_rather_than_dying_at_connect():
    """The scheme alone parses, drivername and all, with host=None."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(CONTROL_DB_URL="postgresql+asyncpg://")
    rendered = str(exc_info.value)
    assert "CONTROL_DB_URL" in rendered
    assert "no host" in rendered


def test_the_live_neon_pooler_shapes_still_boot():
    """Positive control against the shape actually read from the live `.env`.

    Pooler host, database `neondb`, one ssl-flavoured query key per engine.
    """
    live_async = "postgresql+asyncpg://user:your-password@db-pooler.example.invalid/neondb?ssl=require"
    live_sync = "postgresql://user:your-password@db-pooler.example.invalid/neondb?sslmode=require"
    assert Settings(CONTROL_DB_URL=live_async).CONTROL_DB_URL == live_async
    assert Settings(CONTROL_DB_SYNC_URL=live_sync).CONTROL_DB_SYNC_URL == live_sync
