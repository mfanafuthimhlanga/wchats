import { h } from 'preact'
export function MessageBubble({ role, text }) {
  return <div class={`message-bubble ${role}`}>{text}</div>
}
