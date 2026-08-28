import { h } from 'preact'

// POPIA s18 notification, decided on issue #16 and recorded in ADR 0006. The
// egress is accepted rather than firewalled, so the Customer is told who
// receives the message. The previous copy, "Powered by AI", said an AI was
// involved and said nothing about where the text goes.
//
// The bar is one 32px row inside a 380px widget and it stays one row. The
// notice shares 356px with the version tag, which is 380 less 12px of padding
// each side. How much of that 356 the tag takes has not been measured.
//
// NOTICE_MAX_CHARS is therefore a PROXY, and it is worth knowing which way it
// is wrong. It counts characters; the constraint is rendered width, and a
// 41-character string of capitals is wider than one of commas. It holds
// because this copy is ordinary sentence case in an 11px system font. A test
// asserting character count may not claim the line fits, and the one below
// does not.
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
