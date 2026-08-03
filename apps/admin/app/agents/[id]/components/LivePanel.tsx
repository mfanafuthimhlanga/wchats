'use client'
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import {
  METRICS_SENTINEL,
  formatPercent,
  formatCsatScore,
  formatMilliseconds,
  formatDollars,
  formatInteger,
  renderLiveMetricCell,
} from './opsFormat'

/**
 * The Live region (WIRE-01, 23-05) — GET /agents/{id}/metrics
 * (apps/api/app/api/v1/metrics.py:56-103). A client component matching the
 * house query shape (apps/admin/app/agents/[id]/deploy/page.tsx:2118-2126):
 * token from the session hook, bearer header, status check, typed cast,
 * enabled from the parent's readiness flag.
 *
 * Every cell renders through the shared pure layer's renderLiveMetricCell,
 * which already carries the tests for the one rule this region exists to
 * enforce: a sentinel is never formatted as a number, and a measured zero
 * is never shown as an absence. This file defines no sentinel literal, no
 * formatter, and no copy string of its own.
 *
 * The eighth cell (deflection) carries a locked caption because the
 * service documents it as the same signal as containment until an
 * independent measure exists — showing the two side by side without that
 * caption would read as two confirmations where there is one measurement.
 */

// `pluralize(1, 'day')` -> "1 day", `pluralize(7, 'day')` -> "7 days".
// window_days is a general int on the response (not hardcoded to 7), so a
// bare template literal here would read "last 1 days" once a per-agent
// window selector ships — 23-09 adversarial review flagged the same class
// of bug already fixed in page.tsx for "documents"/"scenarios".
function pluralize(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

interface AgentMetricsResponse {
  containment: number | typeof METRICS_SENTINEL
  deflection: number | typeof METRICS_SENTINEL
  escalation_rate: number | typeof METRICS_SENTINEL
  csat_avg: number | typeof METRICS_SENTINEL
  thumbs_down_rate: number | typeof METRICS_SENTINEL
  p95_latency_ms: number | typeof METRICS_SENTINEL
  cost_per_session: number | typeof METRICS_SENTINEL
  sample_size: number
  window_days: number
}

export default function LivePanel({
  agentId,
  enabled,
  onError,
}: {
  agentId: string
  enabled: boolean
  onError: (region: string, message: string | null) => void
}) {
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  const metricsQuery = useQuery({
    queryKey: ['metrics', agentId],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${agentId}/metrics`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return (await r.json()) as AgentMetricsResponse
    },
    enabled,
    staleTime: 15_000,
  })

  // The one error path this region reports into — the page's shared
  // callback, folded into its single existing banner. On error the grid
  // below still renders (its placeholder shell), never a second surface.
  useEffect(() => {
    if (metricsQuery.isError) {
      onError('live', (metricsQuery.error as Error).message || 'Failed to load live metrics.')
    } else {
      onError('live', null)
    }
  }, [metricsQuery.isError, metricsQuery.error, onError])

  const data = metricsQuery.data
  const windowDays = data?.window_days ?? 7

  // 23-09 adversarial review: 23-UI-SPEC.md §4.1 locks the pre-data shell
  // as "`--` mono placeholders (matching `.metrics .pending` treatment
  // already established at globals.css:387)" — two ASCII hyphens, the
  // established loading-shell glyph elsewhere in this codebase, not a
  // typographic em-dash. This single constant is shared by every cell
  // below so the nine call sites cannot drift onto two different
  // characters again.
  const PENDING = '--'

  return (
    <>
      <p className="mono head-count">{data ? `last ${pluralize(windowDays, 'day')}` : PENDING}</p>
      <div className="chans">
        <div className="chan">
          <span className="chan-name">sessions</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.sample_size, windowDays, formatInteger) : PENDING}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">containment</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.containment, windowDays, formatPercent) : PENDING}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">deflection</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.deflection, windowDays, formatPercent) : PENDING}
            </span>
          </div>
          <p className="chan-thr">Same signal as containment until an independent measure ships.</p>
        </div>
        <div className="chan">
          <span className="chan-name">escalation to human</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.escalation_rate, windowDays, formatPercent) : PENDING}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">CSAT</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.csat_avg, windowDays, formatCsatScore) : PENDING}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">thumbs down</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.thumbs_down_rate, windowDays, formatPercent) : PENDING}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">p95 latency</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.p95_latency_ms, windowDays, formatMilliseconds) : PENDING}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">cost / session</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.cost_per_session, windowDays, formatDollars) : PENDING}
            </span>
          </div>
        </div>
      </div>
    </>
  )
}
