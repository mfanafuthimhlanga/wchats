// reading.ts is the reading aid on the review page: the response split into
// sentences, the retrieved text split into passages, and word overlap between
// the two. It tints an edge and points at a passage. It never labels; the
// Tenant does. Ported from the labelling page's template.html, the v1.

export type UnitKind = 'p' | 'li' | 'cont' | 'code' | 'cite'

export interface Unit {
  kind: UnitKind
  text: string
  /** A blank line came before this unit. */
  para: boolean
  /** The list marker the line carried, "-" or "1.", or "". */
  marker: string
}

export interface Passage {
  text: string
}

export interface Match {
  /** Index into the passages, or -1 when nothing shares a word. */
  passage: number
  shared: Set<string>
  /** Shared tokens over the unit's tokens, 0 to 1. */
  score: number
}

/** bone: carried. grey: partly. fail: no shared words. none: nothing to score. */
export type Tint = 'none' | 'bone' | 'grey' | 'fail'

export interface ReadUnit extends Unit {
  tokens: Set<string>
  match: Match
  tint: Tint
}

export interface Reading {
  units: ReadUnit[]
  passages: Passage[]
}

const STOP = new Set(
  (
    'the and for that with this from are was were has have had not but all any can you your they them ' +
    'their there then than when what which who whom how why into onto over under out off per via about after before ' +
    'between during each every some such only also more most other same both been being does did doing done will would ' +
    'should could may might must shall here where while because since though although upon among along around these ' +
    'those very much many just like within without across toward towards doesn didn aren wasn weren hasn haven'
  ).split(' '),
)

