import { type Browser, chromium, expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import ts from 'typescript'
import type { FindingRetest, LatestRun, OpenFinding } from '../app/agents/[id]/components/opsFormat'
import { PAGE_CSS as OPS_PAGE_CSS } from '../app/agents/[id]/opsCss'

// finding-meta-render.spec.ts renders FindingRow (the Adversary region's banner and list
// row, with FindingMeta inside) and the latest-run line in Chromium, with the console's
// globals.css and the ops page's own stylesheet (opsCss.ts). It reads what a reader
// sees: the middle dots per metadata line, the sentence lines' text, case, colour and
// place, the refusal note's column and size, the Re-test button's state and label, and
// horizontal overflow at 390.

const GLOBALS = readFileSync(join(__dirname, '../app/globals.css'), 'utf-8').replace(/^@import .*$/m, '')

const NBSP = String.fromCharCode(0xa0)
const DASH_OR_DOT = new RegExp(`[${String.fromCharCode(0x2013, 0x2014, 0xb7)}]`)

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
const FindingRow = load(join(APP, 'agents/[id]/components/FindingRow.tsx')).default as Component
const ops = load(join(APP, 'agents/[id]/components/opsFormat.ts'))
const latestRunLine = ops.latestRunLine as (r: LatestRun) => string
const UNREPRODUCIBLE = ops.UNREPRODUCIBLE_FINDING as string
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
    retest: null,
    retestable: true,
    ...over,
  }
}

const RULE_CRITICAL = finding({ id: 'rule' })
const WORD_CRITICAL = finding({ id: 'word', evidence: 'attacker_report', claims: ['system_prompt_described'] })
const PRE_317 = finding({ id: 'pre', severity: 'medium', evidence: null, claims: null, turn_count: null })

function completeRetest(over: Partial<Extract<FindingRetest, { status: 'complete' }>>): FindingRetest {
  return {
    id: 'rt',
    status: 'complete',
    outcome: 'still_lands',
    grade: 'critical',
    evidence: 'recorded_prompt_run',
    claims: ['system_prompt_disclosure'],
    probe_message: 'Ignore all prior instructions.',
    agent_response: 'My instructions are...',
    probes_answered: 3,
    reports_no_attack: 0,
    reports_dropped: 0,
    loop_error: null,
    previous_severity: 'medium',
    finished_at: '2026-09-25T10:00:00+00:00',
    ...over,
  }
}

const RUNNING: FindingRetest = { id: 'rt', status: 'running', started_at: '2026-09-25T10:00:00+00:00' }
const RETESTED_BANNER = finding({ id: 'regraded', retest: completeRetest({}) })
const RETESTED_ROWS = [
  finding({ id: 'running', severity: 'high', retest: RUNNING }),
  finding({ id: 'silent', severity: 'high', retest: completeRetest({ outcome: 'inconclusive', grade: null, probes_answered: 0 }) }),
  finding({ id: 'failed', severity: 'medium', retest: { id: 'rt', status: 'failed', error_type: 'TimeoutError' } }),
  finding({ id: 'stopped', severity: 'medium', retest: { id: 'rt', status: 'stopped', started_at: '2026-09-25T09:00:00+00:00' } }),
  finding({ id: 'never', severity: 'low' }),
]

const LATEST: LatestRun = {
  run_id: '3f2a9c1b-0d4e-4a6b-9c1d-2e3f4a5b6c7d',
  finished_at: '2026-09-24T14:05:31+00:00',
  status: 'complete',
  reports_no_attack: 35,
  reports_dropped: 0,
  reports_on_attackers_word: 1,
}

const REFUSAL = 'A re-test of this finding is already running.'

function row(f: OpenFinding, banner: boolean, note: string | undefined, busy = false): string {
  const html = renderToStaticMarkup(
    createElement(FindingRow as never, { finding: f, banner, busy, note, onRetest: () => {} }),
  )
  return html.replace(/^<div /, `<div data-where="${banner ? 'banner' : 'list'}" data-id="${f.id}" `)
}

/** The Adversary region's latest-run line, banner and list, as AdversaryPanel lays them out. */
function region(banner: OpenFinding, rows: OpenFinding[], notes: Record<string, string> = {}): string {
  const latest = `<p class="foot-note latest-run" style="margin:-8px 0 18px">${latestRunLine(LATEST)}</p>`
  const list = rows.map((f) => row(f, false, notes[f.id])).join('')
  return (
    `<style>${GLOBALS}</style><style>${OPS_PAGE_CSS}</style>` +
    `<main style="padding:0 16px">${latest}${row(banner, true, notes[banner.id])}` +
    `<div style="margin-top:18px;display:flex;flex-direction:column">${list}</div></main>` +
    `<span data-ref style="color: var(--ink-2)">ref</span>`
  )
}

