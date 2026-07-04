"""GET /api/v1/ready (B-6): deep readiness beyond `/health`'s liveness check.

Store, DWH connectivity (`SELECT 1`) and BI reachability gate `ok`; LLM reachability is
reported but never gates it — a transient LLM/GraceKelly outage still lets an
already-built dashboard keep serving traffic.
"""

from fastapi.testclient import TestClient

from auto_bi.adapters.base import AdapterHealth
from auto_bi.api import create_app
from auto_bi.store import Store
from tests.test_machine import ScriptedLLM


def _client(demo_model, **kwargs) -> TestClient:
    app = create_app(model=demo_model, llm=ScriptedLLM([]), **kwargs)
    return TestClient(app)


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
    assert body["checks"]["store"]["ok"] is False


def test_ready_reports_dwh_failure_and_503s(demo_model) -> None:
    def failing_run_query(sql: str) -> list[dict]:
        raise RuntimeError("connection refused")

    client = _client(demo_model, run_query=failing_run_query)
    r = client.get("/api/v1/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["dwh"]["ok"] is False


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
        demo_model, bi_healthcheck=lambda: AdapterHealth(ok=False, message="superset down")
    )
    r = client.get("/api/v1/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["checks"]["bi"] == {"ok": False, "message": "superset down"}


def test_ready_llm_failure_is_advisory_only(demo_model) -> None:
    # the defining contract of B-6: LLM is checked and reported, but never flips `ok`
    client = _client(
        demo_model,
        bi_healthcheck=lambda: AdapterHealth(ok=True),
        llm_healthcheck=lambda: AdapterHealth(ok=False, message="gracekelly down"),
    )
    r = client.get("/api/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["checks"]["llm"] == {"ok": False, "message": "gracekelly down"}


def test_ready_survives_a_raising_healthcheck_callable(demo_model) -> None:
    def boom() -> AdapterHealth:
        raise ConnectionError("no route to host")

    client = _client(demo_model, bi_healthcheck=boom)
    r = client.get("/api/v1/ready")
    assert r.status_code == 503
    assert "no route to host" in r.json()["checks"]["bi"]["message"]


def test_ready_is_reachable_without_auth_token(demo_model) -> None:
    client = _client(demo_model, auth_enabled=True)
    r = client.get("/api/v1/ready")
    assert r.status_code == 200
