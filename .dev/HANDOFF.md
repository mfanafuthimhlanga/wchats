# HANDOFF

**State as of 2026-08-15.** Current state only. The 1,101-line diary this file had become is
archived at `.dev/traces/260815-handoff-archive-through-phase-a.md`; nothing was deleted.

## Next move

**Run E2E-3b: one live customer turn, and read the grounding verdict it produces.**

`5.16` was fixed today, so the Auditor is now handed every retrieved chunk untruncated instead of
1800 chars. **No live turn has produced a verdict that way.** The only grounding verdict ever
observed came from the capped era and is an artefact of the cap, so calibrating judges (E2E-6, the
next Phase B step) on top of it measures the cap a second time.

Set `RETRIEVAL_FAITHFULNESS_SAMPLE_RATE=1.0` for that run. It costs nothing extra and settles `5.13`
and `5.15`, which have been waiting on one sampled turn since 2026-08-13.

Read beside the verdict: `run_agent_turn.judge_context` logs `calls`, `chunks`, `empty`, `unparsed`,
`errored` and `chars`. Those five states are counted separately on purpose, because the first
version of the fix collapsed them and fed a corpus miss to the judge as evidence.

Expect the Auditor call to cost about **$0.0025** on a typical turn and up to **$0.020** at the
retrieval ceiling (80,000 chars, about 20,000 input tokens), against roughly $0.00046 before.

After that, `PRODUCTION-READINESS.md` §4 Phase B: E2E-6, then E2E-7.

## The one thing to internalise from today

**A structural guard bans the spelling its author imagined.** `5.16` shipped with two AST checks on
the line that built the judge's context. An adversary reintroduced the whole defect five ways that
stayed 10/10 green: a truncating helper, a renamed variable, `itertools.islice`, a second assignment
on the next line, and rebuilding the old value while still calling the new helper.

What works is asserting on **the argument the consumer receives**, which needs a seam a test can
reach. `_dispatch_validation_chain` is that seam; `TestWhatTheAuditorIsActuallyHanded` reads
`run_auditor.si.call_args`. All five now fail.

`.dev/reference/260815-wiring-is-invisible-to-behavioural-tests.md` carries the argument and the
list of modules where the same hole is likely. The seam question to ask of each: **is there any test
that observes the value the next stage receives?** For `retrieved_context_json` the answer was no,
and both ends of that boundary had tests.

## Where the code is

`chore/local-postgres`, 59 commits ahead of `main`, **unmerged**. The owner merges; Claude never does
(the `PreToolUse` hook enforces it).

## Gates

| Gate | Last observed | Result |
|---|---|---|
| backend unit | 2026-08-15, this session | see `.dev/traces/260815-judge-sees-agent-context.md` |
| admin e2e | 2026-08-12 | **7 failed / 128 passed**, all timeouts, `1.19`. Not green, cause not established |
| backend integration | 2026-08-12 | 40 passed / 24 skipped / **2 failed**, both external (`5.6`, and `ver01` needs a real key) |

**`.dev/gates.json` was wrong from the day it was written and is fixed today.** The interpreter path
used forward slashes, which `cmd.exe` parses as a switch, so the stop hook's gate exited in 0s with
`'.venv' is not recognized` and **never ran once**. Two consequences now encoded in the file:

- Paths use backslashes.
- **`fast` is a smoke gate, not the definition of done.** The harness clamps `timeoutSec` to 170s and
  the unit suite needs 480 to 560s on this box, so declaring the suite there gets the hook killed and
  produces no report at all. `fast` collects; `full` is the real battery and is run **detached**
  (`Start-Process -RedirectStandardOutput`), because ordinary backgrounded runs were killed mid-suite
  five times this week.

**The integration suite stays out of both** and must: it has no protection against `.env` pointing
`CONTROL_DB_URL` at live Neon production, and two of its modules spend money. Reasoning:
`.dev/reference/260815-gates-and-what-is-unsafe-to-automate.md`.

## What is true about this platform, in one paragraph

Substantially built, and until this week **never run as a system**. Phase A (E2E-0 to E2E-5) ran it
end to end for the first time and found six defects, four of which meant a headline feature had never
worked at all: ingestion (`1.26`), the grounding judge (`5.14`), the deployment checklist (`1.32`),
the deploy gate (`5.1`). An adversarial pass then found the same class inside the fixes (`1.33`).
Engineering gap small, **evidence gap the entire product**, because "defensible" is what is sold.

