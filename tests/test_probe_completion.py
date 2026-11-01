"""Complete answers, isolated settings and preparation errors with substituted drivers."""

import re
from contextlib import contextmanager
from types import SimpleNamespace

import clickhouse_connect
import psycopg
import pytest
from clickhouse_connect.driver.exceptions import (
    DatabaseError,
    OperationalError,
    StreamFailureError,
)

from auto_bi.introspect.clickhouse import make_run_query
from auto_bi.verification import environment_probe
from auto_bi.verification.result_probe import ResultCaptureError
from tests.test_probe_safety import Driver, SimpleStream, factory, settings
from tests.test_runtime_probe import question

OVERFLOW_NAMES = (
    "distinct_overflow_mode",
    "group_by_overflow_mode",
    "join_overflow_mode",
    "read_overflow_mode",
    "read_overflow_mode_leaf",
    "result_overflow_mode",
    "set_overflow_mode",
    "sort_overflow_mode",
    "timeout_overflow_mode",
    "timeout_overflow_mode_leaf",
    "transfer_overflow_mode",
)


class CompleteDriver(Driver):
    def __init__(self, mode=0, empty=False):
        super().__init__("clickhouse", rows=() if empty else ((1,),))
        self.values = dict.fromkeys(OVERFLOW_NAMES, "throw")
        self.values.update(readonly=mode, max_execution_time=0.04)
        self.limit_rows = [("limit", "0", 0), ("offset", "0", 0)]
        self.fail_stage = None
        self.error = DatabaseError("Code: 159. preparation failed", code=159)
        self.stages = []
        self.texts = []
        self.empty = empty
        self.metadata_confirm = 2

    def stage(self, name):
        self.stages.append(name)
        if name == self.fail_stage:
            raise self.error

    def command(self, sql, **kwargs):
        self.calls.append((sql, kwargs))
        suffix = "_metadata" if self.texts else ""
        if "max_execution_time" in sql and "getSetting" in sql:
            self.stage("session" + suffix)
            if suffix and self.fail_stage == "set_metadata":
                self.values["readonly"] = 0
            return [self.values[name] for name in re.findall(r"getSetting\('([^']+)'\)", sql)]
        if sql == "SET readonly = 2":
            self.stage("set" + suffix)
            self.values["readonly"] = 2
            return None
        if sql == "SELECT getSetting('readonly')":
            self.stage("confirm" + suffix)
            return self.metadata_confirm if self.texts else 2
        self.texts.append((sql, kwargs))
        self.stage("text")
        return SimpleNamespace(summary={"elapsed_ns": "1", "read_bytes": "1"})

    def query(self, sql, **kwargs):
        self.calls.append((sql, kwargs))
        if "system.settings" in sql:
            self.stage("limits_metadata" if self.texts else "limits")
            assert kwargs == {}
            return SimpleNamespace(result_rows=self.limit_rows)
        self.texts.append((sql, kwargs))
        metadata = sql.startswith("SELECT * FROM (")
        self.stage("metadata" if metadata else "text")
        return SimpleNamespace(
            named_results=lambda: [{"value": 1}],
            column_names=("value",) if metadata or not self.empty else (),
            column_types=(
                (SimpleNamespace(name="Int64", nullable=False),)
                if metadata or not self.empty
                else ()
            ),
            result_rows=self.rows,
        )

    @contextmanager
    def query_rows_stream(self, sql, **kwargs):
        self.calls.append((sql, kwargs))
        self.texts.append((sql, kwargs))
        metadata = "AS capture_metadata" in sql
        self.stage("metadata" if metadata else "text")
        source = SimpleNamespace(
            column_names=("value",) if metadata or not self.empty else (),
            column_types=(
                (SimpleNamespace(name="Int64", nullable=False),)
                if metadata or not self.empty
                else ()
            ),
        )
        yield SimpleStream(source, iter(self.rows))


def ch_driver(monkeypatch, **kwargs):
    driver = CompleteDriver(**kwargs)
    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kw: driver)
    return driver


def execute(executor, sql="SELECT 1 AS value"):
    if executor == "catalog":
        return make_run_query(settings(), timeout_ms=75)(sql)
    run = factory("clickhouse", executor == "runtime")(settings())
    if executor == "runtime":
        return run(question(timeout_ms=75, max_scanned_bytes=100).model_copy(update={"sql": sql}))
    return run(sql)


