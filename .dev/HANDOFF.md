# HANDOFF

**State as of 2026-08-22.** Current state only. The previous version, which had accreted into a
diary through M0-M3, is archived at `.dev/traces/260818-handoff-archive-through-m3.md`; nothing was
deleted.

**`.dev/MASTERPLAN.md` is the frame.** It carries the ordered path M0 to M7 and the one definition
of production readiness: the Mellow transactional agent live via the UI, and the Bantuson support
agent live via MCP, both tested on their Vercel URLs. Nothing else counts as done.

## Next move

**Decided 2026-08-22: refactor in place, and the queue moves to GitHub issues.** The measurement
is `.dev/reference/260822-salvage-or-restart.md`; its six changes (cut the eval subsystem to one
`ship` path, `app/domain/` with a new import-linter rung, lizard and `inspect.getsource` gates with
shrinking exemption lists, ADRs replacing plan plus trace, M2 before more M1) are the work.

**The owner runs `/wayfinder` next.** It is user-invoked only. It builds the map issue that
replaces `MASTERPLAN.md`, from that reference note, `PRODUCTION-READINESS.md`, and
`.dev/reference/260818-llm-eval-fundamentals.md`. The harness and eval tickets are the ones that
need the most grilling: validating the harness is the hardest thing in this project, and the
fundamentals doc is what every such ticket cites.

Conventions changed the same day, recorded in `CLAUDE.md` "Agent skills" and `docs/agents/`:
Matt Pocock's engineering skills are installed under `.agents/skills/`; `BACKLOG.md`,
`MASTERPLAN.md` and `plans/` are frozen; `traces/`, `reference/` and `retro.md` carry on.
Everything below this line was the state on 2026-08-18 and waits behind the map.

**Gates on 2026-08-22, observed:** `gates.py full` exit 0 in 1085.7s, 2612 passed, 13 skipped,
0 failed. Admin `tsc` 0 errors, `test:unit` 45 passed. Widget 9471 bytes gzipped.

**Two things from the owner after that.** How many of the 45 available calibration rows will be
labelled, twice (the sheet size), and whether `--emit-second-pass` may top up an existing sheet
(`8.15`). Neither is urgent: the corpus is contaminated, so there is nothing to label until the
re-capture.

| | State |
|---|---|
| `8.1` k>1 record shape, pass@k | **landed + adversarially reviewed.** 3 BLOCKs and 11 surviving mutants fixed; 12/12 mutations now red |
| `8.2a` judge temperature | **landed + reviewed.** 3 tautologies fixed |
| `8.2b` binary label + kappa | **landed + reviewed.** 2 BLOCKs fixed, one of which broke the owner's workflow |
| `8.2c` data-derived threshold | landed, then **half of it was wrong**. See `8.2d` |
| `8.2d` the rework | **landed 2026-08-19, 24 of 24 mutations red.** Four independent reviews found the replacement threshold was itself a constant nobody chose. The rule is three parts now |
| **the sheet size** | **[owner] the one open decision, and now the only one left in the harness.** See "What labelling costs" below |
| the re-capture at k=5 | **[code], unblocked.** 100 live turns. Seed the LOCAL control DB; `7.32` does not block it |
| `7.34` chunks, `7.29` firewall | landed; `0017` applied and verified on the live tenant |

### The gate, after four adversarial reviews found the first version wrong

`KAPPA_THRESHOLD = 0.6` is deleted. **0.6 was a Landis-Koch band boundary: a 1977 rule of thumb
published with no empirical basis**, so it was not merely unmeasured here, it was never measured
anywhere. What decides now is two intervals bootstrapped from the labels themselves, and BOTH are
required:

```
(a)  the judge beats chance    judge_ci_low   > 0
(b1) your labels set a scale   ceiling_ci_low > 0
(b2) the judge reaches it      paired difference (ceiling - judge), measured
                               inside each resample, does not put you above it
```

**`8.2c` shipped `(b) judge_high >= ceiling_low` and that was a constant nobody chose.** Against a
self-consistent labeller it reduces to `judge_high >= 1.000`, which needs 2.5% of resamples to miss
every judge error row: `e <= 3.68` disagreements **at every n**. Looser than kappa 0.6 below n=18,
stricter above, and harder the more rows you label. Two reviewers also produced exit-0 CALIBRATED
from a ceiling whose own interval spanned zero, which is what (b1) now refuses.

