'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { HeroPipeline } from './components/HeroPipeline'

export default function LandingPage() {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 60)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <main
      style={{
        background: 'transparent',
        fontFamily: 'var(--font-sans)',
        minHeight: '100vh',
      }}
    >
      {/* ── Landing nav — glass → solid on scroll ──────────────────────── */}
      <header
        style={{
          height: '56px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 32px',
          position: 'sticky',
          top: 0,
          zIndex: 100,
          background: scrolled ? 'var(--bg-elev)' : 'transparent',
          backdropFilter: scrolled ? 'blur(20px) saturate(140%)' : 'none',
          WebkitBackdropFilter: scrolled ? 'blur(20px) saturate(140%)' : 'none',
          transition: 'background 0.3s, backdrop-filter 0.3s',
          borderBottom: scrolled ? '1px solid var(--border-soft)' : '1px solid var(--glass-border)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginRight: 0 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/wordmark.svg" alt="w.chats" style={{ height: '24px' }} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '2px', flex: 1, justifyContent: 'center' }}>
          {['Product', 'How it works', 'Pricing', 'Docs', 'Changelog'].map(label => (
            <span
              key={label}
              style={{
                padding: '7px 14px',
                fontSize: '13.5px',
                fontWeight: 500,
                color: 'var(--text-2)',
                borderRadius: 'var(--radius-xs)',
                cursor: 'pointer',
              }}
            >
              {label}
            </span>
          ))}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Link
            href="/sign-in"
            style={{ fontSize: '14px', fontWeight: 500, color: 'var(--text-2)', textDecoration: 'none' }}
          >
            Sign in
          </Link>
          <Link
            href="/sign-up"
            style={{
              background: 'var(--accent)',
              color: '#0B0717',
              padding: '8px 18px',
              borderRadius: 'var(--radius-sm)',
              fontWeight: 600,
              fontSize: '14px',
              textDecoration: 'none',
              display: 'inline-block',
            }}
          >
            Start free →
          </Link>
        </div>
      </header>

      {/* ── Hero — transparent, city shows through ─────────────────────── */}
      <section
        style={{
          background: 'transparent',
          padding: '120px 56px 64px',
        }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 0.72fr',
            gap: '56px',
            alignItems: 'center',
            maxWidth: '1280px',
            margin: '0 auto',
          }}
        >
          {/* Left column — copy */}
          <div>
            {/* Fraunces headline */}
            <h1
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 400,
                fontVariationSettings: '"opsz" 144, "SOFT" 30',
                fontSize: 'clamp(32px, 3.4vw, 52px)',
                letterSpacing: '-0.035em',
                lineHeight: 0.98,
                color: 'var(--text-1)',
                marginBottom: '28px',
                marginTop: 0,
              }}
            >
              Ship a customer support agent that is{' '}
              <em
                style={{
                  fontStyle: 'italic',
                  fontWeight: 300,
                  color: 'var(--accent)',
                  fontVariationSettings: '"opsz" 144, "SOFT" 100',
                  textDecoration: 'line-through',
                }}
              >
                defensible
              </em>
              {' '}— grounded, evaluated, and red-teamed before it goes live.
            </h1>

            <p
              style={{
                fontSize: '15px',
                lineHeight: 1.6,
                color: 'var(--text-2)',
                marginBottom: '22px',
                maxWidth: '480px',
              }}
            >
              W Chats wires a <strong style={{ color: 'var(--text-1)', fontWeight: 600 }}>Claude Agent SDK</strong> reasoning engine to your business documents, evaluates every answer, and ships a <strong style={{ color: 'var(--text-1)', fontWeight: 600 }}>20kb widget</strong> for any page.
            </p>

            {/* CTA row */}
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '40px' }}>
              <Link
                href="/sign-up"
                style={{
                  background: 'var(--accent)',
                  color: '#0B0717',
                  padding: '14px 28px',
                  borderRadius: 'var(--radius-sm)',
                  fontWeight: 600,
                  fontSize: '15px',
                  textDecoration: 'none',
                  display: 'inline-block',
                }}
              >
                Build your agent
              </Link>
              <button
                style={{
                  background: 'transparent',
                  border: '1px solid var(--border)',
                  color: 'var(--text-2)',
                  padding: '14px 28px',
                  borderRadius: 'var(--radius-sm)',
                  fontWeight: 600,
                  fontSize: '15px',
                  cursor: 'pointer',
                }}
              >
                ▶ Watch the build&nbsp;&nbsp;2:18
              </button>
            </div>

            {/* Trust strip */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                borderTop: '1px solid var(--glass-border)',
                paddingTop: '24px',
              }}
            >
              {[
                { value: '>248+', label: 'Agents deployed' },
                { value: '>0.91', label: 'Faithfulness median' },
                { value: '>$0.17', label: 'Avg cost / session' },
                { value: '0', label: 'Critical red team findings' },
              ].flatMap(({ value, label }, i) => [
                ...(i > 0 ? [
                  <div key={`sep-${i}`} style={{ width: '1px', height: '32px', background: 'var(--glass-border)', flexShrink: 0 }} />
                ] : []),
                <div key={label} style={{ display: 'flex', flexDirection: 'column', gap: '2px', padding: i === 0 ? '0 28px 0 0' : '0 28px' }}>
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: '18px',
                      fontWeight: 600,
                      color: 'var(--text-1)',
                      letterSpacing: '-0.01em',
                    }}
                  >
                    {value}
                  </span>
                  <span
                    style={{
                      fontSize: '9.5px',
                      fontWeight: 600,
                      letterSpacing: '0.1em',
                      textTransform: 'uppercase',
                      color: 'var(--text-3)',
                    }}
                  >
                    {label}
                  </span>
                </div>,
              ])}
            </div>
          </div>

          {/* Right column — static BUILD PIPELINE card */}
          <HeroPipeline />
        </div>
      </section>
    </main>
  )
}
