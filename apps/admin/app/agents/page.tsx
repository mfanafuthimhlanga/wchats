'use client'
import { useState, useEffect } from 'react'
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

// ---------------------------------------------------------------------------
// AgentsDashboardPage
// ---------------------------------------------------------------------------

export default function AgentsDashboardPage() {
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  const [agents, setAgents] = useState<AgentSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    const loadAgents = async () => {
      try {
        const token = await getToken()
        if (!token) {
          setLoadError('Not authenticated. Please sign in.')
          return
        }

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

        const r = await fetch(`${apiBase}/api/v1/agents`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const data = await r.json()
        if (!Array.isArray(data?.agents)) {
          throw new Error('Unexpected response shape from /api/v1/agents')
        }
        setAgents(data.agents)
      } catch (err) {
        console.error(err)
        const isNetworkError =
          err instanceof TypeError && err.message.toLowerCase().includes('fetch')
        setLoadError(
          isNetworkError
            ? 'Cannot reach the API server. Make sure the backend is running on ' +
                (apiBase || 'http://localhost:8000') +
                ' and refresh.'
            : 'Failed to load agents. Please refresh.'
        )
      } finally {
        setLoading(false)
      }
    }
    loadAgents()
  }, [apiBase, getToken])

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
        <Link href="/agents/new" style={primaryButtonInline}>
          + Create agent
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
      {loading && (
        <p style={{ color: 'var(--text-3)' }}>Loading agents…</p>
      )}

      {/* Empty state */}
      {!loading && agents.length === 0 && !loadError && (
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
      {!loading && agents.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: '20px',
          }}
        >
          {agents.map((a) => (
            <AgentCard key={a.id} {...a} />
          ))}
        </div>
      )}
    </div>
  )
}
