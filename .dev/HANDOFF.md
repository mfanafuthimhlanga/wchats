# HANDOFF — 2026-08-12

> # START HERE — 2026-08-12, later session. E2E-0 IS DONE. NEXT IS E2E-1.
>
> ## The one instruction
>
> **Continue `.dev/PRODUCTION-READINESS.md` §4 at E2E-1.** E2E-0 closed this session; everything the
> block below says about the plan still holds, including "do Phase A before anything cloud".
>
> ## What E2E-0 did, and the correction it carries
>
> `1.21` said "`.env.example` omits 5 of the 10 required settings". **It was counting one of two
> tracked example files**, and the other failed differently:
>
> ```
> root      .env.example   missing 5
> apps/api/ .env.example   missing 3 -- and TWO of the three were COMMENTED OUT, not absent
> ```
>
> **A commented key is worse than an absent one**: the reader sees `# JWT_SECRET=` in the file while
> dotenv ignores it, so the `ValidationError` looks like an application bug. **Which file loads is
> positional** — `_find_env_file()` walks up from `app/core/config.py` and stops at the first `.env`,
> so a fresh clone reads the **root** file and `apps/api/.env` wins permanently once it exists. Both
> examples therefore had to be complete; both now are.
>
> Pinned by `apps/api/tests/unit/test_env_example_covers_required_settings.py`, which derives the
> required set from `Settings.model_fields` rather than a copied list — a copied list is `1.14`'s
> failure mode exactly. **Four mutation proofs, red then green, restore from `HEAD`:**
> `.dev/reference/260812-e2e0-mutation-proofs.md`. M4 is the one to read — with the required set
> forced empty, both coverage tests **passed vacuously**, which is what the guard-of-guard is for.
>
> Commit `2482283`. Trace `.dev/traces/260812-e2e0-boot-contract.md`. Plan
> `.dev/plans/260812-e2e0-boot-contract.md`. `1.21` struck through; `PRODUCTION-READINESS` §3.2 and
> the §4 E2E-0 line both updated.
>
> ## What E2E-0 does NOT establish — do not read the closure as more than it is
>
> **Nothing has booted the app.** The test asserts key *names* are present and uncommented. No API
> process, no worker, no alembic command has been started from an example-derived environment, and
> the four secret-generation commands printed in the examples were never run. **E2E-1 is the first
> step that would show any of that.**
>
> ## Environment change made this session, because it will otherwise confuse the next one
>
> The owner's **global** `~/.claude/settings.json` denied `Read(.env.*)`, which also blocked the
> committed, secret-free `.env.example` files. The owner confirmed the rule targets real secrets, so
> **the deny was narrowed** to `.env`, `.env.local`, `.env.*.local`, and
> `.env.{dev,development,prod,production,staging,test}`. `.env.example` / `.env.sample` /
> `.env.template` / `apps/admin/.env.local.example` are now readable; real env files still are not.
>
> ## Gates
>
> Backend unit gate re-run at `2482283`, **observed, not relayed**:
>
> ```
> 2206 passed, 13 skipped, 0 failed, 28 warnings in 409.27s   exit 0
> grep -cE "^(FAILED|ERROR)"  ->  0
> ```
>
> **The arithmetic is exact: 2202 → 2206 is the 4 new tests and nothing else**, so no pre-existing
> test changed status. Skips unchanged at 13.
>
> **Do not read 409s against the previous 545s as a speed-up.** Two data points, different machine
> load; this file already records one wall-clock "regression" that was diagnosed from two points and
> did not exist.
>
> Nothing else was touched — no frontend file changed, so the admin and widget gates are unaffected
> and were **not** re-run (`test:e2e` is still 7 failed / 128 passed per `1.19`, unchanged and
> untouched). The two integration failures below (`5.6`, `ver01`) are unchanged and still external.

> **`.dev/BACKLOG.md` is the single ordered list of open work.** Read it before starting anything.
> This file is the current-state snapshot; that one is the queue.
>
> **Backlog numbers are addresses, not priorities.** Every row now carries a slug —
> `5.1 · ops15-server-gap`. Use slugs in conversation; the numbers exist only because source comments
> cite them (`# BACKLOG 2.1`) and cannot be renumbered. `.dev/BACKLOG.md`'s "How to read the numbers"
> block says where the sections mislead.

> # START HERE — 2026-08-12. NEXT SESSION RUNS END-TO-END VALIDATION.
>
> ## The one instruction
>
> **Read `.dev/PRODUCTION-READINESS.md` and execute its §4 plan, starting at E2E-0.** It is the gap
> register and the ordered validation plan, and every claim in it is marked `OBSERVED` / `READ` /
> `RECORD` so you know what has actually been run versus what is inherited from these notes.
>
> **Do Phase A (E2E-0 … E2E-5) before anything cloud.** It runs entirely against the local
> PostgreSQL and Redis that already exist. The reasoning is in the plan and it is the lesson of this
> whole week: every defect found was found by running something that had never run, and Phase A is
> the first time this system would be exercised *as a system*. Standing the AWS stack up first moves
> that discovery into an environment that bills by the hour.
>
> ## The state in one paragraph
>
> The platform is substantially built and **has never been run against reality** — no deployed
> environment, no customer turn, no calibrated judge, no completed eval, no real red-team run, zero
> production traffic. The engineering gap is small. **The evidence gap is the entire product**,
> because "defensible" is what is being sold. Terraform for a full AWS Fargate stack exists and has
> **never been applied** (no state file, no `~/.aws`). `.env.example` omits 5 of the 10 mandatory
> settings, so a fresh environment cannot boot. Clerk is on development keys.
>
> ## Gates, all run by this session and observed
>
> ```
> backend unit                  2202 passed,  13 skipped, 0 failed   545s
> backend integration flag-OFF    15 passed,  47 skipped, 0 failed   281s
> backend integration flag-ON     40 passed,  24 skipped, 2 failed   473s
> admin tsc                      1 error — the ONE CLAUDE.md documents; zero new
> admin check:no-dusk-tokens     PASS
> admin check:ops-room-wiring    PASS
> admin test:unit                45 passed
> widget build + size            PASS — 8968 bytes gzipped (ceiling 20480)
> admin test:e2e                 7 FAILED, 128 passed — 35.9 min.  NOT GREEN.
> ```
>
> Both integration failures are external, not code: `5.6 · tightened-ceiling-audit-row` (owner
> decision) and `ver01` (needs a real `ANTHROPIC_API_KEY` in `os.environ`, not merely `.env`).
>
> **The e2e line is new information and CLAUDE.md is wrong about it.** CLAUDE.md documents this gate
> as "113 across three viewports"; Playwright reports **135 tests**, and 7 fail. All 7 are
> **timeouts** (`90000ms exceeded` on `page.goto` / `waitForLoadState('networkidle')`), not assertion
> failures — the pages never finish loading, so nothing is actually asserted. The same log is full of
> Clerk development-instance errors (`Failed to load Clerk JS`, renderer not mounting, strict usage
> limits). **Cause is NOT established and there is no prior local baseline** — the gate has never run
> in CI either. Treat 128/7 as the first measurement, not a regression, and **settle the Clerk keys
> before debugging the seven** — this is the likeliest way to burn a day on an environment artifact
> that looks like a product bug. Detail: `PRODUCTION-READINESS.md` §3.8.
>
> ## What this session changed
>
> Four defects fixed, all found by running things that had never run, all mutation-proved:
>
> - **`5.9`** — tool results arrive on `UserMessage`, not `AssistantMessage`. Both consumers read the
>   wrong one, so **the grounding judge received an empty context on every turn since 2026-05-16**
>   and the eval excluded every row as `no_retrieval`. Also `5.8`, the RTX identity probe reporting a
>   blocked attack as SUCCEEDED.
> - **`1.14 · paramstyle-collision`** — `:param::type` silently binds a *truncated* name. Three live
>   sites; the third means **OPS-04 has never sent a digest**. Now gated by an AST scan of `app/`.
> - **`1.15`, `1.16`** — two integration tests that had never executed, each with a second blocker
>   underneath the first. OPS-15's end-to-end claim is observed for the first time.
>
> **Retro families `I` and `J` were added and they are the transferable part.** `I`: a mock is a
> claim about a boundary that nobody was required to evidence. `J`: the second layer is only visible
> from on top of the first — four rows in one day were each filed as one thing and were each at
> least two, so **after fixing anything in code with no execution history, re-run before reporting
> done, and expect a different error rather than a pass.**
>
> ## Two corrections this session made to its own claims
>
> Recorded because the pattern matters more than the instances: both were notes-quoted-as-observation.
>
> 1. "Nothing is deployed and there is no deploy path" — **a full Terraform stack exists**; it has
>    never been *applied*.
> 2. "The digest will send real email to real recipients from a scheduled beat" — **no beat worker
>    exists**, the recipient is a single `OWNER_EMAIL`, and SMTP is unset. Comparing it to `0.4`
>    (customer data egressing to a judge API) was wrong.
>
> ## What is genuinely waiting on the owner
>
> `0.1 · score-judge-calibration` (nothing in §3.4 of the readiness doc moves without it),
> `0.3 · actions-billing-cap`, `0.4 · eval-pii-egress`, `0.6 · size-labelling-loop`,
> `0.7 · model-provider-decision`, `5.6 · tightened-ceiling-audit-row`. Plus, for Phase C: an AWS
> account with billing, a domain, two ACM certs, and Bedrock access.

