'use client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import EmptyState from '../../../../../components/gotham/EmptyState'
import { analyse, carryingUnit, inlineParts, segments, type ReadUnit } from './reading'
import {
  isDone,
  keyOf,
  resumeIndex,
  scenarioProgress,
  sittingBody,
  storedAnswers,
  type ClaimAnswer,
  type FlaggedClaimsResponse,
} from './sitting'

/**
 * The review of flagged claims: one screen per answer, the claims the judge
 * could not find beside it, a yes or no on each. Ported from the labelling
 * page (apps/api/tests/evals/calibration/page/template.html), the v1.
 *
 * The reading aid (sentence edges, the lit passage) measures word overlap and
 * nothing else; the legend says so. The claim statement is the judge's own
 * sentence and is set in the judge's voice. Answers are saved as a sitting:
 * every answer made since the last flush goes in one POST, 600ms after the
 * last keypress. A refused sitting (the run no longer flags a claim, or its
 * statement changed) is retried one answer at a time; each refused answer goes
 * back to the last value the server confirmed and the status says to reload.
 * Any other failure retries every 3s while the page is open, and a sitting
 * still pending on unmount is sent once more on the way out.
 */

/** Thrown by `save` so the page can tell a refusal from an outage. */
export class SaveError extends Error {
  constructor(public status: number) {
    super(`HTTP ${status}`)
  }
}

export type SaveSitting = (body: { answers: ClaimAnswer[] }) => Promise<void>

interface Props {
  data: FlaggedClaimsResponse
  save: SaveSitting
  backHref: string
}

type SaveState = 'saved' | 'saving' | 'failed' | 'refused' | 'unavailable'

const SAVE_TEXT: Record<SaveState, string> = {
  saved: 'saved',
  saving: 'saving',
  failed: 'save failed, retrying',
  refused: 'an answer was refused, reload the page',
  unavailable: 'answers cannot be stored on this agent yet',
}
const WARN: ReadonlySet<SaveState> = new Set(['failed', 'refused', 'unavailable'])

const FLUSH_MS = 600
const RETRY_MS = 3000
// what the server refuses about the sitting itself; an expired token or a rate limit is an outage to retry
const REFUSALS = new Set([400, 404, 409, 413, 422])
const isRefusal = (e: unknown) => e instanceof SaveError && REFUSALS.has(e.status)

