# Salvage or restart, measured 2026-08-22

Whether this repo can reach production readiness in place, or whether a rebuild to stricter
standards is cheaper. For whoever has to make or revisit that call. Every number below was
measured on `d99bad3` by read-only agents, and a second agent was briefed to refute the
first draft; the numbers that survived are the ones here.

## Verdict

**Refactor in place. The damage is real, measurable, and concentrated in one subsystem that
has never run end to end, so it can be cut without losing anything proven.** A rebuild
replaces about 146k lines to fix about 17k, and re-earns three flows that already work.

## What is proven to work

| Flow | Observed end to end | Where |
|---|---|---|
| signup to agent | yes, 2026-08-12, 12/12 assertions, live Neon | PRODUCTION-READINESS |
| ingest | yes, on MinIO, never on AWS S3 | PRODUCTION-READINESS |
| chat turn | yes, 2026-08-13, two live turns | PRODUCTION-READINESS |
| eval invoking the agent | never | PRODUCTION-READINESS |
| red team 7/7 with tools | never | PRODUCTION-READINESS |
| a `ship` verdict | never produced | PRODUCTION-READINESS |
| cloud deploy | never, Terraform never applied | PRODUCTION-READINESS |
| MCP provisioning | never built, 15/15 at zero | MASTERPLAN M7 |
| production traffic | zero, ever | PRODUCTION-READINESS |

Masterplan position: M0 and M3 done, M1 two thirds done and waiting on the owner to label a
calibration sheet, M2 and M4 to M7 not started. First commit 2026-05-12.

## Gates, observed 2026-08-22

`gates.py full` exit 0, "full gates passed in 1085.7s". Ruff 2 found against 2 pinned.
Import-linter 2 contracts kept. Lizard zero warnings at its floors. 2625 collected in 222s,
2612 passed, 13 skipped, 0 failed in 788s. Admin `tsc` 0 errors, `test:unit` 45 passed.
Widget 9471 bytes gzipped against a 20480 limit.

Green here means the code does what its tests say. The lizard floors
(`-C 35 -L 804 -Tnloc=545`) sit on the worst observed function, so the complexity gate only
stops things getting worse.

## Shape

| Measure | Value |
|---|---|
| `apps/api/app` | 135 files, 42,339 LOC |
| `apps/api/tests` | 198 files, 88,449 LOC, 2,375 tests. Ratio 2.09 : 1 |
| `apps/admin` | 14,163 LOC, 135 e2e tests, Gotham console |
| files over 950 LOC | 10, totalling 16,858 LOC (40% of app). Largest `eval_service.py` 2458, `deployment_service.py` 2087, `runtime/agent.py` 2041 |
| functions over CCN 15 or 100 lines | 75. Worst `_execute_transactional_tool` 804 lines, `run_eval_suite` 705, `run_agent_turn` 506 |
| domain layer | none. Routes call `db.execute(select(...))` inline and import Celery tasks directly. Tasks open raw `psycopg2` |
| typed core concepts | tenant, agent, job, deployment are pydantic. Chunk, verdict, retrieved context, eval result, tool result are `dict`. The ingestion chain passes an untyped `dict` between parse, chunk and embed |
| import cycles | 4 inside `app.services`, exempted from the acyclic contract |
| ADRs | 2, last touched 2026-06-29 |
| `.dev` | 21 plans, 44 traces, 44 reference notes, 7 workflows. BACKLOG 122 open rows |

## Test quality, sampled across agent, eval, calibration, retrieval, ingestion, tools, probe

| Style | Count in sample |
|---|---|
| asserts real behaviour on concrete input | 197 |
| asserts a mock was called | 56 |
| asserts a constant, a signature, or a string in source | 37 |

78 `inspect.getsource` sites in 30 test files pass if the behaviour is deleted. Example
`test_rrf_math_k60_formula` asserts `"60.0" in src`. 60 of the 76 blamable sites were
written in August. `test_validators.py` is wholly `xfail(strict=False)`.

