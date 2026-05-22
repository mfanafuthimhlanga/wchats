'use client'
import { use } from 'react'
import { useQuery } from '@tanstack/react-query'
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
  soul_voice?: string | null
  soul_do_list?: string[] | null
  created_at: string
}

// ---------------------------------------------------------------------------
// Status color map — mirrors AgentCard STATUS_COLORS
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, { bg: string; fg: string; label: string }> = {
  ready: { bg: 'var(--green-bg)', fg: 'var(--green)', label: 'Ready' },
  pending: { bg: 'var(--amber-bg)', fg: 'var(--amber)', label: 'Provisioning' },
  provisioning: { bg: 'var(--amber-bg)', fg: 'var(--amber)', label: 'Provisioning' },
  error: { bg: 'var(--red-bg)', fg: 'var(--red)', label: 'Error' },
}

function getStatusColor(status: string) {
  return (
    STATUS_COLORS[status] ?? {
      bg: 'var(--surface-3)',
      fg: 'var(--text-3)',
      label: status,
    }
  )
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

  const step3Done = false     // Test: not done until M6 eval harness wires up
  const step4Done = false     // Deploy: not done until widget deployed

  switch (stepNum) {
    case 1:
      return step1Done ? 'done' : 'active'
    case 2:
      return step1Done ? 'active' : 'locked'
    case 3:
      if (!step1Done) return 'locked'
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
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  const agentQuery = useQuery({
    queryKey: ['agent', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated. Please sign in.')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<AgentDetail>
    },
    enabled: isLoaded && !!isSignedIn,
    // Poll every 3s while provisioning; stop once ready
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
  const loadError = agentQuery.isError
    ? (agentQuery.error as Error).message || 'Failed to load agent. Please refresh.'
    : null

  // Derived step1Done for right-panel dispatch
  const step1Done =
    !!agent &&
    (agent.status === 'ready' ||
      agent.status === 'provisioning_complete' ||
      agent.neon_project_id !== null)

  // Derived soulSaved — used in C-03 gating
  const soulSaved = !!(
    agent?.soul_voice ||
    (agent?.soul_do_list?.length ?? 0) > 0
  )

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

  // Right-panel: provisioning status card (step1 not yet done)
  const provisioningPanel = (
    <div
      style={{
        padding: '24px',
        background: 'var(--surface-2)',
        border: '1px solid var(--border-soft)',
        borderRadius: 'var(--radius-xs)',
        maxWidth: '480px',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-1)', margin: 0 }}>
          Provisioning
        </h2>
        {agent && (
          <span
            style={{
              padding: '4px 10px',
              borderRadius: '999px',
              fontSize: '11px',
              fontWeight: 600,
              background: getStatusColor(agent.status).bg,
              color: getStatusColor(agent.status).fg,
              whiteSpace: 'nowrap',
            }}
          >
            {getStatusColor(agent.status).label}
          </span>
        )}
      </div>
      <p style={{ fontSize: '14px', color: 'var(--text-3)', marginBottom: '16px' }}>
        Your dedicated database is being provisioned. This may take up to 30 seconds.
      </p>
      {agent && (
        <p style={{ fontSize: '12px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
          Agent ID: {agent.id}
        </p>
      )}
    </div>
  )

  // Right-panel: configure subtask cards (step1 done)
  const configurePanel = (
    <>
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

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
          marginTop: '24px',
        }}
      >
        {/* Soul — available once step1 is done */}
        <StepSubtaskCard
          icon="◐"
          title="Define the soul"
          description="Voice, do, do-not — structured fields, not a blank textarea"
          href={step1Done ? `/agents/${id}/soul` : undefined}
          ctaLabel="Open editor"
          state={step1Done ? 'active' : 'idle'}
        />

        {/* Ingest — available once soul is saved */}
        <StepSubtaskCard
          icon="⬆"
          title="Ingest documents"
          description={soulSaved ? 'Upload PDFs or URLs' : 'Complete provisioning first'}
          href={soulSaved ? `/agents/${id}/ingest` : undefined}
          ctaLabel="Upload"
          state="idle"
        />

        {/* Eval — always unavailable until M6 */}
        <StepSubtaskCard
          icon="✓"
          title="Run evaluations"
          description="Ragas metrics + adversarial probes"
          ctaLabel="Available in M6"
          state="idle"
        />

        {/* Deploy — available once soul is saved */}
        <StepSubtaskCard
          icon="↗"
          title="Deploy widget"
          description="Embed snippet + design customization"
          href={soulSaved ? `/agents/${id}/deploy` : undefined}
          ctaLabel="Configure deploy"
          state="idle"
        />
      </div>
    </>
  )

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

          {/* Dispatch on step1Done */}
          {step1Done ? configurePanel : provisioningPanel}
        </section>
      </div>
    </div>
  )
}
