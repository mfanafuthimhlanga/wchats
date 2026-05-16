import { h } from 'preact'
import { useState } from 'preact/hooks'
export function InputBar({ disabled, submitting, onSend }) {
  const [value, setValue] = useState('')
  const submit = () => { if (value.trim()) { onSend(value.trim()); setValue('') } }
  const onKey = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }
  return (
    <div class="input-bar">
      <textarea
        aria-label="Message"
        value={value}
        onInput={e => setValue(e.target.value)}
        onKeyDown={onKey}
        disabled={disabled}
        placeholder="Type a message..."
        rows={1}
      />
      <button
        class="send"
        onClick={submit}
        disabled={disabled}
        aria-label={submitting ? 'Sending…' : 'Send message'}
        style="width:44px;height:44px;min-width:44px;min-height:44px;"
      >
        {submitting
          ? <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10" stroke-dasharray="16" stroke-dashoffset="0"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></path></svg>
          : <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        }
      </button>
    </div>
  )
}
