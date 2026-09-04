#!/usr/bin/env node
// check-chart-render.mjs
//
// The eval chart's numerals are pins: absolutely positioned boxes the layout
// effect in TelemetryChart.tsx places in a 148px gutter, one per series, each
// pushed down until it clears the pin above it. Where that stack runs past the
// bottom of the chart the effect shifts it back up. Nothing in the repo watched
// the result. tsc, test:unit, check:no-dusk-tokens and check:ops-room-wiring all
// stayed green with the whole clamp deleted, and the pin that then landed on the
// judge's paragraph was found by hand, in a browser, by a reviewer.
//
// This gate is that browser. It compiles the real TelemetryChart.tsx and
// evalSeries.ts, mounts them with the real React the app ships and the real
// app/globals.css, and measures every pin's bounding box against the chart
// wrap's. A pin outside the wrap fails the build. So does a pair of pins that
// overlap, which is the same collision loop getting its gap wrong rather than
// its clamp.
//
// Fixtures are the shapes that break it, not the shape that works. The pin
// stack has two halves and the second one is where the bug lived: a series the
// latest run measured takes a slot by its value, and a series it did not
// measure is parked below the whole stack. Four series with three of them
// unmeasured pushes that tail furthest, and doing it across both datasets makes
// each pin three lines instead of two, which is the tallest column the gutter
// is ever asked to hold.
//
// Four viewports, because the wrap's height follows the trace's width and the
// trace is `calc(100% - 168px)` of a container that follows the viewport. The
// chart is at its shortest just above 900px, where the gutter still exists and
// the drawing has shrunk with the page, so 910 is the run that catches a column
// too tall for its box. Below 900px the leaders switch off and the pins become
// a static grid, and 860 is here to prove that path still puts them inside.

import { execFileSync } from 'node:child_process'
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, relative } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from '@playwright/test'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const ADMIN = join(__dirname, '..')
const REPO_ROOT = join(ADMIN, '..', '..')
const EVAL_DIR = join(ADMIN, 'app', 'agents', '[id]', 'eval')
const GLOBALS = join(ADMIN, 'app', 'globals.css')
const require_ = createRequire(import.meta.url)

const label = (path) => relative(REPO_ROOT, path).replace(/\\/g, '/')
const round = (n) => Math.round(n * 10) / 10

// ── the fixtures ───────────────────────────────────────────────────────────
// Runs in the shape `GET /api/v1/agents/{id}/eval-runs` returns them, built the
// way evalSeries.ts reads them: a `metrics` block when exactly one dataset
// scored, a `datasets` block when both did. `aggregate_scores` is filled with
// the 0.0 projection the route really sends, so a fixture cannot pass by having
// been handed cleaner data than production sends.

const CHANNELS = ['faithfulness', 'answer_relevancy', 'context_recall', 'context_precision']
const measurement = (v) =>
  v === null ? { value: null, measured: false, observations: 0 } : { value: v, measured: true, observations: 6 }
const metrics = (values) => Object.fromEntries(CHANNELS.map((k, i) => [k, measurement(values[i] ?? null)]))
const projection = (values) => Object.fromEntries(CHANNELS.map((k, i) => [k, values[i] ?? 0]))
const outcome = (values) => ({
  scenario_count: 6,
  valid_scenario_count: 6,
  scored_scenario_count: 6,
  metrics: metrics(values),
})
const day = (i) => `2026-08-${String(i + 1).padStart(2, '0')}T00:00:00Z`

// One dataset scored, so the run carries a run-level `metrics` block.
const oneSet = (i, values) => ({
  id: `run-${i}`,
  started_at: day(i),
  finished_at: day(i),
  status: 'complete',
  scenario_count: 6,
  metrics: metrics(values),
  metrics_dataset: 'exploratory',
  aggregate_scores: projection(values),
})