Two areas are sound and lift as is: `tests/evals/calibration/compute_correlation.py`
(pure `spearman`, `matthews`, `ceiling_pairs_for`, 73 tests) and
`services/red_team_probe.py` (frozen dataclass, contextvar).

## The August window, 2026-08-01 to 08-22

| Measure | Before August | August |
|---|---|---|
| commits | 1,028 | 300 |
| feat : fix | 299 : 197 (0.66) | 37 : 92 (2.5) |
| docs-only commits | | 145 of 296 (49%) |
| backend lines last touched | | 13,508 of 42,339 (32%) |
| of those, inside the 10 god files | | 10,665 (79%) |

Where the bloat went, by blame: `decision_eval_service.py` 1897 of 1897 lines August
(created 08-05), `eval_service.py` 2122 of 2458, `runtime/eval.py` 419 lines on 07-31 and
1442 now, `api/v1/evals.py` 77%, `deployment_service.py` 67%, `runtime/agent.py` 56%.
`run_eval_suite` is 539 of 705 lines August; `_execute_transactional_tool` 437 of 804.
38 of the 92 August fixes rework an `app/` file an August feat touched; `(8.1)` alone
carries 14 commits.

The pre-August code has the same structural gaps (no domain layer, inline SQL, dict
passing) at a third of the function size, and it is the code behind the three observed
flows.

## Cost of each path

| | Refactor | Rebuild |
|---|---|---|
| app lines touched | at most 16,858 (the 10 god files) | 42,339 |
| test lines touched | the mock-heavy subset of 49,173 that import those files | 88,449 |
| admin | kept | 14,163 LOC rebuilt or re-coupled |
| migrations | kept | 36 rewritten |
| observed flows | kept | signup, ingest, chat re-earned from zero |
| total surface | about 17k app plus tests | about 146k |

The owner's previous rebuild (`sentinel-v2`, `../practice-audit/FINDINGS.md:108`) needed
five v1 features pointed at by hand in six days. A 146k-line reference repo makes that
worse, not better.

## The strangler path, which the code already supports

`pyproject.toml:112` enforces `api > worker > schemas > services > models | core | utils`.
A `domain` rung at the bottom is one line in that contract. Pure, app-import-free anchors
already exist and move first:

- `utils/pii_firewall.py` (`detect_pii`, `scan_response`, 229 LOC)
- `utils/chunk_id.py` (`deterministic_chunk_id`), `utils/context_frame.py`
- `transactional/registry.py`, `transactional/schemas.py`, `DecisionFixture` (frozen)
- pure functions inside god files: `derive_blast_radius_warnings` (deployment_service:1925),
  `classify_outcome`, `score_decision_run` (decision_eval:1312, 1436), `classify_severity`
  (red_team:96), `summarise_run_validity` (eval_service:1119), `rrf_fuse` (retrieval)
- `compute_correlation.py` math, `red_team_probe.py`

## What changes from the next commit, or this repeats

1. **The eval subsystem shrinks to what one `ship` verdict needs.** `eval_service.py`,
   `decision_eval_service.py`, `runtime/eval.py`, `api/v1/evals.py` (7,193 LOC, never
   invoked the agent) are cut to the path that runs one suite against the live agent and
   returns one verdict. The 2026-08-18 rule stands: a judge is calibrated against a human
   before it gates money. Machinery beyond that minimum goes.
2. **A domain type per core concept, in `app/domain/`, importing nothing from `services`,
   `worker`, `api`, `models`.** Chunk, Verdict, RetrievedContext, EvalResult, ToolResult.
   The ingestion chain passes `Chunk`, not `dict`.
3. **Lizard becomes a standard with a shrinking exemption list.** CCN 15, 60 lines. The 75
   current offenders go in an explicit list the gate refuses to grow.
4. **A gate that fails any test file containing `inspect.getsource`.** The 30 files go in
   the same kind of shrinking list.
5. **One artifact per decision.** An ADR in `docs/adr/` replaces plan plus trace plus
   reference note for design decisions. Workflows run for adversarial review only.
6. **M2 before any more M1.** The next code change is the first eval run that invokes the
   live agent. Calibration sheet size is the owner's call and does not block it.
