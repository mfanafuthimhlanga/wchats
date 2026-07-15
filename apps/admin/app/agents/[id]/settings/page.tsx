'use client'
import { use, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Btn from '../../../components/gotham/Btn'
import { useGate } from '../../../components/gotham/GateProvider'

/**
 * Settings — `/agents/[id]/settings` (UI-SPEC S6.9, UI2-07, ported from
 * prototypes/gotham/settings.html). The last dusk sub-route retired.
 *
 * MUST-FIX 4 (UI-SPEC S6.9 / S10.8): settings.html's "Delete permanently"
 * button only ever wrote a fake no-op status line claiming nothing had
 * really happened — it never called anything. That fake status line is
 * DROPPED entirely. This port wires the button to the REAL, already-existing
 * `DELETE /api/v1/agents/{id}` endpoint (apps/api/app/api/v1/agents.py:208,
 * a soft-delete scoped by tenant_id) and redirects to /agents on success —
 * the same "real action, not demo theatre" fix already applied to
 * deploy.html's gate-test buttons (20-11).
 *
 * WARDEN's law (ported verbatim, UI-SPEC S6.9: "port this exactly, it is
 * intentional"): arming the danger-zone confirm panel sets
 * `data-gate="blocked"` on the whole console via GateProvider — the same
 * single writer the gatebar and deploy page use — never a page-local red
 * box. Disarming (Cancel/Escape) restores 'open'.
 *
 * Rule 2 fix (missing error handling, found during this port): the
 * prototype's destroy handler disarmed the panel and wrote its status line
 * synchronously, with nothing async underneath. A real DELETE call can
 * fail (network, auth, server error) — this port keeps the panel open and
 * armed while the request is in flight, disables Cancel/Delete during that
 * window, and surfaces a real failure message so the operator can retry
 * instead of silently losing the confirm state.
 */

interface AgentDetail {
  id: string
  name: string
  neon_project_id: string | null
}

// Minimal document shape — only the array length is used, for the danger
// zone's blast-radius sentence.
interface AgentDocument {
  id: string
}

export default function SettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const queryClient = useQueryClient()
  const { setGate } = useGate()

  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  // Danger zone state
  const [armed, setArmed] = useState(false)
  const [confirmValue, setConfirmValue] = useState('')
  const confirmInputRef = useRef<HTMLInputElement>(null)
  const armBtnRef = useRef<HTMLButtonElement>(null)

  // Copy-to-clipboard state (shared between the two .fact rows)
  const [copiedField, setCopiedField] = useState<string | null>(null)

  // ---------------------------------------------------------------------------
  // Load — TanStack Query. Shares the ['agent', id] cache with the layout
  // and the other operations-room pages.
  // ---------------------------------------------------------------------------

  const agentQuery = useQuery<AgentDetail>({
    queryKey: ['agent', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json()
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 30_000,
  })

  const documentsQuery = useQuery<AgentDocument[]>({
    queryKey: ['agent', id, 'documents'],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/documents`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json()
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 30_000,
  })

  const documentCount = documentsQuery.data?.length ?? 0

  const loadError =
    isLoaded && !isSignedIn
      ? 'Not authenticated. Please sign in.'
      : agentQuery.isError
        ? 'Failed to load agent. Please refresh.'
        : null

  // ---------------------------------------------------------------------------
  // Danger zone — arm/disarm drives the console-wide gate (WARDEN law).
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (armed) confirmInputRef.current?.focus()
  }, [armed])

  const disarm = () => {
    setArmed(false)
    setConfirmValue('')
    setGate('open')
    armBtnRef.current?.focus()
  }

  const arm = () => {
    setArmed(true)
    setGate('blocked')
  }

  const toggleArm = () => (armed ? disarm() : arm())

  // ---------------------------------------------------------------------------
  // Real delete — DELETE /api/v1/agents/{id}, then redirect to /agents.
  // ---------------------------------------------------------------------------

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
    },
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ['agent', id] })
      setGate('open')
      router.push('/agents')
    },
  })

  const handleDelete = () => {
    if (confirmValue.trim() !== id) return
    deleteMutation.mutate()
  }

  const statusText = deleteMutation.isError
    ? 'Delete failed — check connection and try again.'
    : deleteMutation.isPending
      ? `Deleting ${id}…`
      : armed
        ? `Armed. Type ${id} to confirm.`
        : ''

  // ---------------------------------------------------------------------------
  // Copy-to-clipboard — matches settings.html's [data-copy] buttons.
  // ---------------------------------------------------------------------------

  const copyValue = (field: string, value: string) => {
    const done = () => {
      setCopiedField(field)
      setTimeout(() => setCopiedField((f) => (f === field ? null : f)), 1400)
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done, done)
    } else {
      done()
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="page">
      <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />

      <header className="page-head">
        <h1>Settings</h1>
        <p className="sub">
          The agent&rsquo;s record and its lifecycle. Its behaviour lives in soul, its knowledge lives in ingest.
        </p>
      </header>

      {loadError && (
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
          {loadError}
        </div>
      )}

      <section aria-labelledby="record-h">
        <div className="section-head">
          <h2 className="label" id="record-h">
            Record
          </h2>
        </div>

        <div className="zone tint">
          <div className="field">
            <label htmlFor="f-agent-name">Agent name</label>
            <input type="text" id="f-agent-name" value={agentQuery.data?.name ?? ''} disabled />
            <p className="help">
              Renaming arrives with multi-agent workspaces. The name is baked into the soul and the widget until
              then.
            </p>
          </div>

          <dl className="facts">
            <div className="fact">
              <dt className="label">Agent id</dt>
              <dd>
                <span className="mono fact-value">{id}</span>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => copyValue('agent-id', id)}>
                  {copiedField === 'agent-id' ? 'Copied' : 'Copy'}
                </button>
              </dd>
            </div>

            <div className="fact">
              <dt className="label">Neon project id</dt>
              <dd>
                <span className="mono fact-value">{agentQuery.data?.neon_project_id ?? '—'}</span>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={!agentQuery.data?.neon_project_id}
                  onClick={() => copyValue('neon-id', agentQuery.data?.neon_project_id ?? '')}
                >
                  {copiedField === 'neon-id' ? 'Copied' : 'Copy'}
                </button>
              </dd>
            </div>
          </dl>

          <p className="help fact-note">
            This agent owns its own Neon project. Every eval run branches from it and throws the branch away.
          </p>
        </div>
      </section>

      {/* ── the danger zone: one hairline above it, and nothing else ────── */}
      <section className="section" aria-labelledby="danger-h">
        <h2 className="label" id="danger-h">
          Danger zone
        </h2>

        <p className="voice danger-voice">
          Deleting this agent destroys its <span className="num">{documentCount}</span> documents, its eval suite,
          its red team history and the record of every session it ever held, and none of it comes back.
        </p>

        <div className="danger-act">
          <button
            ref={armBtnRef}
            type="button"
            className="btn btn-seal"
            aria-expanded={armed}
            aria-controls="confirm"
            onClick={toggleArm}
          >
            Delete agent
          </button>
        </div>

        {armed && (
          <div className="confirm" id="confirm">
            <div>
              <label htmlFor="f-confirm">Type the agent id to confirm</label>
              <div className="confirm-row">
                <input
                  ref={confirmInputRef}
                  type="text"
                  id="f-confirm"
                  value={confirmValue}
                  onChange={(e) => setConfirmValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                      e.preventDefault()
                      disarm()
                    }
                  }}
                  placeholder={id}
                  autoComplete="off"
                  spellCheck={false}
                  aria-describedby="danger-status"
                  disabled={deleteMutation.isPending}
                />
              </div>
            </div>

            <div className="confirm-actions">
              <Btn
                variant="seal"
                disabled={confirmValue.trim() !== id || deleteMutation.isPending}
                onClick={handleDelete}
              >
                {deleteMutation.isPending ? 'Deleting…' : 'Delete permanently'}
              </Btn>
              <Btn variant="ghost" disabled={deleteMutation.isPending} onClick={disarm}>
                Cancel
              </Btn>
            </div>
          </div>
        )}

        <p className="mono danger-status" id="danger-status" role="status" data-armed={armed ? 'true' : undefined}>
          {statusText}
        </p>
      </section>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page-scoped CSS — classes with no equivalent in the shared globals.css
// Gotham port (they were page-local `<style>` rules in settings.html, not
// app.css), following the same static dangerouslySetInnerHTML pattern used
// by soul/page.tsx and deploy/page.tsx.
// ---------------------------------------------------------------------------
const PAGE_CSS = `
  .page { max-width: 760px; }

  .zone .field:last-child { margin-bottom: 0; }

  .facts { margin: 0; padding: 0; }
  .fact {
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 13px 0;
    border-top: 1px solid var(--hairline-soft);
  }
  .fact dt { margin: 0; }
  .fact dd {
    margin: 0; display: flex; align-items: center; gap: 12px;
    min-width: 0;
  }
  .fact-value { font-size: 13px; color: var(--ink); }
  .fact-note { margin-top: 12px; }

  .btn-sm { padding: 5px 10px; font-size: 12px; }

  .danger-voice { margin-top: 14px; font-size: 16px; max-width: 60ch; }
  .danger-act { margin-top: 20px; }

  .confirm { margin-top: 20px; display: grid; gap: 14px; max-width: 460px; }
  .confirm-row { display: flex; align-items: center; gap: 10px; }
  .confirm-row input { font-family: var(--mono); font-size: 13px; }
  .confirm-actions { display: flex; gap: 10px; }
  .danger-status { margin-top: 14px; font-size: 12px; color: var(--ink-3); min-height: 1.4em; }
  .danger-status[data-armed="true"] { color: var(--seal-hot); }

  @media (max-width: 620px) {
    .fact { flex-direction: column; align-items: flex-start; gap: 8px; }
    .confirm-row { flex-wrap: wrap; }
  }
`
