'use client'

import { useState, useEffect } from 'react'

// ── Pipeline frames ───────────────────────────────────────────────────────────

type StepStatus = 'done' | 'active' | 'pending'

interface Step {
  label: string
  meta: string
  status: StepStatus
}

const PIPELINE: Step[][] = [
  [
    { label: 'Provision', meta: 'Setting up tenant database...', status: 'active' },
    { label: 'Configure', meta: 'Soul + knowledge base', status: 'pending' },
    { label: 'Test', meta: 'Evals + red team', status: 'pending' },
    { label: 'Deploy', meta: 'Embed widget live', status: 'pending' },
  ],
  [
    { label: 'Provision', meta: 'Tenant database ready', status: 'done' },
    { label: 'Configure', meta: 'Ingesting docs...', status: 'active' },
    { label: 'Test', meta: 'Evals + red team', status: 'pending' },
    { label: 'Deploy', meta: 'Embed widget live', status: 'pending' },
  ],
  [
    { label: 'Provision', meta: 'Tenant database ready', status: 'done' },
    { label: 'Configure', meta: 'Docs ingested', status: 'done' },
    { label: 'Test', meta: 'Running evals — pass rate 0.91...', status: 'active' },
    { label: 'Deploy', meta: 'Embed widget live', status: 'pending' },
  ],
  [
    { label: 'Provision', meta: 'Tenant database ready', status: 'done' },
    { label: 'Configure', meta: 'Docs ingested', status: 'done' },
    { label: 'Test', meta: 'Evals passed · 0 critical findings', status: 'done' },
    { label: 'Deploy', meta: 'Widget live on your site', status: 'active' },
  ],
]

// ── Widget chat frames ────────────────────────────────────────────────────────

type ChatMsg = { role: 'agent' | 'user'; text: string; typing?: true }
type Receipt = { ref: string; item: string; status: 'processing' | 'confirmed' }
type ChatState = { messages: ChatMsg[]; receipt?: Receipt }

const CHAT: ChatState[] = [
  // Frame 4 — agent greeting appears
  {
    messages: [
      { role: 'agent', text: "Hi! I'm Acme's support agent. How can I help you today?" },
    ],
  },
  // Frame 5 — user sends request, agent starts typing
  {
    messages: [
      { role: 'agent', text: "Hi! I'm Acme's support agent. How can I help you today?" },
      { role: 'user', text: 'My order #ORD-7821 arrived damaged, I need a replacement' },
      { role: 'agent', text: '', typing: true },
    ],
  },
  // Frame 6 — agent responds, receipt processing
  {
    messages: [
      { role: 'agent', text: "Hi! I'm Acme's support agent. How can I help you today?" },
      { role: 'user', text: 'My order #ORD-7821 arrived damaged, I need a replacement' },
      { role: 'agent', text: 'Found ORD-7821 ✓ · Damage noted · Raising replacement now...' },
    ],
    receipt: { ref: 'REP-3C9A', item: 'Premium Kit × 1', status: 'processing' },
  },
  // Frame 7 — replacement confirmed
  {
    messages: [
      { role: 'agent', text: "Hi! I'm Acme's support agent. How can I help you today?" },
      { role: 'user', text: 'My order #ORD-7821 arrived damaged, I need a replacement' },
      { role: 'agent', text: "Replacement raised ✓ · Ships in 1–2 business days · You'll get a confirmation email shortly." },
    ],
    receipt: { ref: 'REP-3C9A', item: 'Premium Kit × 1', status: 'confirmed' },
  },
]

const PIPELINE_COUNT = PIPELINE.length
const TOTAL_FRAMES = PIPELINE_COUNT + CHAT.length
const FRAME_DURATION = 2000

// ── Component ─────────────────────────────────────────────────────────────────

