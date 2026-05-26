'use client'
import { use } from 'react'
import Link from 'next/link'

export default function SettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)

  return (
    <div
      style={{
        padding: '32px 40px',
        maxWidth: '720px',
        fontFamily: 'var(--font-sans)',
        background: 'transparent',
      }}
    >
      <Link
        href={`/agents/${id}`}
        style={{
          fontSize: '14px',
          color: 'var(--accent)',
          textDecoration: 'none',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px',
          marginBottom: '24px',
        }}
      >
        ← Back to Configure
      </Link>

      <h1
        style={{
          fontSize: '22px',
          fontWeight: 700,
          color: 'var(--text-1)',
          marginBottom: '8px',
        }}
      >
        Settings
      </h1>

      <p
        style={{
          color: 'var(--text-3)',
          fontSize: '14px',
          marginBottom: '24px',
        }}
      >
        Agent settings coming soon.
      </p>

      {/* Dark form panel — same pattern as soul editor */}
      <div
        style={{
          background: 'var(--surface-1)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          padding: '24px',
        }}
      >
        {/* Placeholder label */}
        <p
          style={{
            fontSize: '10.5px',
            fontWeight: 600,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: 'var(--text-3)',
            marginBottom: '16px',
          }}
        >
          General
        </p>

        {/* Placeholder input field */}
        <div style={{ marginBottom: '20px' }}>
          <label
            style={{
              display: 'block',
              fontSize: '10.5px',
              fontWeight: 600,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'var(--text-3)',
              marginBottom: '6px',
            }}
          >
            Agent name
          </label>
          <input
            type="text"
            disabled
            placeholder="Coming soon"
            style={{
              width: '100%',
              background: 'var(--surface-2)',
              border: '1px solid var(--border-soft)',
              borderRadius: 'var(--radius-xs)',
              padding: '10px 14px',
              fontSize: '14px',
              color: 'var(--text-1)',
              fontFamily: 'var(--font-sans)',
              boxSizing: 'border-box',
              cursor: 'not-allowed',
              opacity: 0.6,
            }}
          />
        </div>

        {/* Save button — coral primary, sentence case */}
        <button
          disabled
          style={{
            background: 'var(--accent)',
            color: '#0B0717',
            border: 'none',
            borderRadius: 'var(--radius-xs)',
            padding: '10px 18px',
            fontSize: '14px',
            fontWeight: 600,
            fontFamily: 'var(--font-sans)',
            cursor: 'not-allowed',
            opacity: 0.6,
          }}
        >
          Save settings
        </button>
      </div>
    </div>
  )
}