export default function ClaimsReview({ data, save, backHref }: Props) {
  // a scenario with no flagged claim has nothing to ask; the API does not send one, and the page does not trust that
  const scenarios = useMemo(() => data.scenarios.filter((s) => s.claims.length > 0), [data.scenarios])
  const total = scenarios.length
  const canStore = data.reviews_available

  const [answers, setAnswers] = useState<Map<string, boolean>>(() => storedAnswers(scenarios))
  const [current, setCurrent] = useState(() => resumeIndex(scenarios, answers))
  const [activeClaim, setActiveClaim] = useState(0)
  const [selected, setSelected] = useState(-1)
  const [saveState, setSaveState] = useState<SaveState>(canStore ? 'saved' : 'unavailable')
  // a refusal stays on the status line until the page is reloaded, whatever saves after it
  const [refusedOnce, setRefusedOnce] = useState(false)
  const shownState: SaveState = refusedOnce && saveState === 'saved' ? 'refused' : saveState
  const [reduced, setReduced] = useState(false)
  const [landOnQuestion, setLandOnQuestion] = useState(false)

  const answersRef = useRef(answers)
  answersRef.current = answers
  // what the server holds, as far as this page has seen: the stored answers, plus every sitting it accepted
  const confirmed = useRef(new Map(storedAnswers(scenarios)))
  const saveRef = useRef(save)
  saveRef.current = save
  const scenariosRef = useRef(scenarios)
  scenariosRef.current = scenarios
  const pending = useRef(new Set<string>())
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flushing = useRef(false)
  const mounted = useRef(true)
  const paneRef = useRef<HTMLDivElement>(null)
  const responseRef = useRef<HTMLDivElement>(null)
  const questionRef = useRef<HTMLParagraphElement>(null)

  const scenario = scenarios[current]
  const reading = useMemo(
    () => (scenario ? analyse(scenario.response, scenario.retrieved_contexts) : null),
    [scenario],
  )
  const lit = selected >= 0 && reading ? reading.units[selected]?.match : undefined

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduced(mq.matches)
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  // ── saving: a sitting is every answer since the last flush ──────────────
  const revert = useCallback((keys: Iterable<string>) => {
    setAnswers((prev) => {
      const next = new Map(prev)
      for (const k of keys) {
        const last = confirmed.current.get(k)
        if (last === undefined) next.delete(k)
        else next.set(k, last)
      }
      return next
    })
  }, [])

  const flush = useCallback(async () => {
    if (flushing.current || !pending.current.size) return
    flushing.current = true
    const keys = new Set(pending.current)
    pending.current.clear()
    const body = sittingBody(scenarios, keys, answersRef.current)
    const accept = (sent: ClaimAnswer[]) => {
      for (const a of sent) confirmed.current.set(keyOf(a.scenario_id, a.position), a.supported)
    }
    let refused = false
    try {
      if (body.answers.length) await save(body)
      accept(body.answers)
    } catch (e) {
      if (!isRefusal(e)) {
        flushing.current = false
        if (!mounted.current) return
        // a newer answer already waiting wins over this snapshot
        for (const k of keys) if (!pending.current.has(k)) pending.current.add(k)
        setSaveState('failed')
        flushTimer.current = setTimeout(() => void flush(), RETRY_MS)
        return
      }
      // the server refuses a sitting whole, so the answers go one at a time: the good ones stay, the refused go back
      refused = true
      if (body.answers.length > 1) {
        for (const a of body.answers) {
          try {
            await save({ answers: [a] })
            accept([a])
          } catch (err) {
            if (!isRefusal(err)) pending.current.add(keyOf(a.scenario_id, a.position))
            else revert([keyOf(a.scenario_id, a.position)])
          }
        }
      } else revert(keys)
    }
    flushing.current = false
    if (!mounted.current) return
    if (refused) {
      setRefusedOnce(true)
      setSaveState('refused')
    }
    if (pending.current.size) {
      // after a refusal, whatever is left waiting is an outage or a newer answer: either way, on the clock
      if (refused) flushTimer.current = setTimeout(() => void flush(), RETRY_MS)
      else void flush()
    } else if (!refused) setSaveState('saved')
  }, [save, scenarios, revert])

  const answer = useCallback(
    (claimIndex: number, supported: boolean) => {
      if (!scenario || !canStore) return
      const claim = scenario.claims[claimIndex]
      if (!claim) return
      const k = keyOf(scenario.scenario_id, claim.position)
      if (answersRef.current.get(k) === supported) return // a second press on the same answer sends nothing
      setAnswers((prev) => new Map(prev).set(k, supported))
      pending.current.add(k)
      setSaveState('saving')
      if (flushTimer.current) clearTimeout(flushTimer.current)
      flushTimer.current = setTimeout(() => void flush(), FLUSH_MS)
    },
    [scenario, canStore, flush],
  )

  // on the way out: stop the retry clock, and send what is still pending once more.
  // Unmount only, through refs: with `save` or `scenarios` as dependencies this ran on
  // every refetch, swallowed the send's failure and left the status on "saving".
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (flushTimer.current) clearTimeout(flushTimer.current)
      if (pending.current.size) {
        const body = sittingBody(scenariosRef.current, pending.current, answersRef.current)
        pending.current.clear()
        if (body.answers.length) void saveRef.current(body).catch(() => undefined)
      }
    }
  }, [])

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (pending.current.size || flushing.current) e.preventDefault()
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])

  // ── the reading aid ─────────────────────────────────────────────────────
  const selectSentence = useCallback(
    (n: number) => {
      setSelected(n)
      const u = reading?.units[n]
      if (!u || u.match.passage < 0) return
      const pane = paneRef.current
      const pw = pane?.querySelector<HTMLElement>(`[data-i="${u.match.passage}"]`)
      if (!pane || !pw) return
      const top = pane.scrollTop + (pw.getBoundingClientRect().top - pane.getBoundingClientRect().top) - 10
      pane.scrollTo({ top, behavior: reduced ? 'auto' : 'smooth' })
    },
    [reading, reduced],
  )

  const sentenceIndexes = useMemo(
    () => (reading ? reading.units.map((u, i) => (scoreable(u) ? i : -1)).filter((i) => i >= 0) : []),
    [reading],
  )

  const moveSentence = useCallback(
    (d: 1 | -1) => {
      if (!sentenceIndexes.length) return
      let i = sentenceIndexes.indexOf(selected)
      i = i === -1 ? (d > 0 ? 0 : sentenceIndexes.length - 1) : Math.max(0, Math.min(sentenceIndexes.length - 1, i + d))
      const n = sentenceIndexes[i]
      responseRef.current?.querySelector<HTMLElement>(`[data-s="${n}"]`)?.focus()
      selectSentence(n)
    },
    [sentenceIndexes, selected, selectSentence],
  )

  const go = useCallback(
    (i: number) => {
      if (i < 0 || i >= total) return
      // a button that kept focus across the screen change would take the next Space as an answer
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
      setCurrent(i)
      setLandOnQuestion(true)
      setActiveClaim(0)
      setSelected(-1)
      window.scrollTo({ top: 0 })
    },
    [total],
  )

  // the active claim lights the sentence that carries it, and that sentence lights its passage
  const focusClaim = useCallback(
    (i: number) => {
      if (!scenario || !reading) return
      const n = Math.max(0, Math.min(scenario.claims.length - 1, i))
      setActiveClaim(n)
      const unit = carryingUnit(scenario.claims[n].statement, reading.units)
      if (unit >= 0) selectSentence(unit)
      else setSelected(-1)
    },
    [scenario, reading, selectSentence],
  )

  useEffect(() => {
    if (scenario && reading) focusClaim(0)
    // keyboard users land on the new screen's question, not back at the top of the document
    if (landOnQuestion) {
      questionRef.current?.focus()
      setLandOnQuestion(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return
      if (/^(TEXTAREA|INPUT|SELECT)$/.test((e.target as HTMLElement).tagName)) return
      if (e.key === 'ArrowRight') return go(current + 1)
      if (e.key === 'ArrowLeft') return go(current - 1)
      if (e.key === 'j' || e.key === 'k') {
        e.preventDefault()
        return moveSentence(e.key === 'j' ? 1 : -1)
      }
      if (e.key === 'y') return answer(activeClaim, true)
      if (e.key === 'n') return answer(activeClaim, false)
      if (/^[1-9]$/.test(e.key) && scenario && Number(e.key) <= scenario.claims.length) return focusClaim(Number(e.key) - 1)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [scenario, current, activeClaim, go, moveSentence, answer, focusClaim])

  // ── the screen ──────────────────────────────────────────────────────────
  const notes = (
    <>
      {data.dropped_scenarios > 0 && (
        <p className="note warn" role="status">
          {data.dropped_scenarios} answer{data.dropped_scenarios === 1 ? '' : 's'} could not be read and {data.dropped_scenarios === 1 ? 'is' : 'are'} not shown.
        </p>
      )}
      {!canStore && (
        <p className="note warn" role="status">
          This agent&apos;s database cannot store answers yet.
        </p>
      )}
    </>
  )

  if (total === 0) {
    return (
      <div className="page review">
        <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />
        <Head backHref={backHref} />
        {notes}
        <EmptyState
          heading="No flagged claims on this run"
          body="The judge flagged nothing, or this run was scored before claims were recorded."
        />
      </div>
    )
  }

  const answeredTotal = scenarios.reduce((n, s) => n + scenarioProgress(s, answers).answered, 0)
  const flaggedTotal = scenarios.reduce((n, s) => n + s.claims.length, 0)
  const progress = scenarioProgress(scenario, answers)
  const active = scenario.claims[activeClaim] ?? scenario.claims[0]
  const activeAnswer = answers.get(keyOf(scenario.scenario_id, active.position))

  return (
    <div className="page review">
      <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />
      <Head backHref={backHref} />

      <div className="bar">
        <span className="keys" aria-hidden="true">
          <b>←</b> <b>→</b> answer · <b>j</b> <b>k</b> sentence{scenario.claims.length > 1 && <> · <b>1</b> <b>2</b> claim</>} · <b>y</b> yes · <b>n</b> no
        </span>
        <div className="progress">
          <div className="cells" aria-hidden="true">
            {scenarios.map((s, i) => {
              const p = scenarioProgress(s, answers)
              const cls = ['cell', isDone(p) ? 'full' : '', p.only === true ? 'yes' : p.only === false ? 'no' : '', i === current ? 'here' : '']
              return <i key={s.scenario_id} className={cls.filter(Boolean).join(' ')} />
            })}
          </div>
          <span className="count mono" aria-live="polite">
            {answeredTotal} / {flaggedTotal}{answeredTotal === flaggedTotal ? ', all answered' : ''}
          </span>
          <span className={`save mono${WARN.has(shownState) ? ' warn' : ''}`} aria-live="polite">
            {SAVE_TEXT[shownState]}
          </span>
        </div>
      </div>

      {notes}

      <main className="bench">
        <section className="answer" aria-label="the answer">
          <div className="block">
            <div className="eyebrow">question · {current + 1} of {total}</div>
            <p className="question" ref={questionRef} tabIndex={-1}>{scenario.question}</p>
          </div>
          <div className="block">
            <div className="eyebrow">agent response</div>
            <div className="response" ref={responseRef}>
              {reading?.units.map((u, i) => (
                <Sentence key={`${scenario.scenario_id}:${i}`} unit={u} index={i} on={i === selected} onSelect={selectSentence} />
              ))}
            </div>
            <div className="legend">
              left edge: how many of the sentence&apos;s words appear in some passage. <b className="k-bone">bone</b> most · <b className="k-grey">grey</b> some · <b className="k-fail">red</b> none. Shared words are not support; read the passage.
            </div>
          </div>
        </section>

        <aside className="ctxcol" aria-label="retrieved text">
          <div className="eyebrow">retrieved text</div>
          {selected >= 0 && lit && lit.passage < 0 && reading && reading.passages.length > 0 && (
            <div className="ctxnote" aria-live="polite">no passage shares words with that sentence</div>
          )}
          <div className="ctxpane" ref={paneRef}>
            {reading?.passages.length === 0 && <p className="pw empty">no retrieved text was stored for this answer</p>}
            {reading?.passages.map((p, i) => {
              const isLit = lit?.passage === i
              return (
                <p key={`${scenario.scenario_id}:${i}`} className={`pw${isLit ? ' lit' : ''}`} data-i={i}>
                  {isLit && lit
                    ? segments(p.text, lit.shared).map((s, k) => (s.hit ? <mark key={k}>{s.text}</mark> : s.text))
                    : p.text}
                </p>
              )
            })}
          </div>
        </aside>

        <aside className="claims" aria-label="flagged claims">
          <div className="eyebrow">flagged claims · {progress.answered} of {progress.total} answered</div>
          <p className="rubric">Yes when a document of yours says this. No when none does.</p>
          {scenario.claims.map((c, i) => {
            const v = answers.get(keyOf(scenario.scenario_id, c.position))
            const on = i === activeClaim
            return (
              <div
                key={`${scenario.scenario_id}:${c.position}`}
                className={`card${on ? ' active' : ''}`}
                onClick={() => focusClaim(i)}
                onFocus={() => { if (!on) focusClaim(i) }}
              >
                <h3>
                  Claim {i + 1} of {scenario.claims.length}
                  {scenario.claims.length > 1 && i < 9 && <span className="kbd">{i + 1}</span>}
                </h3>
                <p className="voice statement">{c.statement}</p>
                <div className="seg">
                  <button type="button" className={`vbtn yes${v === true ? ' on' : ''}`} aria-pressed={v === true} aria-label={`claim ${i + 1} yes`} disabled={!canStore} onClick={() => answer(i, true)}>
                    Yes
                  </button>
                  <button type="button" className={`vbtn no${v === false ? ' on' : ''}`} aria-pressed={v === false} aria-label={`claim ${i + 1} no`} disabled={!canStore} onClick={() => answer(i, false)}>
                    No
                  </button>
                </div>
              </div>
            )
          })}
          <div className="navbtns">
            <button type="button" className="btn btn-ghost" disabled={current === 0} onClick={() => go(current - 1)}>Previous</button>
            <button type="button" className="btn btn-primary" disabled={current === total - 1} onClick={() => go(current + 1)}>Next</button>
          </div>
        </aside>
      </main>

      {/* under 1180px the claims sit below the fold, so the strip carries the active statement with its buttons */}
      <div className="strip" role="group" aria-label="answer shortcuts">
        <div className="strow">
          <span className="slabel mono" aria-hidden="true">{activeClaim + 1}/{scenario.claims.length}</span>
          <span className="voice sstatement">{active.statement}</span>
        </div>
        <div className="strow">
          <button type="button" className={`vbtn yes${activeAnswer === true ? ' on' : ''}`} aria-pressed={activeAnswer === true} aria-label="active claim yes" disabled={!canStore} onClick={() => answer(activeClaim, true)}>Yes</button>
          <button type="button" className={`vbtn no${activeAnswer === false ? ' on' : ''}`} aria-pressed={activeAnswer === false} aria-label="active claim no" disabled={!canStore} onClick={() => answer(activeClaim, false)}>No</button>
          <div className="navbtns">
            <button type="button" className="btn btn-ghost" aria-label="Previous answer" disabled={current === 0} onClick={() => go(current - 1)}>←</button>
            <button type="button" className="btn btn-primary" aria-label="Next answer" disabled={current === total - 1} onClick={() => go(current + 1)}>→</button>
          </div>
        </div>
      </div>
    </div>
  )
}

const scoreable = (u: ReadUnit) => u.kind !== 'cite' && u.tint !== 'none'

function Head({ backHref }: { backHref: string }) {
  return (
    <header className="page-head">
      <div className="row">
        <div>
          <h1>Flagged claims</h1>
          <p className="sub">
            Statements the judge could not find in the retrieved text, under the answer they came from.
            Say whether each one is in your documents.
          </p>
        </div>
        <Link href={backHref} className="btn btn-ghost">Back to evals</Link>
      </div>
    </header>
  )
}

function Sentence({ unit, index, on, onSelect }: { unit: ReadUnit; index: number; on: boolean; onSelect: (n: number) => void }) {
  const open = scoreable(unit)
  const cls = ['sent', unit.kind === 'p' ? '' : unit.kind, `t-${unit.tint}`, unit.para ? 'para' : '', on ? 'on' : ''].filter(Boolean).join(' ')
  const bullet = unit.marker ? <span className="bu">{/^[-*•]$/.test(unit.marker) ? '•' : unit.marker} </span> : null
  const body = unit.kind === 'code' ? unit.text : inlineParts(unit.text).map((p, k) => (p.code ? <code key={k}>{p.text}</code> : p.text))
  if (!open) return <span className={cls}>{bullet}{body}</span>
  return (
    <span
      className={cls}
      data-s={index}
      tabIndex={on ? 0 : -1}
      role="button"
      onClick={() => onSelect(index)}
      onFocus={() => { if (!on) onSelect(index) }}
      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onSelect(index) } }}
    >
      {bullet}{body}
    </span>
  )
}

