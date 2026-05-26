'use client'
import { useState } from 'react'
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
  /**
   * Deletes the agent. Resolves on success (the parent removes it from the
   * list); rejects with an Error whose message is shown inline by this card.
   */
  onDelete?: (id: string) => Promise<void>
}

// ---------------------------------------------------------------------------
// Status color map — uses design-g tokens from globals.css
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, { bg: string; fg: string; label: string }> = {
  ready: { bg: 'var(--green-bg)', fg: 'var(--green)', label: 'Ready' },
  pending: { bg: 'var(--gold-bg)', fg: 'var(--gold)', label: 'Provisioning' },
  provisioning: { bg: 'var(--gold-bg)', fg: 'var(--gold)', label: 'Provisioning' },
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
// Inline button styles — match radius/font of existing buttons (radius-xs,
// 13px, weight 600). The destructive variant uses the --red / --red-bg tokens.
// ---------------------------------------------------------------------------

const baseActionButton: React.CSSProperties = {
  appearance: 'none',
  border: '1px solid transparent',
  borderRadius: 'var(--radius-xs)',
  fontFamily: 'var(--font-sans)',
  fontSize: '13px',
  fontWeight: 600,
  padding: '6px 12px',
  cursor: 'pointer',
  lineHeight: 1.2,
}

const deleteTriggerButton: React.CSSProperties = {
  ...baseActionButton,
  background: 'transparent',
  color: 'var(--red)',
  borderColor: 'transparent',
  padding: '6px 0',
}

const confirmDeleteButton: React.CSSProperties = {
  ...baseActionButton,
  background: 'var(--red)',
  color: '#fff',
  borderColor: 'var(--red)',
}

const cancelButton: React.CSSProperties = {
  ...baseActionButton,
  background: 'var(--red-bg)',
  color: 'var(--red)',
  borderColor: 'transparent',
}

// ---------------------------------------------------------------------------
// AgentCard
// ---------------------------------------------------------------------------

export default function AgentCard({ id, name, role, status, created_at, onDelete }: AgentCardProps) {
  const c = getStatusColor(status)
  const formattedDate = new Date(created_at).toLocaleDateString()

  const [hovered, setHovered] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const runDelete = async () => {
    if (!onDelete) return
    setDeleteError(null)
    setDeleting(true)
    try {
      await onDelete(id)
      // On success the parent removes this card from the list, so no further
      // state reset is needed here.
    } catch (err) {
      console.error(err)
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete agent.')
      setDeleting(false)
      setConfirming(false)
    }
  }

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--surface-1)',
        border: `1px solid ${hovered ? 'var(--border)' : 'var(--border-soft)'}`,
        borderTop: `1px solid ${hovered ? 'var(--border-hard)' : 'var(--border-soft)'}`,
        borderRadius: 'var(--radius-md)',
        boxShadow: 'var(--shadow-card)',
        color: 'var(--text-1)',
        overflow: 'hidden',
        transform: hovered ? 'translateY(-2px)' : 'translateY(0)',
        transition: 'transform 0.2s, box-shadow 0.2s, border-color 0.2s',
        cursor: 'pointer',
      }}
    >
      {/* Navigable area — keep the delete controls OUTSIDE this anchor so we
          never nest interactive elements inside a link (invalid HTML + would
          otherwise trigger navigation when clicking Delete). */}
      <Link
        href={`/agents/${id}`}
        style={{
          display: 'block',
          textDecoration: 'none',
          padding: '20px 20px 12px 20px',
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
              fontFamily: 'var(--font-display)',
              fontSize: '17px',
              fontWeight: 600,
              fontVariationSettings: '"opsz" 144, "SOFT" 30',
              color: 'var(--text-1)',
              margin: 0,
            }}
          >
            {name}
          </h3>
          <span
            style={{
              padding: '3px 10px',
              borderRadius: 'var(--radius-pill)',
              fontSize: '10.5px',
              fontWeight: 600,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              background: c.bg,
              color: c.fg,
              whiteSpace: 'nowrap',
            }}
          >
            {c.label}
          </span>
        </div>

        {/* Subtitle: role — UPPERCASE TRACKED micro-label */}
        <p
          style={{
            fontSize: '10.5px',
            fontWeight: 600,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: 'var(--text-3)',
            margin: '0 0 12px 0',
          }}
        >
          {role}
        </p>

        {/* Created date */}
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

      {/* Action footer — delete controls live here, outside the link */}
      {onDelete && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            padding: '0 20px 16px 20px',
            marginTop: 'auto',
          }}
        >
          {deleteError && (
            <p
              role="alert"
              style={{
                margin: 0,
                fontSize: '12px',
                color: 'var(--red)',
              }}
            >
              {deleteError}
            </p>
          )}

          {confirming ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                flexWrap: 'wrap',
              }}
            >
              <span style={{ fontSize: '13px', color: 'var(--text-2)' }}>
                Delete this agent?
              </span>
              <button
                type="button"
                onClick={runDelete}
                disabled={deleting}
                style={{ ...confirmDeleteButton, opacity: deleting ? 0.6 : 1 }}
              >
                {deleting ? 'Deleting…' : 'Confirm'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirming(false)
                  setDeleteError(null)
                }}
                disabled={deleting}
                style={cancelButton}
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => {
                setDeleteError(null)
                setConfirming(true)
              }}
              style={{ ...deleteTriggerButton, alignSelf: 'flex-start' }}
            >
              Delete
            </button>
          )}
        </div>
      )}
    </div>
  )
}
