'use client'
import { useState } from 'react'
import Link from 'next/link'

import Chip, { type ChipVerdict } from './gotham/Chip'
import Btn from './gotham/Btn'

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
// Status -> verdict-chip map (UI-SPEC §6.2, §8 "colour is a verdict")
// ---------------------------------------------------------------------------

interface StatusChipSpec {
  verdict: ChipVerdict
  label: string
  dot?: boolean
}

const STATUS_CHIP: Record<string, StatusChipSpec> = {
  ready: { verdict: 'live', label: 'Live', dot: true },
  testing: { verdict: 'mute', label: 'Testing' },
  pending: { verdict: 'mute', label: 'Building' },
  provisioning: { verdict: 'mute', label: 'Building' },
  // "error" carries the same claim as a shut gate — same red, same seal.
  error: { verdict: 'seal', label: 'Error' },
}

function getStatusChip(status: string): StatusChipSpec {
  return STATUS_CHIP[status] ?? { verdict: 'mute', label: status }
}

// ---------------------------------------------------------------------------
// AgentCard — a `.zone.card` (UI-SPEC §6.2, §14). Exactly one real `<a>`
// (the stretched `.card-open::after` link) lives in the tree; the name is a
// `<span>` (via `<h3>`), never a nested anchor (§10 anti-pattern 5).
// ---------------------------------------------------------------------------

export default function AgentCard({ id, name, status, created_at, disableNavigation, onDelete }: AgentCardProps) {
  // `role` is part of the data contract (kept for API-shape compatibility)
  // but the Gotham `.zone.card` (agents.html) has no role/icon slot — name +
  // mono id + verdict chips is the whole identity block.
  const chip = getStatusChip(status)
  // "shut" mirrors the gate-shut claim on the agent operations room's
  // gatebar (§6.4) — the only status this list has enough signal to treat
  // that way is a hard error.
  const shut = status === 'error'
  const formattedDate = new Date(created_at).toISOString().slice(0, 10)

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
    <article
      className="zone card"
      data-live={status === 'ready' ? 'true' : undefined}
      data-shut={shut ? 'true' : undefined}
    >
      <div className="card-top">
        <div>
          <h3 className="card-name">{name}</h3>
          <span className="card-id mono">{id}</span>
        </div>
        <div className="card-chips">
          <Chip verdict={chip.verdict} dot={chip.dot}>{chip.label}</Chip>
        </div>
      </div>

      <div className="hair" />

      {/* Docs / Pass rate / Sessions — honest-empty: GET /agents does not
          return per-agent corpus/eval/session counts, so these render as
          placeholders rather than fabricated numbers (§10 anti-pattern 6). */}
      <div className="metrics">
        <div>
          <span className="label">Docs</span>
          <span className="mono">—</span>
        </div>
        <div>
          <span className="label">Pass rate</span>
          <span className="mono pending">pending</span>
        </div>
        <div>
          <span className="label">Sessions</span>
          <span className="mono">—</span>
        </div>
      </div>

      <div className="card-foot">
        <span className="mono">created {formattedDate}</span>
        {disableNavigation ? (
          <span className="card-open">Open →</span>
        ) : (
          <Link className="card-open" href={`/agents/${id}`} aria-label={`Open ${name}`}>
            Open →
          </Link>
        )}
      </div>

      {/* Delete controls — raised above the stretched `.card-open::after`
          link (see `.card-actions` in globals.css) so they stay clickable. */}
      {onDelete && (
        <div className="card-actions">
          {deleteError && (
            <p role="alert" style={{ margin: '0 0 8px', fontSize: '12px', color: 'var(--fail)' }}>
              {deleteError}
            </p>
          )}

          {confirming ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '12.5px', color: 'var(--ink-2)' }}>Delete this agent?</span>
              <Btn variant="seal" onClick={runDelete} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Confirm'}
              </Btn>
              <Btn
                variant="ghost"
                onClick={() => {
                  setConfirming(false)
                  setDeleteError(null)
                }}
                disabled={deleting}
              >
                Cancel
              </Btn>
            </div>
          ) : (
            <Btn
              variant="ghost"
              onClick={() => {
                setDeleteError(null)
                setConfirming(true)
              }}
            >
              Delete
            </Btn>
          )}
        </div>
      )}
    </article>
  )
}
