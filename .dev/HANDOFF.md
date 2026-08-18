# HANDOFF

**State as of 2026-08-18.** Current state only. The previous version, which had accreted into a
diary through M0-M3, is archived at `.dev/traces/260818-handoff-archive-through-m3.md`; nothing was
deleted.

**`.dev/MASTERPLAN.md` is the frame.** It carries the ordered path M0 to M7 and the one definition
of production readiness: the Mellow transactional agent live via the UI, and the Bantuson support
agent live via MCP, both tested on their Vercel URLs. Nothing else counts as done.

## Next move

**Everything the re-capture needed is landed. The re-capture itself is blocked on the owner, and
the next unblocked code item is `8.2`'s temperature half.**

| | State |
|---|---|
| `8.1` k > 1 record shape and pass@k | **landed 2026-08-18**, five mutation proofs, `260818-pass-at-k-corpus-shape.md` |
| `7.34` chunks in the corpus | landed, migration `0017` verified locally |
| `7.29` PII firewall | landed, five mutation proofs |
| **the re-capture** | **NOT owner-blocked. See "7.32 does not block the capture" below.** Needs `8.2` first (temperature, and the human column's scale) |
| **`8.2a` judge temperature** | **landed 2026-08-18.** Nine verdict sites at `temperature=0`; three generators deliberately not |
| **`8.2b` binary label + kappa** | **landed 2026-08-18.** Gate is Cohen's kappa >= 0.6, superseding AI-SPEC §5.2 |
| **`8.2c` confidence intervals** | **[code], OPEN, and it gates reading any number**: the harness prints point estimates |

`8.2a` goes before the re-capture rather than after it, because a judge sampling at the provider
default poisons every number downstream of it including the first calibration run. Measured
2026-08-18: `grep -rn "temperature" app tests/evals` returns nothing.

**The corpus still cannot be scored.** Run this and it says so itself:

```
apps/api/.venv/Scripts/python.exe apps/api/tests/evals/validate_corpus.py
```

`FATAL 15 rows, BLIND 14 rows, exit 1`. Two independent defects, and the second is the one that
matters more:

1. **Four responses are the PII firewall's deflection** (S-002, S-003, S-005, S-010). Scoring a
   deflection measures the firewall. `7.29`'s code fix has landed, so a re-capture returns real
   answers.
2. **No response carries a retrieved chunk.** `grounding_fidelity`'s rubric requires a claim to be
   traceable to a chunk *provided in the tool_calls log*, and every entry has `"result": {}`. The
   rubric's PASS branch is unreachable, so **every grounding verdict must FAIL regardless of the
   answer**. Every tool call is also named `""`, so the judge cannot see that retrieve was called
   either, and `run_evals.py` counts escalate and clarify calls by that same name.

**Do not score the current sheet.** Eight rows are readable, but the number that came out would be
agreement between a human and a judge that cannot see the evidence, on a corpus that has to be
re-captured anyway.

### `7.34` is decided and landed

The chunks existed only on the worker's in-process `tool_calls_log`. The owner chose the SERVED path
on 2026-08-18, so the corpus keeps measuring what a customer receives with the firewall applied:

```
worker turn ──> _persist_messages ──> tool_calls.retrieved_chunks   (migration 0017)
capture ──SSE──> response_text, then reads that row back ──> responses/S-0NN.json
```

**Migration `0017` is applied and verified** (2026-08-18, local `wchats_tenant_probe`, through
`run_tenant_migrations`): 0016 to 0017, column `jsonb` and nullable with no DEFAULT, NULL and `[]`
and a populated array all distinguishable in the database, downgrade drops it, re-upgrade restores
it. **The live tenant still needs migrating before a capture**, or the INSERT fails there.

This file previously said the migration could never run here. That was quoting a CLAUDE.md line
stale since 2026-08-10, and it was wrong: see `7.36`.

### `8.1` is landed, and what it changed about reading a number

The corpus records k runs per scenario. `tests/evals/corpus.py` owns the shape and all five call
sites read through it; `tests/evals/rates.py` computes the two metrics.

```
{"scenario_id": "S-001", "runs": [{"response_text": ..., "tool_calls_log": [...]}, ...]}
```

- **Position is the run index. Run 0 is the row the human scores**, and calibration reads run 0 only.
  Run 0 rather than the last run, because the last run moves under a top-up.
- **`capture_responses.py --runs K` TOPS A SCENARIO UP.** It no longer skips on the file existing,
  which under k meant "captured at some k, possibly 1". Deleting a file still re-captures it in full.
- **Each run is a fresh conversation with its own widget JWT.** A run continuing the previous run's
  conversation is turn k+1 of one session, and reliable@k over those measures the session.
- **The P0 gate is `reliable@k == 1.0`**, and a failure says NEVER passed (capability: change the
  model, tools or architecture) or FLAKY n/k (variance). Those need opposite work.
- **Aggregation is the mean of per-scenario rates**, never total successes over total runs, so a
  ragged corpus cannot let the scenarios that got more runs decide the number.

**The twenty files on disk are still the contaminated k=1 set**, and the harness says so in its own
output rather than implying otherwise: `runs per scenario: k=1 across all 20`, and a standing
warning under the summary table. Only the re-capture changes that.

### `7.32` does not block the capture, and `0017` is applied to the live tenant

**Measured 2026-08-18 with `apps/api/scripts/probe_environment.py`. Run that script; do not quote
this table.**

| Check | Result |
|---|---|
| Control DB password in `.env` | present in both files, and genuinely rejected: `password authentication failed for user 'neondb_owner'` |
| `NEON_API_KEY` scope | valid, sees **one** project: `mute-dream-53534177` (us-east-1). The control DB is `ep-falling-glade-ac3zhiqu` in **sa-east-1**, invisible to this key, so it cannot read or reset that password |
| **Local `wchats_control`** | **UP, alembic 0019 (head), 19 tables** |
| **Live tenant connection URI** | **retrievable** via `GET /projects/{id}/connection_uri`, pooled or direct |
| Live tenant corpus | 16 chunks, 2 documents, 54 conversations, 76 messages |
| **Live tenant migration** | **0016 to 0017 APPLIED and verified 2026-08-18.** `retrieved_chunks` is `jsonb`, nullable, no DEFAULT; 44 existing `tool_calls` rows untouched. Reproduce with `scripts/migrate_tenant.py mute-dream-53534177 --check` |
| `.env` Redis host (Upstash) | **does not resolve**: `gaierror getaddrinfo failed`. Local Redis answers `+PONG` |
| `ANTHROPIC_API_KEY` | in `.env`, **NOT exported** into `os.environ` (`1.28`) |

**The capture never needed the Neon control DB.** It needs *a* control DB to decrypt the tenant
connection string, and there is one on localhost at head. The plaintext tenant `API_KEY` stops
mattering once a local control row is seeded, because then whoever seeds it chooses the plaintext.

**This file said "BLOCKING, nothing can run until it is refreshed" for a whole session and that was
never tested.** Same class as `7.36`, one row over. `probe_environment.py` exists so the next
session runs the check instead of inheriting the sentence.

### `k=5`, and the arithmetic is written down

`.dev/reference/260818-how-much-k-and-why.md`. The fundamentals guide names no k: it gives one floor
(k > 1) and one warning that five trials is still unstable. Applying its own "quote an interval" rule
settles it: **k=5 is the smallest k where "never passes" `[0.00, 0.43]` and "always passes"
`[0.57, 1.00]` do not overlap at 95%.** At k=3 they overlap. k=5 is 100 live agent turns.

k=5 does **not** buy a shipping claim; the strongest honest sentence is "at least 57% reliable", and
k >= 10 is 200 turns. The top-up is the way out: a scenario that comes back flaky at k=5 goes to
k=25 for 20 more turns instead of re-capturing twenty.

### The human score column WAS on the wrong scale. Fixed, and still empty

**Decided and landed 2026-08-18 (owner).** The sheet now carries an empty `human_verdict`
column, the gate is Cohen's kappa >= 0.6, and `human_score` is optional. **Label BINARY, and write
why in `notes`.** What follows is why, kept because the reasoning outlives the change.

The fundamentals guide §8 is explicit: **"Binary, not a scale. Over a hundred traces a human cannot hold
a 1-5 scale steady."** §9's loop is human-labels-binary, then judge, then measure agreement.

Measured 2026-08-18:

```
human_scores.csv    scenario_id,dimension,human_score,notes    10 rows, ALL EMPTY
judge()             returns BOTH "verdict" (PASS/FAIL) and "score" (1-5)
run_evals.py        gates on verdict        <- already binary
compute_correlation correlates on score     <- the only 1-5 consumer
kappa / matthews    absent from the repo for judge calibration
```

So the binary signal already exists on the judge side. The 1-5 scale exists only to feed Spearman,
which AI-SPEC §5.2 chose before this practice was read. It costs three things: noisier labels,
no chance correction, and **no confusion matrix**, because kappa and a 2x2 are categorical and
cannot be built from 1-5 vs 1-5 without collapsing to binary first.

Moving the gate from Spearman to kappa contradicts AI-SPEC §5.2. **The owner decided it on
2026-08-18** ("the spec changes because i pushed back on it"), and all ten cells were still empty,
so it cost nothing. AI-SPEC has NOT been edited; `compute_correlation.py`'s docstring records the
supersession.

### The order, and why it is one capture rather than two

`human_scores.csv` is one row per (scenario, dimension) with no concept of a run. Scoring all k
multiplies the only human step in the system by k, which is why the sequence matters:

```
1. land 8.1              record shape holds k runs        DONE 2026-08-18
2. capture ONCE at k>1   run 0 of each scenario is the human's row
3. human scores run 0    ten rows, unchanged from today
4. judge is calibrated   against those human labels, at temperature 0
5. calibrated judge      scores all k runs -> reliable@k per category, with an interval
```

**Run 0 carries the human column, so human effort does not multiply and only one capture is
needed.** Reversing steps 4 and 5 buys nothing and costs k times the labelling.

**Correcting what this file said last:** `8.2` is not simply "after the capture". Its temperature
half must be set before ANY judging happens, and its kappa half is entangled with step 4 above.
`8.3` (per scenario category rather than pooled) is genuinely analysis-side and can land last, but
before anyone reads a number off the corpus.

### Then the re-capture, once, with everything already closed

The capture now validates itself and exits with the validator's code, so a contaminated run says so
while the services are still up rather than a day later at scoring time.

| Before running | State |
|---|---|
| `8.1` k > 1 and the record shape | **landed 2026-08-18.** Pass `--runs 5`; a scenario short of 5 is topped up, not skipped |
| `7.32` control DB credential | **not blocking.** Seed the LOCAL `wchats_control` (alembic 0019) with a tenant and agent row whose `neon_connection_string` is the Fernet-encrypted live tenant URI |
| plaintext tenant `API_KEY` and `AGENT_ID` | **not blocking.** Both are absent from `.env`, but seeding the local control row means choosing the plaintext |
| `7.34` chunk source | decided, landed, and **`0017` is APPLIED to the live tenant and verified** (2026-08-18) |
| `7.29` firewall | closed in code |
| `tool_name: ""` | closed in the capture script |
| `CAPTURE_TIMEOUT` | closed: default is 300, was 30 against a measured 101s turn |
| `CONTROL_DB_SYNC_URL` exported for the capture | needed for the chunk read-back. Unset means the run warns, records no chunks, and the validator reports BLIND |
| widget JWT expiry (`7.23`) | closed: minted per RUN, since k runs cross the 900s window k times sooner |
| Voyage throttling (`7.21`) | closed by the credit. Confirm by the ABSENCE of `rerank.voyage_failed_falling_back` in the run |
| `ANTHROPIC_API_KEY` exported into `os.environ` | operational, see below |
| Redis `runtime` queue drained, tenant Neon pre-warmed | operational, see below |

**Re-capture all twenty, not the four.** The unnamed tool calls and missing chunks affect every row,
not just the deflected ones.

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
| `7.32 · control-db-credential-stale` | **RE-SCOPED 2026-08-18: it does NOT block the capture.** The credential really is rejected, and `NEON_API_KEY` cannot reach that project to fix it, so refreshing it from the Neon console is still yours. But the local control DB is at head and the tenant URI is retrievable, so nothing waits on this. It blocks only work that must read PRODUCTION control state |
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
| `full` | fast + the unit suite | **green 2026-08-18 on the `8.2` tree**: **2495 passed, 13 skipped, 454.6s**, whole target 534.4s, exit 0. A KILLED run reports `FAILED at step 5`, which is indistinguishable from a real failure until you read the log for an `F` |

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
4. **A `full` run stalled at 2 percent is not stalled.** `tests/unit/test_agent_task.py` runs at
   about 11 seconds a test and sits early in the alphabet, so the first 5 percent of 2470 takes
   roughly twenty minutes and the remaining 95 percent takes a few. Measured 2026-08-18: 126 tests
   at 14:31:43, 1400 at 14:34:34. The 468.8s whole-suite figure is consistent with that.

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

- **`260818-eval-practice-gap-analysis.md`** and the two beside it are findings. This one is a
  warning: **`7.36`, an environment constraint was quoted for eight days and never tested**, and
  it propagated into seven files in one session. Open a socket before repeating a limit.
- **`260818-llm-eval-fundamentals.md`** — the practice itself, written down so it outlives the
  session that watched it: capability against consistency, the three levels, trace analysis and
  saturation, building an aligned judge, judging the judge, RAG retrieval metrics, judge bias.
- **`260818-eval-practice-gap-analysis.md`** — nine things two eval practitioners do that we do
  not, each checked against the code before being written down. Act on the first one first:
  every scenario runs ONCE, so no number we have separates "cannot" from "sometimes".
- **`260818-pass-at-k-mutation-proofs.md`** — the five guards `8.1` added, each mutated and
  observed red. Every one of them mutates a SILENCE: the mutant returns a plausible number rather
  than raising, which is the class the note below generalises.
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
