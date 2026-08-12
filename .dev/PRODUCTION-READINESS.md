# PRODUCTION READINESS — every gap, and the end-to-end validation plan

**Written 2026-08-12.** Branch `chore/local-postgres`, tip `62e67cc`, unmerged.

**What this file is for:** the next session begins **project-wide end-to-end validation**. This is
the gap register it works from, and §4 is the ordered plan.

**Rule this file is written under:** every claim is marked `OBSERVED` (I ran it, this session, and
the output is quoted or summarised), `READ` (established from source/config I opened, not executed),
or `RECORD` (taken from `.dev/` or `.planning/` and **not** re-verified). Two claims I made verbally
earlier today turned out to be `RECORD` repeated as fact and were wrong — see §5. Do not promote a
`RECORD` line to a decision without re-checking it.

---

## 1. What the product claims, because that is the bar

A non-technical business owner completes **signup → ingest → deploy** and gets a customer service
agent that is **defensible**: grounded in their own corpus, evaluated, and red-teamed *before* it
goes live. Per-tenant Neon projects. Preact widget on the customer's site; Next.js admin console for
the owner. Transactional tools (refunds, orders) behind capability envelopes, an identity gate and an
Actor gate.

**The differentiator is the evidence, not the chat.** That matters for prioritisation: a shipped
agent whose defensibility has never been demonstrated is not this product.

---

## 2. Status of the things that ARE built

`OBSERVED 2026-08-12`, on this machine, by running them:

| Gate | Result |
|---|---|
| backend unit (`pytest tests/unit`, docling modules excluded per CLAUDE.md) | **2202 passed, 13 skipped, 0 failed** — 545s |
| backend integration, flag OFF (the CLAUDE.md gate) | **15 passed, 47 skipped, 0 failed** — 281s |
| backend integration, flag ON (whole directory, nothing deselected) | **40 passed, 24 skipped, 2 failed** — 473s |
| admin `npx tsc --noEmit` | **1 error, and it is the one CLAUDE.md documents** (`tests/reduced-motion.spec.ts:18`). Zero new. |
| admin `check:no-dusk-tokens` | **PASS** |
| admin `check:ops-room-wiring` | **PASS** — "every region calls its own Phase 21 endpoint" |
| admin `test:unit` | **45 passed** (4.9s) |
| widget `build` + `check-size.mjs` | **PASS — 8968 bytes gzipped**, against a 20480 ceiling |
| admin `test:e2e` | **7 failed, 128 passed — 35.9 min. NOT GREEN.** See §3.8. |

The two integration failures are **both external and neither is a code defect**: `5.6 ·
tightened-ceiling-audit-row` (an owner decision) and `ver01` (needs a real `ANTHROPIC_API_KEY` in
`os.environ`).

**The API surface is complete and the chain exists** `READ`: `POST /tenants` → `POST /agents` →
`POST /agents/{id}/documents` (202, dispatches to Celery) → `GET /jobs/{id}/events` (SSE) →
`POST /agents/{id}/checklist-runs` → `POST /agents/{id}/approve-deployment` → widget serves at
`/widget/*`. 18 routers registered in `main.py`.

**So the engineering is substantially done.** Everything below is about evidence and environment,
not about missing features.

---

## 3. THE GAPS

### 3.1 Infrastructure — fully designed, never stood up

`READ`, from `deploy/`:

- A complete AWS serving substrate exists in `deploy/terraform/`: VPC + NAT + IGW, ALB with 2
  listeners, CloudFront + OAC, 2× ECR, ECS Fargate cluster with **3 services and 3 task
  definitions**, ElastiCache replication group (Redis), Route53, S3, Secrets Manager, IAM roles and
  policies, 3 CloudWatch log groups. Plus `deploy/systemd/` units and a `deploy/caddy/Caddyfile` from
  the earlier single-box design.
- **`main.tf` declares no `backend` block**, so Terraform state is local — and **no `.tfstate` and no
  `.terraform/` exist anywhere in the repo.** Terraform has therefore **never been applied**.
