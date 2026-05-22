'use client'
import { use } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import JourneyStepper, { type JourneyStep, type StepState } from '../../components/JourneyStepper'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AgentDetail {
  id: string
  name: string
  role: string
  status: string
  neon_project_id: string | null
  schema_version: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// Step state derivation — shared by the stepper for every sub-page
// ---------------------------------------------------------------------------

function deriveStepState(stepNum: number, agent: AgentDetail | null): StepState {
  if (!agent) return stepNum === 1 ? 'active' : 'locked'

  const step1Done =
    agent.status === 'ready' ||
    agent.status === 'provisioning_complete' ||
    agent.neon_project_id !== null

  switch (stepNum) {
    case 1:
      return step1Done ? 'done' : 'active'
    case 2:
      return step1Done ? 'active' : 'locked'
    case 3:
      return step1Done ? 'active' : 'locked' // active = available but not done yet
    case 4:
      return step1Done ? 'active' : 'locked'
    default:
      return 'locked'
  }
}

// ---------------------------------------------------------------------------
// AgentDetailLayout — provides the journey stepper for ALL /agents/[id]/* pages
// ---------------------------------------------------------------------------

export default function AgentDetailLayout({
  children,
  params,
}: {
  children: React.ReactNode
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  const agentQuery = useQuery({
    queryKey: ['agent', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<AgentDetail>
    },
    enabled: isLoaded && !!isSignedIn,
    refetchInterval: (query) => {
      const d = query.state.data
      if (!d) return false
      const done =
        d.status === 'ready' ||
        d.status === 'provisioning_complete' ||
        d.neon_project_id !== null
      return done ? false : 3000
    },
    staleTime: 0,
  })

  const agent = agentQuery.data ?? null

  const steps: JourneyStep[] = [
    {
      num: 1,
      key: 'provision',
      title: 'Provision',
      subtitle: 'Dedicated tenant database',
      state: deriveStepState(1, agent),
      href: `/agents/${id}`,
    },
    {
      num: 2,
      key: 'configure',
      title: 'Configure',
      subtitle: 'Soul, voice, knowledge base',
      state: deriveStepState(2, agent),
      href: `/agents/${id}`,
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
    <div
      style={{
        display: 'flex',
        minHeight: 'calc(100vh - 56px)',
        fontFamily: 'var(--font-sans)',
        background: 'var(--bg)',
      }}
    >
      <JourneyStepper
        agentName={agent?.name ?? 'Loading…'}
        agentRole={agent?.role ?? ''}
        steps={steps}
      />
      <section style={{ flex: 1, overflowY: 'auto' }}>{children}</section>
    </div>
  )
}
