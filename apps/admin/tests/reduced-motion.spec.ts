import { test, expect } from '@playwright/test'

// Wave 0 stub (plan 20-01) -- SC4 / UI2-08 prefers-reduced-motion check.
// Asserts the gate shutter repaint (globals.css .tint rule) and row-fade
// transitions collapse to ~0 duration under `prefers-reduced-motion: reduce`.
// Filled in by 20-15 with a real transition-duration assertion once the gate
// interaction is ported (20-03 tokens + landing/operations-room waves).

test.use({ reducedMotion: 'reduce' })

test.describe('@smoke reduced motion', () => {
  test('landing skips shutter repaint animation under reduced motion', async ({ page }) => {
    test.fixme(true, 'Gate shutter interaction pending 20-03/20-15 (SC4)')

    await page.goto('/')

    const prefersReduced = await page.evaluate(
      () => matchMedia('(prefers-reduced-motion: reduce)').matches
    )
    expect(prefersReduced).toBe(true)
  })
})
