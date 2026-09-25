'use client'
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Btn from '../../../components/gotham/Btn'
import Chip from '../../../components/gotham/Chip'
import EmptyState from '../../../components/gotham/EmptyState'
import Ledger, { LedgerCell, LedgerColHead, LedgerRowHead } from '../../../components/gotham/Ledger'
import {
  type LatestRun,
  type OpenFinding,
  computeSeverityCounts,
  firstCriticalFinding,
  formatInteger,
  formatPercent,
  gateMessage,
  latestRunLine,
  retestIsRunning,
} from './opsFormat'
import FindingMeta from './FindingMeta'

/**
 * The Adversary region (WIRE-01, WIRE-03, WIRE-04, 23-06):
 * GET /agents/{id}/red-team/programme (redteam_programme_service.py) for the
 * coverage rollup and the live open-findings list, and
 * POST /agents/{id}/red-team/findings/{finding_id}/retest (red_team.py) for
 * the Re-test action. House query shape, same as
 * LivePanel/RetrievalHealthPanel.
 *
 * The owner clears a finding by changing the agent and re-testing it: the
 * platform replays the finding's recorded attack against the agent as it is
 * now, and today's red-team rules decide (red_team_retest.py). The POST
 * answers 202 and the re-test runs on the worker for about a minute, so the
 * programme query polls every five seconds while any open finding's re-test
 * is running. A resolved finding leaves `open_findings`: the tiles recount,
 * the lifted array changes and the page's gate recomputes, and that change
 * is the confirmation. A finding that stays open says what the re-test found
 * in its re-test line (FindingMeta, retestLine).
 *
 * This component never calls the red-team run-history endpoint (the
 * per-run snapshot whose blocked flag and findings JSONB are frozen the
 * moment a run completes, 23-UI-SPEC.md §3.3). Severity counts and the
 * first-critical selection come from the shared, proven pure functions in
 * opsFormat.ts, over the live `open_findings` array this query returns. A
 * component that never fetches the runs endpoint cannot accidentally read a
 * snapshot from it.
 *
 * open_findings is also lifted to the page (onOpenFindingsChange), the same
 * callback-up idiom AlertsBanner already established (onAlertsChange). The
 * page's own deploy-gate computation (Task 2, 23-06) reads the same array
 * this component's tiles do, so the two can never disagree.
 */

interface CoverageRow {
  strategy_id: string
  attack_vector: string
  probes_tested: number
  findings_count: number
  high_severity_count: number
  attack_success_rate: number
}

interface RedTeamProgrammeResponse {
  coverage: CoverageRow[]
  open_findings: OpenFinding[]
  latest_run: LatestRun | null
}

// Stable reference so the lift effect below does not re-fire on every
// render while the query is still pending — a fresh `[]` literal would be a
// new array identity each time, even though its content never changes.
const EMPTY_OPEN_FINDINGS: OpenFinding[] = []

/** "prompt_injection" -> "Prompt Injection" — the existing originLabel()-style
 * sentence/title-case convention this page already uses elsewhere
 * (23-UI-SPEC.md §4.5: "title-cased for display"). Kept local: this is a
 * display-only string transform, not a sentinel/derivation decision, so it
 * does not belong in opsFormat.ts alongside the functions this plan does
 * not touch. */
