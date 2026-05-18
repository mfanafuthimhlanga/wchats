import Link from 'next/link'

// ---------------------------------------------------------------------------
// Button styles (module-scope constants to keep JSX compact)
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// How it works data
// ---------------------------------------------------------------------------

const HOW_IT_WORKS = [
  {
    num: 1,
    title: 'Provision',
    desc: "We spin up a dedicated Neon database for your tenant — your data never mixes with anyone else's.",
  },
  {
    num: 2,
    title: 'Configure',
    desc: "Upload PDFs or URLs. Define the agent's voice and what it must/must not do.",
  },
  {
    num: 3,
    title: 'Test',
    desc: 'Ragas evaluations and adversarial probes run before deploy — you see the failures, not the customer.',
  },
  {
    num: 4,
    title: 'Deploy',
    desc: 'Copy a 20kb embed snippet. Customise colors and typography from the admin panel.',
  },
]

// ---------------------------------------------------------------------------
// LandingPage — server component, no auth, no client hooks
// ---------------------------------------------------------------------------

export default function LandingPage() {
  return (
    <main
      style={{
        minHeight: '100vh',
        background: 'var(--bg)',
        fontFamily: 'var(--font-sans)',
      }}
    >
      {/* ── Public mini-nav ─────────────────────────────────────────────── */}
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
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div
            style={{
              width: '30px',
              height: '30px',
              background: 'var(--accent)',
              borderRadius: '7px',
            }}
          />
          <span style={{ fontWeight: 700, fontSize: '16px', color: 'var(--text-1)' }}>
            Veridian
          </span>
        </div>

        {/* Right links */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Link
            href="/sign-in"
            style={{
              fontSize: '14px',
              fontWeight: 500,
              color: 'var(--text-2)',
              textDecoration: 'none',
            }}
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
          {/* Left column — copy */}
          <div>
            {/* Tag pill */}
            <span
              style={{
                display: 'inline-block',
                padding: '6px 12px',
                background: 'var(--accent-dim)',
                border: '1px solid var(--accent)',
                color: 'var(--accent)',
                borderRadius: 'var(--radius-xs)',
                fontSize: '12px',
                fontWeight: 600,
                marginBottom: '20px',
              }}
            >
              For non-technical founders &amp; teams
            </span>

            {/* Headline */}
            <h1
              style={{
                fontSize: '52px',
                fontWeight: 800,
                lineHeight: '1.1',
                letterSpacing: '-0.03em',
                color: 'var(--text-1)',
                marginBottom: '20px',
                marginTop: 0,
              }}
            >
              Ship a customer support agent that is{' '}
              <span style={{ color: 'var(--accent)' }}>defensible</span>
              {' '}— grounded, evaluated, and red-teamed before it goes live.
            </h1>

            {/* Subtext */}
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

            {/* CTA row */}
            <div style={{ display: 'flex', gap: '12px', marginBottom: '16px' }}>
              <Link href="/sign-up" style={primaryButtonStyle}>
                Start for free →
              </Link>
              <Link href="/sign-in" style={outlineButtonStyle}>
                Sign in to dashboard
              </Link>
            </div>

            {/* Proof line */}
            <p
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
                color: 'var(--text-4)',
                margin: 0,
              }}
            >
              No credit card. Provisioning takes ~30 seconds.
            </p>
          </div>

          {/* Right column — Step pill preview */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {/* Step 1 — done */}
            <div
              style={{
                padding: '16px 18px',
                background: 'var(--bg)',
                border: '1px solid var(--border-soft)',
                borderRadius: 'var(--radius-xs)',
                boxShadow: 'var(--shadow-card)',
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
              }}
            >
              <div
                style={{
                  width: '24px',
                  height: '24px',
                  borderRadius: '50%',
                  background: 'var(--accent)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#fff',
                  fontSize: '12px',
                  fontWeight: 700,
                  flexShrink: 0,
                }}
              >
                ✓
              </div>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-1)' }}>
                  1 · Provision
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-3)', marginTop: '2px' }}>
                  Dedicated tenant database ready
                </div>
              </div>
            </div>

            {/* Step 2 — done */}
            <div
              style={{
                padding: '16px 18px',
                background: 'var(--bg)',
                border: '1px solid var(--border-soft)',
                borderRadius: 'var(--radius-xs)',
                boxShadow: 'var(--shadow-card)',
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
              }}
            >
              <div
                style={{
                  width: '24px',
                  height: '24px',
                  borderRadius: '50%',
                  background: 'var(--accent)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#fff',
                  fontSize: '12px',
                  fontWeight: 700,
                  flexShrink: 0,
                }}
              >
                ✓
              </div>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-1)' }}>
                  2 · Configure
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-3)', marginTop: '2px' }}>
                  Soul + documents ingested
                </div>
              </div>
            </div>

            {/* Step 3 — active */}
            <div
              style={{
                padding: '16px 18px',
                background: 'var(--bg)',
                border: '1px solid var(--accent)',
                borderRadius: 'var(--radius-xs)',
                boxShadow: 'var(--shadow-card)',
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
              }}
            >
              <div
                style={{
                  width: '24px',
                  height: '24px',
                  borderRadius: '50%',
                  background: 'transparent',
                  border: '2px solid var(--accent)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'var(--accent)',
                  fontSize: '11px',
                  fontWeight: 700,
                  flexShrink: 0,
                }}
              >
                3
              </div>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-1)' }}>
                  3 · Test
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-3)', marginTop: '2px' }}>
                  Evals running
                </div>
              </div>
            </div>

            {/* Step 4 — locked */}
            <div
              style={{
                padding: '16px 18px',
                background: 'var(--bg)',
                border: '1px solid var(--border-soft)',
                borderRadius: 'var(--radius-xs)',
                boxShadow: 'var(--shadow-card)',
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
                opacity: 0.6,
              }}
            >
              <div
                style={{
                  width: '24px',
                  height: '24px',
                  borderRadius: '50%',
                  background: 'var(--surface-3)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'var(--text-4)',
                  fontSize: '11px',
                  fontWeight: 700,
                  flexShrink: 0,
                }}
              >
                4
              </div>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-1)' }}>
                  4 · Deploy
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-3)', marginTop: '2px' }}>
                  Embed widget
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── How it works ────────────────────────────────────────────────── */}
      <section
        style={{
          padding: '64px 32px',
          borderTop: '1px solid var(--border-soft)',
          background: 'var(--bg)',
        }}
      >
        <div style={{ maxWidth: '1180px', margin: '0 auto' }}>
          {/* Eyebrow */}
          <p
            style={{
              fontSize: '11px',
              textTransform: 'uppercase',
              letterSpacing: '0.12em',
              color: 'var(--text-3)',
              marginBottom: '32px',
              marginTop: 0,
            }}
          >
            HOW IT WORKS
          </p>

          {/* 4-card grid */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(4, 1fr)',
              gap: '24px',
            }}
          >
            {HOW_IT_WORKS.map(({ num, title, desc }) => (
              <div
                key={num}
                style={{
                  padding: '24px',
                  background: 'var(--bg)',
                  border: '1px solid var(--border-soft)',
                  borderRadius: 'var(--radius-xs)',
                }}
              >
                {/* Numbered wine circle */}
                <div
                  style={{
                    width: '32px',
                    height: '32px',
                    background: 'var(--accent)',
                    color: '#fff',
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontWeight: 600,
                    fontSize: '14px',
                    marginBottom: '14px',
                  }}
                >
                  {num}
                </div>
                <h2
                  style={{
                    fontSize: '15px',
                    fontWeight: 700,
                    color: 'var(--text-1)',
                    margin: '0 0 8px 0',
                  }}
                >
                  {title}
                </h2>
                <p
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-3)',
                    lineHeight: '1.6',
                    margin: 0,
                  }}
                >
                  {desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </main>
  )
}
