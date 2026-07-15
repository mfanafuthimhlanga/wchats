'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Check, Lock } from 'lucide-react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type StepState = 'done' | 'active' | 'locked'

export interface JourneyStep {
  num: 1 | 2 | 3 | 4
  key: 'provision' | 'configure' | 'test' | 'deploy'
  title: string
  subtitle: string
  state: StepState
  href?: string
}

export interface JourneyStepperProps {
  agentName: string
  agentRole: string
  steps: JourneyStep[]
}

// ---------------------------------------------------------------------------
// JourneyStepper
// ---------------------------------------------------------------------------

export default function JourneyStepper({ agentName, agentRole, steps }: JourneyStepperProps) {
  const pathname = usePathname()

  // Determine which step is the current page (exact href match wins; fall back
  // to the longest prefix match so sub-pages like /soul and /ingest still light
  // up their parent step).
  const currentStepKey = (
    steps.find((s) => s.href && s.href === pathname) ??
    steps.reduce<JourneyStep | null>((best, s) => {
      if (!s.href || !pathname.startsWith(s.href + '/')) return best
      if (!best || s.href.length > (best.href?.length ?? 0)) return s
      return best
    }, null)
  )?.key ?? null

  return (
    <aside
      className="glass-strong"
      style={{
        width: '280px',
        flexShrink: 0,
        borderRight: '1px solid var(--glass-border)',
        padding: '32px 24px',
      }}
    >
      {/* Agent header */}
      <h2
        style={{
          fontFamily: 'var(--font-display)',
          fontVariationSettings: '"opsz" 144, "SOFT" 30',
          fontSize: '16px',
          fontWeight: 700,
          color: 'var(--text-1)',
          marginBottom: '4px',
          margin: 0,
        }}
      >
        {agentName}
      </h2>
      <p
        style={{
          fontSize: '13px',
          color: 'var(--text-3)',
          marginBottom: '32px',
          marginTop: '4px',
        }}
      >
        {agentRole}
      </p>

      {/* Step list */}
      {steps.map((step, idx) => {
        const isLast = idx === steps.length - 1
        // If the user is on this step's page and data says locked, treat it as
        // active so the current page is never ghosted.
        const isCurrentPage = step.key === currentStepKey
        const visualState: StepState =
          isCurrentPage && step.state === 'locked' ? 'active' : step.state

        const containerStyle: React.CSSProperties = {
          position: 'relative',
          padding: '12px',
          borderRadius: 'var(--radius-xs)',
          marginBottom: '8px',
          ...(visualState === 'active'
            ? { background: 'rgba(244,116,140,0.28)', border: '1px solid var(--accent)', boxShadow: '0 0 0 3px rgba(244,116,140,0.12)' }
            : visualState === 'done'
            ? { background: 'var(--green-bg)', border: '1px solid rgba(52,211,153,0.25)' }
            : { background: 'transparent', border: '1px solid transparent', opacity: 0.65 }),
        }

        const circleStyle: React.CSSProperties = {
          width: '28px',
          height: '28px',
          borderRadius: '50%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '13px',
          fontWeight: 600,
          flexShrink: 0,
          ...(visualState === 'done'
            ? { background: 'var(--green)', color: 'var(--text-on-accent)' }
            : visualState === 'active'
            ? { background: 'rgba(244,116,140,0.22)', border: '2px solid var(--accent)', color: 'var(--accent)' }
            : { background: 'var(--chip)', border: '1px solid var(--border)', color: 'var(--text-4)' }),
        }

        const rowContent = (
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            {/* Circle indicator — check when done, padlock when locked, else the step number */}
            <div style={circleStyle}>
              {visualState === 'done' ? (
                <Check size={15} strokeWidth={2} />
              ) : visualState === 'locked' ? (
                <Lock size={14} strokeWidth={2} />
              ) : (
                step.num
              )}
            </div>

            {/* Text block */}
            <div style={{ flex: 1 }}>
              <div
                style={{
                  fontSize: '14px',
                  fontWeight: 600,
                  color: 'var(--text-1)',
                }}
              >
                {step.title}
              </div>
              <div
                style={{
                  fontSize: '12px',
                  color: 'var(--text-2)',
                }}
              >
                {step.subtitle}
              </div>
            </div>

            {/* Done badge */}
            {visualState === 'done' && (
              <span
                style={{
                  marginLeft: 'auto',
                  padding: '2px 8px',
                  background: 'var(--green-bg)',
                  color: 'var(--green)',
                  borderRadius: 'var(--radius-pill)',
                  fontSize: '11px',
                  fontWeight: 600,
                  whiteSpace: 'nowrap',
                }}
              >
                Done
              </span>
            )}
          </div>
        )

        return (
          <div key={step.key} style={containerStyle}>
            {/* Row: optionally wrapped in Link */}
            {step.href && visualState !== 'locked' ? (
              <Link
                href={step.href}
                style={{ textDecoration: 'none', color: 'inherit', display: 'block' }}
                aria-label={`${step.title} — ${visualState === 'done' ? 'completed' : 'in progress'}`}
              >
                {rowContent}
              </Link>
            ) : (
              rowContent
            )}

            {/* Vertical connector line (all steps except last) */}
            {!isLast && (
              <span
                style={{
                  position: 'absolute',
                  left: '25px',
                  top: '52px',
                  bottom: '-8px',
                  width: '2px',
                  background: visualState === 'done' ? 'var(--green)' : 'var(--border-soft)',
                  opacity: visualState === 'done' ? 0.4 : 1,
                }}
              />
            )}
          </div>
        )
      })}
    </aside>
  )
}
