import { defineConfig } from '@playwright/test'

// playwright.unit.config.ts — browserless runner for opsFormat.ts's pure
// functions (23-03, Phase 23 Wave 0/1).
//
// Deliberately NOT an edit to the shipped `playwright.config.ts`. That
// config boots a Next dev server and runs every spec across three viewport
// projects — correct for the shell/route/a11y tests it owns, and pure waste
// for assertions over pure functions that need no DOM, no server, and no
// browser at all. More importantly, this config's own test directory
// (`./tests-unit`) sits OUTSIDE the shipped config's test directory
// (`./tests`) precisely so the shipped end-to-end suite's behaviour cannot
// change as a side effect of this file existing — a diff-scope gate in
// 23-03-PLAN.md Task 1 asserts `playwright.config.ts` stays byte-unchanged
// and that its `testDir` never reaches into `tests-unit`.
//
// No `use.baseURL`, no `projects` (device/viewport), no `webServer`. A spec
// file that never requests the `page` fixture never launches a browser, so
// the whole ops-format.spec.ts suite runs in about a second on the exact
// `@playwright/test` binary already installed for the end-to-end suite —
// zero new dependency, per WIRE-01's prohibition on adding one.
export default defineConfig({
  testDir: './tests-unit',
  fullyParallel: true,
  timeout: 10_000,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: 'list',
})
