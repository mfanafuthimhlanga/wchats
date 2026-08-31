# 260831 · mcp-server (ticket 19, #56, PR #137)

Branch `feat/mcp-server` off `b18b41a`. Five commits: ADR 0004, the golden path, the MCP
server, the connect guide. Draft PR #137, closes #56 on merge.

## What changed

- `docs/adr/0004-mcp-auth-tenant-key.md`: tenant key as static bearer, no OAuth. Committed
  before the code it justifies.
- `apps/api/app/api/mcp.py`: one stateless POST `/mcp`, protocol revision 2026-07-28.
  `tools/call` replays the tool's route in-process (httpx ASGITransport on `request.app`)
  with the key forwarded as `X-API-Key`. No business logic in the server.
- `apps/api/app/api/v1/evals.py`: `POST /agents/{agent_id}/golden-scenarios`. Idempotent by
  question text, one transaction per batch, provenance derived from the credential kind.
  `_LEDGER_SQL`'s authored bucket now counts `source='authored'`.
- `apps/api/app/services/scenario_service.py`: `insert_authored_golden_scenario`, the one
  writer of `dataset='golden'`. Names no label column; `label_trust_tier` stays NULL.
- `apps/api/alembic_tenant/versions/0024_eval_scenarios_authored_source.py`: source CHECK
  v3 admits `'authored'`, 0011's discover-and-drop shape.
- `apps/api/app/schemas/eval.py` (new), `apps/api/app/main.py` (registration),
  `docs/guides/mcp.md` (the one-liner and the lifecycle).

## Decisions

- In-process ASGI replay over direct service dispatch. The route is the behaviour: auth,
  IDOR, validation and status codes are reused, and the route tests cover the tools. Direct
  dispatch would have copied each route's guards into the server, which is the business
  logic the decision forbids.
- Eighteen tools, not fourteen. #57 forbids any lifecycle step outside MCP and the golden
  set is a mandatory Provisioning step, so registration must be a tool; no tool without a
  route, so the route was built. The tier-1 review then showed the poll loop cannot close
  without the run readers (trigger responses carry task ids, not run ids), so
  `list_red_team_runs`, `list_checklist_runs` and `get_checklist_run` wrap their existing
  routes too. Decision #10's fourteen are pinned by test; the deviation is named in
  PR #137 and merging ratifies it.
- Golden provenance is a record, not a tier. A machine credential cannot assert which human
  wrote a pair (label_service R1 to R4), so the rows carry `source='authored'` plus a
  provenance tag derived server-side, and no `authored_by` field is accepted. The ship rule
  gates golden on `dataset` alone, so the gate holds with the tier NULL.
- Hand-rolled protocol shell, no `mcp` SDK dependency. The 2026-07-28 revision is stateless;
  the required surface (headers, discover, list, call, 202 notifications, 405s) fits one
  small module, and the repo pattern is owned loops (ADR 0008). The normative requirements
  are quoted in `.dev/reference/260822-mcp-spec-requirements.md` on `research/mcp-spec`.

## Observed

- New tests: 27 passed in 61.6s (`test_mcp_routes.py`, `test_golden_scenarios_route.py`).
- Mutation proofs: the X-API-Key forward dropped, 1 failed in 43.8s, restored 1 passed in
  91.6s. The empty-pair guard disarmed, 2 failed in 48.7s, restored 3 passed in 46.3s.
- Migration 0024 round trip on `wchats_tenant_probe`: `authored` inserted at head; `bogus`
  refused (`eval_scenarios_source_check_v3`); downgrade to 0023 refused `authored`
  (`..._v2`); re-upgrade inserted again.
- `gates.py fast`: passed in 79.8s, 4106 tests collected. `gates.py static`: passed in
  22.9s, source assertions clean over 233 files.
- mypy: 153 errors in 13 files, equal to main's 153; none in the new code.
- `test_eval_routes.py` after the ledger change: 46 passed in 56.7s.

## gates.py full and the tier-1 review

- `gates.py full` at `ada75c9`: 1 failed, 4092 passed, 13 skipped in 1004s. The one
  failure was the tenant head pin (`test_0023_is_the_tenant_head`), which exists to force
  a conscious update when a migration lands; per its own docstring the assertion moved
  into a new `test_migration_tenant_0024.py` (identity, parentage, head, CHECK
  containment), 36 passing across both migration files.
- The tier-1 review returned 20 findings. Applied: the three run readers above (the poll
  loop could not close without them), route crashes mapped to isError results instead of
  raw 500s (`raise_app_exceptions=False`), a 409 naming #64's class when a pre-0024
  tenant DB refuses authored rows, legacy-version requests freed from the modern header
  contract, upload entry validation, a jsonrpc field check with the id echoed, the
  notification 202 moved ahead of header judgement, resultType on ping and discover,
  structlog contextvar save/restore around the in-process hop, provenance colon
  sanitising, the golden writer added to the R3 name-scan loop in
  `test_label_provenance.py`, corrected 0024 and label_service prose, an em-dash sweep,
  and a wiring test driving all eighteen tools through their real routes plus twelve
  protocol-edge tests.
- Declined, with reasons on PR #137: Sentry local-variable capture (the default scrubber
  covers api_key-named locals), auth resolving before Origin validation, and the ledger
  bucket's name mixing authored with generated rows.
- After the fixes: 108 passed across the MCP, golden and label-provenance suites; ruff
  clean; static gates green; zero em or en dashes on the branch's added lines.