> # SUPERSEDED — 2026-08-11 session boundary. Kept for the defect detail it carries.

> ## The 2026-08-11 session boundary (was START HERE; superseded by the block above)
>
> ## Where the code is
>
> **`chore/local-postgres`, tip `5102ddf`, clean tree, UNMERGED.** 32 commits ahead of `main`
> (`main` is at `57be16b` and already carries D1 + D6). **All three gates run by the session and
> observed, not relayed from an agent:**
>
> ```
> unit                          2202 passed, 13 skipped, 0 failed    545s   (2026-08-12, tip d839100)
> integration (flag OFF, gate)    15 passed, 47 skipped, 0 failed    281s   (2026-08-11)
> integration (flag ON)           40 passed, 24 skipped, 2 failed    473s   (2026-08-12, tip d839100)
> ```
>
> **Flag-ON went `33/24/5` → `40/24/2` across 2026-08-12** as `1.14`, `1.15` and `1.16` closed.
> **Both remaining failures are external to the code and neither is fixable by an agent:**
>
> | Failure | What it is |
> |---|---|
> | `test_act07_resolve_live::test_tightened_ceiling…` | **`5.6`** — an audit-provenance **owner decision**. Writing `actor_decision='approved_by_human'` on a denial row would make `pending_confirmations.py:172`'s query start matching denied actions, so it is not free. |
> | `test_ver01_adversarial_harness::test_100_adversarial…` | needs a real `ANTHROPIC_API_KEY` **in `os.environ`**, not merely in `.env`. Passed on 2026-08-11 when one was exported (~$0.024). |
>
> That is the floor for this machine without an owner decision and a credential. The intermediate
> reading below is kept because it names what each failure was:
>
> | Failure | What it is |
> |---|---|
> | `test_act07_resolve_live::test_tightened_ceiling…` | **`5.6`** — the owner's audit-provenance decision. |
> | ~~`test_deploy_gate_redteam::…`~~ | **`1.15` — CLOSED later the same day** (`d839100`). |
> | `test_ver01_adversarial_harness::test_100_adversarial…` | needs `ANTHROPIC_API_KEY` in `os.environ`. |
>
> **After `1.15` closed, the expected flag-ON state is 2 failures, both external to the code:** `5.6`
> (an owner decision) and `ver01` (a credential). The final gate run is recorded at the bottom of
> this block.
>
> **Read the flag-ON line carefully — 5 failed, and NONE of them are this change.** It is also not
> comparable to the morning's `28 passed / 5 skipped / 1 failed`: that run collected 34 tests, this
> one collects 62 (the whole of `tests/integration`, nothing deselected). Three of the five had
> **never executed before**, which is the entire reason they now show. Attribution, each verified:
>
> | Failure | What it is |
> |---|---|
> | `test_act07_resolve_live::test_tightened_ceiling…` | **`5.6`** — the owner's audit-provenance decision. Known, expected. |
> | `test_ver01_adversarial_harness::test_100_adversarial…` | needs `ANTHROPIC_API_KEY` in `os.environ`. Environmental, documented below. |
> | `test_deploy_gate_redteam::test_deploy_gate_blocks_then_unblocks_on_contain` | **`1.15`** — stale against D1/P3's evidence gate; `eval_signal=no_runs` downgrades to `block`, so its second assertion cannot hold. Gate behaving as designed. |
> | `test_transactional_idempotency_e2e` ×2 | **`1.16`** — fixture inserts `NULL` into `capability_envelopes.constraints`, a `NOT NULL` column. |
>
> **The bottom three were proved pre-existing rather than assumed so**: `git checkout 4621cdd --
> apps/api/app/`, re-run, `3 failed in 36.58s` — identical — then `git checkout HEAD -- apps/api/app/`
> and a clean `git status`.
>
> **`1.14` is now CLOSED (`c65137e`, 2026-08-12) and it was three sites, not one.** Scanning for the
> *class* rather than the instance found `digest.py:87` carrying the same defect, and that one is the
> bigger find: the INSERT is the WR-02 idempotency anchor committed *before* the send, so it raised,
> retried 3×, re-raised, and **`send_digest_email` has never been reached — OPS-04 has never sent a
> digest**, while `REQUIREMENTS.md` ticks it Phase 21 Complete.
>
> The row's diagnosis was also corrected in the fixing: SQLAlchemy does not leave the parameter
> unbound, it **silently binds a truncated name** (`:window_days::text` → `window_day`,
> `:payload::jsonb` → `payloa`). A misnamed-but-present parameter is precisely why five phases of
> review read past it — the string looks correct on the page. The durable half of the fix is a gate:
> `tests/unit/test_sql_paramstyle_collisions.py` (AST scan of every string literal under `app/`, plus
> characterization tests pinning the truncation) and `tests/integration/test_paramstyle_real_db.py`
> (a real server parses the statements). **Two consequences are filed as owner-facing: `1.17`** — the
> digest task will now send real email from a scheduled beat, the same dormant-path-becomes-live shape
> as `0.4` — and **`1.18`**, blast-radius warnings firing for the first time.
>
> **`1.15` and `1.16` also closed on 2026-08-12** (`a5e4101`, `d839100`), and both were two-layered
> in the same way. `1.16`'s `NULL`-into-`NOT NULL` fixture fix exposed a second blocker: the test's
> docstring says it spies on `StubProviderAdapter`, but the only path returning the stub is a
> `red_team_mode` short-circuit the test never opens, so the spy was unreachable and the exactly-once
> assertion had nothing to assert against. `1.15`'s eval-run seed exposed approve-route guards `3b`
> and `4b`, which no run of that test had ever reached because the recommendation was always `block`.
> **OPS-15's end-to-end claim — open critical → `block` → 422, contain → `ship` → 200 — is now
> observed for the first time.**
>
> **The day's shape, and it is the practical lesson: four rows (`5.9`, `1.14`, `1.16`, `1.15`) were
> each filed as one narrow thing and each was at least two.** In three of the four, fixing the first
> layer is what exposed the second — because the first layer was the reason nothing had ever executed
> far enough to meet it. So: **after fixing anything in code with no execution history, re-run before
> reporting done, and expect a different error rather than a pass.** Filed as retro **Family J**.
>
> Traces: `.dev/traces/260812-paramstyle-collision-class.md`. Proofs:
> `.dev/reference/260812-paramstyle-mutation-proofs.md` (M7-M13, including two invalid proofs
> recorded rather than quietly redone). Original entry below.
>
> **And that run found a live product defect nobody had seen: `1.14`.** Every
> `run_deployment_checklist` logs `blast_radius_fetch_failed … syntax error at or near ":"`.
> `deployment_service.py:1237`/`:1253` write `(:window_days::text || ' days')::interval` — SQLAlchemy
> leaves the bindparam unbound where `::` abuts it (`window_days` is absent from the bound parameters
> in the error), Postgres rejects it, the caller catches and falls back. **So every `configured_max_*`
> and `observed_max_*` in the blast-radius payload is `None` on every run, while the thresholds
> beside them populate from settings and look healthy.** The Phase 18 blast-radius warnings have
> never evaluated real exposure. Sixth instance of the `:name::type` class `1.1` records — the first
> in production code rather than a test.
>
> Unit is +26 on the morning's 2167/13, all of them new tests in this change; zero `FAILED`/`ERROR`
> lines (`grep -cE "^(FAILED|ERROR)"` → 0). Flag-OFF's skip count moved 22 → 47 because
> `red_team_rtx` is no longer deselected — those tests now *skip* under the OFF flag instead of being
> excluded from collection, which is a more honest reading of the same state.
>
> The flag-ON failure to expect is **`BACKLOG 5.6`** — an audit-provenance decision for the owner,
> not a regression. See the `1.13` section below for what flag-ON means and how to run it.
>
> **The "not one defect was in the product" line that stood here all morning is no longer true, and
> that is the day's headline.** `git diff --stat 3e7fb8e..HEAD -- apps/api/app/` is now **five**
> files: `config.py` (`hide_input_in_errors`), `transactional/tools.py`,
> `transactional/confirmation_resolution.py`, `worker/tasks/runtime/agent.py` and
> `services/red_team_probe.py` — **two live product defects, both found only by running things that
> had never run.** One moves money (`a180624`); the other means the grounding judge has never seen
> its evidence (`dc67d37`), and it has been that way since 2026-05-16.
>
> ## The measurement defect (`dc67d37`) — the day's second, and the larger of the two
>
> **Tool results arrive on `UserMessage`. Both consumers read them only from `AssistantMessage`.**
> `_run_sdk_turn` (`agent.py`) and `_build_transactional_probe_fn` (`red_team_probe.py`) each had a
> `ToolResultBlock` branch nested under `isinstance(msg, AssistantMessage)`. The CLI emits tool
> results as `type:"user"`, so **both branches were unreachable** and three downstream readers were
> consuming a channel nothing ever wrote:
>
> - `agent.tool_result` job_events — never emitted, so `retrieval_eval._fetch_turn_context` always
>   built `[]`, `citation_coverage` was always `None`, and `run_retrieval_faithfulness` returned
>   `no_signal` on every turn (`5.13`).
> - `tc["result"]` — never set, so **the Auditor, the grounding judge, received
>   `retrieved_context_json == "[]"` on every turn the platform has ever run** (`5.11`).
> - `RETRIEVE_CHUNKS_KEY` — never set, so `eval.py` saw zero chunks and excluded every row as
>   `no_retrieval`. D1/P2's untruncated-chunk capture, closed as `2.13` on a code reading, was inert
>   from the day it landed.
>
> **Age: `git log -S` returns ONE commit — `2b38648`, 2026-05-16, the original `run_agent_turn`.**
> Born dead, never executed once, through ~3 months, 23 phases, a seven-defect measurement audit and
> two tier-2 judgements. Live on `main` (`57be16b`) right now.
>
> **A second defect was stacked underneath, and fixing the message type alone would NOT have fixed
> it.** The handler read `getattr(block, "name", "unknown")`, but `ToolResultBlock` declares only
> `tool_use_id` / `content` / `is_error`. `"unknown"` was the only value it could produce, and
> `retrieval_eval.py:194` joins on `tool_name == "retrieve"`. Both consumers now resolve the name by
> joining `tool_use_id` back to the `ToolUseBlock` — which also fixes mis-attribution under parallel
> tool calls, which `red_team_probe`'s single `pending_skill` variable got wrong by construction.
>
> **Settled for free, before touching code**, as `5.9` asked: (a) the SDK's own transcript readers
> treat `tool_result` as a user-entry phenomenon (`_internal/sessions.py:277-280`,
> `_internal/session_summary.py:81-92`); (b) **42,334** `tool_result` entries across **782** real CLI
> session transcripts on this machine are all `type:"user"`, **zero** assistant-carried; (c) the
> Messages API shape. Six mutation proofs, all red first time —
> `.dev/reference/260811-tool-result-mutation-proofs.md`.
>
> **Why nothing caught it:** every unit test of that loop installs a fake `claude_agent_sdk` and
> hand-builds the message stream, so the stream's shape was whatever the test assumed — the same
> assumption the code made. That is a new retro family (**Family I**, "the code was correct; the
> shape it was handed was never checked") and `a180624` below is its first member.
>
> **What is still unproven:** no test observes the stdout stream-json the SDK actually parses. Filed
> as `5.10`, ~$0.12, and now worth buying.
>
> ## The security defect (`a180624`) — read this before touching enforcement
>
> **`max_amount_cents` was enforced nowhere.** `apply_rate_and_constraint_checks` reads the amount via
> `getattr(args, "amount_cents", None)` and its docstring says *"args: Validated Pydantic input
> model"* — but **both** production call sites passed a plain dict (`tools.py` step 4 passed
> `raw_args`, typed `raw_args: dict`; `confirmation_resolution.py` step 4 passed the stored JSONB).
> `getattr` on a dict returns the default, so `amount` was unconditionally `None` and the comparison
> at `enforcement.py:370` could never be true. **A refund of any size cleared its envelope's value
> bound** and went on to the Actor gate and the adapter. Both sites now pass `validated`.
>
> Mutation-proved: reverting `tools.py` alone turns
> `TestMaxAmountCentsIsEnforcedByTheDispatcher::test_over_ceiling_amount_is_denied_before_the_adapter`
> red with `transactional_tool.success` in the log; restored, 95 pass.
>
> Why nothing caught it: `test_capability_enforcement.py` drives that function with a `MagicMock`,
> whose attribute access succeeds. The dict shape production actually passes was never tested.
>
> **This also corrected `5.6`'s diagnosis.** The earlier claim — "the execution path never re-reads
> the envelope, it trusts the frozen approval" — was **wrong**. It re-reads the live snapshot
> correctly, exactly as its comment says, then compared it against a dict.
>
> ## The environment is real now — do not re-derive this
>
> - **PostgreSQL 17.6 as a Windows service** `postgresql-17-local`, `localhost:5432`, survives reboot.
>   Binaries `C:\Users\Bantu\pgsql`, cluster `C:\Users\Bantu\pgdata`. **`fsync=off` — disposable test
>   infrastructure, never real data.**
> - **pgvector 0.8.1**, built from official source with the MSVC toolset already on the box. The
>   Windows SDK came from NuGet (`Microsoft.Windows.SDK.CPP` + `.x64`, plain zips, no admin) —
>   the VS Installer route is dead here because the Build Tools install is orphaned (`vswhere
>   -products *` returns nothing).
> - **Redis** already installed, `PONG` on 6379.
> - **Both migration chains applied:** control `0019 (head)`, tenant probe `0016 (head)`, `0016`
>   roundtrip exercised both ways. `BACKLOG 0.2` and `3.5` are closed.
> - **The Neon account is EMPTY** — the owner authorised deleting all 8 projects (each ~30 MB,
>   schema-only). `neon-baseline.txt` is deliberately empty; full quota free. `NEON_API_KEY` in
>   `.env` is real and works.
> - **Run integration with** `INTEGRATION_DB_URL=postgresql://wchats:wchats@localhost:5432/wchats_control`
>   and `REDIS_URL=redis://localhost:6379/0`.
> - **Never run the two suites concurrently** — 4 GB box; contention manufactured 2 phantom errors
>   and tripled the unit suite's wall clock (977s vs 394s).
>
> ## The open discussion: DeepSeek / OpenAI-compatible models
>
> The owner has DeepSeek credits and no Claude credits, and asked what it would take to run on
> DeepSeek. **Investigated, not decided.** What was established:
>
> - **Two integration surfaces, counted:** `claude_agent_sdk` in **7** source files (`agent.py`,
>   `agent_tools.py`, `transactional/tools.py`, `red_team_probe.py`, `red_team_service.py`,
>   `deployment_service.py`, `eval.py`); the direct `anthropic` SDK in **11** (judges, Actor seam,
>   eval, scenario, strategy, metadata, retrieval, validation).
> - **`claude_agent_sdk` ships a bundled `_bundled/claude.exe`.** The only hits for
>   `ANTHROPIC_BASE_URL` / `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX` are *inside that
>   binary*, which was not readable. The knobs exist; their semantics were **not** verified.
> - **The load-bearing distinction: base URL is not wire format.** Bedrock and Vertex work because
>   they serve the Anthropic Messages API shape. DeepSeek is OpenAI-compatible — a different schema.
>   So the Agent SDK needs a **translating proxy** (Messages API in, Chat Completions out), and the
>   hard part is tool-use translation, because the entire capability envelope rides on
>   `mcp__customer-tools__*` tool calls.
> - **The 11 direct-API files are the tractable half** — DeepSeek has an OpenAI-compatible endpoint,
>   so they are a provider-adapter swap. That is also where most call volume lives (judges,
>   `classify_severity`, scenario generation, the Actor gate). **Ragas is easiest** — takes any
>   LangChain-wrapped LLM.
>
> **VERIFIED 2026-08-11 — the proxy conclusion holds, and the cost is higher than recorded.** Full
> writeup: `.dev/reference/260811-agent-sdk-provider-surface.md`. What settled it:
>
> - **The env knobs are real and the SDK honours them.** Not inferred — `subprocess_cli.py:430`
>   inherits the whole process env into `_bundled/claude.exe` and merges `options.env` over it, so
>   every provider variable documented for the Claude Code CLI applies to `claude_agent_sdk`.
> - **Exactly three wire formats exist**, all Anthropic-shaped: Anthropic Messages
>   (`ANTHROPIC_BASE_URL`), Bedrock InvokeModel, Vertex rawPredict. Foundry and Claude Platform on
>   AWS implement the Messages format. **OpenAI Chat Completions is not among them**, so the "base
>   URL is not wire format" distinction was right.
> - **The new fact the last session did not have: Anthropic states it "doesn't support routing Claude
>   Code to non-Claude models through any gateway."** That turns "hard to build" into "unsupported
>   even if built" — a different decision.
> - **Tool-use translation is one of six hard requirements, not the only one.** The forwarding
>   contract is *deliberately open-ended* ("treat the headers and body fields as open lists"), so the
>   proxy must track Claude Code releases forever; beta-header/body pairs must travel together or
>   400; streaming is mandatory with a 300s byte watchdog that counts SSE pings; errors must pass
>   through unmodified or the client's retry path breaks; and unknown model aliases still get
>   `thinking: {"type": "adaptive"}` posted to them.
>
> **Net:** the 7/11 file counts stand, but the scoring changes. The 11 direct-`anthropic` files are
> still an ordinary provider-adapter swap and still hold most of the call volume. The 7 Agent-SDK
> files are a standing maintenance commitment against a configuration Anthropic does not support.
> Filed as `BACKLOG 0.7` — owner decision, not agent work.
>
> ## Next moves, in order
>
> 1. **`0.6`** — one `count(*)` on the control DB over `gatekeeper.complete`/`auditor.complete` with
>    verdict in (`fail`,`ungrounded`,`partial`). Owner-run, against production. Decides whether the
>    labelling loop is waiting on code or on traffic, and therefore whether `2.28` (the miner repair)
>    and P4 (the console queue) are worth building at all. Cheapest high-leverage item in the queue.
>    **Read `5.11` first:** every `auditor.complete` verdict in that table was produced against an
>    empty retrieved context, so the counts describe a judge that could not see its evidence.
> 2. **`5.10` — the ~$0.12 that is now worth spending, and was not this morning.** One live
>    `test_confused_deputy` turn. Everything in `dc67d37` is verified against the message shape
>    observed in 42,334 real CLI transcript entries, but **no test observes the stdout stream-json
>    the SDK actually parses** — every one constructs SDK dataclasses directly. That is the single
>    gap the static evidence cannot close. Before the fix the same $0.12 could only have bought a
>    vacuous pass.
> 3. **`0.1`** — now genuinely unblocked: a tenant DB exists, so `capture_responses.py` can finally
>    produce the transcripts the owner would score. Judge calibration is downstream of it.
> 4. **`0.4`** — before anything runs nightly. `eval-nightly` fires at 02:00 UTC and full Neon quota
>    is free, so the first beat worker against production sends customer rows to the Ragas judge with
>    `pii_firewall_applied=False`.
>
> ~~`5.9` / `5.8`~~ — **both closed 2026-08-11** (`dc67d37`, `5102ddf`). `5.9` was settled
> statically and for free exactly as the row asked, and it was **larger than filed**: the same dead
> branch is on the production customer turn path. See the section below.
>
> ~~`1.14`~~ — **closed 2026-08-12** (`c65137e`). Also larger than filed: three sites, not one, and
> the third means the weekly digest has never sent. **Its two consequences, `1.17` and `1.18`, are
> owner-facing and should be read before any beat worker runs this code** — `1.17` in particular is
> the same shape as `0.4`.
>
> **A pattern worth naming, because it has now happened three times in two days.** `5.9`, `1.14` and
> `2.13` were each filed as one narrow thing and each turned out to be a class. In all three the
> filed row was written from *reading* the code and the real extent only appeared when something
> *ran* — a skipped test, a deselected suite, a scan for the shape rather than the instance. The
> practical form: when a row names one call site, grep for its shape before believing the count.
>
> ## `1.13` — LARGELY CLOSED 2026-08-11. How to run the opened suite
>
> Integration went **`15 passed / 22 skipped` → `28 passed / 5 skipped / 1 failed`**. Six modules
> opened, every one of which had never executed: `act07` (2/3), `ver01` (1), `prompt_versions` (6),
> `integration_e2e` (2), `aud03` (1), `agent_chat` (2).
>
> **To run flag-ON**, set `INTEGRATION_TESTS_ENABLED=1` alongside the two URLs above **and export
> `ANTHROPIC_API_KEY` into `os.environ`** — `.env` is not enough. Pydantic loads `.env` into
> `Settings`; `actor_seam.py:38` builds `anthropic.Anthropic()` off `os.environ`, so `ver01` fails
> `401 invalid x-api-key` rather than skipping. Cost of a full flag-ON pass is ~$0.024 (30 Actor-gate
> calls in `ver01`; everything else stubs or patches its model calls).
>
> **The 5 remaining skips are a hard floor, not neglect:** `stripe_live` ×2 (Stripe test credentials),
> `ingestion_e2e` ×2 (live ingestion APIs), `test_ingestion_chain` ×1 (docling `pipeline` extra).
> ~~`red_team_rtx` ×3 is deselected pending `5.8`/`5.9`.~~ **`red_team_rtx` RUNS as of 2026-08-11:
> `1 passed, 3 skipped, 0 failed`** (`1.13c`). Getting there needed the module's `clean_tenant` to be
> fixed — it had the *identical* `get_sync_db`-bound-above-`_control_db_redirected` bug fixed in
> `ver01`, which `1.13b` predicted here and marked unverified. Now verified, fixed, and it was the
> **last** instance. Its 3 remaining skips are all "needs a real key in `os.environ`", and two of
> them are newly-discovered facts rather than known ones: **`test_identity_bypass` and
> `test_value_bound_evasion` both make live model calls**, because everything past the IDV gate hits
> the Actor gate. The module docstring claimed the former needed no Anthropic API; it 401'd the first
> time it ever ran. Same shape as `ver01`'s "every mutating call dies at the IDV gate" — a confident
> claim from a method that could not have checked it, on a test that had never executed.
>
> **What opening them found is the point, not the count:** the `a180624` security defect above; two
> live model-call paths open in the chat tests (unpatched Haiku judges at `agent.py:1428`, and a real
> `run_agent_turn` parked on the Redis `runtime` queue for whatever worker drained it next — that bill
> lands detached from the test run); two outright tautologies, one of which closes `4.2`; a third
> instance of `4.6`'s ContextVar bleed; and rows `5.7`/`5.8`/`5.9`.
>
> **Two method lessons worth carrying**, both the same shape — a confident claim from a method that
> could not have detected what it ruled out. (a) `ver01`'s docstring argued every mutating call dies
> at the IDV gate; the gate is conditional on a per-envelope flag set only on `issue_refund`, so it
> covered 20 of 104. (b) An agent reported "the control chain has zero foreign keys — checked by
> compiling the statement, no DB"; `agents.tenant_id` carries a FK and every insert raised
> `ForeignKeyViolation`. Statement compilation cannot see a constraint.
>
> Trace: `.dev/traces/260811-integration-skip-inventory.md`.
>
> ## Two things worth carrying forward about how this went
>
> - **A mock is a claim about a boundary, and nothing required anyone to evidence it.** That is the
>   whole of `dc67d37` and of `a180624`, and it is now `retro.md` **Family I**. Reviews checked the
>   code against its tests and the tests against the code; the loop closes without either being
>   checked against the boundary. New rule: a fixture standing in for a third-party boundary must be
>   built by the real producer or from a real captured sample. The new stream tests build their
>   retrieve payload with the production framer for exactly this reason.
> - **`getattr(x, "name", default)` is how that family hides.** A default that is silently plausible
>   for the wrong input type converts a boundary mismatch into a value instead of an exception —
>   `"unknown"` here, `None` in the `max_amount_cents` case. Both defects were one raised
>   `AttributeError` away from being found in May.
> - **The full gate earned its runtime.** `test_agent_options_seam`'s nested-def guard caught the
>   first shape of this fix. Every module the change touched was green; the guard that failed lives
>   in a module the change does not touch, so no "related modules" selection would have run it.
> - **Three of the thirteen defects were recorded somewhere as already fixed.** Unobserved is not
>   passing — the principle this repo writes down for metrics, holding identically for its own test
>   suite. **`2.13` is now a fourth**: closed 2026-08-08 on a code reading, and the code it described
>   was correct and unreachable.
> - **The session made three errors worth knowing about**, all corrected in the record: tier-1
>   reviews were labelled tier-2 twice; a suite figure was reported as "still running" when the run
>   had been killed; and a wall-clock regression was diagnosed from two data points and did not
>   exist. The pattern is inference stated at the confidence of observation — the same weakness the
>   D1 tier-2 judge named in the branch it was reviewing.

