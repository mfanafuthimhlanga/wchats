import type { ElementType, HTMLAttributes, ReactNode } from 'react'

/**
 * The whisper-zone (UI-SPEC §14, prototypes/gotham/app.css `.zone`) — the
 * only "container" primitive on the bench. A tint and a hairline, never a
 * drop-shadowed card. `live` maps to `data-live="true"`, which gets the
 * `--live`-derived border glow (`.zone[data-live="true"]` in app.css) — the
 * one place a whisper-zone is allowed to look alive.
 */

interface ZoneProps extends Omit<HTMLAttributes<HTMLElement>, 'children'> {
  as?: ElementType
  live?: boolean
  children?: ReactNode
}

export default function Zone({ as, live, className, children, ...rest }: ZoneProps) {
  const Comp = as ?? 'div'
  const cls = className ? `zone ${className}` : 'zone'
  return (
    <Comp className={cls} data-live={live ? 'true' : undefined} {...rest}>
      {children}
    </Comp>
  )
}
