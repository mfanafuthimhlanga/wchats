'use client'
import Link from 'next/link'

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
  return (
    <aside
      style={{
        width: '320px',
        flexShrink: 0,
        borderRight: '1px solid var(--border-soft)',
        padding: '32px 24px',
        background: 'var(--bg)',
      }}
    >
      {/* Agent header */}
      <h2
        style={{
          fontSize: '18px',
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
        const containerStyle: React.CSSProperties = {
          position: 'relative',
          padding: '12px',
          borderRadius: 'var(--radius-xs)',
          marginBottom: '8px',
          ...(step.state === 'active'
            ? { background: 'var(--accent-dim)', border: '1px solid rgba(123,28,58,0.12)' }
            : step.state === 'done'
            ? { background: 'transparent', border: '1px solid transparent' }
            : { background: 'transparent', border: '1px solid transparent', opacity: 0.6 }),
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
          ...(step.state === 'done'
            ? { background: 'var(--accent)', color: '#fff' }
            : step.state === 'active'
            ? { background: '#fff', border: '2px solid var(--accent)', color: 'var(--accent)' }
            : { background: 'var(--surface-3)', border: '1px solid var(--border)', color: 'var(--text-4)' }),
        }

        const rowContent = (
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            {/* Circle indicator */}
            <div style={circleStyle}>
              {step.state === 'done' ? '✓' : step.num}
            </div>

            {/* Text block */}
            <div style={{ flex: 1 }}>
              <div
                style={{
                  fontSize: '14px',
                  fontWeight: 600,
                  color: step.state === 'locked' ? 'var(--text-4)' : 'var(--text-1)',
                }}
              >
                {step.title}
              </div>
              <div
                style={{
                  fontSize: '12px',
                  color: step.state === 'locked' ? 'var(--text-4)' : 'var(--text-3)',
                }}
              >
                {step.subtitle}
              </div>
            </div>

            {/* Done badge */}
            {step.state === 'done' && (
              <span
                style={{
                  marginLeft: 'auto',
                  padding: '2px 8px',
                  background: 'var(--green-bg)',
                  color: 'var(--green)',
                  borderRadius: '999px',
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
            {step.href && step.state !== 'locked' ? (
              <Link
                href={step.href}
                style={{ textDecoration: 'none', color: 'inherit', display: 'block' }}
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
                  background: step.state === 'done' ? 'var(--accent)' : 'var(--border-soft)',
                  opacity: step.state === 'done' ? 0.35 : 1,
                }}
              />
            )}
          </div>
        )
      })}
    </aside>
  )
}
