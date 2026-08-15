# Plan: Martin battery adoption + the tsc gate deadlock (7.8, 7.9)

M1 infrastructure work, zero credentials needed. Branch: `chore/m0-gate-followups`.

## Goal

The declared `full` gate can pass end-to-end, and the Layer 1 battery exists with measured floors.

## Phases

1. **7.9** Fix `apps/admin/tests/reduced-motion.spec.ts:18` (TS2353, Playwright fixtures typing).
   Exit: `npx tsc --noEmit` observed with ZERO errors, so the `&&` chain in `gates.json` is live
   for the first time. Retires the CLAUDE.md "one known error" exception.
2. **7.8a** Add battery tools to `apps/api` dev deps: `ruff` (pinned, closes `2.25`), `lizard`,
   `import-linter`, `mutmut`. `uv sync --extra dev --extra pipeline` — BOTH extras or docling is
   removed (CLAUDE.md).
3. **7.8b** Measure baselines, write floors: lizard CCN/size report over `app/`; import-linter
   cycle contract; ruff clean or measured; mutmut configured for DIFFERENTIAL use only (document
   the command, never wire a global run). Floors are measured numbers, never aspirations.
4. **7.8c** Wire `make gates` / `make gates-fast` in `apps/api/Makefile`; `gates.json` `fast`
   stays inside the 170s clamp; heavy gates in `full`.
5. Verify pass by a separate agent; commits by the orchestrator, staged by path.

## Constraints

- 4 GB box: agents run sequentially, never two test runs at once; no agent runs the full unit
  suite (the orchestrator quotes it once at the end).
- Execution agents: Opus, small bounded briefs, no self-verification. Verification: session model.

## Risks

- mutmut on Windows is unproven here; if it cannot run, record the observation and keep it as the
  documented differential command rather than blocking the milestone.
- New floors that fail existing code get set AT the measured value, not "fixed" in this pass.
