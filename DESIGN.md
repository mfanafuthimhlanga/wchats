# Design

Direction: **GOTHAM — "Bone on Graphite"**. A precision instrument for shipping a
defensible support agent: graphite ground, bone lettering, and colour reserved
entirely for verdicts.

**THE LAW OF THIS SYSTEM: colour is a verdict, never a decoration.**

The chrome has no hue at all. Ground is graphite, lettering is bone, and a live
thing goes BRIGHT, not coloured. The only two colours that exist in the entire
product are judgments: green means it held, red means the gate is shut. If you
find yourself reaching for a hue to make something look nice, you have broken the
system, because a colour on this bench is a claim about whether an agent can be
trusted with a customer.

> **Source of truth.** This file records what actually shipped in Phase 20
> (Milestone v1.2, verified 2026-07-18). The canonical tokens live in
> `apps/admin/app/globals.css`; the working prototypes live in
> `prototypes/gotham/`. If this document and `globals.css` ever disagree,
> **`globals.css` wins** — and fix this file.
>
> **Superseded directions — do not reintroduce.** "Hillbrow at Dusk"
> (skyline photo + indigo glass, retired 2026-07-10) and **"Amber Console"**
> (amber on chroma-zero near-black, the intermediate direction this file
> previously described, never shipped). `prototypes/gotham/MESH.md` also still
> describes an earlier "Brass on Petrol" palette that was renamed to LIVE in plan
> 20-05 — treat its palette table as historical, its ten borrowings as current.
>
> `pnpm --filter wchats-admin check:no-dusk-tokens` **fails the build** on any
> `dusk-*`, skyline, or `amber-console` marker. The gate is the enforcement; this
> paragraph is only the explanation.
>
> The `wchats-design` skill still describes the retired dusk system and is
> superseded by this file for all new work.

## Theme

Dark, locked. No light mode. No section inversions. `color-scheme: dark`.

## Colour

Hex values are canonical (they are what `globals.css` ships).

### The bench — graphite, chroma zero. Nothing here has an opinion.

| Token | Value | Role |
|---|---|---|
| `--bg` | `#0E1012` | page ground, never pure black |
| `--surface` | `#15181B` | panels, table ground (the whisper-zone) |
| `--surface-2` | `#1E2327` | raised: inputs, hover rows, muted chips |
| `--well` | `#08090B` | sunken: code, logs, the deep |

### Hairlines — engraved, not drawn

| Token | Value | Role |
|---|---|---|
| `--hairline` | `rgba(231,229,225,0.13)` | primary structure line |
| `--hairline-soft` | `rgba(231,229,225,0.06)` | in-table row rules |
| `--hairline-strong` | `rgba(231,229,225,0.30)` | crosshairs, emphasis rules, card hover |

### Bone lettering

| Token | Value | Role |
|---|---|---|
| `--ink` | `#E7E5E1` | primary text |
| `--ink-2` | `#9BA1A3` | secondary text |
| `--ink-3` | `#7E8588` | tertiary/disabled |

`--ink-3` is `#7E8588`, **not** `#6B7275`. The darker tone measured 3.64–4.07:1
against `--bg` / `--surface` / `--well` and failed WCAG AA (4.5:1); it was
lightened during the 20-15 axe pass. Do not darken it back.

### LIVE is not a colour. It is brightness.

Where every other console would put an accent hue, this one puts light. A live
agent, a focused field, the primary action: all go bone-white.

| Token | Value | Role |
|---|---|---|
| `--live` | `#E7E5E1` | live, active, focus, primary CTA |
| `--live-hot` | `#FFFFFF` | the hottest state |
| `--live-dim` | `rgba(231,229,225,0.10)` | selected / tint backgrounds |
| `--live-ink` | `#0E1012` | graphite text ON a bone fill |

### The two colours. Both are verdicts.

| Token | Value | Role |
|---|---|---|
| `--pass` / `--pass-dim` | `#4CC38A` / `rgba(76,195,138,0.13)` | it held |
| `--fail` / `--fail-dim` | `#E5484D` / `rgba(229,72,77,0.08)` | it did not |
| `--seal` / `--seal-hot` / `--seal-dim` | `#E5484D` / `#FF6369` / `rgba(229,72,77,0.08)` | the gate is shut — the same red, because it is the same claim |

`--fail-dim` alpha is `0.08`, not `0.13`. `.chip-fail` puts `--fail` on
`--fail-dim`, which composited to 4.34:1 at the higher alpha. `--fail` itself is a
locked brand hex (UI-SPEC S8), so the *background* alpha was darkened to reach
4.58:1. Do not raise it.

### The four eval channels

Even here, restraint: four **values of bone** separated by luminance, not four
hues. Only the channel that is failing may be red. A chart on this bench is read
by weight, the way an engineer reads a drawing.

| Token | Value | Channel |
|---|---|---|
| `--ch-1` | `#E7E5E1` | faithfulness — the brightest, because it matters most |
| `--ch-2` | `#A9AFB1` | |
| `--ch-3` | `#7C8386` | |
| `--ch-4` | `#565C5F` | |

## The gate

