# Rendered pixels for a console page, without Clerk

How to screenshot a console route and compute its contrast ratios on this machine. The
console's authenticated routes need a Clerk session the test harness does not have, so a
page that reads the API renders its loading skeleton forever under Playwright. The demo
seam is the way round it.

## The seam

`NEXT_PUBLIC_DEMO=true` makes every route public (`apps/admin/proxy.ts`) and a page that
checks `process.env.NEXT_PUBLIC_DEMO === 'true'` renders a fixture instead of fetching.
Two pages do this: the agents dashboard (`DEMO_AGENTS`) and the claims review
(`demoFixture.ts`). A new page that needs a pixel pass adds the same check and a fixture
file beside it.

## Run

```bash
cd apps/admin
NEXT_PUBLIC_DEMO=true corepack pnpm exec next dev -p 3100     # background, about 40s to first 200
```

A Playwright script must live under `apps/admin/` to resolve `@playwright/test`; a script
in the scratchpad fails with `ERR_MODULE_NOT_FOUND`. Copy it to `apps/admin/scripts/_x.tmp.mjs`,
run it with `node`, delete the copy. The script for the claims review page is the model:
four viewports (1440, 1280, 900, 390), a full-page PNG each, `scrollWidth > clientWidth`
for overflow, `pageerror` and console errors collected, and `getComputedStyle` on every
text element the contract names.

## Contrast

WCAG relative luminance over the composited colours. Two traps observed 2026-09-22:

- An alpha fill (`rgba(...)`) has to be composited over what is under it before the ratio
  means anything. `--live-dim` on the well is not `--live-dim` on the page.
- `.btn-primary` computes to `color(srgb r g b / 0.7)`, channels in 0 to 1, which a parser
  written for `rgb(0-255)` reads as near black and reports 1.05. Parse both forms, or
  multiply by hand: 0.7 of bone over graphite is `rgb(166, 165, 163)`, and graphite text
  on it is about 6.9.

A selector that matches a state other than the one intended (`.vbtn` picking a button that
is on) reports the wrong pair without failing. Read the fg and bg the probe prints, not
only the ratio.

## Numbers on the claims review page, 1440px, 2026-09-22

| pair | ratio |
|---|---|
| `--ink-3` on `--bg` (keys, eyebrows, legend, save line) | 5.08 |
| `--ink-2` on `--bg` (sub, count, rubric) | 7.28 |
| `--ink-2` on `--surface` (card heading, off buttons) | 6.81 |
| `--live-ink` on `--pass` (Yes on) | 8.61 |
| `--live-ink` on `--fail` (No on) | 4.87 |
| `--live-hot` on `--live-dim` over `--well` (mark) | 16.51 |

The lowest pair is the No button. `--fail` is a locked hex (DESIGN.md), so a darker text
or a larger size is the only move if that pair ever needs raising.
