import { h } from 'preact'
export function ToolCallLabel({ toolName, input }) {
  const shortInput = JSON.stringify(input).slice(0, 40)
  return (
    <div class="tool-call-label">
      <span class="dot" />
      <code>{toolName}({shortInput})</code>
    </div>
  )
}
