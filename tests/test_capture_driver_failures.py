"""A failed capture publishes no partial result and reports one driver-error line."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import clickhouse_connect
import psycopg
import pytest
import yaml
from clickhouse_connect.driver.exceptions import OperationalError, StreamFailureError
from clickhouse_connect.driver.query import QueryResult as DriverResult

from auto_bi import cli, config
from auto_bi.config import Settings
from auto_bi.verification.result_probe import (
    ResultCaptureError,
    make_clickhouse_result_query,
    make_postgres_result_query,
)
from tests.test_decision_verify import (
    ASSET_ID,
    CREATED_AT,
    REFERENCE_SQL,
    Case,
    _write_yaml,
    verified_asset,
)

FAILURES = (
    [
        pytest.param("clickhouse", error, stage, id=f"ch-{error.__name__}-{stage}")
        for error in (OperationalError, StreamFailureError)
        for stage in ("connection", "query", "metadata", "rows")
    ]
    + [
        pytest.param("postgres", psycopg.OperationalError, stage, id=f"pg-{stage}")
        for stage in ("connection", "query", "metadata", "rows", "rollback")
    ]
    + [
        pytest.param("postgres", psycopg.errors.DataCorrupted, "rows", id="pg-data-corrupted"),
        pytest.param("greenplum", psycopg.errors.QueryCanceled, "query", id="gp-cancelled"),
        pytest.param("greengage", psycopg.OperationalError, "connection", id="gg-connection"),
        pytest.param("postgres", psycopg.errors.ConnectionTimeout, "connection", id="pg-timeout"),
    ]
)


def _driver(
    monkeypatch: pytest.MonkeyPatch, engine: str, stage: str, error: Exception
) -> list[str]:
    """Replace driver IO, retaining ClickHouse's real lazy block materialization."""
    calls: list[str] = []

    def fail_at(where: str) -> None:
        calls.append(where)
        if where == stage:
            raise error

    def blocks() -> Iterator[list[list[Any]]]:
        yield [["Москва"], [Decimal("0.412")]]
        fail_at("rows")

    def ch_query(sql: str, *, tz_mode: str) -> DriverResult:
        assert tz_mode == "aware"
        if calls == ["connection"]:
            assert sql == REFERENCE_SQL
            fail_at("query")
            if stage == "metadata":
                return DriverResult(result_set=[], column_names=())
        else:
            statement = REFERENCE_SQL.strip().rstrip(";")
            assert sql == f"SELECT * FROM (\n{statement}\n) AS capture_metadata LIMIT 0"
            fail_at("metadata")
        return DriverResult(
            column_names=("region", "gross_margin_pct"),
            column_types=(
                SimpleNamespace(name="String", nullable=False),
                SimpleNamespace(name="Decimal(18, 6)", nullable=False),
            ),
            block_gen=blocks(),
        )

    def execute(sql: str) -> None:
        if sql.startswith(("SET ", "SHOW ")):
            return
        if sql == "SAVEPOINT capture_query":
            fail_at("savepoint")
            return
        if sql == "ROLLBACK TO SAVEPOINT capture_query":
            fail_at("rollback to savepoint")
            return
        if stage == "metadata":
            statement = REFERENCE_SQL.strip().rstrip(";")
            if sql == f"SELECT * FROM (\n{statement}\n) AS capture_metadata LIMIT 0":
                fail_at("metadata")
                return
        assert sql == REFERENCE_SQL
        fail_at("query")

    def fetchall() -> list[tuple[str, Decimal]]:
        fail_at("rows")
        if stage == "metadata":
            return []
        return [("Москва", Decimal("0.412"))]

    def stream(sql, *, size):
        assert size == 1
        execute(sql)
        yield from fetchall()

    def ch_stream(sql, **kwargs):
        return ch_query(sql, tz_mode=kwargs["tz_mode"]).rows_stream

    @contextmanager
    def cursor() -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(
            execute=execute,
            description=(
                None
                if stage == "metadata"
                else (
                    SimpleNamespace(name="region", type_display="text", null_ok=None),
                    SimpleNamespace(name="gross_margin_pct", type_display="numeric", null_ok=None),
                )
            ),
            fetchall=fetchall,
            fetchone=lambda: ("on",),
            stream=stream,
        )

    depth = 0
    info = SimpleNamespace(
        parameter_status={"client_encoding": "UTF8", "standard_conforming_strings": "on"}.get,
        transaction_status=psycopg.pq.TransactionStatus.IDLE,
    )

    @contextmanager
    def transaction(*, force_rollback=False) -> Iterator[None]:
        nonlocal depth
        assert depth == 0
        depth += 1
        info.transaction_status = psycopg.pq.TransactionStatus.INTRANS
        calls.append("begin")
        try:
            yield
        except Exception:
            calls.append("rollback")
            raise
        else:
            fail_at("rollback" if force_rollback else "commit")
        finally:
            depth -= 1
            info.transaction_status = psycopg.pq.TransactionStatus.IDLE

    def connect(**_kwargs: Any) -> SimpleNamespace:
        fail_at("connection")
        return SimpleNamespace(
            query=ch_query,
            query_rows_stream=ch_stream,
            command=lambda sql, **kwargs: (
                [2, 0] + ["throw"] * (sql.count("getSetting") - 2)
                if "max_execution_time" in sql
                else 2
            ),
            cursor=cursor,
            transaction=transaction,
            info=info,
        )

    settings = Settings(_env_file=None)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    if engine == "clickhouse":
        monkeypatch.setattr(clickhouse_connect, "get_client", connect)
    else:
        monkeypatch.setattr(psycopg, "connect", connect)
    return calls


