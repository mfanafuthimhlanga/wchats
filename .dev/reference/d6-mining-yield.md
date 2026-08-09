# D6 — how many rows would the labelling queue actually contain?

**Date:** 2026-08-09 · **Branch:** `feat/d6-labelling-loop` at `d0a3b4e` (off `feat/d1-agent-invocation`)
**Question asked by the owner before any P4 console work starts.**
**Answers `BACKLOG 4.10`, which said "zero is a plausible reading". It is not plausible. It is
established, and the mechanism is not the one 4.10 names.**

## What I did and did not do

I ran **nothing**. No gate run, no test run, no database touched. There is no PostgreSQL on this
machine and `CONTROL_DB_URL` points at live Neon production, which I did not query. Every claim below
is a static read of the code and the migrations at `d0a3b4e`, with `file:line` for each. Where I have
inferred a runtime behaviour rather than observed it, I say so in that sentence.

---

## Answer, up front

Split the queue by producer, because the queue is **source-agnostic** — `evals.py:797` selects
`WHERE NOT (reference_answer != '')` across all of `eval_scenarios`, not `WHERE source='mined'`:

| producer | rows it contributes today |
|---|---|
| `mine_production_scenarios` (`source='mined'`) | **exactly 0, provably, from the schema** |
| `bench.promote_trace_to_scenario` (`source='production'`) | `F` — one per trace the owner graded `filed`. Unknown; plausibly 0. **This path works.** |
| `red_team.py` containment (`source='red_team'`) | **0** — it writes a non-empty answer, so those rows are *labelled* and never enter the queue |
| `generate_eval_suite_for_agent` (`source='generated'`) | `G` — only when Haiku returns an empty `reference_answer`. Unknown, expected small, and each one is a generation defect |

**`queue_depth = 0 + F + G`, where both unknowns are plausibly zero.**

And the mined component is zero **not** because the miner `continue`s past jobs with no
`conversation_id`. That `continue` (`scenario_service.py:444-446`) **is unreachable.** The statement
two steps before it names a column that does not exist in the control DB, so the miner **raises** on
the first flagged row and `run_eval_suite` swallows the exception as a warning. The plan, the
docstring and `BACKLOG 4.10` all describe a graceful skip. What actually happens is an aborted
function.

---

## 1. Every condition, in order, for one flagged event to become one queued row

### Chain A — mining. The queue's nominal producer.

#### C1 — `run_eval_suite` must reach the mining block

```python
# apps/api/app/worker/tasks/runtime/eval.py:938-950
    # Mine new production scenarios and store them
    try:
        with get_sync_db() as control_db:
            mined = mine_production_scenarios(agent_id, conn_str, control_db)
        if mined:
            store_scenarios(mined, conn_str)
    except Exception as mine_exc:
        # Mining is best-effort — never blocks the eval run
        log.warning("run_eval_suite.mine_failed", agent_id=agent_id, error=str(mine_exc))
```

Three entry points reach it: the nightly beat (`celery_app.py:207-211`,
`crontab(hour=2, minute=0)` → `run_eval_suite_beat`), the manual trigger route
(`evals.py:676`), and `deployment_service`'s day-1 first-eval dispatch.

Two early returns can skip it: the concurrent-run idempotency guard
(`eval.py:788-793`, returns `{"status": "already_running"}`) and a scenario-fetch failure
(`eval.py:884-892`, returns or retries). Neither is the usual case.

**Established?** The beat *entry* exists in config. Whether a `celery beat` process is running in
production is not something the repo can establish, and I did not check the deployed host.

#### C2 — at least one flagged judge event, for this agent, inside the window

```sql
-- apps/api/app/services/scenario_service.py:387-399
SELECT DISTINCT je.job_id AS job_id, je.payload->>'verdict' AS verdict
FROM job_events je
WHERE je.event_type IN ('gatekeeper.complete', 'auditor.complete')
  AND je.payload->>'agent_id' = :agent_id
  AND je.payload->>'verdict' IN ('fail', 'ungrounded', 'partial')
  AND je.created_at > NOW() - make_interval(hours => :hours)   -- 168h default
```

The producers are real and durable:

- `agent.py:1428-1431` chains `run_gatekeeper` → `run_auditor` after **every** agent turn.
- `validators.py:207-213` and `validators.py:350-356` call
  `emit(job_id, "<judge>.complete", {**verdict.model_dump(), "agent_id": agent_id}, db, _redis)`.