- **There is no `~/.aws` directory on this machine**, so the AWS CLI has never been configured and
  the ECR build/push steps in the runbook have never been started.
- **No deploy workflow.** `.github/workflows/` contains exactly `ci.yml` and `nightly.yml`.
  Deployment is a manual runbook, executed by a human, and has not been executed.

**Prerequisites from `deploy/README.md` that are unmet or unconfirmed:** an AWS account with billing
enabled; a Route53 hosted zone (the runbook assumes `wchats.app`); **two** ACM certificates, one of
which *must* be in `us-east-1` for CloudFront; Bedrock model access granted for
`amazon.titan-embed-text-v2:0`; Docker with buildx, building `--platform linux/amd64` (the runbook
warns that building on Windows without it produces an image Fargate cannot run).

### 3.2 Configuration — the example env cannot boot the app

`OBSERVED`: `app/core/config.py` declares **63 settings fields**, of which **10 have no default and
must be supplied**:

```
NEON_API_KEY  NEON_ENCRYPTION_KEY  PLATFORM_CREDENTIAL_KEY  CONTROL_DB_URL
CONTROL_DB_SYNC_URL  ADMIN_KEY  ANTHROPIC_API_KEY  VOYAGE_API_KEY
JWT_SECRET  CLERK_WEBHOOK_SIGNING_SECRET
```

