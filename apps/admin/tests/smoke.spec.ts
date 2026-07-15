import { test, expect } from '@playwright/test'

// SC2 route-load + no-console-error smoke check, filled in by 20-15 once the
// Gotham routes are rebuilt (was `test.fixme()`'d in 20-01).
//
// Route list: every real routed page in the port (landing, auth, agents
// dashboard, agent-new, and the operations room + its five sub-routes).
// `/agents/demo-1*` relies on NEXT_PUBLIC_DEMO=true (playwright.config.ts
// webServer.env) to bypass the Clerk route guard — see that file's comment
// for the demo-mode caveat (the sub-routes themselves are not demo-aware and
// render their own loading/empty state without a real session, which is
// exactly what this smoke check needs: a real DOM, no JS exception).
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

test.describe('@smoke route load', () => {
  for (const route of ROUTES) {
    test(`${route} loads without a page error`, async ({ page }) => {
      const pageErrors: string[] = []
      page.on('pageerror', (err) => pageErrors.push(err.message))

      const response = await page.goto(route)
      expect(response?.ok(), `${route} did not return an ok HTTP status`).toBe(true)

      // Let client-side effects (queries, mounts) settle before asserting.
      await page.waitForLoadState('networkidle')

      expect(pageErrors, `console pageerror(s) on ${route}`).toEqual([])
    })
  }
})

// SC2 three.js confinement: the specimen (SceneMount -> <canvas>) is
// permitted only on landing/auth (UI-SPEC S5.3) and must render on `/`. It
// must be structurally absent from every authenticated console route so the
// ~600KB `three` chunk never enters the authenticated bundle's first-load JS.
test.describe('@smoke three.js confinement', () => {
  test('/ mounts the three.js specimen canvas', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('canvas')).toHaveCount(1)
  })

  const CONFINED_ROUTES = [
    '/agents',
    '/agents/new',
    `/agents/${AGENT_ID}`,
    `/agents/${AGENT_ID}/soul`,
    `/agents/${AGENT_ID}/ingest`,
    `/agents/${AGENT_ID}/eval`,
    `/agents/${AGENT_ID}/deploy`,
    `/agents/${AGENT_ID}/settings`,
  ]

  for (const route of CONFINED_ROUTES) {
    test(`${route} has no <canvas> (three.js confined)`, async ({ page }) => {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      await expect(page.locator('canvas')).toHaveCount(0)
    })
  }
})