@pytest.mark.parametrize("mode", [0, 2])
@pytest.mark.parametrize("executor", ["capture", "catalog", "runtime"])
def test_complete_query_options(monkeypatch, mode, executor):
    driver = ch_driver(monkeypatch, mode=mode, empty=True)
    execute(executor)
    assert len(driver.texts) == (2 if executor == "capture" else 1)
    for _, options in driver.texts:
        actual = options["settings"]
        assert actual == {
            **dict.fromkeys(OVERFLOW_NAMES, "throw"),
            "max_execution_time": 0.075,
            "limit": 0,
            "offset": 0,
            **({"max_bytes_to_read": 100} if executor == "runtime" else {}),
        }
    assert not any("system.settings" in sql for sql, _ in driver.calls)


@pytest.mark.parametrize("executor", ["capture", "catalog"])
@pytest.mark.parametrize(
    "name,value",
    [(name, "break") for name in OVERFLOW_NAMES] + [("group_by_overflow_mode", "any")],
)
def test_readonly_one_overflow_refusal(monkeypatch, executor, name, value):
    driver = ch_driver(monkeypatch, mode=1)
    driver.values[name] = value
    error = ResultCaptureError if executor == "capture" else OperationalError
    with pytest.raises(error, match=f"{name}={value}") as caught:
        execute(executor)
    assert not environment_probe._REFUSED_TEXT.search(str(caught.value))
    assert getattr(caught.value, "sqlstate", None) not in environment_probe._PG_REFUSALS
    assert not driver.texts


@pytest.mark.parametrize("executor", ["capture", "catalog"])
@pytest.mark.parametrize("name", ["limit", "offset"])
@pytest.mark.parametrize("bad", ["changed", "value", "missing", "shape", "duplicate"])
def test_readonly_one_limit_refusal(monkeypatch, executor, name, bad):
    driver = ch_driver(monkeypatch, mode=1)
    driver.limit_rows = [row for row in driver.limit_rows if row[0] != name]
    if bad != "missing":
        driver.limit_rows.append(
            (name, "5" if bad == "value" else "0", 1 if bad == "changed" else 0)
        )
    if bad == "shape":
        driver.limit_rows[-1] = (name, "0")
    if bad == "duplicate":
        driver.limit_rows.append((name, "0", 0))
    error = ResultCaptureError if executor == "capture" else OperationalError
    with pytest.raises(error, match=name) as caught:
        execute(executor)
    message = str(caught.value)
    if bad == "changed":
        assert "changed=1" in message and "user's own settings" in message
        assert "readonly=1" in message
    assert not environment_probe._REFUSED_TEXT.search(message)
    assert not driver.texts


@pytest.mark.parametrize("executor", ["capture", "catalog"])
def test_readonly_one_complete_session(monkeypatch, executor):
    driver = ch_driver(monkeypatch, mode=1, empty=True)
    execute(executor)
    assert sum("system.settings" in sql for sql, _ in driver.calls) == (
        2 if executor == "capture" else 1
    )
    assert all("SET " not in sql and "settings" not in options for sql, options in driver.calls)


@pytest.mark.parametrize("executor", ["capture", "runtime"])
@pytest.mark.parametrize("name", (*OVERFLOW_NAMES, "profile"))
@pytest.mark.parametrize("form", ["outer", "inner", "upper", "union"])
def test_complete_settings_refusal(monkeypatch, executor, name, form):
    driver = ch_driver(monkeypatch)
    sql = f"SELECT 1 AS value SETTINGS {name.upper() if form == 'upper' else name}='break'"
    if form == "inner":
        sql = f"SELECT * FROM ({sql})"
    if form == "union":
        sql = "SELECT 2 AS value UNION ALL " + sql
    if executor == "capture":
        with pytest.raises(ResultCaptureError, match=name):
            execute(executor, sql)
    else:
        fact = execute(executor, sql)
        assert name in fact.evidence
        assert fact.checked_bounds == [] and fact.exceeded_bound is None
        assert fact.evidence.endswith("no declared runtime bound was measured")
    assert driver.calls == []


@pytest.mark.parametrize("executor", ["capture", "runtime"])
@pytest.mark.parametrize("name", ["limit", "offset"])
def test_text_limit_and_offset_accepted(monkeypatch, executor, name):
    driver = ch_driver(monkeypatch)
    execute(executor, f"SELECT 1 AS value SETTINGS {name}=3")
    assert len(driver.texts) == 1
    assert driver.texts[0][1]["settings"][name] == 0