// Both datasets scored, so every run-level metric is unmeasured by construction
// and the two halves live under `datasets`.
const bothSets = (i, golden, exploratory) => ({
  id: `run-${i}`,
  started_at: day(i),
  finished_at: day(i),
  status: 'complete',
  scenario_count: 12,
  metrics: metrics([]),
  metrics_dataset: null,
  aggregate_scores: projection([]),
  datasets: { available: true, golden: outcome(golden), exploratory: outcome(exploratory) },
})

// One run that measured nothing, which is what the chart's empty branch
// renders. Two fixtures use it: one mounts it on its own, one arrives at it
// from a chart that had pins.
const nothingRun = {
  ...oneSet(0, []),
  metrics: metrics([]),
  metrics_dataset: null,
  datasets: { available: false },
}

// Four three-line pins clustered on the gate, the tallest column the gutter
// ever holds and so the largest min-height the layout effect ever writes.
const clusteredRuns = [
  bothSets(0, [0.9, 0.899], [0.898, 0.897]),
  bothSets(1, [0.901, 0.9], [0.899, 0.898]),
  bothSets(2, [0.9005, 0.9004], [0.9003, 0.9002]),
]

const FIXTURES = [
  {
    id: 'four-measured',
    about: 'four channels on one dataset, every one measured on the latest run',
    pins: 4,
    runs: [
      oneSet(0, [0.95, 0.91, 0.88, 0.86]),
      oneSet(1, [0.948, 0.916, 0.89, 0.872]),
      oneSet(2, [0.946, 0.922, 0.9, 0.884]),
    ],
  },
  {
    // The block. One pin takes a slot by its value and three are parked below
    // it, and before the fix the clamp only ever moved the first of those four.
    id: 'three-unmeasured',
    about: 'four channels on one dataset, the latest run measured one of them',
    pins: 4,
    runs: [
      oneSet(0, [0.95, 0.91, 0.88, 0.86]),
      oneSet(1, [0.948, 0.916, 0.89, 0.872]),
      { ...oneSet(2, [0.946, 0.922, 0.9, 0.884]), metrics: metrics([null, null, 0.9, null]) },
    ],
  },
  {
    // The same shape with three-line pins: two channels across both datasets is
    // four series, which is still a gutter and not the legend grid.
    id: 'both-sets-three-unmeasured',
    about: 'two channels across both datasets, the latest run measured one of the four',
    pins: 4,
    runs: [
      bothSets(0, [0.96, 0.93], [0.94, 0.9]),
      bothSets(1, [0.955, 0.934], [0.936, 0.906]),
      bothSets(2, [], [0.932]),
    ],
  },
  {
    // Four three-line pins all measured and all within a thousandth of the
    // gate, so the collision loop stacks them at the full gap and the column is
    // the tallest the gutter ever holds.
    id: 'both-sets-clustered',
    about: 'two channels across both datasets, all four measured and clustered on the gate',
    pins: 4,
    runs: clusteredRuns,
  },
  {
    // Every series measured on the latest run and on no earlier one, so each is
    // a single point with no line to draw it into, sitting at the head of the
    // chart. That point gets a mark from the trace and a head from the leader,
    // both at x = CHART_X1, and this is the fixture that puts two marks on one
    // point. Both datasets, so a golden open ring and an exploratory filled dot
    // are each covered.
    id: 'lone-latest-both-sets',
    about: 'two channels across both datasets, measured on the latest run and on no other',
    pins: 4,
    runs: [
      bothSets(0, [], []),
      bothSets(1, [], []),
      bothSets(2, [0.95, 0.91], [0.93, 0.89]),
    ],
  },
  {
    // Above four series the pins leave the gutter for the legend grid. The
    // clamp does not run here, and this fixture is what says so.
    id: 'eight-series',
    about: 'four channels across both datasets, the legend grid rather than the gutter',
    pins: 8,
    runs: [
      bothSets(0, [0.96, 0.93, 0.9, 0.88], [0.94, 0.9, 0.87, 0.85]),
      bothSets(1, [0.955, 0.934, 0.902, 0.884], [0.936, 0.906, 0.872, 0.854]),
      bothSets(2, [0.958, 0.936, 0.904, 0.886], [0.938, 0.908, 0.874, 0.856]),
    ],
  },
  {
    id: 'one-run',
    about: 'a single run, four channels measured',
    pins: 4,
    runs: [oneSet(0, [0.95, 0.91, 0.88, 0.86])],
  },
  {
    // No pins at all. The chart says so in a sentence, and the sentence has to
    // announce itself, because it replaces a loading state.
    id: 'nothing-measured',
    about: 'one run that recorded no measurement at all',
    pins: 0,
    announces: true,
    runs: [nothingRun],
  },
  {
    // The same empty state, reached the way a tenant reaches it. Moving
    // between two agents' eval pages is a re-render, not a fresh mount:
    // React keeps the wrap element across `.telemetry` and
    // `.telemetry.empty`, so anything written on it in pixels by the chart
    // that had pins is still there under the chart that has none. Mounting
    // each fixture into a fresh root, which is what every fixture above
    // does, is the one way to miss it.
    id: 'nothing-measured-after-pins',
    about: 'four three-line pins, then a run that recorded no measurement at all',
    pins: 0,
    announces: true,
    before: clusteredRuns,
    runs: [nothingRun],
  },
]

