'use client'

import { useState, useEffect } from 'react'

type StepStatus = 'done' | 'active' | 'pending'

interface Step {
  label: string
  meta: string
  status: StepStatus
}

const JOURNEY: Step[][] = [
  // Frame 0 — Provision active
  [
    { label: 'Provision', meta: 'Setting up tenant database...', status: 'active' },
    { label: 'Configure', meta: 'Soul + knowledge base', status: 'pending' },
    { label: 'Test', meta: 'Evals + red team', status: 'pending' },
    { label: 'Deploy', meta: 'Embed widget live', status: 'pending' },
  ],
  // Frame 1 — Provision done, Configure active
  [
    { label: 'Provision', meta: 'Tenant database ready', status: 'done' },
    { label: 'Configure', meta: 'Ingesting 47 documents...', status: 'active' },
    { label: 'Test', meta: 'Evals + red team', status: 'pending' },
    { label: 'Deploy', meta: 'Embed widget live', status: 'pending' },
  ],
  // Frame 2 — Provision + Configure done, Test active
  [
    { label: 'Provision', meta: 'Tenant database ready', status: 'done' },
    { label: 'Configure', meta: 'Soul + 47 docs indexed', status: 'done' },
    { label: 'Test', meta: 'Running evals — pass rate 0.91...', status: 'active' },
    { label: 'Deploy', meta: 'Embed widget live', status: 'pending' },
  ],
  // Frame 3 — All done, Deploy active
  [
    { label: 'Provision', meta: 'Tenant database ready', status: 'done' },
    { label: 'Configure', meta: 'Soul + 47 docs indexed', status: 'done' },
    { label: 'Test', meta: 'Evals passed · 0 critical findings', status: 'done' },
    { label: 'Deploy', meta: 'Widget live on your site', status: 'active' },
  ],
]

const FRAME_DURATION = 2200 // ms each frame shows

export function HeroPipeline() {
  const [frame, setFrame] = useState(0)

  useEffect(() => {
    const id = setInterval(() => {
      setFrame((f) => (f + 1) % JOURNEY.length)
    }, FRAME_DURATION)
    return () => clearInterval(id)
  }, [])

  const steps = JOURNEY[frame]
  const activeIdx = steps.findIndex((s) => s.status === 'active')

  return (
    <div
      style={{
        background: 'var(--glass-bg)',
        backdropFilter: 'var(--glass-blur)',
        WebkitBackdropFilter: 'var(--glass-blur)',
        border: '1px solid var(--glass-border)',
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
        boxShadow: 'var(--shadow-lift)',
      }}
    >
      {/* Card header */}
      <div
        style={{
          padding: '18px 22px 14px',
          borderBottom: '1px solid var(--glass-border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '10.5px',
            fontWeight: 700,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: 'var(--text-3)',
          }}
        >
          Build Pipeline · Agent Alpha
        </span>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '5px',
            background: 'var(--accent-dim)',
            color: 'var(--accent)',
            fontSize: '9.5px',
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            padding: '3px 8px',
            borderRadius: 'var(--radius-pill)',
          }}
        >
          <span
            style={{
              width: '5px',
              height: '5px',
              borderRadius: '50%',
              background: 'var(--accent)',
              animation: 'blink-dot 1.1s ease-in-out infinite',
              display: 'inline-block',
            }}
          />
          Running
        </span>
      </div>

      {/* Steps */}
      <div style={{ padding: '18px 22px 22px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {steps.map((step, i) => {
          const isDone = step.status === 'done'
          const isActive = step.status === 'active'
          const isPending = step.status === 'pending'

          return (
            <div key={step.label}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '14px',
                  padding: '13px 16px',
                  borderRadius: 'var(--radius-xs)',
                  background: isActive ? 'rgba(244,116,140,0.06)' : 'transparent',
                  border: isActive
                    ? '1px solid rgba(244,116,140,0.18)'
                    : '1px solid transparent',
                  opacity: isPending ? 0.4 : 1,
                  transition: 'all 0.4s ease',
                }}
              >
                {/* Step indicator */}
                <div
                  style={{
                    width: '24px',
                    height: '24px',
                    borderRadius: '50%',
                    background: isDone
                      ? 'var(--green)'
                      : isActive
                      ? 'var(--accent)'
                      : 'var(--surface-3)',
                    color: isDone || isActive ? '#fff' : 'var(--text-4)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '11px',
                    fontWeight: 700,
                    flexShrink: 0,
                    boxShadow: isActive ? '0 0 0 4px rgba(244,116,140,0.20)' : 'none',
                    animation: isActive ? 'pulse-ring 1.6s ease-out infinite' : 'none',
                    transition: 'all 0.4s ease',
                  }}
                >
                  {isDone ? (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                  ) : (
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                      {isActive ? '◉' : (i + 1)}
                    </span>
                  )}
                </div>

                {/* Label + meta */}
                <div style={{ flex: 1 }}>
                  <div
                    style={{
                      fontSize: '13px',
                      fontWeight: isActive ? 600 : 500,
                      color: isDone ? 'var(--green)' : isActive ? 'var(--text-1)' : 'var(--text-3)',
                      transition: 'color 0.4s ease',
                    }}
                  >
                    {step.label}
                  </div>
                  <div
                    style={{
                      fontSize: '11px',
                      fontFamily: 'var(--font-mono)',
                      color: isDone ? 'var(--green)' : isActive ? 'var(--accent)' : 'var(--text-4)',
                      marginTop: '2px',
                      transition: 'color 0.4s ease',
                    }}
                  >
                    {step.meta}
                  </div>
                </div>

                {/* Right indicator */}
                {isDone && (
                  <span style={{ fontSize: '10px', fontWeight: 600, color: 'var(--green)', fontFamily: 'var(--font-mono)' }}>
                    done
                  </span>
                )}
                {isActive && (
                  <span
                    style={{
                      width: '6px',
                      height: '6px',
                      borderRadius: '50%',
                      background: 'var(--accent)',
                      animation: 'blink-dot 1.1s ease-in-out infinite',
                      display: 'inline-block',
                      flexShrink: 0,
                    }}
                  />
                )}
              </div>

              {/* Connector line */}
              {i < steps.length - 1 && (
                <div
                  style={{
                    width: '2px',
                    height: '8px',
                    marginLeft: '27px',
                    background: isDone ? 'var(--green)' : 'var(--border)',
                    borderRadius: '1px',
                    transition: 'background 0.4s ease',
                  }}
                />
              )}
            </div>
          )
        })}

        {/* Progress bar */}
        <div style={{ marginTop: '16px', height: '3px', background: 'var(--surface-3)', borderRadius: '2px', overflow: 'hidden' }}>
          <div
            style={{
              height: '100%',
              width: `${((frame + 1) / JOURNEY.length) * 100}%`,
              background: 'linear-gradient(90deg, var(--green) 0%, var(--accent) 100%)',
              borderRadius: '2px',
              transition: 'width 0.6s ease',
            }}
          />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '6px' }}>
          <span style={{ fontSize: '10px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
            Step {activeIdx + 1} of {steps.length}
          </span>
          <span style={{ fontSize: '10px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
            {Math.round(((frame + 1) / JOURNEY.length) * 100)}%
          </span>
        </div>
      </div>
    </div>
  )
}
