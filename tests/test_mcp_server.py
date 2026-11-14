"""The MCP protocol the DecisionOps server speaks on stdio (ADR 0012, D5).

Modern requests (2026-07-28) carry their version and the client's capabilities in `_meta`;
legacy clients open with `initialize`. These tests drive `Server.handle_line` with the
messages a client writes and read back what reaches the wire.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from auto_bi import __version__
from auto_bi.mcp import server as mcp_server
from auto_bi.mcp import tools as mcp_tools
from auto_bi.mcp.server import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    UNSUPPORTED_PROTOCOL_VERSION,
    Server,
)
from auto_bi.mcp.tools import Outcome, Tool, ToolBox, input_schema

MODERN = "2026-07-28"
META = {
    "io.modelcontextprotocol/protocolVersion": MODERN,
    "io.modelcontextprotocol/clientCapabilities": {},
}
SERVER_INFO = {"name": "auto-bi-decisionops", "version": __version__}


def _line(server: Server, message: Any) -> bytes | None:
    raw = message if isinstance(message, bytes) else json.dumps(message).encode()
    return server.handle_line(raw)


def _send(server: Server, message: Any) -> dict[str, Any]:
    answer = _line(server, message)
    assert answer is not None
    assert b"\n" not in answer and answer.isascii()
    decoded = json.loads(answer)
    assert isinstance(decoded, dict)
    return decoded


def _request(method: str, ident: Any = 1, **params: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}


def _modern(method: str, ident: Any = 1, **params: Any) -> dict[str, Any]:
    return _request(method, ident, _meta=dict(META), **params)


def _result(answer: dict[str, Any]) -> dict[str, Any]:
    assert "error" not in answer, answer
    result = answer["result"]
    assert isinstance(result, dict)
    return result


def _error(answer: dict[str, Any], code: int) -> dict[str, Any]:
    assert "result" not in answer, answer
    error = answer["error"]
    assert error["code"] == code, error
    return dict(error)


# --- modern: per-request metadata ---------------------------------------------


def test_discover_names_the_modern_version_the_tools_and_the_server() -> None:
    answer = _send(Server(ToolBox()), _modern("server/discover", ident="d-1"))

    assert answer["id"] == "d-1" and answer["jsonrpc"] == "2.0"
    result = _result(answer)
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == [MODERN]
    assert result["capabilities"] == {"tools": {}}
    assert result["_meta"] == {"io.modelcontextprotocol/serverInfo": SERVER_INFO}
    assert result["instructions"]
    assert result["ttlMs"] > 0 and result["cacheScope"] == "private"


@pytest.mark.parametrize("version", ["2025-11-25", "1900-01-01", ""])
def test_a_version_the_server_does_not_serve_statelessly_is_refused_with_the_list(
    version: str,
) -> None:
    message = _modern("tools/list")
    message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = version

    error = _error(_send(Server(ToolBox()), message), UNSUPPORTED_PROTOCOL_VERSION)
    assert error["data"] == {"supported": [MODERN], "requested": version}


def _nested(depth: int) -> Any:
    value: Any = MODERN
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize(
    "version",
    [20260728, None, [MODERN], {"value": MODERN}, float("nan"), float("inf"), _nested(500)],
    ids=["integer", "null", "list", "object", "nan", "infinity", "nested"],
)
def test_a_version_that_is_no_string_is_malformed_and_not_echoed(version: Any) -> None:
    server = Server(ToolBox())
    message = _modern("tools/list")
    message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = version

    error = _error(_send(server, message), INVALID_PARAMS)
    assert "data" not in error
    assert _result(_send(server, _modern("tools/list", ident=2)))["tools"]


@pytest.mark.parametrize("capabilities", ["absent", None, [], "tools"])
def test_a_modern_request_without_client_capabilities_is_malformed(capabilities: Any) -> None:
    message = _modern("tools/list")
    if capabilities == "absent":
        del message["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]
    else:
        message["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = capabilities

    _error(_send(Server(ToolBox()), message), INVALID_PARAMS)


@pytest.mark.parametrize(
    "params", [{}, {"_meta": {}}, {"_meta": "2026-07-28"}, {"_meta": {"progressToken": 1}}]
)
def test_a_request_with_no_version_and_no_initialize_is_malformed(params: Any) -> None:
    message = {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": params}

    error = _error(_send(Server(ToolBox()), message), INVALID_PARAMS)
    assert "io.modelcontextprotocol/protocolVersion" in error["message"]


def test_a_request_without_params_is_malformed_too() -> None:
    message = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}

    _error(_send(Server(ToolBox()), message), INVALID_PARAMS)


@pytest.mark.parametrize("method", ["ping", "initialized", "resources/list", "tools/get"])
def test_a_modern_method_the_server_does_not_have_is_not_found(method: str) -> None:
    error = _error(_send(Server(ToolBox()), _modern(method)), METHOD_NOT_FOUND)
    assert method in error["message"]


def test_modern_tool_list_is_cacheable_and_carries_the_server() -> None:
    result = _result(_send(Server(ToolBox()), _modern("tools/list")))

    assert result["resultType"] == "complete"
    assert result["ttlMs"] > 0 and result["cacheScope"] == "private"
    assert result["_meta"] == {"io.modelcontextprotocol/serverInfo": SERVER_INFO}
    assert "nextCursor" not in result


def test_modern_tool_result_carries_the_result_type_and_the_server() -> None:
    message = _modern("tools/call", name="runs", arguments={"path": "missing-evidence-root"})

    result = _result(_send(Server(ToolBox()), message))
    assert result["resultType"] == "complete"
    assert result["_meta"] == {"io.modelcontextprotocol/serverInfo": SERVER_INFO}
    assert result["isError"] is True


def test_a_modern_request_is_served_after_an_initialize_as_modern() -> None:
    server = Server(ToolBox())
    _send(server, _request("initialize", protocolVersion="2025-06-18"))

    assert _result(_send(server, _modern("tools/list")))["resultType"] == "complete"


# --- legacy: initialize --------------------------------------------------------


@pytest.mark.parametrize("asked", ["2025-11-25", "2025-06-18"])
def test_initialize_accepts_a_legacy_version_it_serves(asked: str) -> None:
    result = _result(_send(Server(ToolBox()), _request("initialize", protocolVersion=asked)))

    assert result == {
        "protocolVersion": asked,
        "capabilities": {"tools": {}},
        "serverInfo": SERVER_INFO,
        "instructions": result["instructions"],
    }
    assert result["instructions"]


@pytest.mark.parametrize("asked", ["2024-11-05", "2025-03-26", MODERN, None, 3])
def test_initialize_offers_the_newest_legacy_version_for_any_other(asked: Any) -> None:
    result = _result(_send(Server(ToolBox()), _request("initialize", protocolVersion=asked)))

    assert result["protocolVersion"] == "2025-11-25"


def test_after_initialize_a_legacy_client_is_served_without_metadata() -> None:
    server = Server(ToolBox())
    _send(server, _request("initialize", protocolVersion="2025-06-18"))
    assert _line(server, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    assert _result(_send(server, _request("ping", ident=2))) == {}
    listed = _result(_send(server, _request("tools/list", ident=3)))
    assert set(listed) == {"tools"}  # no field of the modern revision
    called = _result(
        _send(server, _request("tools/call", ident=4, name="runs", arguments={"path": "x"}))
    )
    assert set(called) == {"content", "structuredContent", "isError"}
    _error(_send(server, _request("server/discover", ident=5)), METHOD_NOT_FOUND)


def test_a_ping_before_initialize_is_answered_and_selects_no_version() -> None:
    server = Server(ToolBox())

    assert _result(_send(server, _request("ping"))) == {}
    _error(_send(server, _request("tools/list", ident=2)), INVALID_PARAMS)


# --- JSON-RPC framing ----------------------------------------------------------


@pytest.mark.parametrize("line", [b"{", b"not json", b"\xff\xfe", b'{"a": 1'])
def test_a_line_that_is_no_json_is_a_parse_error_with_a_null_id(line: bytes) -> None:
    answer = _send(Server(ToolBox()), line)

    assert answer["id"] is None
    _error(answer, PARSE_ERROR)


def test_a_line_nested_past_the_parser_is_a_parse_error() -> None:
    answer = _send(Server(ToolBox()), b"[" * 100_000 + b"]" * 100_000)

    _error(answer, PARSE_ERROR)


@pytest.mark.parametrize("message", [[], [_modern("tools/list")], "tools/list", 1, None])
def test_a_batch_or_a_value_that_is_no_object_is_an_invalid_request(message: Any) -> None:
    answer = _send(Server(ToolBox()), message)

    assert answer["id"] is None
    _error(answer, INVALID_REQUEST)


@pytest.mark.parametrize("ident", [None, True, False, 1.5, [], {}])
def test_an_id_that_is_no_string_or_integer_is_an_invalid_request(ident: Any) -> None:
    answer = _send(Server(ToolBox()), _modern("tools/list", ident=ident))

    assert answer["id"] is None
    _error(answer, INVALID_REQUEST)


@pytest.mark.parametrize(
    "change",
    [
        {"jsonrpc": "1.0"},
        {"jsonrpc": None},
        {"method": 5},
        {"method": None},
        {"params": []},
        {"params": "x"},
        {"params": None},
    ],
)
def test_a_request_that_is_no_jsonrpc_request_is_invalid(change: dict[str, Any]) -> None:
    message = {**_modern("tools/list", ident="r"), **change}

    answer = _send(Server(ToolBox()), message)
    assert answer["id"] == "r"
    _error(answer, INVALID_REQUEST)


def test_a_request_without_jsonrpc_is_invalid() -> None:
    message = _modern("tools/list", ident=0)
    del message["jsonrpc"]

    answer = _send(Server(ToolBox()), message)
    assert answer["id"] == 0
    _error(answer, INVALID_REQUEST)


@pytest.mark.parametrize(
    "message",
    [
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "verify"}},
        {"jsonrpc": "2.0", "method": 5},
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 1, "error": {"code": 1, "message": "x"}},
    ],
)
def test_a_notification_or_a_response_is_not_answered(message: dict[str, Any]) -> None:
    assert _line(Server(ToolBox()), message) is None


@pytest.mark.parametrize(
    ("message", "ident"),
    [
        ({"jsonrpc": "2.0", "id": 5}, 5),
        ({"jsonrpc": "2.0", "id": "a", "params": {}}, "a"),
        ({}, None),
        ({"jsonrpc": "2.0", "id": None}, None),
        ({"jsonrpc": "2.0", "id": 1.5}, None),
    ],
)
def test_a_message_with_no_method_result_or_error_is_an_invalid_request(
    message: dict[str, Any], ident: Any
) -> None:
    answer = _send(Server(ToolBox()), message)

    assert answer["id"] == ident
    _error(answer, INVALID_REQUEST)


@pytest.mark.parametrize("line", [b"", b"\n", b"\r\n", b"  \t\n"])
def test_a_blank_line_is_not_answered(line: bytes) -> None:
    assert Server(ToolBox()).handle_line(line) is None


def test_serve_answers_each_request_on_its_own_line_in_order() -> None:
    lines = [
        json.dumps(_modern("server/discover", ident=1)),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "",
        json.dumps(_modern("tools/list", ident=2)) + "\r",
        "nonsense",
    ]
    reader = io.BytesIO("\n".join(lines).encode() + b"\n")
    writer = io.BytesIO()

    Server(ToolBox()).serve(reader, writer)

    written = writer.getvalue()
    assert written.endswith(b"\n")
    answers = [json.loads(line) for line in written.splitlines()]
    assert [answer["id"] for answer in answers] == [1, 2, None]
    assert answers[2]["error"]["code"] == PARSE_ERROR


class _Pipe:
    """The server's end of a pipe: what is written reaches the client when it is flushed."""

    def __init__(self) -> None:
        self.pending = b""
        self.delivered: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.pending += data
        return len(data)

    def flush(self) -> None:
        if self.pending:
            self.delivered.append(self.pending)
        self.pending = b""


