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
// below reads: the bar's scroll box against its client box, its own box against
// the viewport, the document's scroll width against the viewport, and for each
// child its height against its line-height and its scrollWidth against its
// clientWidth. A line marked "measured, not gated" carries the bounding width,
// the font and the bar's content width, which help a reader of a red run and
// cannot change the exit code. Then one PASS line and exit 0, or one line per
// finding and exit 1. When the bar never appears it says so, names the file to
// check, and exits 1.
//
// It opens eight viewports, because the frame is not one size. embed/widget.js:72
// makes the frame 100vw below a 480px screen, so the frame is as wide as the
// phone: 360, 390, 412, 430, up to 480. Anything the widget draws past that
// edge is outside the frame, where no measurement taken at 380 can see it
// (#114), and anything it stops short of leaves the iframe's own white ground
// showing beside it. Both are measured here, on the horizontal axis, against
// the frame edge.
//
// The same argument runs vertically (#176). embed/widget.js:67 caps the frame
// at `calc(100vh - 120px)` and :72 makes it 100vh on a phone, so a short window
// hands the widget a short frame. The widget root pinned itself to 600px with
// `overflow:hidden` above it, so on any frame under 600 the input bar and the
// send button rendered below the frame's bottom edge with no scroll to reach
// them: the customer could read the transcript and could not type. The five
// frames this gate opened before were all at least 600 tall, so none of them
// could see it. Three short frames are open now, and the input bar and the send
// button are measured against the frame's bottom edge in each of them, twice:
// once on the empty transcript and once with 2000px of content pushed into it,
// which is where a transcript that grows the page instead of scrolling itself
// shows up.

import { existsSync, readFileSync } from 'node:fs'
import { basename, join, relative } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from 'playwright-core'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const WIDGET_ROOT = join(__dirname, '..')
const REPO_ROOT = join(WIDGET_ROOT, '..', '..')
const DIST = join(WIDGET_ROOT, 'dist')
const EMBED_PAGE = join(WIDGET_ROOT, 'embed', 'index.html')

// The frames embed/widget.js can build. 380x600 is the desktop iframe it sizes
// at :67, and everything under it is a phone below the 480px breakpoint at :72,
// where the frame becomes 100vw and the widget has to come with it, up as well
// as down. 360 and 480 are the ends of that band and 390 and 412 are an iPhone
// 14 and a Pixel 7, which is where `min(380px, 100vw)` left a 32px strip of the
// iframe's own white beside the widget: at 380 and at 360 the old rule and the
// new one compute the same number, so neither run could see it. The printed
// numbers are the ones a Customer's frame really produces, at sizes a Customer
// really gets.
const VIEWPORTS = [
  { width: 380, height: 600, label: 'the 380px desktop frame' },
  { width: 360, height: 640, label: 'a 360px phone frame' },
  { width: 390, height: 844, label: 'a 390px iPhone 14 frame' },
  { width: 412, height: 800, label: 'a 412px Pixel 7 frame' },
  { width: 480, height: 800, label: 'a 480px frame, the widest the loader makes 100vw' },
  // The short half of the same arithmetic (#176). Each height is what
  // embed/widget.js computes for a real browser window:
  //   a 360x560 phone window     -> 360x560, the :72 branch, 100vw by 100vh
  //   a 640x360 landscape window -> 380x240, the :67 branch, 360 - 120
  //   a 1280x660 desktop window  -> 380x540, the :67 branch, 660 - 120
  // All three are shorter than the 600px the widget root used to pin itself to.
  { width: 360, height: 560, label: 'a 360x560 frame, from a 360x560 phone window' },
  { width: 380, height: 240, label: 'a 380x240 frame, from a 640x360 landscape window' },
  { width: 380, height: 540, label: 'a 380x540 frame, from a 1280x660 desktop window' },
]

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

// ── the floor ──────────────────────────────────────────────────────────────
// The PASS line at the bottom names four assertions, and each one needs a
// frame to have a subject. Nothing here supplied one: `VIEWPORTS = []` ran
// the measuring loop zero times and printed that sentence over nothing, exit
// 0. A gate over zero observations is unknown, never pass. The counts under
// the loop below finish the job, and the PASS line prints them beside the
// claims they belong to.
if (VIEWPORTS.length === 0) {
  console.error(
    'check:rendered-notice: FAIL -- VIEWPORTS is empty, so no frame is opened and nothing is ' +
    'measured. Every assertion below it would pass by having no subject.'
  )
  process.exit(1)
}