> # SUPERSEDED — historical, kept for the Neon-boundary detail. Was "IN FLIGHT", tip `d4f65e2`
>
> **Its gate figures and tip are stale** — see START HERE above for current. Its "the branch changed
> only tests, docs and one config line" claim is also no longer true: `a180624` changed enforcement.
> Still load-bearing below: the Neon-account history and the `nightly.yml` / `test_worker_kill`
> destroying-or-leaking analysis, neither of which anything later revisited.
>
> **The environment is real now, and both gates are green on it.** PostgreSQL 17.6 with pgvector
> 0.8.1 on `localhost:5432`, Redis on `:6379`, control DB at `0019`, a tenant probe DB at `0016`.
> `BACKLOG 0.2` is closed and `3.5` with it.
>
> **Measured 2026-08-11, on this tree, by running them:**
>
> ```
> integration  15 passed, 22 skipped, 24 deselected in 109.31s   (0 failed, 0 errors)
> unit       2164 passed, 13 skipped, 30 warnings in 397.83s     (0 failed)
> ```
>
> Run integration with `INTEGRATION_DB_URL=postgresql://wchats:wchats@localhost:5432/wchats_control`
> and `REDIS_URL=redis://localhost:6379/0`. The unit command is the one in CLAUDE.md, unchanged.
>
> **The branch changed only tests, docs and one config line.** `git diff --stat 3e7fb8e..d4f65e2 --
> apps/api/app/` is a single line: `hide_input_in_errors=True` on `Settings`. Every one of the
> thirteen defects fixed here was in the test suite, not the product.
>
> **Two things were destroying-or-leaking shaped, and both are closed:**
> - `nightly.yml` reclaimed Neon projects by **listing the account and matching names**
>   (`vrd-*` + `e2e`). It was simultaneously dead — `_project_slug` never emits that prefix — and
>   able to delete anything. Now an id-scoped ledger; nothing is listed, nothing matched by name.
> - `test_worker_kill.py` was the last un-stubbed provisioning dispatch, one exported
>   `NEON_API_KEY` away from creating real billable projects with no teardown. Ported onto the
>   in-worker Neon stub, and it **runs green for the first time in repo history** (62s).
>
> **Neon account: EMPTY as of 2026-08-11.** During the work it held 8 baseline projects, verified
> present before and after every phase — nothing created, nothing leaked, nothing destroyed by any
> agent. **The owner then authorised deleting all 8** ("earlier test work, not important"); evidence
> supported it, every one was ~30 MB, Neon's floor for a schema with no meaningful data. All 8
> deleted by id (never by name pattern), `Veridian` / `dark-snow-18891572` included.
> `C:/Users/Bantu/pg-setup/neon-baseline.txt` is **now empty**, deliberately: left naming 8 dead
> projects it would report 8 destructions on every future check — a guard inverted into a permanent
> false positive. Full quota is free, which matters for `1.7`'s live-Neon path.
>
> **Read next:** `.dev/reference/260811-review-fix-mutation-proofs.md` — 13 mutation proofs with
> verbatim red and green, both gate runs, both baseline checks, and a section on the one guard that
> was measured to be a tautology and deleted. Traces: `260811-review-fixes.md`,
> `260810-neon-boundary.md`, `260810-query-dispatch.md`, `260810-sse-live-events.md`,
> `260810-local-postgres.md`, `260810-docling-gate.md`.
>
> **Next move:** `0.6` (one `count(*)`, sizes the labelling loop), then `0.1`/`0.4`. `BACKLOG 1.13`
> is newly named and cheap to start: 22 integration tests still skip, and a skip is unobserved.

