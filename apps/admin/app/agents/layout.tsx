'use client'
import type { ReactNode } from 'react'
import PageChrome from '../components/gotham/PageChrome'
import Rail from '../components/gotham/Rail'

/**
 * The console shell (UI-SPEC S5.1-B, "Shell B") for every /agents/** route.
 * Replaces the old dusk top-bar with the fixed left Rail. PageChrome
 * renders the graticule/bloom/crosses + the "Skip to content" link once per
 * page; `.deck` (app.css) reserves the 56px the Rail occupies so content
 * never scrolls under it.
 */
export default function AgentsLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <PageChrome />
      <Rail />
      <main className="deck" id="main">
        {children}
      </main>
    </>
  )
}
