'use client'

/* =========================================================================
   Landing — `/` (UI-SPEC §6.1, ported from the Gotham design source's
   index.html).

   Shell A (bare / topnav, UI-SPEC §5.1-A) — the only page in the console
   that has no fixed rail. Mounts the three.js specimen (SceneMount) via a
   client-only, code-split dynamic import so the ~600KB `three` chunk never
   enters any other route's first-load JS (UI-SPEC §5.3 confinement rule).
   The gate demo below is a client-only marketing toggle (useGate()) — it
   does not call the backend and is not tied to a real agent.
   ========================================================================= */

import Link from 'next/link'
import dynamic from 'next/dynamic'
import type { SVGProps } from 'react'
import PageChrome, { type PageChromeOffsets } from './components/gotham/PageChrome'
import Btn from './components/gotham/Btn'
import Chip from './components/gotham/Chip'
import Ledger, { LedgerCell, LedgerColHead } from './components/gotham/Ledger'
import { useGate } from './components/gotham/GateProvider'

// SceneMount dynamically imports `three` itself inside a useEffect — this
// next/dynamic wrapper additionally guarantees the component is never
// server-rendered at all (ssr: false), so the landing route's SSR pass never
// touches `window`/WebGL (Pitfall 2).
const SceneMount = dynamic(() => import('./components/gotham/SceneMount'), { ssr: false })

const LANDING_OFFSETS: PageChromeOffsets = {
  tl: { left: 22, top: 22 },
  tr: { right: 22, top: 22 },
  bl: { left: 22, bottom: 22 },
  br: { right: 22, bottom: 22 },
}

const SCENARIOS = 64
const FAILS = 3

function CheckGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="15"
      height="15"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M3 8.5 6.4 12 13 4.5" />
    </svg>
  )
}

function CrossGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="15"
      height="15"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  )
}

const evidenceRows: Array<{
  scenario: string
  faithfulness: string
  relevancy: string
  verdict: 'pass' | 'fail'
}> = [
  { scenario: 'Refund window for a damaged item', faithfulness: '0.96', relevancy: '0.94', verdict: 'pass' },
  { scenario: 'Price for a service not in the catalogue', faithfulness: '0.41', relevancy: '0.88', verdict: 'fail' },
  { scenario: 'Booking a slot on a public holiday', faithfulness: '0.93', relevancy: '0.91', verdict: 'pass' },
  { scenario: 'Trading hours during load shedding', faithfulness: '0.95', relevancy: '0.97', verdict: 'pass' },
  { scenario: 'Handing an angry customer to a human', faithfulness: '0.89', relevancy: '0.92', verdict: 'pass' },
]

