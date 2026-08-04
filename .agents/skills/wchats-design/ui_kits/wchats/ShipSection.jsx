/* global React, Icon */

const FEATURES = [
  { icon: 'shield-check', title: 'Pre-deployment checklist', desc: 'Eval pass rates, red team severity, latency, cost and corpus coverage all clear before the deploy button is even live.' },
  { icon: 'shield',       title: 'Per-tenant data isolation', desc: 'Dedicated Neon project per business. No shared vector store. Eval branches off live data without duplicating it.' },
  { icon: 'clock',        title: 'Continuous post-deploy monitoring', desc: 'Scheduled evals, weekly red team runs, monthly graph-RAG insights digest. You get a weekly brief, not a pager.' },
  { icon: 'user-check',   title: 'Verified knowledge layer', desc: 'Eval-passing answers and approved production responses cache as canonical Q\u0026A. Cost per query drops 40%+ by month three.' },
];

const ShipSection = () => (
  <section className="ship-section">
    <div className="ship-grid">
      <div className="ship-copy">
        <h2 className="ship-title">Agents that <em>earn</em><br />the right to ship.</h2>
        <p className="ship-desc">
          Most chatbot platforms hand you a widget and assume you have engineers
          waiting in the wings. W Chats handles what those engineers would do — and
          then it keeps doing it, after the agent is live, on your behalf.
        </p>
      </div>

      <div className="ship-list">
        {FEATURES.map((f, i) => (
          <div key={i} className="ship-item">
            <div className="ship-item-icon"><Icon name={f.icon} size={16} strokeWidth={2.2} /></div>
            <div>
              <div className="ship-item-title">{f.title}</div>
              <div className="ship-item-desc">{f.desc}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  </section>
);

window.ShipSection = ShipSection;
