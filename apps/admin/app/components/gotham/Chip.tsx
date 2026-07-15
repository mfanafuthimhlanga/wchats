import type { ReactNode } from 'react'

/**
 * The verdict-chip primitive (UI-SPEC §8, §14; prototypes/gotham/app.css
 * `.chip-*` / `.dot*`). This is where "colour is a verdict" is enforced BY
 * CONSTRUCTION: `verdict` is a closed union mapped to a fixed class table.
 * There is no `color` / `background` / raw-hex prop anywhere on this
 * component — a caller cannot introduce a fourth hue through Chip even if
 * they wanted to (UI-SPEC §8 rule 1: "Only three hue families exist in the
 * console ... Any new component ... must be re-expressed as one of these
 * three").
 */

export type ChipVerdict = 'live' | 'pass' | 'fail' | 'seal' | 'mute'

const VERDICT_CLASS: Record<ChipVerdict, string> = {
  live: 'chip-live',
  pass: 'chip-pass',
  fail: 'chip-fail',
  seal: 'chip-seal',
  mute: 'chip-mute',
}

interface ChipProps {
  verdict: ChipVerdict
  children: ReactNode
  /** Renders the `.dot` glyph before the label — `.dot-live` breathes when verdict="live". */
  dot?: boolean
  className?: string
}

export default function Chip({ verdict, children, dot, className }: ChipProps) {
  const cls = [ 'chip', VERDICT_CLASS[verdict], className ].filter(Boolean).join(' ')
  const dotCls = verdict === 'live' ? 'dot dot-live' : 'dot'
  return (
    <span className={cls}>
      {dot ? <i className={dotCls} aria-hidden="true" /> : null}
      {children}
    </span>
  )
}
