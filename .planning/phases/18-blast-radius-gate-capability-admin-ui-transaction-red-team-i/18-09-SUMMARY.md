---
phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
plan: 09
subsystem: security
tags: [red-team, prompt-injection, content-injection, poisoned-chunk, zero-vector, sanitize]

# Dependency graph
requires:
  - phase: 18-06
    provides: "run_red_team's six-runner sequential wiring (three M7 + three RTX), the seam this plan extends to seven"
  - phase: 18-01
    provides: "OD-7 decision text (this plan's contract) — rename + alias + direct chunk seeding with a fixed zero vector"
provides:
  - "red_team_service.INJECTION_ATTACK_VECTORS, run_conversation_injection_agent (renamed), run_prompt_injection_agent (backward-compatible alias), run_content_injection_agent, POISONED_CHUNK_TEXT/CANARY/PROBE_QUESTION/VECTOR_DIM, seed_poisoned_chunk, remove_poisoned_chunk"
  - "worker/tasks/runtime/red_team.py: seven sequential runner calls, content_injection receiving conn_str as a function argument"
  - "tests/unit/test_red_team_service.py: test_conversation_content_split (pinned node id) + TestInjectionSplit (10 cases)"
affects: [18-11]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Poisoned-chunk seeding writes directly to the tenant chunks/embeddings/documents tables with a fixed zero vector — no embed call, no ingestion pipeline — mirroring the demo_business_tenant.sql eval-fixture precedent (Phase 4 decision)"
    - "Canary-substring decision for a retrieval-time injection probe: a fixed nonsense token embedded in the poisoned chunk, asserted by substring test rather than an LLM judgement call over prose"

key-files:
  created: []
  modified:
    - apps/api/app/services/red_team_service.py
    - apps/api/app/worker/tasks/runtime/red_team.py
    - apps/api/tests/unit/test_red_team_service.py
    - apps/api/tests/unit/test_red_team_rtx_runners.py
    - apps/api/tests/unit/test_red_team_task.py
    - apps/api/tests/unit/test_redteam_findings.py
    - apps/api/tests/unit/test_redteam_programme.py

key-decisions:
  - "seed_poisoned_chunk also inserts a throwaway documents row: chunks.document_id is a NOT NULL FK to documents(id) (0001_tenant_v1_schema.py), not mentioned explicitly in the plan text but required by the schema. remove_poisoned_chunk deletes it explicitly since the chunks->documents cascade only runs one direction (document delete cascades to chunks, not the reverse)."
  - "run_content_injection_agent calls probe_fn directly and synchronously (no asyncio.run wrapper) — both shipped probe_fn variants (_build_probe_fn, _build_transactional_probe_fn) already return a synchronous Callable[[str], str] that bridges async internally, so no additional bridge is needed for a bare dispatch."
  - "The two new attack_vector defaults (conversation_injection) replace the old prompt_injection fallback inside run_conversation_injection_agent's classify_severity call and RedTeamFinding construction — proven by TestInjectionSplit's SDK-loop-driven test that deliberately omits attack_vector from the report_finding tool_use input."

requirements-completed: [SEC-03]

