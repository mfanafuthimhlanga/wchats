'use client'
import { useState, useEffect, use } from 'react'
import { useAuth } from '@clerk/nextjs'
import Link from 'next/link'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DeployTab = 'customize' | 'predeploy' | 'embed'
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

type ChecklistState =
  | { kind: 'idle' }
  | { kind: 'running'; runId: string }
  | { kind: 'complete'; run: ChecklistRun }
  | { kind: 'approved' }

interface ChecklistRun {
  id: string
  status: string
  recommendation: string | null
  report: Record<string, unknown> | null
  warnings: Array<{ warning_id: string; category: string; message: string; severity_level: string }>
  warning_acknowledgments: Record<string, string>
  all_warnings_acknowledged: boolean
}

interface WidgetConfig {
  appearance: 'floating-button' | 'floating-mini-modal' | 'slide-out-panel'
  launcher_shape: 'circle' | 'square'
  colors: {
    widget_bg: string
    header_bg: string
    header_text: string
    agent_bubble_bg: string
    agent_bubble_text: string
    user_bubble_bg: string
    user_bubble_text: string
    send_button: string
    input_bg: string
  }
  typography: {
    font_family: 'Inter' | 'System UI' | 'Georgia' | 'custom'
    font_custom_url: string | null
    border_radius_preset: 'sharp' | 'rounded' | 'pill'
  }
}

// ---------------------------------------------------------------------------
// Module-level constants
// ---------------------------------------------------------------------------

const DEFAULT_CONFIG: WidgetConfig = {
  appearance: 'floating-button',
  launcher_shape: 'circle',
  colors: {
    widget_bg: '#FDF9F5',
    header_bg: '#7B1C3A',
    header_text: '#FFFFFF',
    agent_bubble_bg: '#FDF9F5',
    agent_bubble_text: '#4A2030',
    user_bubble_bg: '#7B1C3A',
    user_bubble_text: '#FFFFFF',
    send_button: '#7B1C3A',
    input_bg: '#F7F0EA',
  },
  typography: {
    font_family: 'Inter',
    font_custom_url: null,
    border_radius_preset: 'rounded',
  },
}

const RADIUS_MAP: Record<'sharp' | 'rounded' | 'pill', string> = {
  sharp: '4px',
  rounded: '14px',
  pill: '24px',
}

const APPEARANCE_OPTIONS: { key: string; label: string; hint: string; icon: string }[] = [
  {
    key: 'floating-button',
    label: 'Floating Button',
    hint: 'Circular launcher fixed to page corner — chat opens on click',
    icon: '💬',
  },
  {
    key: 'floating-mini-modal',
    label: 'Floating Mini-modal',
    hint: 'Compact card showing greeting + input, expands on click',
    icon: '🪟',
  },
  {
    key: 'slide-out-panel',
    label: 'Slide-out Panel',
    hint: 'Full-height panel slides in from right page edge',
    icon: '▶',
  },
]

const COLOR_FIELDS: { key: keyof WidgetConfig['colors']; label: string }[] = [
  { key: 'widget_bg', label: 'Widget Background' },
  { key: 'header_bg', label: 'Header Background' },
  { key: 'header_text', label: 'Header Text' },
  { key: 'agent_bubble_bg', label: 'Agent Bubble Background' },
  { key: 'agent_bubble_text', label: 'Agent Bubble Text' },
  { key: 'user_bubble_bg', label: 'User Bubble Background' },
  { key: 'user_bubble_text', label: 'User Bubble Text' },
  { key: 'send_button', label: 'Send Button' },
  { key: 'input_bg', label: 'Input Field Background' },
]

function EMBED_SNIPPET(id: string): string {
  return '<script src="https://widget.veridian.app/widget.js" data-agent="' + id + '" async></script>'
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ColorRow({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '10px 0' }}>
      <input
        type="color"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ width: '24px', height: '24px', border: 'none', cursor: 'pointer', borderRadius: '50%', padding: 0 }}
        aria-label={`${label} color picker (currently ${value})`}
      />
      <span style={{ flex: 1, fontSize: '13px', color: 'var(--text-2)' }}>{label}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-3)' }}>{value}</span>
    </div>
  )
}