async function measure(browser, viewport) {
  const page = await browser.newPage({
    viewport: { width: viewport.width, height: viewport.height },
  })

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

    const barBox = bar.getBoundingClientRect()

    // The two boxes that carry the widget's width. The bar is a row inside
    // them, so a root narrower than the frame moves the bar's right edge in
    // with it and every bar assertion still passes.
    const boxOf = (el) => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }
    }

    // The controls the customer needs to reach, and where the transcript's
    // overflow goes. Read after each layout, so the same function serves the
    // empty transcript and the filled one.
    const readControls = () => ({
      inputBar: boxOf(document.querySelector('.input-bar')),
      sendButton: boxOf(document.querySelector('.input-bar button.send')),
      scrollArea: boxOf(document.querySelector('.scroll-area')),
      scrollAreaScrollHeight: document.querySelector('.scroll-area')?.scrollHeight ?? null,
      scrollAreaClientHeight: document.querySelector('.scroll-area')?.clientHeight ?? null,
      docScrollHeight: document.documentElement.scrollHeight,
      bodyScrollHeight: document.body.scrollHeight,
    })

    const empty = readControls()

    // A transcript long enough to overflow any frame this gate opens. The
    // widget has to absorb it inside .scroll-area; a root that grows with its
    // content instead pushes the input bar out of a frame whose body is
    // `overflow:hidden`, and the customer has no scroll to bring it back.
    const filler = document.createElement('div')
    filler.setAttribute('data-gate-filler', '')
    filler.style.height = '2000px'
    filler.style.flexShrink = '0'
    document.querySelector('.scroll-area')?.appendChild(filler)
    document.documentElement.getBoundingClientRect() // force layout
    const filled = readControls()
    filler.remove()

    return {
      empty,
      filled,
      viewportHeight: window.innerHeight,
      scrollAreaOverflowY: (() => {
        const el = document.querySelector('.scroll-area')
        return el ? getComputedStyle(el).overflowY : null
      })(),
      children: Array.from(bar.children, readChild),
      barHeight: barBox.height,
      // Where the bar sits in the frame, not just how wide it is. A bar that
      // fits its own box perfectly is still clipped when that box starts inside
      // the frame and ends outside it (#114).
      barLeft: barBox.left,
      barRight: barBox.right,
      barScrollWidth: bar.scrollWidth,
      barClientWidth: bar.clientWidth,
      barScrollHeight: bar.scrollHeight,
      barClientHeight: bar.clientHeight,
      barContentWidth: bar.clientWidth - padding,
      // The whole page against the frame. The bar is one row of a widget; if
      // the root is wider than the frame, every row is clipped, not only this
      // one, and this number says so in one place.
      docScrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      root: boxOf(document.getElementById('root')),
      widgetRoot: boxOf(document.querySelector('.widget-root')),
    }
  })
}

const measurements = []
const browser = await chromium.launch()
try {
  for (const viewport of VIEWPORTS) {
    measurements.push([viewport, await measure(browser, viewport)])
  }
} finally {
  await browser.close()
}

for (const [viewport, m] of measurements) {
  if (m === null) {
    console.error(
      `check:rendered-notice: FAIL -- .disclosure-bar never rendered within ${WAIT_SECONDS} ` +
      `seconds in ${viewport.label}, so nothing was measured. Check that ` +
      `${label(join(DIST, 'widget.iife.js'))} is a bundle that mounts, and that ` +
      `${label(EMBED_PAGE)} still requests ./widget.iife.js.`
    )
    process.exit(1)
  }
}

const findings = []
const gutter = ' '.repeat(18)

for (const [viewport, m] of measurements) {
  console.log(`  ${viewport.label}`)
  check(viewport, m)
}