(b) is the one that matters. **A judge cannot be expected to agree with a human more than that
human agrees with themself**, so the owner's test-retest kappa IS the scale, and with no ceiling
measured the harness refuses rather than inventing a number.

**The second pass is a separate FILE, not a `human_verdict_2` column, and the plan said otherwise.**
A column sits on the same row as the first verdict, so the labeller reads their own answer while
writing the new one, and the ceiling then measures memory rather than consistency. Trace:
`260818-kappa-threshold-from-data.md`.

**What the owner runs, in order:**

```
compute_correlation.py --check              names every missing input; makes no judge call
    ... label human_scores.csv ...          binary pass/fail, plus the reason in notes
compute_correlation.py --emit-second-pass   writes human_scores_pass2.csv, shuffled and empty
    ... label that, WITHOUT opening the first sheet ...
compute_correlation.py                      the run: judge interval against the ceiling
```

`--emit-second-pass` refuses to overwrite an existing sheet, refuses while the first pass is
unfinished, and refuses a sheet with no rows. **It cannot yet top up a sheet you have already
emitted, which is `8.15` and the one reviewer BLOCK still open.**

`--check` now names every locally-checkable input before any money is spent: the label balance
(at least 2 rows must carry each label, derived from `e^-m`), whether `ANTHROPIC_API_KEY` is
exported into `os.environ` rather than merely in `.env`, and every row either sheet could not
read, by name.

### What labelling costs, and what a row is

A row is one **(scenario, dimension)** pair. **45 apply across the 20 scenarios; the sheet uses 10.**
Adding rows is a sheet change and costs nothing; the owner's cost is the two columns at the end:
`human_verdict` (pass/fail) and a one-line reason in `notes`. Then the same rows again, blind.

Measured 2026-08-18, 95% bootstrap interval on a judge wrong about 2 rows in 10:

```
rows      judge interval        what can be concluded
  10      [-0.09, 1.00]         nothing; it includes zero
  15      [ 0.05, 1.00]         beats chance, barely
  20      [ 0.17, 0.90]         beats chance
  30      [ 0.26, 0.86]         beats chance, and the interval is readable
```

The owner's own test-retest ceiling needs the same: at 10 rows it is `[0.19, 1.00]`, too wide to cap
anything; at 30 it is `[0.52, 1.00]`.

**The sheet must span all four scenario categories deliberately.** The 45 rows skew hard
(`escalation_accuracy` 20, `session_continuity` 2), and kappa needs BOTH labels present. If every
labelled row comes back `pass`, kappa is undefined and the exercise reports "cannot establish".
Adversarial and out-of-scope scenarios are where the `fail`s come from.

### What the two adversarial reviews changed

Both ran as independent agents. Between them: **5 BLOCKs, 14 surviving mutants, and six claims of
mine that were false.** All fixed or filed; **19/19 mutations now go red.** Traces carry the detail;
the four that change how someone works here:

- **The workflow given to the owner crashed on first use.** `compute_correlation` prints "a 1-5
  score is optional" and its table then did arithmetic on that `None`. The gate had computed
  `kappa=1.0 calibrated`; the CLI died before printing it, and a shell reads the non-zero exit as
  NOT calibrated. The fixture derived the verdict FROM the score, so the row shape the harness asks
  for was inexpressible in any test.
- **Excel would have broken the sheet silently.** "CSV UTF-8" writes a BOM; every row parsed valid
  with an empty `scenario_id` and readiness reported READY over a file producing zero pairs. Now
  `utf-8-sig`, and a padded header is normalised rather than read as an unlabelled sheet.
- **A `getsource` guard was bypassed by one indirection.** Moving `{"temperature": 0}` to a module
  constant left the red-team probe deterministic on the wire and invisible to the check. Assert on
  the kwargs the client receives; never on source text.
- **`pass@k` was biased under ragged k** while `reliable@k` was not. It grows with k on its own, so
  it is now `None` on a ragged corpus rather than printed with a warning beside it.

### Numbers that were repeated here and were wrong

- "every LLM call sampled at the provider default": false for the two Ragas sites, which were at
  `temperature=0.01, top_p=0.1` (`8.10`).
- "3 to 8 percent verdict variance survives temperature 0": quoted from a talk, never measured here,
  and it had reached six production comments (`8.11`). This is `7.36` happening inside code.
- "every verdict samples at temperature 0": false of the deployment orchestrator; four
  `claude_agent_sdk` sites take no temperature and one returns a verdict (`8.12`).

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

### The k, the label scale, and where their reasoning lives

