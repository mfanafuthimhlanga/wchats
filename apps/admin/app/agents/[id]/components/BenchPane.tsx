'use client'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Btn from '../../../components/gotham/Btn'
import Chip from '../../../components/gotham/Chip'
import EmptyState from '../../../components/gotham/EmptyState'
import { judgeVerdictToChip, gradeToChip } from './opsFormat'

/**
 * The bench region (WIRE-01, 23-08) — GET /agents/{id}/traces?status=failing
 * (bench_service.list_failing_traces, traces.py:84-118) for the failing-trace
 * listing and its tally, and POST /agents/{id}/traces/{trace_id}/grade
 * (traces.py:126-186) for the operator's grade. Filing dispatches
 * promote_trace_to_scenario server-side (traces.py:158-167) on a best-effort
 * basis — the grade itself always succeeds even if that dispatch fails — so
 * this component's only job is to call the grade route and render the tally
 * the (re-fetched) listing response carries. It never increments a tally
 * locally and it never touches the promotion path.
 *
 * The two-pane roving listbox is net-new UI in this codebase — 23-PATTERNS.md
 * flags it as having no analog anywhere in apps/admin. Built to
 * 20-UI-SPEC.md S6.4.1's interaction contract: a listbox/option pair with a
 * roving tab index, arrow/Home/End moving both selection and focus together,
 * and the P/H/X grade shortcuts acting on the selected trace from anywhere
 * within the region. Filing is irrevocable (TERRARIUM law, traces.py:142-145)
 * and the three grade actions are deliberately unstaged, per the approved
 * design contract (23-UI-SPEC.md S4.3) — no confirmation ships in this
 * region, so the "confirmation open" guard the plan's threat register names
 * has nothing to check; see confirmationOpen below for why that is
 * documented rather than silently dropped.
 */

interface Trace {
  trace_id: string
  verdict: string
  judge_rationale: string
  customer_turn: string
  agent_turn: string
  conversation_id: string | null
  graded_status: string // 'ungraded' | 'filed' | 'held' | 'dismissed'
}

interface Tally {
  filed: number
  held: number
  dismissed: number
}

interface TracesResponse {
  traces: Trace[]
  tally: Tally
}

type Grade = 'filed' | 'held' | 'dismissed'

const GRADE_ORDER: Grade[] = ['filed', 'held', 'dismissed']
const GRADE_KEYS: Record<string, Grade> = { p: 'filed', h: 'held', x: 'dismissed' }
const GRADE_LABEL: Record<Grade, string> = { filed: 'File', held: 'Hold', dismissed: 'Dismiss' }
const GRADE_BUSY_LABEL: Record<Grade, string> = {
  filed: 'Filing…',
  held: 'Holding…',
  dismissed: 'Dismissing…',
}

/** The first non-empty line of a customer turn, with an honest fallback for a blank one. */
function firstLine(text: string): string {
  const line = text.split('\n')[0]?.trim()
  return line || 'No customer turn recorded.'
}

function describeTrace(trace: Trace, index: number, total: number): string {
  const graded = trace.graded_status !== 'ungraded' ? ` Grade: ${trace.graded_status}.` : ''
  return `Trace ${index + 1} of ${total}. ${firstLine(trace.customer_turn)}. Judge verdict: ${trace.verdict}.${graded}`
}