function check(viewport, m) {
const note = (text) => findings.push(`in ${viewport.label}, ${text}`)

if (m.children.length === 0) {
  note('the bar rendered with no children, so there is nothing to fit in the row')
}

// Every child of the bar is held to the same three properties. Watching only
// the notice missed the tag being crushed to three lines beside it, because
// both are flex items and the tag gives up width first.
for (const child of m.children) {
  const lineHeight = parseFloat(child.lineHeight)

  if (!Number.isFinite(lineHeight)) {
    note(
      `${child.name} has line-height '${child.lineHeight}', so one row has no ` +
      'measurable height. Give .disclosure-bar or its ancestor a numeric line-height'
    )
  } else if (child.height > lineHeight + 0.5) {
    note(
      `${child.name} wraps onto a second row, ${round(child.height)}px tall ` +
      `against a ${round(lineHeight)}px line-height`
    )
  }

  if (child.clientWidth === 0) {
    note(
      `${child.name} renders 0px of client width, so nothing measured it. An ` +
      'inline or unrendered child does not pass as a fitting one'
    )
  } else if (child.scrollWidth > child.clientWidth) {
    note(
      `${child.name} is clipped, scrollWidth ${child.scrollWidth}px over ` +
      `clientWidth ${child.clientWidth}px`
    )
  }
}

if (m.barScrollWidth > m.barClientWidth) {
  note(
    `the bar overflows horizontally, scrollWidth ${m.barScrollWidth}px over ` +
    `clientWidth ${m.barClientWidth}px`
  )
}

if (m.barScrollHeight > m.barClientHeight) {
  note(
    `the bar overflows vertically, scrollHeight ${m.barScrollHeight}px over ` +
    `clientHeight ${m.barClientHeight}px`
  )
}

// #114. The bar can measure perfectly and still be half outside the frame,
// because the frame is 100vw below 480px while the widget root used to be
// pinned at 380px. Nothing inside the page can see that, so the frame edge is
// the reference here, not the bar's own box.
if (m.barRight > m.viewportWidth + 0.5) {
  note(
    `the bar's right edge is outside the frame, ${round(m.barRight)}px against a ` +
    `${m.viewportWidth}px viewport, so its last ${round(m.barRight - m.viewportWidth)}px ` +
    'is clipped. The widget root is wider than the frame the loader builds'
  )
}

if (m.barLeft < -0.5) {
  note(
    `the bar's left edge is outside the frame at ${round(m.barLeft)}px, so its ` +
    'first pixels are clipped'
  )
}

// The frame is the width, and the widget fills it. #114 fixed the narrow half
// of this and left the wide one: `min(380px, 100vw)` followed the frame down
// and not up, so a 412px Pixel got a 380px widget and a 32px strip of the
// iframe's own white down the full height, since embed/index.html paints a
// transparent body over `background:#fff`. Nothing already measured here could
// see it. Everything the widget drew fitted, the document did not scroll, and
// the bar's right edge moved in with the root it sits in. The frame edge is the
// reference, in both directions.
for (const [name, box] of [['#root', m.root], ['.widget-root', m.widgetRoot]]) {
  if (box === null) {
    note(
      `${name} never rendered, so the frame's width was measured against nothing. ` +
      'Check that the bundle mounts and that src/widget.css still names it'
    )
    continue
  }

  if (box.right < m.viewportWidth - 0.5) {
    note(
      `${name} stops ${round(m.viewportWidth - box.right)}px short of the frame's right ` +
      `edge, right ${round(box.right)}px in a ${m.viewportWidth}px frame, so that strip is ` +
      'the iframe\'s own white ground beside the widget, down its full height'
    )
  }

  if (box.right > m.viewportWidth + 0.5) {
    note(
      `${name} reaches ${round(box.right - m.viewportWidth)}px past the frame's right edge, ` +
      `right ${round(box.right)}px in a ${m.viewportWidth}px frame, so that much of every row ` +
      'is clipped'
    )
  }

  if (Math.abs(box.left) > 0.5) {
    note(
      `${name} starts at ${round(box.left)}px rather than the frame's left edge, so the ` +
      'widget is offset inside its own frame'
    )
  }
}

if (m.docScrollWidth > m.viewportWidth) {
  note(
    `the widget is wider than its frame, document scrollWidth ` +
    `${m.docScrollWidth}px over a ${m.viewportWidth}px viewport, so every row ` +
    'is clipped and not only this bar'
  )
}

// ── the vertical axis (#176) ───────────────────────────────────────────────
// The frame is the height as well as the width. A widget root pinned at 600px
// put the input bar at 536-600 inside a 560px frame and at 536-600 inside a
// 240px one, with `overflow:hidden` on the body above it, so the only control
// the widget has was off the bottom edge and no scroll reached it. The frame
// edge is the reference here too, and the two controls are measured on the
// empty transcript and again with 2000px pushed into it, because a transcript
// that grows the page rather than scrolling itself moves the input bar out at
// exactly the moment a conversation gets going.
for (const [phase, c] of [['on an empty transcript', m.empty], ['with 2000px of transcript', m.filled]]) {
  for (const [name, box] of [['.input-bar', c.inputBar], ['button.send', c.sendButton]]) {
    if (box === null) {
      note(
        `${name} never rendered, so ${phase} the control the customer types into was ` +
        'measured against nothing. Check that the bundle mounts src/components/InputBar.jsx'
      )
      continue
    }

    if (box.height <= 0 || box.width <= 0) {
      note(
        `${name} renders ${round(box.width)}x${round(box.height)}px ${phase}, so there is no ` +
        'box to reach'
      )
    }

    if (box.bottom > m.viewportHeight + 0.5) {
      note(
        `${name} is below the frame's bottom edge ${phase}, bottom ${round(box.bottom)}px in a ` +
        `${m.viewportHeight}px frame, so its last ${round(box.bottom - m.viewportHeight)}px is ` +
        'outside the iframe and the body above it is overflow:hidden, so nothing scrolls to it'
      )
    }

    if (box.top < -0.5) {
      note(
        `${name} is above the frame's top edge ${phase}, top ${round(box.top)}px, so its first ` +
        'pixels are outside the iframe'
      )
    }
  }

  if (c.docScrollHeight > m.viewportHeight + 0.5) {
    note(
      `the widget is taller than its frame ${phase}, document scrollHeight ` +
      `${c.docScrollHeight}px over a ${m.viewportHeight}px frame. The overflow belongs in ` +
      '.scroll-area, which scrolls; the document does not, so anything past the edge is lost'
    )
  }
}

// The transcript is the one box that scrolls. If 2000px of content did not
// leave .scroll-area with more scroll height than client height, the overflow
// went somewhere else, and the assertions above only caught it if that
// somewhere else pushed a control out of this particular frame.
if (m.filled.scrollAreaClientHeight === null) {
  note('.scroll-area never rendered, so the transcript was not measured at all')
} else {
  if (m.scrollAreaOverflowY !== 'auto' && m.scrollAreaOverflowY !== 'scroll') {
    note(
      `.scroll-area computes overflow-y '${m.scrollAreaOverflowY}', so the transcript does not ` +
      'scroll and its overflow lands on the page instead'
    )
  }

  if (m.filled.scrollAreaClientHeight <= 0) {
    note(
      `.scroll-area has ${m.filled.scrollAreaClientHeight}px of client height with 2000px of ` +
      'transcript in it, so there is nowhere to read the conversation'
    )
  } else if (m.filled.scrollAreaScrollHeight <= m.filled.scrollAreaClientHeight) {
    note(
      `.scroll-area did not absorb the 2000px filler, scrollHeight ` +
      `${m.filled.scrollAreaScrollHeight}px against clientHeight ` +
      `${m.filled.scrollAreaClientHeight}px, so the overflow went to some other box`
    )
  }
}

// The widget fills the frame vertically, the same claim the loop above makes
// horizontally. A 600px root in an 844px iPhone frame leaves 244px of the
// iframe's own white ground under the input bar.
for (const [name, box] of [['#root', m.root], ['.widget-root', m.widgetRoot]]) {
  if (box === null) continue

  if (box.bottom < m.viewportHeight - 0.5) {
    note(
      `${name} stops ${round(m.viewportHeight - box.bottom)}px short of the frame's bottom edge, ` +
      `bottom ${round(box.bottom)}px in a ${m.viewportHeight}px frame, so that strip is the ` +
      'iframe\'s own white ground under the widget'
    )
  }

  if (box.bottom > m.viewportHeight + 0.5) {
    note(
      `${name} reaches ${round(box.bottom - m.viewportHeight)}px past the frame's bottom edge, ` +
      `bottom ${round(box.bottom)}px in a ${m.viewportHeight}px frame, so that much of the ` +
      'widget is outside the iframe'
    )
  }

  if (Math.abs(box.top) > 0.5) {
    note(
      `${name} starts at ${round(box.top)}px rather than the frame's top edge, so the widget is ` +
      'offset inside its own frame'
    )
  }
}

// Every number on the gated lines is read by an assertion above. The
// "measured, not gated" lines are diagnostics for the reader of a red run;
// they can change without the exit code changing, and the label says so.
console.log(
  `  bar             scrollWidth ${m.barScrollWidth}px within clientWidth ${m.barClientWidth}px, ` +
  `scrollHeight ${m.barScrollHeight}px within clientHeight ${m.barClientHeight}px`
)
console.log(
  `${gutter}left ${round(m.barLeft)}px and right ${round(m.barRight)}px within a ` +
  `${m.viewportWidth}px frame, document scrollWidth ${m.docScrollWidth}px`
)
for (const [name, box] of [['#root', m.root], ['.widget-root', m.widgetRoot]]) {
  const edges =
    box === null ? 'never rendered' : `left ${round(box.left)}px and right ${round(box.right)}px`
  console.log(`  ${name.padEnd(14)}  ${edges} within a ${m.viewportWidth}px frame`)
  if (box !== null) {
    console.log(
      `${gutter}top ${round(box.top)}px and bottom ${round(box.bottom)}px within a ` +
      `${m.viewportHeight}px frame`
    )
  }
}
for (const [phase, c] of [['empty', m.empty], ['filled', m.filled]]) {
  for (const [name, box] of [['.input-bar', c.inputBar], ['button.send', c.sendButton]]) {
    const edges =
      box === null
        ? 'never rendered'
        : `top ${round(box.top)}px and bottom ${round(box.bottom)}px, ${round(box.width)}x` +
          `${round(box.height)}px`
    console.log(`  ${name.padEnd(14)}  ${phase.padEnd(6)} ${edges} within a ${m.viewportHeight}px frame`)
  }
  console.log(
    `  ${'.scroll-area'.padEnd(14)}  ${phase.padEnd(6)} scrollHeight ${c.scrollAreaScrollHeight}px ` +
    `against clientHeight ${c.scrollAreaClientHeight}px, overflow-y ${m.scrollAreaOverflowY}, ` +
    `document scrollHeight ${c.docScrollHeight}px`
  )
}
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
  `${round(m.barHeight)}px tall, viewport ${viewport.width}px`
)
}

