'use client'
import { useState } from 'react'
import Link from 'next/link'
import { MessageCircle, Zap, Settings, Bot } from 'lucide-react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AgentCardProps {
  id: string
  name: string
  role: string
  status: string
  created_at: string
  disableNavigation?: boolean
  onDelete?: (id: string) => Promise<void>
}

// ---------------------------------------------------------------------------
// Status color map — uses design-g tokens from globals.css
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, { bg: string; fg: string; label: string }> = {
  ready: { bg: 'var(--green-bg)', fg: 'var(--green)', label: 'LIVE' },
  testing: { bg: 'var(--gold-bg)', fg: 'var(--gold)', label: 'TESTING' },
  pending: { bg: 'var(--lilac-dim)', fg: 'var(--lilac)', label: 'BUILDING' },
  provisioning: { bg: 'var(--lilac-dim)', fg: 'var(--lilac)', label: 'BUILDING' },
  error: { bg: 'var(--red-bg)', fg: 'var(--red)', label: 'Error' },
}

function getRoleIcon(role: string) {
  const r = role.toLowerCase()
  if (r.includes('support') || r.includes('service') || r.includes('customer')) {
    return <MessageCircle size={20} color="var(--accent)" strokeWidth={1.5} />
  }
  if (r.includes('sales') || r.includes('revenue')) {
    return <Zap size={20} color="var(--gold)" strokeWidth={1.5} />
  }
  if (r.includes('helpdesk') || r.includes('help') || r.includes('tech')) {
    return <Settings size={20} color="var(--lilac)" strokeWidth={1.5} />
  }
  return <Bot size={20} color="var(--text-3)" strokeWidth={1.5} />
}

function getRoleIconBg(role: string): string {
  const r = role.toLowerCase()
  if (r.includes('helpdesk') || r.includes('help') || r.includes('tech')) {
    return 'var(--lilac-dim)'
  }
  return 'var(--accent-dim)'
}

function getStatusColor(status: string) {
  return (
    STATUS_COLORS[status] ?? {
      bg: 'var(--chip)',
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
  color: 'var(--text-on-accent)',
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

export default function AgentCard({ id, name, role, status, created_at, disableNavigation, onDelete }: AgentCardProps) {
  const c = getStatusColor(status)
  const formattedDate = new Date(created_at).toLocaleDateString()

  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
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

  // The card lifts + flashes a coral top-bar on hover; the same affordance
  // mirrors on keyboard focus-within (onFocus/onBlur bubble from the inner
  // link + delete buttons) so tab-navigation gets the same visual cue.
  const active = hovered || focused

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      className="glass-strong"
      style={{
        display: 'flex',
        flexDirection: 'column',
        border: `1px solid ${active ? 'var(--border)' : 'var(--border-soft)'}`,
        borderTop: active ? '3px solid var(--accent)' : '1px solid var(--border-soft)',
        borderRadius: 'var(--radius-md)',
        boxShadow: active
          ? 'var(--glass-highlight), var(--shadow-lift)'
          : 'var(--glass-highlight), var(--shadow-card)',
        color: 'var(--text-1)',
        overflow: 'hidden',
        transform: active ? 'translateY(-2px)' : 'translateY(0)',
        transition: 'transform 0.2s, box-shadow 0.2s, border-color 0.2s',
        cursor: 'pointer',
      }}
    >
      {/* Navigable area — keep the delete controls OUTSIDE this anchor so we
          never nest interactive elements inside a link (invalid HTML + would
          otherwise trigger navigation when clicking Delete). */}
      {(() => {
        const cardContent = (
          <>
            {/* ac-top: [icon + name/role] left, status chip right */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', marginBottom: '14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1, minWidth: 0 }}>
                <div style={{
                  width: '40px', height: '40px',
                  background: getRoleIconBg(role),
                  borderRadius: 'var(--radius-sm)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  flexShrink: 0,
                }}>
                  {getRoleIcon(role)}
                </div>
                <div style={{ minWidth: 0 }}>
                  <h3 style={{
                    fontFamily: 'var(--font-display)',
                    fontSize: '15px',
                    fontWeight: 700,
                    fontVariationSettings: '"opsz" 144, "SOFT" 30',
                    color: 'var(--text-1)',
                    margin: '0 0 2px 0',
                    letterSpacing: '-0.01em',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}>
                    {name}
                  </h3>
                  <p style={{ fontSize: '11px', color: 'var(--text-3)', margin: 0, lineHeight: 1.3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {role}
                  </p>
                </div>
              </div>
              <span style={{
                padding: '3px 10px',
                borderRadius: 'var(--radius-pill)',
                fontSize: '10.5px',
                fontWeight: 600,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                background: c.bg,
                color: c.fg,
                whiteSpace: 'nowrap',
                flexShrink: 0,
              }}>
                {c.label}
              </span>
            </div>

            {/* ac-metrics: 3-column mini stat grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr 1fr',
          gap: '12px',
          paddingTop: '14px',
          paddingBottom: '14px',
          borderTop: '1px solid var(--border-soft)',
          borderBottom: '1px solid var(--border-soft)',
          marginBottom: '14px',
        }}>
          {[
            { label: 'Conv · 7D', val: '—' },
            { label: 'Faithfulness', val: '—' },
            { label: 'Cost/Sess', val: '—' },
          ].map(({ label, val }) => (
            <div key={label}>
              <div style={{ fontSize: '10.5px', fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: '4px' }}>
                {label}
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', fontWeight: 600, color: 'var(--text-1)' }}>
                {val}
              </div>
            </div>
          ))}
        </div>

        {/* ac-footer */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '16px' }}>
          <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>
            {formattedDate}
          </span>
          {!disableNavigation && (
            <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: '4px' }}>
              View details →
            </span>
          )}
        </div>
          </>
        )
        const sharedStyle: React.CSSProperties = {
          display: 'block',
          textDecoration: 'none',
          padding: '22px 22px 0 22px',
          color: 'var(--text-1)',
        }
        return disableNavigation
          ? <div style={sharedStyle}>{cardContent}</div>
          : <Link href={`/agents/${id}`} style={sharedStyle}>{cardContent}</Link>
      })()}

      {/* Action footer — delete controls live here, outside the link */}
      {onDelete && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            padding: '0 22px 16px 22px',
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
