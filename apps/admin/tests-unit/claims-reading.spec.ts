import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  analyse,
  bestPassage,
  carryingUnit,
  claimFocus,
  groundClaim,
  inlineParts,
  isDecline,
  litPassages,
  missingNumbers,
  passagesOf,
  percent,
  responseUnits,
  scoreText,
  secondReading,
  segments,
  splitSentences,
  stem,
  tintOf,
  tokensOf,
} from '../app/agents/[id]/eval/[runId]/claims/reading'

// claims-reading.spec.ts is the browserless proof for the review page's reading
// aid. The aid only points: it tints a sentence by the faithfulness gate's rules
// and lights the passages the gate read it against. Every ranking
// fixture below carries a decoy that shares SOME words and sits first, so a
// reader that takes the first overlap, or ignores the score, picks the decoy
// (FM-004; the first version of this file had decoys sharing nothing, and two
// such mutations survived it).

test('tokensOf drops stop words and short words, keeps numbers and identifiers', () => {
  const t = tokensOf('The refund takes 14 days via app.config and the API')
  expect([...t].sort()).toEqual(['14', 'app.config', 'days', 'refund', 'take'])
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
  expect([...m.shared].sort()).toEqual(['arrive', 'days', 'fourteen', 'refund'])
  // and with the decoy last, so "last with any overlap" loses too
  expect(bestPassage(tokens, [passages[1], passages[0]]).passage).toBe(0)
  expect(bestPassage(tokens, [tokensOf('nothing here')]).passage).toBe(-1)
  expect(bestPassage(new Set(), passages).passage).toBe(-1)
})

