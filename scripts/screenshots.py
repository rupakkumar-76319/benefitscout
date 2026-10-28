"""Capture README screenshots from a running server:  python scripts/screenshots.py <run_id> [base_url]"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

run_id = sys.argv[1]
base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8010"
out = Path(__file__).resolve().parent.parent / "docs"
out.mkdir(exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 860}, device_scale_factor=1.5)
    pg.goto(f"{base}/#{run_id}")
    pg.wait_for_selector(".scheme", timeout=20000)
    pg.wait_for_timeout(800)
    pg.evaluate("window.scrollTo(0,0)")
    pg.screenshot(path=str(out / "screenshot-results.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 860})
    el = pg.query_selector(".scheme")
    el.screenshot(path=str(out / "screenshot-scheme.png"))
    led = pg.query_selector(".ledger")
    led.scroll_into_view_if_needed()
    led.screenshot(path=str(out / "screenshot-ledger.png"))
    pg.goto(f"{base}/")
    pg.wait_for_timeout(500)
    pg.screenshot(path=str(out / "screenshot-home.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 860})
    b.close()
print("saved to", out)
