# MASTERPLAN — from here to two live agents

**FROZEN 2026-08-22.** The `wayfinder:map` issue on GitHub carries the path now; this file is the record of how it was framed up to M1.

The single ordered path from the current branch to production, for every session between now and
launch. Milestone plans in `.dev/plans/` decompose these milestones; if a plan conflicts with this
file, this file wins or gets amended in the same commit.

**Production readiness has one definition.** Two agents live, tested on their deployed Vercel URLs:

1. **Mellow Earth Elements — transactional agent.** Provisioned end to end through the admin UI
   (signup, ingest, eval, red team, checklist, approve), embedded on the Mellow storefront
   (`../mellows-earth-elements`, Vite + React + Supabase), and observed completing a real
   transactional action through the Actor gate against Stripe test mode.
2. **Bantuson portfolio — support agent.** Provisioned and managed through the W Chats MCP server
   (MCP 2026-07-28 spec), served on the portfolio site (`../portfolio-dashboard`), and observed
   answering grounded support questions live.

Neither test passing = not production ready. No other definition counts.

## Terminology, fixed

These words have been conflated in the record; each now means exactly one thing.

| Term | Means | Status |
|---|---|---|
| **Transactional (PRD v1.1)** | Typed skills + envelope + Actor gate + adapters (Phases 14-19) | Built. Never exercised against a live provider |
| **Executed v1.2** | Gotham console + agent management (Phases 20, 21, 23) | Built. `gaps_found`, polish pending (M5) |
| **PRD v1.2 "protocol surface"** | A2A runtime endpoint, agent card, JSON-RPC, counterparty auth, MCP provisioning, `wchats` CLI, Skill | **Never built. 15/15 deliverables at zero** |
| **A2A** | A *runtime* protocol: other agents calling a deployed W Chats agent as a counterparty | Out of scope until a real counterparty exists. Not in this plan's finish line |
| **MCP provisioning** | A *control-plane* surface: creating and managing W Chats agents from developer tools over the REST API | In scope, last (M7), built on the stateless MCP 2026-07-28 spec |
| **CLI / provisioning Skill** | ADR-0002's other two surfaces | CLI dropped (superseded by stateless MCP). Skill ships with M7 |

`registry.py`'s `to_a2a_skill()` metadata stays as forward-compat; nothing in this plan consumes it.

## Model provider: DeepSeek by default (owner decision, 2026-08-15)

Every model call, both halves of the split, runs against DeepSeek's Anthropic-compatible endpoint
`https://api.deepseek.com/anthropic` (DeepSeek v4: flash for haiku/sonnet-class calls, pro for
opus-class). $5 of DeepSeek credits covers comprehensive testing; the Anthropic balance is off the
critical path.

- **Mechanism, no refactor.** The endpoint speaks the Anthropic Messages format, which is one of
  the three wire formats the Agent SDK's CLI natively selects via `ANTHROPIC_BASE_URL`
  (`.dev/reference/260811-agent-sdk-provider-surface.md` §2). Set `ANTHROPIC_BASE_URL` and put the
  DeepSeek key in `ANTHROPIC_API_KEY`, **exported into `os.environ` of the API and every worker**
  (the `1.28` rule); the SDK passes the whole environment to the spawned CLI.
- **Model IDs stay pinned.** The endpoint auto-maps `claude-haiku-*`/`claude-sonnet-*` to
  `deepseek-v4-flash` and `claude-opus-*` to `deepseek-v4-pro`, so the constants in judges,
  attackers and the customer agent need no edit. Explicit `deepseek-v4-*` names also work.
- **Direct clients verified env-driven.** All `anthropic.Anthropic()` sites construct bare
  (`actor_seam.py:57`, `validation_service.py:24`, `red_team_service.py:50`, and 10 more);
  `metadata_service.py:51` passes `api_key` but not `base_url`, so the env base URL still applies.
- **What stays true.** Anthropic states it does not support routing to non-Claude models through
  any gateway: this is a support posture to carry, not a blocker. Judge calibration is
  per-provider: Spearman numbers earned on DeepSeek hold for DeepSeek only, and switching a role
  back to Anthropic re-runs `0.1` for that role.