@pytest.mark.parametrize(
    "executor,stage,mode",
    [
        (executor, stage, 1 if stage.startswith("limits") else 0)
        for executor, stages in (
            (
                "capture",
                (
                    "session",
                    "set",
                    "confirm",
                    "limits",
                    "session_metadata",
                    "set_metadata",
                    "confirm_metadata",
                    "limits_metadata",
                ),
            ),
            ("catalog", ("session", "limits")),
            ("runtime", ("session", "set", "confirm")),
        )
        for stage in stages
    ],
)
def test_preparation_error_is_not_query_outcome(monkeypatch, executor, stage, mode):
    driver = ch_driver(monkeypatch, mode=mode, empty=True)
    driver.fail_stage = stage
    error = ResultCaptureError if executor == "capture" else DatabaseError
    with pytest.raises(error) as caught:
        execute(executor)
    if executor == "capture":
        assert "session preparation" in str(caught.value)
        assert "DatabaseError: Code: 159. preparation failed" in str(caught.value)
    else:
        assert caught.value is driver.error
    assert "exceeded" not in str(caught.value)
    assert len(driver.texts) == int(stage.endswith("_metadata"))


@pytest.mark.parametrize("failure", ["timeout", "readonly"])
def test_metadata_question_own_boundary(monkeypatch, failure):
    driver = ch_driver(monkeypatch, mode=2, empty=True)
    if failure == "timeout":
        driver.fail_stage = "metadata"
    else:
        driver.metadata_confirm = 0
    with pytest.raises(ResultCaptureError) as caught:
        execute("capture")
    if failure == "timeout":
        assert str(caught.value) == "capture_timeout_ms=75 exceeded (ClickHouse)"
        assert driver.texts[-1][1]["settings"]["result_overflow_mode"] == "throw"
    else:
        assert "session preparation" in str(caught.value) and "readonly=2" in str(caught.value)
        assert len(driver.texts) == 1


@pytest.mark.parametrize("executor", ["capture", "catalog", "runtime"])
@pytest.mark.parametrize(
    "error,timed_out",
    [
        (DatabaseError("Code: 159. table TIMEOUT_EXCEEDED", code=60), False),
        (DatabaseError("server response: Code: 159. elapsed"), True),
        (DatabaseError("response\nCode: 159. elapsed"), True),
        (StreamFailureError("Code: 159. elapsed"), True),
        (StreamFailureError("Code: 60. table TIMEOUT_EXCEEDED"), False),
    ],
)
def test_clickhouse_codes(monkeypatch, executor, error, timed_out):
    driver = ch_driver(monkeypatch)
    driver.error = error
    driver.fail_stage = "text"
    if executor == "runtime" and timed_out:
        fact = execute(executor)
        assert fact.exceeded_bound.value == "timeout_ms"
    else:
        expected = (
            ResultCaptureError
            if executor == "capture"
            else OperationalError if timed_out else type(error)
        )
        with pytest.raises(expected) as caught:
            execute(executor)
        if timed_out:
            assert "timeout_ms=75" in str(caught.value) and "exceeded" in str(caught.value)
        elif executor == "capture":
            assert "query failed" in str(caught.value) and "TIMEOUT_EXCEEDED" in str(caught.value)
        else:
            assert caught.value is error


def test_catalog_session_timeout_names_both_limits(monkeypatch):
    driver = ch_driver(monkeypatch, mode=1)
    driver.fail_stage = "text"
    with pytest.raises(OperationalError) as caught:
        execute("catalog")
    assert str(caught.value) == (
        "ClickHouse session max_execution_time=0.04 s exceeded; "
        "configured environment_query_timeout_ms=75"
    )


class PostgresDriver(Driver):
    def __init__(self, empty):
        super().__init__("postgres", rows=() if empty else ((1,),))
        self.events = []
        self.depth = 0
        self.info = SimpleNamespace(
            parameter_status=self.status, transaction_status=psycopg.pq.TransactionStatus.IDLE
        )
        self.saved_description = self.description
        if empty:
            self.description = None
        self.fail_stage = None
        self.suppress_rollback = None
        self.error = psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

    def status(self, name):
        self.events.append("check")
        return {"client_encoding": "UTF8", "standard_conforming_strings": "on"}[name]

    @contextmanager
    def transaction(self, *, force_rollback=False):
        assert self.depth == 0
        start = "BEGIN"
        end = "ROLLBACK"
        self.events.append(start)
        if self.fail_stage == start:
            raise self.error
        self.depth += 1
        self.info.transaction_status = psycopg.pq.TransactionStatus.INTRANS
        try:
            yield
        finally:
            self.depth -= 1
            self.events.append(end if force_rollback else "COMMIT")
            self.info.transaction_status = (
                psycopg.pq.TransactionStatus.INERROR
                if self.suppress_rollback == end
                else psycopg.pq.TransactionStatus.IDLE
            )
            if self.fail_stage == end:
                raise self.error

    def execute(self, sql):
        self.events.append(sql)
        if self.fail_stage == sql:
            raise self.error
        if self.suppress_rollback == sql:
            self.info.transaction_status = psycopg.pq.TransactionStatus.INERROR
        super().execute(sql)
        if "AS capture_metadata" in sql:
            self.description = self.saved_description


