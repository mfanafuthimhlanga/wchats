# ARCHIVE — frozen 2026-08-05

This directory holds 544 GSD planning artifacts covering Phases 1–23 and milestones v1.0–v1.2.

**It is frozen. Do not add to it. Do not update it.** Git preserves the full history; treat this as
reference of last resort.

Active development artifacts now live in **`.dev/`** — see `CLAUDE.md` for the convention:

| Was here | Is now |
|---|---|
| `phases/NN-*/NN-MM-PLAN.md` | `.dev/plans/YYMMDD-<slug>.md` |
| `phases/NN-*/NN-MM-SUMMARY.md` | `.dev/traces/YYMMDD-<slug>.md` |
| `phases/NN-*/NN-{RESEARCH,PATTERNS,UI-SPEC}.md` | `.dev/reference/<topic>.md` |
| `STATE.md` (597 lines) | `.dev/HANDOFF.md` (terse) |
| lessons scattered across SUMMARYs | `.dev/retro.md` |
| — (no equivalent) | `.dev/workflows/<name>.workflow.js` |

## Still authoritative inside this archive

- **`PROJECT.md`** — product context, core value, requirements, decision log.
- **`REQUIREMENTS.md`** — requirement IDs and traceability, with **two known defects**: `WIRE-01..05`
  has zero rows, and the `OPS-01..06` collision is live (lines 274-279 hold them as Phase 10 / M10
  `Pending` while line 415 falsely ticks them complete via `OPS-01..16 | Phase 21`).
- **`v1.2-MILESTONE-AUDIT.md`** — the 2026-08-04 audit, `gaps_found`, and the 2026-08-05 closure note.
- **Each phase's `SECURITY.md`** — threat registers, still the record of what was assessed.

## Why it was frozen rather than migrated

The GSD artifacts are narrative-heavy by design — a single plan SUMMARY routinely runs 600 words.
The `.dev` format is deliberately terse. A mechanical migration would have moved the volume without
the value. Everything load-bearing was distilled instead:

- the measurement-layer audit → `.dev/reference/measurement-layer-audit.md`
- the recurring defect families → `.dev/retro.md`
- current state, blockers and their one shared root cause → `.dev/HANDOFF.md`
