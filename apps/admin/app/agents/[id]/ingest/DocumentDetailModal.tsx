'use client'
import { useEffect, useRef, useCallback } from 'react'
import { useAuth } from '@clerk/nextjs'
import { useQuery } from '@tanstack/react-query'
import Chip, { type ChipVerdict } from '../../../components/gotham/Chip'

/**
 * Document detail modal — `/agents/[id]/ingest` (UI-SPEC S6.6, S7.1). No
 * Gotham prototype page has a modal equivalent (ingest.html has no popover),
 * so this restyles the existing dusk modal to `.zone`/`.well`/Chip tokens and
 * keeps its own layout + fetch wiring unchanged (GET .../documents/{id}/detail).
 */

// ---------------------------------------------------------------------------
// Types — mirror the backend GET /detail response shape.
// ---------------------------------------------------------------------------

interface ChunkMetadata {
  summary: string
  keywords: string[]
  questions: string[]
}

interface ChunkEntity {
  name: string
  type: string
  normalized: string
}

interface ChunkDetail {
  id: string
  chunk_index: number
  text: string
  metadata: ChunkMetadata | null
  entities: ChunkEntity[]
}

interface DocumentDetail {
  id: string
  title: string
  source_uri: string
  source_type: string
  parse_status: string
  created_at: string
  chunks: ChunkDetail[]
}

// ---------------------------------------------------------------------------
// Status verdict — colour is a verdict (UI-SPEC S8): map the raw
// parse_status string onto the closed Chip verdict union instead of a raw
// hex/bg pair. There is no amber/warning tier in Gotham, so "pending" /
// "processing" map to "live" (brightness, not a hue), not the dusk build's
// gold.
// ---------------------------------------------------------------------------

function parseStatusVerdict(status: string): ChipVerdict {
  if (status === 'complete' || status === 'parsed') return 'pass'
  if (status === 'pending' || status === 'processing') return 'live'
  if (status === 'failed') return 'seal'
  return 'mute'
}

const STATUS_LABEL: Partial<Record<ChipVerdict, string>> = {
  pass: 'Parsed',
  live: 'Processing',
  seal: 'Failed',
}

// ---------------------------------------------------------------------------
// Shared inline-style helpers
// ---------------------------------------------------------------------------

const pillBase: React.CSSProperties = {
  display: 'inline-block',
  padding: '3px 9px',
  borderRadius: 'var(--r-pill)',
  fontSize: '11px',
  fontWeight: 600,
  whiteSpace: 'nowrap',
  fontFamily: 'var(--sans)',
}

// Shared uppercase micro-label spec — matches the ported .label class
// (10px / 700 / 0.2em tracking, mono, ink-3).
const microLabel: React.CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: '10px',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.2em',
  color: 'var(--ink-3)',
}

const sectionLabel: React.CSSProperties = {
  ...microLabel,
  marginBottom: '6px',
}

// ---------------------------------------------------------------------------
// DocumentDetailModal
//
// Accessibility:
//   - role="dialog" + aria-modal="true" + aria-labelledby establishes the
//     modal landmark for screen readers (WCAG 4.1.2 Name, Role, Value).
//   - ESC closes the dialog (WCAG 2.1.2 No Keyboard Trap — the user can always
//     escape) and focus is moved into the dialog on open, then restored to the
//     triggering element on close (WCAG 2.4.3 Focus Order).
//   - Tab is trapped within the dialog while open so keyboard users cannot
//     wander into the inert page behind the backdrop.
//   - Backdrop click closes; the inner panel stops propagation so clicks inside
//     do not dismiss.
// ---------------------------------------------------------------------------