coverage:
  - id: D1
    description: "run_red_team invokes a conversation-injection runner and a content-injection runner as two distinct probes, each called exactly once per run"
    requirement: "SEC-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::test_conversation_content_split"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_rtx_runners.py::test_run_red_team_calls_all_six_runners"
        status: pass
    human_judgment: false
  - id: D2
    description: "The two variants write two distinct attack_vector values (conversation_injection, content_injection), becoming separate red_team_strategies rows with no migration via the existing free-TEXT + UNIQUE + ON CONFLICT DO NOTHING upsert"
    requirement: "SEC-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::TestInjectionSplit::test_injection_attack_vectors_tuple"
        status: pass
    human_judgment: false
  - id: D3
    description: "The content-injection probe seeds a poisoned chunk that bypasses admit-time sanitisation, then asks a question that retrieves it, deciding the finding by canary substring test"
    requirement: "SEC-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::TestInjectionSplit::test_seeded_chunk_text_is_not_sanitised"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::TestInjectionSplit::test_content_runner_reports_finding_when_canary_appears"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::TestInjectionSplit::test_content_runner_reports_nothing_when_canary_absent"
        status: pass
    human_judgment: false
  - id: D4
    description: "The content-injection probe never calls a real embedding provider — a fixed zero vector of the correct dimension is written instead"
    requirement: "SEC-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::TestInjectionSplit::test_content_runner_issues_no_embedding_call"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::TestInjectionSplit::test_seeded_vector_is_zero_and_correctly_dimensioned"
        status: pass
    human_judgment: false
  - id: D5
    description: "The existing prompt-injection runner name still resolves (backward-compatible alias) — the rename breaks no importer"
    requirement: "SEC-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::TestInjectionSplit::test_alias_preserves_old_import_name"
        status: pass
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::TestPromptInjectionAgent::test_prompt_injection_agent_finds_vulnerability"
        status: pass
    human_judgment: false
  - id: D6
    description: "A poisoned chunk is never left in a tenant's live corpus — cleanup runs from a finally block even when the probe raises"
    requirement: "SEC-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_red_team_service.py::TestInjectionSplit::test_content_runner_seeds_then_removes_the_chunk"
        status: pass
    human_judgment: false
  - id: D7
    description: "Live content-injection run against a real tenant corpus with a real ANTHROPIC_API_KEY (RTX-04-adjacent integration proof for SEC-03)"
    requirement: "SEC-03"
    verification: []
    human_judgment: true
    rationale: "No live Postgres/Redis/ANTHROPIC_API_KEY in this environment. This plan is autonomous:true (unit-scoped per 18-VALIDATION.md); the live aggregate gate is plan 18-11 (autonomous:false)."

# Metrics
duration: ~35min
completed: 2026-07-27
status: complete
---

# Phase 18 Plan 09: Conversation/content-injection red-team split Summary

**Renamed the shipped M7 prompt-injection runner to `run_conversation_injection_agent` with a backward-compatible alias, and added `run_content_injection_agent` — a deterministic probe that seeds a canary-bearing poisoned chunk directly into the tenant `chunks` table with a zero vector (bypassing `sanitize_chunk_text` to simulate a sanitiser gap), asks an ordinary customer question that retrieves it via native tsvector BM25, and decides the finding by canary substring test.**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-07-27T~01:20:00+02:00 (approx.)
- **Completed:** 2026-07-27T~01:55:00+02:00
- **Tasks:** 3 (plus 2 deviation fix commits for pre-existing tests broken by the rename)
- **Files modified:** 7 (0 new, 7 modified)

## Accomplishments

