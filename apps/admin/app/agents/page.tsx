'use client'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import { useState } from 'react'
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

  const greeting = getTimeGreeting()
  const [activeFilter, setActiveFilter] = useState<'All' | 'Live' | 'Testing' | 'Draft'>('All')

  const liveCount = agents.filter(a => a.status === 'ready').length
  const testingCount = agents.filter(a => a.status === 'testing').length
  const draftCount = agents.filter(a => a.status !== 'ready' && a.status !== 'testing').length

  const filteredAgents = activeFilter === 'All'
    ? agents
    : activeFilter === 'Live'
    ? agents.filter(a => a.status === 'ready')
    : activeFilter === 'Testing'
    ? agents.filter(a => a.status === 'testing')
    : agents.filter(a => a.status !== 'ready' && a.status !== 'testing')

  return (
    <div style={{ background: 'transparent' }}>
      {/* Header bar — greeting left, CTA right */}
      <div
        style={{
          padding: '40px 48px 16px',
          position: 'relative',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '32px', maxWidth: '1400px' }}>
          <div>
            <h1 className="on-photo" style={{ fontFamily: 'var(--font-display)', fontWeight: 400, fontVariationSettings: '"opsz" 144, "SOFT" 50', fontSize: '34px', letterSpacing: '-0.022em', lineHeight: 1.1, color: 'var(--text-1)', margin: '0 0 6px 0' }}>
              {greeting},{' '}
              <em style={{ fontStyle: 'italic', fontWeight: 300, color: 'var(--accent)', fontVariationSettings: '"opsz" 144, "SOFT" 100' }}>
                there
              </em>
            </h1>
            <p className="on-photo" style={{ fontSize: '14px', color: 'var(--text-2)', margin: 0 }}>
              {liveCount} live · {testingCount} in test · {draftCount} draft
            </p>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
            <Link
              href="/agents/new"
              style={{
                background: 'var(--accent)',
                color: 'var(--text-on-accent)',
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

      {/* Filter strip */}
      <div style={{ padding: '0 48px 12px', maxWidth: '1400px' }}>
        <div className="on-photo" style={{ display: 'flex', gap: '4px' }}>
          {([
            { label: 'All', count: agents.length },
            { label: 'Live', count: liveCount },
            { label: 'Testing', count: testingCount },
            { label: 'Draft', count: draftCount },
          ] as const).map(({ label, count }) => {
            const isActive = activeFilter === label
            return (
              <button
                key={label}
                onClick={() => setActiveFilter(label)}
                style={{
                  background: isActive ? 'var(--chip)' : 'transparent',
                  border: isActive ? '1px solid var(--border)' : '1px solid transparent',
                  color: isActive ? 'var(--text-1)' : 'var(--text-2)',
                  fontSize: '13px',
                  fontWeight: isActive ? 600 : 500,
                  padding: '5px 12px',
                  borderRadius: 'var(--radius-xs)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                }}
              >
                {label}
                <span style={{
                  fontSize: '11px',
                  fontFamily: 'var(--font-mono)',
                  color: isActive ? 'var(--text-1)' : 'var(--text-2)',
                  opacity: isActive ? 1 : 0.8,
                }}>
                  {count}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Content area */}
      <div style={{ padding: '0 48px 56px', maxWidth: '1400px' }}>
        {/* Error alert */}
        {loadError && (
          <div
            role="alert"
            style={{ padding: '12px 16px', marginBottom: '20px', background: 'var(--red-bg)', border: '1px solid rgba(248,113,113,0.3)', borderRadius: 'var(--radius-xs)', fontSize: '14px', color: 'var(--red)' }}
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
            <div className="glass" style={{ display: 'inline-flex', alignItems: 'center', borderRadius: 'var(--radius-pill)', padding: '4px 14px', marginBottom: '20px' }}>
              <span style={{ fontSize: '10.5px', fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-3)' }}>No agents yet</span>
            </div>
            <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 400, fontVariationSettings: '"opsz" 144, "SOFT" 30', fontSize: '24px', color: 'var(--text-1)', margin: '0 0 8px 0' }}>
              Build your first{' '}
              <em style={{ fontStyle: 'italic', fontWeight: 300, color: 'var(--accent)', fontVariationSettings: '"opsz" 144, "SOFT" 100' }}>agent</em>
            </h2>
            <p style={{ color: 'var(--text-3)', marginBottom: '24px', fontSize: '15px' }}>
              Create a customer service agent and deploy it in minutes.
            </p>
            <Link href="/agents/new" style={{ background: 'var(--accent)', color: 'var(--text-on-accent)', padding: '10px 18px', borderRadius: 'var(--radius-sm)', fontWeight: 600, fontSize: '14px', textDecoration: 'none', display: 'inline-block' }}>
              New agent
            </Link>
          </div>
        )}

        {/* Agent grid */}
        {!isLoading && agents.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
            {filteredAgents.map((a) => (
              <AgentCard key={a.id} {...a} onDelete={isDemoMode ? undefined : handleDelete} disableNavigation={isDemoMode} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
