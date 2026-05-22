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
  soul_voice?: string | null
  soul_do_list?: string[] | null
  created_at: string
}

// Minimal document shape — only parse_status is needed to decide "Configure done".
interface AgentDocument {
  id: string
  parse_status: string
}

// ---------------------------------------------------------------------------
// Step state derivation — shared by the stepper for every sub-page
//
// Stage gating (mirrors the landing page's right-panel dispatch):
//   1 Provision — done when the tenant DB is ready, else active.
//   2 Configure — done when BOTH soul saved AND a non-failed doc exists;
//                 active once provision is done; locked until then.
//   3 Test      — never done (M6 not built); active once configure is done.
//   4 Deploy    — locked until step 3 is done, which requires M6 → always
//                 locked for now.
// ---------------------------------------------------------------------------

interface StepFlags {
  step1Done: boolean
  configureDone: boolean
  step3Done: boolean
}

function deriveStepState(stepNum: number, agent: AgentDetail | null, flags: StepFlags): StepState {
  if (!agent) return stepNum === 1 ? 'active' : 'locked'

  const { step1Done, configureDone, step3Done } = flags

  switch (stepNum) {
    case 1:
      return step1Done ? 'done' : 'active'
    case 2:
      if (!step1Done) return 'locked'
      return configureDone ? 'done' : 'active'
    case 3:
      if (!configureDone) return 'locked'
      return step3Done ? 'done' : 'active'
    case 4:
      // Deploy requires step 3 done (M6). Until then it is always locked.
      return step3Done ? 'active' : 'locked'
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

  const step1Done =
    !!agent &&
    (agent.status === 'ready' ||
      agent.status === 'provisioning_complete' ||
      agent.neon_project_id !== null)

  // Documents query — shares the cache key/shape with the landing + ingest
  // pages so the stepper reflects ingest state without an extra fetch. Gated
  // on step1Done because /documents is rejected while the tenant DB provisions.
  const docsQuery = useQuery({
    queryKey: ['agent-documents', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/documents`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      return (data.documents ?? []) as AgentDocument[]
    },
    enabled: isLoaded && !!isSignedIn && step1Done,
    staleTime: 10_000,
  })

  const documents = docsQuery.data ?? []
  const soulSaved = !!(agent?.soul_voice || (agent?.soul_do_list?.length ?? 0) > 0)
  const hasDocs = documents.some((d) => d.parse_status !== 'failed')
  const configureDone = soulSaved && hasDocs
  const step3Done = false // M6 not built

  const flags: StepFlags = { step1Done, configureDone, step3Done }

  const steps: JourneyStep[] = [
    {
      num: 1,
      key: 'provision',
      title: 'Provision',
      subtitle: 'Dedicated tenant database',
      state: deriveStepState(1, agent, flags),
      href: `/agents/${id}`,
    },
    {
      num: 2,
      key: 'configure',
      title: 'Configure',
      subtitle: 'Soul, voice, knowledge base',
      state: deriveStepState(2, agent, flags),
      href: `/agents/${id}`,
    },
    {
      num: 3,
      key: 'test',
      title: 'Test',
      subtitle: 'Evaluations + adversarial probes',
      state: deriveStepState(3, agent, flags),
      href: `/agents/${id}/eval`,
    },
    {
      num: 4,
      key: 'deploy',
      title: 'Deploy',
      subtitle: 'Embed snippet + design',
      state: deriveStepState(4, agent, flags),
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