const VIEWPORTS = [
  { width: 1280, height: 900, about: 'a full-width desk' },
  { width: 950, height: 900, about: 'a narrow desk' },
  { width: 910, height: 900, about: 'the shortest chart the gutter ever gets' },
  { width: 860, height: 900, about: 'below the 900px breakpoint, where the pins are a grid' },
]

// ── the floor ──────────────────────────────────────────────────────────────
// The PASS line at the bottom names four assertions, and each one needs a
// subject the run supplied. Nothing here supplied one. `FIXTURES = []` and
// `VIEWPORTS = []` each ran the measuring loop zero times and printed that
// sentence over nothing, exit 0. A gate over zero observations is unknown,
// never pass.
//
// A fixture's shape is checked before the browser starts, because a fixture is
// the subject. `pins` is a number this file writes down rather than anything
// the chart reports, so `pins: 0` holds nothing to anything: the pin-count
// comparison passes 0 against 0, and every per-pin assertion under it walks an
// empty list while the line above still prints. The one zero-pin shape this
// gate can assert against is the empty state, which has a sentence to
// announce, so a fixture with no pins has to be that one.

const shapeErrors = []
if (FIXTURES.length === 0) shapeErrors.push('FIXTURES is empty, so no chart renders and nothing is measured')
if (VIEWPORTS.length === 0) shapeErrors.push('VIEWPORTS is empty, so no page opens and nothing is measured')

for (const [i, fixture] of FIXTURES.entries()) {
  const where = `FIXTURES[${i}] (${fixture.id ?? 'unnamed'})`
  if (typeof fixture.id !== 'string' || fixture.id === '') shapeErrors.push(`${where} carries no id`)
  if (!Array.isArray(fixture.runs) || fixture.runs.length === 0) {
    shapeErrors.push(`${where} carries no runs, so the chart is handed nothing to draw`)
  }
  if (fixture.before !== undefined && (!Array.isArray(fixture.before) || fixture.before.length === 0)) {
    shapeErrors.push(`${where} declares a 'before' that is not a list of runs`)
  }
  if (!Number.isInteger(fixture.pins) || fixture.pins < 0) {
    shapeErrors.push(`${where} declares pins ${JSON.stringify(fixture.pins)}, which counts nothing`)
  } else if (fixture.pins === 0 && fixture.announces !== true) {
    shapeErrors.push(
      `${where} declares no pins and does not announce, so every assertion it reaches has an empty ` +
        'subject while its measured line still prints. A fixture with no pins is the empty state, ' +
        'and the empty state announces: give it `announces: true`, or give it pins',
    )
  }
}

if (shapeErrors.length > 0) {
  console.error(`check:chart-render: FAIL -- ${shapeErrors.length} fixture problem(s), so the run would assert nothing:`)
  for (const e of shapeErrors) console.error(`  ${e}`)
  process.exit(1)
}

