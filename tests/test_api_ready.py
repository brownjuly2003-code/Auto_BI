"""GET /api/v1/ready (B-6): deep readiness beyond `/health`'s liveness check.

Store, DWH connectivity (`SELECT 1`) and BI reachability gate `ok`; LLM reachability is
reported but never gates it — a transient LLM/GraceKelly outage still lets an
already-built dashboard keep serving traffic.

plan_sol step 3: public failure faces are SafeError-shaped (code/message/correlation_id)
without raw provider/health text.
"""

from fastapi.testclient import TestClient

from auto_bi.adapters.base import AdapterHealth
from auto_bi.api import create_app
from auto_bi.errors import CODE_BI_HEALTH, CODE_DWH, CODE_LLM, CODE_STORE, PUBLIC_MESSAGES
from auto_bi.store import Store
from tests.test_machine import ScriptedLLM

SECRET_MARKER = "SECRET_MARKER_LEAK_CANARY_READY_7c1e"


def _client(demo_model, **kwargs) -> TestClient:
    app = create_app(model=demo_model, llm=ScriptedLLM([]), **kwargs)
    return TestClient(app)


def _assert_public_fail(check: dict, *, code: str) -> None:
    assert check["ok"] is False
    assert check["code"] == code
    assert check["message"] == PUBLIC_MESSAGES[code]
    assert isinstance(check["correlation_id"], str) and len(check["correlation_id"]) >= 8
    assert SECRET_MARKER not in str(check)
    assert "message" in check
    # No internal keys on the public face.
    assert "internal_detail" not in check
    assert "detail" not in check


def test_ready_ok_when_nothing_is_wired(demo_model) -> None:
    client = _client(demo_model)
    r = client.get("/api/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["checks"]["store"] == {"ok": True, "configured": False}
    assert body["checks"]["dwh"] == {"ok": True, "configured": False}
    assert body["checks"]["bi"] == {"ok": True, "configured": False}
    assert body["checks"]["llm"] == {"ok": True, "configured": False}


def test_ready_reports_store_failure_and_503s(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    store.close()  # any query now raises
    client = _client(demo_model, store=store)
    r = client.get("/api/v1/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    _assert_public_fail(body["checks"]["store"], code=CODE_STORE)


def test_ready_reports_dwh_failure_and_503s(demo_model) -> None:
    def failing_run_query(sql: str) -> list[dict]:
        raise RuntimeError(f"connection refused dsn=clickhouse://u:p@host/{SECRET_MARKER}")

    client = _client(demo_model, run_query=failing_run_query)
    r = client.get("/api/v1/ready")
    assert r.status_code == 503
    body = r.json()
    _assert_public_fail(body["checks"]["dwh"], code=CODE_DWH)
    assert SECRET_MARKER not in r.text


def test_ready_dwh_ok_runs_select_1(demo_model) -> None:
    seen: list[str] = []

    def run_query(sql: str) -> list[dict]:
        seen.append(sql)
        return [{"1": 1}]

    client = _client(demo_model, run_query=run_query)
    r = client.get("/api/v1/ready")
    assert r.status_code == 200
    assert seen == ["SELECT 1"]


def test_ready_bi_failure_gates_ok(demo_model) -> None:
    client = _client(
        demo_model,
        bi_healthcheck=lambda: AdapterHealth(
            ok=False, message=f"superset down token={SECRET_MARKER}"
        ),
    )
    r = client.get("/api/v1/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    _assert_public_fail(body["checks"]["bi"], code=CODE_BI_HEALTH)
    assert SECRET_MARKER not in r.text
    # Raw health message must not appear on unauthenticated ready.
    assert "superset down" not in r.text


def test_ready_llm_failure_is_advisory_only(demo_model) -> None:
    # the defining contract of B-6: LLM is checked and reported, but never flips `ok`
    client = _client(
        demo_model,
        bi_healthcheck=lambda: AdapterHealth(ok=True),
        llm_healthcheck=lambda: AdapterHealth(
            ok=False, message=f"gracekelly down key={SECRET_MARKER}"
        ),
    )
    r = client.get("/api/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    _assert_public_fail(body["checks"]["llm"], code=CODE_LLM)
    assert SECRET_MARKER not in r.text
    assert "gracekelly down" not in r.text


def test_ready_survives_a_raising_healthcheck_callable(demo_model) -> None:
    def boom() -> AdapterHealth:
        raise ConnectionError(f"no route to host secret={SECRET_MARKER}")

    client = _client(demo_model, bi_healthcheck=boom)
    r = client.get("/api/v1/ready")
    assert r.status_code == 503
    _assert_public_fail(r.json()["checks"]["bi"], code=CODE_BI_HEALTH)
    assert SECRET_MARKER not in r.text
    assert "no route to host" not in r.text


def test_ready_is_reachable_without_auth_token(demo_model) -> None:
    client = _client(demo_model, auth_enabled=True)
    r = client.get("/api/v1/ready")
    assert r.status_code == 200