- `events.py:89-95` inserts the `job_events` row and commits — durable, not just Redis.
- The verdict vocabularies overlap the filter: `validation_service.py:42`
  (`pass|fail|needs_clarification`) and `:63` (`grounded|ungrounded|partial`). So `fail`,
  `ungrounded` and `partial` are all reachable values.

**What makes it true or false in production:** the presence of chat traffic that the judges flag.
Unknowable from here — it is a data question about a database I must not touch.

**If C2 is empty**, the miner returns `[]` at `scenario_service.py:401-408` with
`reason="no_flagged_events"`. Yield 0, no error, and this is the *benign* zero.

Worth naming in passing: `needs_clarification` is not in the `IN` list. An entire gatekeeper failure
mode is excluded from mining by omission, with no comment saying it was a choice.

#### C3 — the miner must recover a `conversation_id` from the control DB `jobs` table

```python
# apps/api/app/services/scenario_service.py:422-431 (the SQL string itself is line 424)
        job_row = control_db.execute(
            text("SELECT conversation_id FROM jobs WHERE id = :job_id LIMIT 1"),
            {"job_id": job_id},
        ).fetchone()

        conversation_id = None
        if job_row and getattr(job_row, "conversation_id", None):
            conversation_id = str(job_row.conversation_id)
```

**This condition is FALSE, always, and it fails loudly rather than quietly.**

The task asked me to answer the schema-and-writer question from the code. Both halves:

**The schema half — the column does not exist.**

```sql
-- apps/api/alembic/versions/0001_control_db_initial.py:66-77
CREATE TABLE jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    agent_id    UUID REFERENCES agents(id),
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

Nine columns. No `conversation_id`. No later control migration adds one:
`grep -rn "ALTER TABLE jobs" alembic/versions/` returns **no matches**, and the only other
occurrences of `jobs` across all 19 control migrations are the `job_events` FK
(`0001:86`) and the agent index (`0001:78`). The ORM agrees: `app/models/job.py:17-43` declares the
same nine columns. The two `conversation_id` hits elsewhere in the control migrations are a comment
(`0008:10`) and `tool_calls_audit` (`0014:69`) — a different table.

**The writer half — nothing populates it either.** Every `Job(...)` construction in the codebase
passes exactly `tenant_id`, `agent_id`, `kind`, `status`:
`agents.py:76`, `agent_chat.py:145`, `documents.py:234`, `query.py:95`, `widget.py:403`.
So even if the column were added tomorrow it would be NULL on every row until a writer changed too.
Note also that on the turn paths `body.conversation_id` is legitimately `None` for a first turn — the
conversation row is created downstream inside `run_agent_turn` — so "put it on the job" is not a
one-line fix at those call sites either.

**This is not a new finding.** It was recorded during Phase 21 and deliberately routed around:

- `.planning/phases/21-…/21-RESEARCH.md:274-275` — *"Pitfall 5: `Job` (control DB) has no
  `conversation_id` column — do not rely on it … `scenario_service.mine_production_scenarios`
  (line 362) queries `SELECT conversation_id FROM jobs WHERE id = :job_id` — this column does not
  exist … The query either errors or (if the DB has a stray column from manual DDL) silently returns
  unrelated data."*
- `21-05-PLAN.md:63` explicitly instructed the implementer to reuse the miner's cross-DB pattern
  *"AND its broken `SELECT conversation_id FROM jobs` fallback which must NOT be copied"*.
- `bench_service.py:17-22`, shipped, says the same in its module docstring.

The sibling was built correctly and the miner was left as it was. **It has been broken since before
Phase 21 and nothing in this repo has ever executed it against a database** (see §3).

**Runtime consequence (inferred from the schema, not observed).** `psycopg2` raises
`errors.UndefinedColumn`; SQLAlchemy wraps it as `exc.ProgrammingError`; it propagates out of
`mine_production_scenarios` at line 422 on the **first** flagged row, and `eval.py:944-950` catches
it and logs `run_eval_suite.mine_failed` at **warning** level. The eval run then proceeds normally.
Nothing in the run report, the queue counts, or the deploy gate says mining produced nothing because
it could not run.

**So C4-C7 below are never evaluated.** They are enumerated because they are the cost of a repair,
not because they gate anything today.

#### C4 — `job_row.conversation_id` must be non-NULL (`scenario_service.py:430`)
Would require both a column and a writer. Neither exists (C3).

#### C5 — the tenant `messages` table must hold a `role='user'` row for that conversation

```python
# apps/api/app/services/scenario_service.py:435-438
            messages = _fetch_messages_for_conversation(tenant_conn_str, conversation_id)
            user_messages = [m["content"] for m in messages if m["role"] == "user"]
            question = user_messages[0] if user_messages else ""
