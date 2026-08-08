# TRACE — D1/P2 review fixes

**Branch:** `feat/d1-agent-invocation` · **Commits:** `b62186f`, `075550d` · **Plan:**
`.dev/plans/260807-d1-agent-invocation.md` (P2) · **Input:** the tier-2 read of P2 at `7a7486e` —
17 findings + 7 unsupported claims.

**Gate, observed (apps/api, the exact command in CLAUDE.md):**

```
before:  1795 passed, 11 skipped, 30 warnings in 370.91s (0:06:10)   # 7a7486e, the reviewer's own run
after:   1821 passed, 11 skipped, 30 warnings in 369.36s (0:06:09)
```

`+26`, `0 failed`. `mypy app` → `Success: no issues found in 132 source files`;
`uvx ruff@latest check app tests` → `All checks passed!` (ruff is **not** in `apps/api/.venv`).

Mutation proofs: `.dev/reference/p2-review-mutation-proofs.md` — 23 guards, each run red and green.

---

## The four that change what a run means

**A below-floor run no longer produces a shippable signal.** "Reports `unknown`, never `pass`" was
true of `config["agent_invocation"]["status"]` and `config["agent_invoked"]`, and nothing outside
`eval_service` reads either. Everything a consumer does read reported a pass: 2 surviving rows of a
2-of-40 run were scored, `write_eval_results` wrote them, `update_eval_run_status` marked the run
`complete`, and `_fetch_eval_summary_sync` built a non-empty `pass_rates` and returned
`EVAL_SIGNAL_MEASURED`. `run_eval_suite` now skips scoring entirely below the floor, so the gate
finds no `eval_results` and refuses with `EVAL_SIGNAL_NO_VALID_SCORES` — machinery that already
exists. **This is interim and says so** (BACKLOG `2.2`): a run produced by the pre-P2 tautology still
carries scores and no `agent_invoked`, which is still P3's job.

**The judge was being shown a repr.** `tool_calls_log[*]["result"]` is
`str(block.content)[:1800]` — a repr of `[{'type':'text','text': "<<<HEADER>>>\n[{'chunk_id': ...`
cut mid-structure, handed to Ragas as ONE element. Three failures at once: dict-syntax noise the
metric cannot distinguish from evidence; a cut below one full retrieval (5 chunks x up to 2000 chars
against an 1800 cap); and a single-element context list, which leaves ContextPrecision nothing to
rank. `agent.py` now decodes the framed payload into one untruncated string per chunk on
`RETRIEVE_CHUNKS_KEY`, which `_persist_messages` does not read — BACKLOG `2.13`'s own proposal,
closed. `result` is untouched, so the Auditor, the retrieval-faithfulness sampler and the chat path
are byte-for-byte.

**A run could not reach the bound it advertises.** Every run stamps `max_wall_clock_s = 5400`;
`visibility_timeout` was 3600 and the idempotency window 600. A run consuming its stated ceiling was
redelivered at 60 minutes and a second worker drove the same agent concurrently, with the guard in
place and unable to fire. `visibility_timeout` is 7200, pinned by a **relation** test rather than a
copied number (celery_app cannot import eval_service — ragas/instructor/anthropic at module scope, in
a module every task and the API process imports). The window is derived from the two bounds. And a
failure **after** the invocation no longer retries: `max_retries=2` meant one judge outage bought a
second and third full set of live SDK turns, which no field on the run expressed.

**Zero is not a low score, one metric over.** A responded turn with zero retrieve calls was scored
with `retrieved_contexts=[]`, so Faithfulness / ContextPrecision / ContextRecall were structurally 0
or NaN for an answer the agent gave correctly from its system prompt — and the gate's
"any pass_rate < 0.70" fires on it. Those rows are excluded and counted (`no_retrieval`), and
crucially **not** as failures: they do not depress `response_rate`, because an agent answering
"what are your opening hours?" without retrieving is behaving correctly.

## The floors, and the one that was argued away

`MIN_SCORED_OBSERVATIONS = 3` — compute_correlation.py's `MIN_PAIRS`, restored. The old comment
argued it away: "the denominator travels, so a consumer that wants at least N observations can apply
it to `responded`". No consumer does, and the only one that would (the deploy gate) reads
`agent_invoked`, which is computed here. A floor every consumer must remember to reapply is a floor
nobody has. It is applied to the rows that reached the **scorer**, which also closes the
38-no-retrieval-of-40 shape: perfect response rate, two scored rows, previously `measured`.

`coverage_rate = responded / valid` is reported beside `response_rate = responded / attempted`.
Nothing gates on it, deliberately: gating on coverage would permanently block every tenant above
`AGENT_INVOCATION_MAX_CALLS_PER_RUN` (BACKLOG `2.12`, `2.16`). The divergence from
compute_correlation's shape is now visible instead of silent.

## The rest

- **Deploy gate, in-flight run** — the selector took the newest `eval_runs` row with no status
  filter, so for the whole duration of a run the gate read a `running` row with no results and
  refused the deploy while a good completed run sat one row below. `status <> 'running'`, not an
  IN-list: a status this query has not heard of is still terminal.
