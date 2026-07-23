"""Offline browser E2E (plan_sol step 10 residual): no DWH, no BI, no paid LLM.

Spins `tests/_offline_e2e_server.py` as a **subprocess** (clean kill — avoids Windows
asyncio Proactor ResourceWarning under pytest filterwarnings=error) with ScriptedLLM
+ a controllable fake builder. Covers residual user flows:

* text session → approve → build (scripted LLM, not paid);
* failed build → approve retry → success;
* SSE late-connect / reconnect-style replay of the terminal event;
* browser reload resume (sessionStorage + GET /sessions/{id} hydrate).

Deselected by default (`-m 'not e2e'`). CI job `browser-offline-e2e` runs this module.
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

_pw = pytest.importorskip("playwright.sync_api", reason="browser E2E needs playwright")
expect = _pw.expect
sync_playwright = _pw.sync_playwright

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = Path(__file__).resolve().parent / "_offline_e2e_server.py"

SPEC_TIMEOUT_MS = 60_000
BUILD_TIMEOUT_MS = 60_000


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn_server(tmp_path: Path, *, fail_times: int = 0):
    port = _free_port()
    store = tmp_path / "e2e.sqlite"
    log_path = tmp_path / "server.log"
    env = os.environ | {
        "AUTO_BI_E2E_PORT": str(port),
        "AUTO_BI_E2E_STORE": str(store),
        "AUTO_BI_E2E_FAIL_TIMES": str(fail_times),
    }
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT)],
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(errors="replace")
            raise RuntimeError(f"offline e2e server exited {proc.returncode}:\n{tail}")
        try:
            with httpx.Client(timeout=1.0) as client:
                if client.get(f"{base}/api/v1/health").json().get("ok"):
                    return proc, base, log_path
        except httpx.HTTPError:
            time.sleep(0.15)
    proc.kill()
    raise RuntimeError(f"offline e2e server not healthy:\n{log_path.read_text(errors='replace')}")


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture
def offline_server(tmp_path):
    proc, base, _log = _spawn_server(tmp_path, fail_times=0)
    try:
        yield base
    finally:
        _stop(proc)


@pytest.fixture
def offline_server_fail_once(tmp_path):
    proc, base, _log = _spawn_server(tmp_path, fail_times=1)
    try:
        yield base
    finally:
        _stop(proc)


def _start_text_to_approve(page, base: str) -> None:
    page.goto(f"{base}/")
    expect(page).to_have_title("Auto_BI — агент дашбордов")
    expect(page.locator("#tab-text")).to_be_enabled()
    page.fill("#chat-text", "выручка по дням")
    page.click("#send-btn")
    expect(page.locator("#approve-btn")).to_be_enabled(timeout=SPEC_TIMEOUT_MS)
    expect(page.locator("#spec")).to_be_visible()


def _recover_session_id(page) -> str | None:
    return page.evaluate(
        """() => {
          const entries = performance.getEntriesByType('resource');
          for (const e of entries) {
            const m = e.name.match(/\\/api\\/v1\\/sessions\\/([0-9a-fA-F-]+)/);
            if (m) return m[1];
          }
          return null;
        }"""
    )


def test_text_session_builds_with_scripted_llm(offline_server):
    base = offline_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(15_000)
        try:
            _start_text_to_approve(page, base)
            page.click("#approve-btn")
            result = page.locator("#build-result")
            expect(result).to_be_visible(timeout=BUILD_TIMEOUT_MS)
            assert "failed" not in (result.get_attribute("class") or "")
            expect(page.locator("#session-chip")).to_have_text("построен")
            href = page.locator("#build-result a").get_attribute("href")
            assert href and "superset/dashboard" in href
            assert page.locator("#build-log li").count() > 0
        finally:
            browser.close()


def test_failed_build_retry_succeeds(offline_server_fail_once):
    base = offline_server_fail_once
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(15_000)
        try:
            _start_text_to_approve(page, base)
            page.click("#approve-btn")
            result = page.locator("#build-result")
            expect(result).to_be_visible(timeout=BUILD_TIMEOUT_MS)
            expect(result).to_have_class("build-result failed")
            expect(page.locator("#session-chip")).to_have_text("ошибка сборки")
            expect(page.locator("#approve-btn")).to_be_enabled()
            page.click("#approve-btn")
            expect(page.locator("#build-result:not(.failed)")).to_be_visible(
                timeout=BUILD_TIMEOUT_MS
            )
            expect(page.locator("#session-chip")).to_have_text("построен")
        finally:
            browser.close()


def test_sse_late_connect_replays_terminal_event(offline_server):
    """After a finished build, a new EventSource still receives the terminal done."""
    base = offline_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(15_000)
        try:
            _start_text_to_approve(page, base)
            page.click("#approve-btn")
            expect(page.locator("#session-chip")).to_have_text("построен", timeout=BUILD_TIMEOUT_MS)
            sid = _recover_session_id(page)
            assert sid, "could not recover session_id from browser resource timings"
            late = page.evaluate(
                """(args) => new Promise((resolve, reject) => {
                  const es = new EventSource(
                    args.base + '/api/v1/sessions/' + args.sid + '/events'
                  );
                  const t = setTimeout(() => {
                    es.close();
                    reject(new Error('SSE timeout'));
                  }, 15000);
                  es.addEventListener('done', (e) => {
                    clearTimeout(t);
                    es.close();
                    resolve(JSON.parse(e.data));
                  });
                  es.addEventListener('error', (e) => {
                    if (!e.data) return;
                    clearTimeout(t);
                    es.close();
                    reject(new Error(JSON.parse(e.data).text || 'sse error'));
                  });
                })""",
                {"base": base, "sid": sid},
            )
            assert "dashboard" in (late.get("url") or "")
        finally:
            browser.close()


def test_browser_reload_resumes_built_session(offline_server):
    """After reload, sessionStorage + GET hydrate restore chip, URL and sessionId."""
    base = offline_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(15_000)
        try:
            _start_text_to_approve(page, base)
            page.click("#approve-btn")
            expect(page.locator("#session-chip")).to_have_text("построен", timeout=BUILD_TIMEOUT_MS)
            href_before = page.locator("#build-result a").get_attribute("href")
            assert href_before and "superset/dashboard" in href_before
            stored = page.evaluate("() => sessionStorage.getItem('auto_bi.session_id')")
            assert stored, "session id must be persisted before reload"

            page.reload()
            expect(page.locator("#session-chip")).to_have_text("построен", timeout=SPEC_TIMEOUT_MS)
            expect(page.locator("#build-result a")).to_be_visible()
            href_after = page.locator("#build-result a").get_attribute("href")
            assert href_after == href_before
            stored_after = page.evaluate("() => sessionStorage.getItem('auto_bi.session_id')")
            assert stored_after == stored
            # mode tabs stay locked for an ongoing session
            expect(page.locator("#mode-tabs")).to_be_hidden()
            expect(page.locator("#approve-btn")).to_have_text("Пересобрать дашборд")
            # resume banner in chat
            expect(page.locator(".msg-agent").filter(has_text="восстановлена")).to_be_visible()
        finally:
            browser.close()