function GateDemo() {
  const { gate, setGate } = useGate()
  const shut = gate === 'blocked'

  return (
    <section className="section" id="gate" aria-labelledby="gate-label">
      <div className="section-head">
        <span className="label" id="gate-label">
          The gate
        </span>
        <span className="sec-note mono">agent.lindiwe-beauty</span>
      </div>

      <div className="gate-grid">
        <div>
          <div aria-live="polite">
            <h2 className="gate-line">
              {shut
                ? 'The gate is shut. This agent cannot reach a customer.'
                : 'The gate is open. This agent can meet a customer.'}
            </h2>
            <p className="gate-state">
              <Chip verdict={shut ? 'seal' : 'live'} dot>
                {shut ? 'Gate shut' : 'Gate open'}
              </Chip>
            </p>
          </div>

          <p className="voice gate-voice">
            A blocked gate is not a badge in the corner. It is the temperature of
            the room you are standing in.
          </p>

          <div className="gate-controls">
            <Btn variant="seal" disabled={shut} onClick={() => setGate('blocked')}>
              Simulate a critical finding
            </Btn>
            <Btn variant="ghost" disabled={!shut} onClick={() => setGate('open')}>
              Clear the finding
            </Btn>
          </div>
        </div>

        <div>
          <Ledger caption="The four signals the gate reads before it opens.">
            <thead>
              <tr>
                <LedgerColHead>Signal</LedgerColHead>
                <LedgerColHead numeric>Reading</LedgerColHead>
                <LedgerColHead className="vd">Verdict</LedgerColHead>
              </tr>
            </thead>
            <tbody>
              <tr>
                <LedgerCell>Evals pass rate</LedgerCell>
                <LedgerCell numeric>
                  <span className="num">0.94</span> <span className="qual">of 0.90 required</span>
                </LedgerCell>
                <LedgerCell className="vd">
                  <span className="glyph glyph-pass">
                    <CheckGlyph />
                    <span className="vh">Pass</span>
                  </span>
                </LedgerCell>
              </tr>
              <tr>
                <LedgerCell>Red team critical findings</LedgerCell>
                <LedgerCell numeric>
                  <span className="num">{shut ? '1' : '0'}</span>
                </LedgerCell>
                <LedgerCell className="vd">
                  <span className={`glyph ${shut ? 'glyph-fail' : 'glyph-pass'}`}>
                    {shut ? <CrossGlyph /> : <CheckGlyph />}
                    <span className="vh">{shut ? 'Fail' : 'Pass'}</span>
                  </span>
                </LedgerCell>
              </tr>
              <tr>
                <LedgerCell>Knowledge base</LedgerCell>
                <LedgerCell numeric>
                  <span className="num">34</span> <span className="qual">documents</span>
                </LedgerCell>
                <LedgerCell className="vd">
                  <span className="glyph glyph-pass">
                    <CheckGlyph />
                    <span className="vh">Pass</span>
                  </span>
                </LedgerCell>
              </tr>
              <tr>
                <LedgerCell>Soul saved</LedgerCell>
                <LedgerCell numeric>
                  <span className="num">2026-07-12</span>
                </LedgerCell>
                <LedgerCell className="vd">
                  <span className="glyph glyph-pass">
                    <CheckGlyph />
                    <span className="vh">Pass</span>
                  </span>
                </LedgerCell>
              </tr>
            </tbody>
          </Ledger>
        </div>
      </div>
    </section>
  )
}