- **Proof before spend.** The seam counts as landed only when one Agent SDK turn and one
  direct-API judge call have been observed returning verdicts through the endpoint (`7.7`).
  Anthropic remains available per-environment by unsetting `ANTHROPIC_BASE_URL`.
- **Proven 2026-08-16, with one seam fact every judge depends on:** the endpoint runs claude-*
  aliases in thinking mode, which rejects forced `tool_choice`; every forced-tool call therefore
  carries `thinking={"type": "disabled"}` (a no-op on Anthropic). Trace:
  `260816-deepseek-seam.md`. Cost telemetry on the SDK path is Anthropic-priced fiction: `7.13`.

## What the eval review changed (2026-08-18)

Two eval walkthroughs were worked through end to end and mapped onto what this system actually runs.
Fundamentals in `.dev/reference/260818-llm-eval-fundamentals.md`, the gap list in
`260818-eval-practice-gap-analysis.md`, the queue in BACKLOG section 8. Every gap was checked against
the code before being filed.

**Nothing below changes the definition of production readiness.** Two agents live on their Vercel
URLs is still the only bar. What changes is what counts as evidence on the way there, and in what
order the work happens.

Ordered by how much it moves the plan:

1. **M2 cannot be earned at k=1, so `8.1` goes in front of it.** Every eval scenario currently runs
   once, which conflates two different failures: a system that CANNOT do the task and one that does
   it sometimes. M2's claim is consistency, and one pass per scenario cannot support it. A 7/7
   red-team result at k=1 is silent about the eighth attempt. M2's exit now names reliable@k.
2. **The improvement loop moves BEFORE launch, not after.** `2.28` (miner) and `2.4` (labelling UI)
   were scheduled after the finish line. The trace-to-taxonomy loop is the front end of every other
   eval: read traces, label binary, write reasons, cluster them into failure categories, prioritise
   by frequency AND severity, stop at saturation. Our twenty scenarios were written from
   imagination because the system had no traffic; that was correct then and is not the plan now.
   This is the difference between reaching production and surviving it.
3. **Every number we quote gains an interval and a segment.** No success rate ships as a bare point
   estimate, and none ships pooled across scenario categories the corpus already tags
   (`golden_path`, `edge`, `adversarial`, `out_of_scope`). This is the existing "unknown is never
   pass" rule one level deeper, and it applies to `7/7`, `20/20` and every eval percentage.
4. **Judge calibration needs chance correction, not just correlation.** Spearman rho over three
   pairs cannot carry the claim. Cohen's kappa subtracts agreement by luck; Matthews correlation
   covers the imbalanced case where kappa collapses on a good judge, which is the shape our corpus
   has. The judge also ships a confusion matrix rather than one number, because each quadrant names
   a different fix, including "the product is broken, stop tuning the judge".
5. **Judges run at temperature 0 and get bias probes.** No temperature is set anywhere in `app` or
   `tests/evals` today. Verbosity and position bias are both cheaply testable and currently untested.
6. **M4.5 gains a lever before it starts.** The cheapest eval that answers the question wins:
   code-based, then a small off-the-shelf model, then a judge. Citation validity is set membership
   and is currently judged by an LLM.
7. **Alignment is re-measured on a schedule, because it decays.** Judge-to-human agreement is not
   established once. Prompts, data and users drift, and nothing currently re-measures it. This is
   the piece that makes the harness survive production rather than merely launch.

## The milestones

Order is deliberate: backend and measurement first, cloud before console polish (a polished console
over an undeployed backend proves nothing), UI-driven provisioning as the rehearsal for Mellow, MCP
last. Each milestone ends in an observable gate, run for real, never asserted.

### M0 — land the branch

- Owner merges `chore/local-postgres` (59 commits) into `main`. Claude never merges.
- This file, its BACKLOG rows (§7), and the HANDOFF pointer land with it.

**Exit:** `main` carries the Phase A fixes and this plan.

### M1 — finish the measurement story locally (Phase B of PRODUCTION-READINESS §4)

