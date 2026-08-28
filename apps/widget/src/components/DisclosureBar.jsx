import { h } from 'preact'

// POPIA s18 notification, decided on issue #16 and recorded in ADR 0006. The
// egress is accepted rather than firewalled, so the Customer is told who
// receives the message. The previous copy, "Powered by AI", said an AI was
// involved and said nothing about where the text goes.
//
// The bar is one 32px row inside a 380px widget and it stays one row. After
// 12px padding each side and the version tag, the notice has roughly 280px,
// which is why NOTICE_MAX_CHARS pins the length rather than trusting a reader
// to remember. Widen the bar before widening the string.
//
// OpenAI is hardcoded because ADR 0008 makes it the only provider for every
// model call on the platform. When a second provider serves a Customer turn,
// this becomes a prop fed by the run record's served provider.
export const PROCESSING_NOTICE = 'AI-generated replies, processed by OpenAI'
export const NOTICE_MAX_CHARS = 48

export function DisclosureBar() {
  return (
    <div class="disclosure-bar">
      <span>{PROCESSING_NOTICE}</span>
      <code class="mono-tag">W Chats v0</code>
    </div>
  )
}
