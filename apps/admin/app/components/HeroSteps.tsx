'use client'

import { useEffect, useState } from 'react'

const STEPS = [
  { num: 1, title: '1 · Provision', sub: 'Dedicated tenant database ready' },
  { num: 2, title: '2 · Configure', sub: 'Soul + documents ingested' },
  { num: 3, title: '3 · Test',      sub: 'Evals running' },
  { num: 4, title: '4 · Deploy',    sub: 'Embed widget live' },
]

type StepState = 'done' | 'active' | 'upcoming'

function stepState(i: number, active: number): StepState {
  if (i < active)  return 'done'
  if (i === active) return 'active'
  return 'upcoming'
}

export function HeroSteps() {
  const [active, setActive] = useState(2)

  useEffect(() => {
    const id = setInterval(() => setActive(p => (p + 1) % STEPS.length), 2400)
    return () => clearInterval(id)
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {STEPS.map((step, i) => {
        const state = stepState(i, active)
        const isDone     = state === 'done'
        const isActive   = state === 'active'
        const isUpcoming = state === 'upcoming'

        return (
          <div key={step.num}>
            {/* ── Card ── */}
            <div
              style={{
                padding: '16px 18px',
                background: isUpcoming ? 'var(--surface-2)' : 'var(--surface-1)',
                border: isActive
                  ? '1.5px solid var(--accent)'
                  : isDone
                  ? '1px solid var(--border)'
                  : '1px solid var(--border-soft)',
                borderRadius: 'var(--radius-xs)',
                boxShadow: isActive
                  ? 'var(--shadow-lift), 0 0 0 3px var(--accent-dim)'
                  : isDone
                  ? 'var(--shadow-card)'
                  : 'none',
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
                opacity: isUpcoming ? 0.45 : 1,
                transition: 'opacity 0.5s ease, box-shadow 0.5s ease, border-color 0.5s ease, background 0.5s ease',
              }}
            >
              {/* Circle */}
              <div
                style={{
                  width: '28px',
                  height: '28px',
                  borderRadius: '50%',
                  flexShrink: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontWeight: 700,
                  fontSize: '12px',
                  transition: 'background 0.5s ease, color 0.5s ease, border-color 0.5s ease',
                  ...(isDone ? {
                    background: 'var(--accent)',
                    color: '#fff',
                    border: 'none',
                  } : isActive ? {
                    background: 'var(--accent-dim)',
                    color: 'var(--accent)',
                    border: '2px solid var(--accent)',
                    animation: 'pulse-ring 1.6s ease-out infinite',
                  } : {
                    background: 'var(--surface-3)',
                    color: 'var(--text-4)',
                    border: '1px solid var(--border-soft)',
                  }),
                }}
              >
                {isDone ? '✓' : step.num}
              </div>

              {/* Text */}
              <div style={{ flex: 1 }}>
                <div style={{
                  fontSize: '14px',
                  fontWeight: 600,
                  color: isUpcoming ? 'var(--text-3)' : 'var(--text-1)',
                  transition: 'color 0.5s ease',
                }}>
                  {step.title}
                </div>
                <div style={{
                  fontSize: '12px',
                  color: isActive ? 'var(--text-2)' : 'var(--text-3)',
                  marginTop: '2px',
                  transition: 'color 0.5s ease',
                }}>
                  {step.sub}
                </div>
              </div>

              {/* Live dot on active step */}
              {isActive && (
                <div style={{
                  width: '7px',
                  height: '7px',
                  borderRadius: '50%',
                  background: 'var(--accent)',
                  flexShrink: 0,
                  animation: 'blink-dot 1.1s ease-in-out infinite',
                }} />
              )}
            </div>

            {/* ── Connector ── */}
            {i < STEPS.length - 1 && (
              <div style={{
                marginLeft: '31px',
                width: '2px',
                height: '12px',
                background: isDone ? 'var(--accent)' : 'var(--border-soft)',
                transition: 'background 0.5s ease',
                borderRadius: '1px',
              }} />
            )}
          </div>
        )
      })}
    </div>
  )
}
