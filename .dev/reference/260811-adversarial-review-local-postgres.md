# Adversarial review — `chore/local-postgres`, tasks A/B/C/D

Reviewer: independent adversarial pass, 2026-08-11. Every number below comes from a run
performed in this session. Nothing is taken from the four task reports on trust.

Commits reviewed: `fe45291`, `115f052`, `eb836c1`, `0e2efe7`, `4164fe6`, `013cac4`,
`79601cd`, `18f2c26` (range `3e7fb8e..HEAD`).

---

## 1. Measured gates (my runs, verbatim)

Integration (`INTEGRATION_DB_URL=postgresql://wchats:wchats@localhost:5432/wchats_control`,
`REDIS_URL=redis://localhost:6379/0`):

```
15 passed, 22 skipped, 24 deselected in 200.92s (0:03:20)
```

Unit gate (with the two prescribed `--ignore` flags):

```
2127 passed, 12 skipped, 30 warnings in 512.74s (0:08:32)
```

Baseline was 10F/9P/21S/24D and 2112 passed. Both reproduce the four tasks' claims.
Zero failures, zero errors in either suite.

Patch-target re-measure (`python tests/unit/test_patch_targets_resolve.py`):

```
targets_scanned      1283
unresolvable_sites   1
pinned_targets       1
```

---

## 2. NEON SAFETY — the highest-consequence question

**All 8 baseline projects present. No leak. No project created or deleted by me or by
any task.** Checked three times against `C:/Users/Bantu/pg-setup/neon-baseline.txt` via
`GET https://console.neon.tech/api/v2/projects` (key read from `.env` in-process, never
printed): before the suite, after the suite, and after every mutation run.

```
HTTP 200
live project_count = 8
BASELINE_COUNT=8
MISSING_FROM_LIVE=[]
EXTRA_NOT_IN_BASELINE=[]
BASELINE_INTACT=YES
LEAK=NONE
```

Same 8 ids each time: `dark-snow-18891572`, `round-king-00493014`, `nameless-fog-19651218`,
`floral-bar-83436685`, `morning-math-61244033`, `gentle-cell-49949671`, `cool-pond-11127703`,
`dry-band-71216365`.

### Deletion paths audited

- `tests/e2e/_neon_teardown.py:delete_project` — deletes **only** the id passed in, then
  verifies with a 404 probe and raises if the project survives. No sweep, no pattern.
- `tests/e2e/test_neon_e2e.py` — teardown is in an outer `finally` that runs on every
  failure path, and `resolve_project_id` re-reads the id from the control DB rather than
  trusting a variable the failing path never assigned. This is a genuine fix.
- `app/services/neon.py:238` — `requests.delete` targets `/branches/{branch_id}`, not
  `/projects/{id}`. Not a project-deletion path.

### The one name-pattern deletion that DOES exist

`.github/workflows/nightly.yml:73` sweeps **every project the key can see** and deletes on
a name match:

```python
if p.name.startswith('vrd-') and 'e2e' in p.name.lower():
    client.project_delete(p.id)
```

Pre-existing, not introduced by these four tasks — but Task B owned the Neon boundary,
edited `test_neon_e2e.py`'s teardown, and did not report it. Two things about it:

1. It is exactly the class this workflow's rule 1 forbids (delete by pattern, not by an id
   the run just created). It runs with `secrets.NEON_API_KEY_TEST`; if that key ever names
   the owner's account, it is an unbounded delete over that account.
2. **It no longer matches what the e2e test creates.** `_project_slug(agent.name, tag)`
   (`app/services/neon.py:66-90`) slugifies `agent.name`, which `test_neon_e2e.py:105` sets
   to `e2e-agent-{uuid}`. The result never starts with `vrd-`. So the sweep is a dead
   safety net wearing a live delete. The comment at `test_neon_e2e.py:103-104` still claims
   it works.

None of the 8 baseline names match the pattern today, so the baseline is not at risk from it
as written.

### Residual exposure: `test_worker_kill.py`

`tests/integration/test_worker_kill.py` still carries all three defects Task B fixed in its
siblings, and Task B did not report it:

- respx mock in the pytest process against a Neon call made in a worker subprocess (inert —
  `_register_neon_mock_routes`, line 230);
- hands `_INTEGRATION_DB_URL` (the **control DB**) back as the tenant URI (line 314);
- bare `"celery"` in `Popen` (line 69), the `FileNotFoundError` defect fixed elsewhere;
- **no Neon teardown anywhere in the file.**

It is skipped behind `INTEGRATION_TESTS_ENABLED=1`, and `conftest.py:61`'s
`setdefault("NEON_API_KEY", "test_neon_key_integration")` currently means a 401 rather than
a create. I verified `os.environ` outranks `apps/api/.env` in pydantic-settings
(`os.environ wins over .env: True`), so the placeholder does protect a default run. But
`setdefault` keeps an **exported** key — so this test is one `INTEGRATION_TESTS_ENABLED=1`
plus one exported `NEON_API_KEY` away from creating real projects with no teardown.

