'use client'
import { use, useCallback, useEffect, useState } from 'react'
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
import AdversaryPanel from './components/AdversaryPanel'
import PromptVersionPanel from './components/PromptVersionPanel'
import { type OpenFinding, isGateBlocked, firstCriticalFinding, gateMessage as buildGateMessage } from './components/opsFormat'

/**
 * The agent operations room — `/agents/[id]` (UI-SPEC S6.4, UI2-05, ported
 * from prototypes/gotham/agent.html). Six `.section` regions in a fixed
 * order: Live, Retrieval health, The bench, Judgement, Adversary, The
 * prompt.
 *
 * Live and Retrieval health call their Phase 21 endpoints via
 * `LivePanel`/`RetrievalHealthPanel` (WIRE-01, 23-05) — never the
 * prototype's client-side seeded-noise demo data (hardcoded channel/version
 * arrays). Adversary calls its own endpoint via `AdversaryPanel`
 * (WIRE-03/WIRE-04, 23-06): coverage and the live open-findings list both
 * come from the programme read, never the per-run history endpoint's
 * frozen snapshot. The gatebar's red-team input is recomputed from that
 * same live open-findings list — lifted here via `onOpenFindingsChange` —
 * rather than a run's own once-written blocked flag, which this page never
 * reads (23-UI-SPEC.md §3.3: a verdict must never outlive the event that
 * produced it). The prompt now calls all four prompt-version endpoints
 * (list, diff, canary, rollback) through its own component
 * (WIRE-01/WIRE-03, 23-07), including the two staged live actions —
 * setting a canary share and rolling back — behind the shipped
 * staged-confirm shape. The bench remains unwired as of this plan and
 * renders an honest `<EmptyState>`; 23-08 wires it.
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
// Read on this page only for its two run-completion timestamps (the
// gatebar stamp's fallback, the Adversary section-head stamp) — never for
// a per-run findings snapshot or a per-run blocked flag, both frozen the
// moment a run completed and never updated by a contain action. The live,
// containable findings list and the gate input this page actually reads
// both come from AdversaryPanel's lifted open-findings list instead
// (opsFormat.ts).
interface RedTeamRun {
  started_at: string | null
  finished_at: string | null
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
  // The Adversary component's own live open-findings list, lifted here so
  // this page's gate computation and that component's severity tiles read
  // the identical array rather than two fetches that can disagree.
  const [openFindings, setOpenFindings] = useState<OpenFinding[]>([])

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
  // The run row survives on this page as a display fact only — WHEN
  // something happened, never a verdict about WHETHER anything is wrong.
  // Destructured once, immediately, so its two display uses below read
  // these locals instead of re-deriving from the row a second time.
  const { finished_at: lastRedTeamFinishedAt = null, started_at: lastRedTeamStartedAt = null } =
    latestRedTeamRun ?? {}
  const hasProgrammeRun = latestRedTeamRun !== null

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

  // ---- Gatebar: real checklist-runs + the live red-team open-findings list
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

  // The stale-verdict fix (23-UI-SPEC.md §3.3): the old source here was a
  // per-run blocked flag written once when a run completed and never
  // updated by a contain action — once any run had ever produced a
  // critical finding, this page could never honestly reopen its gate
  // again through that flag. isGateBlocked derives it fresh, every render,
  // from the live open-findings list AdversaryPanel lifts up, so
  // containing the last open critical finding reopens the gate the moment
  // the panel refetches.
  const redTeamBlocked = isGateBlocked(openFindings)
  const checklistBlocked = latestChecklistRun?.recommendation === 'block'
  const gateBlocked = redTeamBlocked || checklistBlocked || hasUnresolvedCriticalAlert

  // This is the single authoritative computation of the real deploy-gate
  // state on this page (checklist recommendation + the live red-team
  // open-findings gate + the folded red_team_critical alert, UI-SPEC S8.6 /
  // OQ2) — the one effect that ever calls setGate('open'). AlertsBanner may
  // independently call setGate('blocked') the instant it sees a critical
  // alert (defense in depth, same signal folded in below), but never
  // 'open', so the two writers can never fight over reopening the gate.
  useEffect(() => {
    setGate(gateBlocked ? 'blocked' : 'open')
  }, [gateBlocked, setGate])

  const gateStamp = latestChecklistRun?.created_at ?? lastRedTeamFinishedAt ?? null
  const gateMessage = gateBlocked
    ? buildGateMessage(firstCriticalFinding(openFindings))
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

        {/* Real gatebar — derives entirely from checklist-runs + the live
            red-team open-findings gate + the folded red_team_critical alert
            (UI-SPEC S8.6, Pitfall 5). No component hand-colours itself
            here; setGate is called once above and the token cascade
            repaints the room. */}
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

      {/* ═══ ADVERSARY — real red-team programme data (WIRE-03, WIRE-04, 23-06) ═ */}
      <section className="section" aria-labelledby="adv-h">
        <div className="section-head">
          <h2 className="label" id="adv-h">Adversary</h2>
          <p className="mono head-count">
            {hasProgrammeRun
              ? `last programme ${formatDateTime(lastRedTeamFinishedAt ?? lastRedTeamStartedAt)}`
              : 'no programme run yet'}
          </p>
        </div>

        <AdversaryPanel
          agentId={id}
          enabled={isLoaded && !!isSignedIn && step1Done}
          onError={setRegionError}
          onOpenFindingsChange={setOpenFindings}
        />

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

      {/* ═══ THE PROMPT — real prompt-version data (WIRE-01, WIRE-03, 23-07) ═ */}
      <section className="section" aria-labelledby="prompt-h">
        <div className="section-head">
          <h2 className="label" id="prompt-h">The prompt</h2>
        </div>
        <PromptVersionPanel
          agentId={id}
          enabled={isLoaded && !!isSignedIn && step1Done}
          onError={setRegionError}
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

  /* Staged-confirm shape (OD-3, 23-01-PLAN.md § Open Decisions Resolved).
     These five rules exist ONLY in deploy/page.tsx's own PAGE_CSS (lines
     2970-2974) — globals.css has none of them — so a staged confirmation
     block rendered in this room would be unstyled without a copy here.
     Ported verbatim rather than lifted into the shared stylesheet: lifting
     would require editing deploy/page.tsx, and this phase's roadmap entry
     states plainly it shares no file with the phase that produced it. A
     gate (23-06's Task 2 verify) asserts these two blocks stay textually
     identical, so the duplication is checked, not merely hoped over. */
  .cap-confirm { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--hairline-soft); }
  .cap-confirm-q { font-size: 13px; line-height: 1.5; color: var(--ink); }
  .cap-confirm-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
  .cap-confirm-actions .btn { flex: none; }
  .cap-confirm-actions .btn:first-child { border-color: var(--hairline-strong); }

  @media (max-width: 720px) {
    .page-head .row { flex-direction: column; }
    .ident { justify-items: start; text-align: left; }
  }
`