export default function DocumentDetailModal({
  agentId,
  documentId,
  onClose,
  returnFocusRef,
}: {
  agentId: string
  documentId: string
  onClose: () => void
  // Element to restore focus to when the modal closes (the clicked row).
  returnFocusRef?: React.RefObject<HTMLElement | null>
}) {
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  const panelRef = useRef<HTMLDivElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const titleId = `doc-detail-title-${documentId}`

  const detailQuery = useQuery({
    queryKey: ['agent-document-detail', agentId, documentId],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(
        `${apiBase}/api/v1/agents/${agentId}/documents/${documentId}/detail`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<DocumentDetail>
    },
    enabled: isLoaded && !!isSignedIn && !!documentId,
    staleTime: 30_000,
  })

  const doc = detailQuery.data

  // -------------------------------------------------------------------------
  // Focus management: move focus into the dialog on open, restore on unmount.
  // -------------------------------------------------------------------------
  useEffect(() => {
    // Snapshot the trigger element (falls back to the active element so focus
    // is still restored even when no ref was supplied).
    const trigger = returnFocusRef?.current ?? (document.activeElement as HTMLElement | null)
    // Defer one frame so the panel is mounted before we move focus into it.
    const raf = requestAnimationFrame(() => {
      closeButtonRef.current?.focus()
    })
    return () => {
      cancelAnimationFrame(raf)
      // Restore focus to the trigger after the dialog tears down.
      trigger?.focus?.()
    }
    // documentId identifies the dialog instance; re-run when it changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId])

  // -------------------------------------------------------------------------
  // Keyboard handling: ESC closes; Tab is trapped within the panel.
  // -------------------------------------------------------------------------
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== 'Tab') return

      const panel = panelRef.current
      if (!panel) return
      const focusable = panel.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
      )
      if (focusable.length === 0) {
        e.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement as HTMLElement | null

      if (e.shiftKey) {
        if (active === first || !panel.contains(active)) {
          e.preventDefault()
          last.focus()
        }
      } else if (active === last) {
        e.preventDefault()
        first.focus()
      }
    },
    [onClose],
  )

  return (
    <div
      // Backdrop — fixed full-screen, semi-transparent. Click to dismiss.
      onClick={onClose}
      onKeyDown={handleKeyDown}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(8,9,11,0.72)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
        className="zone"
        style={{
          width: '100%',
          maxWidth: '720px',
          maxHeight: '80vh',
          display: 'flex',
          flexDirection: 'column',
          padding: 0,
          overflow: 'hidden',
          fontFamily: 'var(--sans)',
        }}
      >
        {/* --------------------------------------------------------------- */}
        {/* Header (sticky region — not part of the scroll body)             */}
        {/* --------------------------------------------------------------- */}
        <div
          style={{
            padding: '20px 24px',
            borderBottom: '1px solid var(--hairline-soft)',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '12px',
            flexShrink: 0,
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2
              id={titleId}
              style={{
                fontFamily: 'var(--display)',
                fontSize: '17px',
                fontWeight: 500,
                letterSpacing: '-0.02em',
                color: 'var(--ink)',
                margin: 0,
                wordBreak: 'break-word',
              }}
            >
              {doc?.title || doc?.source_uri || 'Document'}
            </h2>

            {/* Badges row */}
            {doc && (
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '8px',
                  marginTop: '10px',
                  alignItems: 'center',
                }}
              >
                <Chip verdict="mute">{doc.source_type.toUpperCase()}</Chip>
                {(() => {
                  const verdict = parseStatusVerdict(doc.parse_status)
                  return (
                    <Chip verdict={verdict} dot={verdict === 'live'}>
                      {STATUS_LABEL[verdict] ?? doc.parse_status}
                    </Chip>
                  )
                })()}
              </div>
            )}

            {/* Source URI */}
            {doc?.source_uri && (
              <div
                style={{
                  fontSize: '12px',
                  color: 'var(--ink-3)',
                  fontFamily: 'var(--mono)',
                  marginTop: '8px',
                  wordBreak: 'break-all',
                }}
              >
                {doc.source_uri}
              </div>
            )}
          </div>

          {/* Close (×) */}
          <button
            ref={closeButtonRef}
            onClick={onClose}
            aria-label="Close document detail"
            style={{
              flexShrink: 0,
              width: '32px',
              height: '32px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'none',
              border: '1px solid var(--hairline-soft)',
              borderRadius: 'var(--r-control)',
              cursor: 'pointer',
              color: 'var(--ink-3)',
              fontSize: '18px',
              lineHeight: 1,
              fontFamily: 'var(--sans)',
              transition: 'color 140ms ease, border-color 140ms ease',
            }}
            onMouseEnter={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--ink)'
              ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--hairline-strong)'
            }}
            onMouseLeave={(e) => {
              ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--ink-3)'
              ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--hairline-soft)'
            }}
          >
            ×
          </button>
        </div>

        {/* --------------------------------------------------------------- */}
        {/* Scrollable body                                                 */}
        {/* --------------------------------------------------------------- */}
        <div
          style={{
            overflowY: 'auto',
            padding: '20px 24px 24px',
            flex: 1,
            minHeight: 0,
          }}
        >
          {/* Loading state */}
          {detailQuery.isLoading && (
            <div
              role="status"
              aria-live="polite"
              style={{ fontSize: '14px', color: 'var(--ink-3)', padding: '24px 0' }}
            >
              Loading document details…
            </div>
          )}

          {/* Error state */}
          {detailQuery.isError && (
            <div
              role="alert"
              style={{
                padding: '12px 16px',
                background: 'var(--fail-dim)',
                border: '1px solid color-mix(in oklch, var(--fail) 32%, transparent)',
                borderRadius: 'var(--r-panel)',
                fontSize: '14px',
                color: 'var(--fail)',
              }}
            >
              Failed to load document details. Please try again.
            </div>
          )}

          {/* Loaded content */}
          {doc && (
            <>
              {/* Summary stats */}
              <div style={{ display: 'flex', gap: '24px', marginBottom: '20px' }}>
                <Stat label="Chunks" value={doc.chunks.length} />
                <Stat
                  label="Entities"
                  value={doc.chunks.reduce((acc, c) => acc + c.entities.length, 0)}
                />
              </div>

              {/* Chunk list */}
              {doc.chunks.length === 0 ? (
                <div
                  style={{
                    fontSize: '13px',
                    color: 'var(--ink-2)',
                    fontStyle: 'italic',
                    padding: '16px 0',
                  }}
                >
                  No chunks have been generated for this document yet.
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {doc.chunks.map((chunk) => (
                    <ChunkCard key={chunk.id} chunk={chunk} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stat — a single summary metric (count + label)
// ---------------------------------------------------------------------------

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div
        style={{
          fontSize: '22px',
          fontWeight: 700,
          fontFamily: 'var(--mono)',
          fontVariantNumeric: 'tabular-nums',
          color: 'var(--ink)',
          lineHeight: 1.1,
        }}
      >
        {value}
      </div>
      <div style={{ ...microLabel, marginTop: '4px' }}>{label}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ChunkCard — one chunk: index badge, raw text, metadata, entities
// ---------------------------------------------------------------------------

function ChunkCard({ chunk }: { chunk: ChunkDetail }) {
  return (
    <div
      style={{
        border: '1px solid var(--hairline-soft)',
        borderRadius: 'var(--r-control)',
        background: 'var(--surface-2)',
        overflow: 'hidden',
      }}
    >
      {/* Chunk header: index badge */}
      <div
        style={{
          padding: '10px 14px',
          borderBottom: '1px solid var(--hairline-soft)',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}
      >
        <span
          style={{
            ...pillBase,
            background: 'var(--surface-2)',
            color: 'var(--ink-2)',
            fontFamily: 'var(--mono)',
            fontSize: '11px',
          }}
        >
          #{chunk.chunk_index + 1}
        </span>
      </div>

      <div style={{ padding: '14px' }}>
        {/* Raw chunk text — scrollable, capped at ~300px */}
        <div style={sectionLabel}>Text</div>
        <div
          className="well"
          style={{
            maxHeight: '300px',
            overflowY: 'auto',
            marginBottom: '14px',
            fontSize: '13px',
            lineHeight: 1.6,
            color: 'var(--ink-2)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {chunk.text}
        </div>

        {/* Metadata section */}
        {chunk.metadata ? (
          <div style={{ marginBottom: '14px' }}>
            {/* Summary */}
            {chunk.metadata.summary && (
              <div style={{ marginBottom: '12px' }}>
                <div style={sectionLabel}>Summary</div>
                <div style={{ fontSize: '13px', color: 'var(--ink-2)', lineHeight: 1.5 }}>
                  {chunk.metadata.summary}
                </div>
              </div>
            )}

            {/* Keywords */}
            {chunk.metadata.keywords.length > 0 && (
              <div style={{ marginBottom: '12px' }}>
                <div style={sectionLabel}>Keywords</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {chunk.metadata.keywords.map((kw, i) => (
                    <span
                      key={`${kw}-${i}`}
                      style={{
                        ...pillBase,
                        background: 'var(--surface-2)',
                        color: 'var(--ink-2)',
                        fontWeight: 500,
                      }}
                    >
                      {kw}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Questions */}
            {chunk.metadata.questions.length > 0 && (
              <div>
                <div style={sectionLabel}>Questions</div>
                <ul
                  style={{
                    margin: 0,
                    paddingLeft: '18px',
                    fontSize: '13px',
                    color: 'var(--ink-2)',
                    lineHeight: 1.6,
                  }}
                >
                  {chunk.metadata.questions.map((q, i) => (
                    <li key={`${i}`}>{q}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : (
          <div
            style={{
              marginBottom: '14px',
              fontSize: '12px',
              color: 'var(--ink-3)',
              fontStyle: 'italic',
            }}
          >
            No metadata extracted
          </div>
        )}

        {/* Entities section */}
        <div>
          <div style={sectionLabel}>Entities</div>
          {chunk.entities.length > 0 ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
              {chunk.entities.map((ent, i) => (
                <span
                  key={`${ent.normalized}-${ent.type}-${i}`}
                  style={{
                    ...pillBase,
                    background: 'var(--live-dim)',
                    color: 'var(--live-hot)',
                    fontWeight: 500,
                  }}
                >
                  {ent.name}{' '}
                  <span style={{ color: 'var(--ink-2)', fontWeight: 400 }}>({ent.type})</span>
                </span>
              ))}
            </div>
          ) : (
            <div style={{ fontSize: '12px', color: 'var(--ink-3)', fontStyle: 'italic' }}>
              No entities extracted
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
