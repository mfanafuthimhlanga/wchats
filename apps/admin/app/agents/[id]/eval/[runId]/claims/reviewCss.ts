// reviewCss.ts is the claims review's page-scoped stylesheet, the same
// dangerouslySetInnerHTML pattern as eval/page.tsx. ClaimsReview.tsx injects it;
// tests-unit/claims-review-css.spec.ts renders it in Chromium and reads each
// card reason's colour. Every colour is a token from globals.css; the two hues
// are the two answers.
export const PAGE_CSS = `
  .review .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; padding-bottom: 14px; border-bottom: 1px solid var(--hairline); margin-top: -12px; }
  .review .keys { font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); }
  .review .keys b { display: inline-block; border: 1px solid var(--hairline-strong); padding: 0 5px; border-radius: 2px; color: var(--ink-2); font-weight: 400; }
  .review .progress { margin-left: auto; display: flex; align-items: center; gap: 10px; }
  .review .cells { display: flex; flex-wrap: wrap; gap: 2px; max-width: 366px; }
  .review .cell { display: block; width: 6px; height: 10px; border: 1px solid var(--ink-3); border-radius: 1px; }
  .review .cell.full { background: var(--live); border-color: transparent; }
  .review .cell.full.yes { background: var(--pass); }
  .review .cell.full.no { background: var(--fail); }
  .review .cell.here { border-color: var(--live-hot); box-shadow: 0 0 0 1px var(--live-hot); }
  .review .count { font-size: 12px; color: var(--ink-2); }
  .review .save { font-size: 10.5px; color: var(--ink-3); min-width: 9ch; }
  .review .save.warn { color: var(--fail); }
  .review .note { margin-top: 14px; font-size: 13px; color: var(--ink-2); }
  .review .note.warn { color: var(--fail); }

  .review .bench { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0 18px; }
  .review .answer { padding-block: 18px 24px; display: flex; flex-direction: column; gap: 20px; min-width: 0; }
  .review .block { display: flex; flex-direction: column; gap: 6px; }
  .review .eyebrow { font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-3); font-weight: 700; }
  .review .question { font-family: var(--display); font-size: 18px; line-height: 1.4; letter-spacing: -0.01em; max-width: 68ch; text-wrap: balance; }
  .review .question:focus { outline: none; }
  .review .response { max-width: 78ch; font-size: 14px; line-height: 1.6; background: var(--surface); padding: 12px 16px; border-radius: var(--r-control); overflow-wrap: anywhere; }
  .review .sent { display: block; border-left: 3px solid transparent; padding: 2px 0 2px 10px; }
  .review .sent + .sent { margin-top: 2px; }
  .review .sent.para { margin-top: 10px; }
  .review .sent.li, .review .sent.cont { padding-left: 24px; }
  .review .sent.li { text-indent: -14px; }
  .review .sent .bu { color: var(--ink-3); }
  .review .sent.t-bone { border-left-color: var(--live); }
  .review .sent.t-grey { border-left-color: var(--ink-3); }
  .review .sent.t-fail { border-left-color: var(--fail); }
  .review .sent[data-s] { cursor: pointer; }
  .review .sent[data-s]:hover, .review .sent.on { background: var(--surface-2); }
  .review .sent.code { font-family: var(--mono); font-size: 11.5px; line-height: 1.6; white-space: pre-wrap; overflow-x: auto; background: var(--well); padding: 8px 0 8px 10px; margin-top: 8px; }
  .review .sent.cite { font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); white-space: pre-wrap; margin-top: 12px; }
  .review .response code { font-family: var(--mono); font-size: 0.88em; background: var(--surface-2); padding: 0 3px; border-radius: 2px; }
  .review .legend { font-size: 11px; line-height: 1.5; color: var(--ink-3); }
  .review .legend b { font-weight: 600; }

  .review .ctxcol { min-width: 0; display: flex; flex-direction: column; gap: 6px; padding-block: 18px 12px; }
  .review .ctxnote { font-size: 11.5px; color: var(--fail); }
  .review .ctxpane { min-height: 0; max-height: 70vh; overflow-y: auto; background: var(--well); border: 1px solid var(--hairline); border-radius: var(--r-control); padding: 10px 14px 10px 8px; }
  .review .pw { margin: 0 0 8px; padding-left: 9px; border-left: 3px solid transparent; font-size: 12.5px; line-height: 1.6; color: var(--ink-2); white-space: pre-wrap; overflow-wrap: anywhere; }
  .review .pw:last-child { margin-bottom: 0; }
  .review .pw.lit { border-left-color: var(--live); color: var(--ink); }
  .review .pw.empty { color: var(--ink-3); }
  .review mark { background: var(--live-dim); color: var(--live-hot); border-radius: 1px; padding: 0 1px; }

  .review .claims { display: flex; flex-direction: column; gap: 12px; padding-block: 18px 40px; }
  .review .rubric { color: var(--ink-2); font-size: 12.5px; line-height: 1.5; }
  .review .card { display: flex; flex-direction: column; gap: 10px; background: var(--surface); border: 1px solid var(--hairline-strong); border-radius: var(--r-panel); padding: 14px 16px; cursor: default; }
  .review .card.active { border-color: var(--live-hot); box-shadow: inset 0 0 0 1px var(--live-hot); }
  .review .card h3 { font-size: 13px; color: var(--ink-2); display: flex; align-items: center; gap: 8px; }
  .review .statement { font-size: 15.5px; }
  .review .overlap { font-size: 10.5px; line-height: 1.5; color: var(--ink-2); }
  /* the tint decides a reason's colour, so these come after .overlap at the same specificity */
  .review .k-bone { color: var(--live); }
  .review .k-grey { color: var(--ink-2); }
  .review .k-fail { color: var(--fail); }
  .review .k-none { color: var(--ink-2); }
  .review .seg { display: flex; gap: 6px; }
  .review .vbtn { flex: 1; min-width: 0; font-family: var(--mono); font-size: 12px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; padding: 8px 0; background: transparent; border: 1px solid var(--hairline-strong); color: var(--ink-2); border-radius: var(--r-control); cursor: pointer; }
  .review .vbtn:hover { color: var(--ink); background: var(--surface-2); }
  .review .vbtn.yes.on, .review .vbtn.yes.on:hover { background: var(--pass); border-color: var(--pass); color: var(--live-ink); }
  .review .vbtn.no.on, .review .vbtn.no.on:hover { background: var(--fail); border-color: var(--fail); color: var(--live-ink); }
  .review .vbtn[disabled] { opacity: 0.35; cursor: default; }
  .review .navbtns { display: flex; gap: 6px; }
  .review .claims .navbtns .btn { flex: 1; justify-content: center; }
  .review :focus-visible { outline: 2px solid var(--live-hot); outline-offset: 2px; }

  .review .strip { display: none; position: sticky; bottom: 0; z-index: var(--z-strip); flex-direction: column; gap: 6px; margin: 0 -40px; padding: 8px 16px; background: var(--surface); border-top: 1px solid var(--hairline-strong); }
  .review .strow { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .review .strip .slabel { font-size: 11px; font-weight: 700; color: var(--ink-2); flex: none; }
  .review .sstatement { font-size: 13.5px; line-height: 1.35; min-width: 0; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .review .strip .vbtn { padding: 6px 0; font-size: 11px; }
  .review .strip .navbtns { margin-left: 4px; gap: 4px; }
  .review .strip .btn { padding: 6px 10px; }

  @media (max-width: 860px) { .review .keys, .review .card .kbd { display: none; } }
  @media (max-width: 1179px) {
    .review.page { padding-bottom: 0; }
    .review .strip { display: flex; }
    .review .claims .navbtns { display: none; }
  }
  @media (max-width: 900px) { .review .strip { margin: 0 -20px; bottom: 56px; } }
  @media (min-width: 1180px) {
    .review .bench { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 300px; }
    .review .ctxcol, .review .claims { position: sticky; top: 12px; align-self: start; max-height: calc(100vh - 24px); }
    .review .claims { overflow-y: auto; }
    .review .ctxpane { max-height: calc(100vh - 78px); }
  }
  @media (prefers-reduced-motion: no-preference) { .review .vbtn, .review .btn { transition: background 120ms, color 120ms; } }
`