> # MERGED — 2026-08-09, `main` is at `57be16b`
>
> `feat/d6-labelling-loop` merged with `--no-ff`, carrying `feat/d1-agent-invocation` with it (D6
> contained all 22 of D1's commits). 42 commits, 67 files, +35,033/-411, no conflicts. **The merged
> tree is byte-identical to the tip measured at 2112 passed / 12 skipped / 0 failed** — verified with
> `git diff feat/d6-labelling-loop main`, which is empty — so that observation carries to `main`
> without re-running.
>
> **Merged with `0.4` and `0.5` unsettled.** The owner's call, recorded, not argued. What changed:
> - **Nothing deployed.** No workflow triggers a deploy; the repo has only `ci.yml` and `nightly.yml`.
>   No eval has run against any production tenant.
> - **`0.4` moved rather than lapsed.** `celery_app.py:208` schedules `eval-nightly` at 02:00 UTC
>   daily, so the first beat worker running this code against production tenants sends customer rows
>   to the Ragas judge with `pii_firewall_applied=False`. There is no further merge in the way — the
>   next gate is a deploy, and deploys are not gated here.
> - **`0.5` was decided by merging.** The deviation is on `main`. The row stays open only to record
>   which way, and it is cheap either way.
>
> **What is live and what is inert.** Live: the eval invokes the real agent, and the deploy gate
> refuses a run that does not record having invoked it (including every pre-D1 run, which fails
> closed). Inert: the labelling loop. `alembic_tenant` 0016 has been applied to no database, so every
> label attempt returns 503, and per `2.28` the miner that was to fill the queue has never produced a
> row and cannot. Both behind `0.2`.
>
> **Next, in order:** `0.6` (one `count(*)` — sizes the whole loop and decides whether `2.28` and P4
> are worth building), `0.2` (a local PostgreSQL — unblocks the migration roundtrips, the calibration,
> and the metric ever being observed to move), then `0.4` before anything runs nightly.

> **STATE AS OF 2026-08-08, end of the D1 workflow.** All 14 agents completed. P1, P1b, P2, P3, their
> tier-1 reviews and bounded fixes, and — for the first time on this branch — **the tier-2 judge**.
> Verdict: **`mergeable: true`**, extracted to `.dev/reference/tier2-judge-d1.md`. Its one-line read:
> *"a correctly-shaped, fail-closed measurement pipeline that has never measured anything — which is
> a large improvement over a pipeline that confidently measured its own label, and is honestly
> labelled as such."*
>
> **Two owner decisions block the merge**, filed as `BACKLOG 0.4` and `0.5`: production customer rows
> can reach the Ragas judge API with the PII firewall off (an *egress* question `2.11` frames only as
> scoring fidelity), and the `alembic_tenant` migration the plan required does not exist — argued
> away coherently, but a written-contract deviation only you can accept.
>
> **Naming caveat for anything below and in `.dev/traces/`:** every in-phase reviewer on this branch
> was **tier-1**. Several commit messages, three trace filenames (`…-p1b-tier2-fixes.md`,
> `…-p2-review-fixes.md`, `…-p3-review-fixes.md`) and earlier BACKLOG text call them "tier-2". They
> are not. Tier-2 is the Fable judge, it ran once, and its output is the reference file above.

> **STATE AS OF 2026-08-09 — D6, `feat/d6-labelling-loop`, STACKED ON THE UNMERGED
> `feat/d1-agent-invocation` (`4179a5c`), not on `main`.** Merge only the top of the stack, and only
> once D1 lands. Nothing below has been rebased.
>
> - **P1 landed** (`alembic_tenant` 0016 + `app/services/label_service.py`): the `human_authored`
>   tier that `LABEL_TRUST_TIERS` had declared since D5 and nothing could produce, behind four
>   restrictions. **P2 landed** (`4962ff5`, review fixes `17a5774`): `GET .../eval-scenarios/unlabelled`
>   and `POST .../eval-scenarios/{id}/label`. **P3 landed** (`edb4fbb`). **P4 is unstarted**, by the
>   owner's "backend only this run".
> - **P3's finding is that the run record had gone stale, not that plumbing was missing.**
>   `VERIFIED_QA_PROMOTION_DECISION["reason"]` — stamped into `eval_runs.config` on every run — said
>   "no row is promotable until a correction UI produces human-verified answers". **P1 and P2 built
>   that correction UI**, and `human_authored` (rank 3) clears `VERIFIED_QA_MIN_TRUST_TIER` (rank 2),
>   so the pre-D6 guarantee — "unreachable by construction, no flag needed, and it becomes reachable
>   the moment a human tier exists" — had inverted from a feature into a hazard against the owner's
>   eval-only decision. Promotion is now held by **two independent locks**: the gate reads
>   `eval_scenarios.source` (which labelling never touches) and `enabled: False` refuses last, so a
>   row it refuses is *counted* under `promotion_disabled:eval_only` — which is the measurement of
>   what flipping the decision would actually promote. Downstream, a labelled row enters the eval,
>   joins **no** golden set (the label UPDATE assigns four columns and `dataset` is not one), and
>   raises the **denominator** without raising `scored` when the agent cannot answer it.
>   Long form and all 12 mutation proofs: `.dev/reference/d6-p3-label-downstream.md`.
> - **R2 fired twice on P3's own work and was not weakened** — once on prose naming the writer inside
>   a string constant, once on the new test module importing it. Two tests consequently live in
>   `test_label_provenance.py`; the new module reaches the tier through
>   `VERIFIED_QA_PROMOTION_DECISION["producible_label_tier"]`, pinned equal there.
> - **Branch suite OBSERVED at the D6 tip (`6dc4990`), 2026-08-09: 2112 passed, 12 skipped, 0 failed,
>   28 warnings, 493.34s.** Zero `FAILED`/`ERROR` lines. Detached run (`Start-Process`), not relayed.
>   This is the figure to merge on; the `edb4fbb` reading below predates four later commits.
> - **`feat/d6-labelling-loop` contains ALL of `feat/d1-agent-invocation`** — verified by
>   `git merge-base --is-ancestor`. `main..d6` is 42 commits, of which 22 are D1's. Merging D6 merges
>   D1 with it, so `0.4` and `0.5` sit in that path whichever branch is named. Rebasing D6 onto `main`
>   to separate them is not viable: D6's P3 builds on the eval structures D1 rewrote.
> - Branch suite at `edb4fbb`+: **2101 passed, 12 skipped**. Ignored-new-files control: **2077/12**,
>   which is the *measured* baseline at `1c2b471` — so no pre-existing test changed status. Note the
>   D6 workflow brief's "1873/11" is the branch point `4179a5c`, before P1 and P2 landed; it is not a
>   baseline for anything on this branch.
> - **`BACKLOG 2.4`'s "mined scenarios are inert by construction" is now narrowed but NOT closed.**
>   The tier exists, the routes exist, and **0016 has been applied to no database**, so every label
>   attempt on every tenant today returns a 503 naming the migration. No row has ever left the
>   unlabelled state. Behind `0.2` (no PostgreSQL here) like everything else.
> - The P2 adversarial review found **18 items, including four behaviour mutations that survived the
>   54 tests P2 shipped with** — the queue's sort direction, its `LIMIT`/`OFFSET` binding, the counts
>   identity, and three of four spellings of a forged write. All fixed at `17a5774`; the write is now
>   scoped so it cannot overwrite a golden-set answer, and **only a Clerk JWT may stamp a human tier**
>   (an API key is a machine credential and `label_service`'s guards are all in-process).
>   Long form: `.dev/reference/d6-p2-labelling-queue.md` §7.6, and
>   `.dev/reference/d6-p2-review-fixes.md`.
> - Branch suite at `17a5774`: **2077 passed, 12 skipped**. Ignored-new-files control: **1994/12**.

**In flight (2026-08-08): `feat/d1-agent-invocation`, unmerged.** P1 (the options seam, `ec5f445` +
`d15be3a`), P1b (recorded mode + the canary write order, `487ebbe` + `117de05`), **P2 — the eval
invokes the agent** (`d127b4d`) and the P2 review fixes (`b62186f` + `075550d`).
`eval.py` no longer sets `agent_response = reference_answer`: each scenario's question goes to the
customer agent through the seam with `side_effects="recorded"`, the agent's own text and its own
retrieved contexts are what get scored, failed scenarios are excluded and counted, a run below
`MIN_RESPONSE_RATE` reports `unknown`, and `config.agent_invoked` is written as an observation.
**P3 has not started** (BACKLOG 2.2) — but the ship-on-nothing window it was going to leave open is
closed in the interim: a run below the floor now writes **no `eval_results` at all**, so the gate
reads `EVAL_SIGNAL_NO_VALID_SCORES` and refuses. A run produced by the pre-P2 tautology still carries
scores and no `agent_invoked`, which is still P3's job.

**The tier-2 judge HAS now read P2** (at `7a7486e`): 17 findings, 7 unsupported claims, all
addressed in `b62186f`. Four changed what a run means — the below-floor fail-closed above; the
contexts handed to Ragas are now one untruncated string per chunk rather than a truncated repr of the
SDK block as a single element; the broker's `visibility_timeout` (3600) was below the worst case a run
stamps on itself (5400) and the idempotency window was 1/9 of it; and a responded turn with no
retrieve call is excluded and counted rather than scored 0 on three context metrics. Three of P2's
own guards were proved not to be guards — most sharply, a one-token fallback to the stored context
column passed all 163 eval tests.

**Branch suite OBSERVED at the tip (`1d85789`), 2026-08-08: 1873 passed, 11 skipped, 0 failed,
30 warnings, 366.48s.** 1884 collected. Zero `FAILED`/`ERROR` lines. This supersedes the 1821 below,
which was recorded at the P2 review fixes — P3 and its review fixes added tests after it.

Two notes for whoever runs it next, both learned the hard way here:

- **The gate takes ~6 minutes and fits the tooling fine.** Three consecutive backgrounded attempts
  were killed at ~3-4% and it was briefly written up as a wall-clock regression. That was wrong:
  a detached run (`Start-Process`, output redirected to a file) completed in 366s, faster than
  `main`'s 451s with 178 more tests. Cause of the kills unknown; if it recurs, detach rather than
  concluding anything about the suite.
- **~15 tests cost 14-16s each**, all in `test_agent_task.py`, `test_agent_turn_metrics.py`,
  `test_agent_turn_connection_batch.py` and `test_agent_options_seam.py` — about 225s of the 366s.
  A characteristic of the turn-path tests, not a regression. Isolated-module timings mislead here:
  `test_agent_task.py` alone reads 142s for 13 tests, which invites exactly the wrong conclusion.

Superseded figure: 1821 passed / 11 skipped / 0 failed (was 1795 at `7a7486e`, 1766 at `1d3a7bd`).
`mypy app` clean; `ruff check app tests` clean via `uvx ruff@latest` — ruff is **not** installed
in `apps/api/.venv`.
(`main`'s 1675/11/0 below is the pre-branch number and is not the figure to measure a delta against.)

Traces: `.dev/traces/260808-d1-p2-review-fixes.md` (latest), `.dev/traces/260808-d1-p2-invoke.md`.
Mutation proofs: `.dev/reference/p2-review-mutation-proofs.md` (23, red then green; **one did not go
red first time** and the fixture gap is recorded), `.dev/reference/p2-mutation-proofs.md` (19).
Earlier: `.dev/traces/260807-d1-p1b-recorded-mode.md`, `.dev/reference/p1b-mutation-proofs.md`.

**Unprovable here, and stated as such:** no end-to-end eval run, no live SDK turn, and
`update_eval_run_config`'s jsonb merge has never executed against a database. All behind BACKLOG `0.2`.

**All three PRs are merged. `main` is at `fd47133`** — the `.dev` convention (#1), the eval foundation
(#2) and the CI repair (#3). Suite 1199 → **1675 passed / 11 skipped / 0 failed**; ruff 461 → 0;
mypy 75 → 0. Trace: `.dev/traces/260805-eval-foundation.md`.

**Merge gotcha worth remembering:** the three stacked PRs were merged in one chained `&&` command.
All three reported MERGED, but only #1 reached `main` — #2 and #3 merged into their *original base
branches*, because GitHub had not retargeted them yet. `&&` waits for the CLI call to return, not for
the retarget. Closed by merging `origin/feat/eval-foundation` (which by then held all three) into
`main`. **Next time: merge only the top of the stack, once everything below it has landed there.**

## CI is red for an environmental reason, not a code one

Two consecutive runs died with every job `cancelled` at **15m03s** and **15m02s** — including Lint,
which takes 11 seconds. That is a hard wall-clock cap at the account/runner level, not a workflow or
code fault. Check Actions minutes and the spending limit at `github.com/settings/billing`; until it
lifts, CI cannot report anything and the gate is unreadable again.

**Settled by the remote gate** (run on `a4a03fb`, before the cap bit): Lint (ruff) **pass**,
Type-check (mypy) **pass**. Those two carried the 536 violations.

**Never yet executed on a runner:** Unit and Integration. Both had real, now-fixed causes — the unit
job had no Redis service (`test_agent_task.py` drives the Celery result backend against a real
client), and `conftest.py` inserted into `tenants(api_key)`, a column migration `0006` renamed to
`api_key_hash`. `-x` was also dropped: it halted the unit run at the first failure and reported
"1 failed / 76 passed" while hiding ~1600 tests.

**`--cov-fail-under=80` has still never executed in this project's history.** Real coverage is
unknown. If it lands below 80 the check fails for a true reason — report it, do not lower it.

**The tier-2 verdict stands as the honest read of what merged:** *"an honest and well-guarded
instrument-building milestone, mergeable as such — but do not read it as 'the platform is now
evaluated': nothing on this branch has yet measured a real agent, and the one live signal the gate
consumes is still vacuous."*

**The next phase is already named: D1.** The eval still sets `agent_response = reference_answer`, so
the deploy gate now fail-closes on an *absent* signal while shipping on a *present* one that measures
nothing. The config tuple stamps provenance on that tautology, which makes it look credible. This was
a gap in the plan, not the execution — the audit named target leakage as defect #1 and the four
phases assigned it to nobody.

---

## Where the product actually is

**Milestone v1.2 (Gotham console + agent management) is NOT closeable.** Phases 20, 21, 23 all
executed; the 2026-08-04 audit (`.planning/v1.2-MILESTONE-AUDIT.md`) returned `gaps_found` — 27/29
requirements satisfied, 4/5 integration flows wired. Phase 23 closed the ops-room wiring seam
(11/11 gate) and `3701b05` closed the console half of the deploy-gate contradiction.

**Two things block the milestone:**

1. **OPS-15 — server-side gap.** `POST /approve-deployment` (`deployment.py:348+`) gates on the
   frozen `run.recommendation` and never consults live `open_findings`. A critical finding raised
   after a clean checklist run is still accepted **by the API**; the console can no longer be used to
   do it, but any script or curl can. Fails closed, so not a security hole. Backend change with its
   own threat surface — needs a plan.
2. **`REQUIREMENTS.md` traceability.** `WIRE-01..05` has **zero rows** (grep-confirmed). The v1.2
   rollup sentence is stale. The `OPS-01..06` collision is live: lines 274-279 hold them as Phase 10
   / M10 `Pending`, while line 415's `OPS-01..16 | Phase 21 | ✓ Complete` falsely ticks the weekly
   cron, digest email and alerting that Phase 21 never built.

**Open but not blocking:** Nyquist `status: draft` on `20/21/23-VALIDATION.md`; Phase 20 has no
`20-SECURITY.md`; the `eval/page.tsx` unguarded-read twin (`res.scores.faithfulness`, 5 call sites).

## The live-gate backlog — one root cause

`VER-01`, `AUD-03`, `CAP-03`, the 6 blocked UAT items in `23-UAT.md`, and the 4 `human_needed` items
in `23-VERIFICATION.md` all share one precondition: **there is no PostgreSQL server on this
machine.** Stale `postgresql-x64-17` service pointing at a deleted binary; nothing on 5432-5435.
`CONTROL_DB_URL` is live Neon production and is never a substitute. No v1.2 migration has been
applied to a live Neon DB.

Installing a local PostgreSQL clears all of it with no planning or code work.

## What this session found (the reason the plan exists)

Full detail in `.dev/reference/measurement-layer-audit.md`. Seven defects in the measurement layer:

- **D1** the RAG eval never invokes the agent — the label is used as the prediction (`eval.py:200`)
- **D2** every eval result is written to a Neon branch that is deleted in `finally` (`eval.py:281-313`)
- **D3** the deploy gate's eval query uses columns that do not exist → fails **open**
  (`deployment_service.py:201`)
- **D4** 5 of 7 red-team attackers were never given their tools → report clean
  (`red_team_service.py:179/197` defined, never referenced)
- **D5** filing a *failing* trace stores its answer as ground truth (`bench.py:147`)
- **D6** mined scenarios are inert by construction (`reference_answer=''` vs `WHERE != ''`)
- **D7** every LLM judge is uncalibrated; the harness exists with zero labels entered

**Hard ordering constraint:** D2 currently masks D5. Fixing the write-back without fixing the label
inversion in the same change activates a path that serves a human-flagged failure to customers via
`verified_qa_lookup`.

## Toolchain state

- `apps/api/.venv` is **restored** (2026-08-07): pytest 9.1.1, 396 packages. The earlier "shell, 34
  packages, no pytest" reading is superseded. If it is disk-cleaned again: `cd apps/api && uv sync
  --extra dev`, and run one `uv` at a time — two concurrent runs deadlock on the wheel cache lock.
- `apps/admin/node_modules` and `apps/widget/node_modules` are present.
- Backend suite baseline **OBSERVED 2026-08-07 at `af0f601` (main): 1675 passed, 11 skipped,
  0 failed, 30 warnings, 451s.** Supersedes the 2026-08-05 `fd8fa20` reading of 1199/8/0/202s, which
  predates the eval-foundation merge. Any phase claiming a delta measures against 1675/11.
  (Wall clock roughly doubled with the test count; 451s is the number to expect, not a warning sign.)

## Next move

1. ~~Finish the toolchain restore, observe the real baseline.~~ **Done 2026-08-07** — see above.
2. ~~Run `.dev/workflows/eval-foundation.workflow.js` on `feat/eval-foundation`.~~ **Superseded** —
   that branch merged at `fd47133`; the workflow is archived in `.dev/workflows/` as the reference
   implementation of the tier-2 pattern, not as pending work.
3. **CI (§1) is paused by the owner, 2026-08-07** — blocked on the Actions billing cap (`0.3`), which
   is not a code problem. `1.3` and `1.4` remain available as local work.
4. Step 0 of the ladder is **owner work, not agent work**: score
   `apps/api/tests/evals/calibration/human_scores.csv`. Nothing above it can be trusted until judges
   are calibrated. The workflow prepares the inputs; it cannot supply the judgement.
5. The unblocked headline is **D1** (`BACKLOG` §2) — `app/worker/tasks/runtime/eval.py:374-375`
   still sets `"agent_response": row[3]`, where `row[3]` is `reference_answer`.
