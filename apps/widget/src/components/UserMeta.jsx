import { h } from 'preact'
export function UserMeta() {
  const t = new Date()
  const time = `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}`
  return (
    <div style="font-size:10px;color:var(--text-4);font-family:var(--font-mono);text-align:right;margin-top:2px;">
      You · {time}
    </div>
  )
}
