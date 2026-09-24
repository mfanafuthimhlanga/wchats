import { chromium, expect, test } from '@playwright/test'
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { analyse, groundClaim } from '../app/agents/[id]/eval/[runId]/claims/reading'

// claims-bench.spec.ts opens the claims bench (apps/api/tests/evals/calibration/page/
// claims_template.html) in a real browser on the real widget-claim fixture and on the
// gate-rule fixture, and reads which passages the page lights, each sentence's tint and
// each card's reason. The bench and the console page carry the gate's rules in two
// languages; this is the gate that keeps the bench's copy equal to the console's.

const TEMPLATE = resolve(__dirname, '../../api/tests/evals/calibration/page/claims_template.html')

function buildBench(fixture: { question: string; response: string; retrieved_contexts: string[]; claims: { position: number; statement: string }[] }): string {
  const html = readFileSync(TEMPLATE, 'utf-8')
  const slot = (h: string, name: string, value: unknown) => {
    if (h.split(name).length !== 2) throw new Error(`template needs exactly one ${name}`)
    return h.replace(name, JSON.stringify(value).replace(/<\//g, '<\\/'))
  }
  const scenarios = [{ scenario_id: 'fx', question: fixture.question, response: fixture.response, retrieved_contexts: fixture.retrieved_contexts, claims: fixture.claims }]
  const out = slot(slot(html, '__RUN__', 'fixture-run'), '__SCENARIOS__', scenarios)
  const dir = mkdtempSync(join(tmpdir(), 'claims-bench-'))
  const file = join(dir, 'bench.html')
  writeFileSync(file, out)
  return file
}

test('the bench lights the Deploy passage for the widget claim and gives the gate reason the console gives', async () => {
  // a cold Chromium launch plus the bench's font request runs past the unit config's 10s on this box (#303)
  test.setTimeout(30_000)
  const fx = JSON.parse(readFileSync(join(__dirname, 'fixtures-widget-claim.json'), 'utf-8'))
  const file = buildBench(fx)
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('file:///' + file.replace(/\\/g, '/'))
  await page.waitForSelector('.card.active')
  const lit = await page.locator('.pw.lit').innerText()
  const says = await page.locator('.card.active .overlap').innerText()
  await browser.close()
  expect(errors).toEqual([])
  expect(lit).toContain(fx.expect_passage_contains)
  expect(says).toMatch(/^passage \d+ carries \d+% of its words/)
  expect(says).toBe(groundClaim(fx.claims[0].statement, analyse(fx.response, fx.retrieved_contexts)).reason)
})

// The nine gate-rule sentences of fixtures-gate-rules.json through the built bench: the edge
// tint and the card reason per sentence, read from the DOM, against the table claims-reading.spec.ts
// holds the console aid to, and against the console module run on the same fixture. Then the
// spanned sentence, selected, lights both of its passages.
test('the bench gives every gate-rule sentence the tint and reason the gate and the console give it', async () => {
  test.setTimeout(30_000)
  const fx = JSON.parse(readFileSync(join(__dirname, 'fixtures-gate-rules.json'), 'utf-8'))
  const want: { rule: string; tint: string; reason: string; lit: number[] }[] = fx.expect
  const file = buildBench(fx)
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('file:///' + file.replace(/\\/g, '/'))
  await page.waitForSelector('.card.active')
  const tints = await page.$$eval('.response .sent', (els) =>
    els.map((e) => (e.className.match(/\bt-(\w+)/) ?? ['', ''])[1]),
  )
  const cards = await page.$$eval('.card[data-i] .overlap', (els) => els.map((e) => [e.textContent ?? '', e.className]))
  const spanned = want.findIndex((c) => c.rule === 'two passages')
  await page.click(`.sent[data-s="${spanned}"]`)
  const litOnSentence = await page.$$eval('.pw.lit', (els) => els.map((e) => Number((e as HTMLElement).dataset.i)))
  await page.click(`.card[data-i="${spanned}"] .statement`)
  const litOnClaim = await page.$$eval('.pw.lit', (els) => els.map((e) => Number((e as HTMLElement).dataset.i)))
  await browser.close()

  expect(errors).toEqual([])
  const console_ = analyse(fx.response, fx.retrieved_contexts)
  want.forEach((c, i) => {
    expect({ rule: c.rule, tint: tints[i], reason: cards[i][0] }).toEqual({ rule: c.rule, tint: c.tint, reason: c.reason })
    expect(cards[i][1]).toBe(`overlap k-${c.tint}`)
    // parity: the console aid on the same fixture says the same
    expect({ rule: c.rule, tint: console_.units[i].tint, reason: console_.units[i].reason }).toEqual({ rule: c.rule, tint: c.tint, reason: c.reason })
  })
  expect(tints).toHaveLength(want.length)
  expect(litOnSentence.sort()).toEqual([...want[spanned].lit].sort())
  expect(litOnClaim.sort()).toEqual([...want[spanned].lit].sort())
})

// With no retrieved text the gate grounds a decline and flags every other sentence; the bench
// shows the same, and on a local file its save line says answers stay in the tab.
test('the bench with no retrieved text tints a decline bone and every other sentence red, as the gate does', async () => {
  test.setTimeout(30_000)
  const fx = JSON.parse(readFileSync(join(__dirname, 'fixtures-gate-rules.json'), 'utf-8')).no_passages
  const want: { rule: string; tint: string; reason: string }[] = fx.expect
  const file = buildBench(fx)
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('file:///' + file.replace(/\\/g, '/'))
  await page.waitForSelector('.card.active')
  const tints = await page.$$eval('.response .sent', (els) => els.map((e) => (e.className.match(/\bt-(\w+)/) ?? ['', ''])[1]))
  const cards = await page.$$eval('.card[data-i] .overlap', (els) => els.map((e) => [e.textContent ?? '', e.className]))
  await page.waitForFunction(() => document.getElementById('save')?.textContent !== 'connecting')
  const save = await page.locator('#save').innerText()
  await browser.close()

  expect(errors).toEqual([])
  const console_ = analyse(fx.response, fx.retrieved_contexts)
  expect(tints).toHaveLength(want.length)
  want.forEach((c, i) => {
    expect({ rule: c.rule, tint: tints[i], reason: cards[i][0], cls: cards[i][1] }).toEqual({ rule: c.rule, tint: c.tint, reason: c.reason, cls: `overlap k-${c.tint}` })
    expect({ rule: c.rule, tint: console_.units[i].tint, reason: console_.units[i].reason }).toEqual({ rule: c.rule, tint: c.tint, reason: c.reason })
  })
  expect(save).toBe('local file, answers stay in this tab')
})
