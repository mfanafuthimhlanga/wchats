# The console says what put a red-team finding there (#310, console half)

Branch `feat/console-finding-evidence` off `main` at `6a855cb`. Since #312 and #314 a finding
records `evidence` (`recorded_prompt_run`, `landed_verdict_tag`, `attacker_report`) and
`claims` (the kinds that stood), and since #317 both sit on `red_team_findings` and come back
on `GET /agents/{id}/red-team/programme` under `open_findings`. The console's Adversary panel
(`apps/admin/app/agents/[id]/components/AdversaryPanel.tsx`) shows severity, description,
attack vector and turn, so a medium on the attacker's word reads the same as a rule-backed
critical, and the run's three report counters (`reports_no_attack`, `reports_dropped`,
`reports_on_attackers_word` in `red_team_runs.coverage`) are shown nowhere.

## What the owner sees after this

- **Per open finding**, the mono meta line keeps the vector and turn ("prompt_injection ·
  turn 2", one middle dot at most, DESIGN.md Voice), and a sentence under it says where the
  evidence came from and which claims stood: "Recorded from the agent's reply: it quoted its
  system prompt." for `recorded_prompt_run`, "Recorded from the dispatcher: ..." for
  `landed_verdict_tag`, "On the attacker's word: ..." for `attacker_report`, "Evidence
  unrecorded." for a row from before #317. Claim phrases join with "and" or commas and "and".
  Untrusted text: the phrases come from a fixed map keyed by the seven known kinds, and an
  unknown kind renders as `unknown claim`, never the raw string (`evidenceSentence` in
  `opsFormat.ts`, rendered by `FindingMeta.tsx`).
- **Under the severity cells**, one sentence for the newest run: "Latest run 3f2a9c1b,
  2026-09-24 14:05 UTC: 35 sequences closed with nothing landed, 1 finding on the attacker's
  word, 0 reports dropped." A newest run that failed reads "Latest run 3f2a9c1b failed;
  nothing to count." and one still running says so. A counter the backend could not read
  says "unreadable", never zero. Absent when the newest run is complete and recorded no
  counters (a run before #312).
- No new colours, no chip used as a category (DESIGN.md names that as an anti-pattern): the
  evidence sentence is `--ink-2` text in the banner and the list alike. The meta line wraps
  anywhere, so a long vector cannot scroll the page sideways at 390.

## Backend

`redteam_programme_service.read_programme` returns `latest_run` from the agent's newest
`m7:{agent}` run by `started_at DESC, id DESC`, whatever its status: `{run_id, finished_at,
status, reports_no_attack, reports_dropped, reports_on_attackers_word}`. A complete run sums
each counter over vectors from its `coverage` JSON; a counter whose values are not all
integers, or whose key is missing beside a present one, reads `null`. A failed or running run
reads `null` for all three rather than reaching back to an older complete run
(`eval_service._LATEST_RUN_SQL` states that rule). `null` overall when there is no run, or the
newest complete run carries none of the three keys. One extra SELECT on `red_team_runs`.
`OpenFinding` in `opsFormat.ts` gains `evidence` and `claims` (already in the route's payload
since #317). The ops page's stylesheet moves to `opsCss.ts` so the render spec reads the
stylesheet the page ships.

## Pins

- `apps/admin/tests-unit/ops-format.spec.ts`: `evidenceLabel`, `claimLabels` and
  `evidenceSentence` for the three evidence values, a null, every known kind, an unknown kind,
  `__proto__`, `constructor` and `toString`, and the joining; `latestRunLine` for a complete
  run, zeros, an unreadable counter, and a failed and a running newest run.
- `apps/admin/tests-unit/finding-meta-render.spec.ts`: Chromium renders the real
  `FindingMeta` against `globals.css` and `opsCss.ts` and reads the middle dots per metadata
  line, the evidence sentence's case and full stop, its computed colour in the banner and the
  list, and horizontal overflow at 390 with an 80-character vector.
- `apps/api/tests/unit/test_redteam_programme.py`: `latest_run` from a coverage JSON with the
  counters, `null` from a pre-#312 coverage, a failed or running newest run with null
  counters, a string, float, bool or missing counter reading `null`, and the statement's
  order (`started_at DESC, id DESC`, no status filter), proven once against the probe database.
- A Chromium screenshot of the deploy page's Adversary region with a demo fixture carrying one
  rule-backed critical, one medium on the attacker's word, one unknown claim kind and a latest
  run with counters, at 1280 and 390, colours sampled from pixels (recipe:
  `.dev/reference/260922-rendered-pixels-recipe.md`, `next dev --webpack`).
- `tsc --noEmit`, `test:unit`, `check:chart-render`, `check:no-dusk-tokens`,
  `check:ops-room-wiring`, `gates.py static`.

## Out of scope

Containing the fifteen stale criticals from runs `aa9f38be` and `42175745`: the owner's
action. Showing dropped or no-attack report texts: the API returns them on the run record;
a page for them is its own change.
