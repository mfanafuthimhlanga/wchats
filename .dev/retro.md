# Retro — regression and planning-defect log

Append-only. A regression reaching `main` is a planning defect: record what the plan failed to
anticipate, not just what the code did wrong. Recurring families raise planning depth.

---

## Family A — "a measurement that cannot fail"

**Recurrences: 4 (as of 2026-08-05).**

1. **Eval target leakage.** `eval.py:200` set `agent_response = reference_answer`, so Ragas scored
   the label against the contexts the label was generated from. Scores approach 1.0 by construction.
   Shipped in M6 as scaffolding ("to test the eval harness"), never revisited across 17 phases.
2. **Red-team tests patch out the code under test.** `test_red_team_service.py:165/195/221` patch
   `asyncio.run` with a canned return, so `_run_agent_loop` — which contains D4's unregistered-tool
   defect — never executes. 1199 green tests over a loop that cannot work.
3. **Negative tests never observed to fail.** Phase 22 caught this prospectively and made
   guard-removal demonstrations mandatory (`22-01`: mutate → assert red → restore from `HEAD` →
   assert green). That discipline exists *because* of this family and must not be dropped.
4. **Judge calibration harness with zero labels.** `compute_correlation.py` gates judge trust at
   Spearman ≥ 0.75 against human scores; `human_scores.csv` has 10 rows, every score cell empty. The
   instrument reads "not calibrated" as "no scored rows yet — exit 0, informational."

**What the plans failed to anticipate:** every one of these passes its own acceptance criteria. The
criteria asked "does the code run and do the tests pass", never "could this test fail if the
behaviour were deleted". A green suite is evidence about the suite, not about the system.

5. **`_run_orchestrator_loop` was never awaited** (2026-08-06, found by the tier-2 judge). The
   backend suite emits this `RuntimeWarning` from `test_deployment_service.py` — the *same warning
   class* this repo's own audit cites as runtime proof that D4's attacker loops were mocked away.
   `run_orchestrator` is never executed anywhere on the branch, so every claim about how the
   orchestrator prompt's prose blocking conditions interact with the evidence gate is untested by
   construction. **Four implementation phases and the tier-1 reviewer all read past it**, in the very
   branch built to eliminate this family.
6. **A test whose docstring claims bidirectional protection, demonstrated false in one direction**
   (2026-08-06). `test_the_capability_flag_cannot_be_flipped_without_wiring_the_tools` stayed GREEN
   under tier-1's mutation deleting `mcp_servers=` — `ALLOWED_PROBE_TOOLS`' references to
   `_TOOL_SEND_PROBE['name']` satisfy the string count with no wiring present.
7. **A guard demonstrated only inside the complement of its own blind spot** (2026-08-06). The
   `human_scores.csv` write-ban is a substring list missing the most idiomatic form,
   `with open(HUMAN_SCORES_CSV, 'w')`. Its red demonstration used `csv.DictWriter` — a form that *is*
   on the list. The demonstration passed; the guard has a hole.

**Standing rule:** for any guard, absence pin, or fail-closed path — mutate it, observe red, restore
from `HEAD` unconditionally, observe green, record the observed output.

**Second standing rule, added 2026-08-06:** mutate the guard *in the form the defect would actually
take*, not in a form you already listed. And treat a `RuntimeWarning: coroutine ... was never awaited`
as a **finding**, not noise — it is this repo's most reliable runtime signal that a region is mocked
away. Grep the gate output for it.

**Counter-example worth keeping.** During P1 an implementer mutated the narrow-`except` guard on
`insert_eval_run` and the test **stayed green** — it had injected failure on both the wide and narrow
INSERT, so the fallback re-raised and the test passed for the wrong reason. It diagnosed its own
tautology, rewrote the test to inject on the wide INSERT only plus assert the fallback never ran, and
re-ran the mutation to a real red. That is the discipline catching itself, and it is the standard.

## Family B — "missing data treated as passing data"

**Recurrences: 2.**

1. **Deploy gate eval fetch.** `deployment_service.py:201` queries columns that do not exist; the
   error is caught at `deployment.py:157` and substituted with `pass_rates: {}`. The blocking
   condition "any pass_rate < 0.70" cannot fire over an empty dict. Fails **open**. The same file's
   `_fetch_verified_qa_stats_sync` has exactly the right defensive shape — it was simply not applied
   here.