```

This one *would* hold — `agent.py:454-471` writes the user and assistant rows back-to-back in one
transaction. **But `user_messages[0]` is the first user turn of the entire conversation, not the turn
that was flagged.** On any conversation past turn 1 a repaired miner would attach the wrong question
to the failure. `bench_service._fetch_customer_turn` (`bench_service.py:100-139`) solves exactly this
by walking to the `assistant` message whose content matches the flagged agent turn. The miner does
not.

#### C6 — the recovered question must be non-empty (`scenario_service.py:444-446`)
The `continue` the plan and `BACKLOG 4.10` attribute the zero yield to. Unreachable (C3).

#### C7 — the insert must land

```python
# apps/api/app/services/scenario_service.py:151-168
                    INSERT INTO eval_scenarios
                      (id, source, question, reference_answer, retrieved_contexts,
                       scenario_category, created_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, NOW())
                    ON CONFLICT DO NOTHING
                    ...
                        str(s.get("id") or uuid.uuid4()),
```

Two things here that matter for any future yield estimate:

1. **`ON CONFLICT DO NOTHING` can never fire.** `eval_scenarios`' only unique constraint is the
   primary key (`alembic_tenant/0005:77-88`; `0011` adds `provenance`/`origin_trace_id` columns and a
   plain index, no unique index). The PK is a **fresh `uuid4()`** at line 160. So the clause has
   nothing to conflict on. The docstring at `scenario_service.py:130` — *"Idempotent via ON CONFLICT
   DO NOTHING"* — is not true for this call path.
   **Consequence in the counterfactual where C3 is fixed:** the lookback is 168 hours and the beat is
   nightly, so the same production failure is re-mined and re-inserted on up to **7 consecutive
   nights**. Queue depth would be roughly 7× the distinct-failure count, and the owner would be asked
   to label the same question seven times.
2. **No join key is written.** The insert carries no `job_id`, no `conversation_id`, and not even the
   `origin_trace_id` column that `0011` added and `insert_provenance_scenario` populates for the
   other two paths. That is what makes the duplication un-dedupable, and it is the same absence
   `BACKLOG 6.4` records as the reason the queue cannot be ordered by judge confidence.

### Chain B — the producers that are not mining

The queue does not filter by source, so these land in the same list:

```sql
-- apps/api/app/api/v1/evals.py:797-813
    FROM eval_scenarios
    WHERE NOT (reference_answer != '')
```

**B1 — `production`, from a filed trace. The one producer that works.**
`traces.py:158-167`: grading a trace `filed` dispatches `promote_trace_to_scenario`.
`bench.py:173-186` recovers `conversation_id` from the job's **own `agent.response` event payload**
— which `agent.py:1358-1369` does put there — and then `_fetch_customer_turn`. The row is inserted at
`bench.py:214-222` with `reference_answer=NO_GROUND_TRUTH` (`bench.py:94`, `= ""`), so it is
**unlabelled and lands in the queue.**
Yield = the number of traces the owner has filed in the ops room. That is an owner-behaviour number,
not a code number, and nothing in the repo establishes it is above zero.
One caveat: if `conversation_id` is absent or the fetch raises (`bench.py:183-190`), `question` stays
`""` and the insert at `:214` proceeds anyway — this path can file a queue row with an empty question.

**B2 — `red_team`. Contributes nothing to the queue.**
`red_team.py:464-472` inserts with `reference_answer=_SAFE_SCENARIO_REFERENCE_ANSWER`
(`red_team.py:411-415`), a non-empty constant. Non-empty ⇒ already eligible to the eval selector ⇒
`WHERE NOT (reference_answer != '')` excludes it. Contained critical findings never reach the owner's
queue. That is correct behaviour, but it does mean the D6 plan's line *"contained red-team findings
are all stored and never scored"* is wrong on this one source: they **are** scored.

**B3 — `generated`, occasionally.** `SCENARIO_TOOL` (`scenario_service.py:44`) requires the
`reference_answer` key but sets no `minLength`, and `store_scenarios` defaults a missing key to `""`
(`:163`). A Haiku batch that returns an empty answer string produces an unlabelled `generated` row.
Rare, unquantifiable from here, and each one is a generation defect rather than a production failure.

---

## 2. The yield, as a conjunction of unknowns

```
mined_rows      = 0
                  — unconditional, given C3. The only escape is a live control DB carrying a
                    `jobs.conversation_id` column that no migration in this repo creates and no
                    writer populates. Nothing establishes that, and if it were true, alembic
                    autogenerate would report schema drift against app/models/job.py.