export default function BenchPane({
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
  const queryClient = useQueryClient()

  const tracesQuery = useQuery({
    queryKey: ['bench-traces', agentId],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${agentId}/traces?status=failing`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return (await r.json()) as TracesResponse
    },
    enabled,
    staleTime: 15_000,
  })

  // The one error path this region reports into — the page's shared banner.
  // Grade failures never reach this: they get the per-trace inline note
  // below, not this region-level surface.
  useEffect(() => {
    if (tracesQuery.isError) {
      onError('bench', (tracesQuery.error as Error).message || 'Failed to load the bench.')
    } else {
      onError('bench', null)
    }
  }, [tracesQuery.isError, tracesQuery.error, onError])

  const traces = tracesQuery.data?.traces ?? []
  const tally = tracesQuery.data?.tally ?? null

  // Derived, not stateful: the effective selection falls back to the first
  // trace whenever nothing has been explicitly chosen yet, so the very
  // first render already has a real tab stop and a populated enlarger —
  // no separate mount effect, and no one-frame gap where no option carries
  // tabIndex 0.
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const effectiveSelectedId = selectedId ?? traces[0]?.trace_id ?? null
  const selectedTrace = traces.find((t) => t.trace_id === effectiveSelectedId) ?? null

  const optionRefs = useRef<Record<string, HTMLButtonElement | null>>({})

  // Per-trace busy state keyed by identifier, storing WHICH grade is in
  // flight (not just a boolean) so the busy label on the resting buttons
  // names the actual action taken. Grading one trace must never disable
  // another's actions — every read below is keyed by the specific trace id.
  const [busy, setBusy] = useState<Record<string, Grade>>({})
  // A transient, per-trace note — the concurrent-grade 409's locked
  // sentence, or any other grade failure — self-clearing after six
  // seconds, mirroring deploy/page.tsx's resolveNotes/resolveNoteTimers
  // pattern (2154-2227) that AdversaryPanel already reuses for contain.
  const [notes, setNotes] = useState<Record<string, string>>({})
  const noteTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})
  useEffect(
    () => () => {
      for (const t of Object.values(noteTimers.current)) clearTimeout(t)
    },
    [],
  )

  const [liveMessage, setLiveMessage] = useState('')

  const gradeMutation = useMutation({
    mutationFn: async ({ traceId, grade }: { traceId: string; grade: Grade }) => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${agentId}/traces/${traceId}/grade`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ grade }),
      })
      if (r.status === 409) {
        // TERRARIUM law: two operators grading the same trace at once is
        // the expected, benign outcome of a race, never an error surface
        // (23-UI-SPEC.md S4.3). Thrown with the trace id attached so
        // react-query routes it to onError below, where it becomes the
        // locked inline note plus a refetch — never a toast.
        throw Object.assign(new Error('Someone already graded this trace.'), {
          traceId,
          conflict: true,
        })
      }
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        const detail = (body as { detail?: string }).detail ?? `HTTP ${r.status}`
        throw Object.assign(new Error(detail), { traceId })
      }
      return (await r.json()) as { trace_id: string; grade: Grade; tally: Tally }
    },
    onSuccess: (_data, { traceId }) => {
      // The tally rendered on screen always comes from this refetch of the
      // listing route, never from the mutation's own response body and
      // never from a local increment — the promotion filing dispatches is
      // best-effort server-side, so a locally-incremented count could
      // assert a suite state this console has no way to know.
      queryClient.invalidateQueries({ queryKey: ['bench-traces', agentId] })
      setNotes((prev) => {
        if (!(traceId in prev)) return prev
        const next = { ...prev }
        delete next[traceId]
        return next
      })
    },
    onError: (err: unknown, { traceId }) => {
      queryClient.invalidateQueries({ queryKey: ['bench-traces', agentId] })
      const withContext = err as Error & { traceId?: string }
      const message = withContext.message || 'Failed to grade this trace.'
      setNotes((prev) => ({ ...prev, [traceId]: message }))
      clearTimeout(noteTimers.current[traceId])
      noteTimers.current[traceId] = setTimeout(() => {
        setNotes((prev) => {
          const next = { ...prev }
          delete next[traceId]
          return next
        })
        delete noteTimers.current[traceId]
      }, 6000)
    },
    onSettled: (_data, _err, { traceId }) => {
      setBusy((prev) => {
        const next = { ...prev }
        delete next[traceId]
        return next
      })
    },
  })

  const handleGrade = (traceId: string, grade: Grade) => {
    setBusy((prev) => ({ ...prev, [traceId]: grade }))
    const index = traces.findIndex((t) => t.trace_id === traceId)
    // Captured at call time, not inside the mutation's own onSuccess, so a
    // refetch that lands before the announcement is set can never change
    // which position this sentence names.
    const announcement = `Trace ${index === -1 ? '' : index + 1} graded ${grade}.`
    gradeMutation.mutate(
      { traceId, grade },
      { onSuccess: () => setLiveMessage(announcement) },
    )
  }

  const moveSelection = (nextId: string) => {
    setSelectedId(nextId)
    optionRefs.current[nextId]?.focus()
  }

  // Handled on the listbox container so the keydown events bubbling from
  // every option button are caught exactly once, per the interaction
  // contract (20-UI-SPEC.md S6.4.1).
  const handleListboxKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (traces.length === 0) return
    const currentIndex = Math.max(
      0,
      traces.findIndex((t) => t.trace_id === effectiveSelectedId),
    )
    let nextIndex: number | null = null
    if (e.key === 'ArrowDown') nextIndex = (currentIndex + 1) % traces.length
    else if (e.key === 'ArrowUp') nextIndex = (currentIndex - 1 + traces.length) % traces.length
    else if (e.key === 'Home') nextIndex = 0
    else if (e.key === 'End') nextIndex = traces.length - 1
    if (nextIndex === null) return
    e.preventDefault()
    moveSelection(traces[nextIndex].trace_id)
  }

  // No staged confirmation ships anywhere in this region — file/hold/
  // dismiss are plain, unstaged actions by the approved design contract
  // (23-UI-SPEC.md S4.3: "Do not add a confirmation here; a planner does
  // not redesign an approved contract"). The plan's threat register still
  // names "a confirmation is open anywhere in the region" as a fourth
  // shortcut guard alongside the modifier/form-control/already-filed ones.
  // With no staged UI in this build there is nothing that can ever be
  // open, so this constant is the honest, structural form of that guard:
  // always false, never a state that changes, and never a condition the
  // shortcut can be blocked by in error.
  const confirmationOpen = false

  const handleBenchKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const grade = GRADE_KEYS[e.key.toLowerCase()]
    if (!grade) return
    if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return
    const target = e.target as HTMLElement
    const tag = target.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) return
    if (confirmationOpen) return
    if (!selectedTrace) return
    if (selectedTrace.graded_status === 'filed') return
    if (busy[selectedTrace.trace_id]) return
    e.preventDefault()
    handleGrade(selectedTrace.trace_id, grade)
  }

  if (!tracesQuery.data) {
    // On a genuine failure the region reports through onError above and
    // renders nothing else — the page owns the one error banner.
    return tracesQuery.isError ? null : <p className="foot-note">Fetching the bench…</p>
  }

  if (traces.length === 0) {
    return (
      <EmptyState
        heading="Nothing on the bench"
        body="No failing production traces right now. Every recent turn passed its judge."
      />
    )
  }

  return (
    <>
      <p className="vh" role="status" aria-live="polite">
        {liveMessage}
      </p>

      <div className="bench-panes" onKeyDown={handleBenchKeyDown}>
        <div
          className="bench-sheet"
          role="listbox"
          aria-label="Failing production traces"
          onKeyDown={handleListboxKeyDown}
        >
          {traces.map((trace, index) => {
            const isSelected = trace.trace_id === effectiveSelectedId
            const isGraded = trace.graded_status !== 'ungraded'
            return (
              <button
                key={trace.trace_id}
                ref={(el) => {
                  optionRefs.current[trace.trace_id] = el
                }}
                type="button"
                role="option"
                aria-selected={isSelected}
                tabIndex={isSelected ? 0 : -1}
                aria-label={describeTrace(trace, index, traces.length)}
                onClick={() => setSelectedId(trace.trace_id)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                  width: '100%',
                  textAlign: 'left',
                  padding: '10px 12px',
                  background: isSelected ? 'var(--surface-2)' : 'transparent',
                  border: `1px solid ${isSelected ? 'var(--hairline-strong)' : 'transparent'}`,
                  borderRadius: 'var(--r-control)',
                  color: 'var(--ink)',
                  cursor: 'pointer',
                  font: 'inherit',
                }}
              >
                <span style={{ fontSize: 13.5, lineHeight: 1.4 }}>{firstLine(trace.customer_turn)}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }} aria-hidden="true">
                  <Chip verdict={judgeVerdictToChip(trace.verdict)}>{trace.verdict}</Chip>
                  {isGraded && (
                    <Chip verdict={gradeToChip(trace.graded_status)}>
                      {trace.graded_status.charAt(0).toUpperCase() + trace.graded_status.slice(1)}
                    </Chip>
                  )}
                </span>
              </button>
            )
          })}
        </div>

        <div className="bench-enlarger">
          <h3 className="vh">Selected trace</h3>
          {selectedTrace && (
            <>
              <p className="label">Customer</p>
              <p style={{ fontSize: 13.5, lineHeight: 1.55, color: 'var(--ink)', whiteSpace: 'pre-wrap' }}>
                {selectedTrace.customer_turn || 'No customer turn recorded.'}
              </p>

              <p className="label" style={{ marginTop: 16 }}>
                Agent
              </p>
              <p style={{ fontSize: 13.5, lineHeight: 1.55, color: 'var(--ink)', whiteSpace: 'pre-wrap' }}>
                {selectedTrace.agent_turn || 'No agent turn recorded.'}
              </p>

              <p className="label" style={{ marginTop: 16 }}>
                Judge
              </p>
              <p className="voice" style={{ whiteSpace: 'pre-wrap' }}>
                {selectedTrace.judge_rationale || 'No rationale recorded.'}
              </p>

              {notes[selectedTrace.trace_id] && (
                <p className="help" role="status">
                  {notes[selectedTrace.trace_id]}
                </p>
              )}

              <div style={{ display: 'flex', gap: 10, marginTop: 16, flexWrap: 'wrap' }}>
                {GRADE_ORDER.map((grade) => {
                  const traceId = selectedTrace.trace_id
                  const isFiled = selectedTrace.graded_status === 'filed'
                  const busyGrade = busy[traceId]
                  const isBusy = busyGrade !== undefined
                  const label = !isFiled && busyGrade === grade ? GRADE_BUSY_LABEL[grade] : GRADE_LABEL[grade]
                  return (
                    <Btn
                      key={grade}
                      variant="ghost"
                      disabled={isBusy}
                      aria-disabled={isFiled || undefined}
                      className={isFiled ? 'is-disabled' : undefined}
                      onClick={() => {
                        if (!isFiled) handleGrade(traceId, grade)
                      }}
                    >
                      {label}
                    </Btn>
                  )
                })}
              </div>

              {selectedTrace.graded_status === 'filed' && (
                <p className="help">This trace has been filed. It cannot be re-graded.</p>
              )}
            </>
          )}
        </div>
      </div>

      {tally && (
        <p className="foot-note">
          {tally.filed} filed · {tally.held} held · {tally.dismissed} dismissed
        </p>
      )}
    </>
  )
}
