import { expect, test } from '@playwright/test'
import {
  analyse,
  bestPassage,
  carryingUnit,
  inlineParts,
  passagesOf,
  responseUnits,
  segments,
  splitSentences,
  tintOf,
  tokensOf,
} from '../app/agents/[id]/eval/[runId]/claims/reading'

// claims-reading.spec.ts is the browserless proof for the review page's reading
// aid. The aid only points: it tints a sentence by word overlap with the
// retrieved text and lights the passage sharing the most words. Every ranking
// fixture below carries a decoy that shares SOME words and sits first, so a
// reader that takes the first overlap, or ignores the score, picks the decoy
// (FM-004; the first version of this file had decoys sharing nothing, and two
// such mutations survived it).

test('tokensOf drops stop words and short words, keeps numbers and identifiers', () => {
  const t = tokensOf('The refund takes 14 days via app.config and the API')
  expect([...t].sort()).toEqual(['14', 'app.config', 'days', 'refund', 'takes'])
})

test('splitSentences cuts at sentence ends and rides a short fragment with its neighbour', () => {
  expect(splitSentences('Refunds take fourteen working days. Contact support for a receipt. Ok.')).toEqual([
    'Refunds take fourteen working days.',
    'Contact support for a receipt. Ok.',
  ])
})

test('responseUnits keeps bullets, code fences and a CITATIONS block apart', () => {
  const units = responseUnits(
    'Two steps apply here today.\n\n- Open the settings page first.\n```\nrun this\n```\nCITATIONS: doc-1',
  )
  expect(units.map((u) => u.kind)).toEqual(['p', 'li', 'code', 'cite'])
  expect(units[1].marker).toBe('-')
  expect(units[1].para).toBe(true)
  expect(units[2].text).toBe('run this')
})

test('passagesOf cuts a long chunk at sentence ends and keeps the chunk index', () => {
  const sentence = 'The warranty covers accidental damage for twelve months from purchase. '
  const long = sentence.repeat(10)
  const passages = passagesOf([long, 'Short second chunk.'])
  expect(passages.length).toBeGreaterThan(2)
  expect(passages.every((p) => p.text.endsWith('.'))).toBe(true)
  expect(passages[passages.length - 1]).toEqual({ text: 'Short second chunk.' })
  expect(passagesOf(['', '   '])).toEqual([])
})

test('bestPassage picks the passage sharing most words, not the first with any, and -1 when none share any', () => {
  const tokens = tokensOf('refund arrives within fourteen days')
  // the decoy comes first and shares one word; the right passage shares four
  const passages = [tokensOf('a refund of shipping costs'), tokensOf('a refund arrives within fourteen working days')]
  const m = bestPassage(tokens, passages)
  expect(m.passage).toBe(1)
  expect([...m.shared].sort()).toEqual(['arrives', 'days', 'fourteen', 'refund'])
  // and with the decoy last, so "last with any overlap" loses too
  expect(bestPassage(tokens, [passages[1], passages[0]]).passage).toBe(0)
  expect(bestPassage(tokens, [tokensOf('nothing here')]).passage).toBe(-1)
  expect(bestPassage(new Set(), passages).passage).toBe(-1)
})

test('tintOf reserves red for a sentence with words that no passage shares', () => {
  expect(tintOf(0, 0, 3)).toBe('none')
  expect(tintOf(5, 0, 0)).toBe('none') // no retrieved text at all says nothing about the sentence
  expect(tintOf(5, 0, 3)).toBe('fail')
  expect(tintOf(5, 0.2, 3)).toBe('grey')
  expect(tintOf(5, 0.4, 3)).toBe('bone')
})

test('analyse tints every unit and leaves the citations block unscored', () => {
  const r = analyse(
    'Refunds arrive within fourteen days. Purple elephants dance nightly.\nCITATIONS: doc-2',
    ['Refunds arrive within fourteen working days of the return.'],
  )
  expect(r.units.map((u) => u.tint)).toEqual(['bone', 'fail', 'none'])
  expect(r.units[0].match.passage).toBe(0)
})

test('carryingUnit finds the response sentence sharing most of the claim, not the first sharing any', () => {
  // the first sentence shares one word of the claim; the second shares four
  const r = analyse('Refunds on shipping are never free. Refunds arrive within fourteen days.', ['a passage'])
  expect(carryingUnit('Refunds arrive within fourteen days', r.units)).toBe(1)
  expect(carryingUnit('the', r.units)).toBe(-1)
})

test('segments marks only the shared words and keeps every character', () => {
  const s = segments('Refunds arrive in 14 days.', new Set(['refunds', '14']))
  expect(s.map((x) => x.text).join('')).toBe('Refunds arrive in 14 days.')
  expect(s.filter((x) => x.hit).map((x) => x.text)).toEqual(['Refunds', '14'])
})

test('inlineParts lifts backtick spans and drops bold markers', () => {
  expect(inlineParts('Run `pnpm dev` and **wait**.')).toEqual([
    { text: 'Run ', code: false },
    { text: 'pnpm dev', code: true },
    { text: ' and wait.', code: false },
  ])
})
