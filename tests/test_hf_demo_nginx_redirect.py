"""Pathlib-only ratchet: HF demo nginx root redirects must stay relative."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NGINX = REPO / "deploy" / "hf-demo" / "nginx.conf"


def _text() -> str:
    return NGINX.read_text(encoding="utf-8")


def _server_block(text: str) -> str:
    match = re.search(r"server\s*\{", text)
    assert match, "server block not found in deploy/hf-demo/nginx.conf"
    start = match.start()
    depth = 0
    for index, character in enumerate(text[start:], start=start):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError("unbalanced server block in deploy/hf-demo/nginx.conf")


def test_absolute_redirect_off_in_server_block() -> None:
    server = _server_block(_text())
    assert re.search(
        r"(?m)^\s*absolute_redirect\s+off\s*;", server
    ), "server block must keep Location relative behind the HF TLS proxy"


def test_root_and_agent_redirects_remain_relative() -> None:
    text = _text()
    assert re.search(r"location\s*=\s*/\s*\{\s*return\s+302\s+/agent/\s*;\s*\}", text)
    assert re.search(r"location\s*=\s*/agent\s*\{\s*return\s+301\s+/agent/\s*;\s*\}", text)
    assert "hf.space" not in text
    assert not re.search(r"return\s+\d{3}\s+https?://", text)
