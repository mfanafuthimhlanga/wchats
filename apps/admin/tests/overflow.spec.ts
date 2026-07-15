import { test, expect } from '@playwright/test'

// Wave 0 stub (plan 20-01) -- SC4 / UI2-08 no-horizontal-overflow check.
// playwright.config.ts already defines the three required viewport projects
// (desktop-1440, laptop-1280, tablet-900); this spec runs once per project,
// so the viewport itself needs no per-test setup here. Filled in by 20-15.

const ROUTES = ['/', '/agents', '/agents/new']

test.describe('@smoke no horizontal overflow', () => {
  for (const route of ROUTES) {
    test(`${route} has no horizontal overflow at this viewport`, async ({ page }) => {
      test.fixme(true, 'Gotham route rebuild pending 20-15 parity gate (SC4)')

      await page.goto(route)

      const { scrollWidth, clientWidth } = await page.evaluate(() => {
        const el = document.scrollingElement as HTMLElement
        return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth }
      })

      expect(scrollWidth).toBeLessThanOrEqual(clientWidth)
    })
  }
})
