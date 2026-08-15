# Two v1.2s, the unbuilt protocol surface, and the loop that does not close

Two audit findings from 2026-08-15 that later sessions will otherwise re-derive: the repo contains
two different milestones both named "v1.2", only one of which was built; and the data-science loop
is honest at every joint and closed at none. `.dev/MASTERPLAN.md` is the plan built on both;
BACKLOG §7 carries the widget/endpoint defects found in the same audit.

## 1. The two v1.2s

| Name | Defined in | Content | Status |
|---|---|---|---|
| Executed v1.2 | `.planning/ROADMAP.md:277` | Gotham console + agent management (Phases 20, 21, 23) | Built, `gaps_found` |
| PRD v1.2 | `Post-M10-PRD.md:20` | Agent-native protocol surface: A2A endpoint, agent card, JSON-RPC, counterparty auth/quotas, MCP provisioning, `wchats` CLI, Skill | **Never built. 15 of 15 deliverables at zero. No phase was ever created for it** |

They share a version number and nothing else. Evidence that the protocol surface is absent:

- Every `a2a` occurrence in `apps/api/app` is metadata: two dataclass fields and `to_a2a_skill()`
  in `transactional/registry.py`, whose own comment reads "No network, no server — metadata only".
- `main.py:167-184` registers 17 routers; none is A2A/JSON-RPC. No `/.well-known/*` route exists.
- Greps for `counterparty`, quota, registry-listing code: zero hits in `app/` and `tests/`.
- No CLI anywhere: no `[project.scripts]` in `pyproject.toml`, no `bin` in any `package.json`,
  no `apps/cli`. "CLI" in dev logs means the Claude Agent SDK subprocess or alembic.
- Every `create_sdk_mcp_server` call is an in-process SDK tool server (consumption side), not the
  PRD's standalone control-plane MCP service.
- The repo states it about itself: `docs/guides/tool-author-guide.md:138` "No A2A endpoint exists
  yet"; `REQUIREMENTS.md:385` still files the surface as future; TXN-05, the one ticked A2A
  requirement, is defined as shape-compatibility "without exposing any A2A surface".
- `docs/adr/0002` (CLI + Skill + MCP, "ship together in v1.2") is still `Status: Proposed`.

Vocabulary now pinned in MASTERPLAN: **A2A = runtime protocol** (agents calling a deployed agent;
out of scope until a counterparty exists) vs **MCP provisioning = control plane** (managing W Chats
from developer tools; MASTERPLAN M7, on the stateless MCP 2026-07-28 spec, CLI dropped).

## 2. The loop that does not close

The wiring status of each stage of the improvement loop, as read from code (routes, consumers,
schedules), not from intent. Forward arrows work; backward arrows do not.

| Stage | Status | Anchor |
|---|---|---|
| Soul edit auto-versions | Works, automatic | `agents.py:206` → `prompt_version_service.create_version_from_agent` |
| Rollback / canary | One-click, append-only, weighted serving | `PromptVersionPanel.tsx`, `prompt_version_service.py:178,249` |
| Nightly eval / weekly red team | Scheduled in code, **never fired**: no beat process in any deploy artifact, and no deploy workflow at all | `celery_app.py:207-231`; `deploy/terraform/fargate.tf` (3 services, no beat) |
| Manual eval / red team / gate | One-click, honest, fail-closed | `evals.py:635`, `red_team.py:297`, `deployment.py:430` |
| Per-turn judges | Automatic post-hoc; verdicts reach `job_events`; **no console surface renders them**; all judges uncalibrated (`0.1`) | `agent.py:592-597` |
| Scenario mining | **Never produced a row**: queries `jobs.conversation_id`, a column no migration creates, inside a logged-and-swallowed except | `scenario_service.py:424` (`2.28`) |
| Labelling | API-only (no console UI), and 503 until tenant migration 0016 is applied anywhere | `evals.py:1016,1219` (`2.4`) |
| Bench "file this failure" | Writes `reference_answer=''`, which the eval selector excludes by construction; the click is inert | `bench.py:94,218` vs `eval.py:830-844` |
| Feedback | Becomes CSAT/thumbs-down KPIs, nothing else | `metrics_service.py:97` |
| Corpus improvement | `verified_qa_candidates` has no reader; `promote_to_verified_qa` has no caller and is flag-disabled by design | `eval_service.py:237` "LOCK ZERO, NO CALLER" |

Consequence: there is no automated path from a bad verdict to changed agent behaviour; every
improvement is a human editing the soul. The two cheapest structural fixes are a deployed beat
service (MASTERPLAN M4) and the miner column fix plus a labelling surface (deliberately sequenced
after launch; they need production traffic to be worth measuring, `0.6`).

## 3. Credential surface (names only)

Both `.env` files carry the same 17 names. Missing and needed on the path to production:
`PLATFORM_CREDENTIAL_KEY` (boot blocker, `1.22`), S3/AWS names + `EMBEDDING_PROVIDER=voyage`,
Langfuse, production-instance Clerk keys (present ones are the dev instance, `1.20`), SMTP
(optional), and **Stripe, absent everywhere by design**: no platform Stripe field exists in
`config.py`; it enters as a tenant `integration_credentials` restricted key plus
`STRIPE_TEST_API_KEY`/`STRIPE_TEST_CHARGE_ID` for the never-yet-run live gate
(`test_stripe_live.py`).
