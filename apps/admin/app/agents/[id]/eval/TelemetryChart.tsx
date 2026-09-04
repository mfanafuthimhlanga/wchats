'use client'
import { useEffect, useRef } from 'react'
import {
  buildEvalSeries,
  datasetsCovered,
  describeChart,
  describeSeries,
  seriesDomain,
  seriesSegments,
  type EvalRun,
  type EvalSeries,
} from './evalSeries'

// ---------------------------------------------------------------------------
// Telemetry chart, the VITALS leader-line pattern ported from eval.html's
// layout() script. Numerals are pinned to the head of their own trace via a
// pixel-space leader line; collision-avoidance (MIN_GAP) keeps them apart.
// Collapses to a static 2-col .pin grid under 900px (leaders hidden).
// ---------------------------------------------------------------------------

// Short axis-tick date, "2026-07-06" (UTC).
function formatShortDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10)
}

const CHART_VB_W = 640
const CHART_VB_H = 200
const CHART_X0 = 40
const CHART_X1 = 600
const CHART_Y_TOP = 24
const CHART_Y_BOTTOM = 176
const GATE_VALUE = 0.9
// A pin is two lines when one dataset covers the whole suite and three when the
// dataset has to be named, so the gap that keeps two pins apart follows it. 44
// is the value the chart shipped with, and every tenant who has not curated a
// golden set still gets exactly that layout. Both are floors: the layout effect
// measures the pin it is spacing and takes whichever is larger, because a
// constant guessed at the height of rendered text is a constant that will one
// day be wrong. 58 already was.
const PIN_GAP_TWO_LINE = 44
const PIN_GAP_THREE_LINE = 58
const PIN_GUTTER = 148
// The gutter holds four leader lines. A tenant with a designated golden set has
// eight series, and stacking those in a 200px-tall gutter slides the last of
// them off the bottom of the chart, so above four the pins become the legend
// grid the narrow breakpoint already uses and the trace takes the full width.
const MAX_GUTTER_PINS = 4

// The dataset's secondary encoding. Colour belongs to the channel (--ch-1..4,
// bone luminance, read by weight), so the two halves of one metric share a hue
// and differ in stroke and weight. Exploratory, the half every tenant has,
// keeps the solid 1.7px line this chart has always drawn; the golden set
// arrives thinner and dotted, so curating a golden set adds lines without
// repainting the ones already there. The gate's dash is 10 6 and its line is
// horizontal, which is what keeps these two apart where the tone does not.
const GOLDEN_DASH = '1 4'
const GOLDEN_WIDTH = 1.4
const TRACE_WIDTH = 1.7
const dashFor = (series: EvalSeries) =>
  series.dataset === 'golden' ? GOLDEN_DASH : undefined
const widthFor = (series: EvalSeries) =>
  series.dataset === 'golden' ? GOLDEN_WIDTH : TRACE_WIDTH
// The mark that stands in for a lone measured run. It replaced a 10-unit
// stroke, which carried the golden series' "1 4" dash across ten units and so
// rendered as two loose pixels.
const POINT_RADIUS = 2.6

