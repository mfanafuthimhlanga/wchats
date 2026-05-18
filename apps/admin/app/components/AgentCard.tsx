'use client'
import Link from 'next/link'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AgentCardProps {
  id: string
  name: string
  role: string
  status: string
  created_at: string
}

// ---------------------------------------------------------------------------
// Status color map — uses design-g tokens from globals.css
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, { bg: string; fg: string; label: string }> = {
  ready: { bg: 'var(--green-bg)', fg: 'var(--green)', label: 'Ready' },
  pending: { bg: 'var(--amber-bg)', fg: 'var(--amber)', label: 'Provisioning' },
  provisioning: { bg: 'var(--amber-bg)', fg: 'var(--amber)', label: 'Provisioning' },
  error: { bg: 'var(--red-bg)', fg: 'var(--red)', label: 'Error' },
}

function getStatusColor(status: string) {
  return (
    STATUS_COLORS[status] ?? {
      bg: 'var(--surface-3)',
      fg: 'var(--text-3)',
      label: status,
    }
  )
}

// ---------------------------------------------------------------------------
// AgentCard
// ---------------------------------------------------------------------------

export default function AgentCard({ id, name, role, status, created_at }: AgentCardProps) {
  const c = getStatusColor(status)
  const formattedDate = new Date(created_at).toLocaleDateString()

  return (
    <Link
      href={`/agents/${id}`}
      style={{
        display: 'block',
        textDecoration: 'none',
        padding: '20px',
        background: 'var(--bg)',
        border: '1px solid var(--border-soft)',
        borderRadius: 'var(--radius-xs)',
        boxShadow: 'var(--shadow-card)',
        color: 'var(--text-1)',
      }}
    >
      {/* Top row: name + status chip */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: '6px',
        }}
      >
        <h3
          style={{
            fontSize: '16px',
            fontWeight: 700,
            color: 'var(--text-1)',
            margin: 0,
          }}
        >
          {name}
        </h3>
        <span
          style={{
            padding: '4px 10px',
            borderRadius: '999px',
            fontSize: '11px',
            fontWeight: 600,
            background: c.bg,
            color: c.fg,
            whiteSpace: 'nowrap',
          }}
        >
          {c.label}
        </span>
      </div>

      {/* Subtitle: role */}
      <p
        style={{
          fontSize: '13px',
          color: 'var(--text-3)',
          margin: '0 0 12px 0',
        }}
      >
        {role}
      </p>

      {/* Footer: created date */}
      <p
        style={{
          fontSize: '12px',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text-4)',
          margin: 0,
        }}
      >
        Created {formattedDate}
      </p>
    </Link>
  )
}
