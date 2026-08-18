# HANDOFF

**State as of 2026-08-18.** Current state only. The previous version, which had accreted into a
diary through M0-M3, is archived at `.dev/traces/260818-handoff-archive-through-m3.md`; nothing was
deleted.

**`.dev/MASTERPLAN.md` is the frame.** It carries the ordered path M0 to M7 and the one definition
of production readiness: the Mellow transactional agent live via the UI, and the Bantuson support
agent live via MCP, both tested on their Vercel URLs. Nothing else counts as done.

## Next move

**One owner action, then M1 closes: score eight rows.** Fill the `human_score` column (1 to 5) in
`apps/api/tests/evals/calibration/human_scores.csv` — ten rows present, **two of them now marked
skip** (below), three is the gate's minimum — then run:

```
apps\api\.venv\Scripts\python.exe apps\api\tests\evals\calibration\compute_correlation.py
```

A scoring sheet pairing each captured answer with the judge's own rubric is published at
**https://claude.ai/code/artifact/179dc28f-ce9c-45e9-b2f4-ddedbc381dec** (regenerate with the
scratchpad builder if the corpus is ever re-captured). The scale is the judge's: 1 clear failure,
3 borderline, 5 clear pass. `--check` reports `20 scenarios / 20 responses / 0 of 3 human scores`,
**exit 3 = NOT READY**, which is neither pass nor fail. Nothing but a human may fill that column: a
judge scored against model-written labels measures its agreement with itself. **The scores are
per-provider** — they calibrate DeepSeek.

**S-002 and S-003 are still unscorable, and `--check` now says so itself** rather than leaving it in
this file: it prints `PII-deflected, unscorable : S-002, S-003 (8 scorable row(s) remain)`. Both
hold the PII firewall's deflection, and scoring a deflection measures the firewall rather than
grounding. Eight rows is comfortably over the minimum, but **`grounding_fidelity` rests on S-001
alone** until those two are re-captured.

**`7.29`'s code has landed** (`952fac0`), so a re-capture would now return real answers. It is
blocked on two things this machine does not have, `7.32` and the plaintext tenant API key, both in
the owner table below. **Scoring the eight does not wait for either.**

**Then M4.** M4 also carries M3's unmet exit criterion (below).

## Where things stand

| Milestone | State |
|---|---|
| **M0** merge | **Done.** `main` fast-forwarded 116 commits, battery quoted green |
| **M1** measurement | **One human step from done.** `7.7` DeepSeek seam, `7.8` Martin battery, E2E-3b verdict, `7.18`/`7.20` Ragas, E2E-6 corpus all landed. Only the Spearman gate remains |
| **M2** first earned ship | Not started. Needs a real eval run and a 7/7 red-team run |
| **`7.29`** the firewall | **Code landed 2026-08-18**, five mutation proofs red, trace `260818-pii-firewall-published-contacts.md`. The deflected corpus files are still on disk, so only the re-capture is open. Four responses were affected, not three, and one was an edge scenario |
| **M3** widget + endpoint | **Done** (`7.1`-`7.6`, `7.23`), trace `260818-m3-widget-endpoint.md`. **Exit criterion deliberately unmet**: PROD-11 needs a public API base, so it moves to M4 |
| **M4** cloud | Not started. Absorbs PROD-11, the BYO-client proof and the endpoint doc page |
| **M4.5** unit economics | Scheduled gate before any UI polish (owner, 2026-08-17) |
| **M5**-**M7** | Console polish, Mellow live, MCP + portfolio agent |

**Where the code is:** branch `chore/m0-gate-followups`, ahead of `main`, **unmerged**. The owner
merges; Claude never does (the `PreToolUse` hook enforces it).

## Blocked on the owner