// Page-scoped CSS, the same dangerouslySetInnerHTML pattern as eval/page.tsx.
// Every colour is a token from globals.css; the two hues are the two answers.
const PAGE_CSS = `
  .review .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; padding-bottom: 14px; border-bottom: 1px solid var(--hairline); margin-top: -12px; }
  .review .keys { font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); }
  .review .keys b { display: inline-block; border: 1px solid var(--hairline-strong); padding: 0 5px; border-radius: 2px; color: var(--ink-2); font-weight: 400; }
  .review .progress { margin-left: auto; display: flex; align-items: center; gap: 10px; }
  .review .cells { display: flex; flex-wrap: wrap; gap: 2px; max-width: 366px; }
  .review .cell { display: block; width: 6px; height: 10px; border: 1px solid var(--ink-3); border-radius: 1px; }
  .review .cell.full { background: var(--live); border-color: transparent; }
  .review .cell.full.yes { background: var(--pass); }
  .review .cell.full.no { background: var(--fail); }
  .review .cell.here { border-color: var(--live-hot); box-shadow: 0 0 0 1px var(--live-hot); }
  .review .count { font-size: 12px; color: var(--ink-2); }
  .review .save { font-size: 10.5px; color: var(--ink-3); min-width: 9ch; }
  .review .save.warn { color: var(--fail); }
  .review .note { margin-top: 14px; font-size: 13px; color: var(--ink-2); }
  .review .note.warn { color: var(--fail); }

  .review .bench { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0 18px; }
  .review .answer { padding-block: 18px 24px; display: flex; flex-direction: column; gap: 20px; min-width: 0; }
  .review .block { display: flex; flex-direction: column; gap: 6px; }
  .review .eyebrow { font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-3); font-weight: 700; }
  .review .question { font-family: var(--display); font-size: 18px; line-height: 1.4; letter-spacing: -0.01em; max-width: 68ch; text-wrap: balance; }
  .review .question:focus { outline: none; }
  .review .response { max-width: 78ch; font-size: 14px; line-height: 1.6; background: var(--surface); padding: 12px 16px; border-radius: var(--r-control); overflow-wrap: anywhere; }
  .review .sent { display: block; border-left: 3px solid transparent; padding: 2px 0 2px 10px; }
  .review .sent + .sent { margin-top: 2px; }
  .review .sent.para { margin-top: 10px; }
  .review .sent.li, .review .sent.cont { padding-left: 24px; }
  .review .sent.li { text-indent: -14px; }
  .review .sent .bu { color: var(--ink-3); }
  .review .sent.t-bone { border-left-color: var(--live); }
  .review .sent.t-grey { border-left-color: var(--ink-3); }
  .review .sent.t-fail { border-left-color: var(--fail); }
  .review .sent[data-s] { cursor: pointer; }
  .review .sent[data-s]:hover, .review .sent.on { background: var(--surface-2); }
  .review .sent.code { font-family: var(--mono); font-size: 11.5px; line-height: 1.6; white-space: pre-wrap; overflow-x: auto; background: var(--well); padding: 8px 0 8px 10px; margin-top: 8px; }
  .review .sent.cite { font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); white-space: pre-wrap; margin-top: 12px; }
  .review .response code { font-family: var(--mono); font-size: 0.88em; background: var(--surface-2); padding: 0 3px; border-radius: 2px; }
  .review .legend { font-size: 11px; line-height: 1.5; color: var(--ink-3); }
  .review .legend b { font-weight: 600; }
  .review .k-bone { color: var(--live); }
  .review .k-grey { color: var(--ink-2); }
  .review .k-fail { color: var(--fail); }

  .review .ctxcol { min-width: 0; display: flex; flex-direction: column; gap: 6px; padding-block: 18px 12px; }
  .review .ctxnote { font-size: 11.5px; color: var(--fail); }
  .review .ctxpane { min-height: 0; max-height: 70vh; overflow-y: auto; background: var(--well); border: 1px solid var(--hairline); border-radius: var(--r-control); padding: 10px 14px 10px 8px; }
  .review .pw { margin: 0 0 8px; padding-left: 9px; border-left: 3px solid transparent; font-size: 12.5px; line-height: 1.6; color: var(--ink-2); white-space: pre-wrap; overflow-wrap: anywhere; }
  .review .pw:last-child { margin-bottom: 0; }
  .review .pw.lit { border-left-color: var(--live); color: var(--ink); }
  .review .pw.empty { color: var(--ink-3); }
  .review mark { background: var(--live-dim); color: var(--live-hot); border-radius: 1px; padding: 0 1px; }

  .review .claims { display: flex; flex-direction: column; gap: 12px; padding-block: 18px 40px; }
  .review .rubric { color: var(--ink-2); font-size: 12.5px; line-height: 1.5; }
  .review .card { display: flex; flex-direction: column; gap: 10px; background: var(--surface); border: 1px solid var(--hairline-strong); border-radius: var(--r-panel); padding: 14px 16px; cursor: default; }
  .review .card.active { border-color: var(--live-hot); box-shadow: inset 0 0 0 1px var(--live-hot); }
  .review .card h3 { font-size: 13px; color: var(--ink-2); display: flex; align-items: center; gap: 8px; }
  .review .statement { font-size: 15.5px; }
  .review .seg { display: flex; gap: 6px; }
  .review .vbtn { flex: 1; min-width: 0; font-family: var(--mono); font-size: 12px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; padding: 8px 0; background: transparent; border: 1px solid var(--hairline-strong); color: var(--ink-2); border-radius: var(--r-control); cursor: pointer; }
  .review .vbtn:hover { color: var(--ink); background: var(--surface-2); }
  .review .vbtn.yes.on, .review .vbtn.yes.on:hover { background: var(--pass); border-color: var(--pass); color: var(--live-ink); }
  .review .vbtn.no.on, .review .vbtn.no.on:hover { background: var(--fail); border-color: var(--fail); color: var(--live-ink); }
  .review .vbtn[disabled] { opacity: 0.35; cursor: default; }
  .review .navbtns { display: flex; gap: 6px; }
  .review .claims .navbtns .btn { flex: 1; justify-content: center; }
  .review :focus-visible { outline: 2px solid var(--live-hot); outline-offset: 2px; }

  .review .strip { display: none; position: sticky; bottom: 0; z-index: var(--z-strip); flex-direction: column; gap: 6px; margin: 0 -40px; padding: 8px 16px; background: var(--surface); border-top: 1px solid var(--hairline-strong); }
  .review .strow { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .review .strip .slabel { font-size: 11px; font-weight: 700; color: var(--ink-2); flex: none; }
  .review .sstatement { font-size: 13.5px; line-height: 1.35; min-width: 0; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .review .strip .vbtn { padding: 6px 0; font-size: 11px; }
  .review .strip .navbtns { margin-left: 4px; gap: 4px; }
  .review .strip .btn { padding: 6px 10px; }

  @media (max-width: 860px) { .review .keys, .review .card .kbd { display: none; } }
  @media (max-width: 1179px) {
    .review.page { padding-bottom: 0; }
    .review .strip { display: flex; }
    .review .claims .navbtns { display: none; }
  }
  @media (max-width: 900px) { .review .strip { margin: 0 -20px; bottom: 56px; } }
  @media (min-width: 1180px) {
    .review .bench { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 300px; }
    .review .ctxcol, .review .claims { position: sticky; top: 12px; align-self: start; max-height: calc(100vh - 24px); }
    .review .claims { overflow-y: auto; }
    .review .ctxpane { max-height: calc(100vh - 78px); }
  }
  @media (prefers-reduced-motion: no-preference) { .review .vbtn, .review .btn { transition: background 120ms, color 120ms; } }
`
