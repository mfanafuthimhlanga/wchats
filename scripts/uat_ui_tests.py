"""
UAT UI Tests for Phase 4.2 — Plans 01 & 02
Tests: root route, sign-up page, /agents auth gate, CSS tokens
"""
from playwright.sync_api import sync_playwright
import sys, re

results = {}

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        executable_path=r"C:\Users\Bantu\AppData\Local\ms-playwright\chromium-1200\chrome-win64\chrome.exe"
    )
    context = browser.new_context()

    # ── Test 1: "/" is NOT auth-gated ──────────────────────────────────────────
    page = context.new_page()
    page.goto("http://localhost:3000/", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2000)
    final_url = page.url
    title = page.title()
    page.screenshot(path="/tmp/uat_root.png", full_page=True)
    # PASS: did NOT land on /sign-in or /sign-up
    redirected_to_auth = "/sign-in" in final_url or "/sign-up" in final_url
    results["T1_root_not_auth_gated"] = {
        "pass": not redirected_to_auth,
        "final_url": final_url,
        "title": title,
        "screenshot": "/tmp/uat_root.png",
        "note": "Redirected to auth" if redirected_to_auth else "Stayed at / (404 or landing page OK)",
    }
    page.close()

    # ── Test 2: /sign-up loads Clerk form ──────────────────────────────────────
    page = context.new_page()
    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.goto("http://localhost:3000/sign-up", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(3000)
    page.screenshot(path="/tmp/uat_signup.png", full_page=True)
    final_url = page.url
    # Look for email/password input fields or the Clerk widget container
    email_input = page.locator("input[type='email'], input[name='identifier'], input[name='emailAddress']").count()
    clerk_card = page.locator(".cl-card, .cl-rootBox, [data-localization-key]").count()
    has_form = email_input > 0 or clerk_card > 0
    results["T2_signup_page_loads"] = {
        "pass": has_form and "/sign-up" in final_url,
        "final_url": final_url,
        "email_inputs": email_input,
        "clerk_elements": clerk_card,
        "console_errors": console_errors[:5],
        "screenshot": "/tmp/uat_signup.png",
    }
    page.close()

    # ── Test 3: /agents is auth-gated (redirects to sign-in) ──────────────────
    page = context.new_page()
    page.goto("http://localhost:3000/agents", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(3000)
    final_url = page.url
    page.screenshot(path="/tmp/uat_agents.png", full_page=True)
    redirected_to_signin = "/sign-in" in final_url or "/sign-up" in final_url
    results["T3_agents_is_auth_gated"] = {
        "pass": redirected_to_signin,
        "final_url": final_url,
        "screenshot": "/tmp/uat_agents.png",
        "note": "Correctly redirected to auth" if redirected_to_signin else f"Unexpected URL: {final_url}",
    }
    page.close()

    browser.close()

# ── Test 4: CSS tokens in globals.css (code check) ────────────────────────────
import pathlib
css_path = pathlib.Path(r"C:\Users\Bantu\mzansi-agentive\veridian\apps\admin\app\globals.css")
css = css_path.read_text(encoding="utf-8")

has_bg_correct   = "--bg: #F0E8E0" in css or "--bg:#F0E8E0" in css
has_bg_old_gone  = "--bg: #FDF9F5" not in css and "--bg:#FDF9F5" not in css
has_red          = "--red: #B91C1C" in css or "--red:#B91C1C" in css
has_shadow_card  = "--shadow-card" in css

results["T4_css_tokens"] = {
    "pass": has_bg_correct and has_bg_old_gone and has_red and has_shadow_card,
    "--bg is #F0E8E0":   has_bg_correct,
    "old --bg gone":     has_bg_old_gone,
    "--red is #B91C1C":  has_red,
    "--shadow-card exists": has_shadow_card,
}

# ── Report ─────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  PHASE 4.2 UI UAT RESULTS")
print("="*60)
all_pass = True
for name, r in results.items():
    status = "PASS" if r["pass"] else "FAIL"
    if not r["pass"]:
        all_pass = False
    print(f"\n[{status}] {name}")
    for k, v in r.items():
        if k != "pass":
            print(f"       {k}: {v}")

print("\n" + "="*60)
print(f"  Overall: {'ALL PASS' if all_pass else 'FAILURES FOUND'}")
print("="*60)
sys.exit(0 if all_pass else 1)
