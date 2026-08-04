/* global React, Button, Icon, WorkflowCard */

const Hero = () => {
  return (
    <section className="hero">
      <div className="hero-content">
        {/* Left — copy */}
        <div className="hero-copy">
          <h1 className="hero-headline">
            Ship a customer support agent that is <em>defensible</em> — grounded, evaluated, and red-teamed before it goes live.
          </h1>

          <p className="hero-sub">
            W Chats wires a <strong>Claude Agent SDK</strong> reasoning engine to your
            business documents, evaluates every answer, and ships a <strong>20kb widget</strong>
            {' '}for any page.
          </p>

          <div className="hero-cta-row">
            <Button variant="primary">
              Build your agent
              <Icon name="arrow-right" size={12} strokeWidth={2.5} />
            </Button>
            <Button variant="ghost">
              <Icon name="play" size={11} strokeWidth={2.2} />
              Watch the build
              <span className="mono" style={{ color: 'var(--text-4)', fontSize: 10, marginLeft: 4 }}>2:18</span>
            </Button>
          </div>

          <div className="hero-trust">
            <div className="trust-stat">
              <span className="trust-num">&lt;30<span className="unit">min</span></span>
              <span className="trust-lbl">Signup to deployed</span>
            </div>
            <div className="trust-sep"></div>
            <div className="trust-stat">
              <span className="trust-num">&gt;0.85</span>
              <span className="trust-lbl">Faithfulness target</span>
            </div>
            <div className="trust-sep"></div>
            <div className="trust-stat">
              <span className="trust-num">0<span className="unit">crit</span></span>
              <span className="trust-lbl">Red team threshold</span>
            </div>
          </div>
        </div>

        {/* Right — workflow animation */}
        <div className="hero-demo">
          <WorkflowCard />
        </div>
      </div>
    </section>
  );
};

window.Hero = Hero;
