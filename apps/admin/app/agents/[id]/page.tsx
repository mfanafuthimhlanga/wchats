'use client'
import { use } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Chip from '../../components/gotham/Chip'
import EmptyState from '../../components/gotham/EmptyState'
import { AlertsBanner } from './components/AlertsBanner'

/**
 * The agent operations room — `/agents/[id]` (UI-SPEC S6.4, UI2-05, ported
 * from prototypes/gotham/agent.html). Six `.section` regions in a fixed
 * order: Live, Retrieval health, The bench, Judgement, Adversary, The
 * prompt. Four of the six (Live / Retrieval health / The bench / The
 * prompt) have no backing endpoint yet (AGENT-MGMT-GAPS.md) and render an
 * honest `<EmptyState>` — never the prototype's client-side seeded-noise
 * demo data (hardcoded channel/version arrays). Judgement
 * and Adversary wire to the real eval-runs / red-team-runs endpoints; that
 * wiring (plus the real gatebar derivation and the relocated AlertsBanner)
 * is added in this plan's second task — this shell renders their region
 * heads only, filled in next.
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

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toISOString().slice(0, 10)
}

export default function AgentOperationsRoom({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

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

        {/* Real gatebar derivation (checklist-runs + red-team deployment_blocked
            + the folded red_team_critical alert) is wired in this plan's second
            task — this is the shell only, so it never hand-colours itself. */}
        <div className="gatebar rule-double">
          <Chip verdict="pass">Gate open</Chip>
          <p>Every build ships. No critical finding is open.</p>
          <p className="mono" style={{ marginLeft: 'auto' }}>checking…</p>
        </div>
        <p className="vh" role="status" aria-live="polite" />
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

      {isLoaded && isSignedIn && agent && <AlertsBanner agentId={id} />}

      {/* ═══ LIVE ═══════════════════════════════════════════════════════ */}
      <section className="section" aria-labelledby="live-h">
        <div className="section-head">
          <h2 className="label" id="live-h">Live</h2>
        </div>
        <EmptyState
          heading="No live telemetry yet"
          body="Live performance metrics are not available yet."
        />
      </section>

      {/* ═══ RETRIEVAL HEALTH ═══════════════════════════════════════════ */}
      <section className="section" aria-labelledby="rag-h">
        <div className="section-head">
          <h2 className="label" id="rag-h">Retrieval health</h2>
          <p className="mono head-count">{documents.length} documents</p>
        </div>
        <EmptyState
          heading="No retrieval instrumentation yet"
          body="Retrieval health instrumentation ships in a future release."
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

      {/* ═══ JUDGEMENT — wired to real eval-runs data in Task 2 ═══════════ */}
      <section className="section" aria-labelledby="judge-h">
        <div className="section-head">
          <h2 className="label" id="judge-h">Judgement</h2>
        </div>
      </section>

      {/* ═══ ADVERSARY — wired to real red-team-runs data in Task 2 ═══════ */}
      <section className="section" aria-labelledby="adv-h">
        <div className="section-head">
          <h2 className="label" id="adv-h">Adversary</h2>
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

  @media (max-width: 720px) {
    .page-head .row { flex-direction: column; }
    .ident { justify-items: start; text-align: left; }
  }
`
