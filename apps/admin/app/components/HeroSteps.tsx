'use client'

import { useEffect, useRef, useState, CSSProperties } from 'react'

// ── Types ───────────────────────────────────────────────────────────────────
type Phase = 'steps' | 'widget'
type StepStatus = 'upcoming' | 'active' | 'done'

type ExtraItem =
  | { type: 'citations'; chips: string[] }
  | { type: 'otp'; digits: string[]; activeIndex: number }
  | { type: 'verified' }
  | { type: 'stripe'; cardNum: string; expiry: string; cvc: string; cardActive: boolean; paid: boolean }
  | { type: 'receipt' }

type MessageItem =
  | { id: string; kind: 'agent'; html: string; extras?: ExtraItem[] }
  | { id: string; kind: 'user'; text: string }

const STEP_LABELS = [
  { title: 'Provision', activeSub: 'Provisioning tenant database…', doneSub: 'Dedicated tenant database ready' },
  { title: 'Configure', activeSub: 'Ingesting soul + documents…',   doneSub: 'Soul + documents ingested' },
  { title: 'Test',      activeSub: 'Running evals…',                doneSub: 'Evals passed' },
  { title: 'Deploy',    activeSub: 'Deploying widget…',             doneSub: 'Embed widget live' },
]

const INITIAL_STATUSES: StepStatus[] = ['upcoming', 'upcoming', 'upcoming', 'upcoming']

let __msgIdCounter = 0
const nextId = () => `m-${++__msgIdCounter}`

