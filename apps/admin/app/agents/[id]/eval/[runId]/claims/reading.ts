// reading.ts is the reading aid on the review page: the response split into
// sentences, the retrieved text split into passages, and each sentence read by
// the faithfulness gate's own rules (apps/api/app/domain/grounding.py,
// grounding-v5): word overlap against the best passage, a second reading
// against two, every number in the retrieved text, a decline grounded, the
// agent's view read for its numbers only. It tints
// an edge, lights the passages the gate read and says why in the gate's words.
// It never labels; the Tenant does. Each rule names its Python twin, so an edit
// to one has a named place to land in the other.

export type UnitKind = 'p' | 'li' | 'cont' | 'code' | 'cite'

export interface Unit {
  kind: UnitKind
  text: string
  /** A blank line came before this unit. */
  para: boolean
  /** The list marker the line carried, "-" or "1.", or "". */
  marker: string
  /** The unit sits in the paragraph VIEW_RE opens: the agent's view (response_units). */
  view: boolean
  /** A line introducing the list under it (is_lead_in): shown, never scored. */
  leadIn?: boolean
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

/** bone: the gate grounds it. grey: some words shared, under the floor. fail: no shared word, or a number no passage has. none: nothing to score. */
export type Tint = 'none' | 'bone' | 'grey' | 'fail'

/** What the gate decides about one sentence: SentenceGrounding in grounding.py. */
export interface Grounding {
  /** The best passage alone, as bestPassage ranks it. */
  match: Match
  /** Every number in the sentence the retrieved text never states, as written, in order. */
  missing: string[]
  decline: boolean
  /** A whole sentence pointing to the contact route (is_referral); grounded like a decline. */
  referral?: boolean
  /** In the agent's view, so only its numbers were read. */
  view: boolean
  /** A view beside a grounded fact it can rest on (_anchor_views). False fails the view. */
  anchored?: boolean
  /** The second passage read with the best one when the best alone fell under the floor, or -1. */
  spannedWith: number
  /** The share of the sentence's words the gate counted: the best passage's, or the two passages' together. */
  carried: number
  supported: boolean
  /** The gate's reason string, word for word, or "" for a unit with nothing to score. */
  reason: string
}

export interface ReadUnit extends Unit, Grounding {
  tokens: Set<string>
  tint: Tint
}

export interface Reading {
  units: ReadUnit[]
  passages: Passage[]
  passageTokens: Set<string>[]
  /** Every number the passages state, keyed by numberKey. */
  numbers: Set<string>
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
// _SENT_RE in grounding.py: a sentence end, an optional closing quote (straight or curly), then a capital, a digit or an opener
const SENT_RE = () => /(?<=[.!?]["')\]”’]?)\s+(?=[*`"'([A-Z0-9“‘])/g
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

/** VIEW_MARKER and _VIEW_RE in grounding.py: the words that open the agent's own view. */
export const VIEW_MARKER = 'My view:'
export const VIEW_RE = /^\s*(?:>\s*)*(?:#{1,6}\s+)?[*_]{0,2}\s*My view\s*[*_]{0,2}\s*:\s*[*_]{0,2}/i

/** is_lead_in in grounding.py: a whole line ending in a colon, with no figure, before a list or fence. */
export function isLeadIn(line: string, after: readonly string[]): boolean {
  const text = line.trim().replace(/[\s*_`]+$/, '')
  if (!text.endsWith(':') || numbersIn(text).length) return false
  if (text.split(SENT_RE()).filter((p) => p.trim()).length !== 1) return false
  const following = after.find((l) => l.trim()) ?? ''
  return /^\s*([-*•]|\d+[.)])\s+/.test(following) || following.trim().startsWith('```')
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
  let view = false
  let pending = false
  let first = true
  const lines = body.split('\n')
  for (const [index, line] of lines.entries()) {
    if (/^\s*```/.test(line)) {
      view = pending = false
      first = true
      if (fence) {
        units.push({ kind: 'code', text: fence.join('\n'), para: true, marker: '', view: false })
        fence = null
      } else fence = []
      continue
    }
    if (fence) {
      fence.push(line)
      continue
    }
    // _RULE_LINE_RE in grounding.py: a --- line ends the paragraph, the view and a handed-on view
    if (!line.trim() || /^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      para = true
      view = false
      if (line.trim()) pending = false
      first = true
      continue
    }
    const m = line.match(/^\s*([-*•]|\d+[.)])\s+/)
    // response_units in grounding.py: the marker opens the view on a paragraph's first line only;
    // a list line ends it, and a bare marker line hands it to the next paragraph
    const opened = first && !m ? line.match(VIEW_RE) : null
    if (m) view = pending = false
    else if (opened) {
      view = true
      if (!line.slice(opened[0].length).trim()) {
        pending = true
        first = true
        continue
      }
    } else if (first && pending) {
      view = true
      pending = false
    }
    first = false
    if (!m && isLeadIn(line, lines.slice(index + 1))) {
      units.push({ kind: 'p', text: line.trim(), para, marker: '', view, leadIn: true })
      para = false
      continue
    }
    const rest = m ? line.slice(m[0].length) : line.trim()
    splitSentences(rest).forEach((s, i) =>
      units.push({
        kind: m ? (i === 0 ? 'li' : 'cont') : 'p',
        text: s,
        para: para && i === 0,
        marker: m && i === 0 ? m[1] : '',
        view,
      }),
    )
    para = false
  }
  if (fence) units.push({ kind: 'code', text: fence.join('\n'), para: true, marker: '', view: false })
  if (cites) units.push({ kind: 'cite', text: cites, para: true, marker: '', view: false })
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
 * that is about them. _best_passage in grounding.py.
 */
export function bestPassage(tokens: ReadonlySet<string>, passageTokens: readonly ReadonlySet<string>[]): Match {
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

/** CARRIED_FLOOR in grounding.py: the share of a sentence's words one passage, or two together, must carry. */
export const CARRIED_FLOOR = 0.4

/**
 * Red is reserved for a sentence with no word any passage shares, or a number
 * no passage states; with no passages at all, every sentence but a decline or a clean view is
 * red, as the gate flags it. A unit with no content word is not scored. Given
 * the gate's decision, bone means the gate grounds the sentence (a decline
 * included); without it, the tint falls back to overlap against the floor.
 */
export function tintOf(
  tokenCount: number,
  score: number,
  gate?: { supported: boolean; missing: readonly string[] },
): Tint {
  if (!tokenCount) return 'none'
  if (gate) {
    if (gate.supported) return 'bone'
    return score > 0 && !gate.missing.length ? 'grey' : 'fail'
  }
  if (score >= CARRIED_FLOOR) return 'bone'
  if (score > 0) return 'grey'
  return 'fail'
}

/** _score_text in grounding.py: a source marker names a document rather than asserting anything, so it stays out of the score. */
export const scoreText = (text: string) => text.replace(/\*\(([^()]*)\)\*/g, ' ').replace(/[*`]/g, ' ')

// Python's \d, \w and \b match Unicode in a str pattern; JavaScript's match ASCII, even with the u flag.
// \d is \p{Nd}, \w is [\p{L}\p{N}_], and \b beside a word character is a look-around on that class.
const W = String.raw`[\p{L}\p{N}_]`
const NOT_W = String.raw`[^\p{L}\p{N}_]`
const B_BEFORE = `(?<!${W})`
const B_AFTER = `(?!${W})`

/** _NUMBER_RE in grounding.py: a figure, 1,000,000 with its three-digit groups, 5.5, 20480, never "3,4" read as one. Any decimal digit, as Python's \d. */
export const NUMBER_RE = () => /\p{Nd}{1,3}(?:,\p{Nd}{3})+(?:\.\p{Nd}+)?|\p{Nd}+(?:\.\p{Nd}+)?/gu

/** _number_key in grounding.py: 20,480 and 20480 are one number; 5.5 stays 5.5. */
export const numberKey = (raw: string) => raw.replace(/,/g, '')

/** The numbers a text states, as written, in order: _NUMBER_RE.findall in grounding.py. */
export const numbersIn = (text: string): string[] => text.match(NUMBER_RE()) ?? []

/** The sentence's numbers the retrieved text never states, as written: `missing` in _ground_sentence. */
export function missingNumbers(sentence: string, allNumbers: ReadonlySet<string>): string[] {
  return numbersIn(sentence).filter((n) => !allNumbers.has(numberKey(n)))
}

// _DOC_NOUN and _SAY_VERB in grounding.py: the documents as subject, a verb of saying
const DOC_NOUN =
  '(?:corpus|documentation|documents?|docs|material|knowledge base|sources?|readmes?|passages?|portfolio documentation|retrieved (?:text|material|documentation|context|passages?))'
const SAY_VERB =
  '(?:specify|specifies|say|says|state|states|mention|mentions|cover|covers|document|documents|describe|describes|address|addresses|provide|provides|name|names|list|lists|show|shows|explain|explains|detail|details|confirm|confirms|establish|establishes|define|defines|indicate|indicates|record|records|give|gives|include|includes|contain|contains)'

/** _DECLINE_RE in grounding.py: a sentence that opens by declining. Anchored at the start, as re.match is. */
export const DECLINE_RE = new RegExp(
  `^${NOT_W}*` + String.raw`(?:(?:however|but|also|note that|based on [^,;]{1,40}|according to [^,;]{1,40}),?\s*)?(?:\*\*[^*]+\*\*\s*)?(?:` +
    String.raw`(?:the|this|our|my|that|these|its)\s+(?:(?:${W}|-)+\s+){0,3}?` + DOC_NOUN +
    String.raw`\s+(?:does not|doesn't|did not|didn't|do not|don't|never)\s+` + String.raw`(?:${W}+\s+)?` + SAY_VERB + B_AFTER +
    String.raw`|i (?:don't|do not) have (?:[\p{L}\p{N}_-]+\s+){0,3}?(?:information|documentation|record|details?)` + B_AFTER +
    String.raw`|i (?:don't|do not) have (?:(?:${W}|['.\u0060-])+\s+){1,8}?in my knowledge base` + `${NOT_W}*$` +
    String.raw`|(?:there is|there's) no (?:documented|recorded|stated|documentation|mention|information|record)` + B_AFTER +
    String.raw`|no (?:information|documentation|record|mention) (?:is|was|exists|about|on|of|in)` + B_AFTER +
    ')',
  'iu',
)

/** _SECOND_CLAUSE_RE in grounding.py: a second clause makes a decline a sentence like any other. */
export const SECOND_CLAUSE_RE = new RegExp(
  String.raw`;|\s(?:but|yet|although|though|whereas)\s|` + `${B_BEFORE}it does${B_AFTER}|${B_BEFORE}it is${B_AFTER}`,
  'iu',
)

/** _CLAUSE_COMMA_RE in grounding.py: a comma before "and" or "or" joins a clause, not a list item,
 *  when a subject (a determiner and one to three words, or a capitalised word and up to two), an
 *  auxiliary or a named verb, and a number or a capitalised name within three words follow.
 *  Case-sensitive, as the gate is. */
/** _PLAIN_WORD in grounding.py: a lowercase word that is no determiner, pronoun or relative pronoun. */
const PLAIN_WORD = String.raw`(?!(?:the|a|an|this|that|these|those|its|our|my|their|your|which|who|whom|whose|where|when|it|they|we|each|every|all|and|or)` + B_AFTER + String.raw`)[a-z](?:${W}|-)*`
const CLAUSE_SUBJECT =
  String.raw`(?:the|this|that|these|those|a|an|its|our|my|their|your)\s+(?:[A-Z](?:${W}|-)*\s+){0,2}(?:` + PLAIN_WORD + String.raw`\s+){1,3}?` +
  String.raw`|[A-Z](?:${W}|-)*\s+(?:` + PLAIN_WORD + String.raw`\s+){0,2}?`
/** _CLAUSE_VERBS in grounding.py: the verb is named, never guessed from an s ending. */
const CLAUSE_VERBS =
  'listens|runs|expects|requires|serves|writes|sends|provides|includes|contains|allows|' +
  'keeps|takes|makes|gives|says|specifies|describes|fails|starts|stops|opens|closes|' +
  'connects|accepts|exposes|depends|refers|applies|exists|follows|holds|validates|deploys|' +
  'migrates|publishes|subscribes|emits|waits|throws|raises|wraps|saves|adds|removes|deletes|' +
  'creates|generates|produces|consumes|reaches|sits|lives|goes|comes|gets|becomes|belongs|' +
  'behaves|responds'
const FINITE_VERB =
  '(?:is|are|was|were|has|have|had|does|do|did|will|would|can|cannot|could|should|must|may|might|shall|' +
  CLAUSE_VERBS +
  ')' +
  B_AFTER +
  String.raw`\s+(?:(?:${W}|-)+\s+){0,3}?(?:\p{Nd}|[A-HJ-Z]|I${W})`
export const CLAUSE_COMMA_RE = new RegExp(String.raw`,\s*(?:and|or)\s+(?:` + CLAUSE_SUBJECT + ')' + FINITE_VERB, 'u')

/** _straight_quotes in grounding.py: curly apostrophes as straight ones, so "don’t" reads as "don't". */
export const straightQuotes = (text: string) => text.replace(/[\u2018\u2019]/g, "'")

/** _REFERRAL_OBJECT and _CONTACT_ROUTE in grounding.py: a short object with no joiner, and the route. */
const REFERRAL_OBJECT = String.raw`(?:(?!(?:and|or|which|who|that|with|including|plus)` + B_AFTER + String.raw`)(?:${W}|['-])+\s+){0,4}?(?!(?:and|or|which|who|that|with|including|plus)` + B_AFTER + String.raw`)(?:${W}|['-])+`
const CONTACT_ROUTE = String.raw`(?:the\s+)?contact\s+(?:section|page|form)`
/** _REFERRAL_RE in grounding.py: a whole sentence that ends at the contact route. */
export const REFERRAL_RE = new RegExp(
  `^${NOT_W}*(?:` +
    String.raw`(?:please\s+)?(?:use|see|check|visit)\s+` + CONTACT_ROUTE + String.raw`(?:\s+for\s+` + REFERRAL_OBJECT + ')?' +
    String.raw`|for\s+` + REFERRAL_OBJECT + String.raw`,\s*(?:please\s+)?(?:use|see|check|visit)\s+` + CONTACT_ROUTE +
    '|' + CONTACT_ROUTE + String.raw`\s+(?:is|would be)\s+(?:the\s+)?(?:appropriate|best|right)\s+(?:place|route|channel)` +
    String.raw`(?:\s+(?:to|for)\s+` + REFERRAL_OBJECT + ')?' +
    String.raw`)[\s*_]*[.!]?[\s*_]*$`,
  'iu',
)

/** is_referral in grounding.py: the whole sentence points to the contact route and states no figure. */
export function isReferral(sentence: string): boolean {
  const s = straightQuotes(sentence)
  return REFERRAL_RE.test(s) && !numbersIn(s).length
}

/** is_decline in grounding.py: true when the whole sentence says the documents do not say. */
export function isDecline(sentence: string): boolean {
  const s = straightQuotes(sentence)
  return DECLINE_RE.test(s) && !SECOND_CLAUSE_RE.test(s) && !CLAUSE_COMMA_RE.test(s) && !numbersIn(s).length
}

/** _INFERENCE_RE in grounding.py: a reason, a consequence or a purpose. Such a sentence gets no second reading. */
export const INFERENCE_RE = new RegExp(
  B_BEFORE +
    '(because|therefore|thus|hence|consequently|so that|which means|this means|that means|as a result|in order to|favou?rs?|why)' +
    B_AFTER,
  'iu',
)

/** SECOND_PASSAGE_MIN_WORDS in grounding.py: the words a second passage must add beyond the best before the two are read together. */
export const SECOND_PASSAGE_MIN_WORDS = 2

/**
 * _second_reading in grounding.py: the sentence read against the best passage
 * joined with the passage adding the most words the best one lacks, and only
 * when that passage adds at least SECOND_PASSAGE_MIN_WORDS. Returns the single
 * reading and -1 when no second passage qualifies or the two together still
 * fall under the floor.
 */
export function secondReading(
  tokens: ReadonlySet<string>,
  best: number,
  carried: number,
  passageTokens: readonly ReadonlySet<string>[],
  floor: number = CARRIED_FLOOR,
): { carried: number; second: number } {
  const bestTokens = passageTokens[best]
  let second = -1
  let added = 0
  passageTokens.forEach((pt, i) => {
    if (i === best) return
    let n = 0
    for (const t of tokens) if (pt.has(t) && !bestTokens.has(t)) n++
    if (n > added) {
      second = i
      added = n
    }
  })
  if (second < 0 || added < SECOND_PASSAGE_MIN_WORDS) return { carried, second: -1 }
  let together = 0
  for (const t of tokens) if (bestTokens.has(t) || passageTokens[second].has(t)) together++
  const share = together / tokens.size
  if (share < floor) return { carried, second: -1 }
  return { carried: share, second }
}

/** Python's format(x, ".0%"): the exact double times 100, a half rounded to even. */
export function percent(share: number): string {
  const v = share * 100
  const f = Math.floor(v)
  const d = v - f
  const n = d > 0.5 || (d === 0.5 && f % 2 === 1) ? f + 1 : f
  return `${n}%`
}

/** SentenceGrounding.reason in grounding.py, word for word. */
export function reasonOf(
  g: Pick<Grounding, 'match' | 'missing' | 'decline' | 'spannedWith' | 'carried'> & {
    view?: boolean
    anchored?: boolean
    referral?: boolean
  },
): string {
  if (g.referral) return 'a referral to the contact route asserts nothing the documents would carry'
  if (g.decline) return 'a decline asserts nothing the documents would carry'
  if (g.view) {
    if (g.missing.length) return "the agent's view; number " + g.missing.join(', ') + ' appears in no passage'
    if (g.anchored === false) return "the agent's view, and the answer grounds no fact for it to rest on"
    return "the agent's view, read for its numbers only"
  }
  if (g.match.passage < 0) return 'no passage shares a word with it'
  const parts =
    g.spannedWith >= 0
      ? [`passages ${g.match.passage + 1} and ${g.spannedWith + 1} together carry ${percent(g.carried)} of its words`]
      : [`passage ${g.match.passage + 1} carries ${percent(g.carried)} of its words`]
  if (g.missing.length) parts.push('number ' + g.missing.join(', ') + ' appears in no passage')
  return parts.join('; ')
}

/**
 * _ground_sentence in grounding.py: one sentence's grounding. `tokens` are the
 * sentence's words after scoreText; an empty set is a unit the gate never
 * scores, and it comes back unsupported with no reason.
 */
export function groundSentence(
  statement: string,
  tokens: ReadonlySet<string>,
  passageTokens: readonly ReadonlySet<string>[],
  allNumbers: ReadonlySet<string>,
  view = false,
): Grounding {
  const match = bestPassage(tokens, passageTokens)
  const base = { match, missing: [] as string[], decline: false, view: false, spannedWith: -1, carried: match.score }
  if (!tokens.size) return { ...base, supported: false, reason: '' }
  if (isReferral(statement)) {
    const g = { ...base, decline: true, referral: true }
    return { ...g, supported: true, reason: reasonOf(g) }
  }
  if (isDecline(statement)) {
    const g = { ...base, decline: true }
    return { ...g, supported: true, reason: reasonOf(g) }
  }
  const missing = missingNumbers(statement, allNumbers)
  if (view) {
    // a view is read for its numbers only, so it lights no passage
    const g = { ...base, match: NO_MATCH, carried: 0, missing, view: true }
    return { ...g, supported: !missing.length, reason: reasonOf(g) }
  }
  let carried = match.score
  let spannedWith = -1
  if (match.passage >= 0 && carried < CARRIED_FLOOR && !INFERENCE_RE.test(statement)) {
    const r = secondReading(tokens, match.passage, carried, passageTokens)
    carried = r.carried
    spannedWith = r.second
  }
  const g = { match, missing, decline: false, view: false, spannedWith, carried }
  return { ...g, supported: match.passage >= 0 && carried >= CARRIED_FLOOR && !missing.length, reason: reasonOf(g) }
}

export function analyse(response: string, contexts: readonly string[]): Reading {
  const passages = passagesOf(contexts)
  const passageTokens = passages.map((p) => tokensOf(p.text))
  // ground() in grounding.py: every number any passage states
  const numbers = new Set(passages.flatMap((p) => numbersIn(p.text).map(numberKey)))
  const units = responseUnits(response).map((u): ReadUnit => {
    // the gate scores prose only: a code fence and the CITATIONS block never reach it (response_sentences)
    // _ground_sentence: a line ending in a colon introduces a list and is not scored
    const tokens = u.kind === 'cite' || u.kind === 'code' || u.leadIn ? new Set<string>() : tokensOf(scoreText(u.text))
    const g = groundSentence(u.text, tokens, passageTokens, numbers, u.view)
    return { ...u, ...g, tokens, tint: tintOf(tokens.size, g.match.score, g) }
  })
  return { units: anchorViews(units), passages, passageTokens, numbers }
}

/** _anchor_views in grounding.py: with no grounded fact but a decline, every view sentence fails. */
function anchorViews(units: ReadUnit[]): ReadUnit[] {
  if (units.some((u) => u.tokens.size > 0 && u.supported && !u.view && !u.decline)) return units
  return units.map((u) => {
    if (!u.view) return u
    const g = { ...u, supported: false, anchored: false }
    return { ...g, reason: reasonOf(g), tint: tintOf(u.tokens.size, u.match.score, g) }
  })
}

/** A claim's own grounding against the reading's passages, with its tint and words: what the claim card says. */
export function groundClaim(
  statement: string,
  reading: Reading,
  position?: number,
): Grounding & { tint: Tint; tokens: Set<string> } {
  // a claim's position indexes the gate's scored sentences, so its own sentence, grounded in the
  // whole answer, is the card; the same words elsewhere in the answer never are
  const own = position === undefined ? undefined : reading.units.filter((u) => u.tokens.size > 0)[position]
  if (own && own.text === statement) return own
  const tokens = tokensOf(scoreText(statement))
  const g = groundSentence(statement, tokens, reading.passageTokens, reading.numbers)
  return { ...g, tokens, tint: tintOf(tokens.size, g.match.score, g) }
}

export interface LitPassage {
  passage: number
  /** The sentence's words this passage carries, marked when it is lit. */
  shared: Set<string>
}

/** The passages the gate read a sentence against: the best, and the second when the gate joined two. */
export function litPassages(
  g: Pick<Grounding, 'match' | 'spannedWith'>,
  tokens: ReadonlySet<string>,
  passageTokens: readonly ReadonlySet<string>[],
): LitPassage[] {
  if (g.match.passage < 0) return []
  const out: LitPassage[] = [{ passage: g.match.passage, shared: g.match.shared }]
  const pt = passageTokens[g.spannedWith]
  if (g.spannedWith >= 0 && pt) out.push({ passage: g.spannedWith, shared: new Set([...tokens].filter((t) => pt.has(t))) })
  return out
}

export interface ClaimFocus {
  /** The response sentence that carries the claim, marked as selected, or -1. */
  unit: number
  /** The claim's own grounding: what its card says. */
  grounding: ReturnType<typeof groundClaim>
  /** The passages that grounding read, lit while the claim is active. */
  lit: LitPassage[]
}

/**
 * What an active claim shows: its carrying sentence marked, and the passages
 * its own grounding read lit, so the card's reason and the lit passages come
 * from one reading of the claim statement, as the bench's focusClaim does.
 */
export function claimFocus(statement: string, reading: Reading, position?: number): ClaimFocus {
  const grounding = groundClaim(statement, reading, position)
  return {
    unit: carryingUnit(statement, reading.units),
    grounding,
    lit: litPassages(grounding, grounding.tokens, reading.passageTokens),
  }
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
