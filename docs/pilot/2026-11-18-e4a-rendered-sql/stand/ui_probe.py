"""E4a probe: what the Explore page of one chart shows (data-test attributes with "save", button
texts) and a screenshot, to find the Save control of this Superset version.
Usage: pwvenv/bin/python ui_probe.py 41 CHART
"""

import sys

from measure import STAND, login
from playwright.sync_api import sync_playwright

version, chart = sys.argv[1], int(sys.argv[2])
base = "http://127.0.0.1:" + ("8088" if version == "41" else "8089")
out = STAND / "rec" / "probe"
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as play:
    browser = play.chromium.launch(channel="chrome", headless=True)
    page = browser.new_context(viewport={"width": 1600, "height": 1200}).new_page()
    login(page, base, "admin")
    page.goto(f"{base}/explore/?slice_id={chart}", wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    print("url", page.url)
    tests = page.eval_on_selector_all(
        "[data-test]", "els => els.map(e => e.getAttribute('data-test'))"
    )
    print("data-test with save:", sorted({t for t in tests if "save" in t.lower()}))
    print(
        "buttons:",
        page.eval_on_selector_all(
            "button", "els => els.map(e => e.innerText.trim()).filter(t => t)"
        )[:40],
    )
    page.screenshot(path=str(out / f"explore_{version}_{chart}.png"))
    browser.close()