const WORD_RE = () => /[A-Za-z0-9][A-Za-z0-9._/-]*/g
const SENT_RE = () => /(?<=[.!?]["')\]]?)\s+(?=[*`"'([A-Z0-9])/g
const NO_MATCH: Match = { passage: -1, shared: new Set(), score: 0 }

/** "deployed", "deploys" and "deploy" are one word to the overlap. Suffixes only, never below four letters. */
export function stem(t: string): string {
  // no 'es' rule: it would cut 'arrives' to 'arriv' while 'arrive' stays whole
  for (const suf of ['ies', 'ing', 'ed', 's']) {
    if (t.endsWith(suf) && t.length - suf.length >= 4) return suf === 'ies' ? t.slice(0, -3) + 'y' : t.slice(0, -suf.length)
  }
  return t
}

/** A word normalised for overlap, or "" when it carries no weight. */
export function normWord(raw: string): string {
  const t = raw.toLowerCase().replace(/[._/-]+$/, '')
  if (!t) return ''
  if (/^[0-9]/.test(t)) return t // numbers and versions carry weight at any length
  if (/[._/-]/.test(t)) return t // a command, path or dotted identifier
  if (t.length < 4 || STOP.has(t)) return ''
  return stem(t)
}

export function tokensOf(text: string): Set<string> {
  const set = new Set<string>()
  const re = WORD_RE()
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    const t = normWord(m[0])
    if (t) set.add(t)
  }
  return set
}

export function splitSentences(text: string): string[] {
  const re = SENT_RE()
  const parts: string[] = []
  let m: RegExpExecArray | null
  let start = 0
  while ((m = re.exec(text))) {
    parts.push(text.slice(start, m.index))
    start = m.index + m[0].length
  }
  parts.push(text.slice(start))
  const out: string[] = []
  for (const p of parts) {
    const s = p.trim()
    if (!s) continue
    // a fragment this short cannot be read alone, so it rides with its neighbour
    if (out.length && (s.length < 28 || out[out.length - 1].length < 28)) out[out.length - 1] += ' ' + s
    else out.push(s)
  }
  return out
}

/** The response as sentences, bullets, code fences and a trailing CITATIONS block. */
export function responseUnits(response: string): Unit[] {
  const text = String(response ?? '')
  const cut = text.search(/(^|\n)\s*CITATIONS\s*:/)
  const body = cut === -1 ? text : text.slice(0, cut)
  const cites = cut === -1 ? '' : text.slice(cut).trim()
  const units: Unit[] = []
  let fence: string[] | null = null
  let para = false
  for (const line of body.split('\n')) {
    if (/^\s*```/.test(line)) {
      if (fence) {
        units.push({ kind: 'code', text: fence.join('\n'), para: true, marker: '' })
        fence = null
      } else fence = []
      continue
    }
    if (fence) {
      fence.push(line)
      continue
    }
    if (!line.trim()) {
      para = true
      continue
    }
    const m = line.match(/^\s*([-*•]|\d+[.)])\s+/)
    const rest = m ? line.slice(m[0].length) : line.trim()
    splitSentences(rest).forEach((s, i) =>
      units.push({
        kind: m ? (i === 0 ? 'li' : 'cont') : 'p',
        text: s,
        para: para && i === 0,
        marker: m && i === 0 ? m[1] : '',
      }),
    )
    para = false
  }
  if (fence) units.push({ kind: 'code', text: fence.join('\n'), para: true, marker: '' })
  if (cites) units.push({ kind: 'cite', text: cites, para: true, marker: '' })
  return units
}

const PASSAGE_MIN = 300

/** Each retrieved chunk cut at sentence ends into passages of about 300 characters or more. */
export function passagesOf(contexts: readonly string[]): Passage[] {
  const passages: Passage[] = []
  contexts.forEach((raw) => {
    const b = String(raw ?? '').replace(/\r/g, '').trim()
    if (!b) return
    const re = SENT_RE()
    const cuts: number[] = []
    let m: RegExpExecArray | null
    while ((m = re.exec(b))) cuts.push(m.index + m[0].length)
    cuts.push(b.length)
    let start = 0
    const take = (end: number) => {
      const text = b.slice(start, end).replace(/\s+$/, '')
      if (text.trim()) passages.push({ text })
      start = end
    }
    for (const c of cuts) if (c - start >= PASSAGE_MIN) take(c)
    if (start < b.length) take(b.length)
  })
  return passages
}

/**
 * The passage sharing the most of these tokens. A tie goes to the denser passage,
 * the one where the shared words are a larger share of its own, so a long
 * overview that happens to mention two of the words loses to the short passage
 * that is about them.
 */
export function bestPassage(tokens: Set<string>, passageTokens: readonly Set<string>[]): Match {
  let best: Match = NO_MATCH
  let bestDensity = 0
  if (!tokens.size) return best
  passageTokens.forEach((pt, i) => {
    const shared = new Set<string>()
    for (const t of tokens) if (pt.has(t)) shared.add(t)
    const score = shared.size / tokens.size
    const density = pt.size ? shared.size / pt.size : 0
    if (score > best.score || (score === best.score && score > 0 && density > bestDensity)) {
      best = { passage: i, shared, score }
      bestDensity = density
    }
  })
  return best
}

/** Red is reserved for a sentence with words that no passage shares. With no passages at all there is nothing to say. */
export function tintOf(tokenCount: number, score: number, passageCount: number): Tint {
  if (!tokenCount || !passageCount) return 'none'
  if (score >= 0.4) return 'bone'
  if (score > 0) return 'grey'
  return 'fail'
}

// a source marker names a document rather than asserting anything, so it stays out of the score
const scoreText = (text: string) => text.replace(/\*\(([^()]*)\)\*/g, ' ').replace(/[*`]/g, ' ')

export function analyse(response: string, contexts: readonly string[]): Reading {
  const passages = passagesOf(contexts)
  const passageTokens = passages.map((p) => tokensOf(p.text))
  const units = responseUnits(response).map((u): ReadUnit => {
    const tokens = u.kind === 'cite' ? new Set<string>() : tokensOf(scoreText(u.text))
    const match = bestPassage(tokens, passageTokens)
    return { ...u, tokens, match, tint: tintOf(tokens.size, match.score, passages.length) }
  })
  return { units, passages }
}

/** The response sentence that carries a claim: the one sharing most of the claim's words, or -1. */
export function carryingUnit(claim: string, units: readonly ReadUnit[]): number {
  const ct = tokensOf(claim)
  if (!ct.size) return -1
  let best = -1
  let bestScore = 0
  units.forEach((u, i) => {
    if (u.kind === 'cite' || u.kind === 'code') return
    let shared = 0
    for (const t of ct) if (u.tokens.has(t)) shared++
    const score = shared / ct.size
    if (score > bestScore) {
      bestScore = score
      best = i
    }
  })
  return best
}

export interface Segment {
  text: string
  /** The word is one the lit passage and the sentence share. */
  hit: boolean
}

/** Text cut into runs, the shared words marked, for rendering without innerHTML. */
export function segments(text: string, shared: ReadonlySet<string>): Segment[] {
  const out: Segment[] = []
  const re = WORD_RE()
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    const t = normWord(m[0])
    if (!t || !shared.has(t)) continue
    if (m.index > last) out.push({ text: text.slice(last, m.index), hit: false })
    out.push({ text: m[0], hit: true })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), hit: false })
  return out
}

export interface InlinePart {
  text: string
  code: boolean
}

/** Backtick spans as code, bold markers dropped: markdown the Tenant should read, not decode. */
export function inlineParts(text: string): InlinePart[] {
  const out: InlinePart[] = []
  const re = /`([^`]+)`/g
  let last = 0
  let m: RegExpExecArray | null
  const plain = (s: string) => {
    if (s) out.push({ text: s.replace(/\*\*([^*]+)\*\*/g, '$1'), code: false })
  }
  while ((m = re.exec(text))) {
    plain(text.slice(last, m.index))
    out.push({ text: m[1], code: true })
    last = m.index + m[0].length
  }
  plain(text.slice(last))
  return out
}
