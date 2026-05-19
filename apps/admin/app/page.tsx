import Link from 'next/link'
import { HeroSteps } from './components/HeroSteps'

const primaryButtonStyle: React.CSSProperties = {
  padding: '14px 28px',
  minHeight: '44px',
  borderRadius: 'var(--radius-sm)',
  fontWeight: 600,
  fontSize: '15px',
  textDecoration: 'none',
  display: 'inline-block',
  background: 'var(--accent)',
  color: '#fff',
  border: 'none',
}

const outlineButtonStyle: React.CSSProperties = {
  padding: '14px 28px',
  minHeight: '44px',
  borderRadius: 'var(--radius-sm)',
  fontWeight: 600,
  fontSize: '15px',
  textDecoration: 'none',
  display: 'inline-block',
  background: 'transparent',
  color: 'var(--accent)',
  border: '1px solid var(--accent)',
}

export default function LandingPage() {
  return (
    <main
      style={{
        minHeight: '100vh',
        background: 'var(--bg)',
        fontFamily: 'var(--font-sans)',
      }}
    >
      {/* ── Nav ─────────────────────────────────────────────────────────── */}
      <header
        style={{
          height: '56px',
          borderBottom: '1px solid var(--border-soft)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 32px',
          background: 'var(--bg)',
          position: 'sticky',
          top: 0,
          zIndex: 100,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/w-chats-lettermann.png"
            alt="Chats logo"
            style={{ width: '30px', height: '30px', objectFit: 'contain' }}
          />
          <span style={{ fontWeight: 700, fontSize: '19px', color: 'var(--text-1)' }}>
            Chats
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Link
            href="/sign-in"
            style={{ fontSize: '14px', fontWeight: 500, color: 'var(--text-2)', textDecoration: 'none' }}
          >
            Sign in
          </Link>
          <Link href="/sign-up" style={{ ...primaryButtonStyle, padding: '8px 20px', minHeight: 'auto', fontSize: '14px' }}>
            Get started
          </Link>
        </div>
      </header>

      {/* ── Hero ────────────────────────────────────────────────────────── */}
      <section style={{ padding: '80px 32px' }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '64px',
            alignItems: 'center',
            maxWidth: '1180px',
            margin: '0 auto',
          }}
        >
          {/* Left column */}
          <div>
            <h1
              style={{
                fontSize: '26px',
                fontWeight: 800,
                lineHeight: '1.2',
                letterSpacing: '-0.02em',
                color: 'var(--text-1)',
                marginBottom: '20px',
                marginTop: 0,
              }}
            >
              Ship a customer support agent that is{' '}
              <span style={{ color: 'var(--accent)' }}>defensible</span>
              {' '}— grounded, evaluated, and red-teamed before it goes live.
            </h1>

            <p
              style={{
                fontSize: '17px',
                color: 'var(--text-2)',
                lineHeight: '1.6',
                maxWidth: '480px',
                marginBottom: '28px',
                marginTop: 0,
              }}
            >
              Veridian wires a Claude Agent SDK reasoning engine to your business
              documents, evaluates every answer, and ships a 20kb widget for any page.
            </p>

            <div style={{ display: 'flex', gap: '12px' }}>
              <Link href="/sign-up" style={primaryButtonStyle}>
                Start for free →
              </Link>
              <Link href="/sign-in" style={outlineButtonStyle}>
                Sign in to dashboard
              </Link>
            </div>
          </div>

          {/* Right column — animated step preview */}
          <HeroSteps />
        </div>
      </section>
    </main>
  )
}
