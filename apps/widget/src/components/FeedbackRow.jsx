import { h } from 'preact'
import { useState } from 'preact/hooks'
import { sendFeedback } from '../api.js'

const MAX_SUBMISSIONS = 2

export function FeedbackRow({ apiBase, agentId, messageId, conversationId }) {
  // Hooks first, unconditionally, on every render — the Rules of Hooks
  // forbid calling them after an early return. The degrade guard (below)
  // still governs what gets rendered; it just can't come before these.
  const [rating, setRating] = useState(null)
  const [score, setScore] = useState(null)
  const [sentCount, setSentCount] = useState(0)

  // A rating button with nothing to name is not a smaller version of this
  // feature — it is a control that cannot work. An older cached payload, or
  // a turn served before the identifier shipped, degrades to no control
  // rather than a broken one (23-UI-SPEC.md §6.2).
  if (!messageId) return null

  // OD-7's bound: at most two requests ever leave for one message, so a
  // customer cannot flood the rate limiter or skew the aggregate from a
  // single reply. Past the cap a re-click still updates the visible state
  // (below) so the button never looks unresponsive — it just stops sending.
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

  const handleRate = (next) => {
    const prev = rating
    setRating(next)
    post(next, undefined, () => setRating(prev))
  }

  const handleScore = (next) => {
    const prev = score
    setScore(next)
    post(rating, next, () => setScore(prev))
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
          <div class="feedback-score-options" role="radiogroup" aria-label="Rate this reply, 1 to 5">
            {[1, 2, 3, 4, 5].map(n => (
              <button
                key={n}
                type="button"
                role="radio"
                class="feedback-star"
                aria-checked={score === n}
                aria-label={`Rate ${n} out of 5`}
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
