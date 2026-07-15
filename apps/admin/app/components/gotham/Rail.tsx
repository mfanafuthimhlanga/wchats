'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'
import { AgentsIcon, DeployIcon, EvalIcon, IngestIcon, SettingsIcon } from './icons'

/**
 * The fixed 56px left rail — the "command deck" (UI-SPEC §5.1-B, port of
 * prototypes/gotham/app.css .rail + agents.html/ingest.html rail markup).
 *
 * Two variants, driven entirely by the current pathname, matching the
 * prototype's own split (UI-SPEC §5.1-B):
 *  - dashboard family (`/agents`, `/agents/new`): 4 icons, no Settings.
 *  - operations family (`/agents/[id]/**`): the same 4 icons + Settings
 *    pushed to the bottom via `.spacer`.
 *
 * Active state (aria-current="page") is pathname-driven and mirrors every
 * rail-bearing prototype page's own markup exactly: Settings/Deploy/Eval/
 * Ingest light up when their own route segment is present; Agents is the
 * implicit fallback for every other /agents/** route (dashboard, new,
 * overview, soul) — this is not a guess, it's what agents.html, agent.html,
 * agent-new.html and soul.html all literally do (they all mark
 * `aria-current="page"` on the Agents rail-btn).
 */

interface RailLinkProps {
  href: string | null
  label: string
  active: boolean
  children: ReactNode
}

function RailLink({ href, label, active, children }: RailLinkProps) {
  if (!href) {
    // No agent in the current path — Ingest/Eval/Deploy have nothing to
    // route to. An `<a>` with no `href` is not a hyperlink (not focusable,
    // not clickable) but still matches the `.rail a.rail-btn` CSS selector,
    // so the glyph stays correctly sized/positioned without faking
    // navigation to a route that does not exist yet (UI-SPEC §10 #6).
    // 20-15 fix (axe aria-prohibited-attr, real defect): an `<a>` with no
    // `href` has no implicit ARIA role (maps to `role=generic`), which
    // prohibits `aria-label`/`aria-disabled` (WAI-ARIA "no valid role
    // attribute"). Explicit `role="link"` restores the intended semantics —
    // the WAI-ARIA APG "disabled link" pattern — without adding a fake
    // `href` that would make it focusable/clickable.
    return (
      <a
        className="rail-btn"
        role="link"
        aria-disabled="true"
        aria-label={label}
        style={{ opacity: 0.35, cursor: 'default' }}
      >
        {children}
      </a>
    )
  }
  return (
    <Link className="rail-btn" href={href} aria-current={active ? 'page' : undefined} aria-label={label}>
      {children}
    </Link>
  )
}

export default function Rail() {
  const pathname = usePathname()

  // /agents/{id}/** -> agent id (excluding the /agents/new provisioning route,
  // which has no agent yet).
  const agentMatch = pathname.match(/^\/agents\/([^/]+)/)
  const agentId = agentMatch && agentMatch[1] !== 'new' ? agentMatch[1] : null

  const showSettings = agentId !== null

  const ingestHref = agentId ? `/agents/${agentId}/ingest` : null
  const evalHref = agentId ? `/agents/${agentId}/eval` : null
  const deployHref = agentId ? `/agents/${agentId}/deploy` : null
  const settingsHref = agentId ? `/agents/${agentId}/settings` : null

  const isSettings = pathname.includes('/settings')
  const isDeploy = !isSettings && pathname.includes('/deploy')
  const isEval = !isSettings && !isDeploy && pathname.includes('/eval')
  const isIngest = !isSettings && !isDeploy && !isEval && pathname.includes('/ingest')
  const isAgents = !isSettings && !isDeploy && !isEval && !isIngest

  return (
    <nav className="rail" aria-label="Console">
      <Link className="rail-mark" href="/" aria-label="W Chats home">
        w
      </Link>

      <Link className="rail-btn" href="/agents" aria-current={isAgents ? 'page' : undefined} aria-label="Agents">
        <AgentsIcon />
      </Link>

      <RailLink href={ingestHref} label="Ingest" active={isIngest}>
        <IngestIcon />
      </RailLink>

      <RailLink href={evalHref} label="Eval" active={isEval}>
        <EvalIcon />
      </RailLink>

      <RailLink href={deployHref} label="Deploy" active={isDeploy}>
        <DeployIcon />
      </RailLink>

      <span className="spacer" />

      {showSettings ? (
        <Link
          className="rail-btn"
          href={settingsHref as string}
          aria-current={isSettings ? 'page' : undefined}
          aria-label="Settings"
        >
          <SettingsIcon />
        </Link>
      ) : null}
    </nav>
  )
}