2. **Zero findings from zero probes.** Five red-team attackers return `[]` because they were never
   given their tools; the run reports **clean**. Indistinguishable from a genuinely clean run.

**What the plans failed to anticipate:** an exception handler that supplies a default is a *decision
about what missing data means*, and it was made in passing, inside a `try/except`, without anyone
writing down which way it should fail.

**Standing rule:** a metric over zero valid observations is `unknown`, never `pass`. Every run reports
`(attempted, valid, findings)` — a rate without its denominator is not a measurement. `unknown` and
`pass` must never render the same on screen.

**Prior art in this repo, already correct:** `red_team_service.py:1076` treats
`provider_not_configured` as a finding because the run was *"INVALID, not clean."* One place got it
right; it never became a system rule.

## Family B, recurrence 3 — Langfuse generation tracing has never run

Found 2026-08-06 by mypy, on a branch whose only purpose was making the type checker run at all.

`validation_service.py:382`, `actor_seam.py:279` and `agent.py:443` all called
`_langfuse.start_as_current_generation(...)`. **`langfuse 4.14.0` has no such method** — verified at
runtime: `hasattr(langfuse.Langfuse, 'start_as_current_generation')` is `False`, and the only
`start_*` methods are `start_as_current_observation` and `start_observation`.

Every call site is inside a `try: ... except Exception:` fire-and-forget wrapper, so the
`AttributeError` was swallowed silently. **Judge tracing, Actor-gate tracing and agent-turn tracing
have therefore never produced a single Langfuse generation**, while the code reads as though
observability is wired and CLAUDE.md rule 3 records "Langfuse v4 API only" as satisfied.

Fixed here by switching all three to `start_as_current_observation(as_type="generation", ...)`.

**What the plans failed to anticipate:** a fire-and-forget `except` around an observability call
converts "this feature is broken" into "this feature is quiet". Nothing distinguishes a tracer that
emitted nothing because nothing happened from one that emitted nothing because the method does not
exist. Same shape as D3's swallowed `UndefinedColumn` and the tool-less attackers' clean runs — third
recurrence, third different subsystem.

**Standing rule:** an `except` around an optional subsystem must log at a level someone reads, and
name the exception type. If observability is worth calling, its failure is worth one warning line.

## Family C — "an in-memory or ephemeral marker advanced before/instead of a durable write"

**Recurrences: 2.**

1. **Eval results written to a branch that is then deleted.** `eval.py:281-283` writes results,
   promotions and the terminal status to `branch_conn_str`; `:313` deletes the branch in `finally`.
   Production keeps a row stuck at `running` forever. Every eval observation the system has ever
   produced is gone.
2. **Dispatch-after-claim window** (`T-22-ACT-09`, OD-6): the confirmation claim commits before the
   Celery task is enqueued; an enqueue failure leaves a `resolved` row whose task never ran.
   Deliberately accepted, not closed.

**What the plans failed to anticipate:** D-10 ("never evaluate against production") was applied to
*all* writes rather than to tenant-data writes only. Observations about a run are not tenant data.
The isolation rule was right; its blast radius was never scoped.

## Family D — "the seam between two units belongs to neither"

**Recurrences: 2.**

1. **Wave-crossing seam, Phase 21.** `21-05` shipped in Wave 1 before `21-06` created
   `promote_trace_to_scenario` in Wave 3; grading a trace never dispatched the promotion. Caught by
   the phase verifier. Lesson recorded: *"when a seam crosses waves, the LATER plan must own the
   wiring."*
2. **Phase-crossing seam, milestone v1.2.** Phase 20 shipped six ops-room regions with honest empty
   states; Phase 21 was explicitly *"backend-only, no frontend artifacts"*; neither owned the seam.
   A grep for the six endpoints across `apps/admin` + `apps/widget` returned **zero files**. 13 of 24
   requirements passed phase acceptance while being unreachable by a user. Phase 23 exists to close
   it.

**What the plans failed to anticipate:** the wave-level lesson was recorded and did not generalize to
phases. Phase 23's answer — each region plan creates *and mounts* its component in the same plan, so
no seam crosses a wave at all — is the stronger form and should be the default.

**Standing rule:** a seam is owned by the unit that makes it reachable by a user, and that unit ships
both sides.

## Family E — "a human signal stored as its own opposite"

**Recurrences: 1.**

1. **Bench flywheel label inversion.** `traces.py:84` lists *failing* traces. The operator grades one
   `filed`. `bench.py:147` stores `reference_answer=agent_turn` — the agent's own failing answer
   becomes the ground truth for that question.

