'use client'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Btn from '../../../components/gotham/Btn'
import EmptyState from '../../../components/gotham/EmptyState'
import Ledger, { LedgerCell, LedgerColHead, LedgerRowHead } from '../../../components/gotham/Ledger'
import { renderCanaryPercent } from './opsFormat'

/**
 * The prompt region (WIRE-01, WIRE-03, 23-07) — all four `prompt_versions`
 * routes Phase 21 (OPS-16) shipped and this console never called:
 *
 *   GET  /agents/{id}/prompt-versions          — list_prompt_versions, prompt_versions.py:96
 *   GET  /agents/{id}/prompt-versions/diff      — diff_prompt_versions, prompt_versions.py:121
 *   POST /agents/{id}/prompt-versions/canary    — set_prompt_version_canary, prompt_versions.py:146
 *   POST /agents/{id}/prompt-versions/rollback  — rollback_prompt_version, prompt_versions.py:180
 *
 * `prompt_versions` rows are immutable — the four soul fields on an existing
 * row are never mutated after INSERT (prompt_version_service.py module
 * docstring). Canary and rollback both APPEND a new row; rollback in
 * particular restores a version's content WITHOUT deleting any history
 * (prompt_versions.py:187's docstring) — that fact is the load-bearing
 * clause in this file's locked rollback copy, not decoration.
 *
 * Both actions route real customer turns the instant they are confirmed
 * (percent routing and the active prompt are both chosen at turn dispatch,
 * prompt_version_service.py:249-293's resolve_prompt_version), so both stage
 * behind the house `.cap-confirm` shape (deploy/page.tsx:1746-1889),
 * identically to AdversaryPanel's contain action. Busy state is keyed per
 * version identifier, never a shared flag, so one version's action in
 * flight never disables another's.
 *
 * Nothing in this region is a verdict (OD-2): no Chip, no hue, only the
 * existing bone/graphite tokens and the reused `.ledger`/`.scroll-x`/`.well`/
 * `.cap-confirm*` classes. The canary share renders only through opsFormat's
 * renderCanaryPercent — absent and zero both read `0%`, because a version
 * routing no traffic is a measurement, not a gap.
 */

interface PromptVersionRow {
  id: string
  agent_id: string
  version_number: number
  soul_role: string
  soul_voice: string
  soul_do_list: string[]
  soul_donot_list: string[]
  label: string | null
  canary_percent: number | null
  created_at: string | null
}

interface PromptVersionsResponse {
  versions: PromptVersionRow[]
}

interface DiffSide {
  id: string
  version_number: number
}

interface DiffFieldValue {
  a: string | string[]
  b: string | string[]
  changed: boolean
}

interface PromptVersionDiffResponse {
  version_a: DiffSide
  version_b: DiffSide
  fields: {
    soul_role: DiffFieldValue
    soul_voice: DiffFieldValue
    soul_do_list: DiffFieldValue
    soul_donot_list: DiffFieldValue
  }
}

// The four soul fields, in the order the soul editor's own form presents
// them (soul/page.tsx: Identity's Role, Temperament's composed Voice,
// Rules' Do / Do not) — the comparison never invents its own ordering.
const SOUL_FIELD_ROWS: Array<{
  key: 'soul_role' | 'soul_voice' | 'soul_do_list' | 'soul_donot_list'
  label: string
}> = [
  { key: 'soul_role', label: 'Role' },
  { key: 'soul_voice', label: 'Voice' },
  { key: 'soul_do_list', label: 'Do' },
  { key: 'soul_donot_list', label: 'Do not' },
]

function formatCreatedAt(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toISOString().slice(0, 10)
}

// A list-valued field renders each entry as its own line inside the `.well`
// block. Joining the entries into one string would make a single reordered
// entry look like a total rewrite — exactly the misreading this comparison
// exists to prevent.
function renderFieldSide(value: string | string[]) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <span style={{ color: 'var(--ink-3)' }}>—</span>
    return value.map((line, i) => <div key={i}>{line}</div>)
  }
  return value || '—'
}

function clampPercent(raw: number): number {
  if (Number.isNaN(raw)) return 0
  return Math.min(Math.max(raw, 0), 100)
}

