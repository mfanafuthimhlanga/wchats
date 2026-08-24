import { describe, expect, it } from 'vitest'
import { applyTheming } from './theming.js'

/** Stands in for document.documentElement; records what was written. */
function fakeRoot() {
  const written = {}
  return { written, style: { setProperty: (name, value) => { written[name] = value } } }
}

const apply = (theming) => {
  const root = fakeRoot()
  applyTheming(theming, root)
  return root.written
}

describe('applyTheming', () => {
  it('maps the API default theming onto variables widget.css reads', () => {
    expect(
      apply({
        primary_color: '#7B1C3A',
        accent_gold: '#B8860B',
        font_family: 'system-ui',
        border_radius: '14px',
        background: '#FDF9F5',
      })
    ).toEqual({
      '--accent': '#7B1C3A',
      '--gold': '#B8860B',
      '--font-sans': "system-ui, -apple-system, 'Segoe UI', sans-serif",
      '--radius-sm': '14px',
      '--bg': '#FDF9F5',
    })
  })

  it('maps a stored per-tenant widget config', () => {
    expect(
      apply({
        widget_bg: '#101010',
        header_bg: '#1B4332',
        header_text: '#E9F5EE',
        agent_bubble_bg: '#1A1A1A',
        agent_bubble_text: '#F2F2F2',
        user_bubble_bg: '#2D6A4F',
        user_bubble_text: '#FFFFFF',
        send_button: '#40916C',
        input_bg: '#202020',
      })
    ).toEqual({
      '--bg': '#101010',
      '--header-bg': '#1B4332',
      '--header-text': '#E9F5EE',
      '--surface-1': '#1A1A1A',
      '--text-1': '#F2F2F2',
      '--accent': '#2D6A4F',
      '--user-bubble-text': '#FFFFFF',
      '--send-button': '#40916C',
      '--input-bg': '#202020',
    })
  })

  it('translates enumerated typography names into CSS values', () => {
    expect(apply({ font_family: 'Inter' })['--font-sans']).toContain("'Inter'")
    expect(apply({ font_family: 'Georgia' })['--font-sans']).toContain('Georgia')
    expect(apply({ border_radius_preset: 'pill' })).toEqual({ '--radius-sm': '22px' })
    expect(apply({ border_radius_preset: 'sharp' })).toEqual({ '--radius-sm': '4px' })
  })

  it('drops keys that are not CSS and keys the stylesheet does not know', () => {
    expect(
      apply({
        appearance: 'slide-out-panel',
        launcher_shape: 'square',
        font_custom_url: 'https://fonts.example/x.woff2',
        something_new: '#fff',
      })
    ).toEqual({})
  })

  it('keeps the VIEW link legible at the API schema defaults', () => {
    // apps/api/app/schemas/agent.py WidgetColorsSchema: header_bg and
    // user_bubble_bg are BOTH #7B1C3A. While header_bg wrote --surface-2 —
    // .citation-row's background — and user_bubble_bg wrote --accent —
    // .view-link's colour — saving the widget-config form unchanged put the
    // VIEW control at 1.00:1. The row's background must not be reachable from
    // any key the link's colour is also reachable from.
    const written = apply({
      widget_bg: '#FDF9F5',
      header_bg: '#7B1C3A',
      header_text: '#FFFFFF',
      agent_bubble_bg: '#FDF9F5',
      agent_bubble_text: '#4A2030',
      user_bubble_bg: '#7B1C3A',
      user_bubble_text: '#FFFFFF',
      send_button: '#7B1C3A',
      input_bg: '#F7F0EA',
    })
    expect(written['--surface-2']).toBeUndefined()
    expect(written['--text-2']).toBeUndefined()
    expect(written['--accent']).toBe('#7B1C3A')
    expect(written['--header-bg']).toBe('#7B1C3A')
  })

  it('refuses a value that would carry extra declarations into the stylesheet', () => {
    expect(apply({ primary_color: '#fff;background:url(https://evil.example/x)' })).toEqual({})
    expect(apply({ background: 'red}body{display:none' })).toEqual({})
    expect(apply({ font_family: 'a'.repeat(200) })).toEqual({})
    expect(apply({ primary_color: 42 })).toEqual({})
  })
})