// What the run measured, counted rather than assumed. `framesMeasured`
// against VIEWPORTS catches a frame that never produced a measurement, and
// `childrenMeasured` is the subject of the three per-child assertions: a bar
// that rendered with no children clears all of them by having nothing in it.
const framesMeasured = measurements.length
const childrenMeasured = measurements.reduce((t, [, m]) => t + m.children.length, 0)
const controlsMeasured = measurements.reduce(
  (t, [, m]) =>
    t +
    [m.empty.inputBar, m.empty.sendButton, m.filled.inputBar, m.filled.sendButton].filter(
      (b) => b !== null
    ).length,
  0
)
// The short frames are the subject of the vertical assertions. #176 lived
// behind five frames that were all at least 600 tall: every vertical claim
// below passed because no frame could ever have contradicted it.
const SHORT_FRAME_FLOOR = 3
const shortFrames = VIEWPORTS.filter((v) => v.height < 600)

if (shortFrames.length < SHORT_FRAME_FLOOR) {
  findings.push(
    `${shortFrames.length} frame(s) shorter than 600px where ${SHORT_FRAME_FLOOR} are required, ` +
    'so the input bar is only ever measured in a frame tall enough to hold the old fixed height ' +
    'and the vertical assertions have no subject that could fail'
  )
}

