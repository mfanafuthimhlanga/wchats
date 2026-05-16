import { h } from 'preact'
export function EmptyState({ agentName }) {
  return (
    <div style="display:flex;flex-direction:column;align-items:flex-start;gap:4px;max-width:88%;">
      <div style="display:flex;align-items:center;gap:6px;">
        <span style="font-size:11px;font-weight:600;color:var(--accent);">{agentName || 'Agent'}</span>
        <span style="font-size:9px;font-weight:600;color:var(--text-3);border:1px solid var(--border);background:var(--surface-2);border-radius:99px;padding:1px 6px;letter-spacing:0.05em;text-transform:uppercase;">AGENT</span>
      </div>
      <div class="message-bubble agent">
        Hi! I'm <strong>{agentName || 'your assistant'}</strong>. Ask me anything.
      </div>
    </div>
  )
}
