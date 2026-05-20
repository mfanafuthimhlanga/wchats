'use client'
import { useState, useRef, useEffect } from 'react'
import { useAuth } from '@clerk/nextjs'
import { useRouter } from 'next/navigation'
import JourneyStepper, { type JourneyStep } from '../../components/JourneyStepper'

type Phase = 'form' | 'provisioning' | 'error'
type Role = 'support' | 'sales' | 'helpdesk'

const POLL_TIMEOUT_TICKS = 60 // 60 ticks × 2s = 120s

export default function CreateAgentPage() {
  const router = useRouter()
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  // Form fields
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('support')

  // Wizard state
  const [phase, setPhase] = useState<Phase>('form')
  const [error, setError] = useState<string | null>(null)
  const [agentId, setAgentId] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('')
  const [timedOut, setTimedOut] = useState(false)

  // Polling refs — cleaned up on unmount and on success/error
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isMountedRef = useRef(true)
  const agentIdRef = useRef<string | null>(null)
  const pollCountRef = useRef(0)

  // Cleanup polling on unmount (T-04.2-04-02 resource exhaustion guard)
  useEffect(() => {
    return () => {
      isMountedRef.current = false
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()

    if (!name.trim()) {
      setError('Agent name is required.')
      return
    }

    setError(null)
    setTimedOut(false)
    setPhase('provisioning')
    pollCountRef.current = 0

    const token = await getToken()
    if (!token) {
      setError('Not authenticated. Please sign in.')
      setPhase('error')
      return
    }

    try {
      // Ensure tenant row exists — webhooks don't fire in local dev
      const provRes = await fetch(`${apiBase}/me/provision`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!provRes.ok) {
        setError(`Provision failed: HTTP ${provRes.status}`)
        setPhase('error')
        return
      }

      // POST /api/v1/agents
      const res = await fetch(`${apiBase}/api/v1/agents`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: name.trim(),
          role,
          soul: { voice: '', do: [], do_not: [] },
        }),
      })

      if (!res.ok) {
        const msg = `Create failed: HTTP ${res.status}`
        setError(msg)
        setPhase('error')
        return
      }

      const data: { agent_id: string; job_id: string; status: string; events_url: string } =
        await res.json()
      const createdAgentId = data.agent_id
      setAgentId(createdAgentId)
      agentIdRef.current = createdAgentId
      setStatus('pending')

      // Poll GET /api/v1/agents/{id} every 2s — polling only, not SSE (see RESEARCH Pitfall 4)
      pollRef.current = setInterval(async () => {
        pollCountRef.current += 1

        // Timeout after 60 ticks (120s)
        if (pollCountRef.current >= POLL_TIMEOUT_TICKS) {
          clearInterval(pollRef.current!)
          pollRef.current = null
          if (isMountedRef.current) {
            setTimedOut(true)
            setPhase('error')
            setError(
              'This is taking longer than expected. Make sure your Celery worker is running:\n' +
              'celery -A app.worker.celery_app worker -Q pipeline,runtime --loglevel=info',
            )
          }
          return
        }

        try {
          const pollToken = await getToken()
          if (!pollToken) return

          const currentAgentId = agentIdRef.current
          if (!currentAgentId) return

          const pollRes = await fetch(`${apiBase}/api/v1/agents/${currentAgentId}`, {
            headers: { Authorization: `Bearer ${pollToken}` },
          })
          if (!pollRes.ok) return

          const pollData: { status: string } = await pollRes.json()
          if (!isMountedRef.current) return
          setStatus(pollData.status)

          if (pollData.status === 'ready') {
            clearInterval(pollRef.current!)
            pollRef.current = null
            router.push('/agents/' + currentAgentId)
          } else if (pollData.status === 'error') {
            clearInterval(pollRef.current!)
            pollRef.current = null
            setError('Provisioning failed. Please try again.')
            setPhase('error')
          }
        } catch (pollErr) {
          console.error('Poll error:', pollErr)
        }
      }, 2000)
    } catch (err) {
      console.error(err)
      setError('Failed to create agent. Please try again.')
      setPhase('error')
    }
  }

  const handleReset = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    pollCountRef.current = 0
    setName('')
    setRole('support')
    setError(null)
    setAgentId(null)
    setStatus('')
    setTimedOut(false)
    setPhase('form')
  }

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '10px 12px',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-xs)',
    fontSize: '14px',
    fontFamily: 'var(--font-sans)',
    background: 'var(--surface-1)',
    color: 'var(--text-1)',
    outline: 'none',
    boxSizing: 'border-box',
  }

  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontWeight: 600,
    fontSize: '11px',
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    color: 'var(--text-3)',
    marginBottom: '6px',
  }

  // Left-panel journey steps — step 1 active during form/provisioning, done only when agent ready
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
          <form onSubmit={handleSubmit} style={{ maxWidth: '480px' }}>
            {error && (
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
                {error}
              </div>
            )}

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
                color: '#fff',
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
              Setting up a dedicated database. This usually takes about 30 seconds.
            </p>
            <p style={{ fontSize: '13px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
              Current status: {status || 'pending'}
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
                whiteSpace: timedOut ? 'pre-line' : undefined,
              }}
            >
              {error}
            </div>
            <button
              onClick={handleReset}
              style={{
                padding: '12px 32px',
                minHeight: '44px',
                background: 'var(--accent)',
                color: '#fff',
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                cursor: 'pointer',
                fontSize: '15px',
                fontWeight: 600,
                fontFamily: 'var(--font-sans)',
              }}
            >
              {timedOut ? 'Retry' : 'Try again'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