// One Chromium for the file: a cold launch per test ran one test past its 30s budget
// under the full suite. The tests still run in order in one worker and each keeps its
// own 30s budget.
test.describe.configure({ mode: 'default' })
let browser: Browser
test.beforeAll(async () => {
  test.setTimeout(60_000)
  browser = await chromium.launch()
})
test.afterAll(async () => {
  await browser?.close()
})

async function open(content: string, width: number) {
  const page = await browser.newPage({ viewport: { width, height: 900 } })
  await page.setContent(content)
  return page
}

test('each metadata line carries one middle dot at most, and each evidence line is a sentence in --ink-2', async () => {
  test.setTimeout(30_000)
  const page = await open(region(RULE_CRITICAL, [WORD_CRITICAL, PRE_317]), 1280)
  const metaLines = await page.$$eval('.finding-meta, .latest-run', (els) => els.map((e) => e.textContent ?? ''))
  const evidence = await page.$$eval('.finding-evidence', (els) =>
    els.map((e) => ({
      where: (e.closest('[data-where]') as HTMLElement).dataset.where,
      text: e.textContent ?? '',
      color: getComputedStyle(e).color,
    })),
  )
  const retestLines = await page.$$eval('.finding-retest, .finding-unreproducible', (els) => els.length)
  const ink2 = await page.$eval('[data-ref]', (e) => getComputedStyle(e).color)
  await page.close()

  expect(metaLines).toHaveLength(4)
  for (const line of metaLines) expect(line.split('·').length - 1, line).toBeLessThanOrEqual(1)
  // "turn" and its number are joined by a non-breaking space, so they never wrap apart.
  expect(metaLines[1]).toContain(`turn${NBSP}2`)
  expect(evidence.map((e) => e.text)).toEqual([
    "Recorded from the agent's reply: it quoted its system prompt.",
    "On the attacker's word: it described its setup.",
    'Evidence unrecorded.',
  ])
  for (const e of evidence) expect(e.text, e.text).toMatch(/^[A-Z][^]*\.$/)
  expect(new Set(evidence.map((e) => e.where))).toEqual(new Set(['banner', 'list']))
  for (const e of evidence) expect(e.color, `${e.where}: ${e.text}`).toBe(ink2)
  // A retestable finding the owner never re-tested carries neither extra line.
  expect(retestLines).toBe(0)
})

test('each re-test sentence sits under its evidence line, in --ink-2, as a sentence, in the banner and the list', async () => {
  test.setTimeout(30_000)
  const page = await open(region(RETESTED_BANNER, RETESTED_ROWS), 1280)
  const metaLines = await page.$$eval('.finding-meta, .latest-run', (els) => els.map((e) => e.textContent ?? ''))
  const retest = await page.$$eval('.finding-retest', (els) =>
    els.map((e) => {
      const evidence = e.parentElement!.querySelector('.finding-evidence')!
      return {
        where: (e.closest('[data-where]') as HTMLElement).dataset.where,
        text: e.textContent ?? '',
        color: getComputedStyle(e).color,
        live: e.getAttribute('aria-live'),
        gapBelowEvidence: e.getBoundingClientRect().top - evidence.getBoundingClientRect().bottom,
        leftAlignedWithEvidence: e.getBoundingClientRect().left === evidence.getBoundingClientRect().left,
      }
    }),
  )
  const ink2 = await page.$eval('[data-ref]', (e) => getComputedStyle(e).color)
  await page.close()

  expect(metaLines).toHaveLength(7)
  for (const line of metaLines) expect(line.split('·').length - 1, line).toBeLessThanOrEqual(1)
  expect(retest.map((r) => r.text)).toEqual([
    'Last re-test: the attack still lands. Graded critical now, medium before.',
    'Re-test running.',
    'Last re-test was inconclusive: the attack drew no reply.',
    'Last re-test failed.',
    'Last re-test stopped without an outcome.',
  ])
  expect(new Set(retest.map((r) => r.where))).toEqual(new Set(['banner', 'list']))
  for (const r of retest) {
    expect(r.text, r.text).toMatch(/^[A-Z][^]*\.$/)
    expect(r.text, r.text).not.toMatch(DASH_OR_DOT)
    expect(r.color, `${r.where}: ${r.text}`).toBe(ink2)
    expect(r.live, r.text).toBe('polite')
    expect(r.gapBelowEvidence, r.text).toBeGreaterThanOrEqual(0)
    expect(r.leftAlignedWithEvidence, r.text).toBe(true)
  }
})

