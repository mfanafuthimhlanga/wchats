import { describe, it, expect } from 'vitest'
import {
  DisclosureBar,
  PROCESSING_NOTICE,
  NOTICE_MAX_CHARS,
} from './DisclosureBar.jsx'

// Preact vnodes are plain objects, so the component's output is readable
// without a DOM or a renderer. Asserting on the vnode tests what the Customer
// is shown; asserting on the file's text would only test that a string is
// present somewhere, which is the source-assertion habit the API gates ban.
function textNodes(vnode) {
  const out = []
  const walk = (node) => {
    if (node == null || node === false) return
    if (Array.isArray(node)) return node.forEach(walk)
    if (typeof node === 'string' || typeof node === 'number') {
      return out.push(String(node))
    }
    if (node.props) walk(node.props.children)
  }
  walk(vnode)
  return out
}

describe('DisclosureBar', () => {
  it('renders the processing notice', () => {
    expect(textNodes(DisclosureBar())).toContain(PROCESSING_NOTICE)
  })

  it('names the processor, not just the fact of AI', () => {
    expect(PROCESSING_NOTICE).toMatch(/OpenAI/)
  })

  it('stays inside the one-line budget the 380px bar allows', () => {
    expect(PROCESSING_NOTICE.length).toBeLessThanOrEqual(NOTICE_MAX_CHARS)
  })

  it('keeps the version tag beside the notice', () => {
    expect(textNodes(DisclosureBar())).toContain('W Chats v0')
  })
})
