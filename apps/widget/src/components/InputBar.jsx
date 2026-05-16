import { h } from 'preact'
import { useState } from 'preact/hooks'
export function InputBar({ disabled, onSend }) {
  const [value, setValue] = useState('')
  const submit = () => { if (value.trim()) { onSend(value.trim()); setValue('') } }
  const onKey = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }
  return (
    <div class="input-bar">
      <textarea value={value} onInput={e => setValue(e.target.value)} onKeyDown={onKey} disabled={disabled} placeholder="Type a message..." rows={1} />
      <button class="send" onClick={submit} disabled={disabled} aria-label="Send message" style="width:44px;height:44px;min-width:44px;min-height:44px;">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
      </button>
    </div>
  )
}