// ── build the page ─────────────────────────────────────────────────────────
// The component under test is compiled, never re-implemented. tsc turns the two
// real source files into CommonJS, a five-line module registry stands in for a
// bundler, and React comes out of the same node_modules `next build` uses.

const work = mkdtempSync(join(tmpdir(), 'wchats-chart-render-'))
const src = join(work, 'src')
const out = join(work, 'out')
mkdirSync(src)

for (const file of ['evalSeries.ts', 'TelemetryChart.tsx']) copyFileSync(join(EVAL_DIR, file), join(src, file))

writeFileSync(
  join(work, 'tsconfig.json'),
  JSON.stringify({
    compilerOptions: {
      target: 'ES2020',
      module: 'commonjs',
      jsx: 'react-jsx',
      strict: false,
      esModuleInterop: true,
      skipLibCheck: true,
      outDir: out,
      types: [],
      typeRoots: [join(ADMIN, 'node_modules', '@types')],
      baseUrl: join(ADMIN, 'node_modules'),
    },
    files: [join(src, 'evalSeries.ts'), join(src, 'TelemetryChart.tsx')],
  }),
)

const tsc = join(dirname(require_.resolve('typescript')), '..', 'bin', 'tsc')
try {
  execFileSync(process.execPath, [tsc, '-p', join(work, 'tsconfig.json')], { stdio: 'pipe' })
} catch (error) {
  console.error(
    `check:chart-render: FAIL -- ${label(join(EVAL_DIR, 'TelemetryChart.tsx'))} did not compile, ` +
      'so nothing was rendered:\n' +
      String(error.stdout || error.message),
  )
  process.exit(1)
}

const reactDir = dirname(require_.resolve('react'))
const domDir = dirname(require_.resolve('react-dom'))
const schedulerFile = createRequire(join(domDir, 'index.js')).resolve('scheduler/cjs/scheduler.development.js')

// The development builds, because they are the ones that carry `cjs/`. What is
// measured is layout, and layout does not change between the two.
const MODULES = {
  react: join(reactDir, 'cjs', 'react.development.js'),
  'react/jsx-runtime': join(reactDir, 'cjs', 'react-jsx-runtime.development.js'),
  scheduler: schedulerFile,
  'react-dom': join(domDir, 'cjs', 'react-dom.development.js'),
  'react-dom/client': join(domDir, 'cjs', 'react-dom-client.development.js'),
  './evalSeries': join(out, 'evalSeries.js'),
  './TelemetryChart': join(out, 'TelemetryChart.js'),
}

// Each module goes to its own file beside the page rather than inline, so no
// closing tag inside a bundle can end the script that carries it.
const tags = Object.entries(MODULES)
  .map(([name, file], i) => {
    writeFileSync(
      join(work, `mod-${i}.js`),
      `__define(${JSON.stringify(name)}, function (module, exports, require) {\n${readFileSync(file, 'utf8')}\n});`,
    )
    return `<script src="mod-${i}.js"></script>`
  })
  .join('\n')

