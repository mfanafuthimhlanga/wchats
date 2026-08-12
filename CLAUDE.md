# W Chats

A multi-tenant platform where a non-technical business owner completes signup → ingest → deploy and
gets a customer service agent that is **defensible**: grounded, evaluated, and red-teamed before it
goes live.

**`.planning/PROJECT.md` is the source of truth** for product context, requirements, and the decision
log. `.planning/` as a whole is a **frozen archive** as of 2026-08-05 — see "The archive" below.
Read `.dev/HANDOFF.md` first in every session.

## Branching Model

```
main   ← trunk. Protected by the merge gate. Receives only work branches.
work   ← short-lived branches off main:  feat/<scope> · fix/<scope> · chore/<scope> · spike/<scope>
```

- Never commit directly to `main`. Work branches live days, not weeks.
- `spike/*` branches merge to nothing — their learnings go into a `.dev/traces/` note.
- The owner merges. Claude never merges to trunk (the `PreToolUse` hook enforces this; never work
  around it, never edit `settings.json` to weaken it, never suggest either).

## Quality Gates (Definition of Done)

A merge into `main` must have all gates green, run for real and observed — never asserted:

```bash
# backend  (apps/api)  — docling/chunking modules are excluded: docling is not installed here
uv sync --extra dev                                    # restores .venv if it was disk-cleaned
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py

# admin    (apps/admin)
npx tsc --noEmit          # ONE known pre-existing error: tests/reduced-motion.spec.ts:18. Zero new.
npm run check:no-dusk-tokens      # exit 0
npm run check:ops-room-wiring     # exit 0  (11/11)
npm run test:unit                 # 45, browserless
npm run test:e2e                  # 135 tests (NOT 113 — corrected 2026-08-12 by running it).
                                  # First observed result: 7 failed / 128 passed / 35.9 min.
                                  # All 7 are 90s TIMEOUTS on networkidle, not assertion failures,
                                  # alongside Clerk dev-instance load errors. Cause unestablished,
                                  # no prior baseline. See .dev/PRODUCTION-READINESS.md §3.8 before
                                  # treating a failure here as a product defect.

# widget   (apps/widget)
npm run build && node scripts/check-size.mjs           # ≤ 20480 bytes gzipped
```

Plus: **a test for every behaviour change.** A change that alters behaviour without a test is
incomplete, not "to be tested later."

**Regression policy:** a regression reaching `main` is a *planning defect*, not just a bug. The fix
must include (a) the test that was missing, and (b) an honest paragraph in `.dev/retro.md` naming
what the plan failed to anticipate. Accumulating retro entries mean planning depth increases — that
is the feedback loop working.

**A negative test never observed to fail is indistinguishable from a tautology.** For any guard,
absence pin, or fail-closed path: mutate the guard, observe red, restore from `HEAD`
unconditionally, observe green. Record the observed output, not the intention.

## Session Start

**If `.dev/HANDOFF.md` exists, read it FIRST.** It is the terse current-state handoff: what is in
flight, what is blocked and on what, what the next move is. Update it when you pause; archive it
into `.dev/traces/` when its queue is consumed.

## Execution Workflow & Persistent Artifacts

Substantive multi-step execution in this repo runs through Claude Code's **Workflow** feature. This
is a **standing opt-in** for multi-agent orchestration on phase-scale work (owner, 2026-08-05);
trivial tasks still run solo. Regardless of engine, artifacts persist in `.dev/`:

```
.dev/
  BACKLOG.md                         ← THE single ordered list of open work. Read first.
                                       Rows carry slugs (`5.1 · ops15-server-gap`); the NUMBER is an
                                       address, not a priority. Use slugs in conversation.
  HANDOFF.md                         ← current-state snapshot; read at session start
  PRODUCTION-READINESS.md            ← every gap between here and production, plus the ordered
                                       end-to-end validation plan. Claims are marked
                                       OBSERVED / READ / RECORD — never promote a RECORD line to a
                                       decision without re-checking it.
  plans/     YYMMDD-<slug>.md        ← BEFORE execution: goal, approach, phases, files, risks, tests
  traces/    YYMMDD-<slug>.md        ← AFTER execution: what actually changed, decisions, deviations
  workflows/ <name>.workflow.js      ← the orchestration itself, versioned and re-runnable
  reference/ <topic>.md              ← durable findings that outlive one task
  reviews/   <flattened-branch>.md   ← diff-review packets (SUSPENDED — see below)
  retro.md                           ← append-only regression retro log
```

