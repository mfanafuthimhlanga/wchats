"""Phase 4 UI screenshot script — servers already running."""
from pathlib import Path
from playwright.sync_api import sync_playwright

WIDGET_URL = "http://localhost:5173/index.html?agent_id=demo&api=http://localhost:8000"
ADMIN_URL  = "http://localhost:3001/agents/demo-agent/soul"
DEMO_HTML  = Path(__file__).parent.parent / "apps" / "demo" / "index.html"
OUT_DIR    = Path(__file__).parent.parent / ".planning" / "phases" / "04-reasoning-engine-widget" / "screenshots"

OUT_DIR.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # ── 1. Widget at exact spec dimensions (380×600) ─────────────────────────
    print("Widget screenshots...")
    w = browser.new_page(viewport={"width": 380, "height": 600})
    w.goto(WIDGET_URL)
    w.wait_for_load_state("networkidle")
    w.wait_for_timeout(1200)
    w.screenshot(path=str(OUT_DIR / "01-widget-empty-state.png"))
    print("  01-widget-empty-state.png")

    # Inject a realistic chat exchange for visual fidelity check
    w.evaluate("""() => {
        const scroll = document.querySelector('.scroll-area');
        if (!scroll) return;
        scroll.innerHTML = `
          <div>
            <div class="message-bubble agent">
              Hi! I'm the Bella Vista assistant. Ask me anything about our menu, hours, or specials.
            </div>
            <div class="citation-row">
              <div class="citation-item">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
                Based on: <strong>Bella Vista Menu, Section 2</strong>
                <a href="#" class="view-link">VIEW</a>
              </div>
            </div>
          </div>
          <div>
            <div class="message-bubble user">What latte sizes do you offer?</div>
          </div>
          <div class="typing-indicator" aria-label="Agent is typing">
            <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          </div>
        `;
    }""")
    w.wait_for_timeout(400)
    w.screenshot(path=str(OUT_DIR / "02-widget-chat-with-typing.png"))
    print("  02-widget-chat-with-typing.png")

    # Add tool call label
    w.evaluate("""() => {
        const scroll = document.querySelector('.scroll-area');
        if (!scroll) return;
        const tc = document.createElement('div');
        tc.className = 'tool-call-label';
        tc.innerHTML = '<span class="dot"></span><code>retrieve(\\"latte sizes\\")</code>';
        scroll.appendChild(tc);
    }""")
    w.wait_for_timeout(300)
    w.screenshot(path=str(OUT_DIR / "03-widget-tool-call-state.png"))
    print("  03-widget-tool-call-state.png")

    # Escalation panel
    w.evaluate("""() => {
        const scroll = document.querySelector('.scroll-area');
        if (!scroll) return;
        const esc = document.createElement('div');
        esc.className = 'escalation-panel';
        esc.setAttribute('role', 'dialog');
        esc.innerHTML = `
          <div class="escalation-header">Escalated to Human</div>
          <p style="font-size:12px;margin:4px 0 8px;">I've flagged this for our team. Expect a reply within 24 hours.</p>
          <form style="display:flex;flex-direction:column;gap:6px;">
            <input type="text" placeholder="Your name" />
            <input type="email" placeholder="Your email" />
            <button type="submit">Send my details</button>
          </form>`;
        scroll.appendChild(esc);
    }""")
    w.wait_for_timeout(300)
    w.screenshot(path=str(OUT_DIR / "04-widget-escalation-panel.png"))
    print("  04-widget-escalation-panel.png")

    # Error state
    w.evaluate("""() => {
        const scroll = document.querySelector('.scroll-area');
        if (!scroll) return;
        const err = document.createElement('div');
        err.className = 'error-msg';
        err.setAttribute('role', 'alert');
        err.textContent = 'Something went wrong. Please try again.';
        scroll.appendChild(err);
    }""")
    w.wait_for_timeout(300)
    w.screenshot(path=str(OUT_DIR / "05-widget-error-state.png"))
    print("  05-widget-error-state.png")

    w.close()

    # ── 2. Demo page ─────────────────────────────────────────────────────────
    print("Demo page screenshots...")
    d = browser.new_page(viewport={"width": 1280, "height": 900})
    d.goto(DEMO_HTML.as_uri())
    d.wait_for_load_state("domcontentloaded")
    d.wait_for_timeout(1800)  # let fonts load
    d.screenshot(path=str(OUT_DIR / "06-demo-full-page.png"), full_page=True)
    print("  06-demo-full-page.png")
    d.screenshot(path=str(OUT_DIR / "07-demo-hero-viewport.png"), full_page=False)
    print("  07-demo-hero-viewport.png")
    page_h = d.evaluate("document.body.scrollHeight")
    footer_y = max(0, page_h - 300)
    d.evaluate(f"window.scrollTo(0, {footer_y})")
    d.wait_for_timeout(300)
    d.screenshot(path=str(OUT_DIR / "08-demo-footer.png"), full_page=False)
    print("  08-demo-footer.png")
    d.close()

    # ── 3. Admin Soul Editor ─────────────────────────────────────────────────
    print("Admin Soul Editor screenshots...")
    a = browser.new_page(viewport={"width": 1440, "height": 900})
    a.goto(ADMIN_URL, timeout=60000)
    a.wait_for_load_state("domcontentloaded", timeout=60000)
    a.wait_for_timeout(4000)
    a.screenshot(path=str(OUT_DIR / "09-admin-soul-top.png"), full_page=False)
    print("  09-admin-soul-top.png")
    a.screenshot(path=str(OUT_DIR / "10-admin-soul-full.png"), full_page=True)
    print("  10-admin-soul-full.png")

    # Scroll to save section
    a.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    a.wait_for_timeout(300)
    a.screenshot(path=str(OUT_DIR / "11-admin-soul-save-section.png"), full_page=False)
    print("  11-admin-soul-save-section.png")

    # Narrow viewport (below 1100px collapse point)
    a.set_viewport_size({"width": 1099, "height": 900})
    a.evaluate("window.scrollTo(0, 0)")
    a.wait_for_timeout(400)
    a.screenshot(path=str(OUT_DIR / "12-admin-soul-1099px.png"), full_page=False)
    print("  12-admin-soul-1099px.png")
    a.close()

    browser.close()

print(f"\nAll screenshots in: {OUT_DIR}")
