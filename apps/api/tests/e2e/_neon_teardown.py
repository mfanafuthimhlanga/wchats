"""Teardown helpers for the real-Neon E2E test.

A leaked Neon project is irreversible spend and a consumed slot in the
account's project quota, so teardown is the part of that test that must work
even when everything else has failed.

Two defects this module exists to close:

1. **The project id was only known on the success path.** The E2E test read
   ``agents.neon_project_id`` at step 5, *after* asserting the agent reached
   "ready". Every run where provisioning failed — the runs that matter — left
   the local variable at ``None``, so the ``finally`` block deleted nothing
   while a real project sat in the account. ``resolve_project_id`` re-reads the
   id from the control DB during teardown instead of trusting a variable the
   failing path never assigned. ``provision_neon`` commits the id immediately
   after the Neon API returns, precisely so it survives a later crash.

2. **Deletion went through the neon_api SDK inside a bare ``except``.** If the
   SDK were absent or its call signature drifted, the exception was swallowed
   and the leak was silent. Deletion here uses ``requests`` like the rest of
   the codebase, and is *verified* by a follow-up GET: a project that is still
   there after a delete attempt is reported as a leak, never assumed gone.

Nothing here touches the network or the environment at import time.
"""

from __future__ import annotations

import requests
from sqlalchemy import text

_NEON_API_BASE = "https://console.neon.tech/api/v2"


def resolve_project_id(db, agent_id, known: str | None = None) -> str | None:
    """Return the Neon project id to tear down for *agent_id*.

    Args:
        db:       Open SQLAlchemy session on the control DB.
        agent_id: The agent whose project should be torn down.
        known:    Project id the test already captured, if it got that far.

    Returns:
        The project id, or None if the agent row never recorded one (meaning
        the Neon API call never returned, so nothing was created).
    """
    if known:
        return known
    row = db.execute(
        text("SELECT neon_project_id FROM agents WHERE id = :id"),
        {"id": str(agent_id)},
    ).fetchone()
    return row[0] if row and row[0] else None


def delete_project(project_id: str, api_key: str, timeout: int = 30) -> None:
    """Delete a Neon project and confirm it is gone.

    Only ever called with a project id this run created and read back from its
    own agent row — never a name pattern, never a sweep.

    Raises:
        RuntimeError: If the project is still listed after the delete attempt.
                      A leak is loud by construction; the caller prints the id.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    requests.delete(
        f"{_NEON_API_BASE}/projects/{project_id}", headers=headers, timeout=timeout
    )

    # Verify rather than trust the status code: 'deleted' is a claim until the
    # project stops answering.
    probe = requests.get(
        f"{_NEON_API_BASE}/projects/{project_id}", headers=headers, timeout=timeout
    )
    if probe.status_code != 404:
        raise RuntimeError(
            f"Neon project {project_id} still present after delete "
            f"(probe returned {probe.status_code})"
        )
