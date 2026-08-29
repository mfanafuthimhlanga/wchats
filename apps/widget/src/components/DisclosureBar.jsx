import { h } from 'preact'

// POPIA s18 notification, decided on issue #16 and recorded in ADR 0006. The
// egress is accepted rather than firewalled, so the Customer is told who
// receives the message. The previous copy, "Powered by AI", said an AI was
// involved and said nothing about where the text goes.
//
// The bar is one 32px row inside a 380px widget and it stays one row. That is
// a rendered width, not a character count, so scripts/check-rendered-notice.mjs
// measures it in headless Chromium on every build and fails when this notice
// wraps, clips, or overflows the bar (#106). Change the copy and the gate
// reports whether it still fits.
//
// OpenAI is hardcoded because ADR 0008 makes it the only provider for every
// model call on the platform. When a second provider serves a Customer turn,
// this becomes a prop fed by the run record's served provider.
export const PROCESSING_NOTICE = 'AI-generated replies, processed by OpenAI'

export function DisclosureBar() {
  return (
    <div class="disclosure-bar">
      <span>{PROCESSING_NOTICE}</span>
      <code class="mono-tag">W Chats v0</code>
    </div>
  )
}
