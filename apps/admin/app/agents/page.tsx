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
// Helpers
// ---------------------------------------------------------------------------

function getTimeGreeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

// ---------------------------------------------------------------------------
// Inline style constants
// ---------------------------------------------------------------------------

const primaryButtonInline: React.CSSProperties = {
  background: 'var(--accent)',
  color: '#0B0717',
  padding: '10px 18px',
  borderRadius: 'var(--radius-sm)',
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

  const greeting = getTimeGreeting()

  return (
    <div style={{ background: 'transparent' }}>
      {/* Greeting strip — full-width band with radial gradients */}
      <div
        style={{
          background: 'var(--bg)',
          backgroundImage: `
            radial-gradient(ellipse 60% 40% at 80% 0%, rgba(244,116,140,0.08) 0%, transparent 50%),
            radial-gradient(ellipse 40% 30% at 0% 60%, rgba(183,154,224,0.06) 0%, transparent 50%)`,
          padding: '32px 32px 24px',
        }}
      >
        <p
          style={{
            fontSize: '10.5px',
            fontWeight: 600,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: 'var(--text-3)',
            margin: '0 0 8px 0',
          }}
        >
          {greeting.split(' ')[0].toUpperCase()} · {agents.length} {agents.length === 1 ? 'AGENT' : 'AGENTS'}
        </p>
        <h1
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 400,
            fontVariationSettings: '"opsz" 144, "SOFT" 30',
            fontSize: '32px',
            letterSpacing: '-0.025em',
            lineHeight: 1.1,
            color: 'var(--text-1)',
            margin: 0,
          }}
        >
          {greeting},{' '}
          <em
            style={{
              fontStyle: 'italic',
              fontWeight: 300,
              color: 'var(--accent)',
              fontVariationSettings: '"opsz" 144, "SOFT" 100',
            }}
          >
            there
          </em>
        </h1>
      </div>

      {/* Content area */}
      <div
        style={{
          padding: '32px 32px',
          maxWidth: '1180px',
          margin: '0 auto',
        }}
      >
        {/* Action row */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            alignItems: 'center',
            marginBottom: '24px',
            gap: '10px',
          }}
        >
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
            Create agent
          </Link>
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
            {/* Coral eyebrow pill */}
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                background: 'var(--glass-bg)',
                backdropFilter: 'var(--glass-blur)',
                border: '1px solid var(--glass-border)',
                borderRadius: 'var(--radius-pill)',
                padding: '4px 14px',
                marginBottom: '20px',
              }}
            >
              <span
                style={{
                  fontSize: '10.5px',
                  fontWeight: 600,
                  letterSpacing: '0.12em',
                  textTransform: 'uppercase',
                  color: 'var(--text-3)',
                }}
              >
                NO AGENTS YET
              </span>
            </div>
            <h2
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 400,
                fontVariationSettings: '"opsz" 144, "SOFT" 30',
                fontSize: '24px',
                color: 'var(--text-1)',
                margin: '0 0 8px 0',
              }}
            >
              Build your first{' '}
              <em
                style={{
                  fontStyle: 'italic',
                  fontWeight: 300,
                  color: 'var(--accent)',
                  fontVariationSettings: '"opsz" 144, "SOFT" 100',
                }}
              >
                agent
              </em>
            </h2>
            <p
              style={{
                color: 'var(--text-3)',
                marginBottom: '24px',
                fontSize: '15px',
              }}
            >
              Create a customer service agent and deploy it in minutes.
            </p>
            <Link href="/agents/new" style={primaryButtonInline}>
              Create agent
            </Link>
          </div>
        )}

        {/* Agent grid */}
        {!isLoading && agents.length > 0 && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: '16px',
            }}
          >
            {agents.map((a) => (
              <AgentCard key={a.id} {...a} onDelete={handleDelete} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
