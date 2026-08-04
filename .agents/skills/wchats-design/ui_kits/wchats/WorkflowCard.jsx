/* global React, Icon */
// Workflow card — Phase 1 (build pipeline) cross-fades to Phase 2+ (live widget).
// Mechanics copied from widget-animation-live.html, trimmed to phases 1-5,
// restyled to Hillbrow at Dusk. 19s loop.

const { useEffect, useRef, useState } = React;

const BUILD_STEPS = [
  { title: 'Provision',  subtitle: 'Dedicated tenant database ready' },
  { title: 'Configure',  subtitle: 'Soul + documents ingested' },
  { title: 'Test',       subtitle: 'Evals passed' },
  { title: 'Deploy',     subtitle: 'Embed widget live' },
];

const WorkflowCard = () => {
  const cardRef = useRef(null);
  const stepsPanelRef = useRef(null);
  const widgetPanelRef = useRef(null);
  const messagesRef = useRef(null);
  const typingRef = useRef(null);
  const inputRef = useRef(null);
  const inputTextRef = useRef(null);
  const inputCaretRef = useRef(null);

  // For reduced-motion fallback
  const [reduceMotion] = useState(() =>
    typeof window !== 'undefined' &&
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );

  // Animation engine
  useEffect(() => {
    if (reduceMotion) return; // We render the final state below if true

    let timers = [];
    let paused = false;
    let pauseStartTime = 0;

    const later = (fn, ms) => {
      const t = setTimeout(fn, ms);
      timers.push(t);
      return t;
    };
    const clearAllTimers = () => {
      timers.forEach(t => clearTimeout(t));
      timers = [];
    };

    const setStepState = (i, state) => {
      const card = cardRef.current?.querySelector(`.step-card[data-step="${i}"]`);
      const circle = card?.querySelector('.step-circle');
      if (!card || !circle) return;
      card.classList.remove('upcoming', 'active', 'done');
      circle.classList.remove('upcoming', 'active', 'done');
      card.classList.add(state);
      circle.classList.add(state);
      // Replace circle content with check if done
      if (state === 'done') {
        circle.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
      } else {
        circle.textContent = String(i + 1);
      }
    };

    const resetSteps = () => {
      BUILD_STEPS.forEach((_, i) => setStepState(i, 'upcoming'));
    };

    const showPanel = (which) => {
      const s = stepsPanelRef.current;
      const w = widgetPanelRef.current;
      if (s) {
        s.classList.toggle('hidden', which !== 'steps');
        s.style.opacity = which === 'steps' ? '1' : '0';
      }
      if (w) {
        w.classList.toggle('hidden', which !== 'widget');
        w.style.opacity = which === 'widget' ? '1' : '0';
      }
    };

    const showTyping = () => {
      typingRef.current?.classList.remove('hidden');
      if (typingRef.current && messagesRef.current) {
        messagesRef.current.appendChild(typingRef.current);
      }
      scrollMessages();
    };
    const hideTyping = () => typingRef.current?.classList.add('hidden');

    const scrollMessages = () => {
      const m = messagesRef.current;
      if (m) m.scrollTop = m.scrollHeight;
    };

    const addAgentMsg = (html) => {
      const el = document.createElement('div');
      el.className = 'msg-agent';
      el.innerHTML = html;
      messagesRef.current?.insertBefore(el, typingRef.current);
      scrollMessages();
      return el;
    };

    const addUserMsg = (text) => {
      const el = document.createElement('div');
      el.className = 'msg-user';
      el.textContent = text;
      messagesRef.current?.insertBefore(el, typingRef.current);
      scrollMessages();
      return el;
    };

    // Source chips
    let sourcesRowEl = null;
    const showSources = (sources) => {
      sourcesRowEl = document.createElement('div');
      sourcesRowEl.className = 'sources-row';
      sources.forEach(s => {
        const chip = document.createElement('span');
        chip.className = 'source-chip';
        chip.innerHTML = '\ud83d\udcc4 ' + s;
        sourcesRowEl.appendChild(chip);
      });
      messagesRef.current?.insertBefore(sourcesRowEl, typingRef.current);
      scrollMessages();
    };
    const hideSources = () => {
      if (sourcesRowEl && sourcesRowEl.parentNode) sourcesRowEl.parentNode.removeChild(sourcesRowEl);
      sourcesRowEl = null;
    };

    // Input typing
    const setInputText = (txt) => {
      if (inputTextRef.current) inputTextRef.current.textContent = txt;
      if (inputRef.current) {
        inputRef.current.classList.toggle('empty', !txt);
      }
    };
    const showCaret = () => { if (inputCaretRef.current) inputCaretRef.current.style.display = 'inline-block'; };
    const hideCaret = () => { if (inputCaretRef.current) inputCaretRef.current.style.display = 'none'; };
    const clearInput = () => { setInputText(''); hideCaret(); };

    const typeInput = (text, durationMs) => {
      showCaret();
      const perChar = Math.max(20, durationMs / text.length);
      for (let i = 0; i <= text.length; i++) {
        later(() => setInputText(text.slice(0, i)), i * perChar);
      }
    };

    const resetAll = () => {
      clearAllTimers();
      // Clear messages except typing
      if (messagesRef.current) {
        const kids = Array.from(messagesRef.current.children);
        kids.forEach(k => { if (k !== typingRef.current) messagesRef.current.removeChild(k); });
      }
      hideTyping();
      hideSources();
      clearInput();
      resetSteps();
      showPanel('steps');
    };

    const runSequence = () => {
      resetAll();
      showPanel('steps');

      // PHASE 1 — 4 build steps cycle (0 - 9.0s)
      // Each step: 2.0s active, then mark done. 4 steps = 8.0s. + 1s settle.
      BUILD_STEPS.forEach((_, i) => {
        later(() => setStepState(i, 'active'), i * 2000);
        later(() => setStepState(i, 'done'), i * 2000 + 2000);
      });

      // CROSS-FADE — 9.0s -> 9.6s
      later(() => showPanel('widget'), 9000);

      // PHASE 2 — Greeting (9.0 - 11.0s)
      later(() => showTyping(), 9400);
      later(() => {
        hideTyping();
        addAgentMsg("Hi \ud83d\udc4b I'm Maya, Lakewood Bakery's assistant. How can I help you today?");
      }, 10400);

      // PHASE 3 — User types (11.0 - 13.0s)
      later(() => typeInput('Delivery areas and hours?', 1500), 11200);
      later(() => {
        hideCaret();
        setInputText('');
        addUserMsg('Delivery areas and hours?');
      }, 13000);

      // PHASE 4 — Retrieval (13.0 - 15.0s)
      later(() => showTyping(), 13300);
      later(() => showSources(['Delivery Policy.pdf', 'Opening Hours.pdf']), 13800);
      later(() => { hideTyping(); hideSources(); }, 15000);

      // PHASE 5 — Grounded answer with citations (15.0 - 17.0s)
      later(() => {
        const answerEl = addAgentMsg(
          'We deliver to <strong>Northside</strong>, <strong>Eastpark</strong> &amp; <strong>Downtown</strong>. Mon\u2013Fri 9am\u20136pm \u00b7 Sat 9am\u20132pm. No Sunday delivery.'
        );
        const c = document.createElement('div');
        c.className = 'citations';
        c.innerHTML =
          '<span class="cite-chip">[1] Delivery Policy</span>' +
          '<span class="cite-chip">[2] Opening Hours</span>';
        answerEl.appendChild(c);
        scrollMessages();
      }, 15200);

      // HOLD 17.0 - 19.0s, then reset & loop
      later(() => runSequence(), 19000);
    };

    runSequence();

    // Pause on hover
    const cardEl = cardRef.current;
    const onEnter = () => {
      if (paused) return;
      paused = true;
      pauseStartTime = Date.now();
      // Simplest pause: clear timers; resume restarts from beginning.
      // We choose this over fine-grained resume since one loop is short.
      clearAllTimers();
    };
    const onLeave = () => {
      if (!paused) return;
      paused = false;
      runSequence();
    };
    cardEl?.addEventListener('mouseenter', onEnter);
    cardEl?.addEventListener('mouseleave', onLeave);

    return () => {
      clearAllTimers();
      cardEl?.removeEventListener('mouseenter', onEnter);
      cardEl?.removeEventListener('mouseleave', onLeave);
    };
  }, [reduceMotion]);

  // Static state for reduced motion: show the final widget
  if (reduceMotion) {
    return (
      <div className="demo-card" ref={cardRef}>
        <div className="demo-header">
          <span className="demo-title">
            <span className="header-dot"></span>
            <strong>build pipeline</strong>&nbsp;\u00b7&nbsp;agent.kgalema &nbsp;\u00b7&nbsp; v1
          </span>
        </div>
        <div className="demo-panels">
          <div className="demo-panel" ref={widgetPanelRef}>
            <div className="widget">
              <div className="widget-header">
                <div className="widget-status-dot"></div>
                <div className="widget-title">Bakery Assistant</div>
                <div className="widget-live-chip">LIVE</div>
              </div>
              <div id="messages">
                <div className="msg-agent">Hi 👋 I'm Maya, Lakewood Bakery's assistant. How can I help you today?</div>
                <div className="msg-user">Delivery areas and hours?</div>
                <div className="msg-agent">
                  We deliver to <strong>Northside</strong>, <strong>Eastpark</strong> &amp; <strong>Downtown</strong>. Mon–Fri 9am–6pm · Sat 9am–2pm. No Sunday delivery.
                  <div className="citations">
                    <span className="cite-chip">[1] Delivery Policy</span>
                    <span className="cite-chip">[2] Opening Hours</span>
                  </div>
                </div>
              </div>
              <div className="widget-input-bar">
                <div className="widget-input empty">Ask anything…</div>
                <button className="widget-send">→</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="demo-card" ref={cardRef}>
      <div className="demo-header">
        <span className="demo-title">
          <span className="header-dot"></span>
          <strong>build pipeline</strong>&nbsp;·&nbsp;agent.kgalema &nbsp;·&nbsp; v1
        </span>
      </div>

      <div className="demo-panels">
        {/* PHASE 1 — STEPS */}
        <div className="demo-panel" ref={stepsPanelRef} style={{ opacity: 1 }}>
          <div className="steps-stack">
            {BUILD_STEPS.map((s, i) => (
              <div key={i} className="step-card upcoming" data-step={i}>
                <div className="step-circle upcoming">{i + 1}</div>
                <div className="step-text">
                  <div className="step-title">{s.title}</div>
                  <div className="step-subtitle">{s.subtitle}</div>
                </div>
                <div className="step-blink"></div>
              </div>
            ))}
          </div>
        </div>

        {/* PHASE 2+ — WIDGET */}
        <div className="demo-panel hidden" ref={widgetPanelRef} style={{ opacity: 0 }}>
          <div className="widget" role="region" aria-label="Live customer chat demonstration">
            <div className="widget-header">
              <div className="widget-status-dot" aria-hidden="true"></div>
              <div className="widget-title">Bakery Assistant</div>
              <div className="widget-live-chip">LIVE</div>
            </div>
            <div id="messages" ref={messagesRef} aria-live="polite" aria-atomic="false">
              <div className="typing-bubble hidden" ref={typingRef} aria-hidden="true">
                <div className="typing-dot"></div>
                <div className="typing-dot"></div>
                <div className="typing-dot"></div>
              </div>
            </div>
            <div className="widget-input-bar">
              <div className="widget-input empty" ref={inputRef}>
                <span ref={inputTextRef}></span>
                <span className="caret" ref={inputCaretRef} style={{ display: 'none' }}></span>
              </div>
              <button className="widget-send" tabIndex="-1" aria-label="Send">→</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

window.WorkflowCard = WorkflowCard;
