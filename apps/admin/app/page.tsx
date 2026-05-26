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
          backdropFilter: scrolled ? 'none' : 'var(--glass-blur)',
          WebkitBackdropFilter: scrolled ? 'none' : 'var(--glass-blur)',
          transition: 'background 0.3s, backdrop-filter 0.3s',
          borderBottom: scrolled ? '1px solid var(--border-soft)' : '1px solid var(--glass-border)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/wordmark.svg" alt="w.chats" style={{ height: '24px' }} />
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
          padding: '80px 56px 120px',
          minHeight: '720px',
        }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 0.9fr)',
            gap: '80px',
            alignItems: 'center',
            maxWidth: '1180px',
            margin: '0 auto',
          }}
        >
          {/* Left column — copy */}
          <div>
            {/* Eyebrow pill */}
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                background: 'var(--glass-bg)',
                backdropFilter: 'var(--glass-blur)',
                WebkitBackdropFilter: 'var(--glass-blur)',
                border: '1px solid var(--glass-border)',
                borderRadius: 'var(--radius-pill)',
                padding: '4px 14px',
                marginBottom: '28px',
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
                }}
              >
                OPEN SOURCE · v0.4.2 · M8
              </span>
            </div>

            {/* Fraunces headline with strikethrough + italic-coral */}
            <h1
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 400,
                fontVariationSettings: '"opsz" 144, "SOFT" 30',
                fontSize: 'clamp(48px, 6.4vw, 86px)',
                letterSpacing: '-0.035em',
                lineHeight: 0.98,
                color: 'var(--text-1)',
                marginBottom: '28px',
                marginTop: 0,
              }}
            >
              The chat widget{' '}
              <span
                style={{
                  textDecoration: 'line-through',
                  textDecorationColor: 'var(--accent)',
                  textDecorationThickness: '3px',
                  color: 'var(--text-3)',
                }}
              >
                is the hard part.
              </span>
              {' '}
              <em
                style={{
                  fontStyle: 'italic',
                  fontWeight: 300,
                  color: 'var(--accent)',
                  fontVariationSettings: '"opsz" 144, "SOFT" 100',
                }}
              >
                The layer underneath is.
              </em>
            </h1>

            <p
              style={{
                fontSize: '19px',
                lineHeight: 1.55,
                color: 'var(--text-2)',
                marginBottom: '28px',
                maxWidth: '560px',
              }}
            >
              W Chats does the serious work — structure-aware ingestion, hybrid retrieval, continuous evaluation, weekly red teaming — so small business owners ship a customer service agent that is actually safe to deploy.
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
                Start free →
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
                gap: '32px',
                flexWrap: 'wrap',
                borderTop: '1px solid var(--glass-border)',
                paddingTop: '28px',
              }}
            >
              {[
                { value: '<30 min', label: 'Signup to deployed' },
                { value: '>0.85', label: 'Faithfulness target' },
                { value: '0 critical', label: 'Red team threshold' },
              ].map(({ value, label }) => (
                <div key={label} style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: '30px',
                      fontWeight: 600,
                      color: 'var(--text-1)',
                      letterSpacing: '-0.02em',
                    }}
                  >
                    {value}
                  </span>
                  <span
                    style={{
                      fontSize: '10.5px',
                      fontWeight: 600,
                      letterSpacing: '0.1em',
                      textTransform: 'uppercase',
                      color: 'var(--text-3)',
                    }}
                  >
                    {label}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Right column — static BUILD PIPELINE card */}
          <HeroPipeline />
        </div>
      </section>
    </main>
  )
}
