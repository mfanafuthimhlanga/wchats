import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

// Wave 0 stub (plan 20-01) -- UI2-08 a11y check: zero critical/serious axe
// violations per route. Filled in by 20-15 once the Gotham routes exist.

const ROUTES = ['/', '/agents', '/agents/new']

test.describe('@smoke axe a11y', () => {
  for (const route of ROUTES) {
    test(`${route} has zero critical/serious axe violations`, async ({ page }) => {
      test.fixme(true, 'Gotham route rebuild pending 20-15 parity gate (UI2-08)')

      await page.goto(route)

      const results = await new AxeBuilder({ page }).analyze()
      const blocking = results.violations.filter(
        (v) => v.impact === 'critical' || v.impact === 'serious'
      )

      expect(blocking).toEqual([])
    })
  }
})
