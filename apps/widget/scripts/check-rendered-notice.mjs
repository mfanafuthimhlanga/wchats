#!/usr/bin/env node
// check-rendered-notice.mjs
//
// apps/widget had three test files and none of them rendered anything: vitest
// runs in the node environment and the package carries no jsdom, so no gate in
// the repo looked at what the widget paints. The disclosure bar's notice has to
// fit one 32px row, and its only guard was
// `PROCESSING_NOTICE.length <= NOTICE_MAX_CHARS` -- a character count standing
// in for a rendered width. Change --font-sans, raise the font size, widen the
// version tag or translate the copy and the count still passes while the line
// wraps. The width had been measured once, by hand, into a comment (#106).
// FM-007 logged the claim that a rendered width could not be measured here,
// which it can, in Chromium, in about two seconds.
//
// This gate measures it on every build. It opens the shipped embed/ bundle in
// headless Chromium at 380x600 -- the iframe size embed/widget.js:67 sets --
// aborts every request that is not file: so the config call fails fast instead
// of hanging, waits for the bar, and reads three properties off it:
//
//     wrapped        span bounding height > one computed line-height
//     clipped        span scrollWidth > clientWidth
//     bar overflows  bar scrollWidth > clientWidth
//
// The bar renders unconditionally (src/Widget.jsx:73), so it does not wait on
// the config the aborted request would have carried.
//
// postbuild runs this AFTER sync-embed.mjs, so what it measures is what ships
// rather than whatever dist/ happens to hold.
//
// Exit 0 with the measured numbers, 1 with one line per finding.

import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from 'playwright-core'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const WIDGET_ROOT = join(__dirname, '..')
const EMBED_PAGE = join(WIDGET_ROOT, 'embed', 'index.html')

// embed/widget.js:67 sizes the iframe 380x600. Measuring at any other width
// would report a budget no Customer ever sees.
const VIEWPORT = { width: 380, height: 600 }

// Port 9 is discard. Nothing listens, and page.route aborts the request before
// it leaves anyway; the value only has to be a well-formed base for api.js.
const API_BASE = 'http://127.0.0.1:9'

const round = (n) => Math.round(n * 100) / 100

const target = pathToFileURL(EMBED_PAGE)
target.searchParams.set('agent_id', 'probe')
target.searchParams.set('api', API_BASE)

async function measure(browser) {
  const page = await browser.newPage({ viewport: VIEWPORT })

  // src/api.js calls loadConfig on mount. Let the bundle and the stylesheet
  // load off disk and refuse everything else, so the run costs no network wait.
  await page.route('**/*', (route) =>
    route.request().url().startsWith('file:') ? route.continue() : route.abort()
  )

  await page.goto(target.href, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.disclosure-bar span', { timeout: 15000 })

  return page.evaluate(() => {
    const bar = document.querySelector('.disclosure-bar')
    const span = bar.querySelector('span')
    const tag = bar.querySelector('.mono-tag')
    const spanStyle = getComputedStyle(span)
    const barStyle = getComputedStyle(bar)
    const padding =
      parseFloat(barStyle.paddingLeft) + parseFloat(barStyle.paddingRight)

    return {
      notice: span.textContent,
      fontFamily: spanStyle.fontFamily,
      fontSize: spanStyle.fontSize,
      lineHeight: spanStyle.lineHeight,
      spanHeight: span.getBoundingClientRect().height,
      spanWidth: span.getBoundingClientRect().width,
      spanScrollWidth: span.scrollWidth,
      spanClientWidth: span.clientWidth,
      tagWidth: tag ? tag.getBoundingClientRect().width : null,
      barScrollWidth: bar.scrollWidth,
      barClientWidth: bar.clientWidth,
      barContentWidth: bar.clientWidth - padding,
    }
  })
}

let m
const browser = await chromium.launch()
try {
  m = await measure(browser)
} finally {
  await browser.close()
}

const findings = []
const lineHeight = parseFloat(m.lineHeight)

if (!Number.isFinite(lineHeight)) {
  findings.push(
    `line-height computed to '${m.lineHeight}', so one row has no measurable ` +
    `height -- give .disclosure-bar or its ancestor a numeric line-height`
  )
} else if (m.spanHeight > lineHeight + 0.5) {
  findings.push(
    `the notice wraps: the span renders ${round(m.spanHeight)}px tall, ` +
    `one line-height is ${round(lineHeight)}px`
  )
}

if (m.spanScrollWidth > m.spanClientWidth) {
  findings.push(
    `the notice is clipped: span scrollWidth ${m.spanScrollWidth}px exceeds ` +
    `clientWidth ${m.spanClientWidth}px`
  )
}

if (m.barScrollWidth > m.barClientWidth) {
  findings.push(
    `the bar overflows: bar scrollWidth ${m.barScrollWidth}px exceeds ` +
    `clientWidth ${m.barClientWidth}px`
  )
}

console.log(`  notice          "${m.notice}"`)
console.log(`  font            ${m.fontSize} / ${m.lineHeight}  ${m.fontFamily}`)
console.log(`  notice width    ${round(m.spanWidth)}px`)
console.log(`  version tag     ${m.tagWidth == null ? 'absent' : `${round(m.tagWidth)}px`}`)
console.log(`  bar content     ${round(m.barContentWidth)}px  (viewport ${VIEWPORT.width}px)`)
console.log(`  rendered height ${round(m.spanHeight)}px`)

if (findings.length > 0) {
  console.error(`\ncheck:rendered-notice: FAIL -- ${findings.length} finding(s):`)
  for (const f of findings) console.error(`  ${f}`)
  process.exit(1)
}

console.log(
  `check:rendered-notice: PASS -- the notice renders on one ` +
  `${round(lineHeight)}px row, ${round(m.spanWidth)}px of ` +
  `${round(m.barContentWidth)}px content width.`
)
