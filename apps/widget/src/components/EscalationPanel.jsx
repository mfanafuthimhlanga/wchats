import { h } from 'preact'
import { useState } from 'preact/hooks'
export function EscalationPanel({ reason, context, onSubmit }) {
  const [sent, setSent] = useState(false)
  const handle = (e) => { e.preventDefault(); setSent(true); onSubmit?.() }
  if (sent) return <div class="escalation-panel"><p>Got it — a team member will follow up.</p></div>
  return (
    <div class="escalation-panel">
      <div class="escalation-header">Escalated to Human</div>
      <p>Reason: {reason}</p>
      <form onSubmit={handle}>
        <input type="text" placeholder="Your name" />
        <input type="email" placeholder="Your email" />
        <button type="submit">Send my details</button>
      </form>
    </div>
  )
}