def test_each_answer_reaches_the_client_before_the_next_line_is_read() -> None:
    pipe = _Pipe()
    requests = [
        _modern("server/discover", ident=1),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _modern("tools/list", ident=2),
        _modern("tools/call", ident=3, name="runs", arguments={"path": "missing"}),
    ]

    def client() -> Iterator[bytes]:
        # A client sends a request and waits for its answer before it sends the next one.
        answered = 0
        for request in requests:
            assert pipe.pending == b"" and len(pipe.delivered) == answered
            yield json.dumps(request).encode() + b"\n"
            answered += "id" in request

    Server(ToolBox()).serve(client(), pipe)  # type: ignore[arg-type]

    assert pipe.pending == b""
    assert [json.loads(chunk)["id"] for chunk in pipe.delivered] == [1, 2, 3]
    assert all(chunk.count(b"\n") == 1 for chunk in pipe.delivered)


# --- tools/call ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "said"),
    [
        ({"name": "drop_table", "arguments": {}}, "Unknown tool: drop_table"),
        ({"name": "verify", "arguments": {}}, "Unknown tool: verify"),
        ({"arguments": {}}, "name"),
        ({"name": 3}, "name"),
        ({"name": "runs", "arguments": ["path"]}, "arguments"),
        ({"name": "runs", "arguments": None}, "arguments"),
    ],
)
def test_a_call_that_names_no_tool_of_this_server_is_invalid_params(
    params: dict[str, Any], said: str
) -> None:
    error = _error(_send(Server(ToolBox()), _modern("tools/call", **params)), INVALID_PARAMS)
    assert said in error["message"]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": ""},
        {"path": 3},
        {"path": None},
        {"path": "x", "unknown": 1},
        {"path": ["x"]},
    ],
)
def test_arguments_the_schema_refuses_are_a_tool_execution_error(
    arguments: dict[str, Any],
) -> None:
    message = _modern("tools/call", name="runs", arguments=arguments)

    result = _result(_send(Server(ToolBox()), message))
    assert result["isError"] is True
    assert result["structuredContent"]["error"].startswith("invalid arguments: ")
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_a_call_without_arguments_is_a_call_with_none() -> None:
    message = _modern("tools/call", name="runs")

    result = _result(_send(Server(ToolBox()), message))
    assert result["structuredContent"]["error"].startswith("invalid arguments: path: ")


