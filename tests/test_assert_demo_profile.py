"""Checks for deploy/hf-demo/assert_demo_profile.py against a canned HTTP surface."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ASSERT_SCRIPT = REPO / "deploy" / "hf-demo" / "assert_demo_profile.py"

_AUTO_ONLY_HEALTH = {
    "ok": True,
    "auth": False,
    "version": "0.0-test",
    "demo_auto_only": True,
    "capabilities": {
        "auto_overview": True,
        "text_session": False,
        "fields_session": False,
        "word_edit": False,
        "enrichment": False,
        "llm_wired": False,
    },
}


class _CannedHandler(BaseHTTPRequestHandler):
    """GET health + 403 on gated POSTs/PATCHes. health set on the class per run."""

    health: dict = _AUTO_ONLY_HEALTH

    def log_message(self, format, *args):  # silence access log
        return

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/api/v1/health"):
            body = json.dumps(self.health).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        body = json.dumps({"detail": "gated"}).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PATCH(self) -> None:
        self.do_POST()


def _serve(health: dict):
    handler = type(
        "Handler",
        (_CannedHandler,),
        {"health": health},
    )
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


def _stop(server: HTTPServer) -> None:
    server.shutdown()
    server.server_close()


def test_assert_script_ok_on_auto_only_profile() -> None:
    server, base = _serve(_AUTO_ONLY_HEALTH)
    try:
        proc = subprocess.run(
            [sys.executable, str(ASSERT_SCRIPT), base, "--prefix", ""],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "demo profile OK" in proc.stdout
    finally:
        _stop(server)


def test_assert_script_fails_when_expecting_text_on_auto_only() -> None:
    server, base = _serve(_AUTO_ONLY_HEALTH)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(ASSERT_SCRIPT),
                base,
                "--prefix",
                "",
                "--text-enabled",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "FAILED" in proc.stderr
    finally:
        _stop(server)


def test_assert_script_fails_when_capabilities_missing() -> None:
    bad = {k: v for k, v in _AUTO_ONLY_HEALTH.items() if k != "capabilities"}
    server, base = _serve(bad)
    try:
        proc = subprocess.run(
            [sys.executable, str(ASSERT_SCRIPT), base, "--prefix", ""],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
    finally:
        _stop(server)


def test_require_llm_ready_setting_defaults_false() -> None:
    from auto_bi.config import Settings

    s = Settings(_env_file=None)
    assert s.require_llm_ready is False
    assert s.demo_auto_only is False


def test_require_llm_ready_env_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from auto_bi.config import Settings

    monkeypatch.setenv("AUTO_BI_REQUIRE_LLM_READY", "true")
    s = Settings(_env_file=None)
    assert s.require_llm_ready is True