No test currently requests the plain `celery_worker` fixture, which lowers the risk further.

---

## 3. Tautologies — what I reverted, and what happened

### 3a. The docling gate — the guard is real (CONFIRMED GOOD)

Mutation: repointed `pytest.importorskip("docling.chunking")` to
`pytest.importorskip("totally_fictional_module_xyz")` — a permanent skip.

```
FAILED tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_names_only_modules_app_actually_imports
FAILED tests/unit/test_ingestion_chain_docling_gate.py::test_the_gate_does_not_skip_when_docling_is_importable
2 failed, 6 passed in 63.10s (0:01:03)
```

Restored with `git checkout HEAD -- apps/api/tests/integration/test_ingestion_chain.py`
(`git status --porcelain` empty), then `8 passed in 44.39s`.

Note the `6 passed`: `test_the_gate_skips_when_docling_is_absent` stayed **green** under a
purely tautological gate. Task A's central claim — that the skip-direction assertion alone
certifies nothing and the present-plugin is what makes it mean something — is correct, and I
observed it directly.

Task A is also honest that the four gated tests **skip, not pass**, and that docling presence
is simulated by fake `sys.modules` entries. Both hold.

### 3b. The Neon idempotency test — CONDITIONALLY VACUOUS (NEW FINDING)

`test_provision_neon_idempotency` dispatches twice, sleeps a fixed `time.sleep(5)`, then
asserts exactly one `POST /projects` in the stub journal. There is no positive evidence that
the **second** dispatch ever executed — a second task that never ran is indistinguishable
from a guard that held.

Mutation: routed only the second dispatch to a queue no worker consumes.

```
provision_neon.apply_async(args=(...), queue="mutant_queue_no_consumer")
```

```
.                                                                        [100%]
1 passed in 59.55s
```

The test passes while the behaviour it exists to check was never exercised. Restored from
HEAD → `1 passed in 44.96s`. Task B's mutation proof 5 was valid, but only because the
second dispatch happened to complete inside 5s on that run; on a loaded 4 GB box it will not
always. Fix: wait for observable evidence the second execution finished (a journal marker, a
task result, or a second job row transition) instead of sleeping.

### 3c. Everything else

- `test_query_route.py` (Task C): the replacement assertions are **positional** against
  `retrieve_and_rank(self, job_id, agent_id, query)` and pin `queue="runtime"`. Strictly
  stronger than the membership check they replace, which could not catch a transposition.
  `_celery_task_args()` raises instead of returning `[]`. Verified by reading; Task C's three
  mutation proofs are on product code and consistent with the source.
- `test_sse.py` (Task D): the decoy assertion (published-but-never-persisted must never
  arrive) and the `POLL_INTERVAL_S → 300s` isolation are both genuine strengthenings.
- `_KNOWN_BROKEN`: agrees with reality — 1 pin × 1 site, and my independent re-measure gives
  `unresolvable_sites 1 / pinned_targets 1`.

---

## 4. The `asyncio.timeout` bound on the SSE loops

**Not removed, not raised, not worked around.** `SSE_STREAM_TIMEOUT_S = 30` in both tests,
and both still wrap their consume in `async with asyncio.timeout(SSE_STREAM_TIMEOUT_S)`.
What Task D raised is `POLL_INTERVAL_S` (3s → 300s), which *removes the fallback path* and
makes the test stricter, not looser.

### But the bound is now escapable — the test hangs forever again (NEW, HIGH)

`test_sse.py:580`, `await emitter_task`, sits **outside** the `asyncio.timeout` block:

```python
async with asyncio.timeout(SSE_STREAM_TIMEOUT_S):
    await stream.run()

await emitter_task          # <-- unbounded
```

`emit_live_events` blocks on `stream.wait_for_events(1)` / `(2)`. If the stream closes having
written fewer `event:` lines than that — any 4xx, an auth change, a missing tenant row, an
early terminal event — `run()` returns, `notify_all()` fires, the predicate is still false,
and the emitter waits on the Condition forever. Nothing bounds it.

Proof. Mutation: bogus `x-api-key` in the `_SSEStream` construction (401, zero `event:` lines):

```
=== EXIT_CODE=137  ELAPSED=155s (external kill at 150s; SSE bound is 30s) ===
```

Exit 137 is SIGKILL from an external `timeout --signal=KILL 150`. pytest never printed a
summary — it was still hanging at 5x the stream bound. Restored from HEAD
(`git status --porcelain` empty) → `1 passed in 47.51s`.

This is the same defect class as `a95b581` ("a test that hung forever"), reintroduced in a
narrower form, in the commit whose message is about that very problem.

