"""Browser E2E (D-4 + plan_sol step 10): real Chromium against `auto_bi serve`.

Paths covered (no paid LLM — demo_auto_only + DisabledLLM, or auth-only UI checks):

* auto-overview happy path → SSE → live Superset dashboard link (desktop + mobile);
* axe-core on key UI states;
* auth login (analyst / admin) + forbidden schema (finance analyst sees no dm tables).

Needs playwright (+ chromium) and axe-playwright-python — pulled ephemerally in CI.
Deselected by default via addopts (`-m 'not e2e'`).

Residual (not in this file yet): failed-build retry UI, process restart/resume browser,
SSE reconnect mid-stream, text/fields via FixtureLLM (serve has no fixture mode).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml

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

VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "mobile": {"width": 390, "height": 844},
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn_serve(
    tmp_path_factory,
    *,
    extra_env: dict[str, str] | None = None,
    log_name: str = "serve.log",
) -> tuple[subprocess.Popen, str, Path]:
    port = _free_port()
    log_path = tmp_path_factory.mktemp("serve") / log_name
    env = os.environ | {
        "AUTO_BI_DEMO_AUTO_ONLY": "true",
        # keep the test run off the developer's real ledger/session store
        "AUTO_BI_STORE_PATH": str(tmp_path_factory.mktemp("store") / "auto_bi.sqlite"),
    }
    if extra_env:
        env.update(extra_env)
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
    return proc, base, log_path


def _wait_until_healthy(
    base: str,
    proc: subprocess.Popen,
    log_path: Path,
    *,
    expect_auth: bool | None = None,
) -> dict:
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
        if expect_auth is not None:
            assert health.get("auth") is expect_auth, f"auth flag mismatch: {health}"
        return health
    proc.terminate()
    raise RuntimeError(f"serve not healthy after 90s:\n{log_path.read_text()}")


def _teardown_serve(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def serve_url(tmp_path_factory):
    """`auto_bi serve` as a real subprocess in the demo profile, torn down after."""
    proc, base, log_path = _spawn_serve(tmp_path_factory)
    try:
        _wait_until_healthy(base, proc, log_path, expect_auth=False)
        yield base
    finally:
        _teardown_serve(proc)


@pytest.fixture(scope="module")
def serve_url_auth(tmp_path_factory):
    """Demo profile + auth with analyst/admin/finance users for RBAC UI checks."""
    users_path = tmp_path_factory.mktemp("auth") / "users.yaml"
    users_path.write_text(
        yaml.safe_dump(
            {
                "users": [
                    {
                        "username": "alice",
                        "password": "alice-secret",
                        "role": "analyst",
                        "schemas": ["dm"],
                    },
                    {
                        "username": "root",
                        "password": "root-secret",
                        "role": "admin",
                        "schemas": ["*"],
                    },
                    {
                        "username": "fin",
                        "password": "fin-secret",
                        "role": "analyst",
                        "schemas": ["finance"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    proc, base, log_path = _spawn_serve(
        tmp_path_factory,
        extra_env={
            "AUTO_BI_AUTH_ENABLED": "true",
            "AUTO_BI_AUTH_USERS_FILE": str(users_path),
            # demo_auto_only still true: no LLM; auth is independent.
        },
        log_name="serve-auth.log",
    )
    try:
        _wait_until_healthy(base, proc, log_path, expect_auth=True)
        yield base
    finally:
        _teardown_serve(proc)


def _axe_check(page, state: str) -> None:
    results = Axe().run(page)
    report = results.generate_report()
    assert results.violations_count == 0, f"axe violations at «{state}»:\n{report}"


def _login(page, base: str, username: str, password: str) -> None:
    page.goto(f"{base}/")
    expect(page.locator("#login-overlay")).to_be_visible()
    page.fill("#login-user", username)
    page.fill("#login-pass", password)
    page.click("#login-form button[type='submit']")
    expect(page.locator("#login-overlay")).to_be_hidden()
    expect(page.locator("#user-chip")).to_be_visible()
    expect(page.locator("#user-chip")).to_contain_text(username)


def _run_auto_overview(page, serve_url: str, *, axe: bool = True) -> str:
    """Drive Авто → dm.sales_daily → approve → built. Returns dashboard href."""
    page.goto(f"{serve_url}/")
    expect(page).to_have_title("Auto_BI — агент дашбордов")

    expect(page.locator("#tab-text")).to_be_disabled()
    expect(page.locator("#tab-fields")).to_be_disabled()
    expect(page.locator("#auto-panel")).to_be_visible()
    page.wait_for_selector("#auto-table option[value='dm.sales_daily']", state="attached")
    if axe:
        _axe_check(page, "стартовая страница, вкладка «Авто»")

    page.select_option("#auto-table", "dm.sales_daily")
    page.click("#auto-submit")

    expect(page.locator("#spec")).to_be_visible(timeout=SPEC_TIMEOUT_MS)
    expect(page.locator("#approve-btn")).to_be_enabled()
    assert page.locator("#charts .chart-card, #charts > *").count() > 0
    if axe:
        _axe_check(page, "превью спеки")

    page.click("#approve-btn")

    result = page.locator("#build-result")
    expect(result).to_be_visible(timeout=BUILD_TIMEOUT_MS)
    assert "failed" not in (result.get_attribute("class") or ""), result.inner_text()
    assert page.locator("#build-log li").count() > 0, "SSE build log stayed empty"
    expect(page.locator("#session-chip")).to_have_text("построен")

    href = page.locator("#build-result a").get_attribute("href")
    assert href and "/superset/dashboard/" in href, f"unexpected dashboard url: {href}"
    resp = httpx.get(href, follow_redirects=True, timeout=30.0)
    assert resp.status_code == 200, f"dashboard url {href} -> {resp.status_code}"
    if axe:
        _axe_check(page, "дашборд построен")
    return href


def test_auto_overview_happy_path_with_axe(serve_url: str):
    """Desktop full journey: Авто → spec → approve → SSE → live dashboard + axe."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORTS["desktop"])
        page.set_default_timeout(15_000)
        try:
            _run_auto_overview(page, serve_url, axe=True)
        finally:
            browser.close()