`.env.example` lists **10 keys total and covers only 5 of those 10**. Missing:
**`PLATFORM_CREDENTIAL_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `JWT_SECRET`,
`CLERK_WEBHOOK_SIGNING_SECRET`.** A new environment configured from the example **fails to start**,
and nothing in the repo lists the full required set in one place.

**CLOSED 2026-08-12 (E2E-0). The paragraph above is right about the root file and wrong about the
repo, and the correction is the finding.** `OBSERVED` by a test that derives the required set from
`Settings.model_fields` rather than from a copied list:

- **There are TWO tracked examples, not one.** The 5-missing count describes the **repo-root**
  `.env.example`. **`apps/api/.env.example` was missing 3** — `PLATFORM_CREDENTIAL_KEY`,
  `JWT_SECRET`, `CLERK_WEBHOOK_SIGNING_SECRET` — and two of those three were **present but commented
  out**, which is worse than absent: it names a key the reader can see in the file while dotenv
  ignores it, so the ValidationError looks like a bug rather than a missing line.
- **Which file loads depends on which files exist.** `_find_env_file()` walks up from
  `app/core/config.py` and stops at the first `.env`, so on a fresh clone the **root** file is what
  `Settings` reads, and once `apps/api/.env` exists it wins permanently. Both examples therefore had
  to be complete; both now are.
- Both regenerated, all 10 required keys uncommented, with generation commands for the four that are
  generated rather than fetched. `apps/api/.env.example` carries the full 63-field operational
  surface with its real defaults; the root file carries the 10 plus a pointer.
- Pinned by `apps/api/tests/unit/test_env_example_covers_required_settings.py`, which fails when a
  no-default field is added to `Settings` without landing in both examples, and treats a
  commented-out key as absent. Four mutation proofs, `.dev/reference/260812-e2e0-mutation-proofs.md`.

### 3.3 Authentication — running on Clerk development keys

`OBSERVED` in the e2e run's browser console:

> Clerk has been loaded with development keys. Development instances have strict usage limits and
> should not be used when deploying your application to production.

A production Clerk instance, production keys, and `CLERK_WEBHOOK_SIGNING_SECRET` from that instance
are required. Note the coupling: `4.7 · labelled-by-not-a-person` records that the human-label path
refuses anything but a Clerk JWT, so Clerk is load-bearing for more than sign-in.

### 3.4 The defensibility claim has never been demonstrated once

This is the real gap. Everything here is `RECORD` unless marked otherwise.

- **Every LLM judge is uncalibrated.** `apps/api/tests/evals/calibration/human_scores.csv` holds 10
  rows with every score cell empty. The harness gates trust at Spearman ≥ 0.75 and **has never run**.
  This covers Gatekeeper, Auditor, Strategist, `classify_severity`, and **the Actor gate that runs
  before money moves**. (`0.1 · score-judge-calibration`)
- **No eval has ever run end to end against a real agent.** (`2.14`, `3.6`)
- **The grounding judge was fed an empty context on every turn from 2026-05-16 until 2026-08-11.**
  `OBSERVED` this session: the `ToolResultBlock` branch was unreachable, so `retrieved_context_json`
  was `"[]"` on every turn the platform has ever served. Fixed in `dc67d37`; **not yet confirmed
  against a live SDK turn** (`5.10 · live-turn-unbought`, ~$0.12). (`5.11 · grounding-verdicts-void`)
- **`retrieval_metrics` is empty by construction** — `citation_coverage` and retrieval faithfulness
  have never been computed for any turn. (`5.13 · retrieval-metrics-empty`)
- **The blast-radius section of the deploy checklist never evaluated real exposure** until
  2026-08-12 (`OBSERVED`: every key was `None` because the query raised and was caught).
  (`~~1.14~~ · paramstyle-collision`)
- **The weekly digest has never sent** — the INSERT raised on every run since OPS-04. Fixed
  2026-08-12; never observed sending. (`1.17 · digest-can-now-send`)
- **Red team:** the confused-deputy probe was a vacuous pass over an empty transcript until
  2026-08-11 and **still has not run for real**. Pre-P4 runs remain shippable evidence (`3.1`), three
  deterministic vectors still swallow exceptions and report clean (`3.8`).
- **Zero production traffic has ever existed**, which is why `0.6 · size-labelling-loop` exists.

### 3.5 Verification — CI has never run the tests

- **Unit and Integration jobs have never executed on a runner.** Blocked on the GitHub Actions
  billing cap; two runs were cancelled at 15m03s and 15m02s with every job killed, Lint included.
  (`0.3 · actions-billing-cap`) `RECORD`
- **The frontend gates are not in `ci.yml` at all** (`1.4 · frontend-gates-absent`) — they pass here
  (§2) and no runner has ever checked them.
- **`--cov-fail-under=80` has never executed in this project's history.** Local measurement is
  80.86%, a 0.86-point margin. (`1.2 · coverage-never-run`) `RECORD`
- **`nightly.yml` E2E is failing, pre-existing, never diagnosed** (`1.5`). `RECORD`
- Everything green is green on one 4 GB Windows machine, single-threaded.

### 3.6 Known code gaps that touch the deploy path

- **`5.1 · ops15-server-gap`** — `POST /approve-deployment` gates on the checklist run's **frozen**
  `recommendation` and consults live `open_findings` **nowhere**. `OBSERVED` today: the route reads
  `run.status`, `run.recommendation`, warnings acknowledgement, the stored report's `agent_invoked`,
  and the live envelope hash — and no red-team query at all. So a critical finding raised *after* a
  clean checklist is still accepted by the API. Fails closed, so not a hole; it is the last v1.2
  milestone blocker that is actual code. `2.19 · no-gate-version-on-runs` is the general form.
- **`5.6 · tightened-ceiling-audit-row`** — an owner decision, and the last non-credential
  integration failure.
- **Milestone v1.2 is not closeable**: 27/29 requirements, 4/5 integration flows, per the 2026-08-04
  audit. `REQUIREMENTS.md` traceability is stale — `WIRE-01..05` has zero rows and the `OPS-01..06`
  collision is live. (`5.2 · requirements-traceability`) `RECORD — not re-audited this session`

### 3.8 The admin e2e gate is stale in CLAUDE.md and is not green

`OBSERVED 2026-08-12` — the first time this session ran it:

- **CLAUDE.md documents `npm run test:e2e` as "113 across three viewports". Playwright reports
  `Running 135 tests using 2 workers`.** The documented count is wrong.
- **Result: `7 failed, 128 passed (35.9m)`.** The gate CLAUDE.md lists under Definition of Done does
  not currently pass.
- **All 7 are timeouts, not assertion failures** — `Test timeout of 90000ms exceeded` on
  `page.goto` or `page.waitForLoadState('networkidle')`. 5 are `a11y.spec.ts` on `desktop-1440`
  (`/sign-in`, `/agents/new`, `/agents/demo-1/soul`, `/deploy`, `/settings`); 2 are `smoke.spec.ts`
  on `tablet-900` (`/agents/demo-1/ingest`, `/settings`). Zero axe violations were reported — the
  pages never finished loading, so the assertions never ran.
- **Cause NOT established.** The same log is full of Clerk development-instance failures —
  `ClerkRuntimeError: Failed to load Clerk JS`, `code="failed_to_load_clerk_js"`, `[Clerk UI]
  Component renderer did not mount within 10s`, and the strict-usage-limits warning on every page.
  A page whose auth script never loads plausibly never reaches `networkidle`. That is a hypothesis;
  it is **not** proven, and it must not be assumed before §3.3 is fixed. It may equally be a 4 GB
  machine running two Playwright workers for 36 minutes.
- **Also unknown:** whether this is new. The e2e gate has never run in CI (`1.4`) and there is no
  prior recorded local run, so there is no baseline to compare against. Treat the 128/7 as the
  first measurement, not as a regression.

**This is the single most likely thing to waste the next session's time**, because it looks like a
product bug and may be an environment artifact. Settle §3.3 (production Clerk keys, or a test double
for Clerk) before spending any time debugging the seven.

### 3.7 Operational unknowns I have NOT established

Stated as unknowns rather than guessed:

- Whether platform billing for tenants exists at all (the Stripe code present is for the *tenant's*
  transactional tools — refunds and orders — not for charging tenants for the product).
- Backup, restore and disaster recovery for the control DB and per-tenant Neon projects.
- Rate limiting and abuse controls at the public edge (the widget is public by design).
- Log retention, PII handling in logs, and data deletion / GDPR-style tenant offboarding.
- Runtime cost per tenant per month, and what the per-tenant Neon project count does to the Neon bill.
- On-call, alerting destinations, and who is paged. `alerts` tables exist; delivery is unverified.
- Legal: terms, privacy policy, DPA — none seen in the repo.

---

## 4. THE END-TO-END VALIDATION PLAN

Ordered so each step's precondition is the previous step's output. **Nothing here needs AWS**;
E2E-1 to E2E-5 run against the local PostgreSQL + Redis that already exist.

**Standing rule for the whole plan:** a skip is not a pass and an unread log is not a green.
Record the observed output for each step, not the intention.

### Phase A — make the local chain real (no cloud, no spend beyond model calls)

- ~~**E2E-0 · fix the boot contract.**~~ **DONE 2026-08-12.** Both examples (root and `apps/api/`,
  and there being two is itself the finding) now carry all 10 mandatory fields, pinned by a test
  that derives the set from `Settings.model_fields`. See §3.2 for what the closure corrected.
  **Not yet proven: that a fresh environment built from the example actually boots** — the test
  asserts the keys are named, not that filling them in starts the app. E2E-1 is the first step that
  would show that, and it is the next move.
- ~~**E2E-1 · signup → agent.**~~ **DONE 2026-08-12 — 12/12 assertions passed, first run ever.**
  `POST /tenants` (201) → `POST /agents` (202) → `provision_neon` → `apply_migrations`, 46 seconds
  end to end. Real Neon project **`mute-dream-53534177`** (`aws-us-east-1`), pooled and direct
  connection strings both stored as Fernet ciphertext and both observed to decrypt to real, *distinct*
  endpoints (the pooler/direct split RESEARCH.md Pitfall 1 requires); tenant chain at head **`0016`**,
  24 tables, `embeddings_vector_hnsw_idx` present; all 6 lifecycle events in order.
  **The project is deliberately left running for E2E-2/3/4 — delete by id only.**
  Trace: `.dev/traces/260812-e2e1-signup-to-agent.md`. **Three findings, all filed:** the app does
  not boot here at all (`1.22`, `PLATFORM_CREDENTIAL_KEY` absent from both real env files); the unit
  suite structurally cannot see that because conftest manufactures the key (`1.23`); and `.env`
  points at the **production** control DB and Redis, so every process ran under a localhost overlay
  with a pre-flight abort. `1.20` also confirmed from the backend side.
- **E2E-2 · ingest.** `POST /agents/{id}/documents` with a real PDF, follow `GET /jobs/{id}/events`
  to completion, assert chunks and embeddings land in the tenant DB and the HNSW index is used.
  **Note:** `docling` is not installed here (`1.10`, `4.4`), so this is the first step that may need
  the `pipeline` extra.
- **E2E-3 · one real customer turn.** Drive the widget path end to end. **This is also `5.10`** —
  it is the run that confirms the `ToolResultBlock` fix against the real stdout stream, so do it
  with the SDK turn and capture the raw message types. Assert: `agent.tool_result` events exist,
  `retrieved_context_json` is non-empty, `retrieval_metrics` gets a row.
- **E2E-4 · the checklist and the gate.** `POST /checklist-runs` on that agent. Assert the eval
  actually invokes the agent, red-team runs 7/7 with tools, and the gate's verdict is derived from
  real signals. Then `POST /approve-deployment`.
- **E2E-5 · prove the gate refuses.** Raise a critical finding *after* a clean checklist and confirm
  the API still accepts the deploy — that is `5.1`, and E2E-5 is the test that makes it undeniable.
  Fix it, then re-run.

### Phase B — the evidence the product is sold on

- **E2E-6 · calibrate the judges.** `0.1`. Capture responses (now possible — Phase A produced a real
  ingested agent), then the owner scores 10 rows, then run the Spearman gate. **Everything in §3.4
  is downstream of this.** Blocked on `0.7 · model-provider-decision` if there is no Claude balance.
- **E2E-7 · one full nightly.** Let `eval-nightly` and the red-team beat run once against a real
  tenant, deliberately and watched — **after** `0.4 · eval-pii-egress` is decided, because that is
  when customer rows first reach the Ragas judge.

### Phase C — the cloud

- **E2E-8 · CI first, not last.** Clear `0.3`, get Unit + Integration green on a runner, add the
  five frontend gates to `ci.yml` (`1.4`), and let `--cov-fail-under=80` execute for the first time.
  Report the real number; do not lower the threshold.
- **E2E-9 · stand up the infrastructure.** AWS account, domain, two ACM certs, Bedrock access, then
  `terraform apply`. **Add a remote backend before the first apply** — with local state, the first
  laptop failure loses the ability to manage the stack.
- **E2E-10 · production Clerk**, production secrets in Secrets Manager, and a smoke test of the whole
  chain against the deployed environment.
- **E2E-11 · close §3.7.** Backups, rate limiting, log/PII retention, cost model, alerting
  destinations, legal docs. None of these is code this repo has started.

### The honest sequencing note

**Phase A is worth doing before Phase C even though Phase C looks like "the deployment".** Every
defect found this week was found by running something that had never run, and Phase A is the first
time this system would be run as a system. Standing the AWS stack up first would move that discovery
into an environment that costs money per hour and has a customer attached.

---

## 5. Two claims I made today that were wrong, kept as a warning

Both were `RECORD` repeated with the confidence of `OBSERVED`. Both took one command to disprove.

1. **"Nothing is deployed and there is no deploy path."** There is a complete Terraform stack
   (§3.1). What is true is narrower: it has never been *applied*.
2. **"`run_weekly_digest` will send real email to real recipients from a scheduled beat."** No beat
   worker exists; the recipient is `settings.OWNER_EMAIL`, a single address, not customers; and
   `send_digest_email` returns early unless three SMTP settings are all present. I also cited the
   cron entry at `celery_app.py:216` as though a schedule with no process to run it were
   operational.

The failure mode in both: quoting the record as observation, and leading with the scariest true-ish
sentence rather than the load-bearing one. §2's table exists to make the difference explicit.
