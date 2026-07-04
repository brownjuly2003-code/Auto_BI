"""Happy-path wiring test: fake LLM + stub DWH + fake Superset -> DashboardRef."""

import pytest

from auto_bi.agent.pipeline import build_dashboard, compile_and_build
from auto_bi.agent.sql_guard import LiveSQLValidator, SQLGuardError
from auto_bi.ir.spec import DashboardSpec
from tests.test_propose import GOOD_SPEC, FakeLLM
from tests.test_superset_adapter import FakeSuperset, make_adapter


def stub_run_query(sql: str) -> list[dict]:
    return [{"explain": "Expression"}]


def test_build_dashboard_happy_path() -> None:
    log: list[str] = []
    ref = build_dashboard(
        "выручка по дням",
        demo_model_fixtureless(),
        llm=FakeLLM([GOOD_SPEC]),
        sql_validator=LiveSQLValidator(stub_run_query),
        adapter_for=lambda _target: make_adapter(FakeSuperset()),
        log=log.append,
    )
    assert ref.url.startswith("/superset/dashboard/")
    assert any("PROPOSE_SPEC" in line for line in log)
    assert any("SQL ok" in line for line in log)
    assert any("BUILD done" in line for line in log)


def test_build_dashboard_stops_on_sql_failure() -> None:
    def failing_run(sql: str) -> list[dict]:
        raise RuntimeError("Unknown column")

    fake_superset = FakeSuperset()
    with pytest.raises(SQLGuardError):
        build_dashboard(
            "выручка по дням",
            demo_model_fixtureless(),
            llm=FakeLLM([GOOD_SPEC]),
            sql_validator=LiveSQLValidator(failing_run),
            adapter_for=lambda _target: make_adapter(fake_superset),
            log=lambda s: None,
        )
    # nothing was created in the BI after SQL validation failed
    assert not any(m == "POST" and "chart" in p for m, p, _ in fake_superset.requests)


def test_build_dashboard_records_spec_and_build_in_store(tmp_path) -> None:
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням")
    ref = build_dashboard(
        "выручка по дням",
        demo_model_fixtureless(),
        llm=FakeLLM([GOOD_SPEC]),
        sql_validator=LiveSQLValidator(stub_run_query),
        adapter_for=lambda _target: make_adapter(FakeSuperset()),
        log=lambda s: None,
        store=store,
        session_id=sid,
    )
    (spec_row,) = store.specs(sid)
    assert spec_row["spec_json"]["title"] == GOOD_SPEC["title"]
    (build_row,) = store.builds(sid)
    assert build_row["spec_id"] == spec_row["id"]
    assert build_row["url"] == ref.url
    assert build_row["status"] == "ok"
    store.close()


def test_compile_and_build_marks_session_building_then_failed_on_sql_error(tmp_path) -> None:
    # B-7: before this fix, a SQL-guard failure (as opposed to an adapter.build() failure)
    # never wrote a builds-table row or flipped the session to 'failed' — it just vanished.
    from auto_bi.store import Store

    def failing_run(sql: str) -> list[dict]:
        raise RuntimeError("Unknown column")

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням")
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    spec_id = store.save_spec(sid, spec.model_dump(mode="json"))
    fake_superset = FakeSuperset()

    with pytest.raises(SQLGuardError):
        compile_and_build(
            spec,
            demo_model_fixtureless(),
            LiveSQLValidator(failing_run),
            adapter_for=lambda _target: make_adapter(fake_superset),
            store=store,
            session_id=sid,
            spec_id=spec_id,
        )

    assert store.session_status(sid) == "failed"
    (build,) = store.builds(sid)
    assert build["status"] == "failed"
    assert not any(m == "POST" and "chart" in p for m, p, _ in fake_superset.requests)
    store.close()


def test_compile_and_build_marks_session_failed_on_healthcheck_failure(tmp_path) -> None:
    from auto_bi.adapters.base import AdapterHealth
    from auto_bi.store import Store

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка по дням")
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    class DeadAdapter:
        def healthcheck(self) -> AdapterHealth:
            return AdapterHealth(ok=False, message="superset unreachable")

    with pytest.raises(RuntimeError, match="healthcheck failed"):
        compile_and_build(
            spec,
            demo_model_fixtureless(),
            LiveSQLValidator(stub_run_query),
            adapter_for=lambda _target: DeadAdapter(),
            store=store,
            session_id=sid,
        )

    assert store.session_status(sid) == "failed"
    (build,) = store.builds(sid)
    assert build["status"] == "failed"
    assert "healthcheck failed" in build["error"]
    store.close()


def demo_model_fixtureless():
    """conftest's demo_model as a plain call (this test composes fixtures manually)."""
    from tests.conftest import demo_model

    return demo_model.__wrapped__()
