"""Read-only execution and finite probe limits with substituted drivers."""

from contextlib import contextmanager
from types import SimpleNamespace

import clickhouse_connect
import psycopg
import pytest
from pydantic import ValidationError

from auto_bi.config import Settings
from auto_bi.introspect.clickhouse import make_run_query
from auto_bi.introspect.greenplum import make_run_query_pg
from auto_bi.verification.result_probe import (
    QueryColumn,
    QueryResult,
    ResultCaptureError,
    make_clickhouse_result_query,
    make_postgres_result_query,
)
from auto_bi.verification.runtime_probe import (
    make_clickhouse_runtime_probe,
    make_postgres_runtime_probe,
)
from tests.test_runtime_probe import question


class Driver:
    def __init__(self, engine, rows=((1,),), readonly=None):
        self.engine = engine
        self.rows = rows
        self.readonly = readonly
        self.calls = []
        self.read = 0
        self.closed_stream = False
        self.description = [SimpleNamespace(name="value", type_display="bigint", null_ok=None)]
        self.info = SimpleNamespace(
            transaction_status=psycopg.pq.TransactionStatus.IDLE,
            parameter_status={
                "client_encoding": "UTF8",
                "standard_conforming_strings": "on",
            }.get,
        )

    def close(self):
        pass

    @contextmanager
    def transaction(self, *, force_rollback=False):
        before = self.info.transaction_status
        assert before == psycopg.pq.TransactionStatus.IDLE
        self.info.transaction_status = psycopg.pq.TransactionStatus.INTRANS
        try:
            yield
        finally:
            self.info.transaction_status = before

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql):
        self.calls.append((sql, {}))

    def fetchone(self):
        return (self.readonly if self.readonly is not None else "on",)

    def fetchall(self):
        return list(self.rows)

    def stream(self, sql, *, size):
        assert size == 1
        self.execute(sql)
        try:
            for row in self.rows:
                self.read += 1
                yield row
        finally:
            self.closed_stream = True

    def command(self, sql, *, settings=None):
        self.calls.append((sql, settings or {}))
        if "getSetting('max_execution_time')" in sql:
            return [0 if self.readonly is None else self.readonly, 0] + ["throw"] * (
                sql.count("getSetting") - 2
            )
        if "getSetting" in sql:
            return self.readonly if self.readonly is not None else 2
        return SimpleNamespace(summary={})

    def query(self, sql, **kwargs):
        self.calls.append((sql, kwargs.get("settings", {})))
        return SimpleNamespace(
            column_names=("value",),
            column_types=(SimpleNamespace(name="Int64", nullable=False),),
            result_rows=self.rows,
            named_results=lambda: [{"value": row[0]} for row in self.rows],
        )

    @contextmanager
    def query_rows_stream(self, sql, **kwargs):
        source = self.query(sql, **kwargs)
        iterator = self.stream_rows()
        try:
            yield SimpleStream(source, iterator)
        finally:
            iterator.close()
            self.closed_stream = True

    def stream_rows(self):
        for row in self.rows:
            self.read += 1
            yield row


class SimpleStream:
    def __init__(self, source, iterator):
        self.source = source
        self.iterator = iterator

    def __iter__(self):
        return self.iterator


def install(monkeypatch, engine, **kwargs):
    driver = Driver(engine, **kwargs)
    if engine == "postgres":
        monkeypatch.setattr(psycopg, "connect", lambda **kw: driver)
    else:
        monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kw: driver)
    return driver


def settings(**kwargs):
    return Settings(
        _env_file=None,
        capture_timeout_ms=75,
        environment_query_timeout_ms=80,
        **kwargs,
    )


def factory(engine, runtime=False):
    if runtime:
        return (
            make_postgres_runtime_probe if engine == "postgres" else make_clickhouse_runtime_probe
        )
    return make_postgres_result_query if engine == "postgres" else make_clickhouse_result_query


def invoke(run, runtime):
    return run(question(timeout_ms=75)) if runtime else run("SELECT 1 AS value")


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
@pytest.mark.parametrize("runtime", [False, True])
def test_readonly_is_requested_and_checked_before_text(monkeypatch, engine, runtime):
    driver = install(monkeypatch, engine)
    invoke(factory(engine, runtime)(settings()), runtime)
    sqls = [sql for sql, _ in driver.calls]
    request = "SET TRANSACTION READ ONLY" if engine == "postgres" else "SET readonly = 2"
    check = (
        "SHOW transaction_read_only" if engine == "postgres" else "SELECT getSetting('readonly')"
    )
    assert sqls.index(request) < sqls.index(check) < len(sqls) - 1


