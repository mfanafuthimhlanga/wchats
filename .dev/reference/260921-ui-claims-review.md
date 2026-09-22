# UI contract: the review of flagged claims

Route `/agents/[id]/eval/[runId]/claims`. One screen per answer the judge flagged, the flagged
claims beside it, a yes or no on each, saved through `POST .../claims/review` as a sitting.
v1 is the labelling page in `apps/api/tests/evals/calibration/page/template.html`.

## Copywriting

| Slot | Copy |
|---|---|
| h1 | Flagged claims |
| sub | Statements the judge could not find in the retrieved text, under the answer they came from. Say whether each one is in your documents. |
| rubric | Yes when a document of yours says this. No when none does. |
| buttons | Yes · No · Previous · Next |
| keys | ← → answer · j k sentence · 1 2 claim · y yes · n no |
| legend | left edge: how many of the sentence's words appear in some passage. bone most · grey some · red none. Shared words are not support; read the passage. |
| save | saved · saving · save failed, retrying · an answer was refused, reload the page · answers cannot be stored on this agent yet |
| empty | No flagged claims on this run. The judge flagged nothing, or this run was scored before claims were recorded. |
| unavailable | This agent's database cannot store answers yet. |
| dropped | N answers could not be read and are not shown. |
| error | Could not load the flagged claims. HTTP 503 |

Verdict: PASS.

## Visuals

States: loading (skeleton), error, no flags, flags with answers, flags with storage unavailable
(buttons disabled, status line says why), dropped scenarios (a note above the screen), mid-save,
save failed (retrying), refused (the answer goes back to what the server holds, the status
says to reload). Overflow: response and passages wrap, `overflow-wrap: anywhere`; the pane scrolls
inside its own box. Verdict: PASS.

## Colour

Tokens only, from `globals.css`. The two hues are verdicts: a Yes button on is `--pass` fill,
a No on is `--fail` fill, both with `--live-ink` text. Edges on the response: `--live` for
most words shared, `--ink-3` for some, `--fail` for none. Measured 2026-09-22 with alpha
composited: every text pair 4.75 or better, the lowest being `--ink-3` on `--surface` for the
citations block, then `--live-ink` on `--fail` at 4.87. The numbers are in `260922-rendered-pixels-recipe.md`. Verdict: PASS.

## Typography

Display for h1 and the question, sans 14 for the response, mono for keys, counts and the
status line, `.voice` for the claim statement, which is the judge's own sentence. Response at
78ch max, question at 68ch. Verdict: PASS.

## Spacing

Page furniture from `globals.css` (`.page`, `.page-head`, `.section`). Column gap 18px,
block gap 20px, card gap 12px, the button and pane paddings carried over from v1. Verdict: PASS.

## Safety

New route and new files only. `eval/page.tsx` gains one query and one line linking here.
`tests/smoke.spec.ts` gains the route. No shared component changes. Verdict: PASS.

## Keyboard

Tab is native. v1 took Tab for its own claim cycle, and on this page that is a keyboard trap
(WCAG 2.1.2: focus never leaves the document body), so the number keys pick a claim and a
Yes or No button taking focus makes its card the active one.

## Status

APPROVED after three adversary passes on 2026-09-22: five blockers in the rendered page on
the first, two in the save path on the second, each fixed and observed fixed by the reviewer.
