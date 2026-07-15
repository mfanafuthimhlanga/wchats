import { test, expect } from '@playwright/test'

// Wave 0 stub (plan 20-01) -- SC2 route-load + no-console-error smoke check.
// Filled in by 20-15 once the Gotham routes (landing, agents dashboard,
// agent-new) are rebuilt. Each route is `test.fixme()`d for now so the file
// parses and the test enumerates under `playwright test --list`, without
// asserting against pages that don't exist yet.

const ROUTES = ['/', '/agents', '/agents/new']

test.describe('@smoke route load', () => {
  for (const route of ROUTES) {
    test(`${route} loads without a page error`, async ({ page }) => {
      test.fixme(true, 'Gotham route rebuild pending 20-15 parity gate (SC2)')

      const pageErrors: string[] = []
      page.on('pageerror', (err) => pageErrors.push(err.message))

      await page.goto(route)

      expect(pageErrors).toEqual([])
    })
  }
})
