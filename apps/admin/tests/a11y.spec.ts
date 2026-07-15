import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

// UI2-08 a11y check: zero critical/serious axe violations per route, filled
// in by 20-15 now that the Gotham routes exist.
const AGENT_ID = 'demo-1'
const ROUTES = [
  '/',
  '/sign-in',
  '/sign-up',
  '/agents',
  '/agents/new',
  `/agents/${AGENT_ID}`,
  `/agents/${AGENT_ID}/soul`,
  `/agents/${AGENT_ID}/ingest`,
  `/agents/${AGENT_ID}/eval`,
  `/agents/${AGENT_ID}/deploy`,
  `/agents/${AGENT_ID}/settings`,
]

test.describe('@smoke axe a11y', () => {
  for (const route of ROUTES) {
    test(`${route} has zero critical/serious axe violations`, async ({ page }) => {
      await page.goto(route)
      await page.waitForLoadState('networkidle')

      const results = await new AxeBuilder({ page }).analyze()
      const blocking = results.violations.filter(
        (v) => v.impact === 'critical' || v.impact === 'serious'
      )

      expect(
        blocking,
        `${route}: ${blocking.map((v) => `${v.id} (${v.impact})`).join(', ')}`
      ).toEqual([])
    })
  }
})
