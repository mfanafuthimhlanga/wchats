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

The ledger, and why the CI sweep it replaces was dangerous
---------------------------------------------------------
``.github/workflows/nightly.yml`` used to reclaim orphans by **listing every
project the API key could see and deleting the ones whose NAME matched
``startswith('vrd-') and 'e2e' in name``**. Two independent faults:

* It deletes by pattern over an entire account. Whatever the key is scoped to,
  the set it acts on is "everything visible that looks like a test" — which is
  a judgement about somebody else's data, made by a regex. The rule this repo
  runs on is the opposite: **only ever delete an id this run created.**
* It was already dead for its stated purpose. ``_project_slug(agent.name, tag)``
  (``app/services/neon.py``) slugifies the agent name, and the E2E agent is
  ``e2e-agent-{uuid}``, so the result never starts with ``vrd-``. The sweep
  matched nothing it was written to reclaim.

The replacement is this ledger. The E2E test appends each project id the moment
the control DB shows it — seconds after the Neon API returns, and long before
any assertion can fail — and removes it again once deletion is *verified*. The
nightly job then deletes exactly the ids left in the file. Nothing is listed,
nothing is matched by name, and an id that was never created cannot appear.

Nothing here touches the network or the environment at import time.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests
from sqlalchemy import text

_NEON_API_BASE = "https://console.neon.tech/api/v2"

#: Environment variable naming the run-scoped ledger of created project ids.
#: Unset means "no ledger" — every helper below then degrades to a no-op rather
#: than inventing a path, because a ledger nobody configured must never become a
#: list of ids some later job feels entitled to delete.
LEDGER_ENV = "WCHATS_NEON_PROJECT_LEDGER"


def ledger_path() -> Path | None:
    """The configured ledger file, or None when the run has no ledger."""
    raw = os.environ.get(LEDGER_ENV)
    return Path(raw) if raw else None


def record_created_project(project_id: str) -> None:
    """Append *project_id* to the ledger, if one is configured.

    Called as soon as the id is known — not at teardown. A run killed by a CI
    timeout never reaches its ``finally``, and an id recorded only there is an
    id nothing can reclaim.
    """
    path = ledger_path()
    if path is None or not project_id:
        return
    if project_id in ledger_ids():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{project_id}\n")
        fh.flush()
        os.fsync(fh.fileno())


def ledger_ids() -> list[str]:
    """Project ids currently outstanding in the ledger, oldest first."""
    path = ledger_path()
    if path is None or not path.exists():
        return []
    seen: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        pid = line.strip()
        if pid and pid not in seen:
            seen.append(pid)
    return seen


def forget_project(project_id: str) -> None:
    """Drop *project_id* from the ledger after its deletion was VERIFIED.

    Only ``delete_project`` returning normally justifies this call: it means a
    follow-up GET answered 404. Anything else leaves the id in the ledger so
    the nightly teardown step tries again.
    """
    path = ledger_path()
    if path is None or not path.exists():
        return
    remaining = [pid for pid in ledger_ids() if pid != project_id]
    path.write_text(
        "".join(f"{pid}\n" for pid in remaining), encoding="utf-8"
    )


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


def drain_ledger(api_key: str) -> list[str]:
    """Delete every id still outstanding in the ledger. Returns what leaked.

    This is the whole of the nightly job's reclamation step. It reads ids and
    nothing else — it never lists the account, never inspects a project name,
    and therefore cannot act on a project this suite did not create.
    """
    leaked: list[str] = []
    for project_id in ledger_ids():
        try:
            delete_project(project_id, api_key)
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed
            print(f"LEAKED {project_id}: {exc}")
            leaked.append(project_id)
        else:
            print(f"deleted {project_id}")
            forget_project(project_id)
    return leaked


def _main() -> int:
    """``python -m tests.e2e._neon_teardown`` — drain the ledger, loudly.

    Exits non-zero if anything is left behind, so a leak fails the CI job
    instead of scrolling past in a green log.
    """
    api_key = os.environ.get("NEON_API_KEY")
    if not api_key:
        print("NEON_API_KEY unset — nothing can be reclaimed")
        return 1
    outstanding = ledger_ids()
    if not outstanding:
        print("ledger empty — nothing to reclaim")
        return 0
    print(f"ledger holds {len(outstanding)} project id(s)")
    leaked = drain_ledger(api_key)
    if leaked:
        print(f"!!! NEON PROJECTS LEAKED — delete manually: {leaked}")
        return 1
    print("ledger drained")
    return 0


if __name__ == "__main__":  # pragma: no cover — CI entry point
    raise SystemExit(_main())