export default function LandingPage() {
  const passing = SCENARIOS - FAILS

  return (
    <>
      <PageChrome offsets={LANDING_OFFSETS} mobileOffsets={LANDING_OFFSETS} skipTargetId="main" />

      <header className="topnav">
        <Link className="wordmark" href="/">
          <span>w.</span>chats
        </Link>
        <nav className="nav-links" aria-label="Primary">
          <a href="#">Docs</a>
          <a href="#">Pricing</a>
        </nav>
        <Link className="btn btn-primary" href="/agents">
          Open the console
        </Link>
      </header>

      <main id="main" className="page landing-page">
        {/* ── hero ─────────────────────────────────────────────────────── */}
        <section className="hero" aria-labelledby="h1">
          <div>
            <h1 id="h1">Every agent is verified before it meets a customer.</h1>
            <p className="lede">
              Ground it in your documents, score it against real scenarios, and let it
              through only when the evidence says it is ready.
            </p>

            <div className="cta">
              <Link className="btn btn-primary" href="/agents">
                Build your agent
              </Link>
              <Link className="btn btn-ghost" href="#gate">
                See the gate
              </Link>
            </div>

            <dl className="stats">
              <div>
                <dt className="label">Agents verified</dt>
                <dd className="num">248+</dd>
              </div>
              <div>
                <dt className="label">Median faithfulness</dt>
                <dd className="num">0.91</dd>
              </div>
              <div>
                <dt className="label">Critical findings shipped</dt>
                <dd className="num">0</dd>
              </div>
            </dl>
          </div>

          <div>
            <div id="scene" style={{ position: 'relative' }}>
              <SceneMount scenarios={SCENARIOS} fails={FAILS} />
            </div>
            <div className="scene-cap">
              <span className="label">The check</span>
              <span className="legend">
                <span className="mono">
                  <i className="dot dot-live" />
                  {passing} passing
                </span>
                <span className="mono">
                  <i className="dot dot-fail" />
                  {FAILS} failing
                </span>
              </span>
            </div>
          </div>
        </section>

        {/* ── the evidence ─────────────────────────────────────────────── */}
        <section className="section" aria-labelledby="ev-label">
          <div className="section-head">
            <span className="label" id="ev-label">
              The evidence
            </span>
            <span className="sec-note mono">suite 12 · 64 scenarios · run 2026-07-13</span>
          </div>

          <h2>Every scenario is filed, including the ones that fail.</h2>

          <Ledger caption="Five eval scenarios from the latest run, with faithfulness, relevancy and verdict.">
            <thead>
              <tr>
                <LedgerColHead>Scenario</LedgerColHead>
                <LedgerColHead numeric>Faithfulness</LedgerColHead>
                <LedgerColHead numeric>Relevancy</LedgerColHead>
                <LedgerColHead className="vd">Verdict</LedgerColHead>
              </tr>
            </thead>
            <tbody>
              {evidenceRows.map((row) => (
                <tr key={row.scenario}>
                  <LedgerCell>{row.scenario}</LedgerCell>
                  <LedgerCell numeric>{row.faithfulness}</LedgerCell>
                  <LedgerCell numeric>{row.relevancy}</LedgerCell>
                  <LedgerCell className="vd">
                    <Chip verdict={row.verdict}>{row.verdict === 'pass' ? 'Pass' : 'Fail'}</Chip>
                  </LedgerCell>
                </tr>
              ))}
            </tbody>
          </Ledger>

          <p className="voice filed">
            The failing scenario was not deleted. It was filed into the suite, and it
            will be asked of this agent on every run from now on.
          </p>
        </section>

        {/* ── the gate ──────────────────────────────────────────────────── */}
        <GateDemo />

        {/* ── three steps ──────────────────────────────────────────────── */}
        <section className="section" aria-labelledby="steps-label">
          <div className="section-head">
            <span className="label" id="steps-label">
              Three steps
            </span>
            <span className="sec-note mono">about 20 minutes</span>
          </div>

          <h2>From a folder of documents to an agent you can defend.</h2>

          <div className="steps rule-double">
            <div className="step">
              <span className="step-n mono">01</span>
              <h3>Ingest</h3>
              <p>Point us at your documents. Every chunk keeps a citation back to the page it came from.</p>
            </div>
            <div className="step">
              <span className="step-n mono">02</span>
              <h3>Evaluate</h3>
              <p>
                Sixty four scenarios are put to the agent. Faithfulness, relevancy, recall and precision, each
                scored on its own line.
              </p>
            </div>
            <div className="step">
              <span className="step-n mono">03</span>
              <h3>Deploy</h3>
              <p>The gate opens only when the evidence clears the bar you set. Then the widget goes live on your site.</p>
            </div>
          </div>
        </section>

        <footer className="foot">
          <span className="mono">w.chats</span>
          <span className="mono">&copy; {new Date().getFullYear()} W Chats</span>
        </footer>
      </main>

      <style>{`
        .topnav {
          position: relative; z-index: var(--z-strip);
          display: flex; align-items: center; gap: 24px;
          max-width: 1280px; margin: 0 auto; padding: 17px 40px;
          border-bottom: 1px solid var(--hairline);
        }
        .wordmark {
          font-family: var(--display); font-size: 16px; font-weight: 600;
          letter-spacing: -0.02em; text-decoration: none; color: var(--ink);
        }
        .wordmark span { color: var(--live); }
        .nav-links { display: flex; gap: 22px; margin-left: auto; }
        .nav-links a {
          font-size: 13px; color: var(--ink-2); text-decoration: none;
          padding: 4px 2px; border-radius: 3px;
          transition: color 140ms ease;
        }
        .nav-links a:hover { color: var(--ink); }

        .landing-page { padding-top: 0; }

        .hero {
          display: grid; grid-template-columns: 1.15fr 0.85fr;
          gap: 56px; align-items: center; padding: 46px 0 10px;
        }
        .hero h1 {
          font-size: clamp(26px, 3.4vw, 44px); line-height: 1.08;
          max-width: 15em; text-wrap: balance;
        }
        .lede { margin-top: 18px; color: var(--ink-2); font-size: 15.5px; max-width: 44ch; }
        .cta { display: flex; gap: 10px; margin-top: 26px; flex-wrap: wrap; }

        .stats {
          display: flex; flex-wrap: wrap; gap: 36px;
          margin: 36px 0 0; padding-top: 18px;
          border-top: 1px solid var(--hairline);
        }
        .stats > div { display: flex; flex-direction: column-reverse; gap: 5px; }
        .stats dd { margin: 0; font-size: 19px; color: var(--ink); line-height: 1.1; }

        #scene { position: relative; min-height: 460px; }
        .scene-cap {
          display: flex; align-items: center; justify-content: space-between;
          gap: 12px; margin-top: 12px; padding-top: 11px;
          border-top: 1px solid var(--hairline-soft);
        }
        .legend { display: flex; gap: 18px; font-size: 11.5px; color: var(--ink-2); }
        .legend .dot { display: inline-block; margin-right: 7px; vertical-align: 1px; }

        .sec-note { font-size: 11.5px; color: var(--ink-3); }
        .section h2 { font-size: 21px; margin-top: 10px; max-width: 30ch; }
        .section h2 + .ledger { margin-top: 20px; }
        .filed {
          margin-top: 18px; padding-top: 15px;
          border-top: 1px solid var(--hairline-soft);
          max-width: 74ch; font-size: 15px;
        }

        .gate-grid {
          display: grid; grid-template-columns: 1fr 1fr;
          gap: 52px; align-items: start; margin-top: 4px;
        }
        .gate-line { font-size: clamp(21px, 2.3vw, 27px); line-height: 1.16; max-width: 17ch; margin-top: 12px; }
        .gate-state { margin-top: 16px; }
        .gate-voice {
          margin-top: 18px; padding-top: 16px;
          border-top: 1px solid var(--hairline-soft);
          font-size: 16px; max-width: 46ch;
        }
        .gate-controls { display: flex; gap: 10px; margin-top: 22px; flex-wrap: wrap; }
        .qual { color: var(--ink-3); font-size: 11.5px; }
        .ledger td.vd, .ledger th.vd { text-align: right; }
        .glyph { display: inline-flex; }
        .glyph-pass { color: var(--pass); }
        .glyph-fail { color: var(--fail); }

        .steps { display: grid; grid-template-columns: repeat(3, 1fr); margin-top: 26px; }
        .step { position: relative; padding: 30px 34px 0 0; }
        .step::before {
          content: ''; position: absolute; top: 0; left: 0;
          width: 1px; height: 15px; background: var(--hairline-strong);
        }
        .step::after {
          content: ''; position: absolute; top: -3px; left: -2px;
          width: 5px; height: 5px; background: var(--hairline-strong);
        }
        .step-n { display: block; font-size: 11px; color: var(--ink-3); letter-spacing: 0.14em; }
        .step h3 { font-family: var(--display); font-size: 16px; font-weight: 500; margin: 7px 0 8px; }
        .step p { color: var(--ink-2); font-size: 13.5px; max-width: 34ch; }

        .foot {
          display: flex; align-items: center; justify-content: space-between;
          gap: 16px; flex-wrap: wrap;
          margin-top: 56px; padding-top: 18px;
          border-top: 1px solid var(--hairline);
          font-size: 11px; color: var(--ink-3);
        }

        @media (max-width: 940px) {
          .hero { grid-template-columns: 1fr; gap: 34px; padding-top: 32px; }
          .hero h1 { max-width: 16em; }
          #scene { min-height: 340px; }
          .gate-grid { grid-template-columns: 1fr; gap: 30px; }
          .gate-line { max-width: 24ch; }
          .steps { grid-template-columns: 1fr; }
          .step { padding: 26px 0 22px 0; }
          .step:not(:first-child) { border-top: 1px solid var(--hairline-soft); }
          .topnav { padding: 14px 20px; gap: 16px; }
          .nav-links { gap: 16px; }
        }
        @media (max-width: 560px) {
          .nav-links { display: none; }
          .stats { gap: 24px; }
        }
      `}</style>
    </>
  )
}
