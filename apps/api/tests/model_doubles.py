"""Doubles for the call sites that build their client through the factory.

Ticket #47 moved every direct-API construction into `app.core.model_client`. A
site now takes a `LedgerContext` and asks it for a client per call, so a test
that used to patch a module-level `ANTHROPIC_CLIENT` patches the factory
instead. One target covers every site, and the assertions stay where they were:
on the kwargs the provider receives.

`ledger()` builds a REAL `LedgerContext`, not a stand-in. Its recorder collects
rows in a list rather than opening a database, which is the only part a unit
test cannot afford.
"""

from __future__ import annotations

from unittest.mock import patch

from app.core.model_client import LedgerContext
from app.domain.model_call import ModelCall

#: Ids a unit test bills to. Real UUIDs, because `ModelCall` and the ledger
#: columns take UUID strings and a row built from "t1" would never insert.
TENANT_ID = "11111111-1111-1111-1111-111111111111"
AGENT_ID = "22222222-2222-2222-2222-222222222222"
JOB_ID = "33333333-3333-3333-3333-333333333333"


def ledger(rows: list[ModelCall] | None = None) -> LedgerContext:
    """A LedgerContext whose recorder appends to `rows` instead of writing."""
    collected = [] if rows is None else rows
    return LedgerContext(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        job_id=JOB_ID,
        recorder=collected.append,
    )


def factory(client):
    """Patch the factory so every site under test is handed `client`.

    A context manager and a decorator, the same as any `patch(...)`.
    """
    return patch("app.core.model_client.make_client", return_value=client)
