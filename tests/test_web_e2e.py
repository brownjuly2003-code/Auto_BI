"""Browser E2E (D-4): the web UI happy path a real user walks, plus an axe scan.

Drives a real Chromium (Playwright) against `auto_bi serve` running in the
public-demo profile (AUTO_BI_DEMO_AUTO_ONLY: deterministic auto-overview,
DisabledLLM — no provider/key, no spend) and the live ClickHouse+Superset
stand: Авто → выбор витрины → «Собрать обзор» → спека → «Собрать дашборд» →
SSE-лог → ссылка на дашборд. axe-core scans every UI state along the way —
the static checks in test_ui_a11y.py pin specific D-5 fixes, axe covers the
whole rendered page.

Needs playwright (+ installed chromium) and axe-playwright-python — both are
pulled ephemerally in CI (`uv run --with`), so the module skips cleanly when
they are absent. Deselected by default via addopts (`-m 'not e2e'`), same
contract as `integration`.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

# importorskip (not plain import): the offline suite still collects this module —
# without the deps it must skip, not error. E402-free by design (no import below code).
_pw = pytest.importorskip("playwright.sync_api", reason="browser E2E needs playwright")
_axe = pytest.importorskip(
    "axe_playwright_python.sync_playwright", reason="browser E2E needs axe-playwright-python"
)
Axe = _axe.Axe
expect = _pw.expect
sync_playwright = _pw.sync_playwright

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[1]

# The auto-overview spec needs advisor EXPLAIN/stat passes over the DWH; the build
# then creates datasets+charts+dashboard through the Superset API. Generous ceilings —
# these bound a hang, they are not the expected duration.
SPEC_TIMEOUT_MS = 120_000
BUILD_TIMEOUT_MS = 300_000


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def serve_url(tmp_path_factory):
    """`auto_bi serve` as a real subprocess in the demo profile, torn down after."""
    port = _free_port()
    log_path = tmp_path_factory.mktemp("serve") / "serve.log"
    env = os.environ | {
        "AUTO_BI_DEMO_AUTO_ONLY": "true",
        # keep the test run off the developer's real ledger/session store
        "AUTO_BI_STORE_PATH": str(tmp_path_factory.mktemp("store") / "auto_bi.sqlite"),
    }
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "auto_bi.cli",
                "serve",
                "--model-path",
                "semantic/model.yaml",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            _wait_until_healthy(base, proc, log_path)
            yield base
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


def _wait_until_healthy(base: str, proc: subprocess.Popen, log_path: Path) -> None:
    # serve connects to the DWH eagerly at boot, so readiness includes that round-trip
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"serve exited with {proc.returncode}:\n{log_path.read_text()}")
        try:
            health = httpx.get(f"{base}/api/v1/health", timeout=2.0).json()
        except httpx.HTTPError:
            time.sleep(0.5)
            continue
        # the whole journey depends on the demo profile being active — fail here,
        # not three steps later with an opaque 403
        assert health.get("demo_auto_only") is True, f"demo profile not active: {health}"
        return
    proc.terminate()
    raise RuntimeError(f"serve not healthy after 90s:\n{log_path.read_text()}")


@pytest.fixture(scope="module")
def page(serve_url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.set_default_timeout(15_000)
        yield pg
        browser.close()


def _axe_check(page, state: str) -> None:
    results = Axe().run(page)
    report = results.generate_report()
    assert results.violations_count == 0, f"axe violations at «{state}»:\n{report}"


def test_auto_overview_happy_path_with_axe(page, serve_url):
    page.goto(f"{serve_url}/")
    expect(page).to_have_title("Auto_BI — агент дашбордов")

    # demo profile reached the UI: LLM tabs greyed out, «Авто» panel is the landing state
    expect(page.locator("#tab-text")).to_be_disabled()
    expect(page.locator("#tab-fields")).to_be_disabled()
    expect(page.locator("#auto-panel")).to_be_visible()
    page.wait_for_selector("#auto-table option[value='dm.sales_daily']", state="attached")
    _axe_check(page, "стартовая страница, вкладка «Авто»")

    page.select_option("#auto-table", "dm.sales_daily")
    page.click("#auto-submit")

    # deterministic auto-overview + advisor verdicts → spec preview with the approve button
    expect(page.locator("#spec")).to_be_visible(timeout=SPEC_TIMEOUT_MS)
    expect(page.locator("#approve-btn")).to_be_enabled()
    assert page.locator("#charts .chart-card, #charts > *").count() > 0
    _axe_check(page, "превью спеки")

    page.click("#approve-btn")

    # the build streams progress over SSE and ends with a terminal result line
    result = page.locator("#build-result")
    expect(result).to_be_visible(timeout=BUILD_TIMEOUT_MS)
    assert "failed" not in (result.get_attribute("class") or ""), result.inner_text()
    assert page.locator("#build-log li").count() > 0, "SSE build log stayed empty"
    expect(page.locator("#session-chip")).to_have_text("построен")

    href = page.locator("#build-result a").get_attribute("href")
    assert href and "/superset/dashboard/" in href, f"unexpected dashboard url: {href}"
    # the link must point at a live BI host, not a dangling artifact
    resp = httpx.get(href, follow_redirects=True, timeout=30.0)
    assert resp.status_code == 200, f"dashboard url {href} -> {resp.status_code}"
    _axe_check(page, "дашборд построен")
