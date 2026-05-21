'use client'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import Link from 'next/link'

import AgentCard from '../components/AgentCard'

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
// Inline style constants
// ---------------------------------------------------------------------------

const primaryButtonInline: React.CSSProperties = {
  background: 'var(--accent)',
  color: '#fff',
  padding: '10px 18px',
  borderRadius: 'var(--radius-xs)',
  fontWeight: 600,
  fontSize: '14px',
  textDecoration: 'none',
  display: 'inline-block',
}

const secondaryButtonInline: React.CSSProperties = {
  background: 'transparent',
  color: 'var(--text-2)',
  padding: '8px 14px',
  borderRadius: 'var(--radius-xs)',
  fontWeight: 500,
  fontSize: '13px',
  border: '1px solid var(--border)',
  cursor: 'pointer',
  display: 'inline-block',
}

// ---------------------------------------------------------------------------
// AgentsDashboardPage
// ---------------------------------------------------------------------------

export default function AgentsDashboardPage() {
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

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
    enabled: isLoaded && !!isSignedIn,
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
  const isLoading = agentsQuery.isPending || !isLoaded
  const agents = agentsQuery.data ?? []

  // Compute human-friendly error message
  let loadError: string | null = null
  if (isLoaded && !isSignedIn) {
    loadError = 'Not authenticated. Please sign in.'
  } else if (agentsQuery.isError) {
    const msg = agentsQuery.error?.message ?? ''
    const isNetworkError =
      agentsQuery.error instanceof TypeError && msg.toLowerCase().includes('fetch')
    loadError = isNetworkError
      ? 'Cannot reach the API server. Make sure the backend is running on ' +
        (apiBase || 'http://localhost:8000') +
        ' and refresh.'
      : msg || 'Failed to load agents. Please refresh.'
  }

  return (
    <div
      style={{
        padding: '40px 32px',
        maxWidth: '1180px',
        margin: '0 auto',
      }}
    >
      {/* Heading row */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '24px',
        }}
      >
        <h1
          style={{
            fontSize: '24px',
            fontWeight: 700,
            color: 'var(--text-1)',
            margin: 0,
          }}
        >
          Your agents
        </h1>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          <button
            onClick={() => agentsQuery.refetch()}
            disabled={agentsQuery.isFetching}
            style={{
              ...secondaryButtonInline,
              opacity: agentsQuery.isFetching ? 0.6 : 1,
            }}
          >
            {agentsQuery.isFetching ? 'Refreshing…' : 'Refresh'}
          </button>
          <Link href="/agents/new" style={primaryButtonInline}>
            + Create agent
          </Link>
        </div>
      </div>

      {/* Error alert — exact pattern from soul/page.tsx lines 337-351 */}
      {loadError && (
        <div
          role="alert"
          style={{
            padding: '12px 16px',
            marginBottom: '20px',
            background: 'var(--red-bg)',
            border: '1px solid rgba(192,57,43,0.3)',
            borderRadius: 'var(--radius-xs)',
            fontSize: '14px',
            color: 'var(--red)',
          }}
        >
          {loadError}
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <p style={{ color: 'var(--text-3)' }}>Loading agents…</p>
      )}

      {/* Empty state */}
      {!isLoading && agents.length === 0 && !loadError && (
        <div style={{ textAlign: 'center', padding: '80px 0' }}>
          <p
            style={{
              color: 'var(--text-3)',
              marginBottom: '20px',
              fontSize: '15px',
            }}
          >
            No agents yet.
          </p>
          <Link href="/agents/new" style={primaryButtonInline}>
            Create your first agent
          </Link>
        </div>
      )}

      {/* Agent grid */}
      {!isLoading && agents.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: '20px',
          }}
        >
          {agents.map((a) => (
            <AgentCard key={a.id} {...a} onDelete={handleDelete} />
          ))}
        </div>
      )}
    </div>
  )
}