queue_depth     = 0  +  F  +  G

  F = (traces the owner graded 'filed')
      × P(the trace's job has an `agent.response` job_events row)     — else bench.py:176 returns early
      × 1                                                            — the insert itself is unguarded

  G = (Haiku scenario batches that returned an empty reference_answer)
```

`F` and `G` are both **unknown and plausibly zero**: `F` is zero for any tenant whose owner has never
used the ops-room bench, `G` is zero for any tenant whose generation runs behaved.

**So: plausibly zero overall, and the mined component is not "plausibly" anything — it is zero.**

The number `BACKLOG 4.10` wanted to measure did not need a database. It needed the migration
directory. What still needs a database is `F` — and `F` is a question about the owner's behaviour, not
about the miner.

---

## 3. Why this survived: nothing has ever executed the statement

- **No unit test covers `mine_production_scenarios`.** `tests/unit/test_scenario_service.py` has two
  classes — `TestGenerateScenariosFromChunks` (:31) and `TestStoreScenarios` (:145). Neither touches
  the miner.
- **Every test that would otherwise reach it stubs it out:**
  `test_eval_task.py:164`, `test_eval_agent_invocation.py:1551`, `test_label_downstream.py:247` all
  do `monkeypatch.setattr(mod, "mine_production_scenarios", lambda *a, **kw: [])`.
- **No integration test either**, and every `-m integration` harness skips here for want of a
  PostgreSQL server (`BACKLOG 0.2`), so a skip would have been unobserved even if one existed.
- Phase 21 verified the *sibling* — `21-05-SUMMARY.md:61` records
  `grep -c "FROM jobs" bench_service.py == 0` as a shipped check. **The same check was never pointed
  at `scenario_service.py`**, where it would return 1 and be a real hit rather than a docstring.

---

## 4. What measurement would settle it, and what it needs

Two different questions; only one of them is behind `BACKLOG 0.2`.

**M1 — "does mining yield anything?" Settled by this document; strengthen it with two cheap tests.**
The answer is a static fact about the control schema. To convert it from prose into something a
future change cannot silently un-fix, without any database:

1. A **guard test** asserting `inspect.getsource(mine_production_scenarios)` does not name the `jobs`
   table — the mirror of the `grep -c "FROM jobs" == 0` check Phase 21 applied to `bench_service` and
   never applied here. Mutate it (re-add the query), observe red, restore, observe green.
2. A **behaviour test** driving the miner against a fake `control_db` whose `execute` raises
   `sqlalchemy.exc.ProgrammingError` on the `jobs` statement, pinning that the miner **raises** rather
   than returning `[]` — i.e. pinning that the failure is loud, which today it is not, because
   `eval.py:946` demotes it to a warning.

Neither test observes the real `UndefinedColumn`. Only `0.2` can.

**M2 — "how deep is the queue for a real tenant?" Behind `BACKLOG 0.2`.**
The instrument already exists: `GET /agents/{id}/eval-scenarios/unlabelled`
(`evals.py:1016-1091`) returns `counts.unlabelled` over `counts.total` from
`_QUEUE_COUNTS_SQL` (`evals.py:876`). Note the pre-0016 path (`evals.py:893`) is the one **every**
tenant is on — 0016 has been applied nowhere — so `human_labelled` comes back `null` beside
`label_provenance_available: false`, which is the honest shape.
What it needs: a local PostgreSQL with the control schema at `0019` and a tenant schema at `0016`,
seeded with N flagged `job_events` and M filed traces; run the beat path; read the counts. That is
`0.2` and it is blocked. It cannot be done on this machine, and `CONTROL_DB_URL` is live production,
which is not a substitute.

**M3 — the cheapest way to size the prize, if `0.2` stays blocked.** One control-DB-only read, no
tenant join, no code:

```sql
SELECT payload->>'agent_id' AS agent_id, COUNT(DISTINCT job_id) AS flagged_jobs
FROM job_events
WHERE event_type IN ('gatekeeper.complete','auditor.complete')
  AND payload->>'verdict' IN ('fail','ungrounded','partial')
  AND created_at > NOW() - INTERVAL '168 hours'
GROUP BY 1 ORDER BY 2 DESC;
```

That is the **upper bound on the mined component** — what a repaired miner could see per night, per
agent, before any of C4-C7 costs anything. If it returns zero rows, repairing the miner is worth
nothing today and the whole labelling loop is waiting on traffic, not on code.
**I did not run it.** The only control DB reachable from here is live production. It belongs in a
read-only query the owner runs, or in the `0.2` harness once it exists.

---

## 5. Recommendation on P4 (the console labelling queue)

**Do not build it yet.** Three reasons, ranked:

1. **A console built now would tell the owner something false.** Its nominal producer contributes
   zero rows, and an empty list reads as "there are no failures to label." There *are* failures —
   they are sitting in `job_events`, flagged, with their `conversation_id` recoverable from the
   `agent.response` payload. The miner just cannot reach them. An empty screen would hide a defect
   behind a plausible-looking absence, which is the exact failure mode
   `.dev/reference/measurement-layer-audit.md` exists to prevent.
2. **The one working producer already has a console.** The ops-room bench lists failing traces and
   grading one `filed` already files an unlabelled `production` row. If the goal is "get human labels
   into the system", the shortest path is bench → file → label, and it is worth asking whether the
   bench can carry the label field itself before a second queue screen exists at all. That is a
   design question P4 should answer before it is built, not after.
3. **The miner repair is small, and doing it first means P4 is built against a queue that can be
   non-empty.** Concretely: replace `scenario_service.py:422-442` with the `agent.response`-payload
   lookup that `bench.py:173-186` already performs (and reuse `bench_service._fetch_customer_turn`,
   which picks the *flagged* turn rather than `user_messages[0]`); carry `origin_trace_id=job_id`
   through `store_scenarios` so the `ON CONFLICT` clause at `:157` has a key; add the unique index
   that makes it fire. Roughly 30 lines plus one tenant migration — which **cannot be applied on this
   machine**, so it ships unapplied like `0016`.

**Suggested order instead of P4 next:**
(a) the two guard tests from M1 — cheap, no database, and they stop the defect being re-lost;
(b) the M3 control-DB count, run by the owner against production, to size the prize;
(c) the miner repair as its own small branch, if (b) is non-zero;
(d) revisit P4 with a real number in hand.

If (b) comes back zero, the finding is that **the labelling loop is waiting on production traffic,
not on a queue UI** — and that is worth knowing before a console is built for it.

---

## 6. Corrections this document makes to existing records

| record | says | actually |
|---|---|---|
| `.dev/plans/260808-d6-labelling-loop.md:117-121` (Risks) | mining `continue`s past jobs without `conversation_id`; "a queue with nothing in it is a plausible outcome" | the `continue` is unreachable; the miner **raises** two statements earlier. Empty is not plausible, it is certain |
| `BACKLOG 4.10` | "zero is a plausible reading" | zero is established. The row can be closed as measured, and replaced by a row for the miner repair |
| `scenario_service.py:130` docstring | "Idempotent via ON CONFLICT DO NOTHING" | not idempotent on this path — fresh `uuid4()` PK, no other unique constraint |
| `scenario_service.py:352-368` docstring | describes a "fallback to fetching it via job → conversation linkage where available" | there is no such linkage in the schema; the fallback is a query against a column that does not exist |
| D6 plan §"What D6 actually is" | "mined production failures, owner-filed failing traces and **contained red-team findings** are all stored and never scored" | red-team rows carry a non-empty reference answer (`red_team.py:411-415`) and **are** scored |
| `.dev/reference/d6-p2-labelling-queue.md:74` | the miner "recovers the question from tenant `messages` via `jobs.conversation_id`" | it cannot — the column does not exist; the statement raises |
