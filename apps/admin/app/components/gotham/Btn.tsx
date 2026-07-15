import type { ButtonHTMLAttributes } from 'react'

/**
 * The button primitive (UI-SPEC §14; prototypes/gotham/app.css `.btn*`).
 * `variant` maps to the three button treatments the design system defines —
 * primary (live fill), ghost (hairline outline), seal (destructive, reserved
 * for actions like "Delete permanently" / "Simulate a critical finding").
 * `disabled` renders through app.css's `.btn[disabled]` rule automatically.
 */

export type BtnVariant = 'primary' | 'ghost' | 'seal'

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant
}

export default function Btn({ variant = 'primary', className, type = 'button', ...rest }: BtnProps) {
  const cls = ['btn', `btn-${variant}`, className].filter(Boolean).join(' ')
  return <button type={type} className={cls} {...rest} />
}