@pytest.mark.parametrize("empty", [False, True])
def test_postgres_capture_savepoint_order(monkeypatch, empty):
    driver = PostgresDriver(empty)
    monkeypatch.setattr(psycopg, "connect", lambda **kw: driver)
    result = factory("postgres")(settings())("SELECT 1 AS value")
    events = driver.events
    assert events[-1] == "ROLLBACK" and "COMMIT" not in events
    start = events.index("SAVEPOINT capture_query")
    text = events.index("SELECT 1 AS value")
    end = events.index("ROLLBACK TO SAVEPOINT capture_query")
    assert start < text < end
    assert "check" in events[text + 1 : end]
    if empty:
        metadata = next(i for i, sql in enumerate(events) if "AS capture_metadata" in sql)
        assert end < metadata and "check" in events[end + 1 : metadata]
    assert result.rows == driver.rows and result.columns[0].name == "value"


def test_postgres_runtime_rollback(monkeypatch):
    driver = PostgresDriver(False)
    monkeypatch.setattr(psycopg, "connect", lambda **kw: driver)
    factory("postgres", True)(settings())(question(timeout_ms=75))
    assert driver.events[-1] == "ROLLBACK" and "COMMIT" not in driver.events


@pytest.mark.parametrize(
    "runtime,stage",
    [(False, "ROLLBACK TO SAVEPOINT capture_query"), (False, "ROLLBACK"), (True, "ROLLBACK")],
)
def test_postgres_rollback_status_refuses_result(monkeypatch, runtime, stage):
    # Pin the reported status after SQL rollback and after suppressed outer rollback errors.
    driver = PostgresDriver(True)
    driver.suppress_rollback = stage
    closed = []
    monkeypatch.setattr(driver, "close", lambda: closed.append(True))
    monkeypatch.setattr(psycopg, "connect", lambda **kw: driver)
    with pytest.raises(
        psycopg.OperationalError if runtime else ResultCaptureError, match="rollback"
    ) as caught:
        run = factory("postgres", runtime)(settings())
        run(question(timeout_ms=75) if runtime else "SELECT 1 AS value")
    assert closed
    if not runtime:
        assert "session preparation" in str(caught.value)
    if stage == "ROLLBACK TO SAVEPOINT capture_query":
        assert not any("AS capture_metadata" in event for event in driver.events)


@pytest.mark.parametrize(
    "runtime,stage",
    [
        (False, stage)
        for stage in (
            "SET TRANSACTION READ ONLY",
            "SET LOCAL statement_timeout = 75",
            "SHOW transaction_read_only",
            "SAVEPOINT capture_query",
            "ROLLBACK TO SAVEPOINT capture_query",
        )
    ]
    + [(True, "SET TRANSACTION READ ONLY")],
)
def test_postgres_preparation_error(monkeypatch, runtime, stage):
    driver = PostgresDriver(True)
    driver.fail_stage = stage
    monkeypatch.setattr(psycopg, "connect", lambda **kw: driver)
    with pytest.raises(psycopg.errors.QueryCanceled if runtime else ResultCaptureError) as caught:
        run = factory("postgres", runtime)(settings())
        run(question(timeout_ms=75) if runtime else "SELECT 1 AS value")
    if runtime:
        assert caught.value is driver.error
    else:
        assert "session preparation" in str(caught.value)
        assert "QueryCanceled: canceling statement due to statement timeout" in str(caught.value)
        assert "exceeded" not in str(caught.value)


@pytest.mark.parametrize("engine", ["postgres", "clickhouse"])
@pytest.mark.parametrize("cap,accepted,read", [(6, True, 3), (5, False, 3), (2, False, 1)])
def test_capture_sums_columns_with_nulls(monkeypatch, engine, cap, accepted, read):
    from tests.test_probe_safety import install

    driver = install(
        monkeypatch, engine, rows=(("é", None, "x"), (None, "y", None), ("z", None, "w"))
    )
    run = factory(engine)(settings(capture_max_bytes=cap))
    if accepted:
        assert run("SELECT 1 AS value").rows == driver.rows
    else:
        with pytest.raises(ResultCaptureError, match=f"capture_max_bytes={cap}"):
            run("SELECT 1 AS value")
    assert driver.read == read and driver.closed_stream