**What the plan failed to anticipate:** "promote a filed trace into a scenario" is ambiguous about
*which field is the label*, and nobody asked. The plan reviewed as sensible because the sentence is
sensible.

**Standing rule:** when a human supplies a signal, write down what the signal *means* before writing
where it is stored. A label whose polarity is unstated will eventually be stored backwards.

## Family G — "prose that describes an architecture its own diff deleted"

**Recurrences: 4, all found by the tier-2 judge on one branch (2026-08-06).**

Four of the judge's eight evidence mismatches were comments and docstrings contradicted by the code
they annotate, written in the same commit:

1. `eval_service.py:18,199-200` still describes `branch_conn_str` as used by `write_eval_results` and
   `promote_to_verified_qa`, and scoring as running against the branch — the same phase removed the
   parameter from both functions and made scoring open no database at all.
2. `_fetch_eval_summary_sync`'s docstring asserts the `kind` filter means gate and console "can never
   disagree" — `_LIST_EVAL_RUNS_SQL` and `_LIST_EVAL_RUN_DATASETS_SQL` carry no `kind` filter, so two
   agents in one tenant DB read different runs entirely.
3. Migration `0015`'s docstring justifies itself by "unknown and pass render the same on screen" —
   true, and still true after the migration, because no frontend file is in the diff.
4. The `verified_qa` comment says red-team rows "never assert what the right answer is" —
   `red_team.py:398` writes an authored correct answer. The refusal to promote holds, but for a
   different reason than the comment a future reader would rely on.

**What the plans failed to anticipate:** a docstring is the artifact a future reader trusts *instead
of* reading the code, so a stale one is worse than none. Nothing in the gate checks prose against
behaviour, and reviewers reading a diff for defects read comments as intent rather than as claims.

**Standing rule:** when a change removes a parameter, a call, or a guarantee, grep the module's own
docstrings and comments for it in the same commit. A comment asserting a property is a claim and gets
the same scrutiny as a test.

## Family F — "over-broad mechanical gates produce false positives on their own prose"

**Recurrences: 3** (`22-04`, `22-05`, `23-09`).

