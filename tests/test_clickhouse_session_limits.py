"""Session-owned limits, reviewed SETTINGS and empty-result metadata."""

from contextlib import contextmanager
from types import SimpleNamespace

import clickhouse_connect
import pytest
import sqlglot
from clickhouse_connect.driver.exceptions import (
    ClickHouseError,
    OperationalError,
    ProgrammingError,
)

from auto_bi.introspect.clickhouse import make_run_query
from auto_bi.verification import environment_probe
from auto_bi.verification.query_limits import (
    CLICKHOUSE_OVERFLOW_SETTINGS,
    clickhouse_query_settings,
)
from auto_bi.verification.result_probe import ResultCaptureError
from tests.test_probe_safety import Driver, SimpleStream, factory, install, settings
from tests.test_runtime_probe import question

SESSION_SQL = (
    "SELECT getSetting('readonly'), getSetting('max_execution_time'), "
    "getSetting('timeout_overflow_mode')"
    + "".join(
        f", getSetting('{name}')"
        for name in CLICKHOUSE_OVERFLOW_SETTINGS
        if name != "timeout_overflow_mode"
    )
)
TIME_SETTINGS = (
    "max_execution_time",
    "timeout_overflow_mode",
    "max_execution_time_leaf",
    "timeout_overflow_mode_leaf",
)
BYTE_SETTINGS = (
    "max_bytes_to_read",
    "read_overflow_mode",
    "max_bytes_to_read_leaf",
    "read_overflow_mode_leaf",
)


class SessionDriver(Driver):
    def __init__(self, mode=1, seconds=0, overflow="throw"):
        super().__init__("clickhouse")
        self.mode, self.seconds, self.overflow = mode, seconds, overflow

    def command(self, sql, **kwargs):
        self.calls.append((sql, kwargs))
        if sql == SESSION_SQL:
            return [str(self.mode), str(self.seconds), self.overflow] + ["throw"] * (
                sql.count("getSetting") - 3
            )
        if sql == "SET readonly = 2":
            if self.mode == 1:
                raise OperationalError("Code: 164. Cannot modify 'readonly' setting")
            self.mode = 2
            return None
        if sql == "SELECT getSetting('readonly')":
            return self.mode
        return SimpleNamespace(summary={"elapsed_ns": "1"})

    def query(self, sql, **kwargs):
        if "system.settings" in sql:
            self.calls.append((sql, kwargs))
            return SimpleNamespace(result_rows=[("limit", "0", 0), ("offset", "0", 0)])
        return super().query(sql, **kwargs)


def session(monkeypatch, **kwargs):
    driver = SessionDriver(**kwargs)
    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kw: driver)
    return driver


@pytest.mark.parametrize(
    "seconds,overflow,accepted",
    [
        (0, "throw", False),
        (0.1, "throw", False),
        (0.075, "throw", True),
        (0.04, "throw", True),
        (0.04, "break", False),
    ],
)
@pytest.mark.parametrize("executor", ["capture", "catalog", "runtime"])
def test_readonly_one_session_contract(monkeypatch, seconds, overflow, accepted, executor):
    driver = session(monkeypatch, seconds=seconds, overflow=overflow)
    if executor == "runtime":
        fact = factory("clickhouse", True)(settings())(question(timeout_ms=75))
        assert fact.checked_bounds == [] and fact.exceeded_bound is None
        assert "readonly=1" in fact.evidence
        assert fact.evidence.endswith("no declared runtime bound was measured")
    else:
        run = (
            make_run_query(settings(), timeout_ms=75)
            if executor == "catalog"
            else factory("clickhouse")(settings())
        )
        if accepted:
            assert run("SELECT 1 AS value")
            assert driver.calls[-1][1] == {}
        else:
            error = ClickHouseError if executor == "catalog" else ResultCaptureError
            name = "environment_query_timeout_ms" if executor == "catalog" else "capture_timeout_ms"
            with pytest.raises(error, match=f"{name}=75") as caught:
                environment_probe._ask(run, "SELECT 1 AS value")
            message = str(caught.value)
            assert "readonly=1" in message
            assert f"max_execution_time={seconds:g}" in message
            assert f"timeout_overflow_mode={overflow}" in message
            assert not environment_probe._REFUSED_TEXT.search(message)
            assert getattr(caught.value, "sqlstate", None) not in environment_probe._PG_REFUSALS
    assert driver.calls[0] == (SESSION_SQL, {})
    if executor == "runtime":
        assert driver.calls == [(SESSION_SQL, {})]
    elif not accepted:
        assert len(driver.calls) == 2 and "system.settings" in driver.calls[1][0]
    assert all(not sql.startswith("SET ") and not options for sql, options in driver.calls)


@pytest.mark.parametrize("executor", ["capture", "catalog", "runtime"])
@pytest.mark.parametrize("mode", [0, 2])
def test_adjustable_session(monkeypatch, executor, mode):
    driver = session(monkeypatch, mode=mode)
    if executor == "catalog":
        run = make_run_query(settings(), timeout_ms=75)
        run("SELECT 1 AS value")
        run("SELECT 2 AS value")
        assert sum(sql == SESSION_SQL for sql, _ in driver.calls) == 1
        assert driver.mode == mode
    elif executor == "capture":
        factory("clickhouse")(settings())("SELECT 1 AS value")
    else:
        factory("clickhouse", True)(settings())(question(timeout_ms=75))
    assert driver.calls[0] == (SESSION_SQL, {})
    assert sum(sql == "SET readonly = 2" for sql, _ in driver.calls) == int(
        mode == 0 and executor != "catalog"
    )
    assert sum(sql == "SELECT getSetting('readonly')" for sql, _ in driver.calls) == int(
        executor != "catalog"
    )
    options = driver.calls[-1][1]
    if executor == "runtime":
        options = options["settings"]
    assert options == clickhouse_query_settings(75)