def _capture_args(tmp_path: Path, engine: str) -> tuple[list[str], Path]:
    case = Case(tmp_path).with_candidate().with_asset()
    asset = verified_asset(case.base_digest)
    asset["reference_query"]["engine"] = engine
    assert case.assets is not None
    _write_yaml(case.assets / "weekly_margin_by_region.yaml", asset)
    output = tmp_path / "capture" / "result.yaml"
    return [
        "decision",
        "capture",
        str(case.assets),
        "--candidate",
        str(case.candidate),
        "--asset",
        ASSET_ID,
        "--dwh",
        engine,
        "--captured-at",
        CREATED_AT,
        "--output",
        str(output),
    ], output


@pytest.mark.parametrize(("engine", "error_type", "stage"), FAILURES)
@pytest.mark.parametrize("message", ["  connection\r\n\tclosed\u2028by server  ", " \n\t"])
def test_capture_driver_failure_is_one_line_without_a_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
    error_type: type[Exception],
    stage: str,
    message: str,
) -> None:
    argv, output = _capture_args(tmp_path, engine)
    calls = _driver(monkeypatch, engine, stage, error_type(message))

    assert cli.main(argv) == 2

    captured = capsys.readouterr()
    family = "ClickHouse" if engine == "clickhouse" else "PostgreSQL"
    operation = (
        "connection"
        if stage == "connection"
        else "session preparation" if stage == "rollback" else "query"
    )
    detail = error_type.__name__ + (": connection closed by server" if message.strip() else "")
    assert captured.out == ""
    assert (
        captured.err == f"error: cannot capture {ASSET_ID}: {family} {operation} failed: {detail}\n"
    )
    assert not output.parent.exists()
    expected_calls = ["connection"]
    if stage != "connection":
        if engine != "clickhouse":
            expected_calls.extend(["begin", "savepoint"])
        expected_calls.append("query")
    if engine != "clickhouse" and stage == "metadata":
        expected_calls.extend(["rows", "rollback to savepoint"])
    if stage in {"metadata", "rows"}:
        expected_calls.append(stage)
    if stage == "rollback":
        expected_calls.extend(["rows", "rollback to savepoint", "rollback"])
    elif engine != "clickhouse" and stage != "connection":
        expected_calls.append("rollback")
    assert calls == expected_calls  # no retry, including after a partial stream


@pytest.mark.parametrize(
    "engine,stage",
    [
        ("clickhouse", "rows"),
        ("postgres", "rows"),
        ("postgres", "metadata"),
        ("postgres", "rollback"),
    ],
)
def test_adapter_reports_a_port_error_before_the_use_case_sees_it(
    monkeypatch: pytest.MonkeyPatch, engine: str, stage: str
) -> None:
    error = (
        StreamFailureError("lost\nresponse")
        if engine == "clickhouse"
        else psycopg.DataError("lost\nresponse")
    )
    _driver(monkeypatch, engine, stage, error)
    factory = make_clickhouse_result_query if engine == "clickhouse" else make_postgres_result_query
    run_query = factory(Settings(_env_file=None))

    with pytest.raises(ResultCaptureError) as raised:
        run_query(REFERENCE_SQL)

    family = "ClickHouse" if engine == "clickhouse" else "PostgreSQL"
    operation = "session preparation" if stage == "rollback" else "query"
    assert (
        str(raised.value) == f"{family} {operation} failed: {type(error).__name__}: lost response"
    )


@pytest.mark.parametrize(
    ("engine", "stage"),
    [
        (engine, stage)
        for engine in ("clickhouse", "postgres")
        for stage in ("connection", "query", "rows")
    ]
    + [("clickhouse", "metadata"), ("postgres", "metadata")],
)
def test_non_driver_failure_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, stage: str
) -> None:
    argv, output = _capture_args(tmp_path, engine)
    error = RuntimeError("adapter fault")
    _driver(monkeypatch, engine, stage, error)

    with pytest.raises(RuntimeError) as raised:
        cli.main(argv)

    assert raised.value is error
    assert not output.parent.exists()


@pytest.mark.parametrize("engine", ["clickhouse", "postgres"])
def test_answered_capture_keeps_its_canonical_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    argv, output = _capture_args(tmp_path, engine)
    calls = _driver(monkeypatch, engine, "never", RuntimeError("not raised"))

    assert cli.main(argv) == 0

    recorded = yaml.safe_load(output.read_text(encoding="utf-8"))[ASSET_ID]
    assert recorded["result"]["rows"] == [["Москва", "0.412"]]
    assert recorded["attestation"]["captured_at"] == CREATED_AT
    assert calls == (
        ["connection", "query", "rows"]
        if engine == "clickhouse"
        else [
            "connection",
            "begin",
            "savepoint",
            "query",
            "rows",
            "rollback to savepoint",
            "rollback",
        ]
    )
