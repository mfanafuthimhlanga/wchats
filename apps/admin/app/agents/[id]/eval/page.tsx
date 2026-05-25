'use client'
import { use, useState, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Link from 'next/link'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
} from 'recharts'

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

interface ChartPoint {
  date: string
  faithfulness: number
  answer_relevancy: number
  context_precision: number
  context_recall: number
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(startedAt: string, finishedAt: string | null): string {
  if (!finishedAt) return '—'
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime()
  const seconds = Math.floor(ms / 1000)
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds % 60
  if (minutes === 0) return `${remainingSeconds}s`
  return `${minutes}m ${remainingSeconds}s`
}

function formatUtcDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }) + ' ' + d.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
    hour12: false,
  }) + ' UTC'
}

function getNextRunTime(): string {
  const now = new Date()
  const next = new Date(now)
  next.setUTCHours(2, 0, 0, 0)
  if (next <= now) {
    next.setUTCDate(next.getUTCDate() + 1)
  }
  const diffMs = next.getTime() - now.getTime()
  const hours = Math.floor(diffMs / (1000 * 60 * 60))
  const minutes = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60))
  return `Next run in ${hours}h ${minutes}m`
}

function scoreColor(score: number): string {
  if (score >= 0.9) return 'var(--green)'
  if (score >= 0.7) return 'var(--amber)'
  return 'var(--red)'
}

// ---------------------------------------------------------------------------
// Loading Skeleton
// ---------------------------------------------------------------------------

function SkeletonRow() {
  return (
    <div
      style={{
        height: '44px',
        borderRadius: '6px',
        marginBottom: '8px',
        background:
          'linear-gradient(90deg, var(--surface-2) 25%, var(--surface-3) 50%, var(--surface-2) 75%)',
        backgroundSize: '200% 100%',
        animation: 'shimmer 1.5s infinite',
      }}
    />
  )
}

