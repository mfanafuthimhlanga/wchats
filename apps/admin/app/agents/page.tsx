'use client'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Link from 'next/link'

import AgentCard from '../components/AgentCard'
import EmptyState from '../components/gotham/EmptyState'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type AgentSummary = {
  id: string
  tenant_id: string
  name: string
  role: string
  status: string
  neon_project_id: string | null
  schema_version: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// The prototype's page-head sub line ("Three agents on the bench. Only one
// has cleared its gate, and only that one is answering customers.") is
// authored, static copy — this is the dynamic equivalent for real data.
function subCopy(total: number, liveCount: number): string {
  if (total === 0) {
    return 'Nothing on the bench yet. Provision your first agent to get started.'
  }
  const agentWord = total === 1 ? 'agent' : 'agents'
  if (liveCount === 0) {
    return `${total} ${agentWord} on the bench. None have cleared the gate yet — none are answering customers.`
  }
  const clearedWord = liveCount === 1 ? 'has' : 'have'
  const answeringWord = liveCount === 1 ? 'is' : 'are'
  return `${total} ${agentWord} on the bench. ${liveCount} ${clearedWord} cleared the gate, and only ${liveCount === 1 ? 'that one' : 'those'} ${answeringWord} answering customers.`
}

// ---------------------------------------------------------------------------
// AgentsDashboardPage
// ---------------------------------------------------------------------------

export default function AgentsDashboardPage() {
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const isDemoMode = process.env.NEXT_PUBLIC_DEMO === 'true'

  const DEMO_AGENTS: AgentSummary[] = [
    { id: 'demo-1', tenant_id: 'demo', name: 'Acme Support', role: 'support', status: 'ready', neon_project_id: 'neon-1', schema_version: '1', created_at: '2026-05-01T00:00:00Z' },
    { id: 'demo-2', tenant_id: 'demo', name: 'Hillbrow Realty', role: 'helpdesk', status: 'testing', neon_project_id: 'neon-2', schema_version: '1', created_at: '2026-05-15T00:00:00Z' },
  ]

  const agentsQuery = useQuery({
    queryKey: ['agents'],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')

      // Best-effort tenant provisioning — webhooks don't fire in local dev.
      // A provision failure (e.g. server not running, tenant already exists)
      // is logged but does NOT block the agent list fetch.
      try {
        const provRes = await fetch(`${apiBase}/me/provision`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!provRes.ok && provRes.status !== 200 && provRes.status !== 201) {
          console.warn(`[agents] /me/provision returned HTTP ${provRes.status} — continuing`)
        }
      } catch (provErr) {
        // Network-level failure (ERR_CONNECTION_REFUSED) — API server may not be running.
        // Log for debugging; do not surface to the user yet (the agent list fetch will fail
        // too and produce a clearer error message at that point).
        console.warn('[agents] /me/provision fetch failed (server unreachable?):', provErr)
      }

      const res = await fetch(`${apiBase}/api/v1/agents`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        const body = await res.text().catch(() => '')
        const isNetworkError =
          res.status === 0 || res.type === 'error'
        if (isNetworkError) {
          throw new Error(
            'Cannot reach the API server. Make sure the backend is running on ' +
              (apiBase || 'http://localhost:8000') +
              ' and refresh.',
          )
        }
        throw new Error(`HTTP ${res.status}: ${body}`)
      }
      const data = await res.json()
      if (!Array.isArray(data?.agents)) {
        throw new Error('Unexpected response shape from /api/v1/agents')
      }
      return data.agents as AgentSummary[]
    },
    enabled: !isDemoMode && isLoaded && !!isSignedIn,
    staleTime: 30_000,
  })

  // Deletes an agent via DELETE /api/v1/agents/{id}. Removes from cache
  // on a 204 response. Throws on failure so the card can surface an inline
  // error next to its own Delete button (we do not optimistically remove —
  // the card stays visible until the server confirms the delete).
  const handleDelete = async (agentId: string) => {
    const token = await getToken()
    if (!token) throw new Error('Not authenticated. Please sign in again.')

    const res = await fetch(`${apiBase}/api/v1/agents/${agentId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    })
    if (res.status !== 204) {
      throw new Error(`Delete failed (HTTP ${res.status})`)
    }
    // Refetch after delete so the cache reflects the server state
    await agentsQuery.refetch()
  }

  // Derive display state from the query
  const isLoading = !isDemoMode && (agentsQuery.isPending || !isLoaded)
  const agents = isDemoMode ? DEMO_AGENTS : (agentsQuery.data ?? [])

  // Compute human-friendly error message
  let loadError: string | null = null
  if (!isDemoMode && isLoaded && !isSignedIn) {
    loadError = 'Not authenticated. Please sign in.'
  } else if (!isDemoMode && agentsQuery.isError) {
    const msg = agentsQuery.error?.message ?? ''
    const isNetworkError =
      agentsQuery.error instanceof TypeError && msg.toLowerCase().includes('fetch')
    loadError = isNetworkError
      ? 'Cannot reach the API server. Make sure the backend is running on ' +
        (apiBase || 'http://localhost:8000') +
        ' and refresh.'
      : msg || 'Failed to load agents. Please refresh.'
  }

  const liveCount = agents.filter((a) => a.status === 'ready').length

  return (
    <div className="page">
      {/* Header bar (§6.2 `.page-head`) — h1, sub line, "New agent" CTA */}
      <div className="page-head">
        <div className="row">
          <div>
            <h1>Agents</h1>
            <p className="sub">{subCopy(agents.length, liveCount)}</p>
          </div>
          <Link className="btn btn-primary" href="/agents/new">
            New agent
          </Link>
        </div>
      </div>

      {/* Error alert */}
      {loadError && (
        <div
          role="alert"
          style={{
            padding: '12px 16px',
            marginBottom: '20px',
            background: 'var(--fail-dim)',
            border: '1px solid color-mix(in oklch, var(--fail) 40%, transparent)',
            borderRadius: 'var(--r-control)',
            fontSize: '14px',
            color: 'var(--fail)',
          }}
        >
          {loadError}
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <p className="mono" style={{ color: 'var(--ink-3)' }}>Loading agents…</p>
      )}

      {/* Empty state (§12 copywriting contract) */}
      {!isLoading && agents.length === 0 && !loadError && (
        <EmptyState
          heading="No agents yet"
          body="Provision your first agent to start ingesting documents and shipping a verified assistant."
          linkHref="/agents/new"
          linkLabel="New agent"
        />
      )}

      {/* Agent grid (§6.2 `.agents`) */}
      {!isLoading && agents.length > 0 && (
        <>
          <h2 className="vh">All agents</h2>
          <div className="agents">
            {agents.map((a) => (
              <AgentCard key={a.id} {...a} onDelete={isDemoMode ? undefined : handleDelete} disableNavigation={isDemoMode} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