test('tintOf reserves red for a sentence with words that no passage shares', () => {
  expect(tintOf(0, 0)).toBe('none')
  expect(tintOf(5, 0)).toBe('fail')
  expect(tintOf(5, 0.2)).toBe('grey')
  expect(tintOf(5, 0.4)).toBe('bone')
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
  const s = segments('Refunds arrive in 14 days.', new Set(['refund', '14']))
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

// The case that fooled the aid on 2026-09-23, real data from staging run 09941b0f: the
// claim says "deployed", the passage says "Deploy", and a long overview passage shares
// two other words. Without stemming and the density tie-break the overview lights.
test('the widget claim lights the Deploy passage, not the overview that shares two words', () => {
  const fx = JSON.parse(readFileSync(join(__dirname, 'fixtures-widget-claim.json'), 'utf-8'))
  const r = analyse(fx.response, fx.retrieved_contexts)
  const m = bestPassage(tokensOf(fx.claims[0].statement), r.passages.map((p) => tokensOf(p.text)))
  expect(m.passage).toBeGreaterThanOrEqual(0)
  expect(r.passages[m.passage].text).toContain(fx.expect_passage_contains)
})

test('stem folds inflections and never cuts below four letters', () => {
  expect(['deployed', 'deploys', 'deploying', 'deploy'].map(stem)).toEqual(['deploy', 'deploy', 'deploy', 'deploy'])
  expect(stem('policies')).toBe('policy')
  expect(stem('arrives')).toBe('arrive')
  expect(stem('fees')).toBe('fees') // "fee" would be three letters
})

// ── the gate's rules (grounding.py, grounding-v3), ported so the aid lights what the gate scored ──
// fixtures-gate-rules.json holds ten sentences over three passages and two over none, each
// decided by one rule, and the table of what the gate says of each: ground() produced every
// tint and reason in it, and test_claims_benchmark.py checks it against ground() still.
// claims-bench.spec.ts reads the same table, so the console aid and the bench cannot drift
// from the gate or from each other.

interface GateCase {
  rule: string
  sentence: string
  tint: string
  reason: string
  lit: number[]
}
interface GateScenario {
  response: string
  retrieved_contexts: string[]
  expect: GateCase[]
}
const GATE = JSON.parse(readFileSync(join(__dirname, 'fixtures-gate-rules.json'), 'utf-8')) as GateScenario & {
  no_passages: GateScenario
}
const gateReading = () => analyse(GATE.response, GATE.retrieved_contexts)
const gateUnit = (rule: string) => {
  const i = GATE.expect.findIndex((c) => c.rule === rule)
  expect(i).toBeGreaterThanOrEqual(0)
  return gateReading().units[i]
}

for (const [name, sc] of [['with passages', GATE], ['with no retrieved text', GATE.no_passages]] as const) {
  test(`every fixture sentence ${name} takes the tint, the reason and the lit passages the gate gives it`, () => {
    const r = analyse(sc.response, sc.retrieved_contexts)
    expect(r.units.map((u) => u.text)).toEqual(sc.expect.map((c) => c.sentence))
    for (const [i, c] of sc.expect.entries()) {
      const u = r.units[i]
      expect({ rule: c.rule, tint: u.tint, reason: u.reason }).toEqual({ rule: c.rule, tint: c.tint, reason: c.reason })
      expect(litPassages(u, u.tokens, r.passageTokens).map((l) => l.passage)).toEqual(c.lit)
    }
  })
}

test('with no retrieved text a decline is bone, every other sentence red, and a code fence or citation block unscored', () => {
  const r = analyse(GATE.no_passages.response + '\n```\nrun this\n```\nCITATIONS: doc-1', [])
  expect(r.passages).toEqual([])
  expect(r.units.map((u) => u.tint)).toEqual(['bone', 'fail', 'none', 'none'])
  expect(r.units[1].reason).toBe('no passage shares a word with it')
})

test('the decline and number patterns read Unicode as Python does', () => {
  // Python's \w takes "café" as one word and \d takes the Arabic-Indic three; ASCII ports missed both
  expect(isDecline('The café documentation does not specify the opening hours.')).toBe(true)
  expect(missingNumbers('React Router handles the catalog and the cart in ٣ steps.', new Set(['3']))).toEqual(['٣'])
})

// The console once lit the passages of the carrying sentence while the card's reason came from the
// claim's own grounding. A paraphrase separates the two: its carrying sentence reads two passages,
// the claim itself reads one.
test('an active claim lights the passages of its own grounding, not those of its carrying sentence', () => {
  const r = gateReading()
  const paraphrase = 'No database is written by the app and nothing reaches a server.'
  const f = claimFocus(paraphrase, r)
  const g = groundClaim(paraphrase, r)
  expect(f.grounding.reason).toBe(g.reason)
  expect(f.lit.map((l) => l.passage)).toEqual(litPassages(g, g.tokens, r.passageTokens).map((l) => l.passage))
  expect(f.unit).toBe(carryingUnit(paraphrase, r.units))
  const carrier = r.units[f.unit]
  expect(litPassages(carrier, carrier.tokens, r.passageTokens).map((l) => l.passage)).not.toEqual(f.lit.map((l) => l.passage))
})

test('a sentence the best passage carries whose figure no passage states is red, and the reason names the number', () => {
  const unit = gateUnit('number')
  expect(unit.match.score).toBeGreaterThanOrEqual(0.4) // the words alone would tint it bone
  expect(unit.missing).toEqual(['4173'])
  expect(unit.tint).toBe('fail')
  expect(unit.reason.endsWith('number 4173 appears in no passage')).toBe(true)
})

test('a whole-sentence decline about the documents is bone with the decline reason', () => {
  const unit = gateUnit('decline')
  expect(unit.match.score).toBeLessThan(0.4) // the words alone would tint it grey
  expect(unit.decline).toBe(true)
  expect(unit.tint).toBe('bone')
  expect(unit.reason).toBe('a decline asserts nothing the documents would carry')
})

test('a sentence two passages carry together is bone, both lit, and the reason names both', () => {
  const unit = gateUnit('two passages')
  expect(unit.match.score).toBeLessThan(0.4)
  expect(unit.spannedWith).toBeGreaterThanOrEqual(0)
  expect(unit.spannedWith).not.toBe(unit.match.passage)
  expect(unit.tint).toBe('bone')
  expect(unit.reason).toBe(`passages ${unit.match.passage + 1} and ${unit.spannedWith + 1} together carry 62% of its words`)
  const lit = litPassages(unit, unit.tokens, gateReading().passageTokens)
  expect(lit.map((l) => l.passage)).toEqual([unit.match.passage, unit.spannedWith])
  expect([...lit[1].shared].sort()).toEqual(['catalog', 'cart', 'react', 'router'].sort())
})

test('a reason or a consequence gets no second reading, though two passages would carry its words', () => {
  const unit = gateUnit('connective')
  const r = gateReading()
  // the guard decides it: the second reading alone would ground it
  expect(secondReading(unit.tokens, unit.match.passage, unit.match.score, r.passageTokens).second).toBeGreaterThanOrEqual(0)
  expect(unit.spannedWith).toBe(-1)
  expect(unit.supported).toBe(false)
  expect(unit.tint).toBe('grey')
})

test('a second passage adding one word is not read with the best', () => {
  const unit = gateUnit('one word added')
  const r = gateReading()
  const best = r.passageTokens[unit.match.passage]
  const added = r.passageTokens.map((pt, i) =>
    i === unit.match.passage ? 0 : [...unit.tokens].filter((t) => pt.has(t) && !best.has(t)).length,
  )
  expect(Math.max(...added)).toBe(1)
  // one more word would carry it: the minimum of two decides it
  expect((unit.match.shared.size + 1) / unit.tokens.size).toBeGreaterThanOrEqual(0.4)
  expect(unit.spannedWith).toBe(-1)
  expect(unit.tint).toBe('grey')
})

test('the widget fixture still lights the Deploy passage, from the claim card and from its sentence', () => {
  const fx = JSON.parse(readFileSync(join(__dirname, 'fixtures-widget-claim.json'), 'utf-8'))
  const r = analyse(fx.response, fx.retrieved_contexts)
  const g = groundClaim(fx.claims[0].statement, r)
  const lit = litPassages(g, g.tokens, r.passageTokens)
  expect(lit.length).toBeGreaterThan(0)
  expect(r.passages[lit[0].passage].text).toContain(fx.expect_passage_contains)
  const u = r.units[carryingUnit(fx.claims[0].statement, r.units)]
  expect(r.passages[u.match.passage].text).toContain(fx.expect_passage_contains)
})

// TestTwoPassages in apps/api/tests/unit/test_grounding.py, sentence for sentence
test('the two-passage cases of the Python suite hold in the aid', () => {
  const A = 'The storefront runs React Router for the catalog, the product pages and the cart.'
  const B = 'Orders are never sent to a server; the app reads and writes no database at all.'
  const C = 'The Fastify server broadcasts every event over a WebSocket to the dashboard.'
  const one = (sentence: string, contexts: string[]) => {
    const units = analyse(sentence, contexts).units
    expect(units).toHaveLength(1)
    return units[0]
  }
  const joined =
    'React Router handles the catalog and the cart, the audit log keeps every step, nothing is sent to a server, and the app writes no database.'
  const s = one(joined, [A, B, C])
  expect(s.supported).toBe(true)
  expect(s.spannedWith).toBeGreaterThanOrEqual(0)
  expect(s.spannedWith).not.toBe(s.match.passage)
  expect(s.reason.startsWith(`passages ${s.match.passage + 1} and ${s.spannedWith + 1} together carry`)).toBe(true)

  const single = one('React Router handles the catalog, the product pages and the cart.', [A, B])
  expect([single.supported, single.spannedWith, single.reason.includes('together')]).toEqual([true, -1, false])

  expect(one('The router, the server and the dashboard were rewritten in Rust last quarter.', [A, B, C]).supported).toBe(false)

  const withNumber = one('React Router handles the catalog and the cart, and 42 orders are sent to a server.', [A, B])
  expect([withNumber.supported, withNumber.missing]).toEqual([false, ['42']])

  const because = one(joined.replace('cart, the audit', 'cart because the audit'), [A, B])
  expect([because.supported, because.spannedWith]).toEqual([false, -1])

  expect(one('React Router handles the catalog, the cart and the server that lists them.', [A, C]).spannedWith).toBe(-1)

  const thin =
    'React Router handles the catalog, the audit log keeps every step and the ledger, the dashboard shows the map, nothing is sent to a server.'
  const u = one(thin, [A, B])
  expect([u.spannedWith, u.supported, u.carried]).toEqual([-1, false, one(thin, [A]).carried])
})

// TestTheRule's decline and number cases in test_grounding.py
test('isDecline takes a decline about the documents and refuses a claim about the system or a second clause', () => {
  for (const s of [
    'The corpus does not specify the preview port.',
    'However, the documentation does not establish that every feature works offline.',
    "I don't have more specific post-launch information in my knowledge base.",
    // a list comma, not a clause comma (#319)
    'The corpus does not state who approves an answer, how provenance is stored, or how cache entries are invalidated.',
    'The documentation does not include a measured performance, bundle-size, or maintenance comparison.',
    'The corpus does not document the build, the tests, or the deployment steps.',
    'The documentation does not name React, Vue, or Svelte as options.',
    'The corpus does not document the build, the tests, or the deployment steps for staging.',
    'The corpus does not document the build, or the deployment process for staging.',
    'The corpus does not name the fixtures, or the tests themselves in detail.',
    'The documentation does not name React, Vue, or Svelte plugins for this.',
    'The corpus does not give the timeout, or the 3 retries per minute.',
    'The documentation does not describe the owner, or the teams that are on call.',
    'The documentation does not describe the port, or the host which is used in staging.',
    'The documentation does not describe the queue, or the files it writes to.',
    'The documentation does not describe the owner, or the keys the service requires for signing.',
    'The documentation does not describe the port, or the settings Fastify expects in production.',
    'The documentation does not describe the hosts, or the ports each service listens on.',
    'The documentation does not describe the refunds, or the webhooks Stripe sends on failure.',
  ])
    expect(isDecline(s), s).toBe(true)
  for (const s of [
    'The server listens on port 8080; the corpus does not specify the preview port.',
    'The corpus does not specify a web test command; it documents Playwright for the web tests.',
    'The deploy workflow does not roll the control database back when a tenant migration fails.',
    'The repository does not use Memgraph for the server tests.',
    'That means the repository cannot provide a reliable monthly order count.',
    // a clause after the comma (#319)
    'The documentation does not specify the port, and the Fastify server listens on 8080.',
    'The documentation does not establish that all agent functionality works without network access, and the normal configuration still expects Anthropic credentials.',
    'The corpus does not specify a retry count, or the tests would say so.',
    'The corpus does not specify the port, and Fastify listens on 8080.',
  ])
    expect(isDecline(s), s).toBe(false)
})

test('a thousands separator is one number and a short list is two', () => {
  expect(missingNumbers('The bundle limit is 20,480 bytes for the widget.', new Set(['20480']))).toEqual([])
  expect(missingNumbers('The cap is 1,000,000 rows for the widget.', new Set(['1000000']))).toEqual([])
  expect(missingNumbers('Follow steps 3,4 for the widget refund.', new Set(['3', '4']))).toEqual([])
  expect(missingNumbers('Refunds arrive within 9 working days.', new Set(['14']))).toEqual(['9'])
})

test('percent rounds a half to even, as Python formats it', () => {
  // format(x, ".0%") in Python 3 gives each of these
  expect([0.125, 0.375, 0.625, 1 / 3, 0.285, 0.4].map(percent)).toEqual(['12%', '38%', '62%', '33%', '28%', '40%'])
})

test('a source marker stays out of the score, a code fence is never scored, and a curly-quoted end splits', () => {
  expect(scoreText('Deploy is the last step. *(W Chats `README.md`, "Stack")*')).not.toContain('README')
  const r = analyse('Refunds arrive within fourteen days.\n```\nrefund arrive\n```', ['Refunds arrive within fourteen working days of the return.'])
  expect(r.units.map((u) => u.tint)).toEqual(['bone', 'none'])
  expect(splitSentences('The portfolio says “Go was chosen.” It does show a constraint here today.')).toHaveLength(2)
})

test('tintOf with the gate decision: bone when grounded, red on a missing number or no shared word, grey under the floor', () => {
  expect(tintOf(5, 0.1, { supported: true, missing: [] })).toBe('bone')
  expect(tintOf(5, 0.8, { supported: false, missing: ['9'] })).toBe('fail')
  expect(tintOf(5, 0.2, { supported: false, missing: [] })).toBe('grey')
  expect(tintOf(5, 0, { supported: false, missing: [] })).toBe('fail')
  expect(tintOf(0, 0.9, { supported: false, missing: [] })).toBe('none')
})
