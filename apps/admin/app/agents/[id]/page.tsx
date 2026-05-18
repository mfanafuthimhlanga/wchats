'use client'
import { useState, useEffect, use } from 'react'
import { useAuth } from '@clerk/nextjs'
import Link from 'next/link'
import JourneyStepper, { type JourneyStep, type StepState } from '../../components/JourneyStepper'
import StepSubtaskCard from '../../components/StepSubtaskCard'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AgentDetail {
  id: string
  tenant_id: string
  name: string
  role: string
  status: 'pending' | 'provisioning' | 'ready' | 'error' | string
  neon_project_id: string | null
  schema_version: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// Step state derivation
// ---------------------------------------------------------------------------

function deriveStepState(
  stepNum: number,
  agent: AgentDetail | null,
): StepState {
  if (!agent) {
    return stepNum === 1 ? 'active' : 'locked'
  }

  const step1Done =
    agent.status === 'ready' ||
    agent.status === 'provisioning_complete' ||
    agent.neon_project_id !== null

  const step2Done = step1Done // Configure done once Provision done (soul editor fills in fields)
  const step3Done = false     // Test: not done until M6 eval harness wires up
  const step4Done = false     // Deploy: not done until widget deployed

  switch (stepNum) {
    case 1:
      return step1Done ? 'done' : 'active'
    case 2:
      if (!step1Done) return 'locked'
      return step1Done ? 'active' : 'locked'
    case 3:
      if (!step2Done) return 'locked'
      return step3Done ? 'done' : 'active'
    case 4:
      if (!step3Done) return 'locked'
      return step4Done ? 'done' : 'active'
    default:
      return 'locked'
  }
}

// ---------------------------------------------------------------------------
// AgentJourneyPage
// ---------------------------------------------------------------------------

export default function AgentJourneyPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  const [agent, setAgent] = useState<AgentDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    const loadAgent = async () => {
      try {
        const token = await getToken()
        if (!token) {
          setLoadError('Not authenticated. Please sign in.')
          return
        }
        const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const data: AgentDetail = await r.json()
        setAgent(data)
      } catch (err) {
        console.error(err)
        setLoadError('Failed to load agent. Please refresh.')
      }
    }
    loadAgent()
  }, [id, apiBase, getToken])

  // Build step definitions
  const steps: JourneyStep[] = [
    {
      num: 1,
      key: 'provision',
      title: 'Provision',
      subtitle: 'Dedicated tenant database',
      state: deriveStepState(1, agent),
    },
    {
      num: 2,
      key: 'configure',
      title: 'Configure',
      subtitle: 'Soul, voice, knowledge base',
      state: deriveStepState(2, agent),
    },
    {
      num: 3,
      key: 'test',
      title: 'Test',
      subtitle: 'Evaluations + adversarial probes',
      state: deriveStepState(3, agent),
      href: `/agents/${id}/eval`,
    },
    {
      num: 4,
      key: 'deploy',
      title: 'Deploy',
      subtitle: 'Embed snippet + design',
      state: deriveStepState(4, agent),
      href: `/agents/${id}/deploy`,
    },
  ]

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)', fontFamily: 'var(--font-sans)' }}>
      <div style={{ display: 'flex', minHeight: 'calc(100vh - 56px)' }}>
        {/* Left: 320px journey stepper */}
        <JourneyStepper
          agentName={agent?.name ?? 'Loading…'}
          agentRole={agent?.role ?? ''}
          steps={steps}
        />

        {/* Right: active step content */}
        <section
          style={{
            flex: 1,
            padding: '32px 40px',
            overflowY: 'auto',
          }}
        >
          <h1
            style={{
              fontSize: '22px',
              fontWeight: 700,
              color: 'var(--text-1)',
              marginBottom: '8px',
            }}
          >
            Configure your agent
          </h1>
          <p style={{ fontSize: '14px', color: 'var(--text-3)', marginBottom: '24px' }}>
            Define the soul, ingest your knowledge base, and prepare for testing.
          </p>

          {/* Error alert */}
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

          {/* Substep cards */}
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
              marginTop: '24px',
            }}
          >
            <StepSubtaskCard
              icon="◐"
              title="Define the soul"
              description="Voice, do, do-not — structured fields, not a blank textarea"
              href={`/agents/${id}/soul`}
              ctaLabel="Open editor"
              state="active"
            />
            <StepSubtaskCard
              icon="⬆"
              title="Ingest documents"
              description="Upload PDFs or URLs"
              href={`/agents/${id}/ingest`}
              ctaLabel="Upload"
              state="idle"
            />
            <StepSubtaskCard
              icon="✓"
              title="Run evaluations"
              description="Ragas metrics + adversarial probes"
              href={`/agents/${id}/eval`}
              ctaLabel="Run evals"
              state="idle"
            />
            <StepSubtaskCard
              icon="↗"
              title="Deploy widget"
              description="Embed snippet + design customization"
              href={`/agents/${id}/deploy`}
              ctaLabel="Configure deploy"
              state="idle"
            />
          </div>
        </section>
      </div>
    </div>
  )
}