def test_a_defect_of_a_tool_is_an_internal_error_and_the_server_serves_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(box: ToolBox, arguments: Any) -> Outcome:
        raise KeyError("secret detail")

    tool = Tool(
        name="runs",
        title="t",
        description="d",
        arguments=mcp_tools.RunsArguments,
        read_only=True,
        run=broken,
    )
    monkeypatch.setattr(mcp_tools, "_TOOLS", (tool,))
    server = Server(ToolBox())

    error = _error(
        _send(server, _modern("tools/call", ident=9, name="runs", arguments={"path": "x"})),
        INTERNAL_ERROR,
    )
    assert error == {"code": INTERNAL_ERROR, "message": "Internal error"}
    assert "KeyError" in capsys.readouterr().err
    assert _result(_send(server, _modern("tools/list", ident=10)))["tools"]


def test_a_result_json_cannot_spell_is_an_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = Tool(
        name="runs",
        title="t",
        description="d",
        arguments=mcp_tools.RunsArguments,
        read_only=True,
        run=lambda box, arguments: Outcome({"value": float("nan")}),
    )
    monkeypatch.setattr(mcp_tools, "_TOOLS", (tool,))

    answer = _send(
        Server(ToolBox()), _modern("tools/call", ident=4, name="runs", arguments={"path": "x"})
    )
    assert answer["id"] == 4
    _error(answer, INTERNAL_ERROR)


