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
// AgentsDashboardPage
// ---------------------------------------------------------------------------

export default function AgentsDashboardPage() {
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const isDemoMode = process.env.NEXT_PUBLIC_DEMO === 'true'

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
  const agents = agentsQuery.data ?? []

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

  const greeting = getTimeGreeting()

  return (
    <div style={{ background: 'transparent' }}>
      {/* Header bar — greeting left, CTA right */}
      <div
        style={{
          backgroundImage: `
            radial-gradient(ellipse 40% 80% at 100% 50%, rgba(244, 116, 140, 0.08) 0%, transparent 60%),
            radial-gradient(ellipse 30% 60% at 0% 0%, rgba(183, 154, 224, 0.06) 0%, transparent 60%)`,
          padding: '40px 48px 32px',
          position: 'relative',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: '32px', maxWidth: '1400px' }}>
          <div>
            <p style={{ fontSize: '10.5px', fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-3)', margin: '0 0 6px 0' }}>
              {greeting.split(' ')[0].toUpperCase()} · {agents.length} {agents.length === 1 ? 'AGENT' : 'AGENTS'}
            </p>
            <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 400, fontVariationSettings: '"opsz" 144, "SOFT" 50', fontSize: '34px', letterSpacing: '-0.022em', lineHeight: 1.1, color: 'var(--text-1)', margin: 0 }}>
              {greeting},{' '}
              <em style={{ fontStyle: 'italic', fontWeight: 300, color: 'var(--accent)', fontVariationSettings: '"opsz" 144, "SOFT" 100' }}>
                there
              </em>
            </h1>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
            <button
              onClick={() => agentsQuery.refetch()}
              disabled={agentsQuery.isFetching}
              style={{
                background: 'transparent',
                color: 'var(--text-2)',
                padding: '8px 14px',
                borderRadius: 'var(--radius-xs)',
                fontWeight: 500,
                fontSize: '13px',
                border: '1px solid var(--border)',
                cursor: 'pointer',
                opacity: agentsQuery.isFetching ? 0.6 : 1,
              }}
            >
              {agentsQuery.isFetching ? 'Refreshing…' : 'Refresh'}
            </button>
            <Link
              href="/agents/new"
              style={{
                background: 'var(--accent)',
                color: '#0B0717',
                padding: '10px 18px',
                borderRadius: 'var(--radius-sm)',
                fontWeight: 600,
                fontSize: '14px',
                textDecoration: 'none',
                display: 'inline-block',
              }}
            >
              New agent
            </Link>
          </div>
        </div>
      </div>

      {/* Glass stat cards row */}
      <div style={{ padding: '0 48px 32px', display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px', maxWidth: '1400px' }}>
        {[
          { label: 'Total agents', value: agents.length.toString(), unit: '', color: 'var(--accent)' },
          { label: 'Avg faithfulness', value: '—', unit: '', color: 'var(--lilac)' },
          { label: '7d conversations', value: '—', unit: '', color: 'var(--cyan)' },
          { label: 'Red team blocks', value: '0', unit: '', color: 'var(--amber)' },
        ].map(({ label, value, color }) => (
          <div key={label} style={{
            background: 'var(--glass-bg)',
            backdropFilter: 'var(--glass-blur)',
            WebkitBackdropFilter: 'var(--glass-blur)',
            border: '1px solid var(--glass-border)',
            borderRadius: 'var(--radius-md)',
            padding: '22px 24px',
            position: 'relative',
            overflow: 'hidden',
          }}>
            <div style={{ position: 'absolute', top: 0, right: 0, width: '80px', height: '80px', background: `radial-gradient(circle at 100% 0%, ${color}22 0%, transparent 70%)`, pointerEvents: 'none' }} />
            <p style={{ fontSize: '10.5px', fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-3)', margin: '0 0 12px 0', position: 'relative', zIndex: 1 }}>
              {label}
            </p>
            <p style={{ fontFamily: 'var(--font-mono)', fontSize: '30px', fontWeight: 600, color: 'var(--text-1)', letterSpacing: '-0.025em', lineHeight: 1, margin: 0, position: 'relative', zIndex: 1 }}>
              {value}
            </p>
          </div>
        ))}
      </div>

      {/* Content area */}
      <div style={{ padding: '0 48px 56px', maxWidth: '1400px' }}>
        {/* Error alert */}
        {loadError && (
          <div
            role="alert"
            style={{ padding: '12px 16px', marginBottom: '20px', background: 'var(--red-bg)', border: '1px solid rgba(192,57,43,0.3)', borderRadius: 'var(--radius-xs)', fontSize: '14px', color: 'var(--red)' }}
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
            <div style={{ display: 'inline-flex', alignItems: 'center', background: 'var(--glass-bg)', backdropFilter: 'var(--glass-blur)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-pill)', padding: '4px 14px', marginBottom: '20px' }}>
              <span style={{ fontSize: '10.5px', fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-3)' }}>NO AGENTS YET</span>
            </div>
            <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 400, fontVariationSettings: '"opsz" 144, "SOFT" 30', fontSize: '24px', color: 'var(--text-1)', margin: '0 0 8px 0' }}>
              Build your first{' '}
              <em style={{ fontStyle: 'italic', fontWeight: 300, color: 'var(--accent)', fontVariationSettings: '"opsz" 144, "SOFT" 100' }}>agent</em>
            </h2>
            <p style={{ color: 'var(--text-3)', marginBottom: '24px', fontSize: '15px' }}>
              Create a customer service agent and deploy it in minutes.
            </p>
            <Link href="/agents/new" style={{ background: 'var(--accent)', color: '#0B0717', padding: '10px 18px', borderRadius: 'var(--radius-sm)', fontWeight: 600, fontSize: '14px', textDecoration: 'none', display: 'inline-block' }}>
              New agent
            </Link>
          </div>
        )}

        {/* Agent grid */}
        {!isLoading && agents.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
            {agents.map((a) => (
              <AgentCard key={a.id} {...a} onDelete={handleDelete} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