// The page the chart really renders in: the same font link layout.tsx ships,
// the same globals.css, the same .deck > .page containers, and the judge's
// paragraph underneath, which is what a runaway pin lands on.
writeFileSync(
  join(work, 'page.html'),
  `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&family=Newsreader:ital,opsz,wght@0,6..72,400;1,6..72,400;1,6..72,500&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<style id="globals">${readFileSync(GLOBALS, 'utf8')}</style>
<style id="chart"></style>
</head>
<body>
<main class="deck"><div class="page"><div id="root"></div></div></main>
<script>var process = { env: { NODE_ENV: 'development' } };
var __defs = {}, __cache = {};
function __define(n, f) { __defs[n] = f }
function require(n) {
  if (__cache[n]) return __cache[n].exports
  if (!__defs[n]) throw new Error('no module ' + n)
  var m = { exports: {} }; __cache[n] = m; __defs[n](m, m.exports, require); return m.exports
}</script>
${tags}
<script>
// __render draws into whatever root is already there, __mount makes a new
// one first. The two are separate because a tenant moving between agents
// gets the first and not the second, and the element the layout effect
// writes pixels onto survives that.
window.__render = function (runs) {
  var React = require('react')
  var chart = require('./TelemetryChart')
  var css = getComputedStyle(document.documentElement)
  var colors = ['--ch-1', '--ch-2', '--ch-3', '--ch-4'].map(function (v) { return css.getPropertyValue(v).trim() })
  window.__root.render(
    React.createElement(React.Fragment, null,
      React.createElement(chart.TelemetryChart, { runs: runs, colors: colors }),
      React.createElement('section', { className: 'judge' },
        React.createElement('p', { className: 'voice verdict' },
          'All 6 scenarios held on this run. The gate stays open.'))))
}
window.__mount = function (runs) {
  var client = require('react-dom/client')
  var chart = require('./TelemetryChart')
  document.getElementById('chart').textContent = chart.TELEMETRY_CSS +
    '.judge { margin-top: 8px; padding-top: 22px; border-top: 1px solid var(--hairline-strong); }'
  if (window.__root) window.__root.unmount()
  window.__root = client.createRoot(document.getElementById('root'))
  window.__render(runs)
}
</script>
</body></html>`,
)

// ── render and measure ─────────────────────────────────────────────────────

// One page per viewport, remounted per fixture. Loading the page again for
// every fixture spends most of a minute re-parsing React's development build,
// which is the same build every time.
async function measure(page, fixture, errors) {
  errors.length = 0
  await page.evaluate((runs) => window.__mount(runs), fixture.before ?? fixture.runs)
  // The layout effect runs again when the webfont lands, and the pin's height
  // is a font measurement, so nothing is read before that second pass.
  await page.evaluate(() => document.fonts.ready)
  await page.waitForTimeout(300)
  // A fixture with `before` is measured on its second chart, drawn into the
  // root the first one is already in.
  if (fixture.before) {
    await page.evaluate((runs) => window.__render(runs), fixture.runs)
    await page.waitForTimeout(200)
  }

  const seen = await page.evaluate(() => {
    const wrap = document.querySelector('.telemetry')
    if (!wrap) return null
    const wrapBox = wrap.getBoundingClientRect()
    const judgeBox = document.querySelector('.judge').getBoundingClientRect()
    const nodes = [...document.querySelectorAll('.pin')]
    const pins = nodes.map((el) => {
      const box = el.getBoundingClientRect()
      return {
        value: el.querySelector('.pin-val').textContent,
        name: el.querySelector('.pin-name').textContent,
        set: el.querySelector('.pin-set')?.textContent ?? '',
        top: box.top,
        bottom: box.bottom,
        height: box.height,
      }
    })
    const collisions = []
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i].getBoundingClientRect()
        const b = nodes[j].getBoundingClientRect()
        const acrossX = Math.min(a.right, b.right) - Math.max(a.left, b.left)
        const acrossY = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
        if (acrossX > 0.5 && acrossY > 0.5) collisions.push({ a: i, b: j, overlap: acrossY })
      }
    }
    // Every mark the chart draws, from both SVGs, in screen pixels. .trace
    // carries a viewBox and .leaders does not, so a radius written once in
    // the source arrives here at two sizes unless something reconciles them,
    // and only the rendered box says which.
    const circles = [...wrap.querySelectorAll('svg circle')].map((el) => {
      const box = el.getBoundingClientRect()
      return {
        where: el.ownerSVGElement ? `.${el.ownerSVGElement.getAttribute('class')}` : '(detached)',
        cx: (box.left + box.right) / 2,
        cy: (box.top + box.bottom) / 2,
        across: box.width,
      }
    })

    // Where the trace really puts points: every vertex of every polyline and
    // the centre of every mark, mapped out of viewBox units through the
    // trace's own transform. A leader head claims a point on the trace, and
    // this is the list of points there are.
    const traceSvg = wrap.querySelector('svg.trace')
    const ctm = traceSvg ? traceSvg.getScreenCTM() : null
    const traceVertices = []
    if (ctm) {
      const at = (x, y) => {
        const point = new DOMPoint(x, y).matrixTransform(ctm)
        return { x: point.x, y: point.y }
      }
      for (const line of traceSvg.querySelectorAll('polyline')) {
        for (const pair of (line.getAttribute('points') || '').trim().split(/\s+/)) {
          const [x, y] = pair.split(',').map(Number)
          traceVertices.push(at(x, y))
        }
      }
      for (const mark of traceSvg.querySelectorAll('circle')) {
        traceVertices.push(at(Number(mark.getAttribute('cx')), Number(mark.getAttribute('cy'))))
      }
    }

    const notice = wrap.querySelector('.no-readings')
    const noticeBox = notice ? notice.getBoundingClientRect() : null
    const wrapStyle = getComputedStyle(wrap)
    return {
      pins,
      collisions,
      circles,
      traceVertices,
      // What the wrap is padded by, read rather than copied out of
      // TELEMETRY_CSS, so the sentence-fits-its-box sum below stays true when
      // the padding changes.
      wrapPadding: parseFloat(wrapStyle.paddingTop) + parseFloat(wrapStyle.paddingBottom),
      // The inline pixels the layout effect writes. Read so a box held open
      // by them can say what is holding it.
      minHeightStyle: wrap.style.minHeight,
      wrapTop: wrapBox.top,
      wrapBottom: wrapBox.bottom,
      wrapHeight: wrapBox.height,
      judgeTop: judgeBox.top,
      stacked: wrap.classList.contains('stacked'),
      notice: notice
        ? {
            text: notice.textContent,
            role: notice.getAttribute('role'),
            live: notice.getAttribute('aria-live'),
            height: noticeBox.height,
          }
        : null,
    }
  })

  return { seen, errors: [...errors] }
}

