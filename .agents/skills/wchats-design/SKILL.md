---
name: wchats-design
description: Use this skill to generate well-branded interfaces and assets for W Chats, either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping in the "Hillbrow at Dusk" design system.
user-invocable: true
---

# W Chats Design Skill

W Chats is a multi-tenant RAG platform that lets non-technical small business owners ship a customer service agent in under 30 minutes. The visual identity — "Hillbrow at Dusk" — is derived from a photograph of the Johannesburg skyline at sunset: deep indigo sky, sunset coral, jacaranda lilac, tower cyan, building amber.

## Start here

1. **Read `README.md`** in this folder. It contains the full brand context, palette philosophy, content fundamentals (voice, casing, em-dash rhythm), visual foundations (glass discipline, motion, shadows), and iconography rules.
2. **Load `colors_and_type.css`** before any other styles — it carries the full token system plus semantic element defaults (h1–h5, p, body, scrollbar, selection).
3. **Look at `ui_kits/wchats/`** for the landing page as a reference implementation. Read `WorkflowCard.jsx` if you need to reproduce the build-pipeline → live-widget animation.

## Quick reference — the system in one paragraph

Dark palette anchored on `--bg-deep #0B0717`. Primary accent is **sunset coral** `#F4748C` (CTAs, italic Fraunces accents, focus rings). Secondary is **jacaranda lilac** `#B79AE0` (avatars, soft accents). Tertiary is **tower cyan** `#5EDFD3` — rare. Status is the conventional green/gold/red recalibrated for dark. Type is **Fraunces** (display, variable, use `opsz 144 + SOFT 30 upright / SOFT 100 italic`) + **Inter** (body) + **JetBrains Mono** (numbers, IDs, logs). Glass is used **surgically** — hero card, stat tiles, eyebrow pills. Solid `--surface-1` wins on dense data. Shadows are **violet-tinted, never neutral black**. Animation is **calm** — no spring overshoots, just 0.4s easings and a soft pulse for "live" dots. Sentence case for everything except UPPERCASE TRACKED micro-labels (0.12em).

## When the user invokes this skill

If the user invokes this without further direction, ask:

- What surface are they building? (Landing page · in-app screen · embedded widget · marketing asset · doc?)
- Production code or a throwaway mock/prototype?
- New flow or extending an existing page?
- Any constraints? (Has to embed the live widget? Has to match a specific competitor's pattern?)

Then act as an expert W Chats designer who outputs HTML artifacts or production code, depending on the need.

## What to copy from this skill

- `colors_and_type.css` — drop into any HTML head after Google Fonts.
- `assets/skyline-w-chats.png` — the literal hero background.
- `assets/logo-mark.svg`, `assets/wordmark.svg` — brand marks.
- `assets/icons/*.svg` — the Feather-style icon set used in the prototype.
- Any `ui_kits/wchats/*.jsx` component you want to reuse — they're framework-agnostic enough to lift directly.

## Rules to never break

1. **Never** redraw the skyline as a CSS gradient — use the actual PNG.
2. **Never** put glass on dense data UI (tables, lists, KB rows, code blocks). Solid `--surface-1` only.
3. **Never** mix coral and cyan together as co-equal accents — coral is primary, cyan is a rare highlight. One or two cyan touches per screen, max.
4. **Never** use neutral grey shadows. Always violet-tinted (`rgba(11,7,23,...)`).
5. **Never** title-case UI labels. Sentence case for content, UPPERCASE TRACKED for micro-labels.
6. **Never** use emoji in the W Chats voice. Emoji belongs *inside* the chat widget (the agent talking to a customer) and inside `📄` source/citation chips. Nowhere else.
7. **Always** respect `prefers-reduced-motion: reduce` — fall back to the final state.
