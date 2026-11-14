"""A read-only MCP server over the DecisionOps use cases, on stdio (ADR 0012, D5).

The Model Context Protocol is JSON-RPC 2.0, one message per line on standard input and
output. Version 2026-07-28 has no handshake: every request names its protocol version and the
client's capabilities in `params._meta`, and a server answers `server/discover` with what it
supports. Versions before it open with `initialize`, which names the version for the rest of
the process. This server speaks both — a *dual-era* server in the words of the specification:

* a request whose `_meta` names `io.modelcontextprotocol/protocolVersion` is served
  statelessly at that version — 2026-07-28, the one modern version this server knows, or the
  `-32022` error that lists it; a version that is no string, or a request that names the
  version without `io.modelcontextprotocol/clientCapabilities`, is malformed (`-32602`), and
  what the client sent is not echoed back;
* `initialize` selects a legacy version for the process: the one the client asks for when it
  is 2025-11-25 or 2025-06-18, otherwise 2025-11-25, which the client may refuse;
* a request with neither — a modern request without its version, a legacy one other than
  `ping` before `initialize` — is malformed (`-32602`).

The methods are those a server of tools needs: `server/discover` (modern), `initialize` and
`ping` (legacy; the legacy lifecycle lets a client ping before its `initialize` is
answered), `tools/list` and `tools/call` (both). Notifications are read and not answered;
there is nothing a notification could ask of this server. A request is answered when it is
done, and the answer flushed before the next line is read: requests are served one at a
time, in the order they arrive, so a `notifications/cancelled` finds no request in flight.

Nothing but messages may reach standard output. `serve_stdio` keeps a duplicate of it for
the protocol and points the process's own descriptor of standard output at standard error,
so a `print`, a write to descriptor 1 or what a buffered `sys.stdout` still holds from before
serving began — of this code or of a library — ends on standard error, never as a message on
the wire. What an unbuffered `sys.stdout` (`python -u`, `PYTHONUNBUFFERED`) wrote before is
out already; `decision mcp` prints nothing before it serves. Messages are
written in ASCII, by the escapes of JSON: a line holds no character a reader of UTF-8 could
stumble on, and no line break.

The specification needs no SDK here: a server of tools on stdio is this file. That keeps the
lock file as it was (`mcp` brings a web stack with it) and the protocol the server speaks in
one place that tests read whole.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
from typing import Any, BinaryIO

from auto_bi import __version__
from auto_bi.mcp.tools import ToolBox

#: The modern versions: a request names one in its `_meta`.
MODERN_VERSIONS = ("2026-07-28",)
#: The legacy versions `initialize` negotiates, newest first.
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18")
SERVER_NAME = "auto-bi-decisionops"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022

_VERSION = "io.modelcontextprotocol/protocolVersion"
_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
#: How long a client may keep the tool list and the discovery result: they change only when
#: the server is started again, with or without an evidence directory.
_TTL_MS = 3_600_000

INSTRUCTIONS = (
    "DecisionOps release gate for governed analytics numbers. `impact` says whom a change "
    "of semantics reaches; `verify`, when offered, runs the gate and writes its evidence; "
    "`evidence` re-verifies a bundle; `validate` checks contracts; `runs` lists bundles. "
    "No tool writes to a BI or reaches an engine. A verdict of block is an answer, not an "
    "error."
)

_ABSENT = object()


def _server_info() -> dict[str, str]:
    return {"name": SERVER_NAME, "version": __version__}


def _encode(message: dict[str, Any]) -> bytes:
    return json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode(
        "ascii"
    )


def _error(ident: Any, code: int, message: str, data: Any = _ABSENT) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not _ABSENT:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": ident, "error": error}


def _valid_id(ident: Any) -> bool:
    """A request id is a string or an integer — not null, not a boolean, not a fraction."""
    return isinstance(ident, str) or (isinstance(ident, int) and not isinstance(ident, bool))


class Server:
    """Answers the messages of one client, line by line.

    The only state is the legacy version an `initialize` selected: the specification scopes
    it to the stdio process. A modern request depends on nothing before it.
    """

    def __init__(self, tools: ToolBox) -> None:
        self._tools = tools
        self._legacy: str | None = None

    def serve(self, reader: BinaryIO, writer: BinaryIO) -> None:
        """Answer each line of *reader* on *writer* until the client closes the stream."""
        for line in reader:
            answer = self.handle_line(line)
            if answer is not None:
                writer.write(answer + b"\n")
                writer.flush()

    def handle_line(self, line: bytes) -> bytes | None:
        """The answer to one line, or nothing for a notification or a blank line."""
        if not line.strip():
            return None
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
            return _encode(_error(None, PARSE_ERROR, "Parse error"))
        answer = self._answer(message)
        if answer is None:
            return None
        try:
            return _encode(answer)
        except Exception:  # an answer JSON cannot spell is a defect too: said, and served on
            traceback.print_exc()
            return _encode(_error(answer.get("id"), INTERNAL_ERROR, "Internal error"))

    def _answer(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            # A batch, or a value that is no message: JSON-RPC answers either with one error.
            return _error(None, INVALID_REQUEST, "Invalid Request: one JSON object per line")
        if "method" not in message:
            if "result" in message or "error" in message:
                # A response the client sent, which it must not: there is no request of ours.
                return None
            ident = message.get("id")
            return _error(
                ident if _valid_id(ident) else None,
                INVALID_REQUEST,
                "Invalid Request: no method, result or error",
            )
        if "id" not in message:
            return None  # a notification
        ident = message["id"]
        if not _valid_id(ident):
            return _error(None, INVALID_REQUEST, "Invalid Request: id is a string or an integer")
        method = message["method"]
        params = message.get("params", {})
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(ident, INVALID_REQUEST, "Invalid Request: not a JSON-RPC 2.0 request")
        if not isinstance(params, dict):
            return _error(ident, INVALID_REQUEST, "Invalid Request: params is an object")
        try:
            return self._dispatch(ident, method, params)
        except Exception:  # a defect of the server, not of the request: said, and served on
            traceback.print_exc()
            return _error(ident, INTERNAL_ERROR, "Internal error")

    def _dispatch(self, ident: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(ident, params)
        meta = params.get("_meta")
        if isinstance(meta, dict) and _VERSION in meta:
            version = meta[_VERSION]
            if not isinstance(version, str):
                # No version at all: nothing to list it against, and nothing to echo back.
                return _error(ident, INVALID_PARAMS, f"Invalid params: {_VERSION} is a string")
            if version not in MODERN_VERSIONS:
                return _error(
                    ident,
                    UNSUPPORTED_PROTOCOL_VERSION,
                    "Unsupported protocol version",
                    {"supported": list(MODERN_VERSIONS), "requested": version},
                )
            if not isinstance(meta.get(_CAPABILITIES), dict):
                return _error(
                    ident, INVALID_PARAMS, f"Invalid params: _meta holds no {_CAPABILITIES}"
                )
            return self._modern(ident, method, params)
        if self._legacy is not None or method == "ping":
            # The legacy lifecycle lets a ping come before initialize; it selects no version.
            return self._legacy_request(ident, method, params)
        return _error(
            ident,
            INVALID_PARAMS,
            f"Invalid params: _meta holds no {_VERSION}, and no initialize came before",
        )

    def _initialize(self, ident: Any, params: dict[str, Any]) -> dict[str, Any]:
        asked = params.get("protocolVersion")
        version = asked if asked in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        self._legacy = version
        return {
            "jsonrpc": "2.0",
            "id": ident,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": _server_info(),
                "instructions": INSTRUCTIONS,
            },
        }

    def _modern(self, ident: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "server/discover":
            result: dict[str, Any] = {
                "supportedVersions": list(MODERN_VERSIONS),
                "capabilities": {"tools": {}},
                "instructions": INSTRUCTIONS,
                "ttlMs": _TTL_MS,
                "cacheScope": "private",
            }
        elif method == "tools/list":
            result = {"tools": self._list(), "ttlMs": _TTL_MS, "cacheScope": "private"}
        elif method == "tools/call":
            called = self._call(ident, params)
            if "error" in called:
                return called
            result = called["result"]
        else:
            return _error(ident, METHOD_NOT_FOUND, f"Method not found: {method}")
        result = {"resultType": "complete", **result, "_meta": {_SERVER_INFO: _server_info()}}
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    def _legacy_request(self, ident: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "ping":
            return {"jsonrpc": "2.0", "id": ident, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": ident, "result": {"tools": self._list()}}
        if method == "tools/call":
            return self._call(ident, params)
        return _error(ident, METHOD_NOT_FOUND, f"Method not found: {method}")

    def _list(self) -> list[dict[str, Any]]:
        return [tool.definition() for tool in self._tools.tools()]

    def _call(self, ident: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str):
            return _error(ident, INVALID_PARAMS, "Invalid params: name is a string")
        if not isinstance(arguments, dict):
            return _error(ident, INVALID_PARAMS, "Invalid params: arguments is an object")
        tool = self._tools.find(name)
        if tool is None:
            return _error(ident, INVALID_PARAMS, f"Unknown tool: {name}")
        outcome = self._tools.call(tool, arguments)
        text = json.dumps(outcome.structured, ensure_ascii=False, allow_nan=False)
        return {
            "jsonrpc": "2.0",
            "id": ident,
            "result": {
                "content": [{"type": "text", "text": text}],
                "structuredContent": outcome.structured,
                "isError": outcome.is_error,
            },
        }


def serve_stdio(tools: ToolBox) -> None:
    """Serve one client on this process's standard streams until it closes standard input."""
    wire = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    # From here the descriptor of standard output is standard error's: whatever else the
    # process prints cannot be mistaken for a message — nor can what it printed before and a
    # buffered `sys.stdout` still holds, which is not flushed here and reaches standard error.
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    # A client that went away leaves nobody to answer, nor to flush the last answer to.
    with contextlib.suppress(BrokenPipeError):
        try:
            Server(tools).serve(sys.stdin.buffer, wire)
        finally:
            wire.close()
