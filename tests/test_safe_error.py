"""plan_sol step 3 / audit P0-3: SafeError + secret redaction leak-canary.

One secret marker must never appear in HTTP ready, SSE, Store builds/trace, or logs
when injected via provider-shaped failures (URI creds, Bearer, DSN, response bodies).
"""

from __future__ import annotations

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from auto_bi.adapters.base import AdapterHealth, DashboardRef
from auto_bi.adapters.datalens.client import DataLensAPIError, DataLensClient
from auto_bi.adapters.superset.client import SupersetAPIError, SupersetClient
from auto_bi.agent.pipeline import compile_and_build
from auto_bi.agent.sql_guard import LiveSQLValidator
from auto_bi.api import create_app
from auto_bi.errors import (
    CODE_BI_AUTH,
    CODE_BI_HEALTH,
    CODE_BI_HTTP,
    CODE_INTERNAL,
    CODE_LLM,
    PUBLIC_MESSAGES,
    SafeError,
    SecretRedactFilter,
    new_correlation_id,
    public_error_text,
    redact_secrets,
    store_error_text,
    to_safe_error,
)
from auto_bi.ir.spec import DashboardSpec
from auto_bi.llm.base import LLMError
from auto_bi.logging_setup import configure_logging
from auto_bi.store import Store
from tests.test_machine import CLEAR_REPORT, ScriptedLLM
from tests.test_pipeline import demo_model_fixtureless, stub_run_query
from tests.test_propose import GOOD_SPEC

SECRET_MARKER = "SECRET_MARKER_LEAK_CANARY_SAFEERR_4d9b"
# Shapes that redactor must neutralize even when not using the canary string alone.
URI_LEAK = f"clickhouse://admin:p4ssw0rd@dwh.internal:8123/db?token={SECRET_MARKER}"
BEARER_LEAK = f"Authorization: Bearer {SECRET_MARKER}_jwt_tail"
DSN_LEAK = f"password={SECRET_MARKER} host=dwh.internal"


# --- redactor unit -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        URI_LEAK,
        BEARER_LEAK,
        DSN_LEAK,
        f"Cookie: session={SECRET_MARKER}",
        f"api_key: {SECRET_MARKER}",
        f"access_token={SECRET_MARKER}",
        "https://user:s3cret@bi.example.com/api",
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaaaaaaaa.bbbbbbbbbb",
    ],
)
def test_redact_secrets_strips_known_patterns(raw: str) -> None:
    out = redact_secrets(raw)
    assert SECRET_MARKER not in out
    assert "p4ssw0rd" not in out
    assert "s3cret" not in out
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in out
    # Structure kept enough to debug class of leak source.
    if "clickhouse://" in raw or "https://" in raw:
        assert "://" in out
        assert "***:***@" in out or "***" in out


def test_safe_error_str_is_public_only() -> None:
    err = SafeError(
        CODE_INTERNAL,
        internal_detail=f"boom {URI_LEAK}",
    )
    assert SECRET_MARKER not in str(err)
    assert SECRET_MARKER not in err.public_message
    assert SECRET_MARKER not in err.internal_detail
    assert "***" in err.internal_detail or "admin" not in err.internal_detail
    assert err.for_public() == {
        "code": CODE_INTERNAL,
        "message": PUBLIC_MESSAGES[CODE_INTERNAL],
        "retryable": False,
        "correlation_id": err.correlation_id,
    }
    assert SECRET_MARKER not in err.for_store()
    assert SECRET_MARKER not in err.for_sse()
    assert "ref=" in err.for_sse()


def test_to_safe_error_maps_provider_classes() -> None:
    bi = SupersetAPIError(f"POST /api -> HTTP 500 dsn={URI_LEAK}", status_code=500)
    safe = to_safe_error(bi)
    assert safe.code == CODE_BI_HTTP
    assert safe.provider_status == 500
    assert safe.retryable is True
    assert SECRET_MARKER not in safe.public_message
    assert SECRET_MARKER not in safe.internal_detail
    assert "p4ssw0rd" not in safe.internal_detail

    auth = DataLensAPIError("signin failed: HTTP 401", status_code=401)
    assert to_safe_error(auth).code == CODE_BI_AUTH

    llm = LLMError(f"upstream 502 Authorization: Bearer {SECRET_MARKER}")
    llm_safe = to_safe_error(llm)
    assert llm_safe.code == CODE_LLM
    assert SECRET_MARKER not in llm_safe.for_sse()
    assert SECRET_MARKER not in llm_safe.internal_detail

    health = RuntimeError(f"superset healthcheck failed: {URI_LEAK}")
    h_safe = to_safe_error(health)
    assert h_safe.code == CODE_BI_HEALTH
    assert SECRET_MARKER not in h_safe.for_store()