The defensibility claim rests on judges that have never been read under full context and never
calibrated. Nothing deploys on top of unverified measurement.

- Step 0: land `7.7`, the DeepSeek provider seam (§Model provider), proven by one observed SDK
  turn and one observed judge call. Everything after this spends DeepSeek credits.
- Alongside it: `7.8`, the Martin battery (§How work is verified) — baselines measured, floors
  written, `make gates` wired. It lands here so every later milestone inherits it.
- E2E-3b: one live customer turn, `RETRIEVAL_FAITHFULNESS_SAMPLE_RATE=1.0`, read the verdict and
  `judge_context` counters. Settles `5.13`, `5.15`; first verdict under the `5.16` fix.
- E2E-6: capture the 20 calibration responses (`0.1` prerequisite), owner scores
  `human_scores.csv`, re-label the same rows blind in `human_scores_pass2.csv`, then run
  the three-part calibration gate: (a) the judge's bootstrapped kappa interval clears chance, (b1) the owner's own blind re-label clears chance, (b2) the paired difference does not show the owner beating the judge.
- E2E-7 per PRODUCTION-READINESS §4.
- `0.7` is decided (owner, 2026-08-15): DeepSeek is the default provider for both halves via the
  Anthropic-compatible endpoint. See §Model provider; the work row is `7.7`.

**Exit:** a grounding verdict from a live turn whose reason cites provenance, and calibrated judges
with both kappa intervals and the paired difference recorded. Spearman is still reported and
no longer gates anything (8.2b, 8.2c, 8.2d).

### M2 — earn the first ship verdict

No `ship` has ever come from real signals (`260815-the-never-executed-class` §"not established").

- One eval run observed invoking the agent against an ingested corpus, scores stored, gate reads
  `measured`.
- One red-team run observed at 7/7 attackers with tools; cost RTX-01 before running it (`1.13b`).
- Fix `5.17` (approve-time high-severity drift) and `1.31` (stuck `running` checklist row blocks
  the gate for 60 min) — both sit directly on the deploy path.
- Checklist + approve driven end to end locally: the first earned `ship`.

**Exit:** `run_deployment_checklist.complete recommendation=ship` from real eval + red-team
signals, observed once, trace recorded.

**AMENDED 2026-08-18 (`8.1`):** the eval and red-team runs above are run at k > 1 and reported as
reliable@k per scenario category with an interval, not as a single pass. A verdict from one run per
scenario cannot distinguish a system that cannot do the task from one that does it sometimes, and
consistency is the entire claim M2 exists to earn.

### M3 — make the widget and the agent endpoint products

The two runtime surfaces both customers will use. All defects here are located (rows `7.1`-`7.6`,
plus `7.23`, which joined on 2026-08-17: the widget session dies at 15 minutes and the customer
sees an error, found by the E2E-6 capture rather than by reading).

**One authorisation recorded, because it changes a workspace's shape:** `apps/widget` had NO test
infrastructure, so its behaviour could only ever be verified by "the build passed". vitest is
authorised there as a devDependency (devDeps do not touch the 20480-byte gzip budget) for the
401-retry test, and a theming-contract check joins the repo's existing check-script idiom.
Untestable behaviour is the defect shape this project keeps paying for.

- One embed-snippet generator: the API's (currently omits `data-api` and produces a widget that
  cannot talk to anything); console renders what the API returns.
- Theming: `/widget/{id}/config` serves `agent.widget_config`; align the five injected CSS
  variables with the names `widget.css` reads; fix `agent_name`/`name`.
- SSE: add a terminal event after `agent.response` so third-party clients do not hold sockets 120s.
- `POST /api/v1/agents/{id}/chat`: add the rate limit and daily budget guard the widget path
  already has. Without this the endpoint is an uncapped spend hole.
- Rebuild the stale `embed/` artifact from `dist/`; gate it so they cannot drift.
- PROD-11 proof: paste the snippet on a plain static page, zero hand-edits, working chat.
- Minimal endpoint doc: one markdown page with the POST contract, the SSE event list, auth.

**Exit:** PROD-11 observed, and one BYO-client turn driven with curl + EventSource using only the
doc page.