| Item | What it needs |
|---|---|
| `0.1 · score-judge-calibration` | **The one thing standing between here and M1 closing.** Three rows minimum in `human_scores.csv` |
| `7.32 · control-db-credential-stale` | **NEW, and it outranks the rest of this table: no live run can start.** The `.env` control DB credential is rejected by Neon as of 2026-08-18, having worked earlier the same day. Refresh it from the Neon console. Everything below that needs live services is blocked behind it |
| `7.29` re-capture | `AGENT_ID` and the **plaintext** tenant `API_KEY`, on top of `7.32`. Only the key's hash is stored, so it cannot be recovered here. With both, delete `tests/evals/responses/S-002.json`, `S-003.json`, `S-005.json` and `S-010.json` and run the capture WITHOUT `--overwrite`: it skips files that already exist, so exactly those four re-run, four agent turns of cost |
| `0.4 · eval-pii-egress` | Decide BEFORE M4 deploys a beat worker: PII firewall on the eval path, or accepted egress named as such. `eval-nightly` fires the first night beat exists |
| `0.3 · actions-billing-cap` | CI reports nothing until this lifts (M4) |
| `1.20 · clerk-dev-keys` | A production Clerk instance and its three keys (M4). Also the prime suspect for `1.19` |
| Stripe → **Paystack** | M6 needs a Paystack account plus test-mode key. `7.10` carries the adapter design |
| `5.6 · tightened-ceiling-audit-row` | An audit-provenance decision that re-scopes a live query |
| `0.5 · ratify-missing-migration` | Record-keeping only now: the deviation merged at `57be16b`, so the decision was made by merging. Say which way, cheaply, either way |
| `0.6 · size-labelling-loop` | One query against production, and it is **correctly after launch** — it sizes a loop that has no traffic to size yet |

## Gates

Declared in `.dev/gates.json`, and **the split changed on 2026-08-18**: it is now by cost of import,
because that is where the harness's 170s clamp actually bites.

| Target | Steps | Measured |
|---|---|---|
| `static` | ruff, import contracts, lizard | 8.4s cold, 3.2s warm. **What the Stop hook runs.** Nothing in it imports app code, so its headroom cannot erode by adding a dependency |
| `fast` | static + whole-suite collection | 142.5s and growing with the dependency tree, not with the suite |
| `full` | fast + the unit suite | **693.4s green on 2026-08-18**: suite 547.4s, 2364 passed, 13 skipped, exit 0 |

The old hook gate was whole-suite `--collect-only`. `gates.json` had warned in its own comment that
a heavy dependency would push it past the clamp and that being killed there reports nothing at all.
It did, and it was.

```
apps/api    .venv\Scripts\python.exe scripts\gates.py full   # run DETACHED, never from the hook
apps/admin  npx tsc --noEmit      # ZERO errors; the old known exception was fixed (7.9)
            npm run check:no-dusk-tokens · check:ops-room-wiring (13/13) · test:unit (45)
apps/widget npm run build         # postbuild: check-size + check-theming-contract + sync-embed
            npm run test:unit     # vitest, new in M3
```

Last observed 2026-08-18: widget 9471 of 20480 gzip bytes · admin all green.

**The backend battery was RED for two days and nothing noticed**, because the gate that ran at the
end of every session imported every module and asserted nothing. Three failures, none from the work
that found them: two from `7.18`'s Ragas rename reaching a patch target in the never-executed
integration suite (now pinned, `7.33`), one from the E2E-6 capture filling a directory a test
asserted was empty. **Run `full` before believing the suite is green**; `static` says nothing about
behaviour and is not meant to.

**Still not green and not diagnosed:** admin Playwright e2e, 7 failed / 128 passed, all 90s
timeouts (`1.19`, last run 2026-08-12). **The integration suite stays out of every gate** and must:
it has no protection against `.env` pointing `CONTROL_DB_URL` at live Neon, and two modules spend
money (`.dev/reference/260815-gates-and-what-is-unsafe-to-automate.md`).

## Running anything locally

The real `.env` points `CONTROL_DB_URL` at live Neon and Redis at Upstash. **Always overlay, never
write these into `.env`:**