const results = []
const browser = await chromium.launch()
try {
  for (const viewport of VIEWPORTS) {
    const page = await browser.newPage({
      viewport: { width: viewport.width, height: viewport.height },
      deviceScaleFactor: 1,
    })
    const errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.goto(pathToFileURL(join(work, 'page.html')).href, { waitUntil: 'domcontentloaded' })
    for (const fixture of FIXTURES) {
      results.push({ fixture, viewport, ...(await measure(page, fixture, errors)) })
    }
    await page.close()
  }
} finally {
  await browser.close()
  rmSync(work, { recursive: true, force: true })
}

const findings = []
let marksMeasured = 0
let headsMeasured = 0
let coincidentMarks = 0

for (const { fixture, viewport, seen, errors } of results) {
  const where = `${fixture.id} at ${viewport.width}px`
  const note = (text) => findings.push(`${where}: ${text}`)

  for (const error of errors) note(`the page threw "${error}"`)

  if (seen === null) {
    note(`no .telemetry rendered at all, so nothing was measured (${fixture.about})`)
    continue
  }

  console.log(`  ${where.padEnd(38)} ${fixture.about}`)
  console.log(
    `    wrap ${round(seen.wrapTop)} to ${round(seen.wrapBottom)} (${round(seen.wrapHeight)}px tall), ` +
      `judge top ${round(seen.judgeTop)}, ${seen.stacked ? 'legend grid' : 'gutter'}, ` +
      `${seen.pins.length} pin(s)`,
  )

  // A fixture whose pins never rendered would otherwise pass every assertion
  // below by having nothing to assert against.
  if (seen.pins.length !== fixture.pins) {
    note(`${seen.pins.length} pins rendered where the fixture holds ${fixture.pins} series`)
  }

  for (const pin of seen.pins) {
    const name = `"${pin.value} ${pin.name}${pin.set ? ` / ${pin.set}` : ''}"`
    console.log(
      `    ${name.padEnd(46)} top ${String(round(pin.top)).padStart(6)} bottom ${String(round(pin.bottom)).padStart(6)}` +
        `  ${round(pin.height)}px tall`,
    )
    if (pin.bottom > seen.wrapBottom + 0.5) {
      note(
        `${name} runs ${round(pin.bottom - seen.wrapBottom)}px past the bottom of the chart, ` +
          `bottom ${round(pin.bottom)} against a wrap ending at ${round(seen.wrapBottom)}` +
          (pin.bottom > seen.judgeTop + 0.5 ? `, and ${round(pin.bottom - seen.judgeTop)}px onto the judge` : ''),
      )
    }
    if (pin.top < seen.wrapTop - 0.5) {
      note(
        `${name} starts ${round(seen.wrapTop - pin.top)}px above the top of the chart, ` +
          `top ${round(pin.top)} against a wrap starting at ${round(seen.wrapTop)}`,
      )
    }
  }

  // Two marks on the same point have to be one mark. The head of a trace is
  // drawn in .leaders, which is CSS pixels, and a lone reading's mark is drawn
  // in .trace, which the viewBox scales, so a single nominal radius rendered
  // at two sizes and the smaller sat inside the larger. On a golden series
  // that is an open ring with a mark in the middle of it, which reads as a
  // filled dot and so as the exploratory encoding.
  const coincident = []
  for (let i = 0; i < seen.circles.length; i++) {
    for (let j = i + 1; j < seen.circles.length; j++) {
      const a = seen.circles[i]
      const b = seen.circles[j]
      if (Math.abs(a.cx - b.cx) > 1 || Math.abs(a.cy - b.cy) > 1) continue
      coincident.push([a, b])
      if (Math.abs(a.across - b.across) > 0.5) {
        note(
          `a mark in ${a.where} and a mark in ${b.where} sit on the same point ` +
            `(${round(a.cx)}, ${round(a.cy)}) and render ${round(a.across)}px and ` +
            `${round(b.across)}px across, so the smaller is drawn inside the larger`,
        )
      }
    }
  }
  // A leader head marks a point on the trace, and its line leads from there to
  // the pin. Nothing tied the two together: the head's x was CHART_X1 whatever
  // the data did, so a chart with a single run put every point in the middle of
  // the axis and every head against the right edge, 427px away at 1280px, each
  // one a mark on empty ground with a leader running off it.
  const heads = seen.circles.filter((c) => c.where === '.leaders')
  for (const head of heads) {
    let nearest = Infinity
    for (const v of seen.traceVertices) {
      nearest = Math.min(nearest, Math.hypot(v.x - head.cx, v.y - head.cy))
    }
    if (nearest > 1) {
      note(
        `a leader head at (${round(head.cx)}, ${round(head.cy)}) sits on no point the trace ` +
          `draws, the nearest one being ${round(nearest)}px away, so the head marks empty ground`,
      )
    }
  }
  headsMeasured += heads.length
  marksMeasured += seen.circles.length
  coincidentMarks += coincident.length
  console.log(
    `    ${String(seen.circles.length).padStart(2)} mark(s) drawn, ${coincident.length} pair(s) of ` +
      `them on the same point`,
  )

  // The empty branch draws a sentence, and the box around it belongs to the
  // sentence. The gutter's min-height is written onto this same element in
  // pixels, and React reuses the element between the two branches, so a
  // column of pins from the chart before this one can still be holding the
  // box open around 20px of text.
  if (fixture.pins === 0 && seen.notice !== null) {
    const fits = seen.notice.height + seen.wrapPadding
    if (seen.wrapHeight > fits + 0.5) {
      note(
        `the empty chart's box is ${round(seen.wrapHeight)}px tall around a ` +
          `${round(seen.notice.height)}px sentence and ${round(seen.wrapPadding)}px of padding, so ` +
          `${round(seen.wrapHeight - fits)}px of it is empty ground` +
          (seen.minHeightStyle
            ? `, held open by an inline min-height of ${seen.minHeightStyle} the gutter left behind`
            : ''),
      )
    }
  }

  for (const hit of seen.collisions) {
    const a = seen.pins[hit.a]
    const b = seen.pins[hit.b]
    note(
      `"${a.name}${a.set ? ` / ${a.set}` : ''}" and "${b.name}${b.set ? ` / ${b.set}` : ''}" ` +
        `overlap by ${round(hit.overlap)}px, so the gap between two pins is smaller than a pin`,
    )
  }

  // The empty state replaces a loading state, so a listener who is told the
  // chart is loading has to be told when it stops.
  if (fixture.announces) {
    if (seen.notice === null) {
      note('the chart recorded nothing and said nothing: no .no-readings sentence rendered')
    } else {
      console.log(
        `    .no-readings role="${seen.notice.role}" aria-live="${seen.notice.live}"  "${seen.notice.text}"`,
      )
      if (seen.notice.role !== 'status' || seen.notice.live !== 'polite') {
        note(
          `the "nothing measured" sentence carries role="${seen.notice.role}" aria-live="${seen.notice.live}", ` +
            'so the move out of the loading state is announced to nobody. The judge\'s sentence ' +
            'uses role="status" aria-live="polite"',
        )
      }
    }
  }
}

