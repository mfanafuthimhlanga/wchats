'use client'
import { use, useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Btn from '../../components/gotham/Btn'
import Chip from '../../components/gotham/Chip'
import EmptyState from '../../components/gotham/EmptyState'
import Ledger, { LedgerCell, LedgerColHead, LedgerRowHead } from '../../components/gotham/Ledger'
import { useGate } from '../../components/gotham/GateProvider'
import { AlertsBanner, type Alert } from './components/AlertsBanner'
import LivePanel from './components/LivePanel'
import RetrievalHealthPanel from './components/RetrievalHealthPanel'

/**
 * The agent operations room — `/agents/[id]` (UI-SPEC S6.4, UI2-05, ported
 * from prototypes/gotham/agent.html). Six `.section` regions in a fixed
 * order: Live, Retrieval health, The bench, Judgement, Adversary, The
 * prompt.
 *
 * Live and Retrieval health call their Phase 21 endpoints via
 * `LivePanel`/`RetrievalHealthPanel` (WIRE-01, 23-05) — never the
 * prototype's client-side seeded-noise demo data (hardcoded channel/version
 * arrays). Judgement and Adversary wire to the real eval-runs /
 * red-team-runs endpoints; the gatebar derives from real checklist-runs +
 * red-team `deployment_blocked` + a folded `red_team_critical` alert (OQ2)
 * — never a page-local toggle (UI-SPEC S8.6, Pitfall 5). The bench and The
 * prompt remain unwired as of this plan and render an honest
 * `<EmptyState>`; 23-08 and 23-07 wire them respectively, and 23-06
 * recomputes the Adversary gate/severity inputs from live data.
 */

interface AgentDetail {
  id: string
  tenant_id: string
  name: string
  role: string
  status: 'pending' | 'provisioning' | 'ready' | 'error' | string
  neon_project_id: string | null
  schema_version: string | null
  soul_role?: string | null
  soul_voice?: string | null
  soul_do_list?: string[] | null
  created_at: string
}

// Minimal document shape — only what the region head-count needs.
interface AgentDocument {
  id: string
  parse_status: string
}

// GET /api/v1/agents/{id}/eval-runs — response shape from evals.py list_eval_runs.
interface EvalRunSummary {
  id: string
  started_at: string | null
  finished_at: string | null
  status: string
  scenario_count: number
  aggregate_scores: {
    faithfulness: number
    answer_relevancy: number
    context_precision: number
    context_recall: number
  }
}

// OPS-12 ORRERY ledger (evals.py:97-105,182-189) — a SIBLING of eval_runs on
// the same response, not a per-run field. Always present on a successful
// response; a suite with zero production-born or zero authored scenarios
// reports a real 0, never a sentinel (WIRE-02).
interface EvalLedger {
  born_in_production_count: number
  red_team_count: number
  authored_count: number
}

interface EvalRunsResponse {
  eval_runs: EvalRunSummary[]
  ledger: EvalLedger
}

// GET /api/v1/agents/{id}/eval-runs/{runId}/results — per-scenario results.
interface EvalScenarioResult {
  scenario_id: string
  question: string
  source: string
  scores: {
    faithfulness: number
    answer_relevancy: number
    context_precision: number
    context_recall: number
  }
  passed: boolean
}

// GET /api/v1/agents/{id}/red-team-runs — response shape from red_team.py.
// findings is the raw JSONB list; each entry matches RedTeamFinding
// (severity/description/attack_vector/probe_message/agent_response/turn_count)
// in app/services/red_team_service.py.
interface RedTeamFinding {
  severity: 'low' | 'medium' | 'high' | 'critical'
  description: string
  attack_vector: string
  probe_message: string
  agent_response: string
  turn_count: number
}

interface RedTeamRun {
  id: string
  kind: string
  status: string
  started_at: string | null
  finished_at: string | null
  findings: RedTeamFinding[]
  max_severity: string | null
  deployment_blocked: boolean
}

// GET /api/v1/agents/{id}/checklist-runs — response shape from deployment.py.
interface ChecklistRun {
  id: string
  agent_id: string
  status: string
  recommendation: 'ship' | 'ship_with_warnings' | 'block' | null
  created_at: string
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toISOString().slice(0, 10)
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toISOString().slice(0, 16).replace('T', ' ')
}

// Maps the coarse `source` enum (D-13/D-16 LOCKED) to the Origin column copy.
// No trace-id linkage is returned by the results endpoint (fine-grained
// provenance is Phase 21 OPS-12) — this never invents one.
function originLabel(source: string): string {
  if (source === 'generated') return 'authored'
  if (source === 'mined') return 'mined from production'
  if (source === 'production_failure') return 'production failure'
  return source
}

export default function AgentOperationsRoom({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const queryClient = useQueryClient()
  const { setGate } = useGate()
  const [alerts, setAlerts] = useState<Alert[]>([])

  // The single error path every operations-room region reports into,
  // keyed by region id. Live and Retrieval health are the first two
  // consumers (23-05); the Adversary, prompt and bench regions wire into
  // this same callback in their own later plans (23-06/07/08), each owning
  // its own wiring rather than assuming it. One stable identity (empty dep
  // array, functional updater) so a child effect that lists this callback
  // in its own deps never re-fires on an unrelated parent render.
  const [regionErrors, setRegionErrors] = useState<Record<string, string>>({})
  const setRegionError = useCallback((region: string, message: string | null) => {
    setRegionErrors((prev) => {
      if (message === null) {
        if (!(region in prev)) return prev
        const next = { ...prev }
        delete next[region]
        return next
      }
      if (prev[region] === message) return prev
      return { ...prev, [region]: message }
    })
  }, [])

  // ---- Agent + documents — preserved verbatim from the prior dusk build --
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
    // Poll every 3s while provisioning; stop once ready.
    refetchInterval: (query) => {
      const d = query.state.data
      if (!d) return false
      const done =
        d.status === 'ready' ||
        d.status === 'provisioning_complete' ||
        d.neon_project_id !== null
      return done ? false : 3000
    },
    staleTime: 0,
  })

  const agent = agentQuery.data ?? null
  const loadError = agentQuery.isError
    ? (agentQuery.error as Error).message || 'Failed to load agent. Please refresh.'
    : null

  // The page's one error surface — the agent-load failure plus every
  // region's own reported failure, folded together rather than each region
  // owning a second banner (T-23-UI-06).
  const allErrors = [loadError, ...Object.values(regionErrors)].filter((m): m is string => !!m)

  const step1Done =
    !!agent &&
    (agent.status === 'ready' ||
      agent.status === 'provisioning_complete' ||
      agent.neon_project_id !== null)

  const docsQuery = useQuery({
    queryKey: ['agent-documents', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/documents`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      return (data.documents ?? []) as AgentDocument[]
    },
    enabled: isLoaded && !!isSignedIn && step1Done,
    staleTime: 10_000,
  })
  const documents = docsQuery.data ?? []

  // ---- Judgement: real eval-runs data (UI-SPEC S6.4 region 4) ------------
  const evalRunsQuery = useQuery({
    queryKey: ['eval-runs', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/eval-runs`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return (await r.json()) as EvalRunsResponse
    },
    enabled: isLoaded && !!isSignedIn && step1Done,
    staleTime: 15_000,
  })
  const evalRuns = evalRunsQuery.data?.eval_runs ?? []
  const ledger = evalRunsQuery.data?.ledger ?? null
  const latestEvalRun = evalRuns[0] ?? null

  const evalResultsQuery = useQuery({
    queryKey: ['eval-run-results', id, latestEvalRun?.id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/eval-runs/${latestEvalRun!.id}/results`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      return (data.results ?? []) as EvalScenarioResult[]
    },
    enabled: isLoaded && !!isSignedIn && !!latestEvalRun?.id,
    staleTime: 15_000,
  })
  const evalResults = evalResultsQuery.data ?? []
  const heldCount = evalResults.filter((r) => r.passed).length
  const failedCount = evalResults.filter((r) => !r.passed).length

  // ---- Adversary: real red-team-runs data (UI-SPEC S6.4 region 5) -------
  const redTeamQuery = useQuery({
    queryKey: ['red-team-runs', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/red-team-runs`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      return (data.runs ?? []) as RedTeamRun[]
    },
    enabled: isLoaded && !!isSignedIn && step1Done,
    staleTime: 15_000,
  })
  const redTeamRuns = redTeamQuery.data ?? []
  const latestRedTeamRun = redTeamRuns[0] ?? null

  const severityCounts = useMemo(() => {
    const counts = { critical: 0, high: 0, medium: 0, low: 0 }
    for (const f of latestRedTeamRun?.findings ?? []) {
      if (f.severity in counts) counts[f.severity] += 1
    }
    return counts
  }, [latestRedTeamRun])

  const criticalFinding =
    (latestRedTeamRun?.findings ?? []).find((f) => f.severity === 'critical') ?? null

  const runRedTeam = useMutation({
    mutationFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/red-team-runs`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['red-team-runs', id] })
    },
  })

  // ---- Gatebar: real checklist-runs + red-team deployment_blocked -------
  const checklistQuery = useQuery({
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
    enabled: isLoaded && !!isSignedIn && step1Done,
    staleTime: 15_000,
  })
  const latestChecklistRun = (checklistQuery.data ?? [])[0] ?? null

  const hasUnresolvedCriticalAlert = alerts.some(
    (a) => a.alert_type === 'red_team_critical' && !a.resolved_at
  )
  const unresolvedEvalRegressionAlerts = alerts.filter(
    (a) => a.alert_type === 'eval_regression' && !a.resolved_at
  )

  const redTeamBlocked = latestRedTeamRun?.deployment_blocked === true
  const checklistBlocked = latestChecklistRun?.recommendation === 'block'
  const gateBlocked = redTeamBlocked || checklistBlocked || hasUnresolvedCriticalAlert

  // This is the single authoritative computation of the real deploy-gate
  // state on this page (checklist recommendation + red-team
  // deployment_blocked + the folded red_team_critical alert, UI-SPEC S8.6 /
  // OQ2) — the one effect that ever calls setGate('open'). AlertsBanner may
  // independently call setGate('blocked') the instant it sees a critical
  // alert (defense in depth, same signal folded in below), but never
  // 'open', so the two writers can never fight over reopening the gate.
  useEffect(() => {
    setGate(gateBlocked ? 'blocked' : 'open')
  }, [gateBlocked, setGate])

  const gateStamp = latestChecklistRun?.created_at ?? latestRedTeamRun?.finished_at ?? null
  const gateMessage = gateBlocked
    ? criticalFinding
      ? criticalFinding.description
      : 'A blocking signal is open. Nothing new reaches a customer until it clears.'
    : 'Every build ships. No critical finding is open.'

  return (
    <div className="page">
      <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />

      <header className="page-head">
        <div className="row">
          <div>
            <h1>{agent?.name ?? 'Loading agent…'}</h1>
            <p className="sub">
              {agent?.role || 'Agent'}
              {agent ? ` · Serving since ${formatDate(agent.created_at)}` : ''}
            </p>
          </div>
          <div className="ident">
            <p className="label">Agent</p>
            <p className="mono ident-id">{agent?.id ?? id}</p>
            <Chip verdict={agent?.status === 'ready' ? 'live' : 'mute'} dot>
              {agent?.status === 'ready' ? 'Serving' : agent ? agent.status : 'Loading'}
            </Chip>
          </div>
        </div>

        {/* Real gatebar — derives entirely from checklist-runs + red-team
            deployment_blocked + the folded red_team_critical alert (UI-SPEC
            S8.6, Pitfall 5). No component hand-colours itself here; setGate
            is called once above and the token cascade repaints the room. */}
        <div className="gatebar rule-double">
          <Chip verdict={gateBlocked ? 'seal' : 'pass'}>{gateBlocked ? 'Gate shut' : 'Gate open'}</Chip>
          <p>{gateMessage}</p>
          <p className="mono" style={{ marginLeft: 'auto' }}>
            {gateStamp ? `last verified ${formatDateTime(gateStamp)}` : 'not yet verified'}
          </p>
        </div>
        <p className="vh" role="status" aria-live="polite">
          {gateBlocked
            ? 'The gate is shut. A blocking finding is open and no new build reaches a customer.'
            : 'The gate is open. The agent is serving customers.'}
        </p>
      </header>

      {allErrors.length > 0 && (
        <div
          role="alert"
          style={{
            padding: '12px 16px',
            marginBottom: '20px',
            background: 'var(--fail-dim)',
            border: '1px solid color-mix(in oklch, var(--fail) 32%, transparent)',
            borderRadius: 'var(--r-panel)',
            fontSize: '14px',
            color: 'var(--fail)',
          }}
        >
          {allErrors.length === 1 ? (
            allErrors[0]
          ) : (
            <ul style={{ margin: 0, paddingLeft: '18px' }}>
              {allErrors.map((message, i) => (
                <li key={i}>{message}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {isLoaded && isSignedIn && agent && (
        <AlertsBanner agentId={id} onAlertsChange={setAlerts} />
      )}

      {/* ═══ LIVE — real metrics data (WIRE-01, 23-05) ═════════════════ */}
      <section className="section" aria-labelledby="live-h">
        <div className="section-head">
          <h2 className="label" id="live-h">Live</h2>
        </div>
        <LivePanel
          agentId={id}
          enabled={isLoaded && !!isSignedIn && step1Done}
          onError={setRegionError}
        />
      </section>

      {/* ═══ RETRIEVAL HEALTH — real retrieval-health data (WIRE-01, 23-05) ═ */}
      <section className="section" aria-labelledby="rag-h">
        <div className="section-head">
          <h2 className="label" id="rag-h">Retrieval health</h2>
          <p className="mono head-count">{documents.length} documents</p>
        </div>
        <RetrievalHealthPanel
          agentId={id}
          documentCount={documents.length}
          enabled={isLoaded && !!isSignedIn && step1Done}
          onError={setRegionError}
        />
      </section>

      {/* ═══ THE BENCH ══════════════════════════════════════════════════ */}
      <section className="section" aria-labelledby="bench-h">
        <div className="section-head">
          <h2 className="label" id="bench-h">The bench</h2>
        </div>
        <EmptyState
          heading="Nothing on the bench yet"
          body="No failing production traces to review yet."
        />
      </section>

      {/* ═══ JUDGEMENT — real eval-runs data (UI-SPEC S6.4 region 4) ══════ */}
      <section className="section" aria-labelledby="judge-h">
        <div className="section-head">
          <h2 className="label" id="judge-h">Judgement</h2>
          <p className="mono head-count">
            {latestEvalRun
              ? `run ${formatDateTime(latestEvalRun.started_at)} · ${latestEvalRun.scenario_count} scenarios`
              : 'no runs yet'}
          </p>
        </div>

        {unresolvedEvalRegressionAlerts.length > 0 && (
          <Chip verdict="fail" className="judge-alert-chip">
            {unresolvedEvalRegressionAlerts.length} unresolved eval regression
            {unresolvedEvalRegressionAlerts.length > 1 ? 's' : ''}
          </Chip>
        )}

        {latestEvalRun && ledger ? (
          <>
            <div className="chans">
              <div className="chan">
                <span className="chan-name">scenarios</span>
                <div className="chan-read"><span className="num chan-val">{latestEvalRun.scenario_count}</span></div>
                <p className="chan-thr">in this run</p>
              </div>
              <div className="chan">
                <span className="chan-name">held</span>
                <div className="chan-read"><span className="num chan-val">{heldCount}</span></div>
                <p className="chan-thr">last run</p>
              </div>
              <div className="chan">
                <span className="chan-name">failed</span>
                <div className="chan-read">
                  <span className="num chan-val" style={{ color: failedCount > 0 ? 'var(--fail)' : undefined }}>
                    {failedCount}
                  </span>
                </div>
                <p className="chan-thr">last run</p>
              </div>
              <div className="chan">
                <span className="chan-name">born in production</span>
                <div className="chan-read"><span className="num chan-val">{ledger.born_in_production_count}</span></div>
                <p className="chan-thr">promoted from a trace</p>
              </div>
              <div className="chan">
                <span className="chan-name">authored</span>
                <div className="chan-read"><span className="num chan-val">{ledger.authored_count}</span></div>
                <p className="chan-thr">written by hand</p>
              </div>
            </div>

            {evalResults.length > 0 ? (
              <div className="scroll-x">
                <Ledger caption="The suite. Every scenario records where it came from.">
                  <thead>
                    <tr>
                      <LedgerColHead>Scenario</LedgerColHead>
                      <LedgerColHead>Origin</LedgerColHead>
                      <LedgerColHead>Added</LedgerColHead>
                      <LedgerColHead className="verdict">Last verdict</LedgerColHead>
                    </tr>
                  </thead>
                  <tbody>
                    {evalResults.map((res) => (
                      <tr key={res.scenario_id}>
                        <LedgerRowHead>{res.question || res.scenario_id}</LedgerRowHead>
                        <LedgerCell className="dim mono">{originLabel(res.source)}</LedgerCell>
                        <LedgerCell className="dim mono">not tracked yet</LedgerCell>
                        <LedgerCell className="verdict">
                          <Chip verdict={res.passed ? 'pass' : 'fail'}>
                            {res.scores.faithfulness.toFixed(2)}
                          </Chip>
                        </LedgerCell>
                      </tr>
                    ))}
                  </tbody>
                </Ledger>
              </div>
            ) : (
              <p className="foot-note">Fetching the latest run's scenario results…</p>
            )}
          </>
        ) : (
          <EmptyState
            heading="No eval runs yet"
            body="Run evals from the Eval page to populate the suite ledger."
            linkHref={`/agents/${id}/eval`}
            linkLabel="Go to Evals"
          />
        )}
      </section>

      {/* ═══ ADVERSARY — real red-team-runs data (UI-SPEC S6.4 region 5) ══ */}
      <section className="section" aria-labelledby="adv-h">
        <div className="section-head">
          <h2 className="label" id="adv-h">Adversary</h2>
          <p className="mono head-count">
            {latestRedTeamRun
              ? `last programme ${formatDateTime(latestRedTeamRun.finished_at ?? latestRedTeamRun.started_at)}`
              : 'no programme run yet'}
          </p>
        </div>

        {latestRedTeamRun ? (
          <>
            <div className="sev">
              <div className="sev-cell" data-hot={severityCounts.critical > 0 ? 'true' : 'false'}>
                <span className="num sev-n">{severityCounts.critical}</span>
                <span className="label">Critical</span>
              </div>
              <div className="sev-cell">
                <span className="num sev-n">{severityCounts.high}</span>
                <span className="label">High</span>
              </div>
              <div className="sev-cell">
                <span className="num sev-n">{severityCounts.medium}</span>
                <span className="label">Medium</span>
              </div>
              <div className="sev-cell">
                <span className="num sev-n">{severityCounts.low}</span>
                <span className="label">Low</span>
              </div>
            </div>

            <EmptyState
              className="adv-coverage-empty"
              heading="Per-strategy coverage"
              body="Per-strategy coverage detail ships in a future release; showing the latest run summary above."
            />

            {criticalFinding && (
              <div className="critical">
                <Chip verdict="seal">Critical</Chip>
                <p>
                  {criticalFinding.description}
                  <span className="mono"> {criticalFinding.attack_vector} · turn {criticalFinding.turn_count}</span>
                </p>
              </div>
            )}
          </>
        ) : (
          <EmptyState
            heading="No red-team programme run yet"
            body="Run the programme to test this agent against adversarial probes."
          />
        )}

        <div className="prompt-acts">
          <Btn onClick={() => runRedTeam.mutate()} disabled={runRedTeam.isPending || agent?.status !== 'ready'}>
            {runRedTeam.isPending ? 'Running…' : 'Run the programme'}
          </Btn>
          <span className="foot-note">
            {agent?.status !== 'ready'
              ? 'The agent must be ready before the programme can run.'
              : 'A critical finding shuts the gate on the spot.'}
          </span>
        </div>
      </section>

      {/* ═══ THE PROMPT ═════════════════════════════════════════════════ */}
      <section className="section" aria-labelledby="prompt-h">
        <div className="section-head">
          <h2 className="label" id="prompt-h">The prompt</h2>
        </div>
        <EmptyState
          heading="No version history yet"
          body="Version history, canary releases and rollback ship in a future release."
          linkHref={`/agents/${id}/soul`}
          linkLabel="Edit in the soul editor"
        />
      </section>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page-scoped CSS — classes with no equivalent in the shared globals.css
// Gotham port (they were page-local `<style>` rules in agent.html, not
// app.css), following the same static dangerouslySetInnerHTML pattern used
// by agents/new/page.tsx.
// ---------------------------------------------------------------------------
const PAGE_CSS = `
  .ident { display: grid; justify-items: end; gap: 5px; text-align: right; }
  .ident-id { font-size: 12px; color: var(--ink-2); }
  .head-count { font-size: 12px; color: var(--ink-3); }

  .gatebar {
    margin-top: 22px;
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    padding: 11px 0 0;
  }
  .gatebar p { font-size: 13px; color: var(--ink-2); margin: 0; }
  .gatebar .mono { font-size: 12px; color: var(--ink-3); }

  .chans {
    display: grid; gap: 1px;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    background: var(--hairline-soft);
    border-top: 1px solid var(--hairline-soft);
    border-bottom: 1px solid var(--hairline-soft);
    margin-bottom: 22px;
  }
  .chan { background: var(--bg); padding: 14px 14px 12px; min-width: 0; }
  .chan-name {
    display: block;
    font-family: var(--mono); font-size: 9px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase; color: var(--ink-3);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .chan-read { display: flex; align-items: baseline; gap: 5px; margin-top: 8px; }
  .chan-val { font-size: 19px; color: var(--ink); line-height: 1.2; }
  .chan-untracked { font-family: var(--mono); font-size: 11px; color: var(--ink-3); }
  .chan-thr { margin-top: 3px; font-family: var(--mono); font-size: 10px; color: var(--ink-3); }

  .scroll-x { overflow-x: auto; }
  .ledger th.verdict, .ledger td.verdict { text-align: right; }

  .sev { display: flex; flex-wrap: wrap; gap: 22px; margin-bottom: 18px; }
  .sev-cell { display: grid; gap: 2px; }
  .sev-n { font-size: 20px; color: var(--ink); }
  .sev-cell[data-hot="true"] .sev-n { color: var(--seal-hot); }

  .critical {
    margin-top: 18px; margin-bottom: 18px;
    background: var(--seal-dim);
    border: 1px solid color-mix(in oklch, var(--seal) 32%, transparent);
    border-radius: var(--r-panel);
    padding: 14px 16px;
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  }
  .critical p { flex: 1; min-width: 220px; font-size: 13.5px; margin: 0; }
  .critical .mono { font-size: 11px; color: var(--ink-2); }

  .foot-note { margin-top: 10px; font-size: 11.5px; color: var(--ink-3); }
  .prompt-acts { margin-top: 18px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .judge-alert-chip { margin-bottom: 14px; }
  .adv-coverage-empty { margin-bottom: 4px; }

  @media (max-width: 720px) {
    .page-head .row { flex-direction: column; }
    .ident { justify-items: start; text-align: left; }
  }
`
