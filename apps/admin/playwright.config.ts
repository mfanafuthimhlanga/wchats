import { defineConfig, devices } from '@playwright/test'

// Wave 0 harness (UI2-08 validation infra, plan 20-01).
// Three named viewport projects match the SC4 overflow-check widths
// (1440/1280/900) from 20-VALIDATION.md's Success-Criterion → Validation
// Map. Height is fixed at 900 for all three per the plan's task action.
//
// webServer boots the Next.js dev server for local runs. reuseExistingServer
// is on outside CI so a developer's already-running `pnpm dev` is reused
// instead of double-booting a second instance on port 3000.
export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'desktop-1440',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'laptop-1280',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } },
    },
    {
      name: 'tablet-900',
      use: { ...devices['Desktop Chrome'], viewport: { width: 900, height: 900 } },
    },
  ],
  webServer: {
    command: 'pnpm dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
