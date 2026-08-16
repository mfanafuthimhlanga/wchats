# Trace: the DeepSeek provider seam, proven (7.7)

Plan context: MASTERPLAN M1 step 0. Branch: `chore/m0-gate-followups`.

## What changed

- Both real `.env` files: `ANTHROPIC_API_KEY` carries the DeepSeek key, `ANTHROPIC_BASE_URL`
  set, Anthropic key preserved as a commented fallback, `PLATFORM_CREDENTIAL_KEY` generated once
  and identical in both files (closes `1.22`).
- `scripts/start_native.ps1` exports `apps/api/.env` when it exists instead of the root file,
  matching `_find_env_file` precedence. The old behaviour was the `1.28` split-brain in a provider
  edition: workers would export the Anthropic key while Settings loaded the DeepSeek one.
- Six forced-tool call sites gain `thinking={"type": "disabled"}` (validation_service x3,
  actor_seam, red_team_service.classify_severity, scenario_service.generate_scenarios_from_chunks),
  each pinned by a kwarg assertion on the call the provider receives; one mutation proof observed
  red then green. Plain-text call sites (strategy, retrieval, red_team weekly) need no change.

## Observed

- Plain call: `model=deepseek-v4-flash`, text `SEAM-OK`. The `claude-haiku-4-5` alias auto-maps.
- Forced tool_choice WITHOUT the fix: HTTP 400 `Thinking mode does not support this tool_choice`.
  The endpoint routes claude-* aliases to flash in thinking mode, and every judge is a forced
  tool call, so the whole judge stack was dead on this provider until the fix.
- Forced tool_choice WITH `thinking={"type":"disabled"}`: `stop=tool_use`, verdict
  `{grounded, 1.0}` with sound reasoning. Valid and a no-op on the real Anthropic API, so the
  change is provider-neutral.
- Agent SDK turn: `AssistantMessage model=deepseek-v4-flash`, result `SDK-SEAM-OK`, 1 turn,
  `subtype=success`. The spawned CLI honoured the env pair.
- `total_cost_usd=0.243365` for that trivial turn: the CLI prices against Anthropic tables.
  Filed as `7.13` — budget guards over-throttle (fail-closed) but the numbers are fiction.
- Targeted tests `89 passed in 16.41s`; fast gate `97.6s`, exit 0, ruff baseline unchanged.

## Not proven

- `tool_choice={"type":"any"}` returned an empty tool input under `max_tokens` pressure in one
  probe; no production site uses `any`, left unpursued.
- Verdict *quality* on DeepSeek is one good sample, not a calibration. E2E-6's Spearman gate is
  where that gets measured, and it holds per-provider.