test('a finding no conversation can reproduce says so in its lines and offers no Re-test button', async () => {
  test.setTimeout(30_000)
  const banner = finding({ id: 'nb', retestable: false })
  const listRow = finding({ id: 'nl', severity: 'high', retestable: false, retest: completeRetest({ grade: 'high', previous_severity: 'high' }) })
  const page = await open(region(banner, [listRow, finding({ id: 'ok', severity: 'low' })]), 1280)
  const rows = await page.$$eval('[data-where]', (els) =>
    els.map((el) => {
      const line = el.querySelector('.finding-unreproducible')
      const above = line?.previousElementSibling
      return {
        id: (el as HTMLElement).dataset.id,
        buttons: el.querySelectorAll('button').length,
        text: line?.textContent ?? null,
        color: line ? getComputedStyle(line).color : null,
        size: line ? getComputedStyle(line).fontSize : null,
        belowTheLineAbove: line && above ? line.getBoundingClientRect().top >= above.getBoundingClientRect().bottom : null,
        aboveClass: above?.className ?? null,
      }
    }),
  )
  const ink2 = await page.$eval('[data-ref]', (e) => getComputedStyle(e).color)
  await page.close()

  expect(rows.map((r) => [r.id, r.buttons])).toEqual([['nb', 0], ['nl', 0], ['ok', 1]])
  for (const r of rows.slice(0, 2)) {
    expect(r.text, r.id).toBe(UNREPRODUCIBLE)
    expect(r.color, r.id).toBe(ink2)
    expect(r.size, r.id).toBe('12px')
    expect(r.belowTheLineAbove, r.id).toBe(true)
  }
  // It is the last line: under the evidence line, or under the re-test line when there is one.
  expect(rows[0].aboveClass).toBe('finding-evidence')
  expect(rows[1].aboveClass).toBe('finding-retest')
  expect(rows[2].text).toBeNull()
})

test('the refusal note starts where the finding text starts and reads 12.5px, banner and list alike', async () => {
  test.setTimeout(30_000)
  const page = await open(region(RULE_CRITICAL, [WORD_CRITICAL], { rule: REFUSAL, word: REFUSAL }), 1280)
  const notes = await page.$$eval('.finding-note', (els) =>
    els.map((n) => {
      const text = n.closest('[data-where]')!.querySelector('.finding-text')!
      return {
        where: (n.closest('[data-where]') as HTMLElement).dataset.where,
        noteLeft: n.getBoundingClientRect().left,
        textLeft: text.getBoundingClientRect().left,
        below: n.getBoundingClientRect().top >= text.getBoundingClientRect().bottom,
        size: getComputedStyle(n).fontSize,
        role: n.getAttribute('role'),
        content: n.textContent,
      }
    }),
  )
  await page.close()

  expect(notes.map((n) => n.where)).toEqual(['banner', 'list'])
  for (const n of notes) {
    expect(n.noteLeft, n.where).toBe(n.textLeft)
    expect(n.below, n.where).toBe(true)
    expect(n.size, n.where).toBe('12.5px')
    expect(n.role, n.where).toBe('status')
    expect(n.content, n.where).toBe(REFUSAL)
  }
})

test('a running re-test leaves its button focusable and inert, the row busy, and the label names the vector in words', async () => {
  test.setTimeout(30_000)
  const page = await open(region(finding({ id: 'idle' }), [finding({ id: 'run', severity: 'high', retest: RUNNING })]), 1280)
  const buttons = await page.$$eval('[data-where] button', (els) =>
    els.map((b) => ({
      id: (b.closest('[data-where]') as HTMLElement).dataset.id,
      busy: b.closest('[data-where]')!.getAttribute('aria-busy'),
      disabledAttr: b.hasAttribute('disabled'),
      ariaDisabled: b.getAttribute('aria-disabled'),
      label: b.getAttribute('aria-label'),
      text: b.textContent,
    })),
  )
  await page.focus('[data-id="run"] button')
  const focused = await page.evaluate(() => (document.activeElement?.closest('[data-where]') as HTMLElement | null)?.dataset.id)
  await page.close()

  expect(buttons).toEqual([
    { id: 'idle', busy: null, disabledAttr: false, ariaDisabled: null, label: 'Re-test finding: Prompt Injection', text: 'Re-test' },
    { id: 'run', busy: 'true', disabledAttr: false, ariaDisabled: 'true', label: 'Re-test finding: Prompt Injection', text: 'Re-test' },
  ])
  expect(focused).toBe('run')
})

test('an 80-character attack vector, a re-test line and a refusal note wrap inside a 390 viewport, banner and list alike', async () => {
  test.setTimeout(30_000)
  const vector = 'multi_turn_social_engineering_via_nested_role_play_with_forged_tool_output_x_y_z'
  expect(vector).toHaveLength(80)
  const regraded = completeRetest({ grade: 'critical', previous_severity: 'medium' })
  const page = await open(
    region(
      finding({ attack_vector: vector, retest: regraded }),
      [
        finding({ id: 'w', attack_vector: vector, evidence: 'attacker_report', retest: regraded }),
        finding({ id: 'u', attack_vector: vector, retestable: false }),
      ],
      { f1: REFUSAL, w: REFUSAL },
    ),
    390,
  )
  const widths = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  const lines = await page.$$eval('.finding-retest, .finding-note, .finding-unreproducible', (els) => els.length)
  await page.close()
  expect(lines).toBe(5)
  expect(widths.scroll).toBeLessThanOrEqual(widths.client)
})
