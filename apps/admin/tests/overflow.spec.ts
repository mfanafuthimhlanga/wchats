import { test, expect } from '@playwright/test'

// SC4 / UI2-08 no-horizontal-overflow check, filled in by 20-15. Runs once
// per viewport project (desktop-1440 / laptop-1280 / tablet-900 -- defined
// in playwright.config.ts) against every real routed page. The eval page's
// telemetry leader-line layout at 900px is called out in the plan as the
// most likely regression, so `/agents/demo-1/eval` is included explicitly,
// not just the base four routes.
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

test.describe('@smoke no horizontal overflow', () => {
  for (const route of ROUTES) {
    test(`${route} has no horizontal overflow at this viewport`, async ({ page }) => {
      await page.goto(route)
      await page.waitForLoadState('networkidle')

      const { scrollWidth, clientWidth } = await page.evaluate(() => {
        const el = document.scrollingElement as HTMLElement
        return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth }
      })

      expect(scrollWidth, `${route}: scrollWidth (${scrollWidth}) > clientWidth (${clientWidth})`).toBeLessThanOrEqual(
        clientWidth
      )
    })
  }
})