Rules:

- **No execution of a non-trivial task without a plan file first. No task is done without its trace.**
- **`BACKLOG.md` is the queue and it is maintained transactionally.** A phase that closes an
  item deletes its row in the same commit that lands the fix; a phase that discovers work adds a
  row. Outstanding work living only in the tail of a trace or a plan is how it gets lost.
- **A workflow's tier-2 judgement is extracted to `.dev/reference/` before the session ends.**
  The workflow journal lives in a temp directory and does not survive.
- When a Workflow runs, copy its script into `.dev/workflows/` so the orchestration is versioned.
- Plans and traces are **terse working documents** — bullets, file lists, decisions. The GSD habit of
  600-word narrative paragraphs per plan is what `.planning/` became; do not reproduce it here.
- **Model discipline: every subagent runs the session model.** Never set `model:` in a Workflow
  `agent()` call or pass `model` to the Agent tool — omitting it inherits the resolved session model,
  which is the only reliable way to pin it (`opus`/`sonnet` aliases are not version guarantees). Do
  not spawn named specialist agents whose definitions pin a model in frontmatter; use
  `general-purpose` or the default workflow subagent.
- **The one exception: the tier-2 judge runs `model: 'fable'`.** Once per milestone, before the
  merge. It reads a **bounded artifact only** — the diff, the implementers' claims, the tier-1
  findings — assembled by a session-model collector, and never explores the tree. Its question is not
  "what is broken?" (tier 1 already asked that, against the code) but **"do the claims match the
  evidence, and what is asserted but unproven?"** Reference implementation:
  `.dev/workflows/eval-foundation.workflow.js`.
- **Never tell a reviewer to be conservative.** "Only high-severity", "no speculation", "real defects
  only" all make current models report *less* — they follow it literally. Ask for everything and
  filter in the orchestrator.
- **Never instruct an agent to verify its own work in-turn.** That is what the review stage is for.

## Comprehension gate: SUSPENDED (owner, 2026-08-05)

The diff-review packet (`.dev/reviews/`, `~/.claude/templates/diff-review-packet.md`) is **paused**,
matching the call already made in `sentinel-v2`: learning a milestone in isolation, 23 phases deep,
costs more than it returns.

- **Do NOT block a merge** on packet questions, an answer ledger, or an owner-authored piece.
- **DO keep writing terse `.dev/plans/`, `.dev/traces/` and `.dev/retro.md`.** They are cheap,
  factual, and they are the source material a later relearn is built from.
- **The tier-2 judge stays on.** It is a correctness mechanism, not a teaching one.

## The archive (`.planning/`)

544 files of GSD planning artifacts covering Phases 1–23 and milestones v1.0–v1.2. **Frozen — do not
add to it, do not update it.** Git preserves it; treat it as reference of last resort. Everything
load-bearing has been distilled into `.dev/reference/`, `.dev/HANDOFF.md` and `.dev/retro.md`.

Still authoritative inside the archive: `PROJECT.md` (product context), `REQUIREMENTS.md`
(requirement IDs — note its two known defects, recorded in HANDOFF), and each phase's `SECURITY.md`
threat registers.

## Project rules (binding — these are hard-won, do not relax them)

1. **Connection strings never in Celery task args.** Tasks receive `tenant_id` / `agent_id`; they
   fetch and decrypt from the control DB at runtime.
2. **`acks_late=True` AND idempotency.** Separate requirements — both always required on every task.
3. **Langfuse v4 API only.** `start_span()` / `start_generation()` are gone.
4. **Ragas 0.4.x API only.** `ragas.metrics.collections`, `MetricResult`, and `reference` (never
   `ground_truths`).
5. **No pg_search / pgbm25.** Deprecated on Neon March 2026. BM25 is native `tsvector` +
   `ts_rank_cd` only.
