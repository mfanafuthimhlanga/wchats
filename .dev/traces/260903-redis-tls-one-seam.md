# 260903 · redis-tls-one-seam (#144)

Branch `fix/redis-tls-one-seam` off `26ea5d9`. One logical change: every Redis client in
`apps/api` takes its TLS posture from one function, so `REDIS_TLS_INSECURE` decides
something everywhere instead of in one module out of fourteen.

## What changed

- `apps/api/app/core/redis_tls.py` (new): `redis_ssl_kwargs(url)`. Empty dict for a plain
  `redis://`; `CERT_REQUIRED` + `ssl_check_hostname` for `rediss://`; `CERT_NONE` only on
  `REDIS_TLS_INSECURE=True`, and it logs a warning every time it hands that back. The
  returned dict is what `redis.from_url` takes as keyword arguments and what Celery takes
  as `broker_use_ssl` / `redis_backend_use_ssl`.
- Fourteen call sites now ask it instead of naming a mode: `app/api/deps.py`,
  `app/services/agent_tools.py`, `app/services/transactional/enforcement.py`,
  `app/worker/celery_app.py`, the seven `app/worker/tasks/pipeline/*` modules and the
  three `app/worker/tasks/runtime/*` modules. Each diff is the kwargs lines, the import,
  and a comment that no longer narrates a constant.
- `apps/api/pyproject.toml`: import-linter contract "ssl has one home". `app` may not
  import `ssl`, `app.core.redis_tls` excepted.
- `apps/api/tests/unit/test_redis_tls_seam.py` (new): the seam over its three inputs, the
  three factories that build arguments inside a function driven with a `rediss://` URL and
  the flag off, and Celery's own validation of the dict for both schemes.
- `apps/api/tests/unit/test_capability_enforcement.py`: the WR-04 warning test patches
  `app.core.redis_tls.log`, since that is where the warning is emitted now.

## Decisions

- The gate is an import-linter contract, not a grep for `CERT_NONE` and not a source-scan
  test. `ssl` had exactly one use in this tree, choosing a verification mode, so banning
  the import bans every spelling of the mistake. It also keeps
  `SOURCE_ASSERTION_BASELINE` untouched, which `tests/unit/test_gates.py` goes red on
  additions to.
- Eleven of the fourteen sites build `_ssl_opts` at module import as a constant. Reloading
  eleven modules under patched settings costs more on a 4 GB box than the contract does,
  and the contract is the stronger guard: it fails on the import, before any value exists.
  The three sites with a factory function are driven for real.
- `celery_app.py` keeps its `if _ssl_opts else {}` guard on the conf update. Celery raises
  `E_REDIS_SSL_PARAMS_AND_SCHEME_MISMATCH` when an ssl option reaches a `redis://` scheme,
  so the empty dict must stay out of the config rather than be passed as empty.

## Observed

- RED, contract, before the move: `ssl has one home BROKEN`, 3 kept 1 broken, naming all
  fourteen edges from `app.api.deps -> ssl (l.19)` to
  `app.worker.tasks.runtime.validators -> ssl (l.28)`.
- RED, tests, before the move: `2 failed, 4 passed in 12.29s` —
  `app/api/deps.py` and `app/services/agent_tools.py` passed
  `ssl_cert_reqs=<VerifyMode.CERT_NONE: 0>` against `<VerifyMode.CERT_REQUIRED: 2>`.
- GREEN after: `lint-imports` 4 kept 0 broken; `test_redis_tls_seam.py` 8 passed in 8.31s;
  `test_capability_enforcement.py` + the seam file 39 passed in 8.13s.
- Mutation, `import ssl` added back to `app/worker/celery_app.py`: BROKEN, naming
  `app.worker.celery_app -> ssl (l.48)`. Restored, KEPT again at 4 kept 0 broken.
- Touched-module suites: 69 passed in 179.97s (agent_tools, agent_tools_contextvar,
  agent_task, validators); 42 passed in 16.16s (parse, chunk, embed, metadata, strategy
  tasks); 109 passed in 12.95s (retrieval, retrieved-chunk capture and persistence,
  gates); 20 passed in 3.37s (task_args, services).
- `scripts/gates.py static`: `static gates passed in 9.0s.` No lizard or source-assertion
  pin moved.
- `scripts/gates.py fast`: `4192 tests collected in 116.33s`, `fast gates passed in
  846.2s.` Collection is what proves the fourteen import changes reach every module that
  imports them, including the eleven task modules no focused suite touched.

## Left open

- The staging environment still has to be re-checked once this lands: the beat service's
  `ssl_cert_reqs=CERT_NONE` boot warning is the observable that should disappear, and a
  `rediss://` Upstash certificate that does not verify would now fail the connection
  rather than be accepted silently. That is a deploy-time observation, not one this box
  can make.