```
CONTROL_DB_URL=postgresql+asyncpg://wchats:wchats@localhost:5432/wchats_control
CONTROL_DB_SYNC_URL=postgresql://wchats:wchats@localhost:5432/wchats_control
REDIS_URL=redis://localhost:6379/0        EMBEDDING_PROVIDER=voyage
S3_ENDPOINT_URL=http://127.0.0.1:9000     S3_UPLOADS_BUCKET=wchats-uploads
AWS_ACCESS_KEY_ID=wchatsdev               AWS_SECRET_ACCESS_KEY=wchatsdevsecret

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m celery -A app.worker.celery_app worker -Q runtime -P solo -l info
C:/Users/Bantu/minio/minio.exe server C:/Users/Bantu/minio/data --address 127.0.0.1:9000
```

`-P solo` is required on Windows; use `python -m celery`, the console script is not on PATH.
`ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` must be **exported into `os.environ`**, not merely
present in `.env` — that has cost four debugging cycles, most recently as a provider split-brain.

**Before any costed run, in this order:**

1. **Drain the Redis `runtime` queue**, or a stale task for an unrelated agent starts the moment a
   worker does. Five were purged mid-M1 from killed runs.
2. **Pre-warm the tenant Neon endpoint** with a bad-credential psycopg2 probe until it fails FAST
   (about 2s, `password authentication failed` = warm; a slow timeout = still waking). **TCP connect
   lies** — the proxy accepts while the compute wakes.
3. Run the unit suite only in a **clean shell**: with the overlay sourced, `1.34` makes one test
   place a real Voyage network call.

**The live Neon project `mute-dream-53534177` is deliberately still up** (agent `c14d13a1…`) with a
real 16-chunk corpus. Every live run uses it. **Delete by id only, never by name pattern.**

**But it cannot be reached right now (`7.32`).** The `.env` control DB credential is rejected, and
the tenant connection string is decrypted from the control DB, so both are out of reach until the
credential is refreshed. `NEON_API_KEY` sees only the tenant project, not the control one.

**A crash mid-checklist blocks every later checklist for 60 minutes** (`1.31`); `acks_late`
redelivery cannot rescue it. Reclaim with
`UPDATE checklist_runs SET status='failed' WHERE status='running'`.

## What is true about this platform

Substantially built, and until two weeks ago **never run as a system**. Phase A ran it end to end
for the first time and found a defect at every step, four of them meaning a headline feature had
never worked in any environment. M1 and M3 continued the pattern: the DeepSeek seam was dead for
every judge until proven live, Ragas had three stacked defects behind one error message, and
switching per-tenant theming on is what made a widget control invisible.

The engineering gap is small. **The evidence gap is the product**, because "defensible" is what is
sold. Two things remain unearned: **no `ship` verdict has ever come from real signals**, and **no
judge in this system has been calibrated against a human** — including the Actor gate that runs
before money moves.

## Read these before trusting a number

- **`260818-green-for-the-wrong-reason.md`** — four checks that were green while the thing they
  guard was broken, found in one session, each with the cheap test that separates them. Read it
  before trusting any negative test, and before writing a mutation proof.
- **`260815-the-never-executed-class.md`** — what the Phase A defects had in common, and the three
  greps that would have found them.
- **`260815-wiring-is-invisible-to-behavioural-tests.md`** — the class that recurs most. Changes how
  you write a guard: assert on the argument the consumer receives, not the syntax that produces it.
- **`260817-e2e6-capture-blocked.md`** — how a throttled provider quietly contaminates a corpus, and
  why four captured files were deleted rather than kept.
- **`260815-two-v12s-and-the-loop-that-does-not-close.md`** — A2A and the CLI were never built; the
  improvement loop's backward arrows are all broken.

**Two fences sit on every stored grounding verdict**: `5.11` (before `dc67d37`, empty context) and
`5.16` (before 2026-08-15, half context). Any `count(*)` over stored verdicts counts artefacts as
signal unless it fences on time.

## Queue

`.dev/BACKLOG.md` is the single ordered list, maintained transactionally. Rows carry slugs
(`5.1 · ops15-server-gap`); **the number is an address, not a priority.** Use slugs in conversation.