6. **No Docker.** Development runs locally on a 4 GB RAM machine. Never suggest `docker-compose` or
   container workflows. Local processes only: `redis-server`, PostgreSQL, `uvicorn`,
   `celery -A app.worker.celery_app worker`.
7. **FastAPI never does work inline.** All long-running operations go to Celery.
8. **Two Celery queues always present:** `pipeline` (ingestion/build) and `runtime` (evals, agent
   calls).
9. **Per-tenant Neon projects** (not schema-per-tenant) — required for Neon branching in evals.

## Environment constraints (real, and they shape what can be proven)

- **4 GB RAM.** No parallel test workers, small fixtures, one agent at a time in a workflow.
- **No PostgreSQL server on this machine.** Confirmed repeatedly: the `postgresql-x64-17` service is
  a stale registration pointing at a deleted binary; nothing listens on 5432-5435. Every
  `-m integration` harness therefore **skips**, and a skipped gate is *unobserved*, never a pass.
  `CONTROL_DB_URL` points at live Neon production and is **never** an acceptable substitute.
- **Toolchains get disk-cleaned.** `apps/api/.venv`, `apps/admin/node_modules`, `apps/widget/node_modules`
  and `.next` have been removed by cleanup passes before. Restore: `uv sync --extra dev` in
  `apps/api`; `pnpm install` for the front ends (the pnpm store survives). Verify a gate can actually
  run before reporting it green.
- `docling` / `docling_core` are not installed — `test_chunking_service.py` and
  `test_docling_service.py` cannot collect. Excluded from the gate command above by design.

## Stack

```
Backend:     FastAPI + Pydantic + Celery + Redis + Alembic (uv for Python tooling)
Data:        Neon (control DB + per-tenant projects), pgvector (HNSW)
Agents:      claude-agent-sdk 0.1.81 — customer agents, red-team attackers, deployment orchestrator
             Claude API direct     — all judges, the Actor gate, Ragas' LLM, scenario generation
Models:      claude-haiku-4-5             judges, Gatekeeper/Auditor/Strategist/Actor, scenario gen
             claude-haiku-4-5-20251001    the customer agent (agent.py)
             claude-sonnet-4-6            red-team attackers, deployment orchestrator, strategist
Ingestion:   Docling (layout-aware), Chonkie ≥1.6.5 (structure-aware)
Embeddings:  voyageai (embed + rerank), bedrock (default provider), cohere fallback
Evals:       ragas 0.4.x + custom harness
Red team:    pyrit + custom Claude probes
Observ:      langfuse 4.x
Admin UI:    Next.js — GOTHAM "Bone on Graphite" console
Widget:      Preact (<20 kb gzipped)
```

## Architecture principles

- **Programmatic core, agentic edges.** Deterministic code for anything testable; Claude agents for
  open-ended judgement only. Judges are single tool-calls returning a typed verdict, not SDK agents.
- **SSE via Redis pub/sub.** Celery tasks publish to `job_events:{job_id}`; the SSE endpoint
  subscribes. Events persist to `job_events` for late-join replay.
- **The Claude Agent SDK is stateless.** `system_prompt` is passed in `ClaudeAgentOptions` at every
  call. Session continuity uses `resume=session_id`.
- **Measurement honesty** (2026-08-05, and the reason the eval-foundation work exists): a metric
  computed over zero valid observations is `unknown`, never `pass`. Missing data is never treated as
  passing data. A model-generated label may never gate a deploy or reach a customer. See
  `.dev/reference/measurement-layer-audit.md`.

## Conventions

- **pnpm** for the front ends; **uv** for `apps/api`. Never npm/yarn in the workspaces.
- Commit style: `type(scope): message` (feat/fix/chore/docs/test/refactor), ending with the session
  model's co-author trailer.
- PowerShell breaks on multi-line `-m` arguments: write the message to a temp file and use
  `git commit -F <file>`.
- Design work follows the Mzansi Design Codex and the GOTHAM system; the repo-root `DESIGN.md` is
  **stale** (it still describes the superseded "Amber Console" direction).