function formatAttackVector(vector: string): string {
  return vector.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function AdversaryPanel({
  agentId,
  enabled,
  onError,
  onOpenFindingsChange,
  onCoverageChange,
}: {
  agentId: string
  enabled: boolean
  onError: (region: string, message: string | null) => void
  onOpenFindingsChange: (findings: OpenFinding[]) => void
  /** 23-09 adversarial review (UI-1): lifted so the page's section head can
   * render "no programme run yet" / "last programme ..." from the SAME
   * query this panel's own body renders from, instead of the separate
   * red-team-runs history query the header used before this fix — the two
   * can disagree, and did, in the rendered review that found this. */
  onCoverageChange: (hasCoverage: boolean) => void
}) {
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const queryClient = useQueryClient()

  const programmeQuery = useQuery({
    queryKey: ['red-team-programme', agentId],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${agentId}/red-team/programme`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return (await r.json()) as RedTeamProgrammeResponse
    },
    enabled,
    staleTime: 15_000,
    // A re-test runs for about a minute after its 202. Poll while one is
    // running so its outcome, or the finding leaving the list, shows up
    // without a reload; stop the moment none is.
    refetchInterval: (query) =>
      query.state.data?.open_findings.some((f) => retestIsRunning(f.retest)) ? 5_000 : false,
  })

  // Per-finding busy state, keyed by identifier, mirroring deploy/page.tsx's
  // savingConfirmations. Never a shared boolean: two findings must never
  // share a busy state (T-23-ADV-06).
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  // A per-finding note carrying the API's refusal (a 409's detail). It stays
  // until the owner re-tests that finding again: "A conversation cannot
  // reproduce this finding; re-run the red team to clear it" is standing
  // advice, not a transient blip, so it does not self-clear on a timer.
  const [notes, setNotes] = useState<Record<string, string>>({})

  // The one error path this region reports into — the page's shared
  // callback, folded into its single existing banner. This is the query's
  // own failure only; re-test refusals get the per-finding note below, not
  // this region-level surface.
  useEffect(() => {
    if (programmeQuery.isError) {
      onError('adversary', (programmeQuery.error as Error).message || 'Failed to load the red-team programme.')
    } else {
      onError('adversary', null)
    }
  }, [programmeQuery.isError, programmeQuery.error, onError])

  const data = programmeQuery.data
  const openFindings = data?.open_findings ?? EMPTY_OPEN_FINDINGS

  // Lifted to the page every time the live list changes — following the
  // alerts banner's own lift idiom (onAlertsChange) — so the page's gate
  // computation and this component's own tiles read the same array rather
  // than two fetches that can disagree.
  useEffect(() => {
    onOpenFindingsChange(openFindings)
  }, [openFindings, onOpenFindingsChange])

  // Lifted alongside openFindings, same idiom: only fires once the query
  // has actually resolved, so the page's header keeps its own "no
  // programme run yet" default while this panel is still fetching rather
  // than flashing a premature answer.
  useEffect(() => {
    if (data) {
      onCoverageChange(data.coverage.length > 0)
    }
  }, [data, onCoverageChange])

  const clearNote = (findingId: string) =>
    setNotes((prev) => {
      if (!(findingId in prev)) return prev
      const next = { ...prev }
      delete next[findingId]
      return next
    })

  const retestMutation = useMutation({
    mutationFn: async (findingId: string) => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(
        `${apiBase}/api/v1/agents/${agentId}/red-team/findings/${findingId}/retest`,
        { method: 'POST', headers: { Authorization: `Bearer ${token}` } },
      )
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        const detail = (body as { detail?: unknown }).detail
        throw new Error(typeof detail === 'string' && detail ? detail : `HTTP ${r.status}`)
      }
      return r.json()
    },
    onMutate: (findingId) => clearNote(findingId),
    // Returning the refetch keeps the mutation pending, and the button
    // disabled, until the list carries the running re-test. Without it the
    // button would re-enable for the length of one round trip.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['red-team-programme', agentId] }),
    onError: (err: unknown, findingId) => {
      const message = (err as Error).message || 'The re-test did not start.'
      setNotes((prev) => ({ ...prev, [findingId]: message }))
      // A 409 such as "Finding is resolved, not open" means this list is
      // behind the server; refetch so it catches up.
      queryClient.invalidateQueries({ queryKey: ['red-team-programme', agentId] })
    },
    onSettled: (_result, _err, findingId) => {
      setBusy((prev) => {
        const next = { ...prev }
        delete next[findingId]
        return next
      })
    },
  })

  const handleRetest = (findingId: string) => {
    setBusy((prev) => ({ ...prev, [findingId]: true }))
    retestMutation.mutate(findingId)
  }

  const severityCounts = useMemo(() => computeSeverityCounts(openFindings), [openFindings])
  const critical = useMemo(() => firstCriticalFinding(openFindings), [openFindings])
  const remaining = useMemo(
    () => openFindings.filter((f) => f.id !== critical?.id),
    [openFindings, critical],
  )

  if (!data) {
    // On a genuine failure the region reports through onError above and
    // renders nothing else — the page owns the one error banner.
    return programmeQuery.isError ? null : <p className="foot-note">Fetching the red-team programme…</p>
  }

  const coverage = data.coverage
  const latestRun = latestRunLine(data.latest_run)

  if (coverage.length === 0) {
    return (
      <EmptyState
        heading="No coverage data yet"
        body="Run the programme to populate strategy coverage."
      />
    )
  }

  return (
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
      {latestRun && (
        <p className="foot-note" style={{ margin: '-8px 0 18px' }}>
          {latestRun}
        </p>
      )}

      <div className="scroll-x">
        <Ledger caption="Per-strategy red-team coverage. Findings are all-time across every run and are not filtered to open status.">
          <thead>
            <tr>
              <LedgerColHead>Strategy</LedgerColHead>
              <LedgerColHead numeric>Probes tested</LedgerColHead>
              {/* 23-09 adversarial review: the caption already explains this
                  column is all-time/all-status, but Ledger's caption is
                  always visually hidden (screen-reader only) — a sighted
                  operator saw only the bare word "Findings" next to a
                  three-row severity summary above it and could easily read
                  it as "open findings." "All findings" disambiguates without
                  using the specific phrase 23-UI-SPEC.md says not to use. */}
              <LedgerColHead numeric>All findings</LedgerColHead>
              <LedgerColHead numeric>High severity</LedgerColHead>
              <LedgerColHead numeric>Attack success rate</LedgerColHead>
            </tr>
          </thead>
          <tbody>
            {coverage.map((row) => (
              <tr key={row.strategy_id}>
                <LedgerRowHead>{formatAttackVector(row.attack_vector)}</LedgerRowHead>
                <LedgerCell numeric className="mono">{formatInteger(row.probes_tested)}</LedgerCell>
                <LedgerCell numeric className="mono">{formatInteger(row.findings_count)}</LedgerCell>
                <LedgerCell
                  numeric
                  className="mono"
                  style={{ color: row.high_severity_count > 0 ? 'var(--fail)' : undefined }}
                >
                  {formatInteger(row.high_severity_count)}
                </LedgerCell>
                <LedgerCell numeric className="mono">{formatPercent(row.attack_success_rate)}</LedgerCell>
              </tr>
            ))}
          </tbody>
        </Ledger>
      </div>

      {critical && (
        <div className="critical">
          <Chip verdict="seal">Critical</Chip>
          <p>
            {/* 23-09 adversarial review (finding 15): description,
                attack_vector and turn_count are all typed nullable
                (OpenFinding, opsFormat.ts) — description can miss its JSONB
                correlation, attack_vector/turn_count come straight from the
                findings table's own nullable columns. This banner rendered
                all three raw with no fallback, so a null description could
                blank the single most consequential sentence on this page
                (the one explaining the deployment block) while the metadata
                span below it rendered a stray " · turn 4" with no vector, or
                "prompt_injection · turn " with no count. gateMessage() is
                the same locked fallback (OD-5) the page's own gatebar
                already uses for this exact situation — reused here rather
                than inventing a second apologetic string. attack_vector's
                fallback matches RetestAction's own aria-label below, which
                already guarded it; turn_count's clause is omitted entirely
                rather than rendered empty. */}
            {gateMessage(critical)}
            <FindingMeta finding={critical} />
          </p>
          <RetestAction finding={critical} busy={!!busy[critical.id]} onRetest={handleRetest} />
          <RetestNote note={notes[critical.id]} />
        </div>
      )}

      {remaining.length > 0 && (
        <div style={{ marginTop: 18, display: 'flex', flexDirection: 'column' }}>
          {remaining.map((f) => (
            <div
              key={f.id}
              style={{
                display: 'flex',
                // flex-start, not center (23-09 adversarial review): the same
                // reasoning as the .critical banner above. The description
                // column is the tallest item, and centering the chip and the
                // button against it floats them mid-row.
                alignItems: 'flex-start',
                gap: 14,
                flexWrap: 'wrap',
                padding: '12px 0',
                borderTop: '1px solid var(--hairline-soft)',
              }}
            >
              <Chip verdict={f.severity === 'critical' ? 'seal' : 'mute'}>{f.severity}</Chip>
              <p style={{ flex: 1, minWidth: 220, fontSize: 13.5, margin: 0, color: 'var(--ink-2)' }}>
                {/* Same null-guard as the critical banner above (finding 15).
                    This list's findings are not necessarily critical, so
                    gateMessage()'s "a blocking signal is open" text would be
                    inaccurate here — a plain, honest fallback instead. */}
                {f.description || 'No description recorded.'}
                <FindingMeta finding={f} />
              </p>
              <RetestAction finding={f} busy={!!busy[f.id]} onRetest={handleRetest} />
              <RetestNote note={notes[f.id]} />
            </div>
          ))}
        </div>
      )}
    </>
  )
}

// One click, no staged confirmation: a re-test changes nothing about the
// agent and at worst leaves the finding open with a fresh reading. Disabled
// while this finding's re-test runs or its request is in flight. The
// aria-label names the vector so a screen reader can tell one finding's
// button from the next, and starts with the visible label (WCAG 2.5.3).
function RetestAction({
  finding,
  busy,
  onRetest,
}: {
  finding: OpenFinding
  busy: boolean
  onRetest: (findingId: string) => void
}) {
  const running = retestIsRunning(finding.retest)
  return (
    <Btn
      variant="ghost"
      disabled={busy || running}
      aria-label={`Re-test finding: ${finding.attack_vector ?? 'unrecorded attack vector'}`}
      onClick={() => onRetest(finding.id)}
    >
      Re-test
    </Btn>
  )
}

// The API's refusal for one finding, on its own row under the finding: a
// full-width flex item, so it wraps below the chip, the description and the
// button in the banner and the list alike.
function RetestNote({ note }: { note: string | undefined }) {
  if (!note) return null
  return (
    <p className="help" role="status" style={{ flexBasis: '100%', margin: 0 }}>
      {note}
    </p>
  )
}
