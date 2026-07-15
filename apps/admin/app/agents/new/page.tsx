'use client'
import { useState, useEffect, useRef } from 'react'
import { useAuth } from '@clerk/nextjs'
import Link from 'next/link'
import { useMutation, useQuery } from '@tanstack/react-query'
import JourneyStepper, { type JourneyStep } from '../../components/JourneyStepper'

/**
 * Provisioning — `/agents/new` (UI-SPEC §6.3, ported from
 * prototypes/gotham/agent-new.html, UI2-04).
 *
 * Colour law: this page carries zero verdict colour on purpose ("there is
 * nothing to gate here, not one pixel of it") — the only bone-bright
 * (`--live`) surfaces on this page are baseline chrome the prototype itself
 * uses everywhere (`.btn-primary`, the active stepper station, focus rings,
 * and the role segmented control's selected option), never a pass/fail chip
 * or a fabricated eval number.
 *
 * NON-NEGOTIABLE (SC3): the create→provision→poll sequence below is the
 * exact `POST /me/provision` → `POST /api/v1/agents` → poll
 * `GET /api/v1/agents/{id}` call chain the prior dusk build already used —
 * same request shapes, same headers, same body. Only the presentation and
 * the post-completion UX (an explicit "Open the agent" CTA instead of an
 * automatic redirect, matching the prototype's `.done` block + focus
 * management) changed.
 */

type Role = 'support' | 'sales' | 'helpdesk'
type Phase = 'form' | 'provisioning' | 'done' | 'error'

// The backend exposes no granular provisioning sub-status — only a
// terminal `status` on the polled agent. The 4-row "on create" checklist
// below is driven from the three real signals we DO observe (provision-call
// success, create-call success, first poll response, terminal ready status)
// rather than a fixed-duration setTimeout stagger. This is an honest
// best-effort mapping of 4 UI rows onto 3 real async checkpoints, not a
// fabricated timeline.
type ProvisionStage = 0 | 1 | 2

