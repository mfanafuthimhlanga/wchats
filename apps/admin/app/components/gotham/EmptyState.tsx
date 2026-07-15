import Link from 'next/link'

/**
 * The honest-empty-state block (UI-SPEC §14, §6.4, §12) — no prototype
 * precedent exists for this component (the prototypes are static demos with
 * every region seeded), so it carries no `.zone`/card chrome of its own by
 * design (AERIAL law: "no cards floating on it, only whisper-zones" — a
 * bare empty-state message inside a region shell is not a new container, it
 * IS the region's honest content). Used for the four not-yet-backed
 * operations-room regions (Live, Retrieval health, The bench, The prompt)
 * and the zero-agents dashboard state. Copy is always supplied by the
 * caller per the Copywriting Contract (UI-SPEC §12) — this component has no
 * default copy of its own so nothing here can drift from the contract
 * silently.
 */

interface EmptyStateProps {
  heading: string
  body: string
  linkHref?: string
  linkLabel?: string
  className?: string
}

export default function EmptyState({ heading, body, linkHref, linkLabel, className }: EmptyStateProps) {
  return (
    <div className={className}>
      <p
        style={{
          fontFamily: 'var(--display)',
          fontWeight: 500,
          fontSize: 15,
          letterSpacing: '-0.02em',
          color: 'var(--ink)',
        }}
      >
        {heading}
      </p>
      <p
        style={{
          marginTop: 6,
          fontSize: 13.5,
          lineHeight: 1.55,
          color: 'var(--ink-2)',
          maxWidth: '52ch',
        }}
      >
        {body}
      </p>
      {linkHref && linkLabel ? (
        <Link href={linkHref} className="btn btn-ghost" style={{ marginTop: 14 }}>
          {linkLabel}
        </Link>
      ) : null}
    </div>
  )
}
