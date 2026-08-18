# 8.2a · judge-temperature-zero

**Goal:** every call that produces a VERDICT sends `temperature=0`. Every call that produces
CONTENT does not, and a test says which is which.

**Why it is ahead of the capture:** a judge sampling at the provider default poisons every number
downstream of it, including the very first calibration run. `8.2`'s other two halves (chance
correction, intervals) are entangled with `8.1` step 4 and can follow. This one cannot.

**Measured 2026-08-18:** `grep -rn "temperature" app tests/evals` returns nothing. Not one call site
sets it, so every judge in this system samples at whatever the provider defaults to, including the
Actor gate that runs synchronously before money moves.

## The line, and it is the point of the change

| Call | Produces | Temperature |
|---|---|---|
| `validation_service.call_gatekeeper` / `call_auditor` / `call_strategist` | a verdict | 0 |
| `actor_seam` forced-tool call | a verdict, before money moves | 0 |
| `red_team_service.classify_severity` | a label | 0 |
| `eval_service._build_instructor_llm` (Ragas) | four scores | 0 |
| `retrieval_eval._build_instructor_llm` (Ragas faithfulness) | a score | 0 |
| `tests/evals/judge.py` | the eval verdict | 0 |
| `strategy_service.run_strategist` | a verdict on a corpus | 0 |
| `scenario_service.generate_scenarios_from_chunks` | eval scenarios | **left alone** |
| `red_team.py` probe | attack messages | **left alone** |
| `retrieval_service._expand_query` | query variants | **left alone** |

The last three want variety. Twenty red-team probes at temperature 0 are one probe run twenty times,
and a scenario generator at 0 returns the same five scenarios from every corpus. Determinism is what
a judge wants and the opposite of what a generator wants, so the split is stated in a test rather
than left to whoever edits a call site next.

**Expect 3 to 8 percent verdict variance to survive temperature 0** (batching and hardware
nondeterminism). That is why a high-stakes verdict eventually wants more than one sample; it is not a
reason to skip the setting, and it is recorded here so a later flake is not read as a regression.

## Files

- `apps/api/app/services/validation_service.py` — three call sites.
- `apps/api/app/services/actor_seam.py` — one.
- `apps/api/app/services/red_team_service.py` — `classify_severity` only.
- `apps/api/app/services/strategy_service.py` — one.
- `apps/api/app/services/eval_service.py` — `InstructorLLM(**kwargs)`, the seam its docstring already
  documents for `thinking`.
- `apps/api/app/worker/tasks/runtime/retrieval_eval.py` — the same seam.
- `apps/api/tests/evals/judge.py` — one.
- `apps/api/tests/unit/test_judge_temperature.py` — new.

## Tests

Modelled on `test_judges_disable_thinking.py`, which pins a cross-cutting provider parameter by
**asserting on the kwargs the client receives, never on the source text**. A source-shaped guard bans
one spelling while the author picks the spelling.

1. Each verdict call site sends `temperature=0`, parametrised, one id per judge.
2. `scenario_service` and the red-team probe send NO temperature, so a later blanket edit that makes
   every generator deterministic fails a test instead of quietly halving the corpus's variety.
3. The Ragas `InstructorLLM` carries `temperature=0` in the kwargs that reach `model_args`.

**Mutation proof:** drop `temperature=0` from one judge, observe that judge's parametrised case go
red, restore from `HEAD`, observe green. Record the observed output.

## Exit

- `apps/api` `scripts/gates.py full` green.
- `grep -rn "temperature" app tests/evals` returns a line for every row in the table above marked 0,
  and no line for the three marked left alone.
