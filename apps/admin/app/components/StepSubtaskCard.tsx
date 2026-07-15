'use client'
import Link from 'next/link'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StepSubtaskCardProps {
  icon: React.ReactNode
  title: string
  description: string
  href?: string
  ctaLabel: string
  state?: 'idle' | 'active' | 'completed'
  onCtaClick?: () => void
}

// ---------------------------------------------------------------------------
// StepSubtaskCard
// ---------------------------------------------------------------------------

export default function StepSubtaskCard({
  icon,
  title,
  description,
  href,
  ctaLabel,
  state = 'idle',
  onCtaClick,
}: StepSubtaskCardProps) {
  const ctaPrimary = state === 'active'

  const ctaBaseStyle: React.CSSProperties = {
    padding: '8px 16px',
    borderRadius: 'var(--radius-xs)',
    fontSize: '13px',
    fontWeight: 600,
    textDecoration: 'none',
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    display: 'inline-block',
    fontFamily: 'var(--font-sans)',
    ...(ctaPrimary
      ? { background: 'var(--accent)', color: 'var(--text-on-accent)', border: 'none' }
      : { background: 'transparent', color: 'var(--text-3)', border: '1px solid var(--border)' }),
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '16px',
        padding: '16px 20px',
        background: 'var(--chip)',
        border: '1px solid var(--border-soft)',
        borderRadius: 'var(--radius-sm)',
        borderLeft:
          state === 'completed'
            ? '3px solid var(--green)'
            : state === 'active'
            ? '3px solid var(--accent)'
            : '1px solid var(--border-soft)',
      }}
    >
      {/* Icon block — holds a lucide icon (16px) supplied by the caller */}
      <div
        style={{
          width: '40px',
          height: '40px',
          background: 'var(--accent-dim)',
          borderRadius: 'var(--radius-xs)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--accent)',
          flexShrink: 0,
        }}
      >
        {icon}
      </div>

      {/* Text block */}
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-1)' }}>
          {title}
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-3)', marginTop: '2px' }}>
          {description}
        </div>
      </div>

      {/* CTA */}
      {href ? (
        <Link href={href} style={ctaBaseStyle}>
          {ctaLabel}
        </Link>
      ) : onCtaClick ? (
        <button
          onClick={onCtaClick}
          style={{
            ...ctaBaseStyle,
            border: ctaPrimary ? 'none' : `1px solid var(--border)`,
          }}
        >
          {ctaLabel}
        </button>
      ) : null}
    </div>
  )
}
