'use client'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Btn from '../../../components/gotham/Btn'
import Chip from '../../../components/gotham/Chip'
import EmptyState from '../../../components/gotham/EmptyState'
import Ledger, { LedgerCell, LedgerColHead, LedgerRowHead } from '../../../components/gotham/Ledger'
import {
  type OpenFinding,
  computeSeverityCounts,
  firstCriticalFinding,
  formatInteger,
  formatPercent,
  gateMessage,
} from './opsFormat'

/**
 * The Adversary region (WIRE-01, WIRE-03, WIRE-04, 23-06) —
 * GET /agents/{id}/red-team/programme (redteam_programme_service.py:147-245)
 * for the coverage rollup and the live open-findings list, and
 * POST /agents/{id}/red-team/findings/{finding_id}/contain (red_team.py:414-461)
 * for the staged contain action. House query shape, same as
 * LivePanel/RetrievalHealthPanel.
 *
 * This component never calls the red-team run-history endpoint (the
 * per-run snapshot whose blocked flag and findings JSONB are frozen the
 * moment a run completes and never updated by contain, 23-UI-SPEC.md
 * §3.3). Severity counts and the first-critical selection
 * come from the shared, proven pure functions in opsFormat.ts, over the
 * live `open_findings` array this query returns — the structural form of
 * that correctness fix: a component that never fetches the runs endpoint
 * cannot accidentally read a snapshot from it.
 *
 * open_findings is also lifted to the page (onOpenFindingsChange), the same
 * callback-up idiom AlertsBanner already established (onAlertsChange) — the
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
  })

  // Per-finding busy state, keyed by identifier — mirrors deploy/page.tsx's
  // savingConfirmations exactly (2147-2150). Never a shared boolean: two
  // findings must never share a busy state (T-23-ADV-06).
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  // A transient, per-finding failure note — mirrors resolveNotes/
  // resolveNoteTimers (deploy/page.tsx:2154-2227): six-second self-clear,
  // cleared immediately on the next successful contain for that id.
  const [notes, setNotes] = useState<Record<string, string>>({})
  const noteTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  useEffect(
    () => () => {
      for (const t of Object.values(noteTimers.current)) clearTimeout(t)
    },
    [],
  )

  // The one error path this region reports into — the page's shared
  // callback, folded into its single existing banner. This is the query's
  // own failure only; contain failures get the per-finding note below, not
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

  const containMutation = useMutation({
    mutationFn: async (findingId: string) => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(
        `${apiBase}/api/v1/agents/${agentId}/red-team/findings/${findingId}/contain`,
        { method: 'POST', headers: { Authorization: `Bearer ${token}` } },
      )
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        const detail = (body as { detail?: string }).detail ?? `HTTP ${r.status}`
        throw Object.assign(new Error(detail), { findingId })
      }
      return r.json()
    },
    // On success the finding disappears from the refetched open_findings
    // list — the tiles recount, the lifted array changes, and the page's
    // gate effect re-fires. That chain IS the confirmation
    // (20-UI-SPEC.md §8.1, "the room changes temperature"). No success
    // message, toast, or "recently contained" list is added: 23-UI-SPEC.md
    // §4.5 locks a "Filed as a regression scenario." note only for the
    // variant that KEEPS a contained row visible in a recently-contained
    // list, and that section's own text defaults to the opposite, simpler
    // branch it also offers ("If the row simply disappears... this line is
    // unnecessary; default to disappearing") — the branch 23-06-PLAN.md's
    // action text picks explicitly ("Add no success message, no toast, and
    // no recently-contained list"). So that note is deliberately never
    // rendered by this component.
    onSuccess: (_result, findingId) => {
      queryClient.invalidateQueries({ queryKey: ['red-team-programme', agentId] })
      setNotes((prev) => {
        if (!(findingId in prev)) return prev
        const next = { ...prev }
        delete next[findingId]
        return next
      })
    },
    onError: (err: unknown, findingId) => {
      const message = (err as Error).message || 'Failed to contain this finding.'
      setNotes((prev) => ({ ...prev, [findingId]: message }))
      clearTimeout(noteTimers.current[findingId])
      noteTimers.current[findingId] = setTimeout(() => {
        setNotes((prev) => {
          const next = { ...prev }
          delete next[findingId]
          return next
        })
        delete noteTimers.current[findingId]
      }, 6000)
    },
    onSettled: (_result, _err, findingId) => {
      setBusy((prev) => {
        const next = { ...prev }
        delete next[findingId]
        return next
      })
    },
  })

  const handleContain = (findingId: string) => {
    setBusy((prev) => ({ ...prev, [findingId]: true }))
    containMutation.mutate(findingId)
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
                fallback matches FindingContain's own aria-label three lines
                below, which already guarded it; turn_count's clause is
                omitted entirely rather than rendered empty. */}
            {gateMessage(critical)}
            <span className="mono">
              {' '}
              {critical.attack_vector ?? 'unrecorded attack vector'}
              {critical.turn_count != null ? ` · turn ${critical.turn_count}` : ''}
            </span>
          </p>
          <FindingContain
            finding={critical}
            busy={!!busy[critical.id]}
            note={notes[critical.id] ?? null}
            onContain={handleContain}
          />
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
                // reasoning as the .critical banner above — a staged contain
                // confirmation is taller than the resting Chip/description
                // and centering against it misaligns the shorter siblings.
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
                <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                  {' '}
                  {f.attack_vector ?? 'unrecorded attack vector'}
                  {f.turn_count != null ? ` · turn ${f.turn_count}` : ''}
                </span>
              </p>
              <FindingContain
                finding={f}
                busy={!!busy[f.id]}
                note={notes[f.id] ?? null}
                onContain={handleContain}
              />
            </div>
          ))}
        </div>
      )}
    </>
  )
}

// Per-finding staged confirmation — the local `staged` state lives here,
// per finding, exactly as PendingConfirmationRow (deploy/page.tsx:1746-1889)
// keeps its own `staged` local to each row rather than a page-level map.
// Only the busy/note state is lifted to the parent (keyed by identifier),
// matching that file's own split between per-row local UI state and
// parent-level per-id request state.
function FindingContain({
  finding,
  busy,
  note,
  onContain,
}: {
  finding: OpenFinding
  busy: boolean
  note: string | null
  onContain: (findingId: string) => void
}) {
  const [staged, setStaged] = useState(false)
  const isCritical = finding.severity === 'critical'
  const questionId = `finding-${finding.id}-confirm-q`

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {note && (
        <p className="help" role="status">
          {note}
        </p>
      )}
      {!staged ? (
        <Btn
          variant="ghost"
          disabled={busy}
          aria-label={`Contain finding: ${finding.attack_vector ?? 'unrecorded attack vector'}`}
          onClick={() => setStaged(true)}
        >
          {busy ? 'Containing…' : 'Contain'}
        </Btn>
      ) : (
        <div className="cap-confirm">
          <p className="cap-confirm-q" id={questionId}>
            {isCritical
              ? 'Contain this finding? This clears the deployment block if it was the only open critical finding.'
              : 'Contain this finding?'}
          </p>
          <div className="cap-confirm-actions">
            <Btn
              variant="ghost"
              autoFocus
              disabled={busy}
              aria-describedby={questionId}
              onClick={() => {
                setStaged(false)
                onContain(finding.id)
              }}
            >
              Yes, contain
            </Btn>
            <Btn variant="ghost" disabled={busy} onClick={() => setStaged(false)}>
              Cancel
            </Btn>
          </div>
        </div>
      )}
    </div>
  )
}
