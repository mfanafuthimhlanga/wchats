import { chromium, expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import ts from 'typescript'
import type { LatestRun, OpenFinding } from '../app/agents/[id]/components/opsFormat'
import { PAGE_CSS as OPS_PAGE_CSS } from '../app/agents/[id]/opsCss'

// finding-meta-render.spec.ts renders FindingMeta and the latest-run line in Chromium,
// inside the banner and list markup AdversaryPanel wraps them in, with the console's
// globals.css and the ops page's own stylesheet (opsCss.ts). It reads what a reader
// sees: the middle dots per metadata line, the evidence sentence's case and full stop,
// the evidence line's computed colour in the banner and the list, and horizontal
// overflow at 390.

const GLOBALS = readFileSync(join(__dirname, '../app/globals.css'), 'utf-8').replace(/^@import .*$/m, '')


// Playwright compiles JSX in a spec into its own component-test objects, which
// react-dom cannot render. So the spec compiles the real component files with the
// TypeScript compiler the app already ships and renders them with the app's React.
const nodeRequire = createRequire(__filename)
const loaded = new Map<string, Record<string, unknown>>()

function load(file: string): Record<string, unknown> {
  const hit = loaded.get(file)
  if (hit) return hit
  const { outputText } = ts.transpileModule(readFileSync(file, 'utf-8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  })
  const module = { exports: {} as Record<string, unknown> }
  loaded.set(file, module.exports)
  const req = (spec: string) => {
    if (!spec.startsWith('.')) return nodeRequire(spec)
    const base = resolve(dirname(file), spec)
    return load(['.tsx', '.ts'].map((ext) => base + ext).find((f) => ts.sys.fileExists(f)) ?? base)
  }
  new Function('require', 'module', 'exports', outputText)(req, module, module.exports)
  return module.exports
}

const APP = join(__dirname, '../app')
type Component = (props: never) => unknown
const FindingMeta = load(join(APP, 'agents/[id]/components/FindingMeta.tsx')).default as Component
const Chip = load(join(APP, 'components/gotham/Chip.tsx')).default as Component
const Btn = load(join(APP, 'components/gotham/Btn.tsx')).default as Component
const ops = load(join(APP, 'agents/[id]/components/opsFormat.ts'))
const gateMessage = ops.gateMessage as (f: OpenFinding) => string
const latestRunLine = ops.latestRunLine as (r: LatestRun) => string
const { createElement } = nodeRequire('react') as typeof import('react')
const { renderToStaticMarkup } = nodeRequire('react-dom/server') as typeof import('react-dom/server')

function finding(over: Partial<OpenFinding>): OpenFinding {
  return {
    id: 'f1',
    run_id: 'r1',
    strategy_id: 's1',
    severity: 'critical',
    attack_vector: 'prompt_injection',
    probe_message: null,
    agent_response: null,
    turn_count: 2,
    created_at: null,
    description: 'The agent read out its instructions.',
    evidence: 'recorded_prompt_run',
    claims: ['system_prompt_disclosure'],
    ...over,
  }
}

const RULE_CRITICAL = finding({ id: 'rule' })
const WORD_CRITICAL = finding({ id: 'word', evidence: 'attacker_report', claims: ['system_prompt_described'] })
const PRE_317 = finding({ id: 'pre', severity: 'medium', evidence: null, claims: null, turn_count: null })

const LATEST: LatestRun = {
  run_id: '3f2a9c1b-0d4e-4a6b-9c1d-2e3f4a5b6c7d',
  finished_at: '2026-09-24T14:05:31+00:00',
  status: 'complete',
  reports_no_attack: 35,
  reports_dropped: 0,
  reports_on_attackers_word: 1,
}

const html = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(el)
const contain = html(createElement(Btn as never, { variant: 'ghost' }, 'Contain'))

/** The Adversary region's banner, list rows and latest-run line, in AdversaryPanel's markup. */
function region(banner: OpenFinding, rows: OpenFinding[]): string {
  const bannerHtml =
    `<div class="critical" data-where="banner">${html(createElement(Chip as never, { verdict: 'seal' }, 'Critical'))}` +
    `<p>${gateMessage(banner)}${html(createElement(FindingMeta as never, { finding: banner }))}</p><div>${contain}</div></div>`
  const rowHtml = rows
    .map(
      (f) =>
        `<div data-where="list" style="display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap;padding:12px 0">` +
        html(createElement(Chip as never, { verdict: f.severity === 'critical' ? 'seal' : 'mute' }, f.severity)) +
        `<p style="flex:1;min-width:220px;font-size:13.5px;margin:0;color:var(--ink-2)">${f.description}` +
        `${html(createElement(FindingMeta as never, { finding: f }))}</p><div>${contain}</div></div>`,
    )
    .join('')
  const latest = `<p class="foot-note latest-run" style="margin:-8px 0 18px">${latestRunLine(LATEST)}</p>`
  return (
    `<style>${GLOBALS}</style><style>${OPS_PAGE_CSS}</style>` +
    `<main style="padding:0 16px">${latest}${bannerHtml}<div style="margin-top:18px;display:flex;flex-direction:column">${rowHtml}</div></main>` +
    `<span data-ref style="color: var(--ink-2)">ref</span>`
  )
}

test('each metadata line carries one middle dot at most, and each evidence line is a sentence in --ink-2', async () => {
  test.setTimeout(30_000)
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  await page.setContent(region(RULE_CRITICAL, [WORD_CRITICAL, PRE_317]))
  const metaLines = await page.$$eval('.finding-meta, .latest-run', (els) => els.map((e) => e.textContent ?? ''))
  const evidence = await page.$$eval('.finding-evidence', (els) =>
    els.map((e) => ({
      where: (e.closest('[data-where]') as HTMLElement).dataset.where,
      text: e.textContent ?? '',
      color: getComputedStyle(e).color,
    })),
  )
  const ink2 = await page.$eval('[data-ref]', (e) => getComputedStyle(e).color)
  await browser.close()

  expect(metaLines).toHaveLength(4)
  for (const line of metaLines) expect(line.split('·').length - 1, line).toBeLessThanOrEqual(1)
  expect(evidence.map((e) => e.text)).toEqual([
    "Recorded from the agent's reply: it quoted its system prompt.",
    "On the attacker's word: it described its setup.",
    'Evidence unrecorded.',
  ])
  for (const e of evidence) expect(e.text, e.text).toMatch(/^[A-Z][^]*\.$/)
  expect(new Set(evidence.map((e) => e.where))).toEqual(new Set(['banner', 'list']))
  for (const e of evidence) expect(e.color, `${e.where}: ${e.text}`).toBe(ink2)
})

test('an 80-character attack vector wraps inside a 390 viewport, banner and list alike', async () => {
  test.setTimeout(30_000)
  const vector = 'multi_turn_social_engineering_via_nested_role_play_with_forged_tool_output_x_y_z'
  expect(vector).toHaveLength(80)
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } })
  await page.setContent(
    region(finding({ attack_vector: vector }), [finding({ id: 'w', attack_vector: vector, evidence: 'attacker_report' })]),
  )
  const widths = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  await browser.close()
  expect(widths.scroll).toBeLessThanOrEqual(widths.client)
})