`k=5` because it is the smallest k at which "never passes" `[0.00, 0.43]` and "always passes"
`[0.57, 1.00]` have non-overlapping 95% intervals; k=3 they overlap. Arithmetic:
`260818-how-much-k-and-why.md`.

The human label is BINARY (`human_verdict`), with the 1-5 `human_score` optional and feeding only
the reported Spearman. A human cannot hold a five-point scale steady across many rows, and the judge
already returned both a verdict and a score, so only the human column was ever on the wrong scale.
AI-SPEC §5.2 is struck through on that line, which is the one deliberate edit to frozen `.planning/`.
Reasoning: `260818-judge-temperature-and-kappa.md`.

### The order, and why it is one capture rather than two

`human_scores.csv` is one row per (scenario, dimension) with no concept of a run. Scoring all k
multiplies the only human step in the system by k, which is why the sequence matters:

```
1. 8.1 record shape       k runs per scenario              DONE 2026-08-18
2. 8.2a judge temperature before ANY judging happens       DONE 2026-08-18
3. 8.2b binary label      before the owner labels anything DONE 2026-08-18
4. 8.2c the threshold     before any kappa is read         DONE 2026-08-18
5. size the sheet         N of 45 rows, spanning categories OWNER: the one open number
6. capture ONCE at k=5    run 0 of each scenario is the human's row
7. owner labels run 0     N binary verdicts + N reasons
8. owner RE-labels blind  the same N, in human_scores_pass2.csv
9. the gate computes      judge CI against the human ceiling
```

**Run 0 carries the human column, so human effort does not multiply and only one capture is
needed.** Steps 2 to 4 all come BEFORE the capture, and each for a different reason: a judge
sampling at the provider default poisons every number downstream of it, the sheet's shape changes
what the owner writes, and the threshold decides whether any of it can be read.

`8.3` (per scenario category rather than pooled) is genuinely analysis-side and can land last, but
before anyone reads a number off the corpus.

### Then the re-capture, once, with everything already closed

The capture now validates itself and exits with the validator's code, so a contaminated run says so
while the services are still up rather than a day later at scoring time.

| Before running | State |
|---|---|
| `8.1` k > 1 and the record shape | **landed and adversarially reviewed.** Pass `--runs 5`; a scenario short of 5 is topped up, not skipped |
| `8.2c` the threshold | **landed.** The numbers are readable as soon as both label passes exist |
| the sheet size | **[owner] OPEN.** It does not block the capture either. Label after |
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
| `0.1 · score-judge-calibration` | **Still the one thing between here and M1 closing, and the shape of the ask changed on 2026-08-18.** Not three rows of 1-5 scores: a BINARY `human_verdict` plus a written reason, on as many of the 45 available (scenario, dimension) rows as the owner will take, and then the SAME rows again blind for the ceiling. The number is the open decision; 10 rows can establish nothing, ~30 can. Nothing to label until the re-capture |
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
| `full` | fast + the unit suite | **green 2026-08-19 on the tree carrying `8.2d`**: `fast` (ruff, import contracts, lizard, 2625 collected) **157.3s**; unit suite **2612 passed, 13 skipped**, exit 0. The suite figure was 876s here rather than the usual ~510s because reviewer agents were running against the same 4 GB; time it on a quiet machine before treating it as a baseline. A KILLED run reports `FAILED at step 5`, indistinguishable from a real failure until you read the log for an `F` |

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
- **`260818-the-human-ceiling.md`** - what a judge can be held to, and the three ways collecting
  the ceiling destroys it. Read before building any labelling surface (`2.4`, `2.28`): the prior
  verdict must be absent, the order different, and the rater's own prior reasoning off the screen.
- **`260818-llm-eval-fundamentals.md`** — the practice itself, written down so it outlives the
  session that watched it: capability against consistency, the three levels, trace analysis and
  saturation, building an aligned judge, judging the judge, RAG retrieval metrics, judge bias.
- **`260818-eval-practice-gap-analysis.md`** — nine things two eval practitioners do that we do
  not, each checked against the code before being written down. Act on the first one first:
  every scenario runs ONCE, so no number we have separates "cannot" from "sometimes".
- **`260818-what-a-self-review-missed.md`** - the same branch reviewed three times, with the
  counts. The self-review probed no mutants; the two independent passes found 11 of 22 and 3
  of 14 surviving. Read it before deciding a self-review is enough, and for the four finding
  types an author structurally cannot reach.
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