export function TelemetryChart({ runs, colors }: { runs: EvalRun[]; colors: string[] }) {
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const traceRef = useRef<SVGSVGElement | null>(null)
  const leadersRef = useRef<SVGSVGElement | null>(null)
  const pinRefs = useRef<(HTMLDivElement | null)[]>([])

  const n = runs.length

  // One series per channel per dataset that actually measured something. Nothing
  // below reads `aggregate_scores`, where an unmeasured metric reads 0.0 (#119).
  const series = buildEvalSeries(runs)
  const stacked = series.length > MAX_GUTTER_PINS
  // The dataset is named on the pin only when there is more than one of them.
  // Printing "exploratory sample" four times on a tenant with no golden set
  // distinguishes nothing and costs the pin a third line.
  const showDataset = datasetsCovered(series).length > 1
  const pinGap = showDataset ? PIN_GAP_THREE_LINE : PIN_GAP_TWO_LINE

  // The scale describes the readings. Over `aggregate_scores` it used to take in
  // the 0.0 projections, which dropped the whole axis onto the floor the moment
  // one metric went unmeasured.
  const { min, max } = seriesDomain(series)
  const dataMin = min ?? 0.82
  const dataMax = max ?? 0.98
  const yMin = Math.max(0, Math.min(dataMin - 0.04, 0.86))
  const yMax = Math.min(1, Math.max(dataMax + 0.03, 0.92))
  const yRange = yMax - yMin || 1

  const yFor = (v: number) => CHART_Y_BOTTOM - ((v - yMin) / yRange) * (CHART_Y_BOTTOM - CHART_Y_TOP)
  const xFor = (i: number) =>
    n <= 1 ? (CHART_X0 + CHART_X1) / 2 : CHART_X0 + (i / (n - 1)) * (CHART_X1 - CHART_X0)

  // Segments, never one polyline per series: a run that measured nothing breaks
  // the line instead of being drawn through. A one-point segment becomes a mark
  // rather than a line, because a dash pattern needs a length to be read along
  // and ten units is not one. The dataset stays encoded: exploratory is filled
  // and golden is an open ring, which is the same solid-against-interrupted
  // reading its dash carries. A single run renders the same way.
  const segmentsFor = series.map((s) =>
    seriesSegments(s.values).map((segment) =>
      segment.map((point) => ({
        index: point.index,
        x: xFor(point.index),
        y: yFor(point.value),
      })),
    ),
  )

  const TICK_COUNT = 4
  const ticks = Array.from({ length: TICK_COUNT }, (_, i) => yMax - (i * yRange) / (TICK_COUNT - 1))
  const gateY = yFor(GATE_VALUE)
  const gateVisible = GATE_VALUE >= yMin && GATE_VALUE <= yMax

  const firstRun = runs[0] ?? null
  const lastRun = runs[n - 1] ?? null

  // ── leader-line layout (VITALS pattern), ported from eval.html's layout() ──
  useEffect(() => {
    const wrap = wrapRef.current
    const trace = traceRef.current
    const leaders = leadersRef.current
    if (!wrap || !trace || !leaders || n === 0) return

    const NS = 'http://www.w3.org/2000/svg'
    const narrow = window.matchMedia('(max-width: 900px)')

    function layout() {
      if (narrow.matches || stacked) {
        while (leaders!.firstChild) leaders!.removeChild(leaders!.firstChild)
        // The gutter's min-height goes with the gutter. Left behind, it holds a
        // legend grid open to the height of a stack that is no longer drawn.
        wrap!.style.minHeight = ''
        pinRefs.current.forEach((p) => {
          if (p) p.style.top = ''
        })
        return
      }

      const w = trace!.clientWidth
      if (!w) return
      const scale = w / CHART_VB_W

      const wrapBox = wrap!.getBoundingClientRect()
      const traceBox = trace!.getBoundingClientRect()
      const top = traceBox.top - wrapBox.top
      const left = traceBox.left - wrapBox.left

      // A pin's height in the gutter IS its value: that is what the leader line
      // teaches. So only a series the latest run measured takes a slot in that
      // space, and one reading "—" is parked below the whole stack, where its
      // position asserts nothing. It gets no dot and no leader either, because
      // there is no point on the trace to lead to.
      const led = series
        .map((s, i) => ({
          index: i,
          y: s.latest === null ? null : yFor(s.latest) * scale + top,
          colour: colors[s.colorIndex],
          dash: dashFor(s),
        }))
        .filter((r): r is typeof r & { y: number } => r.y !== null)
        .sort((a, b) => a.y - b.y)
        .map((r) => ({ ...r, lineY: r.y, slotY: r.y, lineX: CHART_X1 * scale + left }))

      // What keeps two pins apart is the height of a pin, so the gap is
      // measured and the constant is only its floor. PIN_GAP_THREE_LINE is 58
      // against a three-line pin that renders 58.7px tall, which overlapped
      // every adjacent pair in a three-line stack by 0.7px. offsetHeight is
      // read here anyway, for the half-height the clamp needs below. A two-line
      // pin measures 43.5px, under PIN_GAP_TWO_LINE, so a tenant with no golden
      // set keeps the 44px spacing the chart shipped with.
      const pinHeight = pinRefs.current.find(Boolean)?.offsetHeight ?? pinGap
      const gap = Math.max(pinGap, pinHeight)
      const pinHalf = pinHeight / 2

      for (let i = 1; i < led.length; i++) {
        if (led[i].slotY - led[i - 1].slotY < gap) {
          led[i].slotY = led[i - 1].slotY + gap
        }
      }

      // The unled pins are placed here, above the clamp, because they are the
      // bottom of the same column and the clamp has to move all of it. Clamped
      // apart, the led stack was pulled back inside the chart and these were
      // then laid out from where it used to end, so a run that measured one of
      // four series put three pins through the floor and onto the judge.
      const unledTop =
        (led.length > 0 ? led[led.length - 1].slotY : CHART_Y_TOP * scale + top) + gap
      const unled = series
        .map((s, i) => ({ index: i, latest: s.latest }))
        .filter((r) => r.latest === null)
        .map((r, k) => ({ index: r.index, slotY: unledTop + k * gap }))

      const column = [...led, ...unled]
      if (column.length > 0) {
        // The gutter is as tall as the column it holds, because a column taller
        // than the chart cannot be clamped into it: shifting it up by the whole
        // headroom still leaves the last pin outside, and every pixel of that
        // shift is taken off the top instead. Four three-line pins want 236px
        // of column and the drawing is 221px tall at 910px, the narrow end of
        // the band where a gutter exists at all.
        wrap!.style.minHeight = `${Math.ceil((column.length - 1) * gap + pinHeight)}px`
        // The push above only ever moves a pin down, so three channels within a
        // pixel of each other near the floor walk the last pin out of the chart
        // and onto the judge underneath. tests/overflow.spec.ts watches the
        // horizontal axis only, and its narrowest project is 900px, exactly where
        // leaders switch off, so this band had no gate at all until
        // scripts/check-chart-render.mjs, which measures every pin against this
        // wrap. Shift the whole column up by the overflow, as far as the
        // headroom above the first pin allows and no further.
        const overflow = column[column.length - 1].slotY - (wrap!.clientHeight - pinHalf)
        if (overflow > 0) {
          const headroom = Math.max(0, column[0].slotY - pinHalf)
          const shift = Math.min(overflow, headroom)
          for (const r of column) r.slotY -= shift
        }
      }

      const gutterX = wrap!.clientWidth - PIN_GUTTER
      while (leaders!.firstChild) leaders!.removeChild(leaders!.firstChild)

      for (const r of unled) {
        const pinEl = pinRefs.current[r.index]
        if (pinEl) pinEl.style.top = `${r.slotY}px`
      }

      led.forEach((r) => {
        const pinEl = pinRefs.current[r.index]
        if (pinEl) pinEl.style.top = `${r.slotY}px`

        // The head of the trace, in the same mark a lone reading takes: filled
        // for exploratory, an open ring for golden, so a golden head is not
        // painted in exploratory's solid encoding. It was a 10px stroke wearing
        // the series' dash, which for golden is two loose pixels.
        const head = document.createElementNS(NS, 'circle')
        head.setAttribute('cx', r.lineX.toFixed(1))
        head.setAttribute('cy', r.lineY.toFixed(1))
        head.setAttribute('r', String(POINT_RADIUS))
        if (r.dash) {
          head.setAttribute('fill', 'none')
          head.setAttribute('stroke', r.colour)
          head.setAttribute('stroke-width', String(GOLDEN_WIDTH))
        } else {
          head.setAttribute('fill', r.colour)
        }
        leaders!.appendChild(head)

        const pl = document.createElementNS(NS, 'polyline')
        pl.setAttribute(
          'points',
          [
            `${r.lineX.toFixed(1)},${r.lineY.toFixed(1)}`,
            `${(r.lineX + 14).toFixed(1)},${r.lineY.toFixed(1)}`,
            `${(gutterX - 12).toFixed(1)},${r.slotY.toFixed(1)}`,
            `${(gutterX - 1).toFixed(1)},${r.slotY.toFixed(1)}`,
          ].join(' '),
        )
        pl.setAttribute('fill', 'none')
        pl.setAttribute('stroke', r.colour)
        // 0.75px at full opacity rather than 1px at 0.7. A leader recedes by
        // being thin; transparency took --ch-4's leader to 1.97:1, and the
        // leader is the only thing tying that pin's numeral to its line.
        pl.setAttribute('stroke-width', '0.75')
        if (r.dash) pl.setAttribute('stroke-dasharray', r.dash)
        leaders!.appendChild(pl)
      })
    }

    const ro = 'ResizeObserver' in window ? new ResizeObserver(layout) : null
    if (ro) {
      ro.observe(wrap)
    } else {
      window.addEventListener('resize', layout)
    }
    // document.fonts.ready is one promise that never resolves twice, so every
    // effect run used to leave another `then` on it holding a torn-down
    // closure. They all fired at once when the font landed, each writing pin
    // positions computed against a layout that no longer existed.
    let cancelled = false
    const relayout = () => {
      if (!cancelled) layout()
    }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(relayout)
    layout()

    return () => {
      cancelled = true
      if (ro) ro.disconnect()
      else window.removeEventListener('resize', layout)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs, colors, n, stacked, pinGap])

  // The label names the picture; the sentences describing each series are a
  // list beside it. Eight of them inside one aria-label is a block a listener
  // cannot interrupt or re-read.
  const chartLabel = `${describeChart(series, n)}${
    n > 0 && series.length > 0 ? ` The gate is set at ${GATE_VALUE.toFixed(2)}.` : ''
  }`
  const seriesSentences = describeSeries(series, n)

  // A full axis drawn over nothing looks like a chart still loading. Say what
  // is true instead: runs exist and none of them recorded a measurement, which
  // is the state a tenant is in while the first run is still going.
  if (series.length === 0) {
    return (
      <div className="telemetry empty" id="telemetry" ref={wrapRef}>
        {/* The sentence replaces a loading state, so it announces itself. A
            listener who was told the chart was loading is otherwise never told
            it stopped, and this is the branch that arrives while the first run
            is still going. The judge's verdict below it does the same. */}
        <p className="no-readings" role="status" aria-live="polite">
          {describeChart(series, n)}
        </p>
      </div>
    )
  }

  return (
    <div className={stacked ? 'telemetry stacked' : 'telemetry'} id="telemetry" ref={wrapRef}>
      <svg
        className="trace"
        ref={traceRef}
        viewBox={`0 0 ${CHART_VB_W} ${CHART_VB_H}`}
        role="img"
        aria-label={chartLabel}
      >
        <g className="grid">
          {ticks.map((t, i) => (
            <line key={i} x1={CHART_X0} y1={yFor(t)} x2={CHART_X1} y2={yFor(t)} />
          ))}
        </g>
        <g className="axis">
          <line x1={CHART_X0} y1={CHART_Y_TOP} x2={CHART_X0} y2={CHART_Y_BOTTOM} />
          <line x1={CHART_X0} y1={CHART_Y_BOTTOM} x2={CHART_X1} y2={CHART_Y_BOTTOM} />
          {/* one tick per run, so a gap can be counted. Without them a line
              that stops has no scale against which to read how long it has
              been stopped: two dates at the ends and nothing between. */}
          {runs.map((run, i) => (
            <line
              key={run.id}
              x1={xFor(i)}
              y1={CHART_Y_BOTTOM}
              x2={xFor(i)}
              y2={CHART_Y_BOTTOM + 3}
            />
          ))}
        </g>
        <g className="tickt">
          {ticks.map((t, i) => (
            <text key={i} x={CHART_X0 - 6} y={yFor(t) + 3} textAnchor="end">
              {t.toFixed(2)}
            </text>
          ))}
          {firstRun && (
            <text x={CHART_X0} y="191">
              {formatShortDate(firstRun.started_at)}
            </text>
          )}
          {lastRun && n > 1 && (
            <text x={CHART_X1} y="191" textAnchor="end">
              {formatShortDate(lastRun.started_at)}
            </text>
          )}
        </g>

        {/* The gate, the line the suite has to clear. Neutral, because a
            threshold is not an accent and not one of the four channels. Its
            colours were a literal #74837F, a green-cast hex belonging to no
            token, close enough to --ch-4 to be read as a fifth channel; the
            line and the label are both --ink-3 now, applied through .gate and
            .gate-label because a CSS variable does not resolve inside an SVG
            presentation attribute. The label carries a --bg halo, since eight
            traces cross where it sits. */}
        {gateVisible && (
          <>
            <line className="gate" x1={CHART_X0} y1={gateY} x2={CHART_X1} y2={gateY} />
            <text className="gate-label" x={CHART_X0 + 6} y={gateY - 4}>
              GATE {GATE_VALUE.toFixed(2)}
            </text>
          </>
        )}

        {/* The traces, in --ch-1..4 bone luminance resolved above, NOT the
            prototype's retired gold/blue/green/purple brand hues. One mark per
            measured stretch, so an unmeasured run leaves a gap in the line
            rather than a point on the floor. A stretch of one point, and a
            single run, draw as a circle instead: filled for exploratory, an
            open ring for golden. The key is the stretch's first run index, not
            its ordinal, so a gap closing later does not shuffle the keys. */}
        {series.map((s, i) =>
          segmentsFor[i].map((segment) =>
            segment.length > 1 ? (
              <polyline
                key={`${s.key}-${segment[0].index}`}
                fill="none"
                stroke={colors[s.colorIndex]}
                strokeWidth={widthFor(s)}
                strokeLinejoin="round"
                strokeLinecap="round"
                strokeDasharray={dashFor(s)}
                points={segment.map((p) => `${p.x},${p.y}`).join(' ')}
              />
            ) : (
              <circle
                key={`${s.key}-${segment[0].index}`}
                cx={segment[0].x}
                cy={segment[0].y}
                r={POINT_RADIUS}
                fill={s.dataset === 'golden' ? 'none' : colors[s.colorIndex]}
                stroke={s.dataset === 'golden' ? colors[s.colorIndex] : undefined}
                strokeWidth={s.dataset === 'golden' ? GOLDEN_WIDTH : undefined}
              />
            ),
          ),
        )}
      </svg>

      {/* the leaders, laid in pixel space by the layout effect above */}
      <svg className="leaders" ref={leadersRef} aria-hidden="true" />

      {/* One pin per series, and the legend for the two datasets: the pin's
          left edge is a sample of the stroke the trace draws, the name is the
          channel, the third line is the dataset the number came off, shown only
          when there is more than one of them. A dash means the latest run did
          not measure this series, never a zero.

          aria-hidden because the list below says all of it in sentences. Left
          visible to a screen reader, the grid reads as a bare run of numbers
          and names, and the "—" is announced as "em dash" or skipped, which is
          the one thing here a listener must not miss. */}
      <div className="pins" aria-hidden="true">
        {series.map((s, i) => (
          <div
            key={s.key}
            className="pin"
            data-dataset={s.dataset}
            ref={(el) => {
              pinRefs.current[i] = el
            }}
            style={{ '--c': colors[s.colorIndex] } as React.CSSProperties}
          >
            <span className="pin-val num">
              {s.latest !== null ? s.latest.toFixed(2) : '—'}
            </span>
            <span className="pin-name label">{s.channelLabel}</span>
            {showDataset && <span className="pin-set mono">{s.datasetLabel}</span>}
          </div>
        ))}
      </div>

      {/* The chart in words, one list item per series, so a listener can move
          through them one at a time. */}
      {seriesSentences.length > 0 && (
        <ul className="vh">
          {series.map((s, i) => (
            <li key={s.key}>{seriesSentences[i]}</li>
          ))}
        </ul>
      )}
    </div>
  )
}


// The chart's own CSS, spliced into the page's <style> block. It lives beside
// the component that draws the elements it names, so a class can never be
// renamed in one file and left behind in the other, and a fixture can render
// the chart with the stylesheet the console really ships.
export const TELEMETRY_CSS = `
  .telemetry { position: relative; padding: 10px 0 22px; }

  .trace { display: block; width: calc(100% - 168px); height: auto; }
  @media (max-width: 900px) { .trace { width: 100%; } }

  .trace .grid  { stroke: var(--hairline-soft); stroke-width: 1; }
  .trace .axis  { stroke: var(--hairline); stroke-width: 1; }
  .trace .tickt { font-family: var(--mono); font-size: 8px; fill: var(--ink-3); }

  /* --ink-3, the token the label beside it already carries, and not
     --hairline-strong. A hairline is 30% bone over --bg, which composites to
     rgb(79,80,80) and measures 2.36:1 there. This line is the threshold every
     series is read against, so it is a graphical object under WCAG 1.4.11 and
     owes 3:1; --ink-3 is #7E8588 and measures 5.08:1. The literal it replaced
     measured 4.81:1, so moving to a token halved the contrast of the one mark
     on the chart that carries a verdict. */
  .trace .gate { stroke: var(--ink-3); stroke-width: 1; stroke-dasharray: 10 6; }
  .trace .gate-label {
    font-family: var(--mono); font-size: 8.5px; letter-spacing: 1.6px;
    fill: var(--ink-3);
    /* the traces cross this label; the halo is the page ground, painted behind
       the glyphs rather than over them */
    paint-order: stroke; stroke: var(--bg); stroke-width: 3px; stroke-linejoin: round;
  }

  .telemetry.empty { padding: 22px 0; }
  .no-readings { margin: 0; color: var(--ink-2); font-size: 13px; }

  .leaders { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }

  .pins { position: absolute; inset: 0; pointer-events: none; }
  .pin {
    position: absolute; right: 0; width: 148px;
    transform: translateY(-50%);
    display: flex; flex-direction: column; gap: 1px;
  }
  .pin-val { font-size: 26px; line-height: 1.04; font-weight: 500; color: var(--c); }
  /* The channel is the identity the colour encodes, so it holds the brighter
     ink; the dataset qualifies it and sits below in the dimmer one. The other
     way round, which is where this started, put the qualifier above the thing
     it qualifies. nowrap because "CONTEXT PRECISION" at 0.2em tracking is
     within a few pixels of the 148px gutter, and a wrap here would break the
     pin height the collision loop assumes. */
  .pin-name { color: var(--ink-2); white-space: nowrap; }
  .pin-set { font-size: 10.5px; line-height: 1.35; color: var(--ink-3); }

  /* Legend mode. The gutter holds four leader lines; a tenant with a designated
     golden set has eight series, and under 900px there is no gutter at all.
     Both land on the same static grid, and the pin's left edge becomes the
     swatch — 2px of the same stroke the trace draws. */
  .telemetry.stacked .trace { width: 100%; }
  .telemetry.stacked .leaders { display: none; }
  .telemetry.stacked .pins {
    position: static; display: grid; gap: 14px 20px;
    /* One column per channel, one row per dataset, and the row is assigned
       explicitly rather than left to fill order. Filling four columns row by
       row happened to pair the two halves of a metric at exactly eight series
       and stopped doing so at seven, which is an ordinary outcome when one
       metric goes unmeasured. With the row pinned, golden is always the top
       band and exploratory the bottom one however many series there are. */
    grid-auto-flow: column;
    grid-template-rows: repeat(2, auto);
    grid-auto-columns: minmax(0, 1fr);
    margin-top: 18px;
  }
  .telemetry.stacked .pin[data-dataset="golden"] { grid-row: 1; }
  .telemetry.stacked .pin[data-dataset="exploratory"] { grid-row: 2; }
  .telemetry.stacked .pin {
    position: static; transform: none; width: auto;
    padding-left: 11px; border-left: 2px solid var(--c);
  }

  @media (max-width: 900px) {
    .leaders { display: none; }
    .pins {
      position: static; display: grid; gap: 14px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 18px;
    }
    /* the stacked rule above is three classes and would otherwise hold four
       columns down here, where there is no room for them. Two columns, filled
       in order, and the dataset rows are released. */
    .telemetry.stacked .pins {
      grid-auto-flow: row;
      grid-template-rows: none;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .telemetry.stacked .pin[data-dataset="golden"],
    .telemetry.stacked .pin[data-dataset="exploratory"] { grid-row: auto; }
    .pin { position: static; transform: none; width: auto; padding-left: 11px; border-left: 2px solid var(--c); }
  }

  /* The same encoding the trace uses: exploratory solid, golden dotted. Written
     to out-specify the .telemetry.stacked .pin rule, which sets the shorthand. */
  .telemetry .pin[data-dataset="golden"],
  .telemetry.stacked .pin[data-dataset="golden"] { border-left-style: dotted; }
`