@pytest.mark.parametrize(
    "engine,bad",
    [
        ("postgres", "off"),
        ("postgres", "ON"),
        ("postgres", ""),
        ("clickhouse", 0),
        ("clickhouse", 1),
        ("clickhouse", ""),
    ],
)
@pytest.mark.parametrize("runtime", [False, True])
def test_unconfirmed_readonly_executes_no_text(monkeypatch, engine, bad, runtime):
    driver = install(monkeypatch, engine, readonly=bad)
    if engine == "clickhouse" and runtime and bad == 1:
        fact = invoke(factory(engine, runtime)(settings()), runtime)
        assert fact.checked_bounds == [] and "readonly=1" in fact.evidence
        assert len(driver.calls) == 1
        return
    error = Exception if runtime else ResultCaptureError
    with pytest.raises(error, match=r"read.only|readonly"):
        invoke(factory(engine, runtime)(settings()), runtime)
    assert all("AS value" not in sql and "sales_daily" not in sql for sql, _ in driver.calls)


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
def test_capture_streams_and_keeps_result_under_limits(monkeypatch, engine):
    driver = install(monkeypatch, engine, rows=((1,), (2,)))
    result = factory(engine)(settings(capture_max_rows=2, capture_max_bytes=2))("SELECT 1 AS value")
    assert result == QueryResult(
        columns=(
            QueryColumn(
                "value",
                "bigint" if engine == "postgres" else "Int64",
                nullable=None if engine == "postgres" else False,
            ),
        ),
        rows=((1,), (2,)),
    )
    assert driver.read == 2 and driver.closed_stream
    if engine == "postgres":
        assert ("SET LOCAL statement_timeout = 75", {}) in driver.calls
    else:
        for sql, options in driver.calls:
            if sql.startswith("SET ") or "getSetting" in sql:
                assert options == {}, sql
                continue
            assert options["max_execution_time"] == 0.075, sql
            assert options["timeout_overflow_mode"] == "throw", sql


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
@pytest.mark.parametrize(
    "limit,value,rows",
    [
        ("capture_max_rows", 2, ((1,), (2,), (3,), (4,))),
        ("capture_max_bytes", 3, (("é",), ("é",), ("more",))),
    ],
)
def test_capture_refuses_size_without_partial_result(monkeypatch, engine, limit, value, rows):
    driver = install(monkeypatch, engine, rows=rows)
    with pytest.raises(ResultCaptureError, match=f"{limit}={value}"):
        factory(engine)(settings(**{limit: value}))("SELECT 1 AS value")
    assert driver.read == (3 if limit == "capture_max_rows" else 2)
    assert driver.closed_stream


