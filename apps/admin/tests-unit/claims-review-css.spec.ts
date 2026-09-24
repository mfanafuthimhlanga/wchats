import { chromium, expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { PAGE_CSS } from '../app/agents/[id]/eval/[runId]/claims/reviewCss'

// claims-review-css.spec.ts renders the claims review's stylesheet in Chromium with the
// console's own tokens (the :root block of app/globals.css) and one card per tint, then
// reads the computed colour of each card's reason. The tint class decides that colour:
// a rule later in PAGE_CSS with the same specificity once greyed every reason.

const ROOT_TOKENS = (() => {
  const css = readFileSync(join(__dirname, '../app/globals.css'), 'utf-8')
  const m = css.match(/^:root \{[\s\S]*?^\}/m)
  if (!m) throw new Error('globals.css has no :root block')
  return m[0]
})()

const TINTS = { bone: '--live', grey: '--ink-2', fail: '--fail', none: '--ink-2' } as const

test('each card reason takes its tint token colour, and bone, grey and red differ', async () => {
  test.setTimeout(30_000)
  const cards = Object.keys(TINTS)
    .map((t) => `<div class="card"><p class="voice statement">a claim</p><p class="overlap mono k-${t}" data-t="${t}">a reason</p></div>`)
    .join('')
  const refs = Object.entries(TINTS)
    .map(([t, token]) => `<span data-ref="${t}" style="color: var(${token})">ref</span>`)
    .join('')
  const html = `<style>${ROOT_TOKENS}</style><style>${PAGE_CSS}</style><div class="page review"><aside class="claims">${cards}</aside></div>${refs}`
  const browser = await chromium.launch()
  const page = await browser.newPage()
  await page.setContent(html)
  const got = await page.$$eval('.overlap', (els) =>
    Object.fromEntries(els.map((e) => [(e as HTMLElement).dataset.t, getComputedStyle(e).color])),
  )
  const want = await page.$$eval('[data-ref]', (els) =>
    Object.fromEntries(els.map((e) => [(e as HTMLElement).dataset.ref, getComputedStyle(e).color])),
  )
  await browser.close()
  expect(got).toEqual(want)
  expect(new Set([got.bone, got.grey, got.fail]).size).toBe(3)
})
