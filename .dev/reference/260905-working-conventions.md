# Working conventions: `.dev/`, workflows, model discipline, the archive

Moved out of `CLAUDE.md` on 2026-09-05 (#180), which had grown to 17KB against an 8KB ceiling.
Read this when starting phase-scale work, deciding where an artifact goes, briefing a subagent,
or looking for something that used to be in the root file.

## Where an artifact goes

```
.dev/
  BACKLOG.md                    FROZEN 2026-08-22. Rows carry slugs (`5.1 · ops15-server-gap`);
                                the NUMBER is an address, not a priority, and source comments
                                cite rows by number. Read a row when an issue or a comment
                                points at it. Never add to it, and open an issue per row as
                                that row enters the work.
  PRODUCTION-READINESS.md       every gap between here and production, plus the ordered
                                end-to-end validation plan. Claims are marked OBSERVED / READ /
                                RECORD. Never promote a RECORD line to a decision without
                                re-checking it.
  MASTERPLAN.md                 FROZEN 2026-08-22. The wayfinder map issue carries the path.
  plans/     YYMMDD-<slug>.md   FROZEN 2026-08-22. A spec lives in its issue (`/to-spec`).
  traces/    YYMMDD-<slug>.md   AFTER execution: what changed, decisions, deviations.
  workflows/ <name>.workflow.js the orchestration itself, versioned and re-runnable.
  reference/ <topic>.md         durable findings that outlive one task.
  reviews/   <branch>.md        diff-review packets. SUSPENDED, see below.
  retro.md                      append-only regression retro log.
```

- No task is done without its trace. Outstanding work living only in the tail of a trace is how
  it gets lost, so every breakage a trace records becomes an issue the same day.
- Plans and traces are terse working documents: bullets, file lists, decisions. The GSD habit of
  600-word narrative paragraphs per plan is what `.planning/` became.
- A workflow's tier-2 judgement is extracted to `.dev/reference/` before the session ends. The
  workflow journal lives in a temp directory and does not survive.
- When a Workflow runs, copy its script into `.dev/workflows/` so the orchestration is versioned.

## Execution engine

Substantive multi-step execution runs through Claude Code's Workflow feature. This is a standing
opt-in for multi-agent orchestration on phase-scale work (owner, 2026-08-05); trivial tasks run
solo.

## Model discipline (owner, 2026-08-15)

Execution runs on Opus; judgement stays on the session model.

- Implementation subagents pass `model: 'opus'` and get small, bounded briefs naming the exact
  files and the exact exit check. The practice audit's Opus frustrations were all wide-brief
  frustrations.
- Orchestration, verification passes and reviews inherit the session model. Omit `model`: the
  alias is not a version guarantee, so pin judgement to the resolved session model by omission.
- Do not spawn named specialist agents whose definitions pin a model in frontmatter. Use
  `general-purpose` or the default workflow subagent.
- The one exception is the tier-2 judge, which runs `model: 'fable'` once per milestone, before
  the merge. It reads a bounded artifact only, the diff plus the implementers' claims plus the
  tier-1 findings, assembled by a session-model collector, and never explores the tree. Its
  question is not "what is broken?" (tier 1 already asked that, against the code) but "do the
  claims match the evidence, and what is asserted but unproven?". Reference implementation:
  `.dev/workflows/eval-foundation.workflow.js`.

## Comprehension gate: SUSPENDED (owner, 2026-08-05)

The diff-review packet (`.dev/reviews/`, `~/.claude/templates/diff-review-packet.md`) is paused,
matching the call already made in `sentinel-v2`: learning a milestone in isolation, 23 phases
deep, costs more than it returns.

- A merge is never blocked on packet questions, an answer ledger, or an owner-authored piece.
- Terse `.dev/plans/`, `.dev/traces/` and `.dev/retro.md` keep being written. They are cheap,
  factual, and they are the source material a later relearn is built from.
- The tier-2 judge stays on. It is a correctness mechanism, not a teaching one.

## The archive (`.planning/`)

564 files of GSD planning artifacts covering Phases 1 to 23 and milestones v1.0 to v1.2. Frozen
as of 2026-08-05: do not add to it, do not update it. Git preserves it; treat it as reference of
last resort. Everything load-bearing has been distilled into `.dev/reference/` and `.dev/retro.md`.

Still authoritative inside the archive: `PROJECT.md` (product context, requirements and the
decision log), `REQUIREMENTS.md` (requirement IDs, with two known defects), and each phase's
`SECURITY.md` threat register.
