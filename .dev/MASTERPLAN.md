# MASTERPLAN — from here to two live agents

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

- E2E-3b: one live customer turn, `RETRIEVAL_FAITHFULNESS_SAMPLE_RATE=1.0`, read the verdict and
  `judge_context` counters. Settles `5.13`, `5.15`; first verdict under the `5.16` fix.
- E2E-6: capture the 20 calibration responses (`0.1` prerequisite), owner scores
  `human_scores.csv`, run the Spearman >= 0.75 gate.
- E2E-7 per PRODUCTION-READINESS §4.
- **[owner]** `0.7` model-provider decision resolves to: fund the Anthropic key. The customer agent
  cannot route elsewhere (SDK constraint, verified in `260811-agent-sdk-provider-surface`), so
  credits are required for production regardless; a DeepSeek judge split stays available later but
  is not on the critical path.

**Exit:** a grounding verdict from a live turn whose reason cites provenance, and calibrated judges
with the Spearman number recorded.

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

### M3 — make the widget and the agent endpoint products

The two runtime surfaces both customers will use. All defects here are located (rows `7.1`-`7.6`).

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

- **[owner]** Stripe test-mode account + restricted key for Mellow; seeded as the tenant's
  `integration_credentials` row through the console (INT-01 path). Set `STRIPE_TEST_MODE_ENABLED`
  ceremony only for the live-gate test.
- Provision the Mellow tenant and agent entirely via UI. Ingest the Mellow corpus (product and
  policy docs from `../mellows-earth-elements`). Eval, red team, checklist, approve: the gate earns
  its second ship, this time on a real business corpus.
- Embed the widget on the Mellow Vercel site; run the live test: grounded product answers plus one
  transactional action (refund or order via Stripe test mode) passing the IDV + Actor + envelope
  chain. First live provider call in the project's history (`test_stripe_live` gate finally
  exercised).

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
| Anthropic credit balance | M1 | key name present; balance is the `0.7` decision |
| `S3_UPLOADS_BUCKET`, `S3_ENDPOINT_URL`, AWS creds | M1 local (MinIO), M4 real | local seam refused in production by design |
| `EMBEDDING_PROVIDER=voyage` | M1 | default is `bedrock`, which needs AWS |
| `LANGFUSE_*` (3 names) | M4 | observability; config fields exist |
| Clerk production instance (3 names) | M4 | current keys are the dev instance (`1.20`) |
| `SMTP_HOST/FROM/OWNER_EMAIL` | M4, optional | digest only |
| **Stripe: absent everywhere** | M6 | no platform Stripe field exists in `config.py` by design; needed as (a) Mellow's tenant `integration_credentials` restricted key, (b) `STRIPE_TEST_API_KEY` + `STRIPE_TEST_CHARGE_ID` for the live gate test |

## Risks that can move the plan

- **RTX-01 red-team cost is unmeasured** (Sonnet attackers, multi-turn); cost it before M2's full
  run, not after.
- **The e2e gate's 7 timeouts have no established cause** (`1.19`); if they are not Clerk, M5
  absorbs a debugging phase.
- **Fargate has never been stood up**; PRODUCTION-READINESS §3.7 lists the operational unknowns. M4
  is the milestone most likely to grow.
- **Judge calibration can fail its own gate** (Spearman < 0.75); if it does, M1 ends in prompt
  iteration on the judges, and nothing downstream starts until it passes.
