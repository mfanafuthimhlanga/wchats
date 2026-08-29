# 260829, the widget notice is measured, not counted (#106)

Closes #106. Branch `feat/widget-rendered-check` off `main` at d8162e3.

`apps/widget` had three test files and no renderer. `DisclosureBar.jsx` had to fit one 32px
row and its guard was `PROCESSING_NOTICE.length <= NOTICE_MAX_CHARS`, a character count in
place of a rendered width, next to a comment holding numbers someone measured by hand once.
Chromium now measures those numbers on every build.

## What changed

```
apps/widget/scripts/check-rendered-notice.mjs      new, the gate
apps/widget/package.json                            postbuild + check:rendered-notice
apps/widget/pnpm-lock.yaml                          playwright-core 1.61.1, dev only
apps/widget/src/components/DisclosureBar.jsx        NOTICE_MAX_CHARS removed
apps/widget/src/components/DisclosureBar.test.js    the count test removed, three kept
.github/workflows/ci.yml                            browser install before the widget build
.github/workflows/nightly.yml                       the same, before the D7 build
```

## Decisions

- **`playwright-core`, not `@playwright/test`.** The gate is one node script driving a
  browser. The runner brings a test framework the widget already has in vitest.
- **Revision 1228, already on disk.** playwright-core 1.61.1 pins chromium and
  chromium-headless-shell at revision 1228, browser version 149.0.7827.55.
  `%LOCALAPPDATA%\ms-playwright` held `chromium_headless_shell-1228` before this branch, so
  the local run downloaded nothing. `chromium.launch()` resolves to that shell.
- **It runs last in `postbuild`, after `sync-embed.mjs`.** The page it opens is
  `embed/index.html`, which sync-embed writes. Running before the sync would measure the
  previous build.
- **Every non-`file:` request aborts.** `src/api.js` calls `loadConfig` on mount, so the
  page fires at the `?api=` base. `page.route` aborts it instead of waiting on a socket.
  The bar renders unconditionally at `src/Widget.jsx:73`, so nothing it measures depends on
  the config that never arrives.
- **The viewport is 380x600**, the iframe size `embed/widget.js:67` sets. Any other width
  reports a budget no Customer sees.
- **Three findings, one line each**, the shape the issue asked for. The wrap check compares
  the span's bounding height to its computed line-height, so a numeric line-height is a
  precondition; when `getComputedStyle` returns `normal` the gate says so rather than
  passing on a `NaN` comparison.

## The gate observed red

Three builds. Green, then the notice at 87 characters, then the string reverted by hand and
diffed against the pre-mutation file.

```
$ pnpm run build                                       # 1, green
  notice          "AI-generated replies, processed by OpenAI"
  font            11px / 16.5px  system-ui, -apple-system, "Segoe UI", sans-serif
  notice width    207.92px
  version tag     54.98px
  bar content     356px  (viewport 380px)
  rendered height 16.5px
check:rendered-notice: PASS -- the notice renders on one 16.5px row, 207.92px of 356px content width.
EXIT=0

$ pnpm run build                                       # 2, PROCESSING_NOTICE at 87 chars
  notice          "AI-generated replies, processed by OpenAI in the United States of America, kept 30 days"
  font            11px / 16.5px  system-ui, -apple-system, "Segoe UI", sans-serif
  notice width    315.48px
  version tag     40.52px
  bar content     356px  (viewport 380px)
  rendered height 33px

check:rendered-notice: FAIL -- 1 finding(s):
  the notice wraps: the span renders 33px tall, one line-height is 16.5px
[ELIFECYCLE] Command failed with exit code 1.
EXIT=1

$ pnpm run build                                       # 3, string restored
  notice          "AI-generated replies, processed by OpenAI"
  font            11px / 16.5px  system-ui, -apple-system, "Segoe UI", sans-serif
  notice width    207.92px
  version tag     54.98px
  bar content     356px  (viewport 380px)
  rendered height 16.5px
check:rendered-notice: PASS -- the notice renders on one 16.5px row, 207.92px of 356px content width.
EXIT=0
```

Build 3 rebuilt `widget.iife.js` to sha256 `f051acf09f54`, the same hash as build 1, and
`git status` reports no change under `embed/` or `apps/admin/public/wchats/`.

Two numbers in the red run are worth keeping. The version tag shrank from 54.98px to
40.52px, because both children are flex items and the tag gave up width before the notice
wrapped, so a check that only watched the notice would have watched the wrong element. And
the bar's `scrollWidth` never exceeded its `clientWidth`, because the text wrapped instead
of overflowing. The wrap check is the one that fires under this stylesheet; the clip and
overflow checks cover `white-space: nowrap` and a widened tag, which this bar does not have
today.

The measured pass numbers match the hand measurement the deleted comment carried, 356px
content, 55px tag, 208px notice, 16.5px line-height, four days later on the same machine.

## Other gates

```
$ pnpm run test:unit
 Test Files  3 passed (3)
      Tests  17 passed (17)      # 18 before, minus the character-count test

$ node scripts/check-size.mjs
Bundle size OK: 9489 bytes
```

## CI

`eval-deterministic` in `ci.yml` and `eval-full` in `nightly.yml` both run
`pnpm install --frozen-lockfile && pnpm run build` on ubuntu-latest, which carries no
Playwright browser, so postbuild would have failed on both. One step goes in ahead of each:

```
pnpm install --frozen-lockfile && pnpm exec playwright-core install --with-deps chromium-headless-shell
```

`pnpm exec` cannot reach playwright-core before the install, hence the repeated
`pnpm install`; the build step's own install then finds the store warm. `--with-deps` and
the `chromium-headless-shell` browser name both come from
`pnpm exec playwright-core install --help` on 1.61.1, checked rather than assumed.

Neither run has been observed on a runner. The claim that the step is enough is READ, not
OBSERVED, and the first push settles it.

## Deviations

- The issue's fix shape put the check "beside `check-size.mjs`". It sits after
  `sync-embed.mjs` instead, for the reason above.
- The mutation string is 87 characters rather than the 90 the task named. 87 already wraps,
  and it is a sentence rather than filler.