export default function CreateAgentPage() {
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  // Form fields
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('support')
  const [primaryRole, setPrimaryRole] = useState('')
  const [businessDomain, setBusinessDomain] = useState('')

  // agentId is set after a successful mutation; drives polling
  const [agentId, setAgentId] = useState<string | null>(null)
  // Real request/response progress markers — see ProvisionStage above
  const [provisionStage, setProvisionStage] = useState<ProvisionStage>(0)
  // Elapsed seconds counter — a real setInterval tied to the actual
  // provisioning window, not a decorative animation
  const [elapsed, setElapsed] = useState(0)
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const openAgentRef = useRef<HTMLAnchorElement>(null)

  // --- Mutation: provision + create agent (endpoint sequence PRESERVED verbatim) ---
  const mutation = useMutation({
    mutationFn: async ({ agentName, agentRole }: { agentName: string; agentRole: Role }) => {
      setProvisionStage(0)
      const token = await getToken()
      if (!token) throw new Error('Not authenticated. Please sign in.')

      // Ensure tenant row exists — webhooks don't fire in local dev
      const provRes = await fetch(`${apiBase}/me/provision`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!provRes.ok) throw new Error(`Provision failed: HTTP ${provRes.status}`)
      setProvisionStage(1)

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
      setProvisionStage(2)

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
      if (query.state.error) return false
      const s = query.state.data?.status
      if (s === 'ready' || s === 'error' || s === 'failed') return false
      return 2000
    },
    retry: 3,
    retryDelay: 2000,
  })

  // --- Derived phase ---
  const polledStatus = pollQuery.data?.status ?? ''
  const isTerminalError =
    mutation.isError ||
    pollQuery.isError ||
    polledStatus === 'error' ||
    polledStatus === 'failed'

  const phase: Phase = isTerminalError
    ? 'error'
    : polledStatus === 'ready'
    ? 'done'
    : mutation.isPending || agentId
    ? 'provisioning'
    : 'form'

  // Start/stop elapsed counter tied to the provisioning phase — freezes at
  // its final real value once the phase leaves 'provisioning' (matches the
  // prototype's `finish()`, which stops the timer and does one final tick).
  useEffect(() => {
    if (phase === 'provisioning') {
      setElapsed(0)
      elapsedRef.current = setInterval(() => setElapsed((s) => s + 1), 1000)
    } else if (elapsedRef.current) {
      clearInterval(elapsedRef.current)
    }
    return () => {
      if (elapsedRef.current) clearInterval(elapsedRef.current)
    }
  }, [phase])

  // Focus management (UI-SPEC §6.3, ported verbatim): move focus to "Open
  // the agent" once provisioning completes.
  useEffect(() => {
    if (phase === 'done') {
      openAgentRef.current?.focus()
    }
  }, [phase])

  // --- Error message (visible alert) ---
  let errorMessage: string | null = null
  if (mutation.isError) {
    errorMessage = `Create failed: ${(mutation.error as Error).message}`
  } else if (pollQuery.isError) {
    errorMessage = 'Provisioning check failed. Check your server is running.'
  } else if (polledStatus === 'error' || polledStatus === 'failed') {
    errorMessage = 'Provisioning failed. Please try again.'
  }

  // --- Status announcement (visually-hidden, non-error phases only — the
  // visible alert above already announces errors via role="alert") ---
  const statusMessage =
    phase === 'provisioning'
      ? 'Provisioning. This takes about a minute.'
      : phase === 'done' && agentId
      ? `Agent ${agentId} provisioned. Open the agent.`
      : ''

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
    setProvisionStage(0)
    setName('')
    setRole('support')
    setPrimaryRole('')
    setBusinessDomain('')
  }

  const fieldsDisabled = phase !== 'form'

  // On-create checklist — each row's `done` flag is derived from a real
  // observed request/response event (see ProvisionStage above), never a
  // fixed-duration timer.
  const ocRows: { label: string; done: boolean }[] = [
    { label: 'Provision an isolated tenant database', done: provisionStage >= 1 },
    { label: 'Create an empty knowledge base', done: provisionStage >= 2 },
    { label: 'Generate a first draft of the soul', done: provisionStage >= 2 && !!pollQuery.data },
    { label: 'Seed evaluation scenarios for this business type', done: polledStatus === 'ready' },
  ]

  // Left-panel journey steps — steps 2–4 are always locked on this page:
  // this is a single-page provisioning wizard, and the operations-room
  // sub-routes (configure/test/deploy) no longer mount JourneyStepper at
  // all once the agent exists (apps/admin/app/agents/[id]/layout.tsx).
  const steps: JourneyStep[] = [
    {
      num: 1,
      key: 'provision',
      title: 'Provision',
      subtitle: 'Name, business type and tone',
      state: phase === 'done' ? 'done' : 'active',
    },
    {
      num: 2,
      key: 'configure',
      title: 'Configure',
      subtitle: 'Draft the soul, load the documents',
      state: 'locked',
    },
    {
      num: 3,
      key: 'test',
      title: 'Test',
      subtitle: 'Run the evals, then the red team',
      state: 'locked',
    },
    {
      num: 4,
      key: 'deploy',
      title: 'Deploy',
      subtitle: 'Clear the gate, then take customers',
      state: 'locked',
    },
  ]

  return (
    <div className="page prov">
      <header className="page-head">
        <h1>New agent</h1>
        <p className="sub">
          Name it and set its temper. Provisioning takes about a minute, and
          nothing is public until it clears its gate.
        </p>
      </header>

      <JourneyStepper steps={steps} />

      <div className="build">
        {/* ── the form ─────────────────────────────────────────────────── */}
        <form onSubmit={handleSubmit}>
          <fieldset className="fields" id="fields" disabled={fieldsDisabled}>
            <legend className="vh">Agent details</legend>

            <div className="f">
              <label className="label" htmlFor="agent-name">Agent name</label>
              <div className="ctl">
                <input
                  type="text"
                  id="agent-name"
                  name="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Acme Support, Hillbrow Realty Assistant"
                  autoComplete="off"
                  spellCheck={false}
                  maxLength={60}
                  required
                />
              </div>
            </div>

            <div className="f">
              <label className="label" htmlFor="agent-primary-role">Primary role</label>
              <div className="ctl">
                <input
                  type="text"
                  id="agent-primary-role"
                  name="primaryRole"
                  value={primaryRole}
                  onChange={(e) => setPrimaryRole(e.target.value)}
                  placeholder="e.g. Customer service agent for Acme Consulting"
                  maxLength={120}
                />
              </div>
            </div>

            <div className="f">
              <label className="label" htmlFor="agent-domain">Business domain</label>
              <div className="ctl">
                <input
                  type="text"
                  id="agent-domain"
                  name="businessDomain"
                  value={businessDomain}
                  onChange={(e) => setBusinessDomain(e.target.value)}
                  placeholder="e.g. SaaS company, property agency, e-commerce"
                  maxLength={120}
                />
              </div>
            </div>

            <fieldset className="f-set">
              <legend className="vh">Role</legend>
              <div className="f">
                <span className="label" aria-hidden="true">Role</span>
                <div className="ctl">
                  <div className="seg">
                    <input
                      type="radio"
                      id="role-support"
                      name="role"
                      value="support"
                      checked={role === 'support'}
                      onChange={() => setRole('support')}
                    />
                    <label htmlFor="role-support">Support</label>

                    <input
                      type="radio"
                      id="role-sales"
                      name="role"
                      value="sales"
                      checked={role === 'sales'}
                      onChange={() => setRole('sales')}
                    />
                    <label htmlFor="role-sales">Sales</label>

                    <input
                      type="radio"
                      id="role-helpdesk"
                      name="role"
                      value="helpdesk"
                      checked={role === 'helpdesk'}
                      onChange={() => setRole('helpdesk')}
                    />
                    <label htmlFor="role-helpdesk">Helpdesk</label>
                  </div>
                </div>
              </div>
            </fieldset>
          </fieldset>

          <p className="voice voice-line">
            There is nothing to measure yet. The bench stays cold until this
            agent has documents to be wrong about.
          </p>

          {phase === 'error' && (
            <div role="alert" className="prov-error">
              {errorMessage}
            </div>
          )}

          <div className="actions">
            {phase === 'error' ? (
              <button type="button" className="btn btn-primary" onClick={handleReset}>
                Try again
              </button>
            ) : (
              <button
                type="submit"
                className="btn btn-primary"
                disabled={phase !== 'form'}
                aria-busy={phase === 'provisioning'}
              >
                {phase === 'provisioning' ? 'Provisioning' : 'Create agent'}
              </button>
            )}
            <Link className="btn btn-ghost" href="/agents">Cancel</Link>
            {phase === 'provisioning' && (
              <span className="mono elapsed" aria-hidden="true">{elapsed}s</span>
            )}
          </div>

          <p className="vh" role="status" aria-live="polite">{statusMessage}</p>

          {phase === 'done' && agentId && (
            <div className="done">
              <p className="mono done-line">agent {agentId} provisioned</p>
              <Link ref={openAgentRef} className="btn btn-primary" href={`/agents/${agentId}`}>
                Open the agent
              </Link>
            </div>
          )}
        </form>

        {/* ── the cold bench ───────────────────────────────────────────── */}
        <aside aria-labelledby="instruments-h">
          <p className="label" id="instruments-h">Instruments · no signal yet</p>

          <div className="instruments">
            {['Faithfulness', 'Answer relevancy', 'Context recall', 'Context precision'].map((c) => (
              <div className="chan" key={c}>
                <span>{c}</span>
                <span className="flatline" aria-hidden="true" />
                <span className="mono reading">--</span>
              </div>
            ))}
          </div>

          <div className="oc">
            <div className="bench-head">
              <p className="label">On create</p>
            </div>

            <ul className="oc-list">
              {ocRows.map((row) => (
                <li className="oc-row" data-done={row.done ? 'true' : undefined} key={row.label}>
                  <span className="glyph" aria-hidden="true">
                    <OcGlyph done={row.done} />
                  </span>
                  <span>{row.label}</span>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      </div>

      <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// OcGlyph — the "on create" checklist tick. Reimplemented locally rather
// than reusing components/gotham/icons.tsx's CheckIcon because the tick
// path's visibility must be individually state-controlled (opacity 0 → 1 as
// each row's real signal fires); the shared icon set has no per-instance
// className hook for that inner path.
// ---------------------------------------------------------------------------

function OcGlyph({ done }: { done: boolean }) {
  return (
    <svg
      viewBox="0 0 14 14"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.3}
      aria-hidden="true"
      focusable="false"
    >
      <rect x="0.7" y="0.7" width="12.6" height="12.6" rx="2" />
      <path
        d="M3.7 7.1 5.9 9.4l4.5-5"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ opacity: done ? 1 : 0, transition: 'opacity 200ms ease' }}
      />
    </svg>
  )
}

// Static string literal only — never interpolate fetched/user data here
// (threat T-20-07-02).
const PAGE_CSS = `
  .page.prov { max-width: 1060px; margin-inline: auto; }
  .page.prov .page-head .sub { max-width: 62ch; }

  .build {
    display: grid;
    grid-template-columns: minmax(0, 560px) minmax(240px, 300px);
    gap: 60px; justify-content: center;
    margin-top: 46px;
  }

  .fields { margin: 0; padding: 0; border: 0; min-width: 0; }
  .fields[disabled] { opacity: 0.5; }

  .f {
    display: grid; grid-template-columns: 132px minmax(0, 1fr);
    align-items: center; gap: 20px;
    padding: 16px 0;
    border-top: 1px solid var(--hairline);
  }
  .f:last-child { border-bottom: 1px solid var(--hairline); }
  .f > .label { margin: 0; }

  .ctl { position: relative; min-width: 0; }
  .f input[type="text"] {
    background: transparent; border: 0; border-radius: 0;
    padding: 4px 0; font-size: 14px; color: var(--ink);
  }
  .f input[type="text"]:focus-visible { outline: 2px solid var(--live); outline-offset: 3px; }

  .f-set { margin: 0; padding: 0; border: 0; min-width: 0; }
  .seg {
    display: inline-flex;
    border: 1px solid var(--hairline); border-radius: var(--r-control);
    overflow: hidden;
  }
  .seg input { position: absolute; width: 1px; height: 1px; opacity: 0; clip-path: inset(50%); }
  .seg label {
    margin: 0; padding: 7px 15px; cursor: pointer;
    font-family: var(--sans); font-size: 12.5px; font-weight: 600;
    letter-spacing: 0; text-transform: none; color: var(--ink-2);
    border-left: 1px solid var(--hairline);
    transition: background 140ms ease, color 140ms ease;
  }
  .seg label:first-of-type { border-left: 0; }
  .seg label:hover { background: var(--surface); color: var(--ink); }
  .seg input:checked + label { background: var(--live); color: var(--live-ink); }
  .seg input:focus-visible + label { outline: 2px solid var(--live); outline-offset: -3px; }

  .voice-line { margin: 26px 0 0; font-size: 16px; max-width: 56ch; }

  .actions { display: flex; align-items: center; gap: 12px; margin-top: 26px; }
  .elapsed { margin-left: 2px; font-size: 12px; color: var(--ink-3); }

  .prov-error {
    margin-top: 20px;
    padding: 12px 16px;
    border: 1px solid var(--hairline-strong);
    border-radius: var(--r-control);
    font-size: 13.5px;
    color: var(--ink);
    background: var(--surface);
  }

  .done { margin-top: 22px; }
  .done-line { font-size: 12.5px; color: var(--ink-2); margin-bottom: 14px; }

  .bench-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
  .instruments { margin-top: 14px; }
  .chan {
    display: grid; grid-template-columns: minmax(0, 1fr) 72px 26px;
    align-items: center; gap: 12px;
    padding: 11px 0;
    border-top: 1px solid var(--hairline-soft);
    font-size: 12.5px; color: var(--ink-3);
  }
  .chan:last-child { border-bottom: 1px solid var(--hairline-soft); }
  .flatline { position: relative; height: 9px; }
  .flatline::before {
    content: ''; position: absolute; left: 0; right: 0; top: 4px;
    height: 1px; background: var(--hairline);
  }
  .flatline::after {
    content: ''; position: absolute; left: 0; top: 0;
    width: 1px; height: 9px; background: var(--hairline);
  }
  .chan .reading { font-size: 12.5px; text-align: right; color: var(--ink-3); }

  .oc { margin-top: 34px; }
  .oc-list { list-style: none; margin: 14px 0 0; padding: 0; }
  .oc-row {
    display: grid; grid-template-columns: 14px minmax(0, 1fr);
    align-items: start; gap: 11px;
    padding: 11px 0;
    border-top: 1px solid var(--hairline-soft);
    font-size: 12.5px; line-height: 1.5; color: var(--ink-3);
    transition: color 240ms ease;
  }
  .oc-row:last-child { border-bottom: 1px solid var(--hairline-soft); }
  .oc-row .glyph { color: var(--ink-3); margin-top: 2px; transition: color 240ms ease; }
  .oc-row[data-done="true"] { color: var(--ink); }
  .oc-row[data-done="true"] .glyph { color: var(--live); }

  @media (max-width: 960px) {
    .build { grid-template-columns: minmax(0, 560px); gap: 44px; }
  }
  @media (max-width: 560px) {
    .f { grid-template-columns: minmax(0, 1fr); gap: 8px; align-items: start; }
  }
`
