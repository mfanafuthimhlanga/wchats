import { defineConfig, devices } from '@playwright/test'

// Wave 0 harness (UI2-08 validation infra, plan 20-01).
// Three named viewport projects match the SC4 overflow-check widths
// (1440/1280/900) from 20-VALIDATION.md's Success-Criterion → Validation
// Map. Height is fixed at 900 for all three per the plan's task action.
//
// webServer boots the Next.js dev server for local runs. reuseExistingServer
// is on outside CI so a developer's already-running `pnpm dev` is reused
// instead of double-booting a second instance.
//
// Port 3100 (not the framework-conventional 3000): this machine already has
// an unrelated process bound to :3000 (confirmed via `netstat` + a probe
// request returning an unrelated Fastify-style 404 body) — reusing it would
// silently point the whole suite at the wrong server. 3100 is exclusive to
// this Playwright run (20-15 deviation, Rule 3 blocking-issue fix).
//
// NEXT_PUBLIC_DEMO=true (webServer.env): `/agents/[id]` and its sub-routes
// require a signed-in Clerk session; there is no seeded test-auth session
// available to this harness. Demo mode (proxy.ts) makes every route public
// and the agents dashboard renders two hardcoded demo agents (`demo-1`,
// `demo-2`) instead of querying the API, which is what makes `/agents/demo-1`
// reachable at all. NOTE: the `/agents/[id]/*` sub-routes themselves are NOT
// demo-mode-aware (they still call `useAuth()`/`getToken()`) — with no real
// session, their own data queries stay disabled and they render their
// natural loading/empty-state UI, not populated data. This validates
// routing/chrome/three-confinement/overflow/a11y of the shell; it does not
// exercise fully data-populated layouts (e.g. real eval telemetry). See
// 20-15-SUMMARY.md for the explicit list of what this does and does not
// cover.
export default defineConfig({
  testDir: './tests',
  // 20-15 deviation (Rule 3): 4GB dev machine + Turbopack's on-demand
  // per-route first-compile (observed up to ~30s/route cold) means the
  // default 30s test timeout and unthrottled worker count risk both timing
  // out mid-compile and starving the single dev-server process of memory
  // under concurrent load. workers: 2 keeps parallelism modest; timeout: 60s
  // gives cold compiles headroom without masking genuine hangs.
  fullyParallel: true,
  workers: 2,
  // 90s (not 60s): a11y.spec.ts's `/`, `/sign-in`, `/sign-up` runs were
  // observed timing out at 60s on tablet-900/desktop-1440 -- Clerk's
  // <SignIn>/<SignUp> load an external script from Clerk's CDN, and axe's
  // full-page analyze() on top of a cold Turbopack compile pushed some runs
  // past 60s with no assertion failure (the scan just didn't finish in
  // time). 90s gives that combination headroom without masking genuine
  // hangs.
  timeout: 90_000,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:3100',
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
    // Bare `pnpm` is not guaranteed to be on PATH in every shell this spawns
    // from (20-15 execution_mode note) — `corepack pnpm` resolves reliably.
    command: 'corepack pnpm exec next dev -p 3100',
    url: 'http://localhost:3100',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_DEMO: 'true',
    },
  },
})
