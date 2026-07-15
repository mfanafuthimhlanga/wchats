'use client'
import { usePathname } from 'next/navigation'
import { LockIcon } from './gotham/icons'

// ---------------------------------------------------------------------------
// Types — UNCHANGED (UI-SPEC §7.1: "Keep logic, restyle presentation. Do not
// rewrite deriveStepState.")
// ---------------------------------------------------------------------------

export type StepState = 'done' | 'active' | 'locked'

export interface JourneyStep {
  num: 1 | 2 | 3 | 4
  key: 'provision' | 'configure' | 'test' | 'deploy'
  title: string
  subtitle: string
  state: StepState
  href?: string
}

export interface JourneyStepperProps {
  steps: JourneyStep[]
}

// ---------------------------------------------------------------------------
// State derivation — the dusk build encoded this inline inside the render
// (an `isCurrentPage` override on top of the caller-supplied `state`). It is
// extracted here, unchanged, as a named exported function so the gating rule
// ("steps 2–4 locked until step 1 completes," UI2-04) is grep-able and
// testable directly. Behaviour is byte-for-byte identical to the prior
// inline computation: only restyle below this point.
// ---------------------------------------------------------------------------

export function deriveStepState(step: JourneyStep, currentStepKey: string | null): StepState {
  // If the visitor is on this step's own route and the data says locked,
  // treat it as active — the current page must never render as inaccessible.
  return currentStepKey === step.key && step.state === 'locked' ? 'active' : step.state
}

function resolveCurrentStepKey(steps: JourneyStep[], pathname: string): string | null {
  const exact = steps.find((s) => s.href && s.href === pathname)
  if (exact) return exact.key
  const prefixMatch = steps.reduce<JourneyStep | null>((best, s) => {
    if (!s.href || !pathname.startsWith(s.href + '/')) return best
    if (!best || s.href.length > (best.href?.length ?? 0)) return s
    return best
  }, null)
  return prefixMatch?.key ?? null
}

// ---------------------------------------------------------------------------
// JourneyStepper — restyled to the `.stepper`/`.station` visual contract
// (prototypes/gotham/agent-new.html, UI-SPEC §6.3): four stations on ONE
// hairline rule, not cards. Locked stations carry a lock glyph + reduced
// opacity + `data-locked="true"`. The only currently active caller
// (apps/admin/app/agents/new/page.tsx) never supplies `href`s — this is a
// single-page wizard, not a multi-route sidebar — so the stations render as
// plain `<li>`s, matching the prototype's non-interactive stepper exactly.
// ---------------------------------------------------------------------------

export default function JourneyStepper({ steps }: JourneyStepperProps) {
  const pathname = usePathname()
  const currentStepKey = resolveCurrentStepKey(steps, pathname)

  return (
    <>
      <ol className="stepper" aria-label="Provisioning">
        {steps.map((step) => {
          const visualState = deriveStepState(step, currentStepKey)
          const locked = visualState === 'locked'
          return (
            <li
              key={step.key}
              className="station"
              aria-current={visualState === 'active' ? 'step' : undefined}
              data-locked={locked ? 'true' : undefined}
            >
              <span className="station-mark" aria-hidden="true">
                {locked ? <LockIcon width={12} height={12} /> : <span className="sq" />}
              </span>
              <h2>
                {step.title}
                {locked && <span className="vh"> (locked)</span>}
              </h2>
              <p>{step.subtitle}</p>
            </li>
          )
        })}
      </ol>

      <style dangerouslySetInnerHTML={{ __html: STEPPER_CSS }} />
    </>
  )
}

// Static string literal only — never interpolate fetched/user data here
// (threat T-20-07-02, matches the page's own dangerouslySetInnerHTML pattern).
const STEPPER_CSS = `
  .stepper {
    list-style: none; margin: 0 0 4px; padding: 0;
    display: grid; grid-template-columns: repeat(4, 1fr);
    border-top: 1px solid var(--hairline);
  }
  .station { position: relative; padding: 21px 22px 0 0; }
  .station-mark {
    position: absolute; top: 0; left: 0;
    transform: translateY(-50%);
    display: flex; align-items: center; height: 14px;
    padding-right: 9px;
    background: var(--bg);
    color: var(--ink-3);
  }
  .station[aria-current="step"] .station-mark { color: var(--live); }
  .sq { width: 8px; height: 8px; background: currentColor; }
  .station h2 {
    font-family: var(--display); font-size: 14px; font-weight: 500;
    color: var(--ink-3); margin: 0;
  }
  .station[aria-current="step"] h2 { color: var(--ink); }
  .station p {
    margin: 5px 0 0;
    font-family: var(--mono); font-size: 11px; line-height: 1.6;
    color: var(--ink-3);
  }
  .station[data-locked="true"] { opacity: 0.62; }
  @media (max-width: 960px) {
    .stepper { grid-template-columns: repeat(2, 1fr); }
    .station { padding-bottom: 20px; }
  }
  @media (max-width: 560px) {
    .stepper { grid-template-columns: 1fr; }
  }
`