### M4 — the cloud (Phase C of PRODUCTION-READINESS §4)

- **[owner]** `0.3` lift the Actions billing cap; CI green remotely for the first time (`1.1`).
- Deploy workflow: build images in CI, stand up the Terraform stack. Local dev stays
  container-free; images are built and run only in CI/cloud.
- **A beat service ships in the same stack.** Highest-leverage single missing piece: without it,
  nightly eval, weekly red team, digest and alerting have never fired. The eval page's "evaluated
  automatically each night" sentence becomes true here or the sentence is removed.
- **[owner] `0.4` decides before beat starts**: PII firewall on the eval path, or accepted egress
  named as such. `eval-nightly` fires the first night a beat worker runs; this is the last moment
  the decision is ahead of the behaviour.
- **[owner]** Clerk production instance + its three keys (`1.20`); real S3 bucket + AWS creds;
  `PLATFORM_CREDENTIAL_KEY` generated and set (`1.22`, key material, owner-held);
  `EMBEDDING_PROVIDER=voyage` set explicitly. Full missing-name list in §Credentials.
- Widget assets published to the CDN origin (`wchats-widget` bucket) from the M3-rebuilt `embed/`.

**Exit:** a staging agent, provisioned in the cloud, answers a grounded question on a public URL.

### M4.5 — unit economics, end to end (owner, 2026-08-17)

**Gate between a validated backend and any UI work.** The backend is provable by then and the cloud
bill is real, so this is the last moment the numbers can be measured rather than estimated, and the
first moment they are all observable. Deliverable: one costing document with measured inputs, plus
the levers ranked by what they save.

Measure per unit, never per month, so the model scales without being rewritten:

- **Per interaction, split by agent type.** A support turn (agent call + Gatekeeper + Auditor +
  Strategist + retrieval + rerank) and a transactional turn (all of that plus the Actor gate per
  mutating attempt, IDV, and provider API calls) have different shapes and must be priced apart.
  The Auditor is the one to watch: it now receives the full retrieved context, up to 80,000 chars.
- **Per flywheel cycle.** Nightly eval (per scenario x per metric, including the Ragas judge and
  the embedding wrapper), weekly red team (attackers are Sonnet-class and multi-turn), scenario
  mining, calibration re-runs. These are the costs that recur without a customer interaction, so
  they set the floor under an idle tenant.
- **Per tenant, fixed.** Neon project, ingestion at signup, storage, the share of Fargate/Redis/S3
  a tenant occupies. This is what a free trial actually costs.
- **Infra, fixed.** The M4 stack: three-plus ECS services, the beat worker, Redis, S3, CloudFront,
  Langfuse, Clerk production.

Two rules for the exercise, both learned the hard way here:

- **Measure from token counts, never from the SDK's `total_cost_usd`** — it is priced against
  Anthropic tables while calls go to DeepSeek (`7.13`), so it is fiction at the exact moment it
  looks authoritative.
- **Report `unknown` for anything not observed.** A costing that quietly fills gaps with plausible
  numbers is the measurement-honesty failure this project already has a rule about.

Known input at time of writing: retrieval/rerank modelled at ~$0.0015 per interaction, dominated by
shipping ~30 chunks to keep 5 (`7.22`, the lever). Everything else is unmeasured.

**Exit:** a costing document whose every headline number traces to an observed measurement, the
per-tenant monthly floor stated, and the top three cost levers named with their measured saving.

### M5 — console polish, mockup first

UI is where the owner's taste is the deciding input, and it comes after the backend is provable.

- Polish the Gotham mockups first; owner reviews the mockups, not the src. No src work until a
  mockup passes.
- Carry approved mockups into `apps/admin` under the design codex; adversarial UX review on
  rendered pixels (screenshots + computed contrast) before the owner sees any screen.
- Diagnose the e2e gate honestly: `1.20` (dev Clerk keys) first, then the 7 timeouts (`1.19`).
- Close `5.4` (unknown rendered as Pass chip) and `5.5` (unguarded score reads) — both are
  honesty-of-display defects, which is what the console polish is for.

