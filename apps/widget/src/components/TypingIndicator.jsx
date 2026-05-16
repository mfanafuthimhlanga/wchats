import { h } from 'preact'
export function TypingIndicator() {
  return (
    <div class="typing-indicator" aria-label="Agent is typing">
      <span class="dot" /><span class="dot" /><span class="dot" />
    </div>
  )
}
