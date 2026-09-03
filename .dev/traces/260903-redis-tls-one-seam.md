# 260903 · redis-tls-one-seam (#144)

Branch `fix/redis-tls-one-seam` off `26ea5d9`. One logical change: every Redis client in
`apps/api` takes its TLS posture from one function, so `REDIS_TLS_INSECURE` decides
something everywhere instead of in one module out of fourteen.

## What changed

- `apps/api/app/core/redis_tls.py` (new): `redis_ssl_kwargs(url)`. Empty dict for a plain
  `redis://`; `CERT_REQUIRED` + `ssl_check_hostname` for `rediss://`; `CERT_NONE` only on
  `REDIS_TLS_INSECURE=True`, and it warns once per process per URL when it hands that
  back. The returned dict is what `redis.from_url` takes as keyword arguments and what
  Celery takes as `broker_use_ssl` / `redis_backend_use_ssl`.
- Fourteen call sites now ask it instead of naming a mode: `app/api/deps.py`,
  `app/services/agent_tools.py`, `app/services/transactional/enforcement.py`,
  `app/worker/celery_app.py`, the seven `app/worker/tasks/pipeline/*` modules and the
  three `app/worker/tasks/runtime/*` modules. Each diff is the kwargs lines, the import,
  and a comment that no longer narrates a constant.
- `apps/api/pyproject.toml`: import-linter contract "ssl has one home". `app` may not
  import `ssl`, `app.core.redis_tls` excepted.
- `apps/api/tests/unit/test_redis_tls_seam.py` (new): the seam over its three inputs, the
  three factories that build arguments inside a function driven with a `rediss://` URL and
  the flag off, Celery's own reading of the dict on both the broker and the backend side
  for both schemes, and the eleven module-level sites imported in a subprocess under a
  `rediss://` URL.
- `apps/api/tests/unit/test_capability_enforcement.py`: the WR-04 warning test patches
  `app.core.redis_tls.log`, since that is where the warning is emitted now, and clears the
  once per process cache first.

## Adversary pass, same day

Five findings on `f8844d9`, each verified by probe before it was fixed.

- The `if _ssl_opts` guard in `celery_app.py` was dead and its comment was false. Both
  gone; the broker side is pinned now, on `connection_for_write().ssl` for `rediss://` and
  on the booted app's empty dict for `redis://`.
- The contract does not ban `{"ssl_cert_reqs": "none"}`, which needs no import.
  `TestModuleLevelSitesTakeTheSeamDict` does.
- `deps.py` and the seam docstring said redis-py never reads `ssl_cert_reqs` from the URL.
  It does, and it lets the URL win, so the query strip is a control rather than tidiness.
  Both comments say that now, and a test drives a dirty URL through the real `from_url`.
- `get_async_redis` builds a client per request, so the warning was one log line per API
  request. It is behind `lru_cache` on the URL prefix now.
- The prose carried an em dash on the RED line, an arithmetic error in the pass count, and
  a docstring saying three call-site tests failed where two did.

## Decisions

- Two guards, because one spelling escapes each. The import-linter contract bans the
  import spellings: `ssl` had exactly one use in this tree, choosing a verification mode,
  so a module that cannot import it cannot write `ssl.CERT_NONE`. It does not ban the
  string, and redis-py 6.4.0 accepts `ssl_cert_reqs="none"` and maps it to `CERT_NONE` in
  `RedisSSLContext`, so a hand-written `{"ssl_cert_reqs": "none"}` imports nothing and
  passes. `TestModuleLevelSitesTakeTheSeamDict` bans that one: each site's `_ssl_opts`
  has to equal `redis_ssl_kwargs` of its own cleaned URL. Neither is a source-scan test,
  so `SOURCE_ASSERTION_BASELINE` stays untouched and `tests/unit/test_gates.py` stays
  green.
- Eleven of the fourteen sites build `_ssl_opts` at module import as a constant, so their
  value is fixed before any test can patch settings. They are checked in a subprocess with
  `REDIS_URL` set to a `rediss://` URL and `REDIS_TLS_INSECURE` false, which imports them
  for real. Reloading eleven modules in the pytest process would rebind `celery_app` and
  re-register every task on the live app for the rest of the session. The three sites with
  a factory function are driven in process.
- `celery_app.py` passes `broker_use_ssl` and `redis_backend_use_ssl` with no guard. The
  first version kept an `if _ssl_opts` around them and said Celery raises
  `E_REDIS_SSL_PARAMS_AND_SCHEME_MISMATCH` when an ssl option reaches a `redis://` scheme.
  Measured on kombu 5.6.2: `Channel._connparams` reads them under `if conninfo.ssl:`, so an
  empty dict is skipped and only an actual ssl key raises. The guard was dead and its
  comment was false, so both went.

## Observed

- RED, contract, before the move: `ssl has one home BROKEN`, 3 kept 1 broken, naming all
  fourteen edges from `app.api.deps -> ssl (l.19)` to
  `app.worker.tasks.runtime.validators -> ssl (l.28)`.
- RED, tests, before the move: `2 failed, 4 passed in 12.29s`. `app/api/deps.py` and
  `app/services/agent_tools.py` passed `ssl_cert_reqs=<VerifyMode.CERT_NONE: 0>` against
  `<VerifyMode.CERT_REQUIRED: 2>`. The third call-site test, enforcement, already read the
  flag and passed.
- GREEN after: `lint-imports` 4 kept 0 broken; `test_redis_tls_seam.py` 8 passed in 8.31s.
  This line also said `test_capability_enforcement.py` plus the seam file was 39 passed in
  8.13s. That number was never run and does not add up, 33 and 8 being 41. Measured on the
  head this trace now describes: 46 passed in 103.69s, 13 in the seam file and 33 in
  `test_capability_enforcement.py`.
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