## Read these before trusting a number

- **`.dev/reference/260815-the-never-executed-class.md`** — what the six Phase A defects had in
  common, and the three greps that would have found them.
- **`.dev/reference/260815-wiring-is-invisible-to-behavioural-tests.md`** — the same class recurring
  three times in one week, including inside today's fix. This is the one that changes how you write
  a guard.
- **`.dev/reference/260815-adversary-phase-a.md`** — eleven mutations, five vacuous guards, one
  credential leak.

**Two fences now sit on every stored grounding verdict, not one.** `5.11` (before `dc67d37`: the
judge received an empty context) and `5.16` (before 2026-08-15: it received about half). `0.6`'s
`count(*)` counts artefacts as signal unless it fences on time.

**No `ship` verdict has ever been earned.** Every one came from seeded eval and red-team signals. The
eval has never been observed invoking the agent; red-team has never run 7/7 with tools.

## Blocked on the owner

| Item | What it needs |
|---|---|
| `1.22 · env-missing-platform-key` | **The app does not boot from `.env` on this machine.** `PLATFORM_CREDENTIAL_KEY` is HKDF master key material, so the value chosen becomes the key every `integration_credentials` row derives from |
| `0.1 · score-judge-calibration` | 10 human scores. Every judge in the system is uncalibrated, including the Actor gate that runs before money moves |
| `0.7 · model-provider-decision` | DeepSeek for the direct-API half, or nothing. Blocks `0.1` if there is no Claude balance |
| `0.4 · eval-pii-egress` | Decide before the first nightly eval: firewall on the eval path, or accepted egress named as such |
| `0.3 · actions-billing-cap` | CI reports nothing until this lifts |
| `1.20 · clerk-dev-keys` | Production Clerk instance. Also the prime suspect for `1.19` |
| `5.6 · tightened-ceiling-audit-row` | An audit-provenance decision that is not free: it re-scopes a live query |

## Running anything locally

Overlay, **never written to `.env`** (the real `.env` points `CONTROL_DB_URL` and Redis at
production):

```
CONTROL_DB_URL=postgresql+asyncpg://wchats:wchats@localhost:5432/wchats_control
CONTROL_DB_SYNC_URL=postgresql://wchats:wchats@localhost:5432/wchats_control
REDIS_URL=redis://localhost:6379/0
PLATFORM_CREDENTIAL_KEY=<generate>   S3_UPLOADS_BUCKET=wchats-uploads
S3_ENDPOINT_URL=http://127.0.0.1:9000   EMBEDDING_PROVIDER=voyage
AWS_ACCESS_KEY_ID=wchatsdev   AWS_SECRET_ACCESS_KEY=wchatsdevsecret
export ANTHROPIC_API_KEY   # os.environ, NOT just .env. This has cost four debugging cycles

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m celery -A app.worker.celery_app worker -Q runtime,pipeline -P solo -l info
C:/Users/Bantu/minio/minio.exe server C:/Users/Bantu/minio/data --address 127.0.0.1:9000
```

`-P solo` is required on Windows. Use `python -m celery`; the console script is not on PATH.

**Run the unit suite in a clean shell.** With the overlay sourced, `1.34` makes
`test_embed_chunks_routes_to_bedrock` place a real network call to Voyage and fail after 29s.

**Drain the Redis `runtime` queue before any costed run**, or a stale task for an unrelated agent
starts the moment a worker does.

**A crash mid-checklist blocks every later checklist for 60 minutes** (`1.31`) and `acks_late`
redelivery cannot rescue it. Reclaim:
`UPDATE checklist_runs SET status='failed' WHERE status='running'`.

**The live Neon project `mute-dream-53534177` is deliberately still up** (tenant `32b3715c…`, agent
`c14d13a1…`) with a real 16-chunk corpus. E2E-3b needs it. **Delete by id only, never by name
pattern.**

## Queue

`.dev/BACKLOG.md` is the single ordered list. Rows carry slugs (`5.1 · ops15-server-gap`); **the
number is an address, not a priority.** Use slugs in conversation.