if (controlsMeasured !== framesMeasured * 4) {
  findings.push(
    `${controlsMeasured} control box(es) were measured where ${framesMeasured} frame(s) x 2 ` +
    'controls x 2 transcript states is ' + framesMeasured * 4 + ', so a control never rendered ' +
    'and its frame-edge assertions passed by having no box'
  )
}

if (framesMeasured !== VIEWPORTS.length) {
  findings.push(
    `${framesMeasured} frame(s) were measured where VIEWPORTS names ${VIEWPORTS.length}, ` +
    'so a frame never reached the page'
  )
}

if (childrenMeasured === 0) {
  findings.push(
    'no child of the bar was measured in any frame, so nothing was held to a single row, ' +
    'to not being clipped, or to the frame edge'
  )
}

if (findings.length > 0) {
  console.error(`\ncheck:rendered-notice: FAIL -- ${findings.length} finding(s):`)
  for (const f of findings) console.error(`  ${f}`)
  process.exit(1)
}

console.log(
  `check:rendered-notice: PASS -- across ${framesMeasured} frame(s) at ` +
  `${VIEWPORTS.map((v) => `${v.width}x${v.height}`).join(', ')}, ${childrenMeasured} measured child(ren) ` +
  'of the bar render on one row, none is clipped, the bar overflows in neither axis, and nothing ' +
  `reaches past the frame edge. In ${shortFrames.length} frame(s) shorter than 600px, and in every ` +
  `other, ${controlsMeasured} measured control box(es) sit inside the frame's bottom edge on an ` +
  'empty transcript and with 2000px pushed into it, which .scroll-area absorbs.'
)
