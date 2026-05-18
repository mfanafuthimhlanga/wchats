'use client'
import { use } from 'react'
import Link from 'next/link'

export default function EvalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)

  return (
    <div
      style={{
        padding: '32px 40px',
        maxWidth: '720px',
        fontFamily: 'var(--font-sans)',
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
        Run evaluations
      </h1>

      <p
        style={{
          color: 'var(--text-3)',
          fontSize: '14px',
          marginBottom: '24px',
        }}
      >
        Test your agent with Ragas metrics and adversarial probes.
      </p>

      <div
        style={{
          padding: '40px',
          background: 'var(--surface-2)',
          border: '1px dashed var(--border)',
          borderRadius: 'var(--radius-xs)',
          textAlign: 'center',
          color: 'var(--text-3)',
        }}
      >
        Coming soon — Ragas metric dashboard. Eval harness ships in M6.
      </div>
    </div>
  )
}