- **`promote_to_verified_qa`** wrote `agent_response` while its trust gate inspected the scenario's
  `source` — the provenance of the **label**. Identical strings before P2, so the gate was right by
  accident; after P2 the day a `human_authored` source exists, the row served to a customer ahead of
  hybrid search would be the agent's own answer. It writes the label now, via `promotable_answer`,
  and refuses a row whose label is empty.
- **`run_eval_for_agent`** is a second door to `run_ragas_eval` that every P2 guard misses (they all
  read eval.py's AST or drive eval.py's loop). It refuses a prediction that is its own label, before
  a judge call is billed.
- **The side-effect sink** is emptied at the top of each loop iteration. It was reset only inside
  `build_agent_options`, and everything before that in `_run_one_eval_turn` can raise — so a scenario
  dying early re-read the previous scenario's sink and the run reported a capability attempt under an
  id that never made one. A fabricated observation in the exact confusion-matrix cell the recording
  exists to populate.

## Guards the review proved were not guards

- `stored_retrieved_contexts` is pinned on the **read**, not on the name. The reviewer ran the
  mutation the implementer did not: `contexts or scenario["stored_retrieved_contexts"]` left all 163
  tests green, because the name check inspects dicts carrying a `reference_answer` key and the scored
  row builds its fields with `**scenario`, and because every dynamic test supplied a non-empty
  retrieve result so the fallback never fired.
- `eval.py` may not name `run_agent_turn` at all. The side-effects guard enumerates
  `build_agent_options` call sites **inside eval.py** and is blind to any other route to a live turn.
- `EVAL_INVOKES_AGENT` is pinned to a call site again, both directions. It was flipped to `True` in
  the same commit that deleted its only pin.
- The eval's **import** of each turn bound is asserted. The old guard read agent.py only; a local
  `AGENT_TURN_TIMEOUT_S = 90` in eval.py left it green, and the provenance test compared 90 to 90.
- `emit()` is **run** through `_EvalEventSink` rather than poked method by method. The property was
  held by an undocumented coupling: a `db.flush()` added to `emit` would make all sixty turns raise,
  the run report `unknown`, and the eval silently stop measuring.
- The one-copy guard matches **bound-consuming syntax** (a slice upper, a `timeout=` keyword) rather
  than a bare integer, so an unrelated future `90` does not fail it for the wrong reason.

## The mutation that did not go red first time

`at-cap-measured-against-the-audit-capture` passed 2/2 against both cap tests as first written.
Neither fixture separated the two caps: for a single short chunk the audit repr is short too, and for
a 2000-char chunk both caps trip. `075550d` adds the production shape — three 700-char chunks, whose
repr exceeds the 1800 audit cap while every chunk is whole — and the mutation goes red. Recorded
rather than quietly fixed: it is the same defect class as `7a7486e`'s self-caught tautology, twice on
one branch, and the lesson is that a cap guard needs the case where the two caps disagree.

## What is NOT proven

- **No end-to-end eval run.** No PostgreSQL on this machine; every `-m integration` harness skips,
  and a skip is unobserved, never a pass. The metric still has not been observed to move.
- **The new `agent.py` decode has never seen a real SDK `ToolResultBlock`.** It is exercised against
  the exact payload `agent_tools.retrieve_tool` constructs (`_frame_retrieved_context(str(chunks))`)
  and against the three content shapes the SDK can hand back, but the SDK is not installed in a form
  these tests drive. If a chunk dict ever carries a non-literal value, `ast.literal_eval` refuses and
  the turn is counted as `retrieved_context_unparsed` — fail-closed, and visible on the run rather
  than silent, which is why the count exists.
- **The `status <> 'running'` filter has never executed against a database** (same standing debt as
  BACKLOG `2.14`, `3.5`). It is asserted on the SQL text.
- **`MIN_SCORED_OBSERVATIONS = 3` is a judgement, not a measurement.** It is MIN_PAIRS' value and
  MIN_PAIRS' argument; nothing here establishes that 3 is the right floor for this metric family.

## Gate

```
1820 passed, 11 skipped, 26 warnings in 371.38s (0:06:11)     # b62186f
1821 passed, 11 skipped, 30 warnings in 369.36s (0:06:09)     # final tree
```

`+26` over the reviewer's reproduced 1795, `0 failed`. Two `wired`-style fixtures moved from 2 rows
to 4, because a two-row run is now correctly below the absolute floor and would have put every test
in those modules on the fail-closed branch.

The warning count moves between runs (26 here, 30 there, 28 and 30 on earlier P2 runs) — it is the
pre-existing "coroutine … was never awaited" family, attributed to whichever line happens to trigger
GC. Nothing in this diff raises a new warning class.

**Process note, again.** A background gate run was started and then `mypy` was run alongside it; on
4 GB the pytest process took 41 s of CPU in 13 minutes of wall clock and was still at 62%. It was
killed and the gate re-run alone, on the final tree. The earlier warning in
`260808-d1-p2-invoke.md` was about *editing* during a run; this is the other half — do not run a
second heavy toolchain alongside it either. The line above is from a run with nothing else going.