export function HeroSteps() {
  const [phase, setPhase] = useState<Phase>('steps')
  const [stepStatuses, setStepStatuses] = useState<StepStatus[]>(INITIAL_STATUSES)
  const [messages, setMessages] = useState<MessageItem[]>([])
  const [inputText, setInputText] = useState('')
  const [showCaret, setShowCaret] = useState(false)
  const [showTyping, setShowTyping] = useState(false)
  const [showSources, setShowSources] = useState(false)

  const messagesRef = useRef<HTMLDivElement | null>(null)
  const timers = useRef<number[]>([])
  const otpMsgId = useRef<string | null>(null)
  const stripeMsgId = useRef<string | null>(null)

  // ── Timer helper ─────────────────────────────────────────────────────────
  const later = (fn: () => void, ms: number): number => {
    const id = window.setTimeout(fn, ms)
    timers.current.push(id)
    return id
  }

  const clearAllTimers = () => {
    timers.current.forEach(id => clearTimeout(id))
    timers.current = []
  }

  // ── Message helpers ──────────────────────────────────────────────────────
  const addAgentMsg = (html: string, extras?: ExtraItem[]): string => {
    const id = nextId()
    setMessages(prev => [...prev, { id, kind: 'agent', html, extras }])
    return id
  }

  const addUserMsg = (text: string): string => {
    const id = nextId()
    setMessages(prev => [...prev, { id, kind: 'user', text }])
    return id
  }

  const appendExtraToMsg = (msgId: string, extra: ExtraItem) => {
    setMessages(prev => prev.map(m => {
      if (m.id !== msgId || m.kind !== 'agent') return m
      return { ...m, extras: [...(m.extras || []), extra] }
    }))
  }

  const updateOtpDigits = (msgId: string, newDigits: string[]) => {
    setMessages(prev => prev.map(m => {
      if (m.id !== msgId || m.kind !== 'agent' || !m.extras) return m
      const newExtras = m.extras.map(e =>
        e.type === 'otp' ? { ...e, digits: newDigits, activeIndex: newDigits.length } : e
      )
      return { ...m, extras: newExtras }
    }))
  }

  const updateStripe = (msgId: string, patch: Partial<Extract<ExtraItem, { type: 'stripe' }>>) => {
    setMessages(prev => prev.map(m => {
      if (m.id !== msgId || m.kind !== 'agent' || !m.extras) return m
      const newExtras = m.extras.map(e =>
        e.type === 'stripe' ? { ...e, ...patch } : e
      )
      return { ...m, extras: newExtras }
    }))
  }

  // Typing into input bar — char by char with caret
  const typeIntoInput = (text: string, perChar: number, onDone: () => void): number => {
    setShowCaret(true)
    setInputText('')
    let i = 0
    const tick = () => {
      i++
      setInputText(text.slice(0, i))
      if (i < text.length) {
        later(tick, perChar)
      } else {
        later(() => {
          setShowCaret(false)
          onDone()
        }, 250)
      }
    }
    return later(tick, perChar)
  }

  // ── Auto-scroll ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight
    }
  }, [messages, showTyping, showSources])

  // ── Animation sequence ───────────────────────────────────────────────────
  useEffect(() => {
    const runSequence = () => {
      // Reset all state at start of each cycle
      clearAllTimers()
      setPhase('steps')
      setStepStatuses(INITIAL_STATUSES)
      setMessages([])
      setInputText('')
      setShowCaret(false)
      setShowTyping(false)
      setShowSources(false)
      otpMsgId.current = null
      stripeMsgId.current = null

      // PHASE 1 — Steps
      later(() => setStepStatuses(['active', 'upcoming', 'upcoming', 'upcoming']), 0)
      later(() => setStepStatuses(['done', 'active', 'upcoming', 'upcoming']), 2000)
      later(() => setStepStatuses(['done', 'done', 'active', 'upcoming']), 4000)
      later(() => setStepStatuses(['done', 'done', 'done', 'active']), 6000)
      later(() => setStepStatuses(['done', 'done', 'done', 'done']), 8200)
      later(() => setPhase('widget'), 9000)

      // PHASE 2 — Greeting
      later(() => setShowTyping(true), 9000)
      later(() => {
        setShowTyping(false)
        addAgentMsg("Hi! 👋 I'm Maya, Lakewood Bakery's assistant. How can I help you today?")
      }, 10000)

      // PHASE 3 — Delivery question
      later(() => {
        typeIntoInput("Delivery areas and hours?", 45, () => {
          addUserMsg("Delivery areas and hours?")
          setInputText('')
        })
      }, 11200)

      // PHASE 4 — RAG retrieval
      later(() => setShowTyping(true), 13000)
      later(() => setShowSources(true), 13500)
      later(() => {
        setShowTyping(false)
        setShowSources(false)
      }, 15000)

      // PHASE 5 — Answer with citations
      later(() => {
        const id = addAgentMsg("We deliver to <strong>Northside</strong>, <strong>Eastpark</strong> & <strong>Downtown</strong>. Mon–Fri 9am–6pm · Sat 9am–2pm. No Sunday delivery.")
        later(() => {
          appendExtraToMsg(id, { type: 'citations', chips: ['[1] Delivery Policy', '[2] Opening Hours'] })
        }, 300)
      }, 15000)

      // PHASE 6 — Order
      // "I'd love 3 tiramisu for my birthday next Tuesday 🎂" = ~51 chars × 35ms + 250ms = ~2035ms
      // → user msg appears at ~18835ms
      later(() => {
        typeIntoInput("I'd love 3 tiramisu for my birthday next Tuesday 🎂", 35, () => {
          addUserMsg("I'd love 3 tiramisu for my birthday next Tuesday 🎂")
          setInputText('')
        })
      }, 16800)

      // PHASE 7 — Collect details (after user msg ~18835ms)
      later(() => setShowTyping(true), 19300)
      later(() => {
        setShowTyping(false)
        addAgentMsg("Lovely choice! 🎉 I need your full name, email & delivery address.")
      }, 20000)

      // PHASE 8 — Customer details (after agent msg 20000ms)
      // "Sarah Mitchell · sarah@example.com · 14 Maple Ave, Northside" = 60 chars × 30ms + 250ms = 2050ms
      // → user msg appears at ~23150ms
      later(() => {
        typeIntoInput("Sarah Mitchell · sarah@example.com · 14 Maple Ave, Northside", 30, () => {
          addUserMsg("Sarah Mitchell · sarah@example.com · 14 Maple Ave, Northside")
          setInputText('')
        })
      }, 21100)

      // PHASE 9 — OTP (after user msg ~23150ms)
      later(() => setShowTyping(true), 23500)
      later(() => {
        setShowTyping(false)
        const id = addAgentMsg("I've sent a 6-digit code to sarah@example.com. Enter it here:")
        otpMsgId.current = id
        later(() => {
          appendExtraToMsg(id, { type: 'otp', digits: [], activeIndex: 0 })
        }, 300)

        // Fill digits one by one (relative to when this callback fires at ~24200ms)
        const digits = ['8', '4', '7', '2', '9', '1']
        digits.forEach((d, idx) => {
          later(() => {
            if (!otpMsgId.current) return
            const accumulated = digits.slice(0, idx + 1)
            updateOtpDigits(otpMsgId.current, accumulated)
          }, 500 + 280 * (idx + 1))
        })
      }, 24200)

      // PHASE 10 — Verified + Stripe (last OTP digit at ~26380ms)
      later(() => {
        if (otpMsgId.current) {
          appendExtraToMsg(otpMsgId.current, { type: 'verified' })
        }
      }, 26700)

      later(() => {
        const id = addAgentMsg("✓ Verified! Complete your payment for 3 × Tiramisu ($89.97):", [{
          type: 'stripe',
          cardNum: '',
          expiry: '',
          cvc: '',
          cardActive: false,
          paid: false,
        }])
        stripeMsgId.current = id
      }, 27000)

      // Activate card field
      later(() => {
        if (stripeMsgId.current) {
          updateStripe(stripeMsgId.current, { cardActive: true })
        }
      }, 27500)

      // Type card number char by char (19 chars × 60ms = 1140ms → done at ~28690ms)
      const cardStr = "4242 4242 4242 4242"
      cardStr.split('').forEach((_, idx) => {
        later(() => {
          if (stripeMsgId.current) {
            updateStripe(stripeMsgId.current, { cardNum: cardStr.slice(0, idx + 1) })
          }
        }, 27550 + 60 * (idx + 1))
      })

      // Fill expiry + cvc
      later(() => {
        if (stripeMsgId.current) {
          updateStripe(stripeMsgId.current, { cardActive: false, expiry: '04 / 28', cvc: '424' })
        }
      }, 28900)

      // Set paid
      later(() => {
        if (stripeMsgId.current) {
          updateStripe(stripeMsgId.current, { paid: true })
        }
      }, 29200)

      // PHASE 11 — Receipt (after paid 29200ms)
      later(() => {
        addAgentMsg("🎉 Payment confirmed! Order #VRD-2847 placed.")
      }, 29500)

      later(() => {
        addAgentMsg("Here's your receipt:", [{ type: 'receipt' }])
      }, 29800)

      // PHASE 12 — Loop
      later(() => setPhase('steps'), 32800)
      later(() => runSequence(), 33500)
    }

    runSequence()

    return () => clearAllTimers()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Renderers ────────────────────────────────────────────────────────────
  const renderStepCard = (i: number) => {
    const status = stepStatuses[i]
    const label = STEP_LABELS[i]
    const isUpcoming = status === 'upcoming'
    const isActive = status === 'active'
    const isDone = status === 'done'
    const prevDone = i > 0 && stepStatuses[i - 1] === 'done'

    const cardStyle: CSSProperties = {
      display: 'flex',
      alignItems: 'center',
      gap: 14,
      padding: '16px 18px',
      borderRadius: 'var(--radius-xs)',
      transition: 'all 0.5s ease',
      background: isUpcoming ? 'var(--surface-2)' : 'var(--surface-1)',
      border: isActive
        ? '1.5px solid var(--accent)'
        : isDone
          ? '1px solid var(--green)'
          : '1px solid var(--border-soft)',
      boxShadow: isActive
        ? 'var(--shadow-lift), 0 0 0 3px var(--accent-dim)'
        : isDone
          ? 'var(--shadow-card)'
          : 'none',
      opacity: isUpcoming ? 0.45 : 1,
    }

    const circleStyle: CSSProperties = {
      width: 28,
      height: 28,
      borderRadius: '50%',
      flexShrink: 0,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontWeight: 700,
      fontSize: 12,
      transition: 'background 0.5s ease, color 0.5s ease, border-color 0.5s ease',
      ...(isDone
        ? { background: 'var(--green)', color: '#fff', border: 'none' }
        : isActive
          ? {
              background: 'var(--accent-dim)',
              color: 'var(--accent)',
              border: '2px solid var(--accent)',
              animation: 'pulse-ring 1.6s ease-out infinite',
            }
          : {
              background: 'var(--surface-3)',
              color: 'var(--text-4)',
              border: '1px solid var(--border-soft)',
            }),
    }

    const subColor = isActive
      ? 'var(--accent)'
      : isDone
        ? 'var(--green)'
        : 'var(--text-3)'

    const subText = isActive ? label.activeSub : label.doneSub

    return (
      <div key={i}>
        <div style={cardStyle}>
          <div style={circleStyle}>{isDone ? '✓' : i + 1}</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontSize: 14,
              fontWeight: 600,
              color: isUpcoming ? 'var(--text-3)' : 'var(--text-1)',
              transition: 'color 0.5s ease',
            }}>
              {`${i + 1} · ${label.title}`}
            </div>
            <div style={{
              fontSize: 12,
              color: subColor,
              marginTop: 2,
              transition: 'color 0.5s ease',
            }}>
              {subText}
            </div>
          </div>
          <div style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: 'var(--accent)',
            flexShrink: 0,
            opacity: isActive ? 1 : 0,
            animation: isActive ? 'blink-dot 1.1s ease-in-out infinite' : 'none',
            transition: 'opacity 0.3s ease',
          }} />
        </div>
        {i < STEP_LABELS.length - 1 && (
          <div style={{
            width: 2,
            height: 10,
            marginLeft: 13,
            background: prevDone || isDone ? 'var(--green)' : 'var(--border)',
            transition: 'background 0.5s ease',
            borderRadius: 1,
          }} />
        )}
      </div>
    )
  }

  // Citation chips
  const renderCitations = (chips: string[], key: number) => (
    <div key={key} style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
      {chips.map((c, i) => (
        <span key={i} style={{
          background: 'var(--accent-dim)',
          border: '1px solid rgba(244,116,140,0.15)',
          borderRadius: 100,
          padding: '2px 8px',
          fontSize: 10,
          fontFamily: 'var(--font-mono)',
          color: 'var(--accent)',
        }}>{c}</span>
      ))}
    </div>
  )

  // OTP row
  const renderOtp = (digits: string[], activeIndex: number, key: number) => (
    <div key={key} style={{ display: 'flex', gap: 6, marginTop: 6 }}>
      {[0, 1, 2, 3, 4, 5].map(i => {
        const filled = !!digits[i]
        const isActive = i === activeIndex && !filled
        return (
          <div key={i} style={{
            width: 30,
            height: 34,
            border: isActive ? '1.5px solid var(--accent)' : '1.5px solid var(--border)',
            boxShadow: isActive ? '0 0 0 3px var(--accent-dim)' : 'none',
            borderRadius: 6,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: 'var(--font-mono)',
            fontSize: 15,
            fontWeight: 600,
            color: 'var(--text-1)',
            background: 'var(--surface-1)',
            animation: filled ? 'otp-pop 0.25s ease forwards' : 'none',
            transition: 'border-color 0.2s, box-shadow 0.2s',
          }}>{digits[i] || ''}</div>
        )
      })}
    </div>
  )

  const renderVerified = (key: number) => (
    <div key={key} style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 5,
      background: 'var(--green-bg)',
      border: '1px solid rgba(52,211,153,0.25)',
      color: 'var(--green)',
      borderRadius: 100,
      padding: '4px 12px',
      fontSize: 11,
      fontWeight: 600,
      marginTop: 6,
      animation: 'hero-fade-in 0.3s ease forwards',
      alignSelf: 'flex-start',
      width: 'fit-content',
    }}>
      ✓ Verified
    </div>
  )

  const renderStripe = (s: Extract<ExtraItem, { type: 'stripe' }>, key: number) => {
    const fieldStyle: CSSProperties = {
      flex: 1,
      border: '1.5px solid var(--border)',
      borderRadius: 6,
      padding: '8px 10px',
      fontFamily: 'var(--font-mono)',
      fontSize: 13,
      background: 'var(--surface-2)',
      color: 'var(--text-4)',
      minHeight: 32,
      display: 'flex',
      alignItems: 'center',
    }

    return (
      <div key={key} style={{
        background: 'var(--surface-1)',
        border: '1px solid var(--border)',
        borderRadius: 10,
        padding: 12,
        marginTop: 6,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        animation: 'hero-fade-in 0.3s ease forwards',
      }}>
        <div style={{
          border: s.cardActive ? '1.5px solid var(--accent)' : '1.5px solid var(--border)',
          borderRadius: 6,
          padding: '8px 10px',
          fontFamily: 'var(--font-mono)',
          fontSize: 13,
          background: 'var(--surface-2)',
          color: s.cardNum ? 'var(--text-1)' : 'var(--text-4)',
          boxShadow: s.cardActive ? '0 0 0 3px var(--accent-dim)' : 'none',
          minHeight: 32,
          display: 'flex',
          alignItems: 'center',
          transition: 'border-color 0.2s, box-shadow 0.2s, color 0.2s',
        }}>
          {s.cardNum || '1234 1234 1234 1234'}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ ...fieldStyle, color: s.expiry ? 'var(--text-1)' : 'var(--text-4)' }}>{s.expiry || 'MM / YY'}</div>
          <div style={{ ...fieldStyle, color: s.cvc ? 'var(--text-1)' : 'var(--text-4)' }}>{s.cvc || 'CVC'}</div>
        </div>
        <div style={{
          padding: 9,
          background: s.paid ? 'var(--green)' : 'var(--accent)',
          color: '#fff',
          borderRadius: 8,
          fontSize: 13,
          fontWeight: 600,
          textAlign: 'center',
          transition: 'background 0.3s',
        }}>
          {s.paid ? '✓ Paid $89.97' : 'Pay $89.97'}
        </div>
      </div>
    )
  }

  const renderReceipt = (key: number) => (
    <div key={key} style={{
      background: 'var(--surface-1)',
      border: '1px solid var(--border)',
      borderRadius: 10,
      padding: 12,
      fontFamily: 'var(--font-mono)',
      fontSize: 11,
      color: 'var(--text-2)',
      lineHeight: 1.8,
      marginTop: 6,
      animation: 'hero-fade-in 0.3s ease forwards',
    }}>
      <div style={{
        fontSize: 9,
        letterSpacing: '0.12em',
        textTransform: 'uppercase',
        color: 'var(--text-3)',
        marginBottom: 6,
        fontWeight: 700,
      }}>Order Receipt</div>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>3× Tiramisu</span><span>$89.97</span></div>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Delivery</span><span>Tue 26 May</span></div>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Address</span><span>14 Maple Ave</span></div>
      <hr style={{ border: 'none', borderTop: '1px dashed var(--border)', margin: '6px 0' }} />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontWeight: 700, color: 'var(--text-1)' }}>
        <span>Total $89.97</span>
        <span style={{
          background: 'var(--green-bg)',
          color: 'var(--green)',
          fontSize: 9,
          fontWeight: 700,
          padding: '2px 7px',
          borderRadius: 100,
        }}>PAID ****4242</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
        <span>Order</span>
        <span style={{ color: 'var(--accent)', fontWeight: 600 }}>#VRD-2847</span>
      </div>
    </div>
  )

  const renderExtra = (e: ExtraItem, key: number) => {
    switch (e.type) {
      case 'citations': return renderCitations(e.chips, key)
      case 'otp':       return renderOtp(e.digits, e.activeIndex, key)
      case 'verified':  return renderVerified(key)
      case 'stripe':    return renderStripe(e, key)
      case 'receipt':   return renderReceipt(key)
    }
  }

  // Message bubble
  const renderMessage = (m: MessageItem) => {
    if (m.kind === 'user') {
      return (
        <div key={m.id} style={{
          alignSelf: 'flex-end',
          maxWidth: '78%',
          background: 'var(--accent)',
          color: '#fff',
          padding: '9px 13px',
          borderRadius: '14px 14px 4px 14px',
          fontSize: 13,
          lineHeight: 1.45,
          boxShadow: 'var(--shadow-card)',
          animation: 'hero-fade-in 0.3s ease forwards',
          wordBreak: 'break-word',
        }}>
          {m.text}
        </div>
      )
    }
    return (
      <div key={m.id} style={{
        alignSelf: 'flex-start',
        maxWidth: '85%',
        background: 'var(--surface-1)',
        color: 'var(--text-1)',
        padding: '10px 13px',
        borderRadius: '14px 14px 14px 4px',
        fontSize: 13,
        lineHeight: 1.5,
        border: '1px solid var(--border-soft)',
        boxShadow: 'var(--shadow-card)',
        animation: 'hero-fade-in 0.3s ease forwards',
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
      }}>
        <div dangerouslySetInnerHTML={{ __html: m.html }} />
        {m.extras?.map((e, i) => renderExtra(e, i))}
      </div>
    )
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div style={{ position: 'relative', minHeight: 340 }}>
      {/* PHASE 1 — Step cards */}
      <div style={{
        position: phase === 'steps' ? 'relative' : 'absolute',
        inset: phase === 'steps' ? undefined : 0,
        opacity: phase === 'steps' ? 1 : 0,
        pointerEvents: phase === 'steps' ? 'auto' : 'none',
        transition: 'opacity 0.6s ease',
        display: 'flex',
        flexDirection: 'column',
      }}>
        {STEP_LABELS.map((_, i) => renderStepCard(i))}
      </div>

      {/* PHASE 2 — Widget */}
      <div style={{
        position: phase === 'widget' ? 'relative' : 'absolute',
        inset: phase === 'widget' ? undefined : 0,
        opacity: phase === 'widget' ? 1 : 0,
        pointerEvents: phase === 'widget' ? 'auto' : 'none',
        transition: 'opacity 0.6s ease',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <div style={{
          width: '100%',
          maxWidth: 420,
          margin: '0 auto',
          background: 'var(--surface-1)',
          borderRadius: 'var(--radius-sm)',
          boxShadow: 'var(--shadow-lift)',
          border: '1px solid var(--border-soft)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}>
          {/* Header */}
          <div style={{
            height: 40,
            background: 'var(--accent)',
            display: 'flex',
            alignItems: 'center',
            padding: '0 14px',
            gap: 8,
            borderRadius: 'var(--radius-sm) var(--radius-sm) 0 0',
          }}>
            <span style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: 'var(--green)',
              boxShadow: '0 0 6px rgba(52,211,153,0.6)',
              animation: 'blink-dot 1.4s ease-in-out infinite',
              flexShrink: 0,
            }} />
            <span style={{ fontSize: 13, fontWeight: 600, color: '#fff' }}>Bakery Assistant</span>
            <span style={{
              marginLeft: 'auto',
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: '0.08em',
              color: '#fff',
              background: 'rgba(255,255,255,0.18)',
              padding: '2px 7px',
              borderRadius: 100,
            }}>LIVE</span>
          </div>

          {/* Messages */}
          <div
            ref={messagesRef}
            style={{
              minHeight: 260,
              maxHeight: 280,
              overflowY: 'auto',
              padding: 14,
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
              background: 'var(--surface-2)',
            }}
          >
            {messages.map(renderMessage)}

            {showSources && (
              <div style={{ display: 'flex', gap: 6, alignSelf: 'flex-start' }}>
                {['Delivery Policy.pdf', 'Opening Hours.pdf'].map((s, i) => (
                  <span key={i} style={{
                    padding: '4px 10px',
                    borderRadius: 'var(--radius-xs)',
                    background: 'linear-gradient(90deg, var(--surface-3) 25%, var(--surface-1) 50%, var(--surface-3) 75%)',
                    backgroundSize: '200% 100%',
                    animation: 'shimmer 1.2s linear infinite',
                    fontSize: 11,
                    color: 'var(--text-3)',
                    border: '1px solid var(--border-soft)',
                  }}>📄 {s}</span>
                ))}
              </div>
            )}

            {showTyping && (
              <div style={{
                alignSelf: 'flex-start',
                maxWidth: 70,
                display: 'flex',
                gap: 4,
                padding: '12px 14px',
                background: 'var(--surface-1)',
                border: '1px solid var(--border-soft)',
                borderRadius: '14px 14px 14px 4px',
                boxShadow: 'var(--shadow-card)',
                animation: 'hero-fade-in 0.2s ease forwards',
              }}>
                {[0, 1, 2].map(i => (
                  <span key={i} style={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    background: 'var(--accent)',
                    animation: `bounce-dot 1.2s ease-in-out ${i * 0.2}s infinite`,
                  }} />
                ))}
              </div>
            )}
          </div>

          {/* Input bar */}
          <div style={{
            height: 44,
            borderTop: '1px solid var(--border)',
            background: 'var(--surface-1)',
            display: 'flex',
            alignItems: 'center',
            padding: '0 10px',
            gap: 8,
          }}>
            <div style={{
              flex: 1,
              background: 'var(--surface-2)',
              border: '1px solid var(--border-soft)',
              borderRadius: 'var(--radius-sm)',
              padding: '7px 10px',
              fontSize: 12,
              minHeight: 30,
              display: 'flex',
              alignItems: 'center',
              color: inputText ? 'var(--text-1)' : 'var(--text-3)',
              fontFamily: 'var(--font-sans)',
              overflow: 'hidden',
              whiteSpace: 'nowrap',
              textOverflow: 'ellipsis',
            }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {inputText || 'Ask me anything…'}
              </span>
              {showCaret && (
                <span style={{
                  display: 'inline-block',
                  width: 1,
                  height: 14,
                  background: 'var(--text-1)',
                  marginLeft: 2,
                  animation: 'blink-caret 0.9s steps(1) infinite',
                }} />
              )}
            </div>
            <button style={{
              width: 30,
              height: 30,
              background: 'var(--accent)',
              color: '#fff',
              border: 'none',
              borderRadius: 6,
              fontSize: 14,
              fontWeight: 700,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}>→</button>
          </div>
        </div>
      </div>
    </div>
  )
}
