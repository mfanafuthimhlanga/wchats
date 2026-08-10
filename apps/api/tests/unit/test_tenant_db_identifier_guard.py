"""`_tenant_db` interpolates a database name into DDL; the name must be checked.

`CREATE DATABASE` and `DROP DATABASE` take an IDENTIFIER, and identifiers cannot
be bound as query parameters — ``CREATE DATABASE :name`` is not valid SQL. So the
name is formatted into the statement, and the only thing standing between that
and arbitrary DDL is a check on the name itself.

Nothing exploits this today: the single caller builds
``f"wchats_stub_tenant_{uuid4().hex[:12]}"``, which is always hex. But these are
public helpers in a shared test-support module, and the argument "the current
caller happens to be safe" is a property of the caller, not of the function. The
next caller may pass a fixture parameter, a CLI argument or an env var.

No database is touched: every case here must be rejected BEFORE a connection is
opened, which is also what these tests assert by the fact that they pass on a
machine with no server reachable at the admin URL.
"""

from __future__ import annotations

import pytest

from tests.integration._tenant_db import (
    _MAX_IDENTIFIER_BYTES,
    _checked,
    create_tenant_database,
    drop_tenant_database,
)

_HOSTILE_NAMES = [
    pytest.param('x"; DROP DATABASE wchats_control; --', id="quote-escape"),
    pytest.param("wchats_control; SELECT 1", id="statement-separator"),
    pytest.param("has space", id="space"),
    pytest.param("Mixed_Case", id="uppercase-would-need-quoting-to-round-trip"),
    pytest.param("9starts_with_digit", id="leading-digit"),
    pytest.param("", id="empty"),
    pytest.param("dash-name", id="dash"),
    pytest.param("unicode_ñ", id="non-ascii"),
]


@pytest.mark.parametrize("db_name", _HOSTILE_NAMES)
def test_create_rejects_names_that_are_not_plain_identifiers(db_name):
    with pytest.raises(ValueError, match="unsafe database name"):
        create_tenant_database(db_name)


@pytest.mark.parametrize("db_name", _HOSTILE_NAMES)
def test_drop_rejects_names_that_are_not_plain_identifiers(db_name):
    """The drop half matters as much: it is the one that says DROP DATABASE."""
    with pytest.raises(ValueError, match="unsafe database name"):
        drop_tenant_database(db_name)


def test_an_over_long_name_is_refused_rather_than_silently_truncated():
    """Postgres truncates identifiers at 63 bytes.

    A create that is truncated and a drop that is truncated the same way happen
    to agree, but the URL returned by `create_tenant_database` carries the FULL
    name — so the caller connects to a database that does not exist, and the
    failure surfaces far from its cause.
    """
    too_long = "w" + "x" * _MAX_IDENTIFIER_BYTES
    with pytest.raises(ValueError, match="identifier limit"):
        create_tenant_database(too_long)


def test_the_name_the_fixture_actually_uses_is_accepted():
    """The guard must not have been made safe by refusing everything.

    Asserted against the validator directly rather than by calling
    `create_tenant_database`, because that call would succeed on this machine —
    a real local Postgres is running — and leave a stray database behind. A test
    of a name check has no business creating a database.
    """
    import uuid

    valid = f"wchats_stub_tenant_{uuid.uuid4().hex[:12]}"
    assert _checked(valid) == valid
    assert _checked("wchats_control") == "wchats_control"
    assert _checked("a") == "a"