@pytest.mark.parametrize(
    "name",
    [
        "capture_timeout_ms",
        "environment_query_timeout_ms",
        "capture_max_rows",
        "capture_max_bytes",
    ],
)
@pytest.mark.parametrize("value", [0, -1])
def test_safety_limits_are_positive(name, value):
    with pytest.raises(ValidationError, match=name):
        Settings(_env_file=None, **{name: value})


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
def test_each_catalog_and_statistics_question_has_timeout(monkeypatch, engine):
    driver = install(monkeypatch, engine)
    build = make_run_query_pg if engine == "postgres" else make_run_query
    run = build(settings(), timeout_ms=80)
    for sql in ("SELECT 1 FROM pg_class", "SELECT count(*), max(value) FROM mart"):
        run(sql)
    if engine == "postgres":
        assert driver.calls == [
            ("SET statement_timeout = 80", {}),
            ("SELECT 1 FROM pg_class", {}),
            ("SET statement_timeout = 80", {}),
            ("SELECT count(*), max(value) FROM mart", {}),
        ]
    else:
        assert "getSetting('max_execution_time')" in driver.calls[0][0]
        assert driver.calls[0][1] == {}
        from auto_bi.verification.query_limits import clickhouse_query_settings

        assert all(options == clickhouse_query_settings(80) for _, options in driver.calls[1:])


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
def test_capture_timeout_names_limit(monkeypatch, engine):
    driver = install(monkeypatch, engine)

    def fail(*args, **kwargs):
        if engine == "postgres":
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
        raise clickhouse_connect.driver.exceptions.OperationalError("Code: 159. TIMEOUT_EXCEEDED")

    monkeypatch.setattr(driver, "stream" if engine == "postgres" else "query_rows_stream", fail)
    with pytest.raises(ResultCaptureError, match="capture_timeout_ms=75"):
        factory(engine)(settings())("SELECT 1 AS value")


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
def test_statistics_timeout_names_limit(monkeypatch, engine):
    driver = install(monkeypatch, engine)

    def fail(sql, **kwargs):
        if sql.startswith("SET "):
            return None
        if engine == "postgres":
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
        raise clickhouse_connect.driver.exceptions.OperationalError("Code: 159. TIMEOUT_EXCEEDED")

    monkeypatch.setattr(driver, "execute" if engine == "postgres" else "query", fail)
    build = make_run_query_pg if engine == "postgres" else make_run_query
    with pytest.raises(Exception, match="environment_query_timeout_ms=80"):
        build(settings(), timeout_ms=80)("SELECT count(*), max(value) FROM mart")


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
@pytest.mark.parametrize("limit", ["capture_max_rows", "capture_max_bytes"])
def test_capture_limit_reaches_use_case_before_any_write(monkeypatch, engine, limit):
    from pathlib import Path

    from auto_bi.decision import use_cases
    from tests.test_result_probe import _asset

    driver = install(monkeypatch, engine, rows=((1,), (2,), (3,)))
    asset = _asset(
        engine=engine,
        sql="SELECT 1 AS value",
        columns=[
            {"name": "value", "type": "integer", "nullable": False},
        ],
    )
    monkeypatch.setattr(use_cases.filesystem, "exists", lambda path: False)
    monkeypatch.setattr(use_cases, "_asset_repository", lambda path: object())
    monkeypatch.setattr(use_cases, "_select_asset", lambda *args: asset)
    monkeypatch.setattr(
        use_cases,
        "_snapshot",
        lambda path: SimpleNamespace(
            canonical_digest="sha256:" + "1" * 64,
        ),
    )
    writes = []

    def written(*args, **kwargs):
        writes.append(args)
        raise AssertionError("capture attempted to write")

    monkeypatch.setattr(Path, "mkdir", written)
    monkeypatch.setattr(Path, "open", written)
    request = use_cases.CaptureRequest(
        assets="assets",
        candidate="candidate",
        output="capture.yaml",
        engine=engine,
        environment_ref="test",
        executed_by="reader",
        captured_at="2026-10-31T00:00:00Z",
        query_factory=lambda: factory(engine)(settings(**{limit: 2})),
        asset_id=asset.metadata.id,
    )
    with pytest.raises(use_cases.VerifyError, match=f"{limit}=2"):
        use_cases.capture_result(request)
    assert not writes
    assert driver.read == 3


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
def test_cli_environment_factory_passes_timeout(monkeypatch, engine):
    from auto_bi import cli
    from auto_bi.introspect import clickhouse, greenplum
    from auto_bi.verification import environment_probe

    requested = []

    def build(configured, **kwargs):
        requested.append(kwargs)
        return lambda sql: []

    module, name = (
        (greenplum, "make_run_query_pg")
        if engine == "postgres"
        else (
            clickhouse,
            "make_run_query",
        )
    )
    monkeypatch.setattr(module, name, build)
    monkeypatch.setattr(cli, "_decision_settings", settings)
    monkeypatch.setattr(environment_probe, "collect", lambda *args, **kwargs: "facts")
    assert cli._environment_probe(engine)(None) == "facts"
    assert requested == [{"timeout_ms": 80}]


def test_safety_defaults_and_verifier_version():
    from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION

    configured = Settings(_env_file=None)
    assert configured.capture_timeout_ms == 30_000
    assert configured.environment_query_timeout_ms == 30_000
    assert configured.capture_max_rows == 10_000
    assert configured.capture_max_bytes == 16_777_216
    assert int(VERIFIER_VERSION) >= 124
    assert int(CHECK_VERSION) >= 81


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
def test_empty_capture_retains_metadata_with_stream(monkeypatch, engine):
    driver = install(monkeypatch, engine, rows=())
    result = factory(engine)(settings())("SELECT 1 AS value WHERE false")
    assert result.rows == ()
    assert result.columns[0].name == "value"
    assert driver.closed_stream


def test_postgres_empty_stream_without_metadata_uses_bounded_fallback(monkeypatch):
    driver = install(monkeypatch, "postgres", rows=())
    description = driver.description
    driver.description = None
    execute = driver.execute

    def metadata(sql):
        execute(sql)
        if "AS capture_metadata LIMIT 0" in sql:
            driver.description = description

    monkeypatch.setattr(driver, "execute", metadata)
    result = make_postgres_result_query(settings())("SELECT 1 AS value WHERE false")
    assert result == QueryResult(columns=(QueryColumn("value", "bigint"),), rows=())
    assert driver.closed_stream
    assert [sql for sql, _ in driver.calls] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = 75",
        "SHOW transaction_read_only",
        "SAVEPOINT capture_query",
        "SELECT 1 AS value WHERE false",
        "ROLLBACK TO SAVEPOINT capture_query",
        "SELECT * FROM (\nSELECT 1 AS value WHERE false\n) AS capture_metadata LIMIT 0",
    ]


def test_clickhouse_statistics_late_timeout_names_limit(monkeypatch):
    from clickhouse_connect.driver.exceptions import StreamFailureError

    driver = install(monkeypatch, "clickhouse")

    def rows():
        raise StreamFailureError("Code: 159. TIMEOUT_EXCEEDED")

    monkeypatch.setattr(driver, "query", lambda *a, **kw: SimpleNamespace(named_results=rows))
    with pytest.raises(Exception, match="environment_query_timeout_ms=80"):
        make_run_query(settings(), timeout_ms=80)("SELECT count(*), max(value) FROM mart")