function AppearanceCard({
  option,
  selected,
  onSelect,
}: {
  option: { key: string; label: string; hint: string; icon: string }
  selected: boolean
  onSelect: () => void
}) {
  return (
    <label
      style={{
        display: 'block',
        padding: '16px',
        borderRadius: 'var(--radius-xs)',
        background: selected ? 'var(--accent-dim)' : 'var(--surface-2)',
        border: `1px solid ${selected ? 'var(--accent)' : 'var(--border-soft)'}`,
        cursor: 'pointer',
        marginBottom: '8px',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <div
          style={{
            width: '36px',
            height: '36px',
            background: 'var(--surface-3)',
            borderRadius: '8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '18px',
          }}
        >
          {option.icon}
        </div>
        <input
          type="radio"
          name="appearance"
          checked={selected}
          onChange={onSelect}
          style={{ accentColor: 'var(--accent)' }}
          aria-label={option.label}
        />
      </div>
      <div style={{ fontSize: '13.5px', fontWeight: 600, color: 'var(--text-1)', marginBottom: '4px' }}>
        {option.label}
      </div>
      <div style={{ fontSize: '12px', color: 'var(--text-3)', lineHeight: 1.45 }}>
        {option.hint}
      </div>
    </label>
  )
}

// ---------------------------------------------------------------------------
// DeployPage
// ---------------------------------------------------------------------------

export default function DeployPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  // State
  const [activeTab, setActiveTab] = useState<DeployTab>('customize')
  const [widgetConfig, setWidgetConfig] = useState<WidgetConfig>(DEFAULT_CONFIG)
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied'>('idle')
  const [checklistState, setChecklistState] = useState<ChecklistState>({ kind: 'idle' })
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())
  const [checklistError, setChecklistError] = useState<string | null>(null)

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  const scoreColor = (score: number) =>
    score >= 0.9 ? 'var(--green)' : score >= 0.7 ? 'var(--amber)' : 'var(--red)'

  // ---------------------------------------------------------------------------
  // Polling useEffect for checklist runs
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (checklistState.kind !== 'running') return
    const interval = setInterval(async () => {
      try {
        const token = await getToken()
        if (!token) return
        const r = await fetch(
          `${apiBase}/api/v1/agents/${id}/checklist-runs/${checklistState.runId}`,
          { headers: { Authorization: `Bearer ${token}` } }
        )
        if (!r.ok) return
        const data = await r.json()
        const run: ChecklistRun = data.run
        if (run.status === 'complete' || run.status === 'failed') {
          if (run.status === 'failed') {
            setChecklistState({ kind: 'idle' })
            setChecklistError('Checklist run failed. Try again or check the API logs.')
          } else {
            setChecklistState({ kind: 'complete', run })
          }
          clearInterval(interval)
        }
      } catch { /* ignore poll errors */ }
    }, 3000)
    return () => clearInterval(interval)
  }, [checklistState, id, apiBase, getToken])

  // ---------------------------------------------------------------------------
  // Load saved widget config on mount
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const loadConfig = async () => {
      try {
        const token = await getToken()
        if (!token) {
          setLoadError('Not authenticated. Please sign in.')
          return
        }
        const r = await fetch(`${apiBase}/api/v1/agents/${id}/widget-config`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!r.ok) {
          setLoadError(`Failed to load widget config (HTTP ${r.status}).`)
          return
        }
        const data = await r.json()
        if (Object.keys(data).length > 0) {
          setWidgetConfig((prev) => ({
            ...prev,
            ...data,
            colors: { ...prev.colors, ...(data.colors ?? {}) },
            typography: { ...prev.typography, ...(data.typography ?? {}) },
          }))
        }
      } catch (err) {
        console.error(err)
        setLoadError('Failed to load widget config. Please refresh.')
      }
    }
    loadConfig()
  }, [id, apiBase, getToken])

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleCopyEmbed = () => {
    navigator.clipboard.writeText(EMBED_SNIPPET(id))
    setCopyStatus('copied')
    setTimeout(() => setCopyStatus('idle'), 2000)
  }

  const handleSaveDesign = async () => {
    const url = widgetConfig.typography.font_custom_url
    if (url) {
      try {
        const parsed = new URL(url)
        if (parsed.protocol !== 'https:') {
          setSaveStatus('error')
          setLoadError('Custom font URL must use HTTPS.')
          return
        }
      } catch {
        setSaveStatus('error')
        setLoadError('Custom font URL is not a valid URL.')
        return
      }
    }
    setSaveStatus('saving')
    try {
      const token = await getToken()
      if (!token) {
        setSaveStatus('error')
        return
      }
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/widget-config`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(widgetConfig),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
    } catch (err) {
      console.error(err)
      setSaveStatus('error')
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div
      style={{
        padding: '32px 40px',
        maxWidth: '1400px',
        margin: '0 auto',
        fontFamily: 'var(--font-sans)',
      }}
    >
      {/* Back link */}
      <Link
        href={`/agents/${id}`}
        style={{
          fontSize: '14px',
          color: 'var(--accent)',
          textDecoration: 'none',
          display: 'inline-block',
          marginBottom: '24px',
        }}
      >
        ← Back to journey
      </Link>

      {/* Page header */}
      <h1 style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-1)', margin: '0 0 6px' }}>
        Deploy
      </h1>
      <p style={{ fontSize: '14px', color: 'var(--text-3)', margin: '0 0 24px' }}>
        Embed the agent on your site and customise its look.
      </p>

      {/* Load error alert */}
      {loadError && (
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
          }}
        >
          {loadError}
        </div>
      )}

      {/* Sub-tab nav */}
      <div
        role="tablist"
        style={{
          display: 'flex',
          borderBottom: '1px solid var(--border-soft)',
          marginBottom: '24px',
          gap: '4px',
        }}
      >
        <button
          role="tab"
          id="tab-customize"
          aria-selected={activeTab === 'customize'}
          aria-controls="panel-customize"
          onClick={() => setActiveTab('customize')}
          style={{
            padding: '10px 20px',
            border: 'none',
            borderBottom: `2px solid ${activeTab === 'customize' ? 'var(--accent)' : 'transparent'}`,
            background: 'none',
            color: activeTab === 'customize' ? 'var(--accent)' : 'var(--text-3)',
            fontWeight: activeTab === 'customize' ? 600 : 400,
            fontSize: '14px',
            cursor: activeTab === 'customize' ? 'default' : 'pointer',
            fontFamily: 'var(--font-sans)',
            marginBottom: -1,
          }}
        >
          Customise Widget
        </button>
        <button
          role="tab"
          id="tab-predeploy"
          aria-selected={activeTab === 'predeploy'}
          aria-controls="panel-predeploy"
          onClick={() => setActiveTab('predeploy')}
          style={{
            padding: '10px 20px',
            border: 'none',
            borderBottom: `2px solid ${activeTab === 'predeploy' ? 'var(--accent)' : 'transparent'}`,
            background: 'none',
            color: activeTab === 'predeploy' ? 'var(--accent)' : 'var(--text-3)',
            fontWeight: activeTab === 'predeploy' ? 600 : 400,
            fontSize: '14px',
            cursor: activeTab === 'predeploy' ? 'default' : 'pointer',
            fontFamily: 'var(--font-sans)',
            marginBottom: -1,
          }}
        >
          Pre-Deploy Check
        </button>
        <button
          role="tab"
          id="tab-embed"
          aria-selected={activeTab === 'embed'}
          aria-controls="panel-embed"
          onClick={() => setActiveTab('embed')}
          style={{
            padding: '10px 20px',
            border: 'none',
            borderBottom: `2px solid ${activeTab === 'embed' ? 'var(--accent)' : 'transparent'}`,
            background: 'none',
            color: activeTab === 'embed' ? 'var(--accent)' : 'var(--text-3)',
            fontWeight: activeTab === 'embed' ? 600 : 400,
            fontSize: '14px',
            cursor: activeTab === 'embed' ? 'default' : 'pointer',
            fontFamily: 'var(--font-sans)',
            marginBottom: -1,
          }}
        >
          Embed Code
        </button>
      </div>

      {/* Tab content */}
      {activeTab === 'embed' && (
        <div
          role="tabpanel"
          id="panel-embed"
          aria-labelledby="tab-embed"
          style={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border-soft)',
            borderRadius: 'var(--radius-xs)',
            padding: '24px',
          }}
        >
          <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-1)', margin: '0 0 8px' }}>
            Paste this snippet into your site&apos;s &lt;head&gt;
          </h2>
          <p style={{ fontSize: '13px', color: 'var(--text-3)', margin: '0 0 0' }}>
            The widget loads asynchronously and is under 20kb gzipped.
          </p>
          <pre
            style={{
              background: 'var(--accent-deep)',
              color: 'rgba(255,255,255,0.92)',
              padding: '16px',
              borderRadius: 'var(--radius-xs)',
              fontFamily: 'var(--font-mono)',
              fontSize: '13px',
              overflowX: 'auto',
              margin: '12px 0 16px',
            }}
          >
            <code>{EMBED_SNIPPET(id)}</code>
          </pre>
          <button
            onClick={handleCopyEmbed}
            style={{
              padding: '10px 18px',
              background: 'var(--accent)',
              color: '#fff',
              border: 'none',
              borderRadius: 'var(--radius-xs)',
              cursor: 'pointer',
              fontWeight: 600,
              fontSize: '14px',
              fontFamily: 'var(--font-sans)',
            }}
          >
            {copyStatus === 'copied' ? 'Copied!' : 'Copy snippet'}
          </button>
          <p
            style={{
              fontSize: '12px',
              color: 'var(--text-4)',
              fontStyle: 'italic',
              marginTop: '8px',
            }}
          >
            Note: The CDN URL above is a preview placeholder. Widget CDN deployment is not yet live and will be activated in a future release.
          </p>
        </div>
      )}

      {activeTab === 'predeploy' && (
        <div role="tabpanel" id="panel-predeploy" aria-labelledby="tab-predeploy" style={{ padding: '32px 40px' }}>
          {/* Checklist error alert */}
          {checklistError && (
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
              }}
            >
              {checklistError}
            </div>
          )}

          {/* STATE 1: idle — No Run */}
          {checklistState.kind === 'idle' && (
            <div style={{ border: '2px dashed var(--border)', padding: '64px 40px', textAlign: 'center', borderRadius: 'var(--radius-xs)' }}>
              <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-1)', margin: '0 0 12px' }}>
                Ready to deploy your agent?
              </h2>
              <p style={{ fontSize: '14px', color: 'var(--text-3)', maxWidth: '420px', margin: '0 auto 24px', lineHeight: 1.5 }}>
                Run the pre-deployment checklist to verify your agent meets quality standards before going live. The check takes 1–2 minutes.
              </p>
              <button
                onClick={async () => {
                  setChecklistError(null)
                  try {
                    const token = await getToken()
                    if (!token) return
                    const r = await fetch(`${apiBase}/api/v1/agents/${id}/checklist-runs`, {
                      method: 'POST',
                      headers: { Authorization: `Bearer ${token}` },
                    })
                    if (r.status === 202) {
                      const data = await r.json()
                      setChecklistState({ kind: 'running', runId: data.checklist_run_id })
                    } else {
                      setChecklistError(`Failed to start checklist run (HTTP ${r.status}).`)
                    }
                  } catch (err) {
                    console.error(err)
                    setChecklistError('Failed to start checklist run. Please try again.')
                  }
                }}
                style={{
                  background: 'var(--accent)',
                  color: '#fff',
                  padding: '10px 18px',
                  borderRadius: 'var(--radius-xs)',
                  fontWeight: 600,
                  fontSize: '14px',
                  border: 'none',
                  cursor: 'pointer',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Run pre-deployment checklist
              </button>
            </div>
          )}

          {/* STATE 2: running */}
          {checklistState.kind === 'running' && (
            <div style={{ border: '2px dashed var(--border)', padding: '64px 40px', textAlign: 'center', borderRadius: 'var(--radius-xs)' }}>
              <div
                aria-label="Checking readiness"
                aria-live="polite"
                style={{
                  width: '32px',
                  height: '32px',
                  border: '3px solid var(--border)',
                  borderTopColor: 'var(--accent)',
                  borderRadius: '50%',
                  animation: 'spin-cw 1s linear infinite',
                  margin: '0 auto 20px',
                }}
              />
              <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-1)', margin: '0 0 12px' }}>
                {"Checking your agent's readiness…"}
              </h2>
              <p style={{ fontSize: '14px', color: 'var(--text-3)', lineHeight: 1.5 }}>
                This usually takes 1–2 minutes. Your results will appear here automatically.
              </p>
            </div>
          )}

          {/* STATES 3/4/4b: complete */}
          {checklistState.kind === 'complete' && (() => {
            const run = checklistState.run
            const rec = run.recommendation
            const report = run.report as Record<string, unknown> | null
            const evalSummary = report?.eval_summary as Record<string, unknown> | null
            const redTeamSummary = report?.red_team_summary as Record<string, unknown> | null
            const corpusStats = report?.corpus_stats as Record<string, unknown> | null
            const qaStats = report?.verified_qa_stats as Record<string, unknown> | null
            const passRates = (evalSummary?.pass_rates as Record<string, number>) ?? {}
            const failingScenarios = (evalSummary?.failing_scenarios as number) ?? 0
            const deploymentBlocked = redTeamSummary?.deployment_blocked as boolean | undefined
            const severityCounts = (redTeamSummary?.severity_counts as Record<string, number>) ?? {}
            const rowCount = (qaStats?.row_count as number) ?? 0
            const avgFaithfulness = (qaStats?.avg_faithfulness as number) ?? 0
            const avgRelevance = (qaStats?.avg_relevance as number) ?? 0
            const docCount = (corpusStats?.document_count as number) ?? 0
            const chunkCount = (corpusStats?.chunk_count as number) ?? 0
            const lastIngested = corpusStats?.last_ingested_at as string | null | undefined
            const isApprovable =
              rec === 'ship' ||
              (rec === 'ship_with_warnings' && acknowledged.size === run.warnings.length && run.warnings.length > 0)

            const cardStyle = {
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-xs)',
              padding: '16px',
              boxShadow: 'var(--shadow-card)',
            }
            const labelStyle = {
              fontSize: '11px',
              fontWeight: 600,
              textTransform: 'uppercase' as const,
              letterSpacing: '0.08em',
              color: 'var(--text-3)',
              marginBottom: '12px',
            }
            const metricRowStyle = {
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              fontSize: '13px',
              marginBottom: '6px',
            }

            return (
              <>
                {/* Banner */}
                {rec === 'block' && (
                  <div
                    role="alert"
                    style={{
                      background: 'var(--red-bg)',
                      border: '1px solid rgba(185,28,28,0.3)',
                      borderLeft: '4px solid var(--red)',
                      borderRadius: 'var(--radius-xs)',
                      padding: '16px',
                      marginBottom: '24px',
                    }}
                  >
                    <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--red)', marginBottom: '6px' }}>
                      ✕ <strong>Deployment blocked</strong>
                    </div>
                    <div style={{ fontSize: '14px', color: 'var(--text-3)' }}>
                      {(report?.summary as string) || 'Agent has critical issues.'}
                    </div>
                  </div>
                )}
                {rec === 'ship_with_warnings' && (
                  <div
                    role="alert"
                    style={{
                      background: 'var(--amber-bg)',
                      border: '1px solid rgba(146,64,14,0.3)',
                      borderLeft: '4px solid var(--amber)',
                      borderRadius: 'var(--radius-xs)',
                      padding: '16px',
                      marginBottom: '24px',
                    }}
                  >
                    <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--amber)', marginBottom: '6px' }}>
                      ⚠ Ready to deploy with warnings
                    </div>
                    <div style={{ fontSize: '14px', color: 'var(--text-3)' }}>{report?.summary as string}</div>
                  </div>
                )}
                {rec === 'ship' && (
                  <div
                    style={{
                      background: 'var(--green-bg)',
                      border: '1px solid rgba(22,163,74,0.3)',
                      borderLeft: '4px solid var(--green-solid)',
                      borderRadius: 'var(--radius-xs)',
                      padding: '16px',
                      marginBottom: '24px',
                    }}
                  >
                    <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--green)', marginBottom: '6px' }}>
                      Agent is ready to deploy
                    </div>
                    <div style={{ fontSize: '14px', color: 'var(--text-3)' }}>{report?.summary as string}</div>
                  </div>
                )}

                {/* Signal cards grid */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '24px' }}>
                  {/* Eval Quality */}
                  <div style={cardStyle}>
                    <p style={labelStyle}>EVAL QUALITY</p>
                    {evalSummary?.last_run_at != null && (
                      <div style={{ fontSize: '12px', color: 'var(--text-4)', marginBottom: '8px' }}>
                        Last run: {String(evalSummary.last_run_at)}
                      </div>
                    )}
                    {Object.entries(passRates).map(([metric, score]) => (
                      <div key={metric} style={metricRowStyle}>
                        <span style={{ color: 'var(--text-2)' }}>{metric.replace(/_/g, ' ')}</span>
                        <span style={{ fontFamily: 'var(--font-mono)', color: scoreColor(score) }}>{score.toFixed(3)}</span>
                      </div>
                    ))}
                    {failingScenarios > 0 && (
                      <div style={{ fontSize: '13px', color: 'var(--red)', marginTop: '8px' }}>
                        {failingScenarios} scenario{failingScenarios !== 1 ? 's' : ''} failed
                      </div>
                    )}
                  </div>

                  {/* Security */}
                  <div style={cardStyle}>
                    <p style={labelStyle}>SECURITY</p>
                    {redTeamSummary?.last_run_at != null && (
                      <div style={{ fontSize: '12px', color: 'var(--text-4)', marginBottom: '8px' }}>
                        Last run: {String(redTeamSummary.last_run_at)}
                      </div>
                    )}
                    <div style={{ ...metricRowStyle, marginBottom: '10px' }}>
                      <span style={{ color: 'var(--text-2)', fontSize: '13px' }}>Deployment status</span>
                      <span style={{
                        fontSize: '11px',
                        fontWeight: 600,
                        padding: '2px 8px',
                        borderRadius: 'var(--radius-xs)',
                        background: deploymentBlocked ? 'var(--red-bg)' : 'var(--green-bg)',
                        color: deploymentBlocked ? 'var(--red)' : 'var(--green)',
                      }}>
                        {deploymentBlocked ? 'BLOCKED' : 'OK'}
                      </span>
                    </div>
                    {(['critical', 'high', 'medium', 'low'] as const).map((sev) => (
                      <div key={sev} style={metricRowStyle}>
                        <span style={{ color: 'var(--text-2)' }}>{sev.charAt(0).toUpperCase() + sev.slice(1)}</span>
                        <span style={{
                          fontFamily: 'var(--font-mono)',
                          color: sev === 'critical' ? 'var(--red)' : sev === 'high' ? '#EA580C' : sev === 'medium' ? 'var(--amber)' : 'var(--text-3)',
                        }}>
                          {(severityCounts[sev] ?? 0)} finding{(severityCounts[sev] ?? 0) !== 1 ? 's' : ''}
                        </span>
                      </div>
                    ))}
                  </div>

                  {/* Corpus Coverage */}
                  <div style={cardStyle}>
                    <p style={labelStyle}>CORPUS COVERAGE</p>
                    <div style={metricRowStyle}>
                      <span style={{ color: 'var(--text-2)' }}>Documents</span>
                      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-1)' }}>{docCount}</span>
                    </div>
                    <div style={metricRowStyle}>
                      <span style={{ color: 'var(--text-2)' }}>Chunks</span>
                      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-1)' }}>{chunkCount}</span>
                    </div>
                    {lastIngested && (
                      <div style={metricRowStyle}>
                        <span style={{ color: 'var(--text-2)' }}>Last ingested</span>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-3)' }}>
                          {new Date(lastIngested).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Knowledge Depth */}
                  <div style={cardStyle}>
                    <p style={labelStyle}>KNOWLEDGE DEPTH</p>
                    <div style={metricRowStyle}>
                      <span style={{ color: 'var(--text-2)' }}>Verified Q&amp;A pairs</span>
                      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-1)' }}>{rowCount}</span>
                    </div>
                    <div style={metricRowStyle}>
                      <span style={{ color: 'var(--text-2)' }}>Avg faithfulness</span>
                      <span style={{ fontFamily: 'var(--font-mono)', color: scoreColor(avgFaithfulness) }}>{avgFaithfulness.toFixed(3)}</span>
                    </div>
                    <div style={metricRowStyle}>
                      <span style={{ color: 'var(--text-2)' }}>Avg relevance</span>
                      <span style={{ fontFamily: 'var(--font-mono)', color: scoreColor(avgRelevance) }}>{avgRelevance.toFixed(3)}</span>
                    </div>
                    {rowCount < 50 && (
                      <div style={{
                        marginTop: '8px',
                        fontSize: '11px',
                        fontWeight: 500,
                        padding: '2px 8px',
                        borderRadius: 'var(--radius-xs)',
                        background: 'var(--amber-bg)',
                        color: 'var(--amber)',
                        display: 'inline-block',
                      }}>
                        Below 50 — agent answers more from scratch
                      </div>
                    )}
                  </div>
                </div>

                {/* Warning acknowledgments (ship_with_warnings only) */}
                {rec === 'ship_with_warnings' && run.warnings.length > 0 && (
                  <div style={{ marginBottom: '24px' }}>
                    <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-1)', marginBottom: '12px' }}>
                      Acknowledge each warning to proceed
                    </p>
                    {run.warnings.map((w) => {
                      const badgeColors: Record<string, { bg: string; color: string }> = {
                        eval_quality: { bg: 'var(--amber-bg)', color: 'var(--amber)' },
                        security: { bg: 'var(--red-bg)', color: 'var(--red)' },
                        knowledge_depth: { bg: '#EFF6FF', color: '#1D4ED8' },
                        corpus_coverage: { bg: 'var(--green-bg)', color: 'var(--green)' },
                      }
                      const badge = badgeColors[w.category] ?? { bg: 'var(--surface-3)', color: 'var(--text-3)' }
                      return (
                        <label
                          key={w.warning_id}
                          style={{
                            display: 'flex',
                            alignItems: 'flex-start',
                            gap: '12px',
                            padding: '12px 0',
                            borderBottom: '1px solid var(--border-soft)',
                            cursor: 'pointer',
                          }}
                        >
                          <input
                            type="checkbox"
                            style={{ accentColor: 'var(--accent)', width: 16, height: 16, marginTop: '2px', flexShrink: 0 }}
                            checked={acknowledged.has(w.warning_id)}
                            onChange={async (e) => {
                              if (e.target.checked) {
                                try {
                                  const token = await getToken()
                                  if (!token) return
                                  await fetch(
                                    `${apiBase}/api/v1/agents/${id}/checklist-runs/${run.id}/acknowledge`,
                                    {
                                      method: 'POST',
                                      headers: {
                                        Authorization: `Bearer ${token}`,
                                        'Content-Type': 'application/json',
                                      },
                                      body: JSON.stringify({ warning_ids: [w.warning_id] }),
                                    }
                                  )
                                } catch { /* ignore */ }
                                setAcknowledged((prev) => new Set(prev).add(w.warning_id))
                              }
                            }}
                          />
                          <span style={{
                            fontSize: '11px',
                            fontWeight: 500,
                            padding: '2px 8px',
                            borderRadius: 'var(--radius-xs)',
                            background: badge.bg,
                            color: badge.color,
                            flexShrink: 0,
                          }}>
                            {w.category.replace(/_/g, ' ')}
                          </span>
                          <span style={{ fontSize: '14px', color: 'var(--text-1)', flex: 1 }}>{w.message}</span>
                        </label>
                      )
                    })}
                  </div>
                )}

                {/* Approve button */}
                {rec !== 'block' && (
                  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <button
                      disabled={!isApprovable}
                      aria-disabled={!isApprovable}
                      onClick={async () => {
                        if (!isApprovable) return
                        setChecklistError(null)
                        try {
                          const token = await getToken()
                          if (!token) return
                          const r = await fetch(`${apiBase}/api/v1/agents/${id}/approve-deployment`, {
                            method: 'POST',
                            headers: {
                              Authorization: `Bearer ${token}`,
                              'Content-Type': 'application/json',
                            },
                            body: JSON.stringify({ checklist_run_id: run.id }),
                          })
                          if (r.ok) {
                            setChecklistState({ kind: 'approved' })
                          } else {
                            const d = await r.json().catch(() => ({}))
                            setChecklistError(`Approval failed — ${(d as Record<string, string>).detail ?? r.status}. Ensure all warnings are acknowledged.`)
                          }
                        } catch (err) {
                          console.error(err)
                          setChecklistError('Approval failed. Please try again.')
                        }
                      }}
                      style={{
                        padding: '12px 24px',
                        background: isApprovable ? 'var(--accent)' : 'var(--surface-3)',
                        color: isApprovable ? '#fff' : 'var(--text-4)',
                        border: 'none',
                        borderRadius: 'var(--radius-xs)',
                        fontWeight: 600,
                        fontSize: '14px',
                        cursor: isApprovable ? 'pointer' : 'not-allowed',
                        fontFamily: 'var(--font-sans)',
                      }}
                    >
                      Approve deployment
                    </button>
                  </div>
                )}
                {rec === 'block' && (
                  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <button
                      disabled
                      aria-disabled="true"
                      style={{
                        padding: '12px 24px',
                        background: 'var(--surface-3)',
                        color: 'var(--text-4)',
                        border: 'none',
                        borderRadius: 'var(--radius-xs)',
                        fontWeight: 600,
                        fontSize: '14px',
                        cursor: 'not-allowed',
                        fontFamily: 'var(--font-sans)',
                      }}
                    >
                      Cannot approve — resolve issues above
                    </button>
                  </div>
                )}
              </>
            )
          })()}

          {/* STATE 5: approved */}
          {checklistState.kind === 'approved' && (
            <div style={{ textAlign: 'center', padding: '64px 40px' }}>
              <div style={{
                display: 'inline-block',
                background: 'var(--green-bg)',
                border: '1px solid rgba(22,163,74,0.3)',
                borderRadius: '9999px',
                padding: '6px 16px',
                color: 'var(--green)',
                fontWeight: 600,
                fontSize: '13px',
                marginBottom: '20px',
              }}>
                ● Live
              </div>
              <h2 style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-1)', margin: '0 0 12px' }}>
                Your agent is live
              </h2>
              <p style={{ fontSize: '14px', color: 'var(--text-3)', marginBottom: '16px' }}>
                Your widget is now active and ready to embed on your site.
              </p>
              <p style={{ fontSize: '14px', color: 'var(--text-3)' }}>
                Go to the{' '}
                <span
                  style={{ color: 'var(--accent)', cursor: 'pointer', textDecoration: 'none' }}
                  onClick={() => setActiveTab('embed')}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter') setActiveTab('embed') }}
                >
                  Embed Code
                </span>
                {' '}tab to get your installation snippet.
              </p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'customize' && (
        <div role="tabpanel" id="panel-customize" aria-labelledby="tab-customize" style={{ padding: '0' }}>
          {/* 3-column design panel */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '255px 310px 1fr',
              gap: '24px',
            }}
          >
            {/* Column 1: Appearance Mode */}
            <div>
              <p
                style={{
                  fontSize: '11px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--text-3)',
                  marginBottom: '12px',
                  paddingBottom: '12px',
                  borderBottom: '1px solid var(--border-soft)',
                  margin: '0 0 12px',
                }}
              >
                APPEARANCE MODE
              </p>
              {APPEARANCE_OPTIONS.map((opt) => (
                <AppearanceCard
                  key={opt.key}
                  option={opt}
                  selected={widgetConfig.appearance === opt.key}
                  onSelect={() =>
                    setWidgetConfig((c) => ({ ...c, appearance: opt.key as WidgetConfig['appearance'] }))
                  }
                />
              ))}
            </div>

            {/* Column 2: Style Pickers */}
            <div
              style={{
                background: 'var(--surface-2)',
                border: '1px solid var(--border-soft)',
                borderRadius: 'var(--radius-xs)',
                padding: '16px',
              }}
            >
              {/* Colors sub-section */}
              <p
                style={{
                  fontSize: '11px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--text-3)',
                  marginBottom: '8px',
                  paddingBottom: '8px',
                  borderBottom: '1px solid var(--border-soft)',
                  margin: '0 0 8px',
                }}
              >
                COLORS
              </p>
              {COLOR_FIELDS.map((f) => (
                <ColorRow
                  key={f.key}
                  label={f.label}
                  value={widgetConfig.colors[f.key]}
                  onChange={(v) =>
                    setWidgetConfig((c) => ({ ...c, colors: { ...c.colors, [f.key]: v } }))
                  }
                />
              ))}

              {/* Typography sub-section */}
              <div style={{ marginTop: '20px' }}>
                <p
                  style={{
                    fontSize: '11px',
                    textTransform: 'uppercase',
                    letterSpacing: '0.08em',
                    color: 'var(--text-3)',
                    marginBottom: '8px',
                    paddingBottom: '8px',
                    borderBottom: '1px solid var(--border-soft)',
                    margin: '0 0 8px',
                  }}
                >
                  TYPOGRAPHY
                </p>
                {/* Font family select */}
                <div style={{ marginBottom: '12px' }}>
                  <label
                    style={{
                      display: 'block',
                      fontSize: '12px',
                      color: 'var(--text-3)',
                      marginBottom: '4px',
                    }}
                  >
                    Font Family
                  </label>
                  <select
                    value={widgetConfig.typography.font_family}
                    onChange={(e) =>
                      setWidgetConfig((c) => ({
                        ...c,
                        typography: {
                          ...c.typography,
                          font_family: e.target.value as WidgetConfig['typography']['font_family'],
                        },
                      }))
                    }
                    style={{
                      width: '100%',
                      background: 'var(--surface-1)',
                      border: '1px solid var(--border)',
                      padding: '8px 10px',
                      borderRadius: 'var(--radius-xs)',
                      fontSize: '13px',
                      color: 'var(--text-1)',
                      fontFamily: 'var(--font-sans)',
                    }}
                  >
                    <option value="Inter">Inter</option>
                    <option value="System UI">System UI</option>
                    <option value="Georgia">Georgia</option>
                    <option value="custom">Custom URL</option>
                  </select>
                </div>

                {/* Custom font URL input — shown only when custom is selected */}
                {widgetConfig.typography.font_family === 'custom' && (
                  <div style={{ marginBottom: '12px' }}>
                    <label
                      style={{
                        display: 'block',
                        fontSize: '12px',
                        color: 'var(--text-3)',
                        marginBottom: '4px',
                      }}
                    >
                      Custom Font URL
                    </label>
                    <input
                      type="url"
                      placeholder="https://..."
                      value={widgetConfig.typography.font_custom_url ?? ''}
                      onChange={(e) =>
                        setWidgetConfig((c) => ({
                          ...c,
                          typography: { ...c.typography, font_custom_url: e.target.value || null },
                        }))
                      }
                      style={{
                        width: '100%',
                        background: 'var(--surface-1)',
                        border: '1px solid var(--border)',
                        padding: '8px 10px',
                        borderRadius: 'var(--radius-xs)',
                        fontSize: '13px',
                        color: 'var(--text-1)',
                        fontFamily: 'var(--font-sans)',
                        boxSizing: 'border-box',
                      }}
                    />
                  </div>
                )}

                {/* Border radius preset */}
                <div>
                  <label
                    style={{
                      display: 'block',
                      fontSize: '12px',
                      color: 'var(--text-3)',
                      marginBottom: '6px',
                    }}
                  >
                    Border Radius
                  </label>
                  <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                    {(['sharp', 'rounded', 'pill'] as const).map((preset) => {
                      const isSelected = widgetConfig.typography.border_radius_preset === preset
                      return (
                        <button
                          key={preset}
                          onClick={() =>
                            setWidgetConfig((c) => ({
                              ...c,
                              typography: { ...c.typography, border_radius_preset: preset },
                            }))
                          }
                          style={{
                            flex: 1,
                            padding: '6px 0',
                            background: isSelected ? 'var(--accent)' : 'var(--surface-2)',
                            color: isSelected ? '#fff' : 'var(--text-3)',
                            border: isSelected ? 'none' : '1px solid var(--border)',
                            borderRadius: 'var(--radius-xs)',
                            cursor: 'pointer',
                            fontSize: '12px',
                            fontWeight: isSelected ? 600 : 400,
                            fontFamily: 'var(--font-sans)',
                            textTransform: 'capitalize',
                          }}
                        >
                          {preset.charAt(0).toUpperCase() + preset.slice(1)}
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>
            </div>

            {/* Column 3: Live Preview */}
            <div
              style={{
                background: 'var(--surface-2)',
                border: '1px solid var(--border-soft)',
                borderRadius: 'var(--radius-xs)',
                padding: '24px',
                minHeight: '400px',
              }}
            >
              <p
                style={{
                  fontSize: '11px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--text-3)',
                  marginBottom: '16px',
                  paddingBottom: '12px',
                  borderBottom: '1px solid var(--border-soft)',
                  margin: '0 0 16px',
                }}
              >
                LIVE PREVIEW
              </p>

              {/* Widget mock — decorative, no API calls */}
              <div aria-hidden="true">
                {/* Chat window */}
                <div
                  style={{
                    width: '320px',
                    minHeight: '380px',
                    background: widgetConfig.colors.widget_bg,
                    borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                    border: '1px solid rgba(0,0,0,0.06)',
                    boxShadow: '0 4px 8px rgba(74,32,48,0.04), 0 16px 32px rgba(74,32,48,0.08)',
                    overflow: 'hidden',
                    fontFamily:
                      widgetConfig.typography.font_family === 'custom'
                        ? 'sans-serif'
                        : widgetConfig.typography.font_family,
                  }}
                >
                  {/* Header */}
                  <div
                    style={{
                      padding: '12px 16px',
                      background: widgetConfig.colors.header_bg,
                      color: widgetConfig.colors.header_text,
                      fontWeight: 600,
                      fontSize: '14px',
                    }}
                  >
                    Chats assistant
                  </div>

                  {/* Chat body */}
                  <div
                    style={{
                      padding: '16px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '10px',
                      height: '240px',
                      overflowY: 'auto',
                    }}
                  >
                    {/* Agent bubble */}
                    <div
                      style={{
                        alignSelf: 'flex-start',
                        maxWidth: '80%',
                        padding: '10px 14px',
                        background: widgetConfig.colors.agent_bubble_bg,
                        color: widgetConfig.colors.agent_bubble_text,
                        borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                        fontSize: '13px',
                      }}
                    >
                      Hi! Ask me anything about the business.
                    </div>

                    {/* User bubble */}
                    <div
                      style={{
                        alignSelf: 'flex-end',
                        maxWidth: '80%',
                        padding: '10px 14px',
                        background: widgetConfig.colors.user_bubble_bg,
                        color: widgetConfig.colors.user_bubble_text,
                        borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                        fontSize: '13px',
                      }}
                    >
                      What are your business hours?
                    </div>
                  </div>

                  {/* Input row */}
                  <div
                    style={{
                      display: 'flex',
                      gap: '8px',
                      padding: '12px 16px',
                      borderTop: '1px solid rgba(0,0,0,0.06)',
                    }}
                  >
                    <input
                      aria-hidden="true"
                      tabIndex={-1}
                      placeholder="Type a message…"
                      readOnly
                      style={{
                        flex: 1,
                        padding: '8px 12px',
                        background: widgetConfig.colors.input_bg,
                        border: '1px solid rgba(0,0,0,0.08)',
                        borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                        fontSize: '13px',
                        color: 'var(--text-3)',
                        fontFamily: 'inherit',
                      }}
                    />
                    <button
                      tabIndex={-1}
                      style={{
                        padding: '8px 14px',
                        background: widgetConfig.colors.send_button,
                        color: '#fff',
                        border: 'none',
                        borderRadius: RADIUS_MAP[widgetConfig.typography.border_radius_preset],
                        fontWeight: 600,
                        fontSize: '13px',
                        cursor: 'default',
                        fontFamily: 'inherit',
                      }}
                    >
                      Send
                    </button>
                  </div>
                </div>

                {/* Floating launcher mock — shown when appearance is floating-button */}
                {widgetConfig.appearance === 'floating-button' && (
                  <div
                    style={{
                      marginTop: '12px',
                      width: '42px',
                      height: '42px',
                      borderRadius: '50%',
                      background: widgetConfig.colors.send_button,
                      color: '#fff',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '20px',
                      marginLeft: 'auto',
                    }}
                  >
                    💬
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Save Design row */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              alignItems: 'center',
              gap: '12px',
              marginTop: '24px',
              paddingTop: '16px',
              borderTop: '1px solid var(--border-soft)',
            }}
          >
            {saveStatus === 'saved' && (
              <span style={{ color: 'var(--green)', fontSize: '13px', fontWeight: 600 }}>
                ✓ Saved
              </span>
            )}
            {saveStatus === 'error' && (
              <span style={{ color: 'var(--red)', fontSize: '13px' }}>
                Save failed — retry
              </span>
            )}
            <button
              onClick={handleSaveDesign}
              disabled={saveStatus === 'saving'}
              style={{
                padding: '12px 24px',
                background: 'var(--accent)',
                color: '#fff',
                border: 'none',
                borderRadius: 'var(--radius-xs)',
                fontWeight: 600,
                fontSize: '14px',
                cursor: saveStatus === 'saving' ? 'wait' : 'pointer',
                fontFamily: 'var(--font-sans)',
              }}
            >
              {saveStatus === 'saving' ? 'Saving…' : 'Save Design'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
