import { test, expect } from '@playwright/test'

// SC4 / UI2-08 prefers-reduced-motion check (UI-SPEC S8.1 exact contract),
// filled in by 20-15 now that the gate interaction is ported.
//
// Mechanism under test: globals.css scopes `.tint` (applied to <body> by
// layout.tsx, and to the `/agents/[id]/*` wrapper) with a 600ms
// background/border/color/box-shadow transition, collapsed to 1ms under
// `prefers-reduced-motion: reduce` (S2.11) -- plus a global blanket
// `transition-duration: 0.01ms !important` fallback that also covers any
// other transition on the page (including `.ledger tbody tr`'s 140ms
// background transition, which stands in for the "row fade" contract: the
// spec's exact example -- `agent.html`'s `fileScenario()` inserting a new
// suite row with a 420ms opacity transition -- is a client-side demo not
// present in this real, backend-driven port, so the ledger row transition is
// used as the concrete, present-in-production analogue of the same
// mechanism).
test.use({ contextOptions: { reducedMotion: 'reduce' } })

test.describe('@smoke reduced motion', () => {
  test('prefers-reduced-motion is honored by the browser context', async ({ page }) => {
    // Belt-and-suspenders: the context-level `reducedMotion: 'reduce'` option
    // above should be sufficient, but explicitly emulating via CDP before
    // navigation guards against any context-option/navigation-timing race.
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/')
    const prefersReduced = await page.evaluate(
      () => matchMedia('(prefers-reduced-motion: reduce)').matches
    )
    expect(prefersReduced).toBe(true)
  })

  test('gate shutter repaint collapses to near-instant under reduced motion', async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/')

    const shutButton = page.getByRole('button', { name: 'Simulate a critical finding' })
    await expect(shutButton).toBeEnabled()
    await shutButton.click()

    // The gate attribute write is synchronous (GateProvider's effect runs on
    // the next commit) -- assert the state actually flipped before checking
    // the transition timing.
    await expect
      .poll(() => page.evaluate(() => document.documentElement.dataset.gate))
      .toBe('blocked')

    const tintDurationMs = await page.evaluate(() => {
      const body = document.body
      const duration = getComputedStyle(body).transitionDuration
      // transitionDuration can be a comma-separated list (one per
      // transitioned property) -- they should all collapse together, so the
      // max is representative.
      return Math.max(
        ...duration
          .split(',')
          .map((d) => d.trim())
          .map((d) => (d.endsWith('ms') ? parseFloat(d) : parseFloat(d) * 1000))
      )
    })

    // Spec: ".tint transition duration collapses to 1ms" -- allow generous
    // rounding headroom (browsers may report 0.01ms as effectively 0) while
    // still failing if the full 600ms easing survived.
    expect(tintDurationMs).toBeLessThanOrEqual(10)
  })

  test('row fade (ledger row transition) collapses to near-instant under reduced motion', async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/')

    const rowDurationMs = await page.evaluate(() => {
      const row = document.querySelector('.ledger tbody tr')
      if (!row) return null
      const duration = getComputedStyle(row).transitionDuration
      return Math.max(
        ...duration
          .split(',')
          .map((d) => d.trim())
          .map((d) => (d.endsWith('ms') ? parseFloat(d) : parseFloat(d) * 1000))
      )
    })

    expect(rowDurationMs, 'expected a .ledger tbody tr row on the landing page').not.toBeNull()
    // A newly-filed row must appear at full opacity immediately, not ease in
    // -- same near-instant threshold as the gate shutter.
    expect(rowDurationMs as number).toBeLessThanOrEqual(10)
  })
})
