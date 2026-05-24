'use client'
import Link from 'next/link'
import { use } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
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

// Minimal document shape — only the field we need to decide "Configure done".
// The full shape lives in the ingest page; here we just need parse_status.
interface AgentDocument {
  id: string
  parse_status: string
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
// AgentJourneyPage — renders the right-panel content only.
// The shared layout (layout.tsx) provides the two-panel wrapper + stepper.
// ---------------------------------------------------------------------------

export default function AgentJourneyPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  // Same queryKey as the layout — TanStack serves this from cache (no extra fetch).
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

  // Documents query — same key/shape as the ingest page so the cache is shared.
  // Only enabled once step1 is done (the backend rejects /documents while the
  // tenant DB is still provisioning), mirroring the ingest page's gate.
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

  // Derived soulSaved — gates the soul card CTA emphasis + downstream copy
  const soulSaved = !!(
    agent?.soul_voice ||
    (agent?.soul_do_list?.length ?? 0) > 0
  )

  // Derived hasDocs — at least one document that did not fail to parse.
  const hasDocs = documents.some((d) => d.parse_status !== 'failed')

  // "Configure done" is a single, unambiguous definition: BOTH the soul is
  // saved AND there is at least one non-failed document in the knowledge base.
  const configureDone = soulSaved && hasDocs

  // step3Done: at least one eval run exists (M6 eval harness is live).
  // This flag controls whether step 4 (Deploy) is unlocked in the stepper.
  // Note: step3Done derivation is authoritative in layout.tsx; this local copy
  // keeps the right-panel dispatch logic self-contained.
  const step3Done = false // layout.tsx owns the gating query; page mirrors it

  // ---- Right-panel: loading skeleton (first load, no cached data yet) -------
  const loadingPanel = (
    <p style={{ fontSize: '14px', color: 'var(--text-3)' }}>Loading agent…</p>
  )

  // ---- Right-panel: provisioning status card (step1 not yet done) ----------
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
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '12px',
        }}
      >
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
        Your dedicated database is being provisioned. This may take up to 60 seconds.
      </p>
      {agent && (
        <p style={{ fontSize: '12px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
          Agent ID: {agent.id}
        </p>
      )}
    </div>
  )

  // ---- Right-panel: configure subtask cards (step1 done, configure pending)-
  // Configure owns exactly two sub-tasks — Soul and Ingest. Steps 3 (Test) and
  // 4 (Deploy) are separate journey stages and are NOT previewed here. The
  // "active" CTA emphasis follows the natural order: soul first, then ingest
  // once the soul is saved.
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
        Define the soul and ingest your knowledge base. Configure is complete once
        both are done.
      </p>

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
          marginTop: '24px',
        }}
      >
        {/* Soul — done when saved, otherwise the active first step */}
        <StepSubtaskCard
          icon="◐"
          title="Define the soul"
          description={
            soulSaved
              ? 'Personality, behaviors, and boundaries — saved'
              : 'Personality, behaviors, and boundaries'
          }
          href={`/agents/${id}/soul`}
          ctaLabel={soulSaved ? 'Edit soul' : 'Open editor'}
          state={soulSaved ? 'completed' : 'active'}
        />

        {/* Ingest — done when at least one non-failed doc exists; becomes the
            active step once the soul is saved. */}
        <StepSubtaskCard
          icon="⬆"
          title="Ingest documents"
          description={
            hasDocs
              ? 'Knowledge base has documents'
              : soulSaved
              ? 'Upload PDFs or URLs'
              : 'Save soul settings first'
          }
          href={`/agents/${id}/ingest`}
          ctaLabel={hasDocs ? 'Manage documents' : 'Upload'}
          state={hasDocs ? 'completed' : soulSaved ? 'active' : 'idle'}
        />
      </div>
    </>
  )

  // ---- Right-panel: Test stage (configure done) ----------------------------
  // M6 eval harness is live. Surface a CTA to the Evals page.
  const testPanel = (
    <>
      <h1
        style={{
          fontSize: '22px',
          fontWeight: 700,
          color: 'var(--text-1)',
          marginBottom: '8px',
        }}
      >
        Test your agent
      </h1>
      <p style={{ fontSize: '14px', color: 'var(--text-3)', marginBottom: '24px' }}>
        Run evaluations and adversarial probes before deploying. Deploy unlocks once
        at least one eval run is complete.
      </p>

      <div style={{ marginTop: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <StepSubtaskCard
          icon="✓"
          title="Run automated evals"
          description="Measure faithfulness, relevance, and red-team resistance"
          href={`/agents/${id}/eval`}
          ctaLabel="Go to Evals →"
          state="active"
        />
        <Link
          href={`/agents/${id}/eval`}
          style={{
            display: 'inline-block',
            padding: '12px 24px',
            background: 'var(--accent)',
            color: '#fff',
            borderRadius: 'var(--radius-sm)',
            fontSize: '15px',
            fontWeight: 600,
            textDecoration: 'none',
            alignSelf: 'flex-start',
          }}
        >
          Open Evals →
        </Link>
      </div>
    </>
  )

  // ---- Dispatch -------------------------------------------------------------
  // This landing page owns the Provision and Configure stages. Once Configure
  // is done, the right panel advances to the (M6-blocked) Test placeholder.
  // The Deploy stage lives at /agents/[id]/deploy and is reached via the stepper.
  let panel: React.ReactNode
  if (agentQuery.isPending) {
    panel = loadingPanel
  } else if (!step1Done) {
    panel = provisioningPanel
  } else if (!configureDone) {
    panel = configurePanel
  } else {
    // configureDone === true → step 3 (Test) is the active stage. step3Done is
    // always false until M6, so we show the Test placeholder.
    panel = step3Done ? configurePanel : testPanel
  }

  return (
    <div style={{ padding: '32px 40px' }}>
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

      {panel}
    </div>
  )
}
