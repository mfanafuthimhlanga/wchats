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

## Second pass

An adversarial review of the gate found six things. All six are fixed in
`scripts/check-rendered-notice.mjs` and `package.json`.

- **F1, the version tag was measured and never judged.** One 62-character word crushed
  `.mono-tag` to 27.5px wide and 45px tall, three lines spilling out of a 32px bar, and the
  gate printed PASS. The loop now holds every child of the bar to the same three
  properties: height within one computed line-height, `scrollWidth <= clientWidth`, and a
  zero `clientWidth` as a finding of its own so an inline or unrendered child cannot pass
  by measuring nothing.
- **F2, the bar's own height was unchecked.** `scrollHeight > clientHeight` joins the
  horizontal check. It is the finding that fires hardest under every mutation below.
- **F3, the viewport comment was wrong.** It claimed another width would report a budget no
  Customer sees. `widget.css` fixes `body`, `#root` and `.widget-root` at 380px, so the
  viewport is inert above 380 wide. The comment says that now. Still 380x600.
- **F4, `postbuild` synced before it checked.** A rejected bundle was already in `embed/`
  and `apps/admin/public/wchats/`, both tracked, by the time the gate went red. The page is
  still `embed/index.html`, but `page.route` now answers its two relative requests from
  `dist/` bytes with `text/css` and `text/javascript`; everything else that is not `file:`
  still aborts. The check moved ahead of `sync-embed.mjs`.
- **F5, a bundle that never loads killed the gate with a raw Playwright `TimeoutError`.**
  `waitForSelector` is caught, the wait is 30 seconds for a cold runner, and the failure is
  one line naming what did not happen and which file to open.
- **F6, the header retold the issue's history** and claimed a two-second run against a real
  six to eight. It now says what the gate checks, why a rendered width cannot be counted,
  what it prints on pass and on fail, and the one thing it does not see. The loader frame
  is 100vw below 480px while the root stays 380px, so a 360px phone clips this bar whatever
  it measures here. That is #114.

Proof that the route serves `dist/` and not the folder beside the page: patching
`font-size:11px` to `19px` in `dist/widget.css` alone, with `embed/widget.css` untouched,
moved the span to 19px/28.5px and failed the gate. Restored by copy.

```
$ pnpm run build                                       # 1, green, both children measured
  bar             356px content, 32px tall  (viewport 380px)
  span            "AI-generated replies, processed by OpenAI"
                  207.92px wide, 16.5px tall, line-height 16.5px, font 11px system-ui, -apple-system, "Segoe UI", sans-serif
  code.mono-tag   "W Chats v0"
                  54.98px wide, 15px tall, line-height 15px, font 10px ui-monospace, "SF Mono", Menlo, monospace
check:rendered-notice: PASS -- all 2 children of the bar render on one row inside 356px of content width.
  synced  apps/widget/embed/widget.iife.js         24888 bytes  sha256:f051acf09f54
  synced  apps/widget/embed/widget.css             6590 bytes  sha256:e6e84f0f7e22
  synced  apps/admin/public/wchats/widget.iife.js  24888 bytes  sha256:f051acf09f54
  synced  apps/admin/public/wchats/widget.css      6590 bytes  sha256:e6e84f0f7e22
  synced  apps/admin/public/wchats/widget.js       5093 bytes  sha256:a3e390742dd3
  synced  apps/admin/public/wchats/index.html      628 bytes  sha256:2735e5ed201d
check:embed-sync: PASS -- embed/ and apps/admin/public/wchats/ both match their sources (6 files).
EXIT=0

$ pnpm run build      # 2, mutation F: PROCESSING_NOTICE is one 62-character word, no spaces
  bar             356px content, 32px tall  (viewport 380px)
  span            "AIgeneratedrepliesprocessedbyOpenAIandkeptforthirtydaysinUSAxy"
                  329.83px wide, 16.5px tall, line-height 16.5px, font 11px system-ui, -apple-system, "Segoe UI", sans-serif
  code.mono-tag   "W Chats v0"
                  27.5px wide, 45px tall, line-height 15px, font 10px ui-monospace, "SF Mono", Menlo, monospace

check:rendered-notice: FAIL -- 2 finding(s):
  code.mono-tag wraps onto a second row, 45px tall against a 15px line-height
  the bar overflows vertically, scrollHeight 38px over clientHeight 31px
[ELIFECYCLE] Command failed with exit code 1.
EXIT=1
# the span itself passes at 329.83px on one line. F1 fires on the tag, F2 on the bar.

$ pnpm run build                          # 3, mutation A: the 87-character sentence
  bar             356px content, 32px tall  (viewport 380px)
  span            "AI-generated replies, processed by OpenAI in the United States of America, kept 30 days"
                  315.48px wide, 33px tall, line-height 16.5px, font 11px system-ui, -apple-system, "Segoe UI", sans-serif
  code.mono-tag   "W Chats v0"
                  40.52px wide, 30px tall, line-height 15px, font 10px ui-monospace, "SF Mono", Menlo, monospace

check:rendered-notice: FAIL -- 3 finding(s):
  span wraps onto a second row, 33px tall against a 16.5px line-height
  code.mono-tag wraps onto a second row, 30px tall against a 15px line-height
  the bar overflows vertically, scrollHeight 32px over clientHeight 31px
[ELIFECYCLE] Command failed with exit code 1.
EXIT=1

$ git status --porcelain                  # 3, straight after the red build
 M apps/widget/package.json
 M apps/widget/scripts/check-rendered-notice.mjs
 M apps/widget/src/components/DisclosureBar.jsx
# nothing under embed/ or apps/admin/public/wchats/. The rejected bundle never shipped.

$ pnpm run build           # 4, mutation D: embed/index.html src="./missing.iife.js"
Bundle size OK: 9489 bytes
check:theming-contract: PASS -- 15 theming keys, every variable read by a rule.
check:rendered-notice: FAIL -- .disclosure-bar never rendered within 30 seconds, so nothing was measured. Check that apps/widget/dist/widget.iife.js is a bundle that mounts, and that apps/widget/embed/index.html still requests ./widget.iife.js.
[ELIFECYCLE] Command failed with exit code 1.
EXIT=1

$ pnpm run test:unit
 Test Files  3 passed (3)
      Tests  17 passed (17)
   Duration  4.60s

$ node scripts/check-size.mjs
Bundle size OK: 9489 bytes
EXIT=0
```

Every mutation was restored with `git checkout HEAD -- <path>` and `git status --porcelain`
reported only the two files this pass edits.

Worth keeping from run 2. The span passed on its own at 329.83px, so the pre-review gate
would have greened a build where the version tag rendered three lines outside the bar. The
two flex children trade width, which is why one of them is never enough to watch.
