import type { ReactNode, TdHTMLAttributes, ThHTMLAttributes } from 'react'

/**
 * The ledger primitive (UI-SPEC §13, §14; prototypes/gotham/app.css
 * `.ledger`). "Evidence is the decoration" — no drop shadows, no zebra
 * stripes beyond the hairline. This wrapper enforces the two accessibility
 * requirements every `.ledger` table must carry (UI-SPEC §13): a real
 * `<caption>` (visually hidden — several prototype pages are missing one and
 * that is called out as a gap to close on port) and `scope`-correct header
 * cells, via `LedgerColHead` / `LedgerRowHead` so a caller cannot forget to
 * set `scope`.
 */

const visuallyHidden = {
  position: 'absolute' as const,
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden' as const,
  clipPath: 'inset(50%)',
  whiteSpace: 'nowrap' as const,
  border: 0,
}

interface LedgerProps {
  /** Visually-hidden — describes the table's content for screen readers. */
  caption: string
  children: ReactNode
  className?: string
}

export default function Ledger({ caption, children, className }: LedgerProps) {
  const cls = className ? `ledger ${className}` : 'ledger'
  return (
    <table className={cls}>
      <caption style={visuallyHidden}>{caption}</caption>
      {children}
    </table>
  )
}

interface LedgerColHeadProps extends ThHTMLAttributes<HTMLTableCellElement> {
  numeric?: boolean
}

/** `<th scope="col">` — the header row cell. */
export function LedgerColHead({ numeric, className, children, ...rest }: LedgerColHeadProps) {
  const cls = [numeric ? 'num' : null, className].filter(Boolean).join(' ') || undefined
  return (
    <th scope="col" className={cls} {...rest}>
      {children}
    </th>
  )
}

type LedgerRowHeadProps = ThHTMLAttributes<HTMLTableCellElement>

/** `<th scope="row">` — a row-header cell (e.g. the readings table in agent.html). */
export function LedgerRowHead({ className, children, ...rest }: LedgerRowHeadProps) {
  return (
    <th scope="row" className={className} {...rest}>
      {children}
    </th>
  )
}

interface LedgerCellProps extends TdHTMLAttributes<HTMLTableCellElement> {
  numeric?: boolean
}

/** Plain `<td>` — offered for consistency with the two `th` helpers above. */
export function LedgerCell({ numeric, className, children, ...rest }: LedgerCellProps) {
  const cls = [numeric ? 'num' : null, className].filter(Boolean).join(' ') || undefined
  return (
    <td className={cls} {...rest}>
      {children}
    </td>
  )
}
