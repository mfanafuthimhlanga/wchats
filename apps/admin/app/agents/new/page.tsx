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
          padding: '32px 40px',
        }}
      >
        <h1
          style={{
            fontSize: '24px',
            fontWeight: 700,
            color: 'var(--text-1)',
            marginBottom: '24px',
          }}
        >
          Create a new agent
        </h1>

        {/* Form phase */}
        {phase === 'form' && (
          <form onSubmit={handleSubmit} style={{
            maxWidth: '480px',
            background: 'var(--surface-1)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            padding: '28px',
          }}>
            {!name.trim() && mutation.isIdle ? null : null}

            {/* Agent name */}
            <div style={{ marginBottom: '20px' }}>
              <label htmlFor="agentName" style={labelStyle}>
                Agent Name <span style={{ color: 'var(--red)' }}>*</span>
              </label>
              <input
                id="agentName"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. SupportBot"
                maxLength={60}
                style={inputStyle}
              />
            </div>

            {/* Role select */}
            <div style={{ marginBottom: '28px' }}>
              <label htmlFor="agentRole" style={labelStyle}>
                Role
              </label>
              <select
                id="agentRole"
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                style={{ ...inputStyle, cursor: 'pointer' }}
              >
                <option value="support">Support</option>
                <option value="sales">Sales</option>
                <option value="helpdesk">Helpdesk</option>
              </select>
            </div>

            {/* Submit */}
            <button
              type="submit"
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
              Create agent
            </button>
          </form>
        )}

        {/* Provisioning phase */}
        {phase === 'provisioning' && (
          <div
            style={{
              padding: '32px',
              background: 'var(--surface-2)',
              border: '1px solid var(--border-soft)',
              borderRadius: 'var(--radius-xs)',
              maxWidth: '480px',
            }}
          >
            <h2
              style={{
                fontSize: '18px',
                fontWeight: 700,
                color: 'var(--text-1)',
                marginBottom: '12px',
              }}
            >
              Provisioning your agent…
            </h2>
            <p style={{ fontSize: '14px', color: 'var(--text-3)', marginBottom: '16px' }}>
              Setting up a dedicated database. This usually takes 30–60 seconds.
            </p>
            <p style={{ fontSize: '13px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
              Status: {polledStatus || 'pending'}{polledStatus === 'ready' ? ' — redirecting…' : ' — working…'}
            </p>
            {agentId && (
              <p style={{ fontSize: '12px', color: 'var(--text-4)', marginTop: '8px', fontFamily: 'var(--font-mono)' }}>
                Agent ID: {agentId}
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
