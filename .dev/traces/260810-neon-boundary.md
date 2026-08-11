# Trace — the Neon test boundary (BACKLOG 1.7)

**2026-08-10 · `chore/local-postgres` · commits `115f052`, `eb836c1`, `0e2efe7`**
Written 2026-08-11: this work landed with a `.dev/reference/` note but no trace, which the
adversarial review flagged. Reconstructed from `.dev/reference/neon-test-boundary.md` and
`git log --stat`; it is a record, not a fresh investigation.

## What changed

- `tests/integration/_neon_stub.py` (new) — patches `requests.adapters.HTTPAdapter.send` for
  `console.neon.tech` **inside the Celery worker subprocess**, loaded by
  `celery worker --include`. Writes a JSONL call journal the test process reads back.
- `tests/integration/_tenant_db.py` (new) — throwaway `CREATE DATABASE` per module.
- `tests/integration/_paths.py`, `conftest.py` — `neon_stub_worker` fixture; worker spawned as
  `sys.executable -m celery`; readiness waits replaced with proof-based ones (`0e2efe7`).
- `tests/integration/test_provision.py`, `test_chain.py` — respx removed; assertions moved onto
  the stub journal.
- `tests/e2e/_neon_teardown.py`, `tests/unit/test_neon_teardown.py` (new) — id-scoped deletion,
  verified by a 404 probe; project id re-read from the control DB at teardown.

## Decisions

- **Stub the transport, not the client.** `HTTPAdapter.send` is the last hop before the socket,
  so `requests` still builds the real request and `app.services.neon` still does its real `r.ok`
  triage and `r.json()` parse. A URL typo or a response-shape misread still fails the test.
- **Fail closed twice over.** Unmodelled endpoint → raise, never proxy. Stub not installed →
  the fixture fails the test. Worker's `NEON_API_KEY` overwritten with a placeholder.
- **Stub rather than provision for real.** Neither test asserts a property of Neon; both assert
  properties of our code. `test_provision_neon_idempotency` dispatches twice on purpose, so the
  failure it exists to catch is precisely the run that would leak a second real project.

## Deviations from the row it closed

`1.7` blamed the placeholder key at `conftest.py` and framed the choice as "a real key, or mock
the client". **That diagnosis was wrong and its suggested fix would have cost money.** The mock
those tests carried had never intercepted anything — respx patches `httpx`, the code uses
`requests`, and the mock lived in the wrong process. Exporting a working key would have turned
four provisioning dispatches per run into an unattended project factory with no teardown
anywhere in either file.

Env-var mechanics were confirmed rather than assumed: an exported `NEON_API_KEY` survives
`setdefault` and outranks `apps/api/.env` in pydantic-settings, and `subprocess` passes it to the
worker via `os.environ.copy()`.

## What it missed, found by the 2026-08-11 review

- `tests/integration/test_worker_kill.py` was a **third** un-stubbed provisioning dispatch, with
  all three defects this task fixed in its two siblings and no Neon teardown at all. Not reported.
- `.github/workflows/nightly.yml:73` deleted Neon projects **by name pattern** across the whole
  account. Not reported, despite `test_neon_e2e.py`'s teardown being edited in the same commit.
- `wait_until_installed` claimed to prove installation "in the worker's own pid" and compared no
  pid.

All three fixed at `d4f65e2`; see `.dev/reference/260811-review-fix-mutation-proofs.md`.
