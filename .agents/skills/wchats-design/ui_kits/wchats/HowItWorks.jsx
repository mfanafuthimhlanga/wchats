/* global React, Icon */

const HOW_STEPS = [
  { num: 'Step 01', icon: 'shield', title: 'Provision', desc: 'A dedicated tenant database spins up per business. Per-business isolation, no shared vector store. Evaluation branches off live data without duplicating it.' },
  { num: 'Step 02', icon: 'upload', title: 'Configure', desc: 'Name your agent and give it the soul. Drop in PDFs, sheets, websites, FAQs — structure-aware parsing keeps tables, lists and headings intact.' },
  { num: 'Step 03', icon: 'bolt',   title: 'Test',      desc: 'A generated eval suite runs against your agent. Pass rates, faithfulness, latency and cost surface as you watch. Red team finds the gaps.' },
  { num: 'Step 04', icon: 'code',   title: 'Deploy',    desc: 'Pre-deployment checklist clears. You get a snippet. Paste it on your site. Weekly red team and drift detection run automatically.' },
];

const HowItWorks = () => (
  <section className="how-section">
    <header className="how-header">
      <div className="how-eyebrow"><span>How it works</span></div>
      <h2 className="how-title">Four steps. <em>One pipeline.</em><br />No code.</h2>
      <p className="how-desc">
        You create your assistant, provide it information about your business. The platform
        handles parsing, retrieval, evaluation, red teaming, and continuous monitoring.
      </p>
    </header>

    <div className="how-grid">
      {HOW_STEPS.map((s, i) => (
        <div key={i} className="how-card">
          <div className="how-num">{s.num}</div>
          <div className="how-icon"><Icon name={s.icon} size={22} /></div>
          <h3 className="how-card-title">{s.title}</h3>
          <p className="how-card-desc">{s.desc}</p>
        </div>
      ))}
    </div>
  </section>
);

window.HowItWorks = HowItWorks;