function ChartSkeleton() {
  return (
    <div
      style={{
        height: '320px',
        borderRadius: 'var(--radius-sm)',
        background:
          'linear-gradient(90deg, var(--surface-2) 25%, var(--surface-3) 50%, var(--surface-2) 75%)',
        backgroundSize: '200% 100%',
        animation: 'shimmer 1.5s infinite',
      }}
    />
  )
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function EvalPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const [activeTab, setActiveTab] = useState<'passrates' | 'scenarios'>(
    'passrates',
  )
  const [isRunning, setIsRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const queryClient = useQueryClient()

  // Fetch eval runs list
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

  // Fetch results for the latest run
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

  // Transform eval runs into chart data (oldest first)
  const chartData: ChartPoint[] = (
    [...(evalRunsQuery.data?.eval_runs ?? [])].reverse()
  ).map((run) => ({
    date: new Date(run.started_at).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    }),
    faithfulness: run.aggregate_scores?.faithfulness ?? 0,
    answer_relevancy: run.aggregate_scores?.answer_relevancy ?? 0,
    context_precision: run.aggregate_scores?.context_precision ?? 0,
    context_recall: run.aggregate_scores?.context_recall ?? 0,
  }))

  const latestRun = evalRunsQuery.data?.eval_runs?.[0] ?? null
  const hasRuns =
    (evalRunsQuery.data?.eval_runs?.length ?? 0) > 0
  const scenarios = resultsQuery.data?.results ?? []

  // Poll while a run is in progress
  useEffect(() => {
    if (isRunning) {
      pollIntervalRef.current = setInterval(async () => {
        await queryClient.invalidateQueries({ queryKey: ['eval-runs', id] })
        // Read fresh data from the query cache after invalidation — avoids stale closure
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

  // Handle Run Now button
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
      // Polling starts via the useEffect above
      queryClient.invalidateQueries({ queryKey: ['eval-runs', id] })
    } catch (err) {
      setIsRunning(false)
      setRunError(err instanceof Error ? err.message : 'Eval run failed')
    }
  }

  const isLoading = evalRunsQuery.isPending || !isLoaded

  // ─── Styles ───────────────────────────────────────────────────────────────

  const wrapStyle: React.CSSProperties = {
    padding: '32px 40px',
    maxWidth: '960px',
    fontFamily: 'var(--font-sans)',
  }

  const backLinkStyle: React.CSSProperties = {
    fontSize: '14px',
    color: 'var(--accent)',
    textDecoration: 'none',
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    marginBottom: '24px',
  }

  const pageHeaderStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    marginBottom: '24px',
  }

  const btnPrimaryStyle: React.CSSProperties = {
    background: isRunning ? undefined : 'var(--accent)',
    color: '#fff',
    border: 'none',
    borderRadius: 'var(--radius-xs)',
    padding: '9px 18px',
    fontSize: '14px',
    fontWeight: 600,
    fontFamily: 'var(--font-sans)',
    cursor: isRunning ? 'not-allowed' : 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    whiteSpace: 'nowrap',
    opacity: isRunning ? 0.5 : 1,
    backgroundColor: isRunning ? 'var(--accent)' : undefined,
  }

  const scheduleStripStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    background: 'var(--surface-3)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-xs)',
    padding: '10px 16px',
    marginBottom: '24px',
    fontSize: '13px',
    color: 'var(--text-2)',
  }

  const tabBarStyle: React.CSSProperties = {
    display: 'flex',
    borderBottom: '1px solid var(--border)',
    marginBottom: '24px',
  }

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: '10px 20px',
    fontSize: '14px',
    fontWeight: active ? 600 : 500,
    color: active ? 'var(--accent)' : 'var(--text-3)',
    cursor: 'pointer',
    borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
    borderTop: 'none',
    borderLeft: 'none',
    borderRight: 'none',
    background: 'transparent',
    fontFamily: 'var(--font-sans)',
    transition: 'color 0.15s, border-color 0.15s',
    marginBottom: '-1px',
  })

  const chartCardStyle: React.CSSProperties = {
    background: 'var(--surface-1)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    padding: '24px',
    marginBottom: '16px',
    boxShadow: 'var(--shadow-card)',
  }

  const emptyStateStyle: React.CSSProperties = {
    border: '2px dashed var(--border)',
    borderRadius: 'var(--radius-sm)',
    padding: '64px 40px',
    textAlign: 'center',
    background: 'var(--surface-2)',
  }

  const tableCardStyle: React.CSSProperties = {
    background: 'var(--surface-1)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    overflow: 'hidden',
    boxShadow: 'var(--shadow-card)',
  }

  // ─── Dot helper for metric column headers ─────────────────────────────────

  const MetricDot = ({ color }: { color: string }) => (
    <span
      style={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: color,
        marginRight: 4,
        flexShrink: 0,
      }}
    />
  )

  // ─── Score cell ───────────────────────────────────────────────────────────

  const ScoreCell = ({ score }: { score: number }) => (
    <span
      style={{
        fontFamily: 'var(--font-mono)',
        fontSize: '13px',
        color: scoreColor(score),
      }}
    >
      {score.toFixed(3)}
    </span>
  )

  // ─── Render ───────────────────────────────────────────────────────────────

  return (
    <div style={wrapStyle}>
      {/* Back link */}
      <Link href={`/agents/${id}`} style={backLinkStyle}>
        ← Back to Configure
      </Link>

      {/* Page header */}
      <div style={pageHeaderStyle}>
        <div>
          <h1
            style={{
              fontSize: '22px',
              fontWeight: 700,
              color: 'var(--text-1)',
              marginBottom: '4px',
            }}
          >
            Run evaluations
          </h1>
          <p
            style={{
              fontSize: '14px',
              color: 'var(--text-3)',
              margin: 0,
            }}
          >
            Ragas-scored nightly tests for your agent&apos;s knowledge base.
          </p>
        </div>
        <button
          style={btnPrimaryStyle}
          onClick={handleRunNow}
          disabled={isRunning}
        >
          {isRunning ? 'Running…' : '▶ Run Now'}
        </button>
      </div>

      {/* Error toast */}
      {runError && (
        <div
          role="alert"
          style={{
            padding: '12px 16px',
            marginBottom: '20px',
            background: 'var(--red-bg)',
            border: '1px solid rgba(185,28,28,0.3)',
            borderRadius: 'var(--radius-xs)',
            fontSize: '14px',
            color: 'var(--red)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span>Eval run failed — {runError}</span>
          <button
            onClick={() => setRunError(null)}
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--red)',
              fontSize: '16px',
              padding: '0 4px',
            }}
          >
            ×
          </button>
        </div>
      )}

      {/* Schedule strip */}
      <div style={scheduleStripStyle}>
        <span style={{ fontSize: '15px', flexShrink: 0 }}>🕑</span>
        <span style={{ fontWeight: 600, color: 'var(--text-1)', marginRight: '4px' }}>
          Automated:
        </span>
        <span>daily at 02:00 UTC</span>
        <span style={{ color: 'var(--border-hard)', margin: '0 8px' }}>·</span>
        {latestRun ? (
          <span>
            Last run:{' '}
            <strong>{formatUtcDate(latestRun.started_at)}</strong>
          </span>
        ) : (
          <span style={{ color: 'var(--text-3)' }}>No runs yet</span>
        )}
        <span style={{ color: 'var(--text-3)', fontSize: '12px', marginLeft: 'auto' }}>
          {latestRun ? getNextRunTime() : 'First run tonight at 02:00 UTC'}
        </span>
      </div>

      {/* Tab navigation */}
      <div style={tabBarStyle} role="tablist" aria-label="Eval dashboard tabs">
        <button
          style={tabStyle(activeTab === 'passrates')}
          onClick={() => setActiveTab('passrates')}
          role="tab"
          aria-selected={activeTab === 'passrates'}
        >
          Pass Rates
        </button>
        <button
          style={tabStyle(activeTab === 'scenarios')}
          onClick={() => setActiveTab('scenarios')}
          role="tab"
          aria-selected={activeTab === 'scenarios'}
        >
          Scenarios
        </button>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div>
          <ChartSkeleton />
          <div style={{ marginTop: '24px' }}>
            {[...Array(5)].map((_, i) => (
              <SkeletonRow key={i} />
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !hasRuns && (
        <div style={emptyStateStyle}>
          <span style={{ fontSize: '40px', marginBottom: '16px', display: 'block' }}>
            📊
          </span>
          <div
            style={{
              fontSize: '16px',
              fontWeight: 600,
              color: 'var(--text-1)',
              marginBottom: '8px',
            }}
          >
            No eval runs yet
          </div>
          <p
            style={{
              fontSize: '14px',
              color: 'var(--text-3)',
              maxWidth: '340px',
              margin: '0 auto 24px',
              lineHeight: 1.6,
            }}
          >
            Your agent is evaluated automatically each night. Run a check now
            to see how it performs.
          </p>
          <button
            style={{
              background: 'var(--accent)',
              color: '#fff',
              border: 'none',
              borderRadius: 'var(--radius-xs)',
              padding: '9px 18px',
              fontSize: '14px',
              fontWeight: 600,
              fontFamily: 'var(--font-sans)',
              cursor: isRunning ? 'not-allowed' : 'pointer',
              opacity: isRunning ? 0.5 : 1,
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
            }}
            onClick={handleRunNow}
            disabled={isRunning}
          >
            {isRunning ? 'Running…' : '▶ Run First Eval'}
          </button>
        </div>
      )}

      {/* Pass Rates tab */}
      {!isLoading && hasRuns && activeTab === 'passrates' && (
        <div>
          <div style={chartCardStyle}>
            <div
              style={{
                fontSize: '13px',
                fontWeight: 600,
                color: 'var(--text-2)',
                marginBottom: '20px',
              }}
            >
              Metric scores over time
            </div>

            <ResponsiveContainer width="100%" height={320}>
              <LineChart
                data={chartData}
                margin={{ top: 8, right: 24, bottom: 8, left: 0 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--border)"
                  vertical={false}
                />
                <XAxis
                  dataKey="date"
                  tick={{
                    fontSize: 12,
                    fill: 'var(--text-3)',
                    fontFamily: 'var(--font-sans)',
                  }}
                  axisLine={{ stroke: 'var(--border)' }}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 1]}
                  tickFormatter={(v: number) => v.toFixed(1)}
                  tick={{
                    fontSize: 12,
                    fill: 'var(--text-3)',
                    fontFamily: 'var(--font-mono)',
                  }}
                  axisLine={false}
                  tickLine={false}
                  width={36}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--surface-1)',
                    border: '1px solid var(--border)',
                    borderRadius: '8px',
                    fontSize: '13px',
                    fontFamily: 'var(--font-sans)',
                  }}
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  formatter={(v: any) => (typeof v === 'number' ? v.toFixed(3) : String(v ?? ''))}
                />
                <Legend
                  wrapperStyle={{
                    paddingTop: 16,
                    fontSize: 13,
                    fontFamily: 'var(--font-sans)',
                  }}
                />
                <ReferenceLine
                  y={0.9}
                  stroke="var(--border-hard)"
                  strokeDasharray="4 4"
                  label={{
                    value: 'Target 0.90',
                    position: 'right',
                    fontSize: 11,
                    fill: 'var(--text-3)',
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="faithfulness"
                  stroke="#B8860B"
                  strokeWidth={2}
                  dot={{ r: 4 }}
                  name="Faithfulness"
                />
                <Line
                  type="monotone"
                  dataKey="answer_relevancy"
                  stroke="#7B1C3A"
                  strokeWidth={2}
                  dot={{ r: 4 }}
                  name="Answer Relevancy"
                />
                <Line
                  type="monotone"
                  dataKey="context_precision"
                  stroke="#4A7C59"
                  strokeWidth={2}
                  dot={{ r: 4 }}
                  name="Context Precision"
                />
                <Line
                  type="monotone"
                  dataKey="context_recall"
                  stroke="#4A6080"
                  strokeWidth={2}
                  dot={{ r: 4 }}
                  name="Context Recall"
                />
              </LineChart>
            </ResponsiveContainer>

            {/* Run summary strip */}
            {latestRun && (
              <div
                style={{
                  display: 'flex',
                  gap: '24px',
                  fontSize: '13px',
                  color: 'var(--text-3)',
                  paddingTop: '14px',
                  borderTop: '1px solid var(--border-soft)',
                  flexWrap: 'wrap',
                }}
              >
                <span>
                  Last run:{' '}
                  <strong style={{ color: 'var(--text-2)', fontWeight: 500 }}>
                    {formatUtcDate(latestRun.started_at)}
                  </strong>
                </span>
                <span>
                  Scenarios:{' '}
                  <strong style={{ color: 'var(--text-2)', fontWeight: 500 }}>
                    {latestRun.scenario_count}
                  </strong>
                </span>
                <span>
                  Duration:{' '}
                  <strong style={{ color: 'var(--text-2)', fontWeight: 500 }}>
                    {formatDuration(latestRun.started_at, latestRun.finished_at)}
                  </strong>
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Scenarios tab */}
      {!isLoading && hasRuns && activeTab === 'scenarios' && (
        <div style={tableCardStyle}>
          {resultsQuery.isLoading ? (
            <div style={{ padding: '24px' }}>
              {[...Array(5)].map((_, i) => (
                <SkeletonRow key={i} />
              ))}
            </div>
          ) : (
            <table
              style={{
                width: '100%',
                borderCollapse: 'collapse',
              }}
            >
              <thead>
                <tr style={{ background: 'var(--surface-2)' }}>
                  <th
                    style={{
                      fontSize: '11px',
                      fontWeight: 600,
                      color: 'var(--text-3)',
                      letterSpacing: '0.04em',
                      textTransform: 'uppercase',
                      padding: '10px 16px',
                      textAlign: 'left',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    Question
                  </th>
                  <th
                    style={{
                      fontSize: '11px',
                      fontWeight: 600,
                      color: 'var(--text-3)',
                      letterSpacing: '0.04em',
                      textTransform: 'uppercase',
                      padding: '10px 16px',
                      textAlign: 'center',
                      whiteSpace: 'nowrap',
                      width: '80px',
                    }}
                  >
                    Source
                  </th>
                  {/* Metric headers with color dots */}
                  {([
                    { key: 'F', color: '#B8860B' },
                    { key: 'AR', color: '#7B1C3A' },
                    { key: 'CP', color: '#4A7C59' },
                    { key: 'CR', color: '#4A6080' },
                  ] as const).map(({ key, color }) => (
                    <th
                      key={key}
                      style={{
                        fontSize: '11px',
                        fontWeight: 600,
                        color: 'var(--text-3)',
                        letterSpacing: '0.04em',
                        textTransform: 'uppercase',
                        padding: '10px 16px',
                        textAlign: 'center',
                        whiteSpace: 'nowrap',
                        width: '80px',
                      }}
                    >
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '4px',
                        }}
                      >
                        <MetricDot color={color} />
                        {key}
                      </span>
                    </th>
                  ))}
                  <th
                    style={{
                      fontSize: '11px',
                      fontWeight: 600,
                      color: 'var(--text-3)',
                      letterSpacing: '0.04em',
                      textTransform: 'uppercase',
                      padding: '10px 16px',
                      textAlign: 'center',
                      whiteSpace: 'nowrap',
                      width: '60px',
                    }}
                  >
                    ✓
                  </th>
                </tr>
              </thead>
              <tbody>
                {scenarios.length === 0 ? (
                  <tr>
                    <td
                      colSpan={7}
                      style={{
                        textAlign: 'center',
                        padding: '32px 16px',
                        color: 'var(--text-3)',
                        fontSize: '14px',
                        borderTop: '1px solid var(--border-soft)',
                      }}
                    >
                      No scenario results for this run.
                    </td>
                  </tr>
                ) : (
                  scenarios.map((s) => (
                    <tr
                      key={s.scenario_id}
                      style={{
                        borderTop: '1px solid var(--border-soft)',
                        transition: 'background 0.1s',
                      }}
                      onMouseEnter={(e) => {
                        ;(e.currentTarget as HTMLTableRowElement).style.background =
                          'var(--surface-2)'
                      }}
                      onMouseLeave={(e) => {
                        ;(e.currentTarget as HTMLTableRowElement).style.background =
                          ''
                      }}
                    >
                      {/* Question */}
                      <td
                        style={{
                          fontSize: '13px',
                          color: 'var(--text-1)',
                          fontWeight: 500,
                          padding: '12px 16px',
                          maxWidth: '280px',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          verticalAlign: 'middle',
                        }}
                        title={s.question}
                      >
                        {s.question.length > 60
                          ? s.question.slice(0, 60) + '…'
                          : s.question}
                      </td>

                      {/* Source badge */}
                      <td
                        style={{
                          textAlign: 'center',
                          padding: '12px 16px',
                          verticalAlign: 'middle',
                        }}
                      >
                        <span
                          style={{
                            display: 'inline-block',
                            padding: '2px 8px',
                            borderRadius: 'var(--radius-xs)',
                            fontSize: '11px',
                            fontWeight: 500,
                            letterSpacing: '0.02em',
                            background:
                              s.source === 'generated'
                                ? 'var(--amber-bg)'
                                : '#EFF6FF',
                            color:
                              s.source === 'generated'
                                ? 'var(--amber)'
                                : '#1D4ED8',
                          }}
                        >
                          {s.source}
                        </span>
                      </td>

                      {/* Score cells: F, AR, CP, CR */}
                      {(
                        [
                          s.scores.faithfulness,
                          s.scores.answer_relevancy,
                          s.scores.context_precision,
                          s.scores.context_recall,
                        ] as number[]
                      ).map((score, idx) => (
                        <td
                          key={idx}
                          style={{
                            textAlign: 'center',
                            padding: '12px 16px',
                            verticalAlign: 'middle',
                          }}
                        >
                          <ScoreCell score={score} />
                        </td>
                      ))}

                      {/* PASS/FAIL badge */}
                      <td
                        style={{
                          textAlign: 'center',
                          padding: '12px 16px',
                          verticalAlign: 'middle',
                        }}
                      >
                        <span
                          style={{
                            display: 'inline-block',
                            padding: '2px 8px',
                            borderRadius: 'var(--radius-xs)',
                            fontSize: '11px',
                            fontWeight: 600,
                            letterSpacing: '0.02em',
                            background: s.passed
                              ? 'var(--green-bg)'
                              : 'var(--red-bg)',
                            color: s.passed ? 'var(--green)' : 'var(--red)',
                          }}
                        >
                          {s.passed ? 'PASS' : 'FAIL'}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
