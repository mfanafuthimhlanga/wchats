import { h } from 'preact'
import { useRef, useState } from 'preact/hooks'
import { sendFeedback } from '../api.js'

const MAX_SUBMISSIONS = 2
const SCORES = [1, 2, 3, 4, 5]

export function FeedbackRow({ apiBase, agentId, messageId, conversationId }) {
  // Hooks first, unconditionally, on every render — the Rules of Hooks
  // forbid calling them after an early return. The degrade guard (below)
  // still governs what gets rendered; it just can't come before these.
  const [rating, setRating] = useState(null)
  const [score, setScore] = useState(null)
  const [sentCount, setSentCount] = useState(0)
  const starRefs = useRef({})

  // A rating button with nothing to name is not a smaller version of this
  // feature — it is a control that cannot work. An older cached payload, or
  // a turn served before the identifier shipped, degrades to no control
  // rather than a broken one (23-UI-SPEC.md §6.2).
  if (!messageId) return null

  // OD-7's bound: at most two requests ever leave for one message, so a
  // customer cannot flood the rate limiter or skew the aggregate from a
  // single reply.
  const post = (nextRating, nextScore, revert) => {
    if (sentCount >= MAX_SUBMISSIONS) return
    setSentCount(c => c + 1)
    sendFeedback(apiBase, agentId, messageId, conversationId, nextRating, nextScore)
      .then(res => { if (!res.ok) throw new Error('feedback request failed: ' + res.status) })
      .catch(err => {
        console.error('widget feedback submission failed', err)
        revert()
      })
  }

  // 23-09 adversarial review (findings 27-28): the optimistic setRating/
  // setScore used to run unconditionally, before post()'s own cap check —
  // so a third click past MAX_SUBMISSIONS still visibly moved the selected
  // state even though post() silently no-opped and revert() never ran.
  // The widget could end up showing "up" selected forever while the last
  // thing actually recorded server-side was "down." Checking the cap here,
  // before any state mutation, means a click that cannot be sent never
  // changes what's on screen — the display only ever shows what was
  // genuinely recorded. Re-clicking the already-selected value is also
  // now a no-op rather than a redundant duplicate POST that burns a slot
  // on nothing.
  const handleRate = (next) => {
    if (next === rating) return
    if (sentCount >= MAX_SUBMISSIONS) return
    const prev = rating
    setRating(next)
    post(next, undefined, () => setRating(prev))
  }

  const handleScore = (next) => {
    if (next === score) return
    if (sentCount >= MAX_SUBMISSIONS) return
    const prev = score
    setScore(next)
    post(rating, next, () => setScore(prev))
  }

  // 23-09 adversarial review (finding 29): role="radiogroup"/role="radio"
  // promises the standard roving-tabindex + arrow-key pattern to assistive
  // tech — one Tab stop for the group, arrow keys move both focus and
  // selection. The stars had the roles with none of the behavior. This
  // mirrors the roving pattern BenchPane.tsx already implements correctly
  // for its trace listbox, at 1/5th the surface.
  const handleStarKeyDown = (e) => {
    const currentIndex = score === null ? -1 : SCORES.indexOf(score)
    let nextIndex = null
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % SCORES.length
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      nextIndex = currentIndex < 0 ? SCORES.length - 1 : (currentIndex - 1 + SCORES.length) % SCORES.length
    } else if (e.key === 'Home') {
      nextIndex = 0
    } else if (e.key === 'End') {
      nextIndex = SCORES.length - 1
    }
    if (nextIndex === null) return
    e.preventDefault()
    const nextScore = SCORES[nextIndex]
    handleScore(nextScore)
    starRefs.current[nextScore]?.focus()
  }

  return (
    <div class="feedback-row">
      <div class="feedback-thumbs">
        <button
          type="button"
          class="feedback-thumb"
          aria-label="Rate this reply helpful"
          aria-pressed={rating === 'up'}
          onClick={() => handleRate('up')}
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
        </button>
        <button
          type="button"
          class="feedback-thumb"
          aria-label="Rate this reply unhelpful"
          aria-pressed={rating === 'down'}
          onClick={() => handleRate('down')}
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>
        </button>
      </div>
      {rating && (
        <div class="feedback-score">
          <div class="feedback-score-label">Rate this reply</div>
          <div
            class="feedback-score-options"
            role="radiogroup"
            aria-label="Rate this reply, 1 to 5"
            onKeyDown={handleStarKeyDown}
          >
            {SCORES.map(n => (
              <button
                key={n}
                ref={el => { starRefs.current[n] = el }}
                type="button"
                role="radio"
                class="feedback-star"
                aria-checked={score === n}
                aria-label={`Rate ${n} out of 5`}
                tabIndex={(score ?? SCORES[0]) === n ? 0 : -1}
                onClick={() => handleScore(n)}
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.77 5.82 21 7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
