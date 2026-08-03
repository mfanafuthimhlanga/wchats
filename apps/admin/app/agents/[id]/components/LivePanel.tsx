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

  return (
    <>
      <p className="mono head-count">{data ? `last ${windowDays} days` : '—'}</p>
      <div className="chans">
        <div className="chan">
          <span className="chan-name">sessions</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.sample_size, windowDays, formatInteger) : '—'}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">containment</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.containment, windowDays, formatPercent) : '—'}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">deflection</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.deflection, windowDays, formatPercent) : '—'}
            </span>
          </div>
          <p className="chan-thr">Same signal as containment until an independent measure ships.</p>
        </div>
        <div className="chan">
          <span className="chan-name">escalation to human</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.escalation_rate, windowDays, formatPercent) : '—'}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">CSAT</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.csat_avg, windowDays, formatCsatScore) : '—'}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">thumbs down</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.thumbs_down_rate, windowDays, formatPercent) : '—'}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">p95 latency</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.p95_latency_ms, windowDays, formatMilliseconds) : '—'}
            </span>
          </div>
        </div>
        <div className="chan">
          <span className="chan-name">cost / session</span>
          <div className="chan-read">
            <span className="num chan-val">
              {data ? renderLiveMetricCell(data.cost_per_session, windowDays, formatDollars) : '—'}
            </span>
          </div>
        </div>
      </div>
    </>
  )
}