// What the run measured, counted rather than assumed. Each count is the
// subject of one clause in the PASS line, and the line prints it beside the
// claim, so a reader can see the assertion had something to bite on.
const renders = results.length
const expectedRenders = FIXTURES.length * VIEWPORTS.length
const pinsMeasured = results.reduce((t, r) => t + (r.seen?.pins.length ?? 0), 0)
const pairsMeasured = results.reduce((t, r) => {
  const k = r.seen?.pins.length ?? 0
  return t + (k * (k - 1)) / 2
}, 0)
const announced = results.filter((r) => r.fixture.announces && r.seen?.notice).length
const announcedAfterPins = results.filter(
  (r) => r.fixture.announces && r.fixture.before && r.seen?.notice,
).length

if (renders !== expectedRenders) {
  findings.push(
    `${renders} render(s) finished where ${FIXTURES.length} fixtures across ${VIEWPORTS.length} ` +
      `viewports are ${expectedRenders}, so a fixture never reached the page`,
  )
}
if (pinsMeasured === 0) {
  findings.push('not one pin was measured in the whole run, so no pin was held to the chart wrap')
}
if (pairsMeasured === 0) {
  findings.push('no render put two pins on the page at once, so no pair of pins was held apart')
}
if (announced === 0) {
  findings.push('no fixture reached the empty state, so nothing was held to announcing it')
}
if (marksMeasured === 0) {
  findings.push('the chart drew no marks anywhere in the run, so no mark was measured at all')
}
if (headsMeasured === 0) {
  findings.push('no leader head was drawn in the run, so none was held to a point on the trace')
}
if (coincidentMarks === 0) {
  findings.push(
    'no render put two marks on the same point, so nothing was held to a head and a lone ' +
      'reading being one mark rather than two sizes',
  )
}
if (announcedAfterPins === 0) {
  findings.push(
    'no fixture reached the empty state by re-rendering over a chart with pins, so nothing was ' +
      'held to letting go of the gutter it inherited',
  )
}

if (findings.length > 0) {
  console.error(`\ncheck:chart-render: FAIL -- ${findings.length} finding(s):`)
  for (const finding of findings) console.error(`  ${finding}`)
  process.exit(1)
}

console.log(
  `\ncheck:chart-render: PASS -- ${renders} render(s), ${FIXTURES.length} fixtures at ` +
    `${VIEWPORTS.map((v) => `${v.width}px`).join(', ')}: ${pinsMeasured} pin(s) measured and every one ` +
    `inside the chart wrap, ${pairsMeasured} pin pair(s) measured and none overlapping, ` +
    `${marksMeasured} mark(s) drawn with ${coincidentMarks} pair(s) on the same point and each pair ` +
    `one size, ${headsMeasured} leader head(s) each on a point the trace draws, and the empty ` +
    `state announced itself in ${announced} render(s), ${announcedAfterPins} of them over a chart ` +
    `that had pins, each as tall as the sentence in it.`,
)