**Exit:** owner signs off the provisioning journey screens; admin e2e green or every remaining red
explained by an established cause.

### M6 — Mellow Earth Elements live (finish line, half 1)

The founding promise, run for real: a business owner completes signup to deploy through the UI.

- Step 0: the Paystack adapter (`7.10`). Paystack is the SA-default provider (owner decision
  2026-08-15, rationale in `.dev/reference/260815-sa-payment-provider-decision.md`: T+1 settlement
  and free payouts against Stripe direct's ~T+7 and uncertain SA availability). Mirrors
  `stripe_adapter.py`'s three operations; idempotency rides the unique transaction `reference`
  plus the platform's own `idempotency.py` for refund dedup, because Paystack has no native
  idempotency header. The Stripe adapter stays for future international tenants and
  `test_stripe_live` parks with it.
- The rails, decided (owner, 2026-08-15): **three rails, one provider.**
  1. **Paystack card** is the agent's automation rail: refunds, subscriptions, payment links, all
     API-driven.
  2. **Instant EFT is an optional extra delivered as a Paystack channel** (`channels:
     ["card", "eft"]` covers Pay with Bank and Capitec Pay), never a second provider. PayFast was
     evaluated and rejected for the agent surface: its refunds are dashboard-driven, which breaks
     `issue_refund` automation.
  3. **The static FNB account stays, agent-served, human-approved** (`7.11`): the agent hands the
     bank details to customers who choose manual EFT; the order is created awaiting proof of
     payment; the customer uploads the POP with the order; it lands in a console queue that
     **Mellow approves, never the agent** — the approval route accepts a Clerk JWT only, the same
     machine-credential refusal the label route already enforces (`4.7`), so the exclusion is
     structural, not prompted. Refunds on this rail likewise route through
     `pending_confirmations` for the owner to execute.
- **[owner]** Paystack account + test-mode secret key for Mellow; seeded as the tenant's
  `integration_credentials` row through the console (INT-01 path). Verify the actual settlement
  terms at signup, before promising Mellow anything.
- Provision the Mellow tenant and agent entirely via UI. Ingest the Mellow corpus (product and
  policy docs from `../mellows-earth-elements`). Eval, red team, checklist, approve: the gate earns
  its second ship, this time on a real business corpus.
- Embed the widget on the Mellow Vercel site; run the live test: grounded product answers plus one
  transactional action (refund or order via Paystack test mode) passing the IDV + Actor + envelope
  chain — the first live provider call in the project's history, and the shop's first real payment
  rail (today's checkout is a static EFT instruction block).

**Exit:** the owner's test on the Mellow Vercel URL passes, recorded with job ids and the audit rows
of the transactional action.

### M7 — MCP provisioning surface + Bantuson support agent (finish line, half 2)

- A thin stateless MCP server (spec 2026-07-28) over the existing REST API: the ~14 curated ops
  from ADR-0002 Part B (create/soul/ingest/eval/red-team/checklist/approve/embed/audit). MRTR maps
  the approve journey's acknowledge-each-warning flow. No session state; serverless-deployable.
- The provisioning Skill carrying the playbook (order of operations, how to read an eval summary
  before approving). CLI: dropped, superseded.
- ADR-0002 updated from `Proposed` to `Accepted (amended)` recording the MCP2 rebase and the CLI
  drop.
- Provision the Bantuson support agent through that MCP server from Claude Code: the dogfood proof.
  Non-transactional envelope (no skills enabled), corpus from the portfolio's own content.
- Serve it on `../portfolio-dashboard`'s Vercel deployment (its existing `wchats/` embed from
  Phase 12 gets replaced with the M3 widget or the documented endpoint).

**Exit:** the owner's test on the portfolio Vercel URL passes, with the agent's entire lifecycle
having run through MCP tool calls.

## How work is verified, milestone by milestone

Three layers (the `verify` skill): the Martin battery, the data-science gates, the adversary. The
battery has **never been applied here**; adopting it is scheduled work (`7.8`, lands in M1), not an
assumption.

### The standing protocol, every milestone

- Plan file before execution, trace after. BACKLOG updated in the landing commit.
- Every behaviour change carries a test **observed to fail**: mutate the guard, observe red,
  restore from HEAD, observe green, record the observed output. A negative test never seen red is
  indistinguishable from a tautology.
- Gates are **quoted as observed output**, never asserted. Skipped is unobserved, never green.
  `gates.json` `fast` (collection smoke, 170s clamp) runs from the session-end hook; `full` is the
  definition of done and runs detached.
- An **adversary pass by a separate agent** before anything is shown to the owner; never briefed
  conservative; restore hygiene verified with `git status --porcelain` empty.
- The **tier-2 Fable judge** once per milestone before the owner merges: bounded artifact, claims
  versus evidence, verdict extracted to `.dev/reference/`.

### Adopting the Martin battery (`7.8`, M1)

Present today: pytest unit (~2,276), coverage floor 80.86 percent (measured baseline, never an
absolute), `tsc`, the two admin check scripts, the widget size gate, mypy in CI. Missing, to be
added **measure-first then ratchet** (a gate introduced at a number the repo already fails gets
commented out within a week):

| Gate | Tool | Policy on this repo |
|---|---|---|
| Mutation | `mutmut` | **Differential only**: no surviving mutant in a function the change touched. Never a global score on a 4 GB box |
| Complexity | `lizard` | Changed functions only: warn CCN over 10, fail over 15 |
| Function and module size | `lizard` | Fail functions over 60 lines; modules warn 200, fail 400, new code only |
| Import cycles | `import-linter` | Any cycle fails, no ratchet |
| Duplication | `jscpd` | Fail on a new clone, not the existing total |
| Acceptance (Gherkin) | `pytest-bdd` | Adopted only where it earns its place: the M6 and M7 acceptance scripts |
| Lint pinned | `ruff` in the venv | Closes `2.25` (today it is `uvx ruff@latest`, unreproducible offline) |

Wire as `make gates` and `make gates-fast` in the existing `apps/api/Makefile`; `gates.json` keeps
`fast` inside the 170s clamp (static + smoke) and the heavy gates live in `full`, detached.

### Per-milestone proof

| M | What proves the exit, concretely |
|---|---|
| M0 | Owner merge; `full` battery quoted green on `main` after |
| M1 | `7.7` seam: one SDK turn and one judge verdict observed through the DeepSeek endpoint. `7.8` battery: baselines measured and written as floors. E2E-3b: verdict text plus `judge_context` counters verbatim in the trace. Calibration: the three-part calibration gate: (a) the judge's bootstrapped kappa interval clears chance, (b1) the owner's own blind re-label clears chance, (b2) the paired difference does not show the owner beating the judge, **observed**. At least two rows must carry each label or no interval is a measurement; a sheet that cannot support one reports `unknown`, never pass |
| M2 | The gate observed **refusing** as well as shipping: flip each signal (below-floor eval, open critical finding) and watch the block, then the earned `ship` quoted with job ids. RTX-01 costed before the 7/7 run. Mutation proofs on the `5.17` and `1.31` fixes |
| M3 | Each `7.1`-`7.6` fix lands red-then-green. PROD-11: snippet pasted on a plain static page, zero hand-edits, working chat, screenshot plus network log. BYO proof: one turn driven by curl and EventSource using only the doc page. Size gate extended to all three shipped files; a contract test pins console snippet == API snippet |
| M4 | CI green **on a remote runner** quoted (first time, `1.1`). Staging: one grounded answer on a public URL, job id quoted. Beat: the first `eval-nightly` run row observed within 24h of deploy, and the `0.4` decision line recorded before it can fire. §3.7 unknowns closed by observation, not reading |
| M5 | Mockup gate: owner eyes, the one deliberately manual gate. Src gate: adversarial UX review on **rendered pixels** with computed contrast ratios (a code-only pass may not report PASS); admin e2e green or every red tied to an established cause; zero new tsc errors |
| M6 | The acceptance script is written **before** the run as Gherkin: grounded cited answer; refund through IDV, Actor and envelope with audit rows plus the Paystack test-mode refund reference; one **refusal observed** (over-ceiling amount denied), because an enforcement chain never seen refusing is unobserved; one manual-EFT order with POP traversing the queue to a Mellow approval; and one agent-credential approval attempt on that queue **observed refused** |
| M7 | MCP conformance: tool list typed, statelessness proven by replaying a call on a fresh connection, the approve journey exercised as MRTR. The Bantuson agent's full lifecycle recorded as MCP tool calls; live URL test same shape as M6 minus the transactional half |

The Layer 2 data-science gates bind M1 and M2 specifically: sample-size floors, `unknown` never
`pass`, seeds pinned so an eval rerun reproduces its metric, and calibration held per-provider.

## After the finish line (explicitly not blocking it)

The improvement loop: miner repair (`2.28`), labelling UI over the existing routes, tenant
migration 0016 applied, bench rows made scoreable, `verified_qa_candidates` promotion, feedback
wired past CSAT, the §6 ladder. Sequenced after launch because every one of them needs production
traffic to be worth measuring (`0.6`), and none is required by the two-URL definition above.

## Credentials

Names only, values never read. Both `.env` files carry the same 17 names.

**Present:** NEON_API_KEY, NEON_REGION, NEON_ENCRYPTION_KEY, ADMIN_KEY, ANTHROPIC_API_KEY,
VOYAGE_API_KEY, CONTROL_DB_URL, CONTROL_DB_SYNC_URL, REDIS_URL, CLERK keys (dev instance),
JWT_SECRET, UPLOADS_DIR, LOG_LEVEL, SENTRY_DSN, CLERK_JWKS_URL.

**Missing, by milestone that needs them:**

| Name | Needed by | Note |
|---|---|---|
| `PLATFORM_CREDENTIAL_KEY` | M1 (app cannot boot without it) | **[owner]** key material; generate once, never rotate casually (`1.22`) |
| DeepSeek API key + `ANTHROPIC_BASE_URL` | M1 | the DeepSeek key goes into `ANTHROPIC_API_KEY` with the base URL set (§Model provider); $5 credits available. Anthropic balance no longer on the critical path |
| `S3_UPLOADS_BUCKET`, `S3_ENDPOINT_URL`, AWS creds | M1 local (MinIO), M4 real | local seam refused in production by design |
| `EMBEDDING_PROVIDER=voyage` | M1 | default is `bedrock`, which needs AWS |
| `LANGFUSE_*` (3 names) | M4 | observability; config fields exist |
| Clerk production instance (3 names) | M4 | current keys are the dev instance (`1.20`) |
| `SMTP_HOST/FROM/OWNER_EMAIL` | M4, optional | digest only |
| **Paystack: absent everywhere** | M6 | the SA-default provider; no platform payment field exists in `config.py` by design. Needed as (a) Mellow's tenant `integration_credentials` test-mode key, (b) the env names the `7.10` live-gate test declares. The Stripe names (`STRIPE_TEST_API_KEY`, `STRIPE_TEST_CHARGE_ID`) park with the Stripe adapter for a future international tenant |

## Risks that can move the plan

- **RTX-01 red-team cost is unmeasured** (Sonnet attackers, multi-turn); cost it before M2's full
  run, not after.
- **The e2e gate's 7 timeouts have no established cause** (`1.19`); if they are not Clerk, M5
  absorbs a debugging phase.
- **Fargate has never been stood up**; PRODUCTION-READINESS §3.7 lists the operational unknowns. M4
  is the milestone most likely to grow.
- **Judge calibration can fail its own gate** (the judge's interval includes chance, or the owner beats it); if it does, M1 ends in prompt
  iteration on the judges, and nothing downstream starts until it passes.
- **The DeepSeek compatibility layer is a third party's implementation of Anthropic's format.**
  Anthropic does not support non-Claude routing, and features the CLI negotiates via beta headers
  (caching, fine-grained tool streaming) may degrade silently. The `7.7` observed-turn proof is the
  detector; if the SDK half fails it, the fallback is DeepSeek for the direct-API half only and
  Anthropic for the agent, which is the old `0.7` split.