export default function PromptVersionPanel({
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

  const versionsQuery = useQuery({
    queryKey: ['prompt-versions', agentId],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${agentId}/prompt-versions`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return (await r.json()) as PromptVersionsResponse
    },
    enabled,
    staleTime: 15_000,
  })

  // The service already orders newest first (prompt_version_service.py's
  // list_versions, ORDER BY version_number DESC) — never re-sorted here. A
  // client-side sort that agrees with the server today is a client-side
  // sort that silently disagrees with it after a server change nobody
  // thinks to check.
  const versions = versionsQuery.data?.versions ?? []

  // ---- The comparison: two sides, defaulting to newest vs. the one before
  const [sideA, setSideA] = useState<string>('')
  const [sideB, setSideB] = useState<string>('')

  useEffect(() => {
    if (versions.length >= 2 && !sideA && !sideB) {
      setSideA(versions[0].id)
      setSideB(versions[1].id)
    }
  }, [versions, sideA, sideB])

  const diffEnabled = enabled && !!sideA && !!sideB && sideA !== sideB

  const diffQuery = useQuery({
    queryKey: ['prompt-versions-diff', agentId, sideA, sideB],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(
        `${apiBase}/api/v1/agents/${agentId}/prompt-versions/diff?a=${sideA}&b=${sideB}`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return (await r.json()) as PromptVersionDiffResponse
    },
    enabled: diffEnabled,
    staleTime: 15_000,
  })

  // The one error path this region reports into — the page's shared
  // callback, folded into its single existing banner. Both queries' load
  // failures go here; mutation failures get the per-version transient note
  // below instead, never this region-level surface.
  useEffect(() => {
    if (versionsQuery.isError) {
      onError('prompt', (versionsQuery.error as Error).message || 'Failed to load prompt versions.')
    } else if (diffQuery.isError) {
      onError('prompt', (diffQuery.error as Error).message || 'Failed to load the version comparison.')
    } else {
      onError('prompt', null)
    }
  }, [versionsQuery.isError, versionsQuery.error, diffQuery.isError, diffQuery.error, onError])

  // ---- Canary / rollback — per-version busy state, keyed by identifier,
  // never a shared flag, mirroring AdversaryPanel's `busy` map and
  // deploy/page.tsx's savingConfirmations exactly. While one version's
  // action is in flight, every other version's actions stay usable because
  // each row only ever reads its own key.
  const [busy, setBusy] = useState<Record<string, 'canary' | 'rollback'>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const noteTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  useEffect(
    () => () => {
      for (const t of Object.values(noteTimers.current)) clearTimeout(t)
    },
    [],
  )

  const clearBusy = (versionId: string) => {
    setBusy((prev) => {
      const next = { ...prev }
      delete next[versionId]
      return next
    })
  }

  const clearNote = (versionId: string) => {
    setNotes((prev) => {
      if (!(versionId in prev)) return prev
      const next = { ...prev }
      delete next[versionId]
      return next
    })
  }

  const setNote = (versionId: string, message: string) => {
    setNotes((prev) => ({ ...prev, [versionId]: message }))
    clearTimeout(noteTimers.current[versionId])
    noteTimers.current[versionId] = setTimeout(() => {
      setNotes((prev) => {
        const next = { ...prev }
        delete next[versionId]
        return next
      })
      delete noteTimers.current[versionId]
    }, 6000)
  }

  const canaryMutation = useMutation({
    mutationFn: async ({ versionId, percent }: { versionId: string; percent: number }) => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${agentId}/prompt-versions/canary`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ version_id: versionId, percent }),
      })
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        const detail = (body as { detail?: string }).detail ?? `HTTP ${r.status}`
        throw Object.assign(new Error(detail), { versionId })
      }
      return r.json()
    },
    // No optimistic mutation of the list: the route returns the resulting
    // version and the server is the authority on labels, which it moves as
    // a side effect of setting a canary (any prior canary is demoted).
    onSuccess: (_result, { versionId }) => {
      queryClient.invalidateQueries({ queryKey: ['prompt-versions', agentId] })
      clearNote(versionId)
    },
    onError: (err: unknown, { versionId }) => {
      setNote(versionId, (err as Error).message || 'Failed to set the canary share.')
    },
    onSettled: (_result, _err, { versionId }) => clearBusy(versionId),
  })

  const rollbackMutation = useMutation({
    mutationFn: async (versionId: string) => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${agentId}/prompt-versions/rollback`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ version_id: versionId }),
      })
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        const detail = (body as { detail?: string }).detail ?? `HTTP ${r.status}`
        throw Object.assign(new Error(detail), { versionId })
      }
      return r.json()
    },
    onSuccess: (_result, versionId) => {
      queryClient.invalidateQueries({ queryKey: ['prompt-versions', agentId] })
      clearNote(versionId)
    },
    onError: (err: unknown, versionId) => {
      setNote(versionId, (err as Error).message || 'Failed to roll back to this version.')
    },
    onSettled: (_result, _err, versionId) => clearBusy(versionId),
  })

  const handleSetCanary = (versionId: string, percent: number) => {
    setBusy((prev) => ({ ...prev, [versionId]: 'canary' }))
    canaryMutation.mutate({ versionId, percent })
  }

  const handleRollback = (versionId: string) => {
    setBusy((prev) => ({ ...prev, [versionId]: 'rollback' }))
    rollbackMutation.mutate(versionId)
  }

  if (!versionsQuery.data) {
    // On a genuine failure the region reports through onError above and
    // renders nothing else — the page owns the one error banner.
    return versionsQuery.isError ? null : <p className="foot-note">Fetching prompt versions…</p>
  }

  if (versions.length === 0) {
    return (
      <EmptyState
        heading="No prompt versions yet"
        body="Save a change in the soul editor to create the first version."
        linkHref={`/agents/${agentId}/soul`}
        linkLabel="Edit in the soul editor"
      />
    )
  }

  const diffData = diffQuery.data

  return (
    <>
      <div className="scroll-x">
        <Ledger caption="Prompt versions for this agent, newest first, with their label, canary share and creation date.">
          <thead>
            <tr>
              <LedgerColHead numeric>Version</LedgerColHead>
              <LedgerColHead>Label</LedgerColHead>
              <LedgerColHead numeric>Canary</LedgerColHead>
              <LedgerColHead>Created</LedgerColHead>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.id}>
                <LedgerRowHead className="mono">{`v${v.version_number}`}</LedgerRowHead>
                <LedgerCell>{v.label ?? '—'}</LedgerCell>
                <LedgerCell numeric className="mono">
                  {renderCanaryPercent(v.canary_percent)}
                </LedgerCell>
                <LedgerCell className="mono">{formatCreatedAt(v.created_at)}</LedgerCell>
              </tr>
            ))}
          </tbody>
        </Ledger>
      </div>

      {versions.length >= 2 && (
        <div style={{ marginTop: 18 }}>
          <p className="label">Compare</p>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 8 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5, color: 'var(--ink-2)' }}>
              Version A
              <select value={sideA} onChange={(e) => setSideA(e.target.value)}>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>{`v${v.version_number}`}</option>
                ))}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5, color: 'var(--ink-2)' }}>
              Version B
              <select value={sideB} onChange={(e) => setSideB(e.target.value)}>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>{`v${v.version_number}`}</option>
                ))}
              </select>
            </label>
          </div>

          {diffEnabled && !diffData && diffQuery.isFetching && (
            <p className="foot-note">Fetching the comparison…</p>
          )}

          {/* 23-09 adversarial review (finding 21): picking the same version
              for both sides used to leave the LAST successfully-fetched
              (different-pair) diff on screen with nothing indicating it no
              longer matched the current selection — react-query keeps prior
              `data` once a query goes `enabled: false`, it doesn't clear it.
              Gating the render on diffEnabled (not just diffData) plus this
              explicit message closes that gap honestly. */}
          {!diffEnabled && sideA && sideB && sideA === sideB && (
            <p className="foot-note">Select two different versions to compare.</p>
          )}

          {diffEnabled && diffData && (
            <div
              role="status"
              aria-live="polite"
              style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 14 }}
            >
              {SOUL_FIELD_ROWS.map(({ key, label }) => {
                const field = diffData.fields[key]
                return (
                  <div key={key}>
                    <p className="label" style={{ marginBottom: 6 }}>
                      {`${label} · ${field.changed ? 'changed' : 'unchanged'}`}
                    </p>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                      <div className="well">{renderFieldSide(field.a)}</div>
                      <div className="well">{renderFieldSide(field.b)}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      <div style={{ marginTop: 18, display: 'flex', flexDirection: 'column' }}>
        {versions.map((v) => (
          <VersionActions
            key={v.id}
            version={v}
            busy={busy[v.id] ?? null}
            note={notes[v.id] ?? null}
            onSetCanary={handleSetCanary}
            onRollback={handleRollback}
          />
        ))}
      </div>
    </>
  )
}

// Per-version staged actions — the local `staged`/`percent` state lives
// here, per version, exactly as PendingConfirmationRow (deploy/page.tsx)
// and AdversaryPanel's FindingContain keep their own `staged` local to each
// row rather than a page-level map. Only busy/note are lifted to the
// parent, keyed by identifier.
function VersionActions({
  version,
  busy,
  note,
  onSetCanary,
  onRollback,
}: {
  version: PromptVersionRow
  busy: 'canary' | 'rollback' | null
  note: string | null
  onSetCanary: (versionId: string, percent: number) => void
  onRollback: (versionId: string) => void
}) {
  const [staged, setStaged] = useState<'canary' | 'rollback' | null>(null)
  const [percent, setPercent] = useState<number>(version.canary_percent ?? 0)
  // 23-09 adversarial review (finding 20): this component is keyed by
  // version.id (page-stable across refetches), so useState's initializer
  // only ran once. Setting a canary on one version demotes any prior
  // canary elsewhere server-side — the Ledger row for the demoted version
  // correctly re-renders from the refetched `canary_percent` prop, but this
  // input's local `percent` never re-synced, silently disagreeing with the
  // Ledger cell sitting right next to it. Re-sync whenever the server value
  // actually changes; an in-progress edit is untouched otherwise, since the
  // effect only re-fires when this specific version's own canary_percent
  // changes, not on every refetch.
  useEffect(() => {
    setPercent(version.canary_percent ?? 0)
  }, [version.canary_percent])
  const isBusy = busy !== null
  const canaryQuestionId = `version-${version.id}-canary-confirm-q`
  const rollbackQuestionId = `version-${version.id}-rollback-confirm-q`

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: '12px 0',
        borderTop: '1px solid var(--hairline-soft)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span className="mono" style={{ fontSize: 12.5, color: 'var(--ink-2)' }}>{`v${version.version_number}`}</span>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--ink-2)' }}>
          Canary %
          <input
            type="number"
            min={0}
            max={100}
            step={1}
            value={percent}
            disabled={isBusy}
            onChange={(e) => setPercent(clampPercent(Number(e.target.value)))}
            style={{ width: 64 }}
            aria-label={`Canary percent for version ${version.version_number}`}
          />
        </label>

        <Btn
          variant="ghost"
          disabled={isBusy}
          aria-label={`Set canary for version ${version.version_number}`}
          onClick={() => setStaged('canary')}
        >
          {busy === 'canary' ? 'Setting…' : 'Set canary'}
        </Btn>
        <Btn
          variant="ghost"
          disabled={isBusy}
          aria-label={`Roll back to version ${version.version_number}`}
          onClick={() => setStaged('rollback')}
        >
          {busy === 'rollback' ? 'Rolling back…' : 'Roll back'}
        </Btn>
      </div>

      {note && (
        <p className="help" role="status">
          {note}
        </p>
      )}

      {staged === 'canary' && (
        <div className="cap-confirm">
          <p className="cap-confirm-q" id={canaryQuestionId}>
            {`Route ${percent}% of turns to version ${version.version_number} now?`}
          </p>
          <div className="cap-confirm-actions">
            <Btn
              variant="ghost"
              autoFocus
              disabled={isBusy}
              aria-describedby={canaryQuestionId}
              onClick={() => {
                setStaged(null)
                onSetCanary(version.id, percent)
              }}
            >
              {`Set ${percent}% canary`}
            </Btn>
            <Btn variant="ghost" disabled={isBusy} onClick={() => setStaged(null)}>
              Cancel
            </Btn>
          </div>
        </div>
      )}

      {staged === 'rollback' && (
        <div className="cap-confirm">
          <p className="cap-confirm-q" id={rollbackQuestionId}>
            {`Roll back to version ${version.version_number}? This creates a new version with that content. Nothing is deleted.`}
          </p>
          <div className="cap-confirm-actions">
            <Btn
              variant="ghost"
              autoFocus
              disabled={isBusy}
              aria-describedby={rollbackQuestionId}
              onClick={() => {
                setStaged(null)
                onRollback(version.id)
              }}
            >
              Yes, roll back
            </Btn>
            <Btn variant="ghost" disabled={isBusy} onClick={() => setStaged(null)}>
              Cancel
            </Btn>
          </div>
        </div>
      )}
    </div>
  )
}
