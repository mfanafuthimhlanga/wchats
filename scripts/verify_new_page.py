"""Screenshot agents/new after provision step fix"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

os.makedirs("apps/admin/verify_screenshots", exist_ok=True)

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto("http://localhost:3000/agents/new", wait_until="commit", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(3500)
    page.screenshot(path="apps/admin/verify_screenshots/03_agents_new_fixed.png", full_page=True)
    print("Screenshot saved: apps/admin/verify_screenshots/03_agents_new_fixed.png")
    ctx.close()
    browser.close()
