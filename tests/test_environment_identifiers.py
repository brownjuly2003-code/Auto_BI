"""An identifier the environment collector cannot quote is a CLI input error."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auto_bi import cli
from auto_bi.cli import main
from auto_bi.introspect import clickhouse, greenplum
from auto_bi.verification.environment import EnvironmentQuestions, TableQuestion
from auto_bi.verification.environment_probe import UnsafeIdentifierError, collect
from auto_bi.verification.model import EXIT_ERROR
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import Case, metric_contract
from tests.test_unanswered_engine import ENGINES, _answered_by, _catalog, _configure, _family

BAD_IDENTIFIERS = [
    pytest.param("event_time", "event time", "event time", id="event-time-space"),
    pytest.param("event_time", "event\n\tstamp\u2028x", "event\n\tstamp\u2028x", id="line-breaks"),
    pytest.param("table", "bad schema.sales_daily", "bad schema", id="schema-space"),
    pytest.param("table", "dm.sales-daily", "sales-daily", id="table-hyphen"),
    pytest.param("table", "dm..sales_daily", "", id="empty-table-component"),
]


def _case(tmp_path: Path, field: str, value: str) -> Case:
    contract = metric_contract()
    if field == "event_time":
        contract["time"]["event_time_column"] = value
    else:
        contract["grain"]["table"] = value
    return Case(tmp_path).with_candidate(contract=contract)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("field,value,rejected", BAD_IDENTIFIERS)
def test_unquotable_identifier_ends_verify_without_a_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
    field: str,
    value: str,
    rejected: str,
) -> None:
    _configure(monkeypatch, 8123)
    catalog = _catalog(engine)
    built = _answered_by(monkeypatch, engine, catalog)
    case = _case(tmp_path, field, value)

    assert main([*_verify_argv(case), "--dwh", engine]) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err == (
        f"error: cannot build environment query: unsafe identifier: {rejected!r}\n"
    )
    assert "Traceback" not in captured.err
    assert len(built) == 1
    assert catalog.asked
    assert not any("AS rows_seen" in sql for sql in catalog.asked)
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_unexpected_collector_errors_still_propagate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    error_type: type[Exception],
) -> None:
    # Even identical text must not turn an unrelated exception into an input refusal.
    error = error_type("unsafe identifier: 'unrelated bug'")

    def broken(_sql: str) -> list[dict[str, Any]]:
        raise error

    _configure(monkeypatch, 8123)
    _answered_by(monkeypatch, engine, broken)
    case = Case(tmp_path).with_candidate()

    with pytest.raises(error_type) as caught:
        main([*_verify_argv(case), "--dwh", engine])

    assert caught.value is error
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("engine", ["clickhouse", "postgres"])
def test_direct_collector_keeps_value_error_compatibility(engine: str) -> None:
    questions = EnvironmentQuestions(
        tables=[TableQuestion(table="dm.sales_daily", event_time_column="event time")]
    )

    with pytest.raises(ValueError) as caught:
        collect(_catalog(engine), questions, engine=engine, target="stand")

    assert type(caught.value) is UnsafeIdentifierError
    assert str(caught.value) == "unsafe identifier: 'event time'"


@pytest.mark.parametrize("engine", ENGINES)
def test_identifier_handler_does_not_cover_the_connection_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    error = UnsafeIdentifierError("factory error")

    def broken(_settings: Any, **kwargs: Any) -> Any:
        raise error

    _configure(monkeypatch, 8123)
    module, name = (
        (clickhouse, "make_run_query")
        if _family(engine) == "clickhouse"
        else (greenplum, "make_run_query_pg")
    )
    monkeypatch.setattr(module, name, broken)
    case = Case(tmp_path).with_candidate()

    with pytest.raises(UnsafeIdentifierError) as caught:
        main([*_verify_argv(case), "--dwh", engine])

    assert caught.value is error
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("engine", ENGINES)
def test_absent_table_is_still_a_fact_before_identifier_quoting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
) -> None:
    _configure(monkeypatch, 8123)
    catalog = _catalog(engine)
    catalog.answers["system.tables" if engine == "clickhouse" else "pg_tables"] = []
    _answered_by(monkeypatch, engine, catalog)
    case = _case(tmp_path, "event_time", "event time")

    assert main([*_verify_argv(case), "--dwh", engine]) != EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "status   completed" in captured.out
    assert list((case.root / "evidence").iterdir())
    assert not any("AS rows_seen" in sql for sql in catalog.asked)


def test_offline_verify_does_not_apply_the_collectors_quoting_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_settings() -> Any:
        pytest.fail("offline verification must not load DWH settings")

    monkeypatch.setattr(cli, "_decision_settings", unexpected_settings)
    case = _case(tmp_path, "event_time", "event time")

    assert main(_verify_argv(case)) != EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "status   completed" in captured.out
    assert list((case.root / "evidence").iterdir())


@pytest.mark.parametrize("engine", ["postgres", "greenplum", "greengage"])
def test_unreadable_postgres_table_is_a_fact_before_identifier_quoting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
) -> None:
    _configure(monkeypatch, 8123)
    catalog = _catalog(engine)
    catalog.answers["has_any_column_privilege"] = [{"ok": False}]
    _answered_by(monkeypatch, engine, catalog)
    case = _case(tmp_path, "event_time", "event time")

    assert main([*_verify_argv(case), "--dwh", engine]) != EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "status   completed" in captured.out
    assert list((case.root / "evidence").iterdir())
    assert not any("AS rows_seen" in sql for sql in catalog.asked)