One attribute on the root, and the room goes with it. `:root[data-gate="blocked"]`
remaps `--live*` to `--seal*` and turns every hairline red, so an open critical
finding repaints the whole console. **The gate is not a badge, it is the room.**

The `:root` qualifier on that selector is load-bearing: a bare
`[data-gate="blocked"]` ties the base `:root` block at specificity (0,1,0) and
loses to the later rule. Managed in React by `GateProvider`.

The room changes temperature, it never cuts: `.tint` transitions
background/border/colour/shadow over 600ms ease. Under
`prefers-reduced-motion: reduce` that collapses to 1ms.

## Typography

| Token | Family | Use |
|---|---|---|
| `--display` | Space Grotesk 500, `letter-spacing: -0.02em` | h1/h2/h3 |
| `--sans` | Inter | body, 14px / 1.55 |
| `--voice` | Newsreader italic | the judge — machine reasoning, typeset |
| `--mono` | JetBrains Mono | every number, id, timestamp, keycap, log |

Numbers are always mono and right-aligned in ledgers (`.ledger .num`).
`.voice` italic serif is reserved for a judge's verdict; it is not a decorative
pull-quote.

## Shape — an instrument has tolerances, not curves

| Token | Value | Use |
|---|---|---|
| `--r-control` | `4px` | buttons, inputs, wells |
| `--r-panel` | `6px` | zones, cards, the command strip |
| `--r-pill` | `100px` | chips only |

Nothing above 6px except pills.

## Layout grammar

- **Rail** — fixed 56px left rail (`.rail`, `z-index: var(--z-rail)`), marks +
  icon buttons, `aria-current="page"` gets `--live` on `--live-dim`. The deck
  offsets by `padding-left: 56px`. Under 900px the rail becomes a 56px bottom bar
  and the deck offsets downward instead. **There is no top nav** — `TopNav` was
  deleted in plan 20-14.
- **Page** — `max-width: 1280px`, `padding: 34px 40px 72px`. Sections are
  separated by a top hairline, never by a box.
- **Zone** — the single panel primitive (`.zone`); `[data-live="true"]` marks a
  live one. Boxes are rare and never nested.
- **Ledger** — tables are ledgers: `border-collapse: collapse`, one header
  hairline, `--hairline-soft` between rows, row hover to `--surface`, numeric
  columns right-aligned.
- **Chip** — verdict-only: `live` / `pass` / `fail` / `seal` / `mute`. A chip is
  never a category tag. `.dot-live` breathes; other dots do not.
- **Command strip** — permanent, never a modal; a run replaces it in place.
- **Cards** — `.card-top` is height-locked to 52px so hairlines, metrics and
  footers read across the whole grid even when a card carries two chips. The
  stretched `.card-open::after` covers the card, so any real control inside
  (e.g. delete) must be raised above it with `position: relative; z-index: 1`.
- **Z-scale** — `--z-grid: 0`, `bench: 1`, `rail: 10`, `strip: 20`, `gate: 25`,
  `sheet: 30`, `toast: 40`.

## Motion

- State only: 140–200ms ease on hover/focus/expand. No entrance choreography.
- The gate tint is the one slow move: 600ms.
- `prefers-reduced-motion: reduce` zeroes all animation and transition durations
  globally, skips the gate-shutter repaint and the row fades, and disables the
  `page-flip` sign-out animation. This is a **parity requirement (UI2-08)**, not a
  nicety — it is asserted by the Playwright suite.

## Three.js — landing and auth only

Mounted client-only via `SceneMount`; `prototypes/gotham/scene.js` is the
reference. Full-bleed behind the hero, `pointer-events: none`, degrades to a
static ground if WebGL is missing. **Never on an admin page** — confinement is
asserted by the parity suite. The soul editor deliberately dropped three.js for a
CSS-only temperament control (plan 20-09).

## Accessibility

- WCAG AA contrast on all text, verified by `@axe-core/playwright`. The two
  contrast fixes above are the ones that were actually caught — do not undo them.
- Skip link (`.skip`) parks off-screen and lands at `left: 68px` on focus, clear
  of the rail.
- `.vh` for screen-reader-only headings and labels.
- Focus is visible and bone: `outline: 2px solid var(--live)` with 2px offset.
- No horizontal overflow at 1440 / 1280 / 900px — asserted at all three widths.
  Wide content scrolls inside its own container (`.well` uses `overflow-x: auto`).

## Voice

Sentence case everywhere. Plain verbs on buttons ("Run evals", "Copy embed
code"). No emoji. No em or en dashes in UI copy — hyphen only. Middle-dot `·` at
most once per metadata line. Evidence over adjectives: show the number.

**No UI copy explains the design metaphor.** The room does not narrate itself.

## Anti-patterns

- A hue used to make something look nice. Colour is a verdict.
- A chip used as a category tag rather than a verdict.
- Decoration in a functional slot.
- A token renamed without a repo-wide grep of the old name — **CSS fails
  silently on an undefined custom property**, so a rename that misses one call
  site degrades quietly instead of erroring.
- Reintroducing `dusk-*`, skyline, or `amber-console` markers (the build gate
  rejects them).
- three.js on an admin route.
- Nested boxes, or a box where a hairline would do.
