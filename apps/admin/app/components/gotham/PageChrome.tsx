'use client'

import { useId, type CSSProperties } from 'react'

/**
 * The fixed, non-scrolling background layers every routed page renders
 * behind its content (UI-SPEC §5.2): the graticule, the bloom, and four
 * registration crosshairs — plus the visually-hidden "Skip to content" link
 * every page needs exactly once. All decorative layers are `aria-hidden` and
 * `pointer-events: none` (UI-SPEC §5.2: "None of these three layers may ever
 * ... " — they carry no interactive surface).
 *
 * Cross offsets differ per page shell (UI-SPEC §5.2, §6). Two families are
 * pre-baked as exports below — the 4-icon rail family (agents/ingest/eval/
 * deploy/agent-new, 78/22 desktop -> 22/78 under 900px) and the 5-icon
 * operations-room family (agent/soul/settings, 70/16 -> 16/70) — callers on
 * later routes pass `offsets`/`mobileOffsets` explicitly when they differ.
 */

export interface CrossOffset {
  top?: number
  bottom?: number
  left?: number
  right?: number
}

export interface PageChromeOffsets {
  tl: CrossOffset
  tr: CrossOffset
  bl: CrossOffset
  br: CrossOffset
}

// prototypes/gotham/{agents,ingest,eval,deploy,agent-new}.html
export const RAIL_4_DESKTOP: PageChromeOffsets = {
  tl: { left: 78, top: 22 },
  tr: { right: 22, top: 22 },
  bl: { left: 78, bottom: 22 },
  br: { right: 22, bottom: 22 },
}
export const RAIL_4_MOBILE: PageChromeOffsets = {
  tl: { left: 22, top: 22 },
  tr: { right: 22, top: 22 },
  bl: { left: 22, bottom: 78 },
  br: { right: 22, bottom: 78 },
}

// prototypes/gotham/{agent,soul,settings}.html
export const RAIL_5_DESKTOP: PageChromeOffsets = {
  tl: { left: 70, top: 16 },
  tr: { right: 16, top: 16 },
  bl: { left: 70, bottom: 16 },
  br: { right: 16, bottom: 16 },
}
export const RAIL_5_MOBILE: PageChromeOffsets = {
  tl: { left: 16, top: 16 },
  tr: { right: 16, top: 16 },
  bl: { left: 16, bottom: 70 },
  br: { right: 16, bottom: 70 },
}

interface PageChromeProps {
  offsets?: PageChromeOffsets
  mobileOffsets?: PageChromeOffsets
  /** Where the skip link should land. Defaults to the `.deck` main landmark. */
  skipTargetId?: string
}

function toStyle(offset: CrossOffset): CSSProperties {
  const style: CSSProperties = { pointerEvents: 'none' }
  if (offset.top !== undefined) style.top = offset.top
  if (offset.bottom !== undefined) style.bottom = offset.bottom
  if (offset.left !== undefined) style.left = offset.left
  if (offset.right !== undefined) style.right = offset.right
  return style
}

function toCssDecl(offset: CrossOffset): string {
  const decls: string[] = []
  if (offset.top !== undefined) decls.push(`top:${offset.top}px`)
  if (offset.bottom !== undefined) decls.push(`bottom:${offset.bottom}px`)
  if (offset.left !== undefined) decls.push(`left:${offset.left}px`)
  if (offset.right !== undefined) decls.push(`right:${offset.right}px`)
  return decls.join(';')
}

export default function PageChrome({
  offsets = RAIL_4_DESKTOP,
  mobileOffsets = RAIL_4_MOBILE,
  skipTargetId = 'main',
}: PageChromeProps) {
  const uid = useId().replace(/[:]/g, '')
  const cls = (corner: 'tl' | 'tr' | 'bl' | 'br') => `pc-${uid}-${corner}`

  return (
    <>
      <a className="skip" href={`#${skipTargetId}`}>
        Skip to content
      </a>

      <div className="graticule" aria-hidden="true" style={{ pointerEvents: 'none' }} />
      <div className="bloom" aria-hidden="true" style={{ pointerEvents: 'none' }} />

      <div className={`cross ${cls('tl')}`} aria-hidden="true" style={toStyle(offsets.tl)} />
      <div className={`cross ${cls('tr')}`} aria-hidden="true" style={toStyle(offsets.tr)} />
      <div className={`cross ${cls('bl')}`} aria-hidden="true" style={toStyle(offsets.bl)} />
      <div className={`cross ${cls('br')}`} aria-hidden="true" style={toStyle(offsets.br)} />

      <style>{`
        @media (max-width: 900px) {
          .${cls('tl')} { ${toCssDecl(mobileOffsets.tl)} }
          .${cls('tr')} { ${toCssDecl(mobileOffsets.tr)} }
          .${cls('bl')} { ${toCssDecl(mobileOffsets.bl)} }
          .${cls('br')} { ${toCssDecl(mobileOffsets.br)} }
        }
      `}</style>
    </>
  )
}