- `red_team_service.py`: `INJECTION_ATTACK_VECTORS = ("conversation_injection", "content_injection")` as the single source of truth. `run_prompt_injection_agent` renamed to `run_conversation_injection_agent` (behaviour unchanged — same signature, same four attack sequences, same system prompt); a module-level `run_prompt_injection_agent = run_conversation_injection_agent` alias keeps every existing importer resolving. `POISONED_CHUNK_TEXT` carries an admit-time `"System:"` marker plus a fixed canary token (`POISONED_CHUNK_CANARY`) and plainly retrievable business wording; `POISONED_CHUNK_PROBE_QUESTION` has lexical overlap for the tsvector BM25 half of hybrid retrieval. `seed_poisoned_chunk(conn_str)` inserts a throwaway `documents` row (required by `chunks.document_id`'s NOT NULL FK, not called out explicitly in the plan text but required by the schema), a `chunks` row with the unsanitised poisoned text, and an `embeddings` row with a fixed zero `vector(1024)` — no embedding function is ever called. `remove_poisoned_chunk(conn_str, chunk_id)` deletes both rows in its own try/except, logging a warning rather than raising. `run_content_injection_agent(probe_fn, max_turns, attack_sequences, conn_str=None)` seeds the chunk inside a try (`finally: remove_poisoned_chunk`), sends the probe question `attack_sequences` times, and reports one finding via `classify_severity` only if the canary appears in any response — returns `[]` and logs a warning when `conn_str` is `None`, and `[]` (no finding) when the canary never appears.
- `worker/tasks/runtime/red_team.py`: Step 5's single `run_prompt_injection_agent(probe_fn, ...)` call becomes two sequential calls — `run_conversation_injection_agent(probe_fn, ...)` (unchanged conversational probe) and `run_content_injection_agent(probe_fn, ..., conn_str=conn_str)` (also the conversational probe, plus the tenant `conn_str` as a plain function argument, decrypted at Step 1 — never a Celery task arg per CLAUDE.md rule 4). `all_findings` now concatenates seven lists. Module docstring, `run_red_team` docstring, and the Step 7b comment updated for the seven-runner count and the two new attack_vector strings, which become separate `red_team_strategies` rows through the existing `ON CONFLICT (attack_vector) DO NOTHING` upsert with zero migration.
- `tests/unit/test_red_team_service.py`: `test_conversation_content_split` (module scope, node id pinned by 18-VALIDATION.md) drives the actual `run_red_team` task with all seven runners mocked, proving each injection variant is called exactly once, the content variant receives the conversational probe plus `conn_str` as a keyword arg, and both a high (conversation) and a critical (content) finding reach the Step 6 severity fold. `TestInjectionSplit` (10 cases): the alias, the `INJECTION_ATTACK_VECTORS` tuple, the SDK-loop-driven default-attack_vector proof (fake `ClaudeSDKClient`/`AssistantMessage`/`ToolUseBlock`, canary omitted from the tool_use input to prove the fallback flipped from `"prompt_injection"` to `"conversation_injection"`), the no-conn_str early return, seed-then-remove-even-on-raise, both canary decision directions, the unsanitised-text proof, the no-embedding-call proof (pinning `EMBEDDING_PROVIDER` to `"voyage"` per the `_force_voyage_provider` pattern), and the zero-vector dimensioning proof.
- Full unit suite: **1092 → 1103 passed, 8 skipped, 0 failed.** `apps/api/pyproject.toml` unchanged — `git diff --exit-code` exits 0, no new dependency.
- Repo-wide grep confirms no stale `run_prompt_injection_agent` **call sites** remain outside the alias definition itself and the tests that deliberately exercise the alias (`TestPromptInjectionAgent`, proving backward compatibility) — matches the must-have "the existing prompt-injection runner name still resolves."

## Task Commits

Each task was committed atomically:

1. **Task 1: Rename the conversation runner, add the alias, and add the content-injection runner** - `80c5cac` (feat)
2. **Task 2: Replace the single prompt-injection call with both variants in run_red_team** - `fe0577c` (feat)
   - Deviation fix: `b8ae3fc` (fix) — `test_red_team_rtx_runners.py`'s six-runner wiring proof needed the rename + a seventh runner
3. **Task 3: Extend test_red_team_service.py with the split, seeding, and no-embed coverage** - `be44085` (test)
   - Deviation fix: `7dc8eb5` (fix) — `test_red_team_task.py`, `test_redteam_findings.py`, `test_redteam_programme.py` all patched the removed `run_prompt_injection_agent` import name

**Plan metadata:** committed alongside this SUMMARY (see below).

## Files Created/Modified

- `apps/api/app/services/red_team_service.py` - `INJECTION_ATTACK_VECTORS`, renamed `run_conversation_injection_agent` + `run_prompt_injection_agent` alias, `POISONED_CHUNK_TEXT`/`CANARY`/`PROBE_QUESTION`/`VECTOR_DIM`, `seed_poisoned_chunk`, `remove_poisoned_chunk`, `run_content_injection_agent`
- `apps/api/app/worker/tasks/runtime/red_team.py` - Step 5 now calls seven runners; content_injection receives `conn_str` as a function argument; docstrings and Step 7b comment updated
- `apps/api/tests/unit/test_red_team_service.py` - `test_conversation_content_split` + `TestInjectionSplit` (10 new tests)
- `apps/api/tests/unit/test_red_team_rtx_runners.py` - six-runner wiring proof updated to the renamed + seventh runner (Rule 1 fix)
- `apps/api/tests/unit/test_red_team_task.py`, `test_redteam_findings.py`, `test_redteam_programme.py` - patch target updated from the removed `run_prompt_injection_agent` import name (Rule 1 fix)

## Decisions Made

- `seed_poisoned_chunk` inserts a throwaway `documents` row alongside the `chunks`/`embeddings` rows — required by `chunks.document_id`'s `NOT NULL` foreign key (`0001_tenant_v1_schema.py`), which the plan text did not spell out but the schema requires. `remove_poisoned_chunk` looks up and deletes the document row explicitly, since the FK cascade only runs document→chunks, never the reverse.
- `run_content_injection_agent` calls `probe_fn` directly and synchronously rather than wrapping it in `asyncio.run` — both shipped `probe_fn` builders (`_build_probe_fn`, `_build_transactional_probe_fn`) already return a plain synchronous `Callable[[str], str]` that bridges async internally, matching the plan's "call probe_fn directly if already synchronous" instruction.
- Both injection runners' rename comments use `# Previously defaulted to "prompt_injection"` inline notes at each of the two `classify_severity`/`RedTeamFinding` call sites and the `log.warning` call, per the acceptance criterion permitting a construct-scoped explanatory comment recording the previous value.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_red_team_rtx_runners.py's six-runner wiring proof for the rename + seventh runner**
- **Found during:** Task 2 verification (running the RTX runner test suite as a broader regression check, as the plan itself anticipated: "plan 18-06's `test_run_red_team_calls_all_six_runners` must be updated by that plan's owner if it asserts an exact count")
- **Issue:** The test patched `app.worker.tasks.runtime.red_team.run_prompt_injection_agent`, an attribute that no longer exists in that module's namespace after Task 2's import rename to `run_conversation_injection_agent`.
- **Fix:** Patched `run_conversation_injection_agent` and added a `run_content_injection_agent` patch; extended the runner-mock helper to accept a `conn_str` kwarg; extended the expected `call_order` to seven entries; added an assertion that `content_injection` receives `conn_str` as a keyword argument.
- **Files modified:** `apps/api/tests/unit/test_red_team_rtx_runners.py`
- **Commit:** `b8ae3fc`

**2. [Rule 1 - Bug] Patched three pre-existing red-team test files for the same rename**
- **Found during:** Full unit suite gate after Task 3
- **Issue:** `test_red_team_task.py::TestRunRedTeamComplete::test_run_red_team_complete`, `test_redteam_findings.py` (2 tests), and `test_redteam_programme.py` (2 tests) all patched the now-removed `run_prompt_injection_agent` attribute on the worker task module.
- **Fix:** Patched `run_conversation_injection_agent` in place of `run_prompt_injection_agent`, added a `run_content_injection_agent` patch (returning `[]`) alongside it in every case, and updated fixture `attack_vector` values from `"prompt_injection"` to `"conversation_injection"` for accuracy where the test data itself represented a conversation-injection finding.
- **Files modified:** `apps/api/tests/unit/test_red_team_task.py`, `apps/api/tests/unit/test_redteam_findings.py`, `apps/api/tests/unit/test_redteam_programme.py`
- **Commit:** `7dc8eb5`

---

**Total deviations:** 2 auto-fixed (both Rule 1 - directly-caused breakage from the rename)
**Impact on plan:** Both fixes were anticipated by the plan text itself (the 18-VALIDATION.md acceptance criterion for `test_red_team_rtx_runners.py` explicitly permits this). No scope creep — same pattern applied to two additional pre-existing files the plan didn't enumerate by name but that shared the identical breakage.

## Issues Encountered

- The 18-09-PLAN.md acceptance criterion `grep -n 'acks_late=True' apps/api/app/worker/tasks/runtime/red_team.py returns two lines` does not hold — it returns **three**: the two task decorators (`run_red_team_beat`, `run_red_team`) plus a pre-existing module-docstring sentence ("acks_late=True AND idempotency guard on every Celery task") that was already present before this plan touched the file. This is a pre-existing planning-doc inaccuracy, not a regression introduced by this plan — verified `git diff` shows no change inside the Step 2 idempotency guard, and both task decorators still carry `acks_late=True`. Not fixed (out of this plan's scope to edit unrelated docstring wording to satisfy a grep count).
- `tests/unit/test_red_team_service.py::TestInjectionSplit::test_conversation_runner_records_conversation_injection_vector` required driving the actual async SDK loop (fake `ClaudeSDKClient`/`AssistantMessage`/`ToolUseBlock`, following the pattern in `tests/unit/test_agent_task.py`) rather than the file's existing convention of patching `asyncio.run` directly — necessary because the goal was proving the *default* `attack_vector` value flipped from `"prompt_injection"` to `"conversation_injection"` inside `classify_severity`'s call, which only exercises when the SDK loop's own `raw.get("attack_vector", ...)` fallback actually runs.

## User Setup Required

None for this plan's automated scope. Live content-injection verification against a real tenant corpus (a real `ANTHROPIC_API_KEY`, local Postgres) is deferred to plan 18-11's `autonomous:false` gate — no Docker in any step (CLAUDE.md rule 9).

## Next Phase Readiness

- SEC-03 is now fully implemented at the unit level: both injection variants exist, are wired into `run_red_team`, and are covered by the pinned `test_conversation_content_split` node id plus 10 `TestInjectionSplit` cases.
- Plan 18-10 (admin UI, `autonomous:false`) and 18-11 (live gates, `autonomous:false`) are unaffected by this plan's file set.
- No blockers. `apps/api/pyproject.toml` untouched; full unit suite green at 1103 passed / 0 failed, above the 970/1092 baselines; 8 skipped unchanged.

## Known Stubs

None — every deliverable in this plan has a live call site inside `run_red_team` and is exercised by a passing unit test.

## Threat Flags

None beyond what `18-09-PLAN.md`'s own `<threat_model>` already registers (T-18-SEC-04 four times, T-18-SC once) — no new network endpoint, auth path, or schema change was introduced outside that register. The `documents` row inserted by `seed_poisoned_chunk` (not explicitly named in the plan's threat register) writes only to the tenant's existing `documents` table via the existing schema — no new surface, and it is removed by `remove_poisoned_chunk` alongside the chunk.

---
*Phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i*
*Completed: 2026-07-27*

## Self-Check: PASSED

- FOUND: apps/api/app/services/red_team_service.py
- FOUND: apps/api/app/worker/tasks/runtime/red_team.py
- FOUND: apps/api/tests/unit/test_red_team_service.py
- FOUND: apps/api/tests/unit/test_red_team_rtx_runners.py
- FOUND: apps/api/tests/unit/test_red_team_task.py
- FOUND: apps/api/tests/unit/test_redteam_findings.py
- FOUND: apps/api/tests/unit/test_redteam_programme.py
- FOUND: 80c5cac (Task 1 commit)
- FOUND: fe0577c (Task 2 commit)
- FOUND: b8ae3fc (Task 2 deviation fix commit)
- FOUND: be44085 (Task 3 commit)
- FOUND: 7dc8eb5 (Task 3 deviation fix commit)
