# Trace: Martin battery adoption + tsc gate fix (7.8, 7.9)

Plan: `.dev/plans/260815-battery-and-tsc-gate.md`. Workflow: `.dev/workflows/m1-battery-and-tsc.workflow.js`
(5 sequential agents: Opus implementers, session-model verifiers). Branch: `chore/m0-gate-followups`.

## What changed

- `apps/admin/tests/reduced-motion.spec.ts` — `test.use({ contextOptions: { reducedMotion: 'reduce' } })`;
  Playwright 1.61 removed the top-level option. tsc observed: **exit 0, zero diagnostics** (twice,
  independently). The declared `full` gate's `&&` chain is live for the first time.
- `apps/api/pyproject.toml` — dev extra gains `ruff==0.16.3`, `lizard>=1.23.0`, `import-linter>=2.13`,
  `mutmut>=3.7.0`; `[tool.importlinter]` (2 contracts), `[tool.mutmut]` (dead config on native
  Windows, prepared for WSL). `uv sync --extra dev --extra pipeline` exit 0; docling survived
  (verified by import).
- `apps/api/scripts/gates.py` (new) — fast: ruff (count-pinned baseline) + lint-imports + collect-only;
  full: + lizard floors + unit suite. Stops at first nonzero exit. `make` does not exist on this box;
  the stale docker-era Makefile was left untouched.
- `.dev/gates.json` — `full`'s api segment now runs `scripts/gates.py full`. `fast` unchanged
  (cold collection alone measured 157.9s against the 170s clamp).
- CLAUDE.md — gate block updated; the "one known tsc error" exception retired.

## Observed

- Fast gate: **64.4s** (implementer) / **69.3s** (verifier), exit 0. Full: **519.0s**, exit 0,
  `2284 passed, 13 skipped`.
- Floors are measured worsts, each observed red one step tighter: `-C 35` (worst CCN 35,
  `retrieve_tool`), `-L 804`, `-a 11`, `-Tnloc 545`.
- Both import contracts observed `BROKEN` under deliberate mutation before being trusted; restores
  hash-verified.
- Vacuity probe: a file with CCN 46 + F401 turned ruff and lizard red, deleted, both green, tree clean.
- Adversary defect D1: set-based ruff baseline passed a NEW I001 in a baselined file (observed:
  3 errors reported, gate green). Fixed to count-pinned `{(path, rule): count}`; re-proved with the
  adversary's own mutation.

## Decisions and deviations

- `lint-imports` must be invoked as the console script: `python -m importlinter.cli` exits 0 without
  checking anything (observed) — a silent pass.
- Acyclicity contract scoped `skip_descendants = ["app.services"]`: four real cycles live there,
  filed as `7.12`. A depth-0 contract passes while checking nothing, so it was not shipped.
- mutmut hard-refuses on native Windows (upstream issue 397). Differential mutation remains a manual
  discipline until WSL exists.

## Not proven

- The three reduced-motion Playwright tests were not executed (need a dev server); behavioural
  equivalence of the fix is source-level verified only.
- The battery constrains changed code going forward; it says nothing about the 59 CCN>10 functions
  and 27 modules over 400 lines already present — those are the recorded baseline, not a pass.
