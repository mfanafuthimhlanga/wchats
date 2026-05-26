'use client'

const STEPS = [
  { label: 'Provision Neon · parse 47 documents', status: 'done' as const },
  { label: 'Generate metadata · entities · embed', status: 'done' as const },
  { label: 'Synthesise retrieval strategy', status: 'active' as const },
  { label: 'Red team · pre-deploy checklist', status: 'locked' as const },
]

export function HeroPipeline() {
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
            background: 'var(--gold-bg)',
            color: 'var(--gold)',
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
              background: 'var(--gold)',
              animation: 'blink-dot 1.1s ease-in-out infinite',
              display: 'inline-block',
            }}
          />
          Running
        </span>
      </div>

      {/* Steps */}
      <div style={{ padding: '18px 22px 22px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {STEPS.map((step, i) => {
          const isDone = step.status === 'done'
          const isActive = step.status === 'active'
          const isLocked = step.status === 'locked'

          const dotBg = isDone
            ? 'var(--green)'
            : isActive
            ? 'var(--accent)'
            : 'var(--surface-3)'

          const dotColor = isDone || isActive ? '#fff' : 'var(--text-4)'

          const rowStyle: React.CSSProperties = {
            display: 'flex',
            alignItems: 'center',
            gap: '14px',
            padding: '13px 16px',
            borderRadius: 'var(--radius-xs)',
            background: isActive ? 'rgba(244,116,140,0.06)' : 'transparent',
            border: isActive
              ? '1px solid rgba(244,116,140,0.18)'
              : '1px solid transparent',
            opacity: isLocked ? 0.4 : 1,
            transition: 'all 0.3s ease',
          }

          return (
            <div key={i}>
              <div style={rowStyle}>
                {/* Step indicator */}
                <div
                  style={{
                    width: '24px',
                    height: '24px',
                    borderRadius: '50%',
                    background: dotBg,
                    color: dotColor,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '11px',
                    fontWeight: 700,
                    flexShrink: 0,
                    border: isActive ? '2px solid var(--accent)' : 'none',
                    boxShadow: isActive ? '0 0 0 3px rgba(244,116,140,0.2)' : 'none',
                    animation: isActive ? 'pulse-ring 1.6s ease-out infinite' : 'none',
                  }}
                >
                  {isDone ? '✓' : isActive ? '◉' : '○'}
                </div>

                {/* Label */}
                <span
                  style={{
                    fontSize: '13px',
                    fontWeight: isActive ? 600 : 500,
                    color: isDone
                      ? 'var(--green)'
                      : isActive
                      ? 'var(--text-1)'
                      : 'var(--text-3)',
                    flex: 1,
                    lineHeight: 1.3,
                  }}
                >
                  {step.label}
                </span>

                {/* Right status mark */}
                {isDone && (
                  <span
                    style={{
                      fontSize: '10px',
                      fontWeight: 600,
                      color: 'var(--green)',
                      fontFamily: 'var(--font-mono)',
                    }}
                  >
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
              {i < STEPS.length - 1 && (
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
        <div
          style={{
            marginTop: '16px',
            height: '3px',
            background: 'var(--surface-3)',
            borderRadius: '2px',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              height: '100%',
              width: '60%',
              background: 'linear-gradient(90deg, var(--green) 0%, var(--accent) 100%)',
              borderRadius: '2px',
            }}
          />
        </div>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            marginTop: '6px',
          }}
        >
          <span style={{ fontSize: '10px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
            Step 3 of 4
          </span>
          <span style={{ fontSize: '10px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
            60%
          </span>
        </div>
      </div>
    </div>
  )
}
