# The reading aids carry the gate's rules (#298)

Branch `feat/aids-carry-gate-rules` off `main` at `03d94a1`. The faithfulness gate is
`app/domain/grounding.py`, `grounding-v2`: word overlap against the best passage, a second
reading against the best passage joined with the one adding the most words (two at least,
never for a sentence with a reason or consequence connective), every number in the retrieved
text, a whole-sentence decline grounded. The two reading aids tint by overlap alone at 0.4
against one passage: the console aid `apps/admin/app/agents/[id]/eval/[runId]/claims/reading.ts`
and the claims bench `apps/api/tests/evals/calibration/page/claims_template.html` (vendored
from `~/.claude/skills/calibrate-judge/page/claims_template.html`, byte-identical today). A
sentence can show bone and fail the gate on a number, show grey and pass by two passages, or
show red and pass as a decline. ADR 0015: what the owner sees lit is what the gate scored.

## What each aid does after this

Per response unit, the aid decides what the gate decides and says why in the gate's words:

| gate rule | aid field | tint | card text (the gate's `reason`) |
|---|---|---|---|
| best passage carries the floor | `match`, `supported` | bone | "passage 3 carries 62% of its words" |
| best under the floor, best plus the passage adding most words (two at least) carries it, no connective | `spannedWith`, `supported` | bone, both passages lit | "passages 2 and 6 together carry 44% of its words" |
| a number the retrieved text lacks | `missing: string[]` | fail | "...; number 4173 appears in no passage" |
| a whole-sentence decline | `decline: true` | bone | "a decline asserts nothing the documents would carry" |
| under the floor, some words shared | | grey | "passage 3 carries 25% of its words" |
| no shared word | | fail | "no passage shares a word with it" |

The rules are ports of `_NUMBER_RE`, `_number_key`, `_DECLINE_RE`, `_SECOND_CLAUSE_RE`,
`is_decline`, `_INFERENCE_RE`, `SECOND_PASSAGE_MIN_WORDS`, `_second_reading`, `_score_text`
and `SentenceGrounding.reason`, each named for its Python source in a comment, so a rule
edit in one place has a named twin. The floor stays 0.4 in both aids.

## Pins

- `apps/admin/tests-unit/claims-reading.spec.ts`: the widget fixture (`fixtures-widget-claim.json`)
  still lights the Deploy passage; a number case (a sentence carried above the floor whose figure
  no passage has is `fail` with the number in its reason); a decline case (`is decline`, bone,
  the decline reason); a two-passage case (bone, `spannedWith` set, both passages, the joint
  reason); a connective case (no second reading); a second passage adding one word (no second
  reading).
- The same six cases through the built bench in Chromium in `claims-bench.spec.ts`: the tint
  class and the card text per sentence, read from the DOM, and the lit passages on selection.
- Parity: a test that runs the console module and the bench on the same fixture and compares
  tint and reason per sentence, so the two aids cannot drift from each other. If the bench's
  JS cannot be imported, the bench spec asserts the same literal expectations the reading spec
  does, and one table in the spec holds them.
- `tests/unit/test_claims_benchmark.py` or a new test: the vendored template is byte-identical
  to the skill copy after the edit (the skill copy is edited too).
- `pnpm exec tsc --noEmit`, `pnpm run test:unit`, `pnpm run check:chart-render`,
  `check:no-dusk-tokens`, `check:ops-room-wiring`.
- Screenshots of the console claims review with the fixture and of the bench, contrast
  computed for each tint on its background, before anything is shown.

## Out of scope

The console showing red-team evidence and claims (#310 console half). The gate itself.