def test_mobile_viewport_landing_and_axe(serve_url: str):
    """Mobile viewport: landing + demo capabilities + axe (no second full BI build)."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORTS["mobile"])
        page.set_default_timeout(15_000)
        try:
            page.goto(f"{serve_url}/")
            expect(page).to_have_title("Auto_BI — агент дашбордов")
            expect(page.locator("#tab-text")).to_be_disabled()
            expect(page.locator("#auto-panel")).to_be_visible()
            page.wait_for_selector("#auto-table option[value='dm.sales_daily']", state="attached")
            # Topbar brand still in view at phone width
            expect(page.locator(".brand-name")).to_be_visible()
            _axe_check(page, "mobile landing, auto panel")
        finally:
            browser.close()


def test_auth_analyst_login_and_auto_tables(serve_url_auth: str):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORTS["desktop"])
        page.set_default_timeout(15_000)
        try:
            _login(page, serve_url_auth, "alice", "alice-secret")
            expect(page.locator("#user-chip")).to_contain_text("analyst")
            expect(page.locator("#auto-panel")).to_be_visible()
            page.wait_for_selector("#auto-table option[value='dm.sales_daily']", state="attached")
            _axe_check(page, "analyst logged in, auto panel")
        finally:
            browser.close()


def test_auth_admin_login(serve_url_auth: str):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORTS["desktop"])
        page.set_default_timeout(15_000)
        try:
            _login(page, serve_url_auth, "root", "root-secret")
            expect(page.locator("#user-chip")).to_contain_text("admin")
            page.wait_for_selector("#auto-table option[value='dm.sales_daily']", state="attached")
        finally:
            browser.close()


def test_auth_forbidden_schema_hides_dm_tables(serve_url_auth: str):
    """Finance-only analyst must not see dm.* tables in the auto picker (RBAC)."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORTS["desktop"])
        page.set_default_timeout(15_000)
        try:
            _login(page, serve_url_auth, "fin", "fin-secret")
            expect(page.locator("#user-chip")).to_contain_text("analyst")
            expect(page.locator("#auto-panel")).to_be_visible()
            # Auto panel loads tables from /model/fields (RBAC-filtered). Empty is valid.
            expect(page.locator("#auto-table")).to_be_visible()
            # Give the client one fields fetch cycle; then assert no dm.* options remain.
            page.wait_for_load_state("networkidle")
            dm_options = page.locator("#auto-table option[value^='dm.']")
            assert (
                dm_options.count() == 0
            ), "finance analyst must not see dm.* tables: " + ", ".join(
                dm_options.all_text_contents()
            )
            _axe_check(page, "finance analyst, no dm tables")
        finally:
            browser.close()


def test_auth_bad_credentials_show_error(serve_url_auth: str):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORTS["desktop"])
        page.set_default_timeout(15_000)
        try:
            page.goto(f"{serve_url_auth}/")
            expect(page.locator("#login-overlay")).to_be_visible()
            page.fill("#login-user", "alice")
            page.fill("#login-pass", "wrong-password")
            page.click("#login-form button[type='submit']")
            expect(page.locator("#login-error")).to_be_visible()
            expect(page.locator("#login-overlay")).to_be_visible()
        finally:
            browser.close()
