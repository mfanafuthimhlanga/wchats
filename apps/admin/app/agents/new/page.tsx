'use client'
import { useState, useEffect } from 'react'
import { useAuth } from '@clerk/nextjs'
import { useRouter } from 'next/navigation'
import { useMutation, useQuery } from '@tanstack/react-query'
import JourneyStepper, { type JourneyStep } from '../../components/JourneyStepper'

type Role = 'support' | 'sales' | 'helpdesk'

export default function CreateAgentPage() {
  const router = useRouter()
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  // Form fields
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('support')

  // agentId is set after a successful mutation; drives polling
  const [agentId, setAgentId] = useState<string | null>(null)

  // --- Mutation: provision + create agent ---
  const mutation = useMutation({
    mutationFn: async ({ agentName, agentRole }: { agentName: string; agentRole: Role }) => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated. Please sign in.')

      // Ensure tenant row exists — webhooks don't fire in local dev
      const provRes = await fetch(`${apiBase}/me/provision`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!provRes.ok) throw new Error(`Provision failed: HTTP ${provRes.status}`)

      // POST /api/v1/agents
      const res = await fetch(`${apiBase}/api/v1/agents`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: agentName.trim(),
          role: agentRole,
          soul: { voice: '', do: [], do_not: [] },
        }),
      })
      if (!res.ok) throw new Error(`Create failed: HTTP ${res.status}`)

      const data: { agent_id: string; job_id: string; status: string; events_url: string } =
        await res.json()
      return { agent_id: data.agent_id, job_id: data.job_id }
    },
  })

  // Set agentId when mutation succeeds
  useEffect(() => {
    if (mutation.data?.agent_id) {
      setAgentId(mutation.data.agent_id)
    }
  }, [mutation.data?.agent_id])

  // --- Query: poll agent status ---
  const pollQuery = useQuery({
    queryKey: ['agent-status', agentId],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Session expired')
      const res = await fetch(`${apiBase}/api/v1/agents/${agentId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json() as Promise<{ status: string }>
    },
    enabled: !!agentId,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      if (s === 'ready' || s === 'error' || s === 'failed') return false
      return 2000
    },
    retry: 3,
    retryDelay: 2000,
  })

  // Navigate to agent page once ready
  useEffect(() => {
    if (pollQuery.data?.status === 'ready' && agentId) {
      router.push('/agents/' + agentId)
    }
  }, [pollQuery.data?.status, agentId, router])

  // --- Derived phase ---
  const polledStatus = pollQuery.data?.status ?? ''
  const isTerminalError =
    mutation.isError ||
    pollQuery.isError ||
    polledStatus === 'error' ||
    polledStatus === 'failed'

  const phase =
    isTerminalError
      ? 'error'
      : mutation.isPending || (agentId && !pollQuery.data)
      ? 'provisioning'
      : agentId
      ? 'provisioning'
      : 'form'

  // --- Error message ---
  let errorMessage: string | null = null
  if (mutation.isError) {
    errorMessage = `Create failed: ${(mutation.error as Error).message}`
  } else if (pollQuery.isError) {
    errorMessage = 'Provisioning check failed. Check your server is running.'
  } else if (polledStatus === 'error' || polledStatus === 'failed') {
    errorMessage = 'Provisioning failed. Please try again.'
  }

  // --- Handlers ---
  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()

    if (!name.trim()) {
      // Surface a validation error inline — mutation hasn't been called yet
      return
    }

    if (!isLoaded || !isSignedIn) return

    mutation.mutate({ agentName: name, agentRole: role })
  }

  const handleReset = () => {
    mutation.reset()
    setAgentId(null)
    setName('')
    setRole('support')
  }

  // --- Styles ---
  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '10px 12px',
    border: '1px solid var(--border-soft)',
    borderRadius: 'var(--radius-xs)',
    fontSize: '14px',
    fontFamily: 'var(--font-sans)',
    background: 'var(--surface-2)',
    color: 'var(--text-1)',
    outline: 'none',
    boxSizing: 'border-box',
  }

  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontWeight: 600,
    fontSize: '10.5px',
    textTransform: 'uppercase',
    letterSpacing: '0.12em',
    color: 'var(--text-3)',
    marginBottom: '6px',
  }

  // Left-panel journey steps
  const steps: JourneyStep[] = [
    {
      num: 1,
      key: 'provision',
      title: 'Provision',
      subtitle: 'Dedicated tenant database',
      state: phase === 'form' || phase === 'provisioning' || phase === 'error' ? 'active' : 'done',
    },
    {
      num: 2,
      key: 'configure',
      title: 'Configure',
      subtitle: 'Soul, voice, knowledge base',
      state: 'locked',
    },
    {
      num: 3,
      key: 'test',
      title: 'Test',
      subtitle: 'Evaluations + adversarial probes',
      state: 'locked',
    },
    {
      num: 4,
      key: 'deploy',
      title: 'Deploy',
      subtitle: 'Embed snippet + design',
      state: 'locked',
    },
  ]

  return (
    <div style={{ display: 'flex', minHeight: 'calc(100vh - 56px)', fontFamily: 'var(--font-sans)' }}>
      {/* Left panel: journey stepper */}
      <JourneyStepper
        agentName="New Agent"
        agentRole=""
        steps={steps}
      />

      {/* Right panel: form / provisioning / error content */}
      <div
        style={{
          flex: 1,
          padding: '40px 48px',
          overflowY: 'auto',
        }}
      >
        {/* Form phase */}
        {phase === 'form' && (
          <form onSubmit={handleSubmit} style={{ maxWidth: '560px' }}>
            {/* Panel header */}
            <p style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '10px',
              fontWeight: 600,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'var(--text-4)',
              marginBottom: '8px',
            }}>
              Step 1 of 4
            </p>
            <h1 style={{
              fontFamily: 'var(--font-display)',
              fontWeight: 600,
              fontVariationSettings: '"opsz" 144, "SOFT" 30',
              fontSize: '24px',
              color: 'var(--text-1)',
              marginBottom: '8px',
            }}>
              Provision your agent
            </h1>
            <p style={{
              fontSize: '14px',
              color: 'var(--text-3)',
              lineHeight: 1.6,
              maxWidth: '520px',
              marginBottom: '32px',
            }}>
              Give your agent a name and define its primary role. This sets the context for all downstream configuration.
            </p>

            {/* Agent name */}
            <div style={{ marginBottom: '20px' }}>
              <label htmlFor="agentName" style={labelStyle}>Agent name</label>
              <input
                id="agentName"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Acme Support, Hillbrow Realty Assistant"
                maxLength={60}
                style={inputStyle}
              />
            </div>

            {/* Primary role */}
            <div style={{ marginBottom: '20px' }}>
              <label htmlFor="agentRole" style={labelStyle}>Primary role</label>
              <select
                id="agentRole"
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                style={{ ...inputStyle, cursor: 'pointer' }}
              >
                <option value="support">Customer service agent</option>
                <option value="sales">Sales agent</option>
                <option value="helpdesk">Helpdesk agent</option>
              </select>
              <p style={{ fontSize: '12px', color: 'var(--text-4)', marginTop: '5px' }}>
                Used as the role context in the system prompt.
              </p>
            </div>

            {/* Panel footer */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginTop: '32px',
              paddingTop: '24px',
              borderTop: '1px solid var(--border-soft)',
            }}>
              <span style={{ display: 'inline-block', width: '110px' }} />
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '11px',
                color: 'var(--text-4)',
                letterSpacing: '0.08em',
              }}>
                Step 1 of 4
              </span>
              <button
                type="submit"
                style={{
                  padding: '11px 28px',
                  background: 'var(--accent)',
                  color: '#0B0717',
                  border: 'none',
                  borderRadius: 'var(--radius-sm)',
                  cursor: 'pointer',
                  fontSize: '14px',
                  fontWeight: 600,
                  fontFamily: 'var(--font-sans)',
                  boxShadow: 'var(--shadow-glow)',
                }}
              >
                Continue →
              </button>
            </div>
          </form>
        )}

        {/* Provisioning phase */}
        {phase === 'provisioning' && (
          <div style={{ maxWidth: '560px' }}>
            <p style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '10px',
              fontWeight: 600,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'var(--text-4)',
              marginBottom: '8px',
            }}>
              Step 1 of 4
            </p>
            <h1 style={{
              fontFamily: 'var(--font-display)',
              fontWeight: 600,
              fontVariationSettings: '"opsz" 144, "SOFT" 30',
              fontSize: '24px',
              color: 'var(--text-1)',
              marginBottom: '8px',
            }}>
              Provisioning your agent…
            </h1>
            <p style={{ fontSize: '14px', color: 'var(--text-3)', lineHeight: 1.6, marginBottom: '24px' }}>
              Setting up a dedicated database. This usually takes 30–60 seconds.
            </p>
            <p style={{ fontSize: '13px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
              status: {polledStatus || 'pending'}{polledStatus === 'ready' ? ' — redirecting…' : ' — working…'}
            </p>
            {agentId && (
              <p style={{ fontSize: '12px', color: 'var(--text-4)', marginTop: '8px', fontFamily: 'var(--font-mono)' }}>
                agent_id: {agentId}
              </p>
            )}
          </div>
        )}

        {/* Error phase */}
        {phase === 'error' && (
          <div style={{ maxWidth: '480px' }}>
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
              {errorMessage}
            </div>
            <button
              onClick={handleReset}
              style={{
                padding: '12px 32px',
                minHeight: '44px',
                background: 'var(--accent)',
                color: '#0B0717',
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                cursor: 'pointer',
                fontSize: '15px',
                fontWeight: 600,
                fontFamily: 'var(--font-sans)',
              }}
            >
              Try again
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