def test_an_answer_of_the_server_json_cannot_spell_is_an_internal_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(mcp_server, "_server_info", lambda: {"name": "x", "version": float("nan")})
    server = Server(ToolBox())

    answer = _send(server, _modern("server/discover", ident=6))
    assert answer["id"] == 6
    _error(answer, INTERNAL_ERROR)
    assert "ValueError" in capsys.readouterr().err
    assert _result(_send(server, _request("ping", ident=7))) == {}


@pytest.mark.parametrize("failure", [RecursionError, ValueError, TypeError, OverflowError])
def test_any_failure_to_encode_an_answer_is_an_internal_error(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    encode = mcp_server._encode
    failed: list[bool] = []

    def flaky(message: dict[str, Any]) -> bytes:
        if not failed:
            failed.append(True)
            raise failure("the encoder gave up")
        return encode(message)

    monkeypatch.setattr(mcp_server, "_encode", flaky)
    server = Server(ToolBox())

    answer = _send(server, _modern("tools/list", ident=8))
    assert answer["id"] == 8
    _error(answer, INTERNAL_ERROR)
    assert _result(_send(server, _modern("tools/list", ident=9)))["tools"]


def test_what_reaches_the_wire_is_ascii_on_one_line() -> None:
    path = "отчёт\nverdict: pass "
    message = _modern("tools/call", name="runs", arguments={"path": path})

    raw = _line(Server(ToolBox()), message)
    assert raw is not None and raw.isascii() and b"\n" not in raw and b"\r" not in raw
    error = json.loads(raw)["result"]["structuredContent"]["error"]
    assert "отчёт" in error


# --- the tool list -------------------------------------------------------------


def _tools(box: ToolBox) -> list[dict[str, Any]]:
    return list(_result(_send(Server(box), _modern("tools/list")))["tools"])


def test_without_an_evidence_directory_the_server_offers_no_verify() -> None:
    assert [tool["name"] for tool in _tools(ToolBox())] == [
        "validate",
        "impact",
        "evidence",
        "runs",
    ]


def test_with_an_evidence_directory_the_server_offers_verify(tmp_path: Path) -> None:
    tools = _tools(ToolBox(evidence_dir=tmp_path))

    assert [tool["name"] for tool in tools] == ["validate", "impact", "verify", "evidence", "runs"]
    assert tools == _tools(ToolBox(evidence_dir=tmp_path))  # the same order every time


def test_only_verify_says_it_writes(tmp_path: Path) -> None:
    for tool in _tools(ToolBox(evidence_dir=tmp_path)):
        annotations = tool["annotations"]
        assert annotations["openWorldHint"] is False
        if tool["name"] == "verify":
            assert annotations["readOnlyHint"] is False
            assert annotations["destructiveHint"] is False
            assert annotations["idempotentHint"] is False
        else:
            assert annotations["readOnlyHint"] is True
            assert "destructiveHint" not in annotations


@pytest.mark.parametrize("tool", mcp_tools._TOOLS, ids=lambda tool: tool.name)
def test_each_input_schema_names_the_arguments_the_tool_takes(tool: Tool) -> None:
    schema = input_schema(tool.arguments)

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert list(schema["properties"]) == list(tool.arguments.model_fields)
    required = [name for name, spec in tool.arguments.model_fields.items() if spec.is_required()]
    assert schema.get("required", []) == required
    assert "title" not in schema
    assert all("title" not in spec for spec in schema["properties"].values())
    assert all(spec.get("description") for spec in schema["properties"].values())
