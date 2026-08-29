#!/usr/bin/env node
// check-rendered-notice.mjs
//
// The disclosure bar is one 32px row across the top of a 380px widget and it
// carries two children, the POPIA processing notice and the version tag. This
// gate opens the build that just finished in headless Chromium and measures
// both of them. A child that wraps onto a second row, that gets clipped, or
// that renders at zero width fails the build, and so does a bar that overflows
// its own box horizontally or vertically.
//
// Those widths have to be measured because no count predicts them. The guard
// this replaced was `PROCESSING_NOTICE.length <= NOTICE_MAX_CHARS`, and a
// character count knows nothing about the pixels a font paints. Swap
// --font-sans, raise the font size, widen the version tag or translate the
// copy into Afrikaans, and the count still passes while the row wraps (#106).
// Chromium reports the rendered box instead.
//
// The page it opens is embed/index.html, the loader a customer's iframe really
// requests, but its two relative requests are answered from dist/ rather than
// from the folder next to it. That way a bundle this gate rejects never has to
// be copied into embed/ or apps/admin/public/wchats/ to be measured, and
// postbuild can run the gate ahead of sync-embed.mjs. Every other request
// aborts. src/api.js calls loadConfig on mount and the bar renders
// unconditionally (src/Widget.jsx:73), so refusing that call costs a socket
// wait and nothing the gate reads.
//
// It prints two kinds of line. A gated line carries only numbers an assertion
// below reads: the bar's scroll box against its client box, and for each child
// its height against its line-height and its scrollWidth against its
// clientWidth. A line marked "measured, not gated" carries the bounding width,
// the font and the bar's content width, which help a reader of a red run and
// cannot change the exit code. Then one PASS line and exit 0, or one line per
// finding and exit 1. When the bar never appears it says so, names the file to
// check, and exits 1.
//
// One thing it does not see. embed/widget.js:72 makes the frame 100vw below a
// 480px screen while widget.css fixes the widget root at 380px, so a 360px
// phone clips the right edge of this bar however the text measures at 380.
// That is issue #114 and it needs a second viewport this gate does not open.

import { existsSync, readFileSync } from 'node:fs'
import { basename, join, relative } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from 'playwright-core'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const WIDGET_ROOT = join(__dirname, '..')
const REPO_ROOT = join(WIDGET_ROOT, '..', '..')
const DIST = join(WIDGET_ROOT, 'dist')
const EMBED_PAGE = join(WIDGET_ROOT, 'embed', 'index.html')

// widget.css fixes body, #root and .widget-root at 380px, so every width read
// below holds still for any viewport at least 380 wide. 380x600 is the iframe
// size embed/widget.js:67 sets, kept so the printed numbers are the ones a
// Customer's frame produces.
const VIEWPORT = { width: 380, height: 600 }

// Port 9 is discard. Nothing listens, and page.route aborts the request before
// it leaves anyway; the value only has to be a well-formed base for api.js.
const API_BASE = 'http://127.0.0.1:9'

// embed/index.html asks for ./widget.css and ./widget.iife.js beside itself.
// Both come from dist/ instead, keyed by file name.
const FRESH = new Map([
  ['widget.css', { file: join(DIST, 'widget.css'), contentType: 'text/css; charset=utf-8' }],
  ['widget.iife.js', { file: join(DIST, 'widget.iife.js'), contentType: 'text/javascript; charset=utf-8' }],
])

// 15 seconds covered a local run with the browser already on disk. A cold CI
// runner paints later, and the timeout only has to be shorter than a build
// that has genuinely hung.
const WAIT_SECONDS = 30

const round = (n) => Math.round(n * 100) / 100
const label = (path) => relative(REPO_ROOT, path).replace(/\\/g, '/')

const target = pathToFileURL(EMBED_PAGE)
target.searchParams.set('agent_id', 'probe')
target.searchParams.set('api', API_BASE)

const missing = [...FRESH.values()].map((f) => f.file).filter((f) => !existsSync(f))
if (missing.length > 0) {
  console.error(
    `check:rendered-notice: FAIL -- ${missing.map(label).join(' and ')} ` +
    'missing -- run `npm run build` before this check.'
  )
  process.exit(1)
}

