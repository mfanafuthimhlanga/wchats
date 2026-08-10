"""Neon API stub, installed **inside the Celery worker subprocess**.

Why this module exists
----------------------
``test_provision.py`` and ``test_chain.py`` used to wrap their dispatch in
``respx.mock(...)``. That mock was inert for two independent reasons, and the
tests had therefore been making real, unauthenticated calls to
``console.neon.tech`` on every run:

1. **Wrong library.** ``respx`` patches ``httpx``. ``app/services/neon.py`` and
   the State-B re-fetch path in ``app/worker/tasks/pipeline/provision.py`` both
   use ``requests``. ``httpx`` is never involved in a Neon call.
2. **Wrong process.** ``provision_neon`` runs in the Celery worker subprocess
   started by the ``celery_worker`` fixture. A transport patch installed in the
   pytest process cannot affect a different process's socket calls.

Observed before the fix (worker stderr, verbatim)::

    provision_neon.neon_api_error ... status_code=401
    detail='{"request_id":"58fd7378-...","message":"supplied credentials do not pass authentication"}'

So the stub has to live where the code runs. It is loaded by
``celery worker --include=tests.integration._neon_stub``; Celery imports
``--include`` modules during worker start-up, before the consumer accepts any
task, so the patch is always in place before the first Neon call.

What is faked, and what is not
------------------------------
Only the **transport** is replaced: ``HTTPAdapter.send``, the last hop before
the socket. Everything above it is the real code path — ``requests`` builds the
real ``PreparedRequest``, the real URL and query string, the real
``Authorization`` header; ``build_response`` constructs the real ``Response``;
``app.services.neon`` does its real ``r.ok`` triage and real ``r.json()``
parsing. That is the tightest honest boundary available: a URL typo, a bad
param name, or a response-shape misread still fails the test.

Fail-closed by construction
---------------------------
* Any request to ``console.neon.tech`` that this stub does not explicitly model
  raises. It is never proxied to the network — an unmodelled endpoint is a
  loud failure, not a silent live call.
* Requests to every other host pass through untouched (the worker still talks
  to Postgres and Redis for real).
* Import fails immediately unless the stub is fully configured, so a
  half-configured worker cannot come up looking healthy.
* The ``installed`` record written at import is the fixture's proof that the
  stub is actually in this process. Without it the fixture fails the test
  rather than letting it run against the real API — a test that silently falls
  back to the network while reporting a pass is the tautology this repo exists
  to prevent.

Configuration (environment, set by the ``neon_stub_worker`` fixture):
    WCHATS_NEON_STUB_URI   Connection URI handed back as both the pooled and the
                           direct URI. The fixture points this at a throwaway
                           local tenant database, so ``apply_migrations`` runs
                           the real tenant Alembic chain against real Postgres.
    WCHATS_NEON_STUB_LOG   Path to a JSONL call journal the test process reads
                           back to assert on what the worker actually sent.
"""

from __future__ import annotations

import io
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import requests
import urllib3

NEON_HOST = "console.neon.tech"

_ENV_URI = "WCHATS_NEON_STUB_URI"
_ENV_LOG = "WCHATS_NEON_STUB_LOG"

_log_lock = threading.Lock()


class NeonStubError(AssertionError):
    """Raised when the worker sends a Neon request the stub does not model.

    Deliberately an AssertionError: it surfaces as a task failure with the
    offending URL in the message, instead of quietly reaching the real API.
    """


def _record(entry: dict) -> None:
    """Append one JSONL record to the call journal, flushed immediately.

    Flushed on every write because the reader is a different process polling
    the file while the worker is still running.
    """
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    line = json.dumps(entry, sort_keys=True)
    with _log_lock:
        with open(os.environ[_ENV_LOG], "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def _json_response(adapter, request, status: int, payload: dict):
    """Build a genuine requests.Response from a canned JSON body.

    Uses the adapter's own ``build_response`` over a urllib3 response so the
    Response object is assembled by the real requests code, not hand-rolled.
    """
    body = json.dumps(payload).encode("utf-8")
    raw = urllib3.HTTPResponse(
        body=io.BytesIO(body),
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
        status=status,
        preload_content=False,
        decode_content=False,
        request_method=request.method,
    )
    return adapter.build_response(request, raw)


def _handle(adapter, request):
    """Serve one Neon API request, or raise if it is not modelled."""
    split = urlsplit(request.url)
    path = split.path
    query = parse_qs(split.query)
    method = request.method.upper()

    # POST /api/v2/projects — create a project.
    # A fresh id per call is what makes the idempotency assertion meaningful:
    # a second create would be visibly a *different* project, not a silent
    # repeat of the same canned id.
    if method == "POST" and path == "/api/v2/projects":
        project_id = f"stub-proj-{uuid.uuid4().hex[:12]}"
        sent = json.loads(request.body) if request.body else {}
        _record(
            {
                "event": "call",
                "method": method,
                "path": path,
                "project_id": project_id,
                "project_name": sent.get("project", {}).get("name"),
            }
        )
        return _json_response(
            adapter,
            request,
            200,
            {
                "project": {
                    "id": project_id,
                    "name": sent.get("project", {}).get("name", "stub-project"),
                    "region_id": sent.get("project", {}).get("region_id", "aws-us-east-1"),
                }
            },
        )

    # GET /api/v2/projects/{id}/connection_uri?pooled=true|false
    if method == "GET" and path.endswith("/connection_uri"):
        project_id = path.split("/")[4]
        _record(
            {
                "event": "call",
                "method": method,
                "path": "/api/v2/projects/{id}/connection_uri",
                "project_id": project_id,
                "pooled": (query.get("pooled") or [None])[0],
                "database_name": (query.get("database_name") or [None])[0],
                "role_name": (query.get("role_name") or [None])[0],
            }
        )
        return _json_response(adapter, request, 200, {"uri": os.environ[_ENV_URI]})

    _record({"event": "unmodelled", "method": method, "path": path})
    raise NeonStubError(
        f"Neon stub has no route for {method} {path}. The stub never proxies to "
        f"the real API, so this request was refused rather than sent."
    )


def install() -> None:
    """Patch the requests transport for console.neon.tech, process-wide."""
    real_send = requests.adapters.HTTPAdapter.send

    def send(self, request, **kwargs):
        if urlsplit(request.url).hostname == NEON_HOST:
            return _handle(self, request)
        return real_send(self, request, **kwargs)

    requests.adapters.HTTPAdapter.send = send
    _record({"event": "installed", "pid": os.getpid()})


# Import-time configuration check. Raising here aborts worker start-up, which
# the fixture detects as a missing 'installed' record — far better than a
# worker that comes up without the stub and reaches the live API.
for _var in (_ENV_URI, _ENV_LOG):
    if not os.environ.get(_var):
        raise RuntimeError(
            f"{__name__} requires {_var}. It is only ever loaded via "
            f"`celery worker --include`, by the neon_stub_worker fixture."
        )

install()