@pytest.mark.parametrize("executor", ["capture", "catalog", "runtime"])
@pytest.mark.parametrize("mode", [3, "", None])
def test_other_session_mode_refuses(monkeypatch, executor, mode):
    driver = session(monkeypatch, mode=mode)
    error = ResultCaptureError if executor == "capture" else ClickHouseError
    with pytest.raises(error, match="readonly"):
        if executor == "catalog":
            make_run_query(settings(), timeout_ms=75)("SELECT 1")
        elif executor == "runtime":
            factory("clickhouse", True)(settings())(question(timeout_ms=75))
        else:
            factory("clickhouse")(settings())("SELECT 1")
    assert driver.calls == [(SESSION_SQL, {})]


def test_capture_session_timeout_names_both_limits(monkeypatch):
    driver = session(monkeypatch, seconds=0.04)

    def fail(*args, **kwargs):
        raise OperationalError("Code: 159. TIMEOUT_EXCEEDED")

    monkeypatch.setattr(driver, "query_rows_stream", fail)
    with pytest.raises(ResultCaptureError, match="capture_timeout_ms=75") as caught:
        factory("clickhouse")(settings())("SELECT 1")
    assert "session max_execution_time=0.04 s" in str(caught.value)


@pytest.mark.parametrize("stage", ["session", "set", "confirm"])
def test_runtime_setting_refusal_at_any_stage(monkeypatch, stage):
    driver = session(monkeypatch, mode=0)
    command = driver.command

    def fail(sql, **kwargs):
        where = (
            "session"
            if sql == SESSION_SQL
            else (
                "set"
                if sql.startswith("SET ")
                else "confirm" if sql == "SELECT getSetting('readonly')" else "text"
            )
        )
        if where == stage:
            raise ProgrammingError("Setting max_execution_time is unknown or readonly")
        return command(sql, **kwargs)

    monkeypatch.setattr(driver, "command", fail)
    with pytest.raises(ProgrammingError, match="Setting max_execution_time is unknown or readonly"):
        factory("clickhouse", True)(settings())(question(timeout_ms=75))


@pytest.mark.parametrize(
    "executor,name",
    [
        (executor, name)
        for executor in ("capture", "runtime")
        for name in TIME_SETTINGS + (BYTE_SETTINGS if executor == "runtime" else ())
    ],
)
@pytest.mark.parametrize("form", ["outer", "inner", "upper", "union"])
def test_reviewed_settings_refuse_before_session(monkeypatch, name, form, executor):
    driver = session(monkeypatch, mode=2)
    spelled = name.upper() if form == "upper" else name
    value = "'break'" if "mode" in name else "0"
    sql = f"SELECT 1 AS value SETTINGS {spelled}={value}"
    if form == "inner":
        sql = f"SELECT * FROM ({sql})"
    elif form == "union":
        sql = f"SELECT 2 AS value UNION ALL {sql}"
    if executor == "capture":
        with pytest.raises(ResultCaptureError, match=name):
            factory("clickhouse")(settings())(sql)
    else:
        q = question(timeout_ms=75, max_scanned_bytes=100).model_copy(update={"sql": sql})
        fact = factory("clickhouse", True)(settings())(q)
        assert fact.checked_bounds == [] and fact.exceeded_bound is None
        assert name in fact.evidence
        assert fact.evidence.endswith("no declared runtime bound was measured")
    assert driver.calls == []


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
def test_empty_comment_metadata_keeps_columns(monkeypatch, engine):
    driver = (
        install(monkeypatch, engine, rows=())
        if engine == "postgres"
        else session(monkeypatch, seconds=0.04)
    )
    sql = "SELECT 1 AS value WHERE false -- c"
    if engine == "postgres":
        description = driver.description
        driver.description = None
        execute = driver.execute

        def metadata(text):
            execute(text)
            if text.startswith("SELECT * FROM ("):
                sqlglot.parse_one(text, read="postgres")
                driver.description = description

        monkeypatch.setattr(driver, "execute", metadata)
    else:

        @contextmanager
        def stream(text, **kwargs):
            driver.calls.append((text, kwargs))
            assert "settings" not in kwargs
            if text.startswith("SELECT * FROM ("):
                sqlglot.parse_one(text, read="clickhouse")
                source = SimpleNamespace(
                    column_names=("value",),
                    column_types=(SimpleNamespace(name="Int64", nullable=False),),
                )
            else:
                source = SimpleNamespace(column_names=(), column_types=())
            yield SimpleStream(source, iter(()))

        monkeypatch.setattr(driver, "query_rows_stream", stream)
    result = factory(engine)(settings())(sql)
    assert result.rows == () and result.columns[0].name == "value"
    assert any(
        text == f"SELECT * FROM (\n{sql}\n) AS capture_metadata LIMIT 0" for text, _ in driver.calls
    )
