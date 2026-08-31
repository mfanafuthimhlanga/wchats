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

### 3.1 Infrastructure — Railway (ADR 0005); the Terraform era is deleted

`READ`, as of ticket 18 (2026-08-31):

- The stack targets Railway: api, runtime worker, pipeline worker and beat services from
  `apps/api/railway.*.toml` (one config file per service, #122), two Dockerfiles
  (`Dockerfile`, and `Dockerfile.pipeline` carrying the docling extra), Railway's managed
  Redis plugin, staging and production as two environments under a $20 hard limit.
- `deploy/terraform/`, `deploy/systemd/` and `deploy/caddy/` are **deleted** with ADR
  0005 (decision #14). Terraform was never applied (no state, no backend block, no
  `~/.aws` on this machine); git history keeps the trees.
- **No deploy workflow, by design.** Railway builds the Dockerfile on push;
  `.github/workflows/` stays `ci.yml` and `nightly.yml`, building no images.
- `RECORD` (the owed observation): a staging deploy from a push serving `/health` on a
  public URL, with worker and beat log lines showing ready — the build-log line
  `Using Detected Dockerfile` is the first checkpoint (the first attempt failed in 9s on
  an unset Root Directory; `scripts/railway_staging_wizard.sh` walks every field).
- `RECORD`: the api serves the widget bundle at `/wchats` and an empty
  `WIDGET_CDN_BASE` derives the snippet host from `PUBLIC_API_BASE` (#135,
  fix/widget-serving; mount tests and derivation pins OBSERVED green locally). The owed
  observation on staging: `curl` fetches `/wchats/widget.js` on the public URL, and the
  snippet an approve issues names that same host.
- `RECORD`: Railway's proxy idle timeout against SSE — the deleted ALB config held 4000s;
  the probe is `curl -N` surviving past 125s on a live SSE stream.
- `RECORD`: migrations at deploy. No toml carries a preDeployCommand; control-DB alembic
  and tenant migrations (#64) both run from nowhere on this stack.

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
  rows with every verdict cell empty, and the blind second pass does not exist yet. The harness gates trust on the three-part calibration gate: (a) the judge's bootstrapped kappa interval clears chance, (b1) the owner's own blind re-label clears chance, (b2) the paired difference does not show the owner beating the judge, and **has never run**. Spearman is reported only (8.2b, 8.2c, 8.2d).
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

Ordered so each step's precondition is the previous step's output. ~~**Nothing here needs AWS**;
E2E-1 to E2E-5 run against the local PostgreSQL + Redis that already exist.~~

> **CORRECTED 2026-08-13 by running it. That premise is false from E2E-2 onward.** E2E-1 needed no
> AWS and passed 12/12. **E2E-2 cannot start**: the upload route calls `storage_service.put_bytes`
> inline (`documents.py:189`), `S3_UPLOADS_BUCKET` defaults to `""`, and there is no `~/.aws` — so
> `POST /agents/{id}/documents` returns **500** `ParamValidationError: Invalid bucket name ""`.
> Ingestion depends on S3 on both the write and read sides, and `EMBEDDING_PROVIDER` defaults to
> `bedrock`. See `1.24` and `.dev/traces/260813-e2e2-ingest-blocked.md`. This is the same failure
> mode as the two claims in §5: a confident sentence that one command disproved.

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
- ~~**E2E-2 · ingest.**~~ **DONE 2026-08-13 — 8/8 assertions, and it found a production-blocking
  defect.** Took three things: the `pipeline` extra installed (docling 2.93.0 / torch 2.13.0+cpu,
  never installed before); the `S3_ENDPOINT_URL` seam + MinIO (`1.24`, owner decision, `c5c40b2`);
  and **`1.26` fixed — `chunk_documents` read `UPLOADS_DIR/...`, a local-disk path nothing writes,
  so ingestion has been broken for every uploaded file since PROD-13, in every environment.** Only
  URL sources could complete. The four ingestion-chain tests would have *passed* over it, because
  their fixture manufactures the local file production stopped creating (retro Family I, rec. 7).
  Observed after the fix: `parse → chunk(16) → metadata(16, 54 entities) → embed(16) → strategy`,
  `job.status=complete`, embeddings dim 1024, and `EXPLAIN` showing
  `Index Scan using embeddings_vector_hnsw_idx`. Also found `1.27` (S3 key case mismatch, latent) and
  `1.28` (the strategist fails and the task reports success anyway).
  **Caveat stated rather than buried: MinIO is not S3.** This proves the ingestion chain, not AWS
  compatibility, and the seam that enabled it is refused in production by design.
  Traces: `.dev/traces/260813-e2e2-ingest.md`, and `260813-e2e2-ingest-blocked.md` for the block.
- ~~**E2E-3 · one real customer turn.**~~ **DONE 2026-08-13 — `5.10` IS ANSWERED, and the answer is
  yes.** Two live turns via `POST /agents/{id}/chat`. **`agent.tool_result` events exist on the real
  stdout stream** (2 per turn), and `tool_name` resolves to `'ToolSearch'` / `'retrieve'` — never
  `'unknown'`, which is the only value the pre-`dc67d37` code could produce. Both halves of that fix
  are confirmed against the protocol the SDK actually parses, which is the one gap the 42,334
  transcript entries could not close. The agent answered **from the corpus** ("R 480/kg, excluding
  VAT"). `retrieval_metrics` got a row (`retrieved_tokens=962`).
  **It found two more defects, and the second is the important one:** `5.14` — the Auditor failed all
  three attempts (`max_tokens=512` truncating a verdict that must echo evidence), **fixed**, and the
  re-run produced `run_auditor.complete citation_spans=7 verdict=partial`, **the first valid grounding
  verdict in the platform's history**; then `5.16` — reading that verdict's reason showed the judge is
  handed `[:3]` results × `[:600]` chars ≈ 1800 chars against 962 retrieved tokens, so **it marked
  price claims unsupported because it was never shown the prices.** Every stored verdict is biased the
  same way. **`5.16` FIXED 2026-08-15** (`OBSERVED` in unit tests and 4 mutation proofs, **not** yet
  against the API): the judge now receives every retrieved chunk, untruncated, and the bound is the
  retrieval layer's own rather than a number at the call site.
  **`citation_coverage` is still NULL** — both turns drew `skipped_not_sampled` at the 0.1 sample
  rate (`5.15`), so **`5.13` is not closed.** Trace: `.dev/traces/260813-e2e3-live-turn.md`.
- **E2E-3b · one turn under the full context. NOT RUN — this is the next step.** `5.16` is fixed in
  code and **no live turn has produced a verdict that way**, so the only grounding verdict ever
  observed belongs to the capped era. Re-run E2E-3 and read `run_agent_turn.judge_context`
  (chunks / unparsed_calls / chars) beside the verdict. Set `RETRIEVAL_FAITHFULNESS_SAMPLE_RATE=1.0`
  for the run and it also settles `5.13` / `5.15`, which are waiting on one sampled turn.
  Trace when done: `.dev/traces/260815-judge-sees-agent-context.md` records the code half.
- **E2E-4 · the checklist and the gate. PARTIALLY DONE 2026-08-13 — the checklist COMPLETED for the
  first time in the project's history** (79.7s, `recommendation=block`), and `POST /approve-deployment`
  refused it with **422**. Took two fixes: `1.30` (the timeout logged an empty `str(asyncio.TimeoutError())`,
  erasing its own diagnosis) and **`1.32` — the orchestrator was never given the `submit_report` tool
  it is prompted to call**, which is audit defect **D4** surviving in a second module. The verdict is
  derived from real signals (`eval_signal=no_runs`, `red_team=no_runs`) and the orchestrator's summary
  states the measurement-honesty rule unprompted: *"'no findings' from zero tests is not a clean
  result, it is an absence of measurement."* **STILL OPEN, and the plan's wording is not satisfied:**
  the eval was never observed invoking the agent and red-team never ran 7/7 with tools — both reported
  *absent*, which is a weaker claim than the step asks for. **No `ship` verdict has ever been
  produced**, so the approve route's success path remains unexercised. Also found `1.31` (a
  crash-orphaned `running` row blocks every checklist for 60 minutes, and `acks_late` redelivery
  cannot rescue it). Trace: `.dev/traces/260813-e2e4-checklist.md`.
- ~~**E2E-5 · prove the gate refuses.**~~ **DONE 2026-08-13 — proved, then fixed, then re-proved.**
  A clean checklist froze `recommendation='ship'`; a critical finding was raised **after** it; approve
  returned **200** and logged `deployment.approved`. The API deployed an agent with an open critical
  finding against it. **Structural, not a missing `if`:** `red_team_findings` is in the *tenant* DB
  and the route only ever touched the *control* DB. Fixed with guard 2b re-reading via the **same**
  function the checklist uses, fail-closed on an unreadable connection. `5.1` closed (`69f6fab`);
  `2.19`'s gate-version general form stays open as the better long-term shape.
  **Read the retro entry for this one:** the first mutation proof of the fail-closed branch was
  **invalid** (two compensating changes, green in both states), which exposed that nothing tested that
  branch at all; and the replacement test initially passed for the wrong reason, because a 422 in a
  five-guard sequence identifies nothing without its message. Trace:
  `.dev/traces/260813-e2e5-ops15.md`.

### Phase B — the evidence the product is sold on

- **E2E-6 · calibrate the judges.** `0.1`. Capture responses (now possible — Phase A produced a real
  ingested agent), then the owner labels the sheet BINARY, then re-labels the same rows blind via `--emit-second-pass`, then run the gate. Both label passes are required: without the second there is no scale and the harness refuses rather than inventing one. **Everything in §3.4
  is downstream of this.** Blocked on `0.7 · model-provider-decision` if there is no Claude balance.
  **Do E2E-3b first.** `5.16`'s fix is unobserved against the API, and calibrating the Auditor on
  responses captured before it is confirmed live measures the cap a second time.
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