async function measure(browser) {
  const page = await browser.newPage({ viewport: VIEWPORT })

  await page.route('**/*', (route) => {
    const url = route.request().url()
    if (!url.startsWith('file:')) return route.abort()
    const fresh = FRESH.get(basename(new URL(url).pathname))
    if (!fresh) return route.continue()
    return route.fulfill({ contentType: fresh.contentType, body: readFileSync(fresh.file) })
  })

  await page.goto(target.href, { waitUntil: 'domcontentloaded' })

  try {
    await page.waitForSelector('.disclosure-bar', { timeout: WAIT_SECONDS * 1000 })
  } catch {
    return null
  }

  return page.evaluate(() => {
    const bar = document.querySelector('.disclosure-bar')
    const barStyle = getComputedStyle(bar)
    const padding =
      parseFloat(barStyle.paddingLeft) + parseFloat(barStyle.paddingRight)

    const readChild = (el) => {
      const style = getComputedStyle(el)
      const rect = el.getBoundingClientRect()
      const classes = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean)
      return {
        name: [el.tagName.toLowerCase(), ...classes].join('.'),
        text: el.textContent,
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        lineHeight: style.lineHeight,
        height: rect.height,
        width: rect.width,
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      }
    }

    return {
      children: Array.from(bar.children, readChild),
      barHeight: bar.getBoundingClientRect().height,
      barScrollWidth: bar.scrollWidth,
      barClientWidth: bar.clientWidth,
      barScrollHeight: bar.scrollHeight,
      barClientHeight: bar.clientHeight,
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

if (m === null) {
  console.error(
    `check:rendered-notice: FAIL -- .disclosure-bar never rendered within ${WAIT_SECONDS} ` +
    `seconds, so nothing was measured. Check that ${label(join(DIST, 'widget.iife.js'))} ` +
    `is a bundle that mounts, and that ${label(EMBED_PAGE)} still requests ./widget.iife.js.`
  )
  process.exit(1)
}

const findings = []

if (m.children.length === 0) {
  findings.push('the bar rendered with no children, so there is nothing to fit in the row')
}

// Every child of the bar is held to the same three properties. Watching only
// the notice missed the tag being crushed to three lines beside it, because
// both are flex items and the tag gives up width first.
for (const child of m.children) {
  const lineHeight = parseFloat(child.lineHeight)

  if (!Number.isFinite(lineHeight)) {
    findings.push(
      `${child.name} has line-height '${child.lineHeight}', so one row has no ` +
      'measurable height. Give .disclosure-bar or its ancestor a numeric line-height'
    )
  } else if (child.height > lineHeight + 0.5) {
    findings.push(
      `${child.name} wraps onto a second row, ${round(child.height)}px tall ` +
      `against a ${round(lineHeight)}px line-height`
    )
  }

  if (child.clientWidth === 0) {
    findings.push(
      `${child.name} renders 0px of client width, so nothing measured it. An ` +
      'inline or unrendered child does not pass as a fitting one'
    )
  } else if (child.scrollWidth > child.clientWidth) {
    findings.push(
      `${child.name} is clipped, scrollWidth ${child.scrollWidth}px over ` +
      `clientWidth ${child.clientWidth}px`
    )
  }
}

if (m.barScrollWidth > m.barClientWidth) {
  findings.push(
    `the bar overflows horizontally, scrollWidth ${m.barScrollWidth}px over ` +
    `clientWidth ${m.barClientWidth}px`
  )
}

if (m.barScrollHeight > m.barClientHeight) {
  findings.push(
    `the bar overflows vertically, scrollHeight ${m.barScrollHeight}px over ` +
    `clientHeight ${m.barClientHeight}px`
  )
}

// Every number on the gated lines is read by an assertion above. The
// "measured, not gated" lines are diagnostics for the reader of a red run;
// they can change without the exit code changing, and the label says so.
const gutter = ' '.repeat(18)
console.log(
  `  bar             scrollWidth ${m.barScrollWidth}px within clientWidth ${m.barClientWidth}px, ` +
  `scrollHeight ${m.barScrollHeight}px within clientHeight ${m.barClientHeight}px`
)
for (const child of m.children) {
  console.log(`  ${child.name.padEnd(14)}  "${child.text}"`)
  console.log(
    `${gutter}${round(child.height)}px tall against line-height ${child.lineHeight}, ` +
    `scrollWidth ${child.scrollWidth}px within clientWidth ${child.clientWidth}px`
  )
  console.log(
    `${gutter}measured, not gated: ${round(child.width)}px wide, ` +
    `font ${child.fontSize} ${child.fontFamily}`
  )
}
console.log(
  `  measured, not gated: bar ${round(m.barContentWidth)}px content, ` +
  `${round(m.barHeight)}px tall, viewport ${VIEWPORT.width}px`
)

if (findings.length > 0) {
  console.error(`\ncheck:rendered-notice: FAIL -- ${findings.length} finding(s):`)
  for (const f of findings) console.error(`  ${f}`)
  process.exit(1)
}

console.log(
  `check:rendered-notice: PASS -- all ${m.children.length} children of the bar render ` +
  `on one row, none is clipped, and the bar overflows in neither axis.`
)
