'use client'
import { use, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Btn from '../../../components/gotham/Btn'
import Chip from '../../../components/gotham/Chip'
import Ledger, { LedgerColHead, LedgerRowHead } from '../../../components/gotham/Ledger'
import Zone from '../../../components/gotham/Zone'
import { useGate } from '../../../components/gotham/GateProvider'

/**
 * Deploy — `/agents/[id]/deploy` (UI-SPEC S6.8, UI2-06, ported from
 * prototypes/gotham/deploy.html). Two-column `.bench`: the gate + embed +
 * appearance on the left, the sticky customer widget preview on the right
 * (hidden <=1100px).
 *
 * PRESERVED VERBATIM (non-regression, UI-SPEC S9): the real
 * `POST/GET /checklist-runs`, `POST .../acknowledge`,
 * `POST /approve-deployment` and `GET/POST /widget-config` endpoints the
 * prior dusk build already consumed. The embed snippet's CDN/API base env
 * vars and iframe query-string shape are unchanged.
 *
 * MUST-FIX 4 (UI-SPEC S6.8 / S10 anti-pattern S10.8): deploy.html's `.rig`
 * "Test the gate" simulate/clear buttons are prototype-only instrumentation
 * that fakes a critical red-team finding on a REAL agent — they are dropped
 * entirely. `data-gate` here is written exactly once, from the real
 * `recommendation` + `deployment_blocked` signals (mirrors the gatebar
 * pattern established in `agents/[id]/page.tsx`), never from a page-local
 * toggle.
 *
 * Bug fix (Rule 1, found during this port): the prior dusk page polled
 * `GET /checklist-runs/{checklist_run_id}` using the id the trigger POST
 * returns — but that id is the CELERY TASK id
 * (`apps/api/app/api/v1/deployment.py` returns `task.id`), not the
 * `checklist_runs.id` row the Celery task creates for itself
 * (`apps/api/app/worker/tasks/runtime/deployment.py:125`, a fresh
 * `gen_random_uuid()`). Polling by that id 404s forever. This port instead
 * polls the LIST endpoint (`GET /checklist-runs`, already ordered
 * newest-first) while its first row's `status === 'running'` — the same
 * row the trigger POST just inserted, so this is strictly correct and
 * avoids the mismatched id.
 *
 * Widget-exception palette (UI-SPEC S4): the sticky preview hardcodes a
 * light theme (`--widget-accent: #C79A3C`, scoped to `.preview`/`.stage`
 * only, never `:root`) and must NOT repaint on `data-gate="blocked"` — see
 * `PAGE_CSS` below, none of its rules reference `--live`/`--seal`/`--ch-*`.
 *
 * Appearance tiles: UI-SPEC S6.8's prototype copy ("Floating button / Side
 * panel / Inline") uses placeholder radio values (`float`/`panel`/`inline`)
 * that do not match the real `widget_config.appearance` enum
 * (`floating-button` | `floating-mini-modal` | `slide-out-panel` —
 * `apps/api/app/schemas/agent.py` `WidgetConfigUpdate`). This port keeps
 * the REAL three enum values + the dusk page's existing labels/hints
 * (non-regression) and re-skins only the tile visuals to the Gotham
 * `.tile`/`Zone` pattern.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AgentDetail {
  id: string
  name: string
  status: string
  is_deployed?: boolean
  soul_role?: string | null
  soul_voice?: string | null
}

interface ChecklistWarning {
  warning_id: string
  category: string
  message: string
  severity_level: string
}

interface DeploymentReport {
  summary?: string
  eval_summary?: {
    pass_rates?: Record<string, number>
    failing_scenarios?: number
  }
  red_team_summary?: {
    deployment_blocked?: boolean
    critical_count?: number
    high_count?: number
  }
  corpus_stats?: {
    document_count?: number
    chunk_count?: number
  }
}

interface ChecklistRun {
  id: string
  agent_id: string
  status: 'running' | 'complete' | 'failed' | string
  recommendation: 'ship' | 'ship_with_warnings' | 'block' | null
  report: DeploymentReport | null
  warnings: ChecklistWarning[]
  warning_acknowledgments: Record<string, string>
  all_warnings_acknowledged: boolean
  approved_at: string | null
  created_at: string
}

interface WidgetConfig {
  appearance: 'floating-button' | 'floating-mini-modal' | 'slide-out-panel'
  launcher_shape: 'circle' | 'square'
  colors: Record<string, string>
  typography: {
    font_family: string
    font_custom_url: string | null
    border_radius_preset: string
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

const WIDGET_CDN_BASE = process.env.NEXT_PUBLIC_WCHATS_WIDGET_CDN || 'https://widget.wchats.app'
const WIDGET_API_BASE = process.env.NEXT_PUBLIC_WCHATS_API_BASE || ''

function EMBED_SNIPPET(id: string): string {
  return (
    '<script src="' + WIDGET_CDN_BASE + '/widget.js"' +
    ' data-agent="' + id + '"' +
    ' data-api="' + WIDGET_API_BASE + '"' +
    ' async></script>'
  )
}

// The three real appearance modes (WidgetConfigUpdate.appearance, non-regression).
const APPEARANCE_OPTIONS: {
  key: WidgetConfig['appearance']
  label: string
  hint: string
  Icon: () => React.JSX.Element
}[] = [
  {
    key: 'floating-button',
    label: 'Floating button',
    hint: 'A circular launcher fixed to the corner of your page',
    Icon: FloatIcon,
  },
  {
    key: 'floating-mini-modal',
    label: 'Floating mini-modal',
    hint: 'A compact card near the launcher, expands on click',
    Icon: ModalIcon,
  },
  {
    key: 'slide-out-panel',
    label: 'Slide-out panel',
    hint: 'Full-height panel slides in from the right page edge',
    Icon: PanelIcon,
  },
]

const PREVIEW_CAPTIONS: Record<WidgetConfig['appearance'], string> = {
  'floating-button': 'The customer sees a floating bubble in the bottom corner of your site.',
  'floating-mini-modal': 'A compact card opens near the launcher and expands into the full conversation.',
  'slide-out-panel': 'The widget slides in from the right edge and holds the full height of the screen.',
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toISOString().slice(0, 16).replace('T', ' ')
}

function avgPassRate(rates: Record<string, number> | undefined): number | null {
  if (!rates) return null
  const values = Object.values(rates)
  if (values.length === 0) return null
  return values.reduce((a, b) => a + b, 0) / values.length
}

function buildConsequence(run: ChecklistRun | null): string {
  if (!run) return 'Run the checklist to see whether this agent is ready for a customer.'
  if (run.status === 'running') return "Checking your agent's readiness…"
  if (run.status === 'failed') return 'The last checklist run failed. Try again below.'
  if (run.recommendation === 'block') {
    return run.report?.summary || 'The gate is shut. This agent cannot reach a customer.'
  }
  if (run.recommendation === 'ship_with_warnings') {
    return run.report?.summary || 'Ready to deploy with warnings. Acknowledge each one before approving.'
  }
  return run.report?.summary || 'Every signal holds. Approving puts this agent in front of every customer.'
}

// ---------------------------------------------------------------------------
// Appearance tile icons — bespoke 30x30 stroke SVGs, ported from
// prototypes/gotham/deploy.html's own page-local `.tile svg` markup (not in
// the shared icons.tsx set — that file only carries rail + cross-page
// utility glyphs; deploy.html defines these three page-locally too).
// ---------------------------------------------------------------------------

function FloatIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.4" y="2.4" width="25.2" height="25.2" rx="2.6" />
      <circle cx="21.6" cy="21.6" r="3.6" fill="currentColor" fillOpacity={0.18} />
    </svg>
  )
}

function PanelIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.4" y="2.4" width="25.2" height="25.2" rx="2.6" />
      <path d="M19.4 2.4v25.2" />
      <path d="M22.2 9.6h2.6" />
      <path d="M22.2 13.2h2.6" />
    </svg>
  )
}

function ModalIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.4" y="2.4" width="25.2" height="25.2" rx="2.6" />
      <rect x="7" y="10.4" width="16" height="9.2" rx="1.6" fill="currentColor" fillOpacity={0.18} />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function AppearanceTile({
  option,
  selected,
  onSelect,
}: {
  option: (typeof APPEARANCE_OPTIONS)[number]
  selected: boolean
  onSelect: () => void
}) {
  const Icon = option.Icon
  return (
    <Zone as="label" live={selected} className="tile">
      <input type="radio" name="appearance" value={option.key} checked={selected} onChange={onSelect} />
      <Icon />
      <span className="name">{option.label}</span>
      <span className="note">{option.hint}</span>
    </Zone>
  )
}

// The customer's side of the glass — the one friendly, light-mode surface in
// the whole console (UI-SPEC S4 widget exception). Decorative: it renders
// the SELECTED mode's layout/caption, but never the operator's own dark
// tokens, and never repaints on `data-gate="blocked"`.
function WidgetPreview({ mode }: { mode: WidgetConfig['appearance'] }) {
  const stageMode = mode === 'slide-out-panel' ? 'panel' : mode === 'floating-mini-modal' ? 'modal' : 'float'
  return (
    <aside className="preview" aria-labelledby="preview-label">
      <span className="label" id="preview-label">Preview</span>
      <div className="stage" data-mode={stageMode}>
        <div className="widget" aria-hidden="true">
          <div className="w-head">
            <span className="w-avatar">W</span>
            <span>
              <span className="w-name">Your agent</span>
              <span className="w-state">Usually replies in a few seconds</span>
            </span>
          </div>
          <div className="w-body">
            <p className="w-msg w-agent">Hi — ask me anything about the business.</p>
            <p className="w-msg w-user">What are your opening hours?</p>
          </div>
          <div className="w-input">
            <span>Ask a question</span>
            <span className="w-send">Send</span>
          </div>
        </div>
        {stageMode === 'float' && (
          <div className="launcher" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
              <path d="M17 9.6a6.6 6.6 0 0 1-7.1 6.6L4 17.5l1.4-4.6A6.6 6.6 0 1 1 17 9.6z" />
            </svg>
          </div>
        )}
      </div>
      <p className="caption">{PREVIEW_CAPTIONS[mode]}</p>
    </aside>
  )
}

// ---------------------------------------------------------------------------
// DeployPage
// ---------------------------------------------------------------------------

export default function DeployPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const queryClient = useQueryClient()
  const { setGate } = useGate()

  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied'>('idle')
  const [widgetConfig, setWidgetConfig] = useState<WidgetConfig>(DEFAULT_CONFIG)

  // ---- Agent (name, is_deployed, soul fields for the "Soul" gate row) ----
  const agentQuery = useQuery({
    queryKey: ['agent', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated. Please sign in.')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<AgentDetail>
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 15_000,
  })
  const agent = agentQuery.data ?? null

  // ---- Checklist runs (list, newest-first) — PRESERVED, GET checklist-runs.
  // Also the polling mechanism: refetches every 3s while the newest row is
  // still 'running' (see Rule-1 bug-fix note in the file header). ---------
  const checklistListQuery = useQuery({
    queryKey: ['checklist-runs', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/checklist-runs`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      return (data.runs ?? []) as ChecklistRun[]
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 10_000,
    refetchInterval: (query) => (query.state.data?.[0]?.status === 'running' ? 3000 : false),
  })
  const latestRun = checklistListQuery.data?.[0] ?? null

  // ---- Widget config — PRESERVED, GET/POST widget-config. ---------------
  const widgetConfigQuery = useQuery({
    queryKey: ['widget-config', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/widget-config`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<Partial<WidgetConfig>>
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 30_000,
  })

  useEffect(() => {
    const data = widgetConfigQuery.data
    if (data && Object.keys(data).length > 0) {
      setWidgetConfig((prev) => ({
        ...prev,
        ...data,
        colors: { ...prev.colors, ...(data.colors ?? {}) },
        typography: { ...prev.typography, ...(data.typography ?? {}) },
      }))
    }
  }, [widgetConfigQuery.data])

  const saveWidgetConfig = useMutation({
    mutationFn: async (next: WidgetConfig) => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      // Always POSTs the FULL merged config — WidgetConfigUpdate.colors/typography
      // default-fill server-side, so a partial body would silently reset any
      // previously-saved colors/typography (Rule 1 data-loss guard).
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/widget-config`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
  })

  const handleSelectAppearance = (appearance: WidgetConfig['appearance']) => {
    const next = { ...widgetConfig, appearance }
    setWidgetConfig(next)
    saveWidgetConfig.mutate(next)
  }

  // ---- Checklist actions — PRESERVED endpoints. --------------------------
  const triggerChecklist = useMutation({
    mutationFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/checklist-runs`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.status !== 202) throw new Error(`Failed to start checklist run (HTTP ${res.status}).`)
      return res.json()
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['checklist-runs', id] }),
  })

  const acknowledgeWarning = useMutation({
    mutationFn: async (warningId: string) => {
      if (!latestRun) throw new Error('No checklist run to acknowledge against')
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(
        `${apiBase}/api/v1/agents/${id}/checklist-runs/${latestRun.id}/acknowledge`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ warning_ids: [warningId] }),
        },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['checklist-runs', id] }),
  })

  const approveDeployment = useMutation({
    mutationFn: async () => {
      if (!latestRun) throw new Error('No checklist run to approve')
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/approve-deployment`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ checklist_run_id: latestRun.id }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error((body as { detail?: string }).detail ?? `Approval failed (HTTP ${res.status})`)
      }
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent', id] })
      queryClient.invalidateQueries({ queryKey: ['checklist-runs', id] })
    },
  })

  // ---- Embed copy — Clipboard API with an execCommand fallback for
  // insecure/file:// contexts (UI-SPEC S6.8). --------------------------
  const handleCopyEmbed = () => {
    const text = EMBED_SNIPPET(id)
    const flip = () => {
      setCopyStatus('copied')
      setTimeout(() => setCopyStatus('idle'), 1500)
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(flip, () => fallbackCopy(text, flip))
    } else {
      fallbackCopy(text, flip)
    }
  }
  const fallbackCopy = (text: string, onDone: () => void) => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      onDone()
    } catch {
      /* Select-and-copy failed; the snippet text is still visible to copy by hand. */
    }
    document.body.removeChild(ta)
  }

  // ---- The gate — derived entirely from the real latest checklist run
  // (mirrors the gatebar pattern in agents/[id]/page.tsx). No page-local
  // toggle ever exists here (must-fix 4). --------------------------------
  const report = latestRun?.status === 'complete' ? latestRun.report : null
  const evalAvg = avgPassRate(report?.eval_summary?.pass_rates)
  const failingScenarios = report?.eval_summary?.failing_scenarios ?? 0
  const criticalFindings = report?.red_team_summary?.critical_count ?? 0
  const highFindings = report?.red_team_summary?.high_count ?? 0
  const redTeamBlockedSignal = report?.red_team_summary?.deployment_blocked === true
  const docCount = report?.corpus_stats?.document_count ?? 0
  const chunkCount = report?.corpus_stats?.chunk_count ?? 0
  const soulConfigured = !!(agent?.soul_role && agent?.soul_voice)

  const checklistBlocked = latestRun?.status === 'complete' && latestRun.recommendation === 'block'
  const gateBlocked = checklistBlocked || redTeamBlockedSignal

  useEffect(() => {
    setGate(gateBlocked ? 'blocked' : 'open')
  }, [gateBlocked, setGate])

  const isApprovable =
    !!latestRun &&
    latestRun.status === 'complete' &&
    (latestRun.recommendation === 'ship' ||
      (latestRun.recommendation === 'ship_with_warnings' && latestRun.all_warnings_acknowledged))

  const loadError = useMemo(() => {
    const errs = [agentQuery.error, checklistListQuery.error, widgetConfigQuery.error]
    const first = errs.find((e): e is Error => e instanceof Error)
    return first?.message ?? null
  }, [agentQuery.error, checklistListQuery.error, widgetConfigQuery.error])

  const actionError = useMemo(() => {
    const errs = [triggerChecklist.error, acknowledgeWarning.error, approveDeployment.error]
    const first = errs.find((e): e is Error => e instanceof Error)
    return first?.message ?? null
  }, [triggerChecklist.error, acknowledgeWarning.error, approveDeployment.error])

  const consequence = buildConsequence(latestRun)
  const gateStamp = latestRun && latestRun.status === 'complete' ? latestRun.created_at : null

  return (
    <div className="page">
      <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />

      <header className="page-head">
        <div className="row">
          <div>
            <h1>Deploy</h1>
            <p className="sub">
              Four signals stand between this agent and a paying customer. The gate opens only
              while all four hold, and it shuts itself the moment one does not.
            </p>
          </div>
        </div>
      </header>

      {loadError && (
        <div role="alert" className="alert">{loadError}</div>
      )}
      {actionError && (
        <div role="alert" className="alert">{actionError}</div>
      )}

      <div className="bench">
        <div>
          {/* ═══ THE GATE ══════════════════════════════════════════════ */}
          <section aria-labelledby="gate-label">
            <div className="section-head">
              <h2 className="label" id="gate-label">The gate</h2>
              <Chip verdict={gateBlocked ? 'seal' : 'pass'}>{gateBlocked ? 'Shut' : 'Open'}</Chip>
            </div>

            {!latestRun && (
              <div className="gate-idle">
                <p className="voice">Run the checklist to see whether this agent is ready for a customer.</p>
                <Btn onClick={() => triggerChecklist.mutate()} disabled={triggerChecklist.isPending || agent?.status !== 'ready'}>
                  {triggerChecklist.isPending ? 'Starting…' : 'Run pre-deployment checklist'}
                </Btn>
                {agent && agent.status !== 'ready' && (
                  <p className="foot-note">The agent must be ready before the checklist can run.</p>
                )}
              </div>
            )}

            {latestRun?.status === 'running' && (
              <div className="gate-idle">
                <span className="spinner" aria-hidden="true" />
                <p className="voice">Checking your agent&apos;s readiness…</p>
                <p className="foot-note" role="status" aria-live="polite">
                  This usually takes 1–2 minutes. Results appear here automatically.
                </p>
              </div>
            )}

            {latestRun?.status === 'failed' && (
              <div className="gate-idle">
                <p className="voice">The last checklist run failed.</p>
                <Btn onClick={() => triggerChecklist.mutate()} disabled={triggerChecklist.isPending}>
                  {triggerChecklist.isPending ? 'Starting…' : 'Retry checklist'}
                </Btn>
              </div>
            )}

            {latestRun?.status === 'complete' && (
              <>
                <Ledger caption="The four signals a deployment gate checks before an agent can reach a customer.">
                  <thead>
                    <tr>
                      <LedgerColHead className="sig">Signal</LedgerColHead>
                      <LedgerColHead>Value</LedgerColHead>
                      <LedgerColHead>Verdict</LedgerColHead>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <LedgerRowHead className="sig">
                        Evals pass rate
                        <span className="why">Ragas-scored scenarios, judged for faithfulness &amp; relevancy</span>
                      </LedgerRowHead>
                      <td className="val mono">
                        {evalAvg !== null
                          ? `${evalAvg.toFixed(2)} avg${failingScenarios > 0 ? ` · ${failingScenarios} failing` : ''}`
                          : 'not yet run'}
                      </td>
                      <td>
                        <Chip verdict={evalAvg === null ? 'mute' : failingScenarios > 0 ? 'fail' : 'pass'}>
                          {evalAvg === null ? 'No data' : failingScenarios > 0 ? 'Fail' : 'Pass'}
                        </Chip>
                      </td>
                    </tr>
                    <tr>
                      <LedgerRowHead className="sig">
                        Red team
                        <span className="why">Adversarial probes for jailbreaks &amp; data leakage</span>
                      </LedgerRowHead>
                      <td className="val mono">
                        {report?.red_team_summary
                          ? `${criticalFindings} critical · ${highFindings} high`
                          : 'not yet run'}
                      </td>
                      <td>
                        <Chip verdict={!report?.red_team_summary ? 'mute' : redTeamBlockedSignal ? 'fail' : 'pass'}>
                          {!report?.red_team_summary ? 'No data' : redTeamBlockedSignal ? 'Fail' : 'Pass'}
                        </Chip>
                      </td>
                    </tr>
                    <tr>
                      <LedgerRowHead className="sig">
                        Knowledge base
                        <span className="why">Everything the agent is allowed to cite</span>
                      </LedgerRowHead>
                      <td className="val mono">
                        {report?.corpus_stats
                          ? `${docCount} document${docCount === 1 ? '' : 's'} · ${chunkCount} chunk${chunkCount === 1 ? '' : 's'}`
                          : 'not yet run'}
                      </td>
                      <td>
                        <Chip verdict={!report?.corpus_stats ? 'mute' : docCount > 0 ? 'pass' : 'fail'}>
                          {!report?.corpus_stats ? 'No data' : docCount > 0 ? 'Pass' : 'Fail'}
                        </Chip>
                      </td>
                    </tr>
                    <tr>
                      <LedgerRowHead className="sig">
                        Soul
                        <span className="why">Tone, refusals and escalation rules</span>
                      </LedgerRowHead>
                      <td className="val mono">
                        {agentQuery.isPending ? 'checking…' : soulConfigured ? 'role & voice set' : 'not yet configured'}
                      </td>
                      <td>
                        <Chip verdict={agentQuery.isPending ? 'mute' : soulConfigured ? 'pass' : 'fail'}>
                          {agentQuery.isPending ? 'No data' : soulConfigured ? 'Pass' : 'Fail'}
                        </Chip>
                      </td>
                    </tr>
                  </tbody>
                </Ledger>

                {latestRun.recommendation === 'ship_with_warnings' && latestRun.warnings.length > 0 && (
                  <div className="warnings">
                    <p className="label">Acknowledge each warning to proceed</p>
                    {latestRun.warnings.map((w) => (
                      <label key={w.warning_id} className="warning-row">
                        <input
                          type="checkbox"
                          checked={!!latestRun.warning_acknowledgments[w.warning_id]}
                          disabled={acknowledgeWarning.isPending || !!latestRun.warning_acknowledgments[w.warning_id]}
                          onChange={(e) => {
                            if (e.target.checked) acknowledgeWarning.mutate(w.warning_id)
                          }}
                        />
                        <Chip verdict="fail" className="warning-cat">{w.category.replace(/_/g, ' ')}</Chip>
                        <span>{w.message}</span>
                      </label>
                    ))}
                  </div>
                )}

                <div className="verdict-bar">
                  <Btn
                    disabled={!isApprovable || approveDeployment.isPending}
                    aria-describedby="consequence"
                    onClick={() => approveDeployment.mutate()}
                  >
                    {approveDeployment.isPending
                      ? 'Approving…'
                      : agent?.is_deployed
                        ? 'Re-approve deploy'
                        : 'Approve deploy'}
                  </Btn>
                  {agent?.is_deployed && <Chip verdict="pass" dot>Live</Chip>}
                  <span className="mono stamp">agent {id}</span>
                </div>
                <p className="voice consequence" id="consequence">{consequence}</p>
                <p className="vh" role="status" aria-live="polite">
                  {gateBlocked
                    ? 'The gate is shut. A blocking finding is open and no new build reaches a customer.'
                    : 'The gate is open. Approving puts this agent in front of every customer.'}
                </p>

                <div className="rig">
                  <Btn variant="ghost" onClick={() => triggerChecklist.mutate()} disabled={triggerChecklist.isPending}>
                    {triggerChecklist.isPending ? 'Starting…' : 'Run checklist again'}
                  </Btn>
                  {gateStamp && <span className="mono stamp">last verified {formatDateTime(gateStamp)}</span>}
                </div>
              </>
            )}
          </section>

          {/* ═══ EMBED ══════════════════════════════════════════════════ */}
          <section className="section">
            <div className="section-head">
              <h2 className="label" id="embed-label">Embed</h2>
              <span className="mono stamp">one tag, before the closing body</span>
            </div>
            <div className="embed-row">
              <code className="well" id="snippet">{EMBED_SNIPPET(id)}</code>
              <Btn variant="ghost" onClick={handleCopyEmbed} aria-describedby="embed-label">
                {copyStatus === 'copied' ? 'Copied' : 'Copy'}
              </Btn>
            </div>
            <p className="help">
              The widget loads on its own, after your page has painted. It adds nothing to your
              first render.
            </p>
          </section>

          {/* ═══ APPEARANCE ═════════════════════════════════════════════ */}
          <section className="section">
            <div className="section-head">
              <h2 className="label" id="appearance-label">Appearance</h2>
              {saveWidgetConfig.isPending && <span className="mono stamp">saving…</span>}
              {saveWidgetConfig.isSuccess && !saveWidgetConfig.isPending && <span className="mono stamp">saved</span>}
            </div>
            <fieldset className="tiles-fieldset">
              <legend className="vh">Where the widget sits on your page</legend>
              <div className="tiles">
                {APPEARANCE_OPTIONS.map((opt) => (
                  <AppearanceTile
                    key={opt.key}
                    option={opt}
                    selected={widgetConfig.appearance === opt.key}
                    onSelect={() => handleSelectAppearance(opt.key)}
                  />
                ))}
              </div>
            </fieldset>
          </section>
        </div>

        <WidgetPreview mode={widgetConfig.appearance} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page-scoped CSS — ported from prototypes/gotham/deploy.html's own <style>
// block (`.bench`/`.gate`/`.rig`/`.embed-row`/`.tiles`/`.preview`/`.widget`
// family), same static dangerouslySetInnerHTML pattern used by eval/agent
// page.tsx. `--widget-accent` is scoped to `.preview`/`.stage` only — never
// `:root` — so it can never leak into console chrome and never resolves off
// `--live`/`--seal` (UI-SPEC S4: the gate must never repaint what the
// customer sees).
// ---------------------------------------------------------------------------
const PAGE_CSS = `
  .alert {
    padding: 12px 16px; margin-bottom: 20px;
    background: var(--fail-dim);
    border: 1px solid color-mix(in oklch, var(--fail) 32%, transparent);
    border-radius: var(--r-panel);
    font-size: 14px; color: var(--fail);
  }

  .bench { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 44px; align-items: start; }
  @media (max-width: 1100px) { .bench { grid-template-columns: minmax(0, 1fr); } .preview { display: none; } }

  .gate-idle {
    border: 2px dashed var(--hairline-strong);
    border-radius: var(--r-panel);
    padding: 40px 32px;
    text-align: center;
    display: flex; flex-direction: column; align-items: center; gap: 14px;
  }
  .gate-idle .voice { font-size: 15px; max-width: 48ch; }

  .spinner {
    display: inline-block; width: 26px; height: 26px;
    border: 3px solid var(--hairline); border-top-color: var(--live);
    border-radius: 50%; animation: gate-spin 1s linear infinite;
  }
  @keyframes gate-spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 1ms; } }

  .ledger th.sig, .ledger td.sig { width: 42%; font-weight: 500; }
  .ledger td .why { display: block; margin-top: 2px; font-size: 12px; color: var(--ink-3); font-weight: 400; }
  .ledger td.val { color: var(--ink-2); }

  .warnings { margin-top: 20px; }
  .warning-row {
    display: flex; align-items: flex-start; gap: 12px;
    padding: 10px 0; border-bottom: 1px solid var(--hairline-soft);
    text-transform: none; font-family: var(--sans); letter-spacing: normal;
    color: var(--ink); cursor: pointer;
  }
  .warning-row input { width: 16px; height: 16px; flex: none; margin-top: 2px; accent-color: var(--live); }
  .warning-cat { flex: none; }
  .warning-row span:last-child { font-size: 13.5px; flex: 1; }

  .stamp { color: var(--ink-3); font-size: 12px; }

  .verdict-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-top: 24px; }
  .consequence { margin-top: 16px; font-size: 16px; max-width: 62ch; }

  .rig { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-top: 26px; padding-top: 20px; border-top: 1px solid var(--hairline-soft); }

  .foot-note { font-size: 11.5px; color: var(--ink-3); }

  .embed-row { display: flex; align-items: flex-start; gap: 12px; }
  .embed-row .well { flex: 1; min-width: 0; white-space: pre-wrap; word-break: break-all; }
  .embed-row .btn { flex: none; }
  @media (max-width: 640px) { .embed-row { flex-direction: column; } .embed-row .btn { width: 100%; justify-content: center; } }

  .tiles-fieldset { border: 0; padding: 0; margin: 0; }
  .tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
  @media (max-width: 700px) { .tiles { grid-template-columns: 1fr; } }

  .tile {
    position: relative; padding: 16px; cursor: pointer;
    font-family: var(--sans); text-transform: none; letter-spacing: 0; color: var(--ink);
  }
  .tile:hover { border-color: var(--hairline-strong); }
  .tile:has(input:focus-visible) { outline: 2px solid var(--live); outline-offset: 2px; }
  .tile input { position: absolute; width: 1px; height: 1px; opacity: 0; clip-path: inset(50%); margin: 0; padding: 0; }
  .tile svg { display: block; color: var(--ink-3); margin-bottom: 12px; }
  .tile[data-live="true"] svg { color: var(--live); }
  .tile .name { display: block; font-size: 13.5px; font-weight: 600; }
  .tile .note { display: block; margin-top: 3px; font-size: 12px; color: var(--ink-3); }

  /* ── the widget preview: the widget-exception light palette (UI-SPEC S4).
       --widget-accent is scoped here only — it must never leak into the
       console and never tracks --live/--seal/data-gate. ─────────────────── */
  .preview { position: sticky; top: 34px; --widget-accent: #C79A3C; }
  .preview .label { display: block; margin-bottom: 12px; }
  .stage {
    background: var(--well); border: 1px solid var(--hairline-soft); border-radius: var(--r-panel);
    padding: 18px; display: flex; flex-direction: column; align-items: flex-end; gap: 12px;
  }
  .widget {
    width: 100%;
    background: rgba(255, 255, 255, 0.7);
    border-radius: 12px; overflow: hidden;
    font-family: var(--sans); color: #12181A;
  }
  .w-head { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid #ECEAE4; }
  .w-avatar {
    width: 30px; height: 30px; flex: none; border-radius: 50%;
    background: var(--widget-accent); color: #0A1416;
    display: grid; place-items: center; font-family: var(--display); font-size: 13px; font-weight: 600;
  }
  .w-name { display: block; font-size: 13px; font-weight: 600; line-height: 1.3; }
  .w-state { display: block; font-size: 11px; color: #7C8687; }
  .w-body { padding: 14px; display: flex; flex-direction: column; gap: 9px; }
  .w-msg { max-width: 88%; padding: 9px 12px; font-size: 12.5px; line-height: 1.5; border-radius: 12px; }
  .w-agent { align-self: flex-start; background: #F3F1EB; border-bottom-left-radius: 4px; }
  .w-user { align-self: flex-end; background: #12181A; color: #F6F5F1; border-bottom-right-radius: 4px; }
  .w-input {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    margin: 0 14px 14px; padding: 9px 12px; border: 1px solid #E4E1D9; border-radius: 100px;
    font-size: 12.5px; color: #9AA3A3;
  }
  .w-send { color: var(--widget-accent); font-weight: 600; }
  .launcher {
    width: 46px; height: 46px; flex: none; border-radius: 50%;
    background: var(--widget-accent); color: #0A1416; display: grid; place-items: center;
  }

  .stage[data-mode="panel"] .widget { border-radius: 12px 0 0 12px; }
  .stage[data-mode="panel"] .w-body { min-height: 190px; }
  .stage[data-mode="modal"] .w-body { min-height: 120px; }

  .caption { margin-top: 12px; font-size: 12px; color: var(--ink-3); }
`