Fix: wrap the whole try-body in the timeout, or `asyncio.wait_for(emitter_task, ...)`, or
cancel the emitter in the `finally`.

---

## 5. Timing margins (measured)

`--durations=5` on the two SSE tests:

```
34.71s call     tests/integration/test_sse.py::test_sse_receives_live_events_after_replay
2.85s call      tests/integration/test_sse.py::test_sse_closes_on_completed_job
2 passed in 38.01s
```

I located the 34.71s by lowering the bound to 5s as a probe: the test **still passed**
(`1 passed in 36.78s`, call 36.33s), so `stream.run()` completes in under 5s and the ~34s is
`app.main` import cost inside `_make_app_with_real_deps`, outside the bound. Task D's
conclusion (the stream is healthy and fast) holds. Task D's stated "2.26s total test
duration incl. fixture setup" and "~40x headroom" describe stream time under a warm import
and do not reproduce standalone; the honest figure for total call duration is ~35s when this
test absorbs the import.

`test_sse_closes_on_completed_job` measures 2.85s against `assert elapsed < 5.0` — a 1.75x
margin on a machine where Task B observed this same assertion fail at 5.5s and 6.8s under
full-suite load (BACKLOG 1.11). It passed in my full-suite run, but the margin is thin.

---

## 6. Secrets

- No secret in any committed file. `git grep` for the live key literal across tracked
  content: **NONE**. `.env` and `apps/api/.env` are both git-ignored (`.gitignore:19`) and
  untracked.
- The branch diff contains no key-shaped literal, no `Bearer`, no non-localhost connection
  string.
- The stub worker is given `NEON_API_KEY=stub-key-never-valid-never-sent`, so even a failed
  stub load holds no usable credential.
- **One live leak vector, pre-existing:** a `pydantic_core.ValidationError` from
  `Settings()` reprs the entire settings input dict. I triggered it accidentally while
  testing env precedence and the traceback printed a truncated dict containing the tail of a
  real secret value. Any crash on a missing/invalid settings field prints secret fragments
  into logs and CI output. Not introduced by these tasks; worth a `SecretStr` /
  `model_config` fix.

---

## 7. Process gaps

- **Zero product-code changes on the branch.** `git diff --stat 3e7fb8e..HEAD -- apps/api/app/`
  is empty. All four "the product was not at fault" claims are structurally verified.
- **BACKLOG is stale, against the transactional rule.** Rows 1.8 (query route), 1.9 (SSE live
  events) and 1.11 (SSE close flake) are still open but are all fixed and passing. Row 1.1
  still reads "3 failed, 12 passed" and "unit half is 2120/12/0"; measured is 0F/15P and
  2127/12/0. Tasks C and D touched no `.dev/BACKLOG.md` — confirmed from `git log --stat`.
- **Only Task A wrote a trace.** `.dev/traces/260810-docling-gate.md` exists; B, C and D wrote
  `.dev/reference/` notes only. CLAUDE.md: "No task is done without its trace."
- `.dev/HANDOFF.md` was not updated on this branch at all.

## 8. Claims that outran their evidence

- Task B: "the fixture refuses to yield until the stub reports itself installed **in the
  worker's own pid**." `wait_until_installed` (conftest.py:331) checks
  `any(r.get("event") == "installed")` — the pid is recorded but never compared. Harmless in
  practice because `tmp_path_factory.mktemp` gives each fixture its own journal, but the
  stated guarantee is not the implemented one.
- Task A: "`tests/unit/test_pipeline_patch_targets.py` is what keeps
  `docling.chunking.HybridChunker` honest from here on." That module's `_GATED_TEST_MODULES`
  covers only `test_chunking_service.py` and `test_docling_service.py`; it never scans
  `test_ingestion_chain.py`. The integration file's targets are covered instead by
  `test_ingestion_chain_docling_gate.py::test_the_chunker_is_patched_where_it_is_imported_from`.
  Combined coverage exists; the attribution is wrong.
- Task C listed "grep the rest of the integration suite for the same missing-`/api/v1`
  pattern" as not-done. I ran it: `test_agent_chat_integration.py:176` and
  `test_agent_e2e.py:36` both post to unprefixed `/agents/{id}/chat` while
  `app/main.py:175` mounts that router at `/api/v1`. Neither is a false green — both assert
  `202`, so they would fail loudly — but both are latent 404s behind env-gated skips.

---

## 9. Restore hygiene

Every mutation in this review was applied to the working tree, run, then restored with
`git checkout HEAD -- <path>` unconditionally, with `git status --porcelain` verified empty
afterwards and the selector re-run green. Final tree state: clean, `SSE_STREAM_TIMEOUT_S = 30`.
One stray Redis key from the queue-routing mutation (`mutant_queue_no_consumer`) was purged.