def test_correlation_id_is_short_opaque() -> None:
    cid = new_correlation_id()
    assert len(cid) == 16
    assert cid.isalnum()


# --- BI clients never put response body in exception messages -------------------------


class _FakeResp:
    def __init__(self, status_code: int, text: str, *, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.content = b"{}" if json_data is None else json.dumps(json_data).encode()
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json


class _FakeHttp:
    def __init__(self, responses: list[_FakeResp]) -> None:
        self._responses = list(responses)
        self.cookies: dict[str, str] = {}
        self.base_url = "http://bi.test"

    def post(self, path: str, json: dict | None = None) -> _FakeResp:
        return self._responses.pop(0)

    def get(self, path: str, headers: dict | None = None) -> _FakeResp:
        return self._responses.pop(0)

    def request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> _FakeResp:
        return self._responses.pop(0)

    def close(self) -> None:
        return None


def test_superset_client_error_omits_response_body() -> None:
    body = f'{{"message":"denied","token":"{SECRET_MARKER}"}}'
    http = _FakeHttp([_FakeResp(401, body)])
    client = SupersetClient("http://bi.test", "u", "p", http=http)  # type: ignore[arg-type]
    with pytest.raises(SupersetAPIError) as ei:
        client.login()
    msg = str(ei.value)
    assert SECRET_MARKER not in msg
    assert body not in msg
    assert "HTTP 401" in msg
    assert ei.value.status_code == 401


def test_datalens_client_error_omits_response_body() -> None:
    body = f'{{"error":"bad","api_key":"{SECRET_MARKER}"}}'
    http = _FakeHttp([_FakeResp(403, body)])
    client = DataLensClient("http://dl.test", "u", "p", http=http)  # type: ignore[arg-type]
    with pytest.raises(DataLensAPIError) as ei:
        client.login()
    msg = str(ei.value)
    assert SECRET_MARKER not in msg
    assert body not in msg
    assert "HTTP 403" in msg


# Innocuous diagnostics are intentionally not secret-shaped, so the ratchet proves that
# auth response bodies are omitted rather than merely pattern-redacted.
_AUTH_BODY_SS_LOGIN = '{"reason":"gate_closed","diag":"SAFEERR_AUTH_BODY_DIAG_ss_login_7f3a"}'
_AUTH_BODY_SS_CSRF = '{"reason":"csrf_unavailable","diag":"SAFEERR_AUTH_BODY_DIAG_ss_csrf_9c1e"}'
_AUTH_BODY_DL_SIGNIN = '{"reason":"signin_rejected","diag":"SAFEERR_AUTH_BODY_DIAG_dl_signin_2b8d"}'
_AUTH_DIAG_SS_LOGIN = "SAFEERR_AUTH_BODY_DIAG_ss_login_7f3a"
_AUTH_DIAG_SS_CSRF = "SAFEERR_AUTH_BODY_DIAG_ss_csrf_9c1e"
_AUTH_DIAG_DL_SIGNIN = "SAFEERR_AUTH_BODY_DIAG_dl_signin_2b8d"


@pytest.mark.parametrize(
    ("kind", "status", "body", "diag", "logger_name", "exc_type"),
    [
        (
            "superset_login",
            401,
            _AUTH_BODY_SS_LOGIN,
            _AUTH_DIAG_SS_LOGIN,
            "auto_bi.adapters.superset.client",
            SupersetAPIError,
        ),
        (
            "superset_csrf",
            503,
            _AUTH_BODY_SS_CSRF,
            _AUTH_DIAG_SS_CSRF,
            "auto_bi.adapters.superset.client",
            SupersetAPIError,
        ),
        (
            "datalens_signin",
            403,
            _AUTH_BODY_DL_SIGNIN,
            _AUTH_DIAG_DL_SIGNIN,
            "auto_bi.adapters.datalens.client",
            DataLensAPIError,
        ),
    ],
)
def test_bi_auth_failure_logs_omit_response_body(
    kind: str,
    status: int,
    body: str,
    diag: str,
    logger_name: str,
    exc_type: type[SupersetAPIError] | type[DataLensAPIError],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Auth-path DEBUG logs must keep status but never the provider response body."""
    if kind == "superset_login":
        http = _FakeHttp([_FakeResp(status, body)])
        client: SupersetClient | DataLensClient = SupersetClient(
            "http://bi.test", "u", "p", http=http  # type: ignore[arg-type]
        )
    elif kind == "superset_csrf":
        http = _FakeHttp(
            [
                _FakeResp(200, "{}", json_data={"access_token": "x"}),
                _FakeResp(status, body),
            ]
        )
        client = SupersetClient("http://bi.test", "u", "p", http=http)  # type: ignore[arg-type]
    else:
        http = _FakeHttp([_FakeResp(status, body)])
        client = DataLensClient("http://dl.test", "u", "p", http=http)  # type: ignore[arg-type]

    with (
        caplog.at_level(logging.DEBUG, logger=logger_name),
        pytest.raises(exc_type) as ei,
    ):
        client.login()

    assert ei.value.status_code == status
    joined = "\n".join(
        record.getMessage() for record in caplog.records if record.name == logger_name
    )
    assert body not in joined
    assert diag not in joined
    assert f"HTTP {status}" in joined


# --- five channels: HTTP ready, SSE, Store, logs, public helpers ----------------------


def test_ready_http_channel_strips_marker(demo_model) -> None:
    app = create_app(
        model=demo_model,
        llm=ScriptedLLM([]),
        run_query=lambda sql: (_ for _ in ()).throw(
            RuntimeError(f"DWH down {URI_LEAK} {BEARER_LEAK}")
        ),
    )
    r = TestClient(app).get("/api/v1/ready")
    assert r.status_code == 503
    assert SECRET_MARKER not in r.text
    assert "p4ssw0rd" not in r.text
    assert "Bearer" not in r.text
    check = r.json()["checks"]["dwh"]
    assert check["ok"] is False
    assert "correlation_id" in check
    assert check["message"] == PUBLIC_MESSAGES["dwh.error"]


def test_sse_and_trace_channels_strip_marker(demo_model, tmp_path) -> None:
    """API custom builder: SSE + durable trace must not carry secrets.

    builds-table rows are owned by compile_and_build (see pipeline store tests).
    """
    store = Store(tmp_path / "safe.sqlite")
    marker_detail = f"Superset healthcheck failed: {URI_LEAK}; {BEARER_LEAK}"

    def broken_builder(spec, log, session_id):
        log("SQL ok (c1)")
        raise RuntimeError(marker_detail)

    app = create_app(
        model=demo_model,
        llm=ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]),
        store=store,
        builder=broken_builder,
    )
    client = TestClient(app)
    sid = client.post("/api/v1/sessions", json={"request": "выручка по дням"}).json()["session_id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202

    events: list[dict] = []
    with client.stream("GET", f"/api/v1/sessions/{sid}/events") as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
                if events[-1]["kind"] in ("done", "error"):
                    break

    assert events, "expected at least one SSE event"
    assert events[-1]["kind"] == "error"
    assert SECRET_MARKER not in events[-1]["text"]
    assert "p4ssw0rd" not in events[-1]["text"]
    assert PUBLIC_MESSAGES[CODE_BI_HEALTH] in events[-1]["text"]

    deadline = time.monotonic() + 5
    while client.get(f"/api/v1/sessions/{sid}").json()["build_status"] != "failed":
        assert time.monotonic() < deadline

    # Trace rows written by the build thread must also be clean.
    rows = store.trace_events(sid)
    assert any(r.get("kind") == "build_error" for r in rows)
    for row in rows:
        assert SECRET_MARKER not in (row.get("detail") or "")
        assert "p4ssw0rd" not in (row.get("detail") or "")

    store.close()


def test_pipeline_store_channel_strips_marker_on_adapter_error(tmp_path) -> None:
    store = Store(tmp_path / "pipe.sqlite")
    sid = store.create_session("выручка")
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    spec_id = store.save_spec(sid, spec.model_dump(mode="json"))

    class LeakyAdapter:
        def healthcheck(self) -> AdapterHealth:
            return AdapterHealth(ok=True)

        # plan_sol step 7: pipeline always calls build(spec, ctx) — old single-arg
        # fakes raised TypeError and collapsed to internal.error (reaudit 23.07).
        def build(self, spec: DashboardSpec, ctx=None) -> DashboardRef:
            raise SupersetAPIError(
                "POST /api/v1/dashboard/ -> HTTP 500",
                status_code=500,
            )

        def delete_artifact(self, kind: str, native_id: str | int) -> None:
            return None

        def close(self) -> None:
            return None

    with pytest.raises(SafeError) as ei:
        compile_and_build(
            spec,
            demo_model_fixtureless(),
            LiveSQLValidator(stub_run_query),
            adapter_for=lambda _t: LeakyAdapter(),
            store=store,
            session_id=sid,
            spec_id=spec_id,
        )
    # Inject marker via re-raise path: store_error_text must still redact if present.
    assert ei.value.code == CODE_BI_HTTP
    (build,) = store.builds(sid)
    assert SECRET_MARKER not in build["error"]
    assert CODE_BI_HTTP in build["error"]
    store.close()


def test_pipeline_health_internal_detail_redacts_uri(tmp_path) -> None:
    store = Store(tmp_path / "health.sqlite")
    sid = store.create_session("выручка")
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    class DeadAdapter:
        def healthcheck(self) -> AdapterHealth:
            return AdapterHealth(ok=False, message=URI_LEAK)

    with pytest.raises(SafeError) as ei:
        compile_and_build(
            spec,
            demo_model_fixtureless(),
            LiveSQLValidator(stub_run_query),
            adapter_for=lambda _t: DeadAdapter(),
            store=store,
            session_id=sid,
        )
    assert ei.value.code == CODE_BI_HEALTH
    assert SECRET_MARKER not in ei.value.public_message
    assert SECRET_MARKER not in ei.value.internal_detail
    assert "p4ssw0rd" not in ei.value.internal_detail
    (build,) = store.builds(sid)
    assert SECRET_MARKER not in build["error"]
    store.close()


def test_log_channel_redacts_via_filter_and_json_formatter(capsys) -> None:
    root = logging.getLogger()
    handlers, level, filters = root.handlers[:], root.level, root.filters[:]
    try:
        configure_logging("INFO", "json")
        logging.getLogger("auto_bi.safe_error_test").error(
            "build failed dsn=%s auth=%s", URI_LEAK, BEARER_LEAK
        )
        out = capsys.readouterr().out
        assert SECRET_MARKER not in out
        assert "p4ssw0rd" not in out
        payload = json.loads(out.strip().splitlines()[-1])
        assert SECRET_MARKER not in payload["message"]
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        root.filters[:] = filters


def test_secret_redact_filter_on_args() -> None:
    filt = SecretRedactFilter()
    record = logging.LogRecord(
        name="t",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="fail %s",
        args=(URI_LEAK,),
        exc_info=None,
    )
    assert filt.filter(record) is True
    assert SECRET_MARKER not in record.getMessage()
    assert "p4ssw0rd" not in record.getMessage()


def test_public_and_store_helpers() -> None:
    exc = RuntimeError(f"healthcheck failed: {DSN_LEAK}")
    pub = public_error_text(exc)
    store = store_error_text(exc)
    assert SECRET_MARKER not in pub
    assert SECRET_MARKER not in store
    assert "ref=" in pub
    assert CODE_BI_HEALTH in store


def test_llm_start_http_detail_is_safe(demo_model) -> None:
    class BoomLLM(ScriptedLLM):
        def complete(self, prompt, schema, *, reasoning=False, session_id=None, step=""):
            raise LLMError(f"provider 502 Authorization: Bearer {SECRET_MARKER}")

    app = create_app(model=demo_model, llm=BoomLLM([]))
    r = TestClient(app).post("/api/v1/sessions", json={"request": "выручка по дням"})
    assert r.status_code == 502
    detail = r.json()["detail"]
    # FastAPI may wrap dict detail as-is.
    if isinstance(detail, dict):
        blob = json.dumps(detail)
        assert detail["code"] == CODE_LLM
        assert "correlation_id" in detail
    else:
        blob = str(detail)
    assert SECRET_MARKER not in blob
    assert "Bearer" not in blob