export function HeroPipeline() {
  const [frame, setFrame] = useState(0)

  useEffect(() => {
    const id = setInterval(() => {
      setFrame((f) => (f + 1) % TOTAL_FRAMES)
    }, FRAME_DURATION)
    return () => clearInterval(id)
  }, [])

  const mode = frame < PIPELINE_COUNT ? 'pipeline' : 'chat'
  const pipelineSteps = mode === 'pipeline' ? PIPELINE[frame] : null
  const chatState = mode === 'chat' ? CHAT[frame - PIPELINE_COUNT] : null
  const activeIdx = pipelineSteps ? pipelineSteps.findIndex((s) => s.status === 'active') : -1

  return (
    <div
      className="glass"
      style={{
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
      }}
    >
      {/* ── Card header ── */}
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
            fontWeight: 600,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: 'var(--text-3)',
            transition: 'opacity 0.35s ease',
          }}
        >
          {mode === 'pipeline' ? 'Build pipeline · agent.alpha' : 'Widget preview · acme-demo.com'}
        </span>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '5px',
            background: mode === 'pipeline' ? 'var(--accent-dim)' : 'var(--green-bg)',
            color: mode === 'pipeline' ? 'var(--accent)' : 'var(--green)',
            fontSize: '9.5px',
            fontWeight: 600,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            padding: '3px 8px',
            borderRadius: 'var(--radius-pill)',
            transition: 'background 0.35s ease, color 0.35s ease',
          }}
        >
          <span
            style={{
              width: '5px',
              height: '5px',
              borderRadius: '50%',
              background: mode === 'pipeline' ? 'var(--accent)' : 'var(--green)',
              animation: 'blink-dot 1.1s ease-in-out infinite',
              display: 'inline-block',
              transition: 'background 0.35s ease',
            }}
          />
          {mode === 'pipeline' ? 'Running' : 'Live'}
        </span>
      </div>

      {/* ── Pipeline body ── */}
      {pipelineSteps && (
        <div style={{ padding: '18px 22px 22px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {pipelineSteps.map((step, i) => {
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
                    border: isActive ? '1px solid rgba(244,116,140,0.18)' : '1px solid transparent',
                    opacity: isPending ? 0.4 : 1,
                    transition: 'all 0.4s ease',
                  }}
                >
                  <div
                    style={{
                      width: '24px',
                      height: '24px',
                      borderRadius: '50%',
                      background: isDone ? 'var(--green)' : isActive ? 'var(--accent)' : 'var(--chip)',
                      color: isDone || isActive ? 'var(--text-on-accent)' : 'var(--text-4)',
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
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    ) : (
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                        {isActive ? '◉' : (i + 1)}
                      </span>
                    )}
                  </div>

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

                {i < pipelineSteps.length - 1 && (
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
          <div style={{ marginTop: '16px', height: '3px', background: 'var(--well)', borderRadius: '2px', overflow: 'hidden' }}>
            <div
              style={{
                height: '100%',
                width: `${((frame + 1) / PIPELINE_COUNT) * 100}%`,
                background: 'linear-gradient(90deg, var(--green) 0%, var(--accent) 100%)',
                borderRadius: '2px',
                transition: 'width 0.6s ease',
              }}
            />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '6px' }}>
            <span style={{ fontSize: '10px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
              Step {activeIdx + 1} of {pipelineSteps.length}
            </span>
            <span style={{ fontSize: '10px', color: 'var(--text-4)', fontFamily: 'var(--font-mono)' }}>
              {Math.round(((frame + 1) / PIPELINE_COUNT) * 100)}%
            </span>
          </div>
        </div>
      )}

      {/* ── Widget chat body ── */}
      {chatState && (
        <div style={{ padding: '14px 16px 18px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {chatState.messages.map((msg, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                alignItems: 'flex-end',
                gap: '8px',
              }}
            >
              {msg.role === 'agent' && (
                <div
                  style={{
                    width: '22px',
                    height: '22px',
                    borderRadius: '50%',
                    background: 'var(--accent-dim)',
                    border: '1px solid rgba(244,116,140,0.3)',
                    flexShrink: 0,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '10px',
                    color: 'var(--accent)',
                  }}
                >
                  ◈
                </div>
              )}

              {msg.typing ? (
                <div
                  style={{
                    background: 'var(--well)',
                    border: '1px solid var(--glass-border)',
                    borderRadius: '10px 10px 10px 2px',
                    padding: '10px 14px',
                    display: 'flex',
                    gap: '4px',
                    alignItems: 'center',
                  }}
                >
                  {[0, 1, 2].map((d) => (
                    <span
                      key={d}
                      style={{
                        width: '5px',
                        height: '5px',
                        borderRadius: '50%',
                        background: 'var(--text-4)',
                        display: 'inline-block',
                        animation: `blink-dot 1.1s ease-in-out ${d * 0.18}s infinite`,
                      }}
                    />
                  ))}
                </div>
              ) : (
                <div
                  style={{
                    background: msg.role === 'user' ? 'var(--accent)' : 'var(--well)',
                    border: msg.role === 'user' ? 'none' : '1px solid var(--glass-border)',
                    borderRadius: msg.role === 'user' ? '10px 10px 2px 10px' : '10px 10px 10px 2px',
                    padding: '8px 12px',
                    fontSize: '12px',
                    color: msg.role === 'user' ? 'var(--text-on-accent)' : 'var(--text-1)',
                    maxWidth: '82%',
                    lineHeight: 1.45,
                    fontWeight: msg.role === 'user' ? 500 : 400,
                  }}
                >
                  {msg.text}
                </div>
              )}
            </div>
          ))}

          {/* Receipt card */}
          {chatState.receipt && (
            <div
              style={{
                marginLeft: '30px',
                border: `1px solid ${chatState.receipt.status === 'confirmed' ? 'rgba(52,211,153,0.35)' : 'var(--glass-border)'}`,
                borderRadius: 'var(--radius-xs)',
                padding: '10px 12px',
                background: chatState.receipt.status === 'confirmed' ? 'var(--green-bg)' : 'var(--chip)',
                transition: 'all 0.45s ease',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '5px' }}>
                <span
                  style={{
                    fontSize: '9.5px',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 600,
                    letterSpacing: '0.1em',
                    textTransform: 'uppercase',
                    color: chatState.receipt.status === 'confirmed' ? 'var(--green)' : 'var(--text-3)',
                    transition: 'color 0.4s ease',
                  }}
                >
                  {chatState.receipt.status === 'confirmed' ? 'Replacement confirmed' : 'Processing...'}
                </span>
                <span style={{ fontSize: '9px', fontFamily: 'var(--font-mono)', color: 'var(--text-4)' }}>
                  {chatState.receipt.ref}
                </span>
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-2)' }}>
                {chatState.receipt.item}
              </div>
              {chatState.receipt.status === 'confirmed' && (
                <div style={{ fontSize: '10px', color: 'var(--green)', fontFamily: 'var(--font-mono)', marginTop: '4px' }}>
                  Ships 1–2 business days
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
