import { h } from 'preact'
import { useState } from 'preact/hooks'
export function EscalationPanel({ reason, onSubmit }) {
  const [sent, setSent] = useState(false)
  const handle = (e) => { e.preventDefault(); setSent(true); onSubmit?.() }
  if (sent) return <div class="escalation-panel"><p>Got it — our team will be in touch.</p></div>
  return (
    <div class="escalation-panel" role="dialog" aria-modal="false" aria-label="Escalation panel">
      <div class="escalation-header">Flagged for our team</div>
      <p style="font-size:12px;margin:4px 0 8px;color:var(--amber);">{reason}</p>
      <form onSubmit={handle}>
        <div style="display:flex;flex-direction:column;gap:2px;margin-bottom:6px;">
          <label for="esc-name" class="escalation-label">Name</label>
          <input id="esc-name" type="text" placeholder="Your name" />
        </div>
        <div style="display:flex;flex-direction:column;gap:2px;margin-bottom:6px;">
          <label for="esc-email" class="escalation-label">Email</label>
          <input id="esc-email" type="email" placeholder="Your email" />
        </div>
        <button type="submit">Send my details</button>
      </form>
    </div>
  )
}
