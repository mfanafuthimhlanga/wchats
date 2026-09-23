import { chromium, expect, test } from '@playwright/test'
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// claims-bench.spec.ts opens the claims bench (apps/api/tests/evals/calibration/page/
// claims_template.html) in a real browser on the real widget-claim fixture and reads
// which passage the page lights for the active claim. The bench and the console page
// carry the same overlap rule in two languages; this is the gate that keeps the bench's
// copy honest, on the case that fooled it on 2026-09-23.

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

test('the bench lights the Deploy passage for the widget claim and says how many words it carries', async () => {
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
  expect(says).toMatch(/carries [1-9]\d* of its \d+ words/)
})