Negative-assertion greps (e.g. "the committed diff may contain no line mentioning
`PLATFORM_CAPABILITY_DEFAULTS`") repeatedly tripped on the explanatory comment or docstring that was
written to *document* the rule. Each was resolved by rewording the prose, which is correct but
recurring.

**Standing rule:** scope a diff-content gate to code lines, or accept that the gate's own
documentation is part of its input and write the needle to be unique to the mechanism.

## Family G — "the guard was placed on one exit and the code found another"

**Recurrences: 2** (`a95b581`, `79601cd`), both on `chore/local-postgres`, 2026-08-10/11.

`a95b581` fixed three unbounded SSE consume loops by wrapping each in
`asyncio.timeout(SSE_STREAM_TIMEOUT_S)`, and its commit message names "a test that hung forever" as
the defect. Nine commits later, `79601cd` — the commit that rewrote the live-event test — introduced
`await emitter_task` **immediately after** that `async with` block, outside the bound. The emitter
parks on an `asyncio.Condition` predicate over the count of `event:` lines the server has written.
Any early close (a 401, a renamed auth header, a missing tenant row) makes `run()` return having
written too few lines; `run()` fires its final `notify_all()`, the predicate re-evaluates false, and
nothing will ever notify that Condition again. Measured: with the api key mutated to a bogus value
the test survived an external SIGKILL at 150s, five times the 30s bound it appeared to have.

The same shape had already appeared once in this family's ancestor: the bound was added to the
`aiter_lines()` loop because *that* was where the previous hang was observed, not because anyone had
enumerated the ways the test could block.

**What the plan failed to anticipate:** a timeout is not a property of a test, it is a property of
one `await`. Adding a second concurrent await to a bounded test silently creates a second, unbounded
exit — and it does so in the file whose docstring now says, in prose, that hanging is impossible
here. The prose is what the next reader checks.

**Standing rule:** a test that creates a task must bound *the task*, not only the thing the task
feeds. Every `create_task` gets an `await` inside the same `asyncio.timeout` and a `cancel()` in the
`finally`. And the proof that a bound exists is a mutation that forces the failure path and observes
the run terminate with a summary line — an external `timeout --signal=KILL` around the run, so a
hang reports as a distinguishable exit code rather than as patience.

## Family H — "the guard that cannot fail, written while fixing guards that cannot fail"

**Recurrences: 1** (2026-08-11, `test_worker_kill.py`).

While fixing a finding that `test_provision_neon_idempotency` was conditionally vacuous, I wrote an
assertion that the kill-9'd Celery message is still in kombu's `unacked` hash and described it, in
the test's own docstring, as "the direct observation of `acks_late=True`". It is a tautology. Flipping
`task_acks_late` to `False` in `celery_app.py` left it green (`1 passed in 63.89s`): on the `solo`
pool the ack — early or late — is flushed by a consumer loop that a SIGKILL'd worker never returns
to, so the Redis-side entry survives under both settings.

It was caught only because the repo's mutation rule was actually executed. Had the proof been
skipped, a false claim about the most safety-relevant setting in the worker would have shipped
inside a commit whose subject was honesty about test vacuity.

**What the plan failed to anticipate:** writing the guard and reasoning about the guard are the same
act, so the reasoning inherits the guard's blind spot. Confidence in a mechanism ("kombu removes the
entry on ack, so this discriminates") is not evidence about the configuration under test.

**Standing rule:** the mutation proof is not paperwork after the fact — it is what decides whether
the assertion ships. An assertion that survives its own mutation gets **deleted**, and the gap gets a
BACKLOG row (here, `1.12`), because a guard that has never been seen to fail is indistinguishable
from a comment.

## Family I — "the code was correct; the shape it was handed was never checked"

**Recurrences: 3** (2026-08-11 — `max_amount_cents`, the `ToolResultBlock` branch, and the
`tool_name` join underneath it).

Family A is about measurements that cannot fail. This is its neighbour and it is *not* the same
thing: here the logic is right, the tests are real, they exercise the logic, and they pass — but the
**data shape** flowing in from a boundary was decided by the test author rather than observed from
the boundary. Every test then agrees with the code because both were written from the same
assumption, and the assumption is the defect.

1. **`max_amount_cents` enforced nowhere** (`a180624`, earlier the same day).
   `apply_rate_and_constraint_checks` reads the amount with `getattr(args, "amount_cents", None)`.
   Its logic is correct. Both production call sites passed a plain `dict`, on which `getattr`
   returns the default, so the ceiling compared `None` and a refund of any size cleared its
   envelope. `test_capability_enforcement.py` drives the function with a `MagicMock`, whose
   attribute access always succeeds — so the one shape production actually passes was the one shape
   never tested.
2. **Tool results collected from the wrong message type** (`dc67d37`). `_run_sdk_turn` and
   `_build_transactional_probe_fn` read `ToolResultBlock` only inside `AssistantMessage`; the CLI
   emits tool results as `type:"user"`. Every unit test of that loop installs a fake
   `claude_agent_sdk` and hand-builds the stream, so the stream's shape was whatever the test
   assumed. Three downstream readers consumed a channel nothing ever wrote — including the Auditor,
   which judged **grounding** against an empty context on every turn the platform has ever run.
3. **The `tool_name` that could only ever be `"unknown"`** (same commit, stacked underneath #2).
   `getattr(block, "name", "unknown")` reads a field `ToolResultBlock` does not declare. Reachable
   or not, it could never produce a name, and `retrieval_eval` joins on `tool_name == "retrieve"`.
   **Fixing #2 alone would have emitted events that still joined to nothing** — which is why the two
   were mutation-proved separately rather than as one change.

**What the plans failed to anticipate:** a mock is a claim about a boundary, and nobody was required
to evidence that claim. Reviews checked the code against its tests and the tests against the code;
the loop closes without either being checked against the boundary. `2.13` was even *closed* on a
code reading in the P2 review — the capture it describes was correct and sat in an unreachable
branch, so the eval scored nothing rather than scoring a repr. Note that all three defects were
found by **running something that had never run**, not by reading: #1 by opening a skipped
integration test, #2 and #3 by settling a backlog row that asked for a static check.

**Standing rule:** when a test fabricates data that a third-party boundary produces at runtime, the
fixture must be built by the real producer or from a real captured sample — never hand-written from
what the code expects. Where the producer cannot be run (an external CLI), the shape must be
evidenced from real artifacts and the evidence recorded next to the code, with the residual gap
named. Here: the SDK's own transcript readers, plus 42,334 `tool_result` entries across 782 real CLI
session transcripts, all `type:"user"`, zero assistant-carried — and the residual gap (session JSONL
is not literally the stdout stream-json the SDK parses) is written down as `5.10` rather than
rounded off.

**Second standing rule:** `getattr(x, "name", default)` and `dict.get` with a default are how this
family hides. A default that is silently correct for the wrong input type turns a boundary mismatch
into a plausible value instead of an exception. Where a field is required, read it as an attribute
or a subscript and let it raise; where a default is genuinely wanted, log when it fires — the
`_run_sdk_turn.tool_result_unresolved` warning added in `dc67d37` exists for exactly this reason.

## Family I, recurrences 4-6 — `:param::type`, and a defect that looks correct on the page

**2026-08-12** (`deployment_service.py` ×2, `digest.py` ×1). Same family as the three from
2026-08-11: the logic is right, the tests are real, and the **shape handed to a boundary** was never
checked by that boundary. What these three add is a sharper version of *why* review does not catch
it.

SQLAlchemy's bindparam regex is `(?<![:\w\x5c]):(\w+)(?!:)`. The trailing lookahead exists to avoid
reading PostgreSQL's `::` cast as a parameter, and it makes `:window_days::text` bind a parameter
named **`window_day`** — greedy `\w+` backtracks one character to satisfy the lookahead. Not
unbound: *misnamed*. The value the call site passes matches nothing, the literal `:` reaches
Postgres, and the statement raises.

**So the defect is invisible to reading.** `":window_days::text"` with `{"window_days": 7}` beside
it is what a correct statement looks like. There is no missing argument, no typo, no shadowed name —
the only way to see it is to run the string through the parser that will actually consume it. Eight
instances across this repo's history (five in tests per `1.1`, three in production) is what that
invisibility costs.

The two failure directions are worth separating, because the quiet one is the expensive one:

- `deployment_service` failed **soft**. Caught, logged, fallback substituted — every
  `configured_max_*` / `observed_max_*` `None` while the thresholds beside them populated from
  settings. Phase 18's blast-radius warnings never evaluated real exposure, and the payload read
  like a tenant with no history. (`5.13` and `2.28` are the same shape; that is now three separate
  mechanisms whose broken state is spelled identically to their honest empty state.)
- `digest.py` failed **loud** and was still missed for months, because the raise happened inside a
  Celery task that retried and re-raised into a log nobody was reading. It was the WR-02 idempotency
  anchor, committed before the send, so `send_digest_email` was never reached: **OPS-04 has never
  sent a digest**, while `REQUIREMENTS.md` ticks it Complete.

**What the plans failed to anticipate:** "the tests pass" and "a database has ever parsed this
string" are independent facts, and only the second one is about the query. `test_digest_service.py`
has four tests; the one reaching the INSERT region seeds `fetchone` to return a row so the function
returns *early*, and `MagicMock.execute` accepts anything. The suite was green over a statement
Postgres rejects outright.

**Standing rule:** every raw SQL string in `app/` must be executed by a real server in at least one
test, or be covered by a static gate that parses it the way the driver will. Mocking the session is
fine for logic; it is not evidence about SQL. Where a real DB is unavailable, assert the *parsed*
form — `text(sql)._bindparams` against the parameter names the call site passes — which needs no
database and would have caught all eight of these.

**Second standing rule, from the gate rather than the defect:** a mechanical gate over source text
gets mutation-proved against its own false positives, not only its true ones. This one reported four
Redis key builders in `widget.py` on its first run, because dropping f-string `{interpolations}`
fused `f"rate:config:{ip}:{bucket}"` into `rate:config::`. That is Family F arriving inside the fix
for Family I, and the proof that the repair is load-bearing (M9) is what distinguishes a gate from a
guess.

## Family J — "the second layer is only visible from on top of the first"

**Recurrences: 4 in one day** (2026-08-12: `5.9`, `1.14`, `1.16`, `1.15`).

Not a defect family — a *diagnosis* family, and it changes how a fix should be reported. Every one of
these was filed as one narrow thing from reading the code, and every one was at least two things. In
three of the four, **fixing the first layer is what exposed the second**, because the first layer was
the reason nothing had ever executed far enough to meet it:

- `1.16` — the fixture wrote `NULL` into a `NOT NULL` column. Fixed, and the test then died on
  `provider.not_configured`: its docstring says it spies on `StubProviderAdapter`, but the only path
  returning the stub is a `red_team_mode` short-circuit the test never opens. The spy was
  unreachable, so the exactly-once assertion had nothing to assert against.
- `1.15` — the test predated the evidence gate, so the recommendation was `block` before *and* after
  containment. Seeded an eval run, and the approve call then reached guards `3b`/`4b` for the first
  time in the test's life, both being handed bare `MagicMock`s.
- `1.14` — filed as one call site in `deployment_service`; scanning for the *shape* found three, and
  the third (`digest.py`) meant OPS-04 has never sent a digest.
- `5.9` — filed against `red_team_probe`; the same dead branch was on the production turn path, with
  a second defect stacked under it (`getattr(block, "name", ...)`) that fixing the first would not
  have fixed.

**What the plans failed to anticipate:** a first green is not a finish when the code under repair had
never run. Each fix moved the failure rather than removing it, and each intermediate state was a
*new* error message that could easily be read as a regression the fix had introduced. Had `1.16`'s
NULL fix been committed and reported on its own, the next reader would have seen a test that used to
error in setup now failing an assertion — which looks exactly like a broken fix.

**Standing rule:** after fixing anything in code with no execution history, **re-run before reporting
done**, and expect a different error rather than a pass. Report the count of layers found, not the
count filed. When a row names one call site, grep for its shape before believing the number — the
scan is what turned `1.14` from one site into three, and a line-oriented grep was not enough (the AST
scan is what would have caught a literal split across concatenated fragments).

**Second standing rule, from two invalid proofs in two days:** write the expected direction of a
mutation down *before* running it. Both failures — the `\x00` heredoc escape, and `replace(..., 1)`
hitting the first of two identical lines in a different test — produced a green that was
indistinguishable from "the guard is a tautology". They were caught only because a red had been
predicted and the green demanded an explanation. A mutation that does not modify what it claims to
modify is not weak evidence; it is none.

## Family I, recurrence 7 — the fixture recreated a contract the product had abandoned

**Instance: `1.26` (2026-08-13), found by E2E-2 — the first time the ingestion chain was ever run.**

PROD-13 moved document bytes from local disk to S3. It migrated `parse_documents`. It did **not**
migrate `chunk_documents`, which kept a helper named — in its own docstring — *"Mirror of
parse_documents path-resolution"*, returning `UPLOADS_DIR/{agent_id}/{doc_id}{ext}`. It had been an
accurate mirror right up until the thing it mirrored moved.

**Consequence: ingestion has been broken for every uploaded file since PROD-13, in every
environment.** Not a local artefact — on Fargate `/vrd-uploads` is an empty container path. Only URL
sources, which re-fetch over HTTP in the `else` branch, could ever complete. Nothing in `app/` writes
a file to disk at all: zero `write_bytes`, zero `open(..., "wb")`, zero `shutil.copy`.

**Why this is Family I and not merely a missed call site.** The four tests in
`test_ingestion_chain.py` write their fixture to `gettempdir()/vrd-uploads/{agent_id}/{doc_id}{ext}`.
They do not mock the storage boundary — **they manufacture the local file that production stopped
creating**. Had those tests ever run (they never have, `4.4`), they would have gone green over a
product that could not ingest a single PDF. Family I has been "the code was correct; the shape it was
handed was never checked". This instance is one turn worse: *the code was wrong, and the fixture was
carefully maintaining the illusion that it was right.*

**What the plan failed to anticipate.** PROD-13 was scoped as "move upload and parse to S3", and both
were done well — `parse.py` even carries a comment explaining why bytes must come from S3 rather than
disk. What no phase asked was: **who else reads these bytes?** The migration was verified per-call-site
by the person who wrote it, and `chunk_documents` was not on that person's list. A grep for
`UPLOADS_DIR` at the end of PROD-13 would have returned exactly one live consumer and taken a minute.

**Standing rule added:** when a storage or transport boundary moves, the change is not complete until
a scan for the *old* accessor returns zero live consumers, and that scan lands as a test. Pinning the
new call site proves the site; pinning the absence of the old one proves the migration. That is why
`test_ingestion_reads_from_s3.py` is an AST scan over every pipeline module rather than an assertion
about `chunk.py`.

**Second observation, worth more than the fix:** three separate places derive the S3 key's extension,
and they did not agree — the writer lowercases it, `parse.py` did not (`1.27`). That was found only
because the `1.26` fix forced a comparison of all three. A key assembled independently at each end is
the same shape of defect as `1.14`'s misnamed bindparam: correct-looking at every individual site,
wrong only in the relationship between them.
