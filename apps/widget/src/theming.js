// The one place an API theming key becomes a CSS custom property.
//
// GET /widget/{agent_id}/config returns a flat `theming` dict in one of two
// shapes (apps/api/app/api/v1/widget.py):
//
//   default   a tenant that never saved a widget config gets DEFAULT_THEMING:
//             primary_color, accent_gold, font_family, border_radius, background
//   stored    a tenant that saved one gets its widget_config flattened:
//             widget_bg, header_bg, header_text, agent_bubble_bg,
//             agent_bubble_text, user_bubble_bg, user_bubble_text, send_button,
//             input_bg, font_family, border_radius_preset, plus non-CSS keys
//
// Both shapes are mapped onto the variables widget.css actually reads.
// scripts/check-theming-contract.mjs imports THEMING_MAP and fails the build if
// any variable here is never referenced by a rule in widget.css, and if any
// module outside this one writes a custom property of its own.

/** API theming key -> the CSS custom property widget.css reads. */
export const THEMING_MAP = {
  // default shape
  primary_color: '--accent',
  accent_gold: '--gold',
  background: '--bg',
  font_family: '--font-sans',
  border_radius: '--radius-sm',

  // stored shape
  widget_bg: '--bg',
  // The header gets variables of its own. header_bg used to write --surface-2
  // and header_text --text-2, both of which .citation-row also reads, so at the
  // schema's OWN defaults (header_bg and user_bubble_bg are both #7B1C3A,
  // apps/api/app/schemas/agent.py) the citation row was painted #7B1C3A while
  // .view-link stayed --accent #7B1C3A: the VIEW control measured 1.00:1 the
  // moment a tenant saved the widget-config form without changing anything.
  // Un-themed it is 8.99:1. A theming key may not reach a variable a component
  // it does not name reads.
  header_bg: '--header-bg',
  header_text: '--header-text',
  agent_bubble_bg: '--surface-1',
  agent_bubble_text: '--text-1',
  user_bubble_bg: '--accent',
  user_bubble_text: '--user-bubble-text',
  send_button: '--send-button',
  input_bg: '--input-bg',
  border_radius_preset: '--radius-sm',
}

// Keys the config route sends that are not CSS at all: `appearance` and
// `launcher_shape` drive the launcher in embed/widget.js, outside this iframe,
// and `font_custom_url` needs an @font-face the widget does not load. They are
// dropped rather than written as variables nothing reads.
export const NON_CSS_KEYS = ['appearance', 'launcher_shape', 'font_custom_url']

// typography.font_family and typography.border_radius_preset are enumerated
// names, not CSS values (apps/api/app/schemas/agent.py). An untranslated
// "System UI" or "pill" reaching the stylesheet is an invalid declaration.
const FONT_STACKS = {
  Inter: "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif",
  'System UI': "system-ui, -apple-system, 'Segoe UI', sans-serif",
  // DEFAULT_THEMING's bare "system-ui" would replace the stylesheet's stack
  // with a single family and no fallback.
  'system-ui': "system-ui, -apple-system, 'Segoe UI', sans-serif",
  Georgia: "Georgia, 'Times New Roman', serif",
  custom: "system-ui, -apple-system, 'Segoe UI', sans-serif",
}
const RADIUS_PRESETS = { sharp: '4px', rounded: '14px', pill: '22px' }

// The API validates hex and enumerated values server-side; this is the second
// line. A custom property is substituted into whatever declarations reference
// it, so a value carrying `;`, a brace, a comment opener or a url() is refused
// rather than trusted.
const UNSAFE_VALUE = /[;{}<>\\]|url\(|\/\*/i
const MAX_VALUE_LENGTH = 120

/** Translate a raw theming value and reject anything unsafe. null = do not apply. */
export function cssValue(key, raw) {
  if (typeof raw !== 'string') return null
  let value = raw.trim()
  if (key === 'font_family') value = FONT_STACKS[value] || value
  if (key === 'border_radius_preset') value = RADIUS_PRESETS[value] || value
  if (!value || value.length > MAX_VALUE_LENGTH || UNSAFE_VALUE.test(value)) return null
  return value
}

/**
 * Write the mapped theming onto `root` (the iframe's documentElement).
 * Unknown and non-CSS keys are ignored. Returns the variables actually written,
 * which is what the unit tests assert against.
 */
export function applyTheming(theming, root) {
  const applied = {}
  if (!theming || !root) return applied
  for (const key of Object.keys(theming)) {
    const name = THEMING_MAP[key]
    if (!name) continue
    const value = cssValue(key, theming[key])
    if (value === null) continue
    root.style.setProperty(name, value)
    applied[name] = value
  }
  return applied
}
