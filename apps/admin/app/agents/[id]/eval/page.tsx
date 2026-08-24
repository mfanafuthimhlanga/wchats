'use client'
import { use, useState, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Chip from '../../../components/gotham/Chip'
import Ledger, { LedgerColHead, LedgerRowHead } from '../../../components/gotham/Ledger'
import EmptyState from '../../../components/gotham/EmptyState'

/**
 * Eval — `/agents/[id]/eval` (UI-SPEC S6.7, UI2-05, ported from
 * prototypes/gotham/eval.html). Telemetry chart (`.telemetry`, VITALS
 * leader-line pattern) + the judge (CHORUS word-by-word typeset) + the
 * scenario `.ledger`.
 *
 * PRESERVED VERBATIM (non-regression, UI-SPEC S9): the real
 * `GET /api/v1/agents/{id}/eval-runs`, `GET .../eval-runs/{id}/results` and
 * `POST .../eval-runs/trigger` fetches the previous dusk build already
 * consumed, plus the poll-while-running effect.
 *
 * Colour fix required (must-fix 1 / UI-SPEC S6.7, S10 anti-pattern 1 — "the
 * clearest violation of the colour-is-a-verdict law in the entire prototype
 * set"): eval.html hardcodes the four ragas channel traces + dot/pin
 * swatches to four literal brand hues (retired gold, blue, green, purple
 * hex literals). This port resolves the CURRENT
 * `--ch-1..4` bone-luminance values via `getComputedStyle` at draw time
 * instead (same technique as the ingest swarm colour fix) — see
 * `useChannelColors` below. Nothing on this page reaches for a hue; the
 * channels are read by weight, not colour.
 *
 * The judge's verdict sentence is generated from real run/scenario data (no
 * judge-summary endpoint exists on the backend — see apps/api/app/api/v1/
 * evals.py) rather than the prototype's hardcoded placeholder paragraph.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EvalRun {
  id: string
  started_at: string
  finished_at: string | null
  status: 'running' | 'complete' | 'failed'
  scenario_count: number
  aggregate_scores: {
    faithfulness: number
    answer_relevancy: number
    context_precision: number
    context_recall: number
  }
}

interface EvalRunsResponse {
  eval_runs: EvalRun[]
}

interface ScenarioResult {
  scenario_id: string
  question: string
  source: 'generated' | 'mined'
  scores: {
    faithfulness: number
    answer_relevancy: number
    context_precision: number
    context_recall: number
  }
  passed: boolean
}

interface EvalResultsResponse {
  results: ScenarioResult[]
}

type ChannelKey = keyof EvalRun['aggregate_scores']

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Compact mono timestamp — "2026-07-13 09:14" (UTC), matching eval.html's
// `.stamp` readouts.
function formatStamp(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}

// Short axis-tick date — "2026-07-06" (UTC).
function formatShortDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10)
}

// The judge's verdict sentence — built from real aggregate + scenario data,
// never hardcoded (UI-SPEC S6.7: "generated from real run data, or omitted
// if there's no LLM-judge summary endpoint" — no such endpoint exists).
function buildVerdict(latestRun: EvalRun | null, scenarios: ScenarioResult[]): string {
  if (!latestRun || scenarios.length === 0) return ''
  const total = scenarios.length
  const failed = scenarios.filter((s) => !s.passed)
  const passedCount = total - failed.length
  if (failed.length === 0) {
    return `All ${total} scenario${total === 1 ? '' : 's'} held on this run. The gate stays open.`
  }
  return (
    `${total} scenario${total === 1 ? '' : 's'} ran on this run. ${passedCount} held, ` +
    `${failed.length} failed. Review the ledger below before the gate can close.`
  )
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduced(mq.matches)
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])
  return reduced
}

// Colour fix (must-fix 1): resolve the CURRENT --ch-1..4 bone-luminance
// values at draw time. CSS custom properties do not resolve inside raw SVG
// presentation attributes, so a JS read is required either way; the fix is
// which values get read (the channel tokens), not the technique.
const CH_FALLBACK = ['#E7E5E1', '#A9AFB1', '#7C8386', '#565C5F']
const CH_VARS = ['--ch-1', '--ch-2', '--ch-3', '--ch-4']

function useChannelColors(): string[] {
  const [colors, setColors] = useState<string[]>(CH_FALLBACK)
  useEffect(() => {
    const style = getComputedStyle(document.documentElement)
    setColors(
      CH_VARS.map((v, i) => style.getPropertyValue(v).trim() || CH_FALLBACK[i]),
    )
  }, [])
  return colors
}

// ---------------------------------------------------------------------------
// Telemetry chart — VITALS leader-line pattern, ported from eval.html's
// layout() script. Numerals are pinned to the head of their own trace via a
// pixel-space leader line; collision-avoidance (MIN_GAP) keeps them apart.
// Collapses to a static 2-col .pin grid under 900px (leaders hidden).
// ---------------------------------------------------------------------------

const CHANNELS: { key: ChannelKey; label: string }[] = [
  { key: 'faithfulness', label: 'Faithfulness' },
  { key: 'answer_relevancy', label: 'Answer relevancy' },
  { key: 'context_recall', label: 'Context recall' },
  { key: 'context_precision', label: 'Context precision' },
]

const CHART_VB_W = 640
const CHART_VB_H = 200
const CHART_X0 = 40
const CHART_X1 = 600
const CHART_Y_TOP = 24
const CHART_Y_BOTTOM = 176
const GATE_VALUE = 0.9
const PIN_MIN_GAP = 44
const PIN_GUTTER = 148

function TelemetryChart({ runs, colors }: { runs: EvalRun[]; colors: string[] }) {
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const traceRef = useRef<SVGSVGElement | null>(null)
  const leadersRef = useRef<SVGSVGElement | null>(null)
  const pinRefs = useRef<(HTMLDivElement | null)[]>([])

  const n = runs.length

  const allValues = runs.flatMap((r) => CHANNELS.map((c) => r.aggregate_scores[c.key]))
  const dataMin = allValues.length ? Math.min(...allValues) : 0.82
  const dataMax = allValues.length ? Math.max(...allValues) : 0.98
  const yMin = Math.max(0, Math.min(dataMin - 0.04, 0.86))
  const yMax = Math.min(1, Math.max(dataMax + 0.03, 0.92))
  const yRange = yMax - yMin || 1

  const yFor = (v: number) => CHART_Y_BOTTOM - ((v - yMin) / yRange) * (CHART_Y_BOTTOM - CHART_Y_TOP)
  const xFor = (i: number) =>
    n <= 1 ? (CHART_X0 + CHART_X1) / 2 : CHART_X0 + (i / (n - 1)) * (CHART_X1 - CHART_X0)

  const channelPoints = CHANNELS.map((c) =>
    runs.map((r, i) => ({ x: xFor(i), y: yFor(r.aggregate_scores[c.key]) })),
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
      if (narrow.matches) {
        while (leaders!.firstChild) leaders!.removeChild(leaders!.firstChild)
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

      const rows = CHANNELS.map((c, i) => {
        const points = channelPoints[i]
        const lastPoint = points[points.length - 1]
        const y = (lastPoint?.y ?? CHART_Y_BOTTOM) * scale + top
        return { index: i, lineY: y, slotY: y, colour: colors[i], lineX: CHART_X1 * scale + left }
      })

      rows.sort((a, b) => a.slotY - b.slotY)
      for (let i = 1; i < rows.length; i++) {
        if (rows[i].slotY - rows[i - 1].slotY < PIN_MIN_GAP) {
          rows[i].slotY = rows[i - 1].slotY + PIN_MIN_GAP
        }
      }

      const gutterX = wrap!.clientWidth - PIN_GUTTER
      while (leaders!.firstChild) leaders!.removeChild(leaders!.firstChild)

      rows.forEach((r) => {
        const pinEl = pinRefs.current[r.index]
        if (pinEl) pinEl.style.top = `${r.slotY}px`

        const dot = document.createElementNS(NS, 'circle')
        dot.setAttribute('cx', r.lineX.toFixed(1))
        dot.setAttribute('cy', r.lineY.toFixed(1))
        dot.setAttribute('r', '2.6')
        dot.setAttribute('fill', r.colour)
        leaders!.appendChild(dot)

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
        pl.setAttribute('stroke-width', '1')
        pl.setAttribute('stroke-opacity', '0.7')
        leaders!.appendChild(pl)
      })
    }

    const ro = 'ResizeObserver' in window ? new ResizeObserver(layout) : null
    if (ro) {
      ro.observe(wrap)
    } else {
      window.addEventListener('resize', layout)
    }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(layout)
    layout()

    return () => {
      if (ro) ro.disconnect()
      else window.removeEventListener('resize', layout)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs, colors, n])

  const chartLabel =
    n > 0
      ? `${n} run${n === 1 ? '' : 's'} on four channels. ` +
        CHANNELS.map((c, i) => {
          const last = runs[n - 1].aggregate_scores[c.key]
          return n > 1
            ? `${c.label} moves from ${runs[0].aggregate_scores[c.key].toFixed(2)} to ${last.toFixed(2)}`
            : `${c.label} at ${last.toFixed(2)}`
        }).join(', ') +
        `. The gate is set at ${GATE_VALUE.toFixed(2)}.`
      : 'No eval runs yet.'

  return (
    <div className="telemetry" id="telemetry" ref={wrapRef}>
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

        {/* the gate: the line the suite has to clear. Neutral, because a
            threshold is not an accent — not one of the four channels. */}
        {gateVisible && (
          <>
            <line
              x1={CHART_X0}
              y1={gateY}
              x2={CHART_X1}
              y2={gateY}
              stroke="#74837F"
              strokeOpacity={0.6}
              strokeWidth={1}
              strokeDasharray="5 5"
            />
            <text
              x={CHART_X0 + 6}
              y={gateY - 4}
              fontFamily="'JetBrains Mono', monospace"
              fontSize={8.5}
              letterSpacing={1.6}
              fill="#74837F"
            >
              GATE {GATE_VALUE.toFixed(2)}
            </text>
          </>
        )}

        {/* the four traces — --ch-1..4 bone luminance, resolved above.
            NOT the prototype's retired gold/blue/green/purple brand hues. */}
        {n > 1 &&
          CHANNELS.map((c, i) => (
            <polyline
              key={c.key}
              fill="none"
              stroke={colors[i]}
              strokeWidth={1.7}
              strokeLinejoin="round"
              strokeLinecap="round"
              points={channelPoints[i].map((p) => `${p.x},${p.y}`).join(' ')}
            />
          ))}
        {n === 1 &&
          CHANNELS.map((c, i) => (
            <circle key={c.key} cx={channelPoints[i][0].x} cy={channelPoints[i][0].y} r={2.6} fill={colors[i]} />
          ))}
      </svg>

      {/* the leaders, laid in pixel space by the layout effect above */}
      <svg className="leaders" ref={leadersRef} aria-hidden="true" />

      <div className="pins">
        {CHANNELS.map((c, i) => {
          const last = runs[n - 1]?.aggregate_scores[c.key]
          return (
            <div
              key={c.key}
              className="pin"
              ref={(el) => {
                pinRefs.current[i] = el
              }}
              style={{ '--c': colors[i] } as React.CSSProperties}
            >
              <span className="pin-val num">{last !== undefined ? last.toFixed(2) : '—'}</span>
              <span className="pin-name label">{c.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// EvalPage
// ---------------------------------------------------------------------------

export default function EvalPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const [isRunning, setIsRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const queryClient = useQueryClient()
  const reducedMotion = useReducedMotion()
  const channelColors = useChannelColors()

  // Fetch eval runs list — PRESERVED VERBATIM (non-regression, UI-SPEC S9).
  const evalRunsQuery = useQuery<EvalRunsResponse>({
    queryKey: ['eval-runs', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/eval-runs`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 30_000,
  })

  const latestRunId = evalRunsQuery.data?.eval_runs?.[0]?.id ?? null

  // Fetch results for the latest run — PRESERVED VERBATIM.
  const resultsQuery = useQuery<EvalResultsResponse>({
    queryKey: ['eval-results', id, latestRunId],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(
        `${apiBase}/api/v1/agents/${id}/eval-runs/${latestRunId}/results`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
    enabled: isLoaded && !!isSignedIn && !!latestRunId,
    staleTime: 30_000,
  })

  // Chronological (oldest-first) run list for the chart.
  const chronologicalRuns: EvalRun[] = [...(evalRunsQuery.data?.eval_runs ?? [])].reverse()

  const latestRun = evalRunsQuery.data?.eval_runs?.[0] ?? null
  const hasRuns = (evalRunsQuery.data?.eval_runs?.length ?? 0) > 0
  const scenarios = resultsQuery.data?.results ?? []

  // Poll while a run is in progress — PRESERVED VERBATIM.
  useEffect(() => {
    if (isRunning) {
      pollIntervalRef.current = setInterval(async () => {
        await queryClient.invalidateQueries({ queryKey: ['eval-runs', id] })
        const fresh = queryClient.getQueryData<EvalRunsResponse>(['eval-runs', id])
        const runs = fresh?.eval_runs ?? []
        if (runs.length > 0 && runs[0].status === 'complete') {
          setIsRunning(false)
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
        }
      }, 5000)
    }
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRunning, id])

  // Run Now — PRESERVED VERBATIM (POST /eval-runs/trigger).
  const handleRunNow = async () => {
    setIsRunning(true)
    setRunError(null)
    try {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(
        `${apiBase}/api/v1/agents/${id}/eval-runs/trigger`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        },
      )
      if (!res.ok) {
        const body = await res.text().catch(() => '')
        throw new Error(`HTTP ${res.status}: ${body}`)
      }
      queryClient.invalidateQueries({ queryKey: ['eval-runs', id] })
    } catch (err) {
      setIsRunning(false)
      setRunError(err instanceof Error ? err.message : 'Eval run failed')
    }
  }

  const isLoading = evalRunsQuery.isPending || !isLoaded

  // ── the judge: word-by-word typeset, generated from real data ───────────
  const verdictText = buildVerdict(latestRun, scenarios)
  const verdictWords = verdictText ? verdictText.split(' ') : []
  const [revealed, setRevealed] = useState(0)

  useEffect(() => {
    if (!verdictText) {
      setRevealed(0)
      return
    }
    if (reducedMotion) {
      // reduced-motion: set the full sentence instantly, no caret.
      setRevealed(verdictWords.length)
      return
    }
    setRevealed(0)
    let i = 0
    const timer = setInterval(() => {
      i += 1
      setRevealed(i)
      if (i >= verdictWords.length) clearInterval(timer)
    }, 30)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [verdictText, reducedMotion])

  const verdictDone = revealed >= verdictWords.length
  const verdictDisplayed = verdictWords.slice(0, revealed).join(' ')

  const passedCount = scenarios.filter((s) => s.passed).length
  const failedCount = scenarios.length - passedCount
  const passRate = scenarios.length > 0 ? passedCount / scenarios.length : null

  const alertStyle: React.CSSProperties = {
    padding: '12px 16px',
    marginBottom: '20px',
    background: 'var(--fail-dim)',
    border: '1px solid color-mix(in oklch, var(--fail) 32%, transparent)',
    borderRadius: 'var(--r-panel)',
    fontSize: '14px',
    color: 'var(--fail)',
  }

  return (
    <div className="page">
      <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />

      <header className="page-head">
        <div className="row">
          <div>
            <h1>Evaluations</h1>
            <p className="sub">
              Ragas-scored tests for your agent&apos;s knowledge base. The suite runs against a
              branch of the agent&apos;s database, so a run costs nothing.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleRunNow}
            disabled={isRunning}
          >
            {isRunning ? 'Running…' : 'Run evals'}
          </button>
        </div>
      </header>

      {runError && (
        <div role="alert" style={alertStyle}>
          Eval run failed — {runError}
        </div>
      )}

      {isLoading && (
        <div aria-hidden="true">
          <div
            style={{
              height: '220px',
              borderRadius: 'var(--r-panel)',
              background: 'var(--surface)',
              marginBottom: '24px',
            }}
          />
          {[...Array(3)].map((_, i) => (
            <div
              key={i}
              style={{
                height: '44px',
                borderRadius: 'var(--r-panel)',
                background: 'var(--surface)',
                marginBottom: '8px',
              }}
            />
          ))}
        </div>
      )}

      {!isLoading && !hasRuns && (
        <>
          <EmptyState
            heading="No eval runs yet"
            body="You run an eval when you want one. Start a check to see how your agent performs against its scenarios."
          />
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginTop: '16px' }}
            onClick={handleRunNow}
            disabled={isRunning}
          >
            {isRunning ? 'Running…' : 'Run first eval'}
          </button>
        </>
      )}

      {!isLoading && hasRuns && (
        <>
          {/* ── the four channels ────────────────────────────────────────── */}
          <h2 className="vh">
            Channel telemetry, last {chronologicalRuns.length} run{chronologicalRuns.length === 1 ? '' : 's'}
          </h2>
          <TelemetryChart runs={chronologicalRuns} colors={channelColors} />

          {/* ── the judge ────────────────────────────────────────────────── */}
          <section className="judge">
            <span className="label" id="judge-label">
              The judge
            </span>
            <p className="voice verdict" aria-hidden="true">
              {verdictDisplayed}
              {!reducedMotion && !verdictDone && verdictText ? (
                <i className="caret" aria-hidden="true" />
              ) : null}
            </p>
            {/* the screen reader gets the whole sentence once, not one word at a time */}
            <p className="vh" role="status" aria-live="polite">
              {verdictText}
            </p>
            {latestRun && (
              <p className="run-note">
                {passRate !== null && (
                  <span className="mono stamp">
                    pass rate {passRate.toFixed(2)} · {passedCount} of {scenarios.length} held
                  </span>
                )}
                <span className="mono stamp">last run {formatStamp(latestRun.started_at)}</span>
              </p>
            )}
          </section>

          {/* ── the scenarios ────────────────────────────────────────────── */}
          <section className="section">
            <div className="section-head">
              <h2 className="label" id="sc-label">
                Scenarios
                <span className="chip chip-mute num" style={{ marginLeft: '10px' }}>
                  {scenarios.length}
                </span>
              </h2>
              {failedCount > 0 && (
                <span className="mono stamp">
                  {failedCount} failed
                </span>
              )}
            </div>

            {resultsQuery.isLoading ? (
              <div aria-hidden="true">
                {[...Array(4)].map((_, i) => (
                  <div
                    key={i}
                    style={{
                      height: '40px',
                      borderRadius: 'var(--r-control)',
                      background: 'var(--surface)',
                      marginBottom: '6px',
                    }}
                  />
                ))}
              </div>
            ) : scenarios.length === 0 ? (
              <EmptyState heading="No scenario results" body="This run has no scenario-level results yet." />
            ) : (
              <Ledger caption="Scenario results: question, source, faithfulness, relevancy, verdict, and run time">
                <thead>
                  <tr>
                    <LedgerColHead>Scenario</LedgerColHead>
                    <LedgerColHead className="col-src">Source</LedgerColHead>
                    <LedgerColHead numeric>
                      <i className="dot" style={{ background: channelColors[0] }} aria-hidden="true" />
                      Faithfulness
                    </LedgerColHead>
                    <LedgerColHead numeric>
                      <i className="dot" style={{ background: channelColors[1] }} aria-hidden="true" />
                      Relevancy
                    </LedgerColHead>
                    <LedgerColHead>Verdict</LedgerColHead>
                    <LedgerColHead className="col-ran">Ran</LedgerColHead>
                  </tr>
                </thead>
                <tbody>
                  {scenarios.map((s) => (
                    <tr key={s.scenario_id} data-verdict={s.passed ? 'pass' : 'fail'}>
                      <LedgerRowHead className="scenario">{s.question}</LedgerRowHead>
                      <td className="col-src">
                        <span className="chip chip-mute">{s.source}</span>
                      </td>
                      <td className="num">{s.scores.faithfulness.toFixed(2)}</td>
                      <td className="num">{s.scores.answer_relevancy.toFixed(2)}</td>
                      <td>
                        <Chip verdict={s.passed ? 'pass' : 'fail'}>{s.passed ? 'Pass' : 'Fail'}</Chip>
                      </td>
                      <td className="col-ran mono stamp">
                        {latestRun ? formatStamp(latestRun.started_at) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Ledger>
            )}
          </section>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page-scoped CSS — ported from prototypes/gotham/eval.html's own <style>
// block (telemetry / leaders / pins / judge / ledger num emphasis), same
// static dangerouslySetInnerHTML pattern used by ingest/page.tsx. `.vh` and
// the registration crosses are handled globally (PageChrome / globals.css)
// and are not repeated here.
// ---------------------------------------------------------------------------
const PAGE_CSS = `
  .telemetry { position: relative; padding: 10px 0 22px; }

  .trace { display: block; width: calc(100% - 168px); height: auto; }
  @media (max-width: 900px) { .trace { width: 100%; } }

  .trace .grid  { stroke: var(--hairline-soft); stroke-width: 1; }
  .trace .axis  { stroke: var(--hairline); stroke-width: 1; }
  .trace .tickt { font-family: var(--mono); font-size: 8px; fill: var(--ink-3); }

  .leaders { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }

  .pins { position: absolute; inset: 0; pointer-events: none; }
  .pin {
    position: absolute; right: 0; width: 148px;
    transform: translateY(-50%);
    display: flex; flex-direction: column; gap: 1px;
  }
  .pin-val { font-size: 26px; line-height: 1.04; font-weight: 500; color: var(--c); }
  .pin-name { color: var(--ink-3); }

  @media (max-width: 900px) {
    .leaders { display: none; }
    .pins {
      position: static; display: grid; gap: 14px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 18px;
    }
    .pin { position: static; transform: none; width: auto; padding-left: 11px; border-left: 2px solid var(--c); }
  }

  .judge { margin-top: 8px; padding-top: 22px; border-top: 1px solid var(--hairline-strong); }
  .judge .label { display: block; margin-bottom: 10px; }
  .verdict { font-size: 17.5px; max-width: 74ch; min-height: 3.3em; }

  .scenario { max-width: 42ch; }
  .ledger th .dot { display: inline-block; margin-right: 6px; vertical-align: 1px; }
  .ledger td.num { color: var(--ink-2); }
  .ledger tr[data-verdict="fail"] td.num { color: var(--ink); }
  .stamp { color: var(--ink-3); font-size: 12px; }

  .run-note { display: flex; align-items: center; gap: 10px; margin-top: 14px; flex-wrap: wrap; }

  @media (max-width: 820px) {
    .ledger .col-src, .ledger .col-ran { display: none; }
  }
`
