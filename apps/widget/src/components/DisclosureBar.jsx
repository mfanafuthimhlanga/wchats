import { h } from 'preact'

// POPIA s18 notification, decided on issue #16 and recorded in ADR 0006. The
// egress is accepted rather than firewalled, so the Customer is told who
// receives the message. The previous copy, "Powered by AI", said an AI was
// involved and said nothing about where the text goes.
//
// The bar is one 32px row inside a 380px widget and it stays one row.
// Measured in headless Chromium against embed/index.html, 2026-08-28:
//
//     bar content width   356px   (380 less 12px padding each side)
//     version tag          55px
//     this notice         208px   at 11px / 16.5px system-ui, 41 chars
//     rendered height    16.5px   one line-height, so it does not wrap
//
// That leaves the notice 301px. NOTICE_MAX_CHARS is still a PROXY, because it
// counts characters while the constraint is rendered width and a string of
// capitals is wider than one of commas. The measurement is what makes 48 safe
// rather than hopeful: at the 5.07px average this copy renders, 48 characters
// come to about 243px, and overflowing 301px needs an average of 6.27px that
// sentence case does not reach. Re-measure before raising it.
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
