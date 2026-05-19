"""Phase 4 UI screenshot script for gsd-ui-review audit."""
import subprocess
import time
import sys
import os
import socket
from pathlib import Path
from playwright.sync_api import sync_playwright

WIDGET_PORT = 5173
ADMIN_PORT  = 3001
OUT_DIR     = Path(__file__).parent.parent / ".planning" / "phases" / "04-reasoning-engine-widget" / "screenshots"
WIDGET_ROOT = Path(__file__).parent.parent / "apps" / "widget"
ADMIN_ROOT  = Path(__file__).parent.parent / "apps" / "admin"
DEMO_HTML   = Path(__file__).parent.parent / "apps" / "demo" / "index.html"

def port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False

def wait_for_port(port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(1)
    return False

def start_server(cmd, cwd, port):
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    )
    print(f"  Started PID {proc.pid}, waiting for port {port}...")
    ready = wait_for_port(port)
    if not ready:
        proc.kill()
        raise RuntimeError(f"Port {port} never opened (cmd={cmd!r})")
    print(f"  Port {port} ready.")
    return proc

def take_screenshots():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    procs = []

    print("Starting widget dev server...")
    procs.append(start_server(f"npm run dev -- --port {WIDGET_PORT}", WIDGET_ROOT, WIDGET_PORT))

    print("Starting admin dev server...")
    procs.append(start_server(f"npx next dev -p {ADMIN_PORT}", ADMIN_ROOT, ADMIN_PORT))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            # --- 1. Widget empty state ---
            print("Screenshotting widget...")
            page.goto(f"http://localhost:{WIDGET_PORT}/index.html?agent_id=demo&api=http://localhost:8000")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT_DIR / "01-widget-empty.png"), full_page=False)
            print("  saved 01-widget-empty.png")

            # Widget with simulated messages (inject via evaluate)
            page.evaluate("""() => {
                // Inject a fake message exchange into the scroll area for visual audit
                const scroll = document.querySelector('.scroll-area');
                if (!scroll) return;
                scroll.innerHTML = `
                  <div>
                    <div class="message-bubble agent">Hello! I'm a Veridian agent. How can I help you today?</div>
                    <div class="citation-row">
                      <div class="citation-item">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                        Based on: Bella Vista Menu, Section 3
                        <a href="#" class="view-link">VIEW</a>
                      </div>
                    </div>
                  </div>
                  <div>
                    <div class="message-bubble user">What are your latte sizes?</div>
                  </div>
                  <div class="typing-indicator" aria-label="Agent is typing">
                    <span class="dot"></span><span class="dot"></span><span class="dot"></span>
                  </div>
                `;
            }""")
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT_DIR / "02-widget-messages.png"), full_page=False)
            print("  saved 02-widget-messages.png")

            # Widget narrow (380px — exact spec size)
            page2 = browser.new_page(viewport={"width": 380, "height": 600})
            page2.goto(f"http://localhost:{WIDGET_PORT}/index.html?agent_id=demo&api=http://localhost:8000")
            page2.wait_for_load_state("networkidle")
            page2.wait_for_timeout(1000)
            page2.screenshot(path=str(OUT_DIR / "03-widget-380x600.png"), full_page=False)
            print("  saved 03-widget-380x600.png")
            page2.close()

            # Escalation panel
            page.evaluate("""() => {
                const scroll = document.querySelector('.scroll-area');
                if (!scroll) return;
                const esc = document.createElement('div');
                esc.className = 'escalation-panel';
                esc.innerHTML = `
                  <div class="escalation-header">Escalated to Human</div>
                  <p>Reason: Query outside agent scope</p>
                  <form>
                    <input type="text" placeholder="Your name" />
                    <input type="email" placeholder="Your email" />
                    <button type="submit">Send my details</button>
                  </form>`;
                scroll.appendChild(esc);
            }""")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT_DIR / "04-widget-escalation.png"), full_page=False)
            print("  saved 04-widget-escalation.png")

            # --- 2. Demo page ---
            print("Screenshotting demo page...")
            demo_url = DEMO_HTML.as_uri()
            page.goto(demo_url)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT_DIR / "05-demo-full.png"), full_page=True)
            print("  saved 05-demo-full.png")
            page.screenshot(path=str(OUT_DIR / "06-demo-hero.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 700})
            print("  saved 06-demo-hero.png")
            page.screenshot(path=str(OUT_DIR / "07-demo-metrics-trust.png"), clip={"x": 0, "y": 700, "width": 1280, "height": 600})
            print("  saved 07-demo-metrics-trust.png")

            # --- 3. Admin Soul Editor ---
            print("Screenshotting admin soul editor...")
            page.goto(f"http://localhost:{ADMIN_PORT}/agents/demo-agent/soul")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            page.screenshot(path=str(OUT_DIR / "08-admin-soul-full.png"), full_page=True)
            print("  saved 08-admin-soul-full.png")
            page.screenshot(path=str(OUT_DIR / "09-admin-soul-viewport.png"), full_page=False)
            print("  saved 09-admin-soul-viewport.png")

            # Admin — scroll to bottom for save section
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT_DIR / "10-admin-soul-bottom.png"), full_page=False)
            print("  saved 10-admin-soul-bottom.png")

            # Admin at 1100px (collapse breakpoint)
            page3 = browser.new_page(viewport={"width": 1099, "height": 900})
            page3.goto(f"http://localhost:{ADMIN_PORT}/agents/demo-agent/soul")
            page3.wait_for_load_state("networkidle")
            page3.wait_for_timeout(2000)
            page3.screenshot(path=str(OUT_DIR / "11-admin-soul-narrow.png"), full_page=False)
            print("  saved 11-admin-soul-narrow.png")
            page3.close()

            browser.close()

    finally:
        for proc in procs:
            proc.kill()
        print("Servers stopped.")

    print(f"\nAll screenshots saved to: {OUT_DIR}")

if __name__ == "__main__":
    take_screenshots()
