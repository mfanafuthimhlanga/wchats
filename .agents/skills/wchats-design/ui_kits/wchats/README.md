# W Chats — Landing UI Kit

The W Chats marketing landing page in the **Hillbrow at Dusk** design system. One self-contained page composed of small JSX components, with the canonical "Build pipeline → Live widget" hero animation.

## What this kit covers

This is the public marketing surface a non-technical operator first lands on. Audience-split: marketing-clear copy on the left, a verifiable working demo (the animated workflow card) on the right.

## File map

| File | What it is |
|---|---|
| `index.html` | The page itself. Loads Google Fonts, design tokens, then mounts `<App />`. |
| `styles.css` | Landing-specific styles (nav, hero, workflow card, animation classes, sections, footer). Extends `colors_and_type.css`. |
| `App.jsx` | Composition root: `<Nav>` + `<Hero>` + `<HowItWorks>` + `<ShipSection>` + `<Footer>`. |
| `components.jsx` | Atomic primitives — `Icon`, `Logo`, `Button`, `Chip`. Inline-SVG Feather icons. |
| `Nav.jsx` | Transparent top nav over the hero. |
| `Hero.jsx` | Two-panel hero with copy + workflow card. |
| `WorkflowCard.jsx` | **The animation engine.** Phase 1 (build pipeline, 4 step cards cycling) cross-fades to Phase 2+ (live widget greeting → user question → retrieval → grounded answer with citations). 19s loop. |
| `HowItWorks.jsx` | 4-step grid (Drop your data / Shape the soul / Test in sandbox / Deploy as iframe). |
| `ShipSection.jsx` | 2-col headline + 4 feature items (pre-deploy checklist, isolation, monitoring, verified knowledge). |
| `Footer.jsx` | Logo + copyright + OS tag + links. |

## The hero animation, in detail

`WorkflowCard.jsx` runs a timer-driven sequence on mount:

| Phase | Time | What renders |
|---|---|---|
| 1 — Build pipeline | 0–9.0s | 4 step cards cycle: `upcoming → active (pulsing) → done (green check)`. 2s per step. |
| Cross-fade | 9.0–9.6s | Steps panel fades out, widget panel fades in (CSS opacity transition). |
| 2 — Greeting | 9.0–11.0s | Typing dots → agent says *"Hi 👋 I'm Maya, Lakewood Bakery's assistant…"*. |
| 3 — Question | 11.0–13.0s | User input types out *"Delivery areas and hours?"*, then commits as a user bubble. |
| 4 — Retrieval | 13.0–15.0s | Typing dots + shimmering source chips `📄 Delivery Policy.pdf`, `📄 Opening Hours.pdf`. |
| 5 — Answer | 15.0–17.0s | Agent message with the grounded answer + two citation chips `[1]`, `[2]`. |
| Hold | 17.0–19.0s | Final state held. |
| Reset | 19.0s | DOM cleared, loop restarts. |

- **`prefers-reduced-motion: reduce`** is respected — the component returns the final widget state statically, no loop.
- **Pause on hover** — hovering the card pauses the sequence; mouse-leave resumes from the start of the cycle.
- **Cross-fade mechanic** — both panels (`#steps-panel`, `#widget-panel`) sit absolutely-positioned at the same coordinates inside `.demo-panels`; only opacity changes.

## How to wire it into a new page

```html
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT@9..144,300..800,0..100&family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="path/to/colors_and_type.css">
<link rel="stylesheet" href="path/to/ui_kits/wchats/styles.css">

<div id="root"></div>
<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" ...></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" ...></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" ...></script>

<script type="text/babel" src="components.jsx"></script>
<script type="text/babel" src="WorkflowCard.jsx"></script>
<!-- …Nav, Hero, etc. -->
<script type="text/babel" src="App.jsx"></script>
```

(Use the exact pinned versions from `index.html`.)

## Caveats

- The "skyline image" is loaded from `../../assets/skyline-w-chats.png` (relative path). If you move `index.html`, fix the URL in `styles.css` (`.hero { background: …url('../../assets/skyline-w-chats.png')… }`).
- The animation timeline is driven by `setTimeout`, not `requestAnimationFrame`. This is intentional — each transition is CSS-driven. The timers are cleaned up on unmount.
- This is a **prototype**, not production code. The widget is a marketing artifact; it doesn't actually call the W Chats API. For the real embedded widget, see the codebase.
