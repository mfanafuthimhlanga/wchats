// opsCss.ts is the operations room's page-scoped stylesheet: classes with no
// equivalent in the shared globals.css Gotham port (they were page-local
// `<style>` rules in agent.html, not app.css). page.tsx injects it with the
// static dangerouslySetInnerHTML pattern agents/new/page.tsx uses, and
// tests-unit/finding-meta-render.spec.ts renders the Adversary region's
// finding lines against it in Chromium.
export const PAGE_CSS = `
  .ident { display: grid; justify-items: end; gap: 5px; text-align: right; }
  .ident-id { font-size: 12px; color: var(--ink-2); }
  .head-count { font-size: 12px; color: var(--ink-3); }

  .gatebar {
    margin-top: 22px;
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    padding: 11px 0 0;
  }
  .gatebar p { font-size: 13px; color: var(--ink-2); margin: 0; }
  .gatebar .mono { font-size: 12px; color: var(--ink-3); }

  .chans {
    display: grid; gap: 1px;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    background: var(--hairline-soft);
    border-top: 1px solid var(--hairline-soft);
    border-bottom: 1px solid var(--hairline-soft);
    margin-bottom: 22px;
  }
  .chan { background: var(--bg); padding: 14px 14px 12px; min-width: 0; }
  .chan-name {
    display: block;
    font-family: var(--mono); font-size: 9px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase; color: var(--ink-3);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .chan-read { display: flex; align-items: baseline; gap: 5px; margin-top: 8px; }
  .chan-val { font-size: 19px; color: var(--ink); line-height: 1.2; }
  .chan-untracked { font-family: var(--mono); font-size: 11px; color: var(--ink-3); }
  .chan-thr { margin-top: 3px; font-family: var(--mono); font-size: 10px; color: var(--ink-3); }

  .scroll-x { overflow-x: auto; }
  .ledger th.verdict, .ledger td.verdict { text-align: right; }

  .sev { display: flex; flex-wrap: wrap; gap: 22px; margin-bottom: 18px; }
  .sev-cell { display: grid; gap: 2px; }
  .sev-n { font-size: 20px; color: var(--ink); }
  .sev-cell[data-hot="true"] .sev-n { color: var(--seal-hot); }

  .critical {
    margin-top: 18px; margin-bottom: 18px;
    background: var(--seal-dim);
    border: 1px solid color-mix(in oklch, var(--seal) 32%, transparent);
    border-radius: var(--r-panel);
    padding: 14px 16px;
    /* flex-start, not center (23-09 adversarial review): the description
       column grows with its meta, evidence and re-test lines, and centering
       the chip and the Re-test button against that taller sibling floats
       them mid-row. Top-aligned reads correctly at every height. */
    display: flex; align-items: flex-start; gap: 14px; flex-wrap: wrap;
  }
  .critical p { flex: 1; min-width: 220px; font-size: 13.5px; margin: 0; }
  .critical .mono { font-size: 11px; color: var(--ink-2); }

  /* A finding's meta line, the evidence sentence under it and the re-test
     sentence under that (FindingMeta.tsx). The meta wraps anywhere so a long
     attack vector cannot push the page wider than a phone. The evidence and
     re-test lines read --ink-2 in the banner and the list alike; .critical
     .mono above lifts only the banner's meta line. */
  .finding-meta { font-size: 11px; color: var(--ink-3); overflow-wrap: anywhere; }
  .finding-evidence, .finding-retest { display: block; margin-top: 4px; font-size: 12px; color: var(--ink-2); }

  .foot-note { margin-top: 10px; font-size: 11.5px; color: var(--ink-3); }
  .prompt-acts { margin-top: 18px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .judge-alert-chip { margin-bottom: 14px; }

  /* Staged-confirm shape (OD-3, 23-01-PLAN.md § Open Decisions Resolved).
     These five rules exist ONLY in deploy/page.tsx's own PAGE_CSS (lines
     2970-2974), and globals.css has none of them, so a staged confirmation
     block rendered in this room would be unstyled without a copy here.
     Ported verbatim rather than lifted into the shared stylesheet: lifting
     would require editing deploy/page.tsx, and this phase's roadmap entry
     states plainly it shares no file with the phase that produced it. A
     gate (23-06's Task 2 verify) asserts these two blocks stay textually
     identical, so the duplication is checked, not merely hoped over. */
  .cap-confirm { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--hairline-soft); }
  .cap-confirm-q { font-size: 13px; line-height: 1.5; color: var(--ink); }
  .cap-confirm-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
  .cap-confirm-actions .btn { flex: none; }
  .cap-confirm-actions .btn:first-child { border-color: var(--hairline-strong); }

  /* The bench's two-pane layout (WIRE-01, 23-08). Net-new: Phase 20 shipped
     this region as an empty state, so no two-pane shell was ever built:
     neither globals.css nor this file's own prior rules have one. Named
     distinctly from deploy/page.tsx's own .bench grid, a different
     two-column layout for a different page that this page never loads
     alongside; sharing the name would be a trap for the next reader even
     though the two never collide at runtime. Both panes get a zero
     minimum width so a long unbroken customer-turn string cannot force
     the grid wider than its container, the specific mechanism behind
     horizontal overflow in a grid, and the three-viewport overflow check
     is an existing gate on this repository. The sheet is bounded and
     independently scrollable so a long trace list can never push the
     enlarger off screen. */
  .bench-panes {
    display: grid;
    grid-template-columns: minmax(0, 340px) minmax(0, 1fr);
    gap: 24px;
    align-items: start;
  }
  .bench-sheet {
    min-width: 0;
    max-height: 560px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .bench-enlarger {
    min-width: 0;
    padding-left: 24px;
    border-left: 1px solid var(--hairline-soft);
  }
  @media (max-width: 900px) {
    .bench-panes { grid-template-columns: minmax(0, 1fr); }
    .bench-sheet { max-height: 320px; }
    .bench-enlarger { padding-left: 0; border-left: none; margin-top: 20px; }
  }

  @media (max-width: 720px) {
    .page-head .row { flex-direction: column; }
    .ident { justify-items: start; text-align: left; }
  }
`
