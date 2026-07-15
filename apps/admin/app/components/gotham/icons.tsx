/**
 * Bespoke stroke-SVG icon set — ported verbatim from prototypes/gotham/*.html.
 *
 * These are hand-authored strokes, NOT an icon-library import. UI-SPEC §1:
 * "the nav-rail glyphs, checkmarks, and doc icons are load-bearing design
 * signatures (ORRERY 'engraved plate' language)" — a third-party icon
 * package is present in package.json but must never be substituted for this
 * glyph set (UI-SPEC §10 anti-pattern list is silent on this only because
 * it's a MUST-NOT stated directly in §1, not an already-observed bug).
 *
 * Source note: the prototype set contains two divergent icon families for the
 * same five rail glyphs — an 18x18 viewBox family (agents.html, ingest.html,
 * eval.html, deploy.html, agent-new.html) and a 24x24 viewBox family
 * (agent.html, soul.html, settings.html). This port standardizes on the
 * 18x18 family: it is the majority (5 of 8 rail-bearing prototype pages) and
 * it is the only family whose `.rail-mark` is a real `<a>` linking home,
 * matching UI-SPEC §5.1-B's literal requirement ("`.rail-mark` ... links to
 * `/` (home)") — the 24x24 family's `.rail-mark` is a non-interactive
 * `<span>`. This is a documented port decision, not a redesign.
 */

import type { ReactNode, SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

function railGlyph(viewBox: string, paths: ReactNode) {
  return function Icon({ width = 18, height = 18, ...rest }: IconProps) {
    return (
      <svg
        viewBox={viewBox}
        width={width}
        height={height}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
        {...rest}
      >
        {paths}
      </svg>
    )
  }
}

function utilGlyph(viewBox: string, paths: ReactNode) {
  return function Icon({ width = 14, height = 14, ...rest }: IconProps) {
    return (
      <svg
        viewBox={viewBox}
        width={width}
        height={height}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.3}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
        {...rest}
      >
        {paths}
      </svg>
    )
  }
}

// ── rail glyphs (18x18) — prototypes/gotham/{agents,ingest,eval,deploy}.html ─
export const AgentsIcon = railGlyph(
  '0 0 18 18',
  <>
    <path d="M9 1.8 15.4 5.4v7.2L9 16.2 2.6 12.6V5.4z" />
    <circle cx="9" cy="9" r="2.6" />
  </>
)

export const IngestIcon = railGlyph(
  '0 0 18 18',
  <>
    <path d="M9 2.2v7.6" />
    <path d="M6 6.8 9 9.8l3-3" />
    <path d="M3 12.2v2.6h12v-2.6" />
  </>
)

export const EvalIcon = railGlyph(
  '0 0 18 18',
  <>
    <path d="M3 15h12" />
    <path d="M5.6 15V9.2" />
    <path d="M9 15V4.4" />
    <path d="M12.4 15v-3.4" />
  </>
)

export const DeployIcon = railGlyph(
  '0 0 18 18',
  <>
    <circle cx="9" cy="9" r="6.4" />
    <path d="M2.6 9h12.8" />
    <path d="M9 2.6c3.2 3.4 3.2 9.4 0 12.8-3.2-3.4-3.2-9.4 0-12.8z" />
  </>
)

export const SettingsIcon = railGlyph(
  '0 0 18 18',
  <>
    <path d="M2.6 5.4h7.7" />
    <path d="M13.7 5.4h1.7" />
    <circle cx="12" cy="5.4" r="1.7" />
    <path d="M2.6 12.6h1.7" />
    <path d="M7.7 12.6h7.7" />
    <circle cx="6" cy="12.6" r="1.7" />
  </>
)

// ── small utility glyphs (14x14) — used elsewhere across Gotham routes ──────
// checkmark: prototypes/gotham/agent-new.html "on create" checklist ticks
export const CheckIcon = utilGlyph(
  '0 0 14 14',
  <>
    <rect x="0.7" y="0.7" width="12.6" height="12.6" rx="2" />
    <path d="M3.7 7.1 5.9 9.4l4.5-5" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" />
  </>
)

// doc: prototypes/gotham/ingest.html knowledge-base ledger document glyph
export const DocIcon = utilGlyph(
  '0 0 14 14',
  <>
    <path d="M8 1.2H3.4v11.6h7.2V3.8z" />
    <path d="M8 1.2v2.6h2.6" />
  </>
)

// lock: prototypes/gotham/agent-new.html stepper locked-station glyph
export const LockIcon = utilGlyph(
  '0 0 14 14',
  <>
    <rect x="2.2" y="6" width="9.6" height="6.6" rx="1.2" />
    <path d="M4.6 6V4.4a2.4 2.4 0 0 1 4.8 0V6" />
  </>
)
