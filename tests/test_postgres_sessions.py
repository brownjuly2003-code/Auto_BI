"""Gate PostgreSQL factories request and verify their string settings before use."""

from __future__ import annotations

import logging
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from auto_bi.config import Settings
from auto_bi.introspect.greenplum import make_run_query_pg
from auto_bi.verification.result_probe import ResultCaptureError, make_postgres_result_query
from auto_bi.verification.runtime_probe import make_postgres_runtime_probe
from tests.test_runtime_probe import question

FACTORIES = [make_run_query_pg, make_postgres_runtime_probe, make_postgres_result_query]
SESSION = {"client_encoding": "UTF8", "standard_conforming_strings": "on"}


class Connection:
    def __init__(self, reported: dict[str, str | None]) -> None:
        self.reported = dict(reported)
        self.checked: list[str] = []
        self.statements: list[str] = []
        self.closed = False
        self.used_before_checks = False
        self.transactions = 0
        self.autocommit = True
        self.in_transaction = False
        self.local_settings: dict[str, str | None] = {}
        self.statement_settings: dict[str, list[tuple[str, str, bool]]] = {}
        self.statement_errors: dict[str, psycopg.Error] = {}
        self.end_errors: dict[str, psycopg.Error] = {}
        self.close_on_end_error = False
        self.events: list[str] = []
        self.info = SimpleNamespace(parameter_status=self.parameter_status)

    def parameter_status(self, name: str) -> str | None:
        self.checked.append(name)
        self.events.append("check " + name)
        if self.closed:
            raise psycopg.OperationalError("the connection is closed")
        return self.reported[name]

    def close(self) -> None:
        self.closed = True

    @contextmanager
    def transaction(self):
        assert not self.closed and not self.in_transaction
        self.transactions += 1
        self.in_transaction = True
        self.events.append("begin")
        try:
            yield
        except Exception:
            if self.closed:
                self.events.append("closed")
            else:
                try:
                    self.finish("rollback")
                except Exception as error:
                    logging.getLogger("psycopg").warning("error ignored in rollback: %s", error)
            raise
        else:
            if self.closed:
                self.events.append("closed")
            else:
                self.finish("commit")
        finally:
            self.end_transaction()
            self.in_transaction = False

    def finish(self, command: str) -> None:
        self.events.append(command)
        if command in self.end_errors:
            self.events.append(command + " failed")
            if self.close_on_end_error:
                self.close()
            raise self.end_errors[command]

    def end_transaction(self):
        self.reported.update(self.local_settings)
        self.local_settings.clear()

    def cursor(self):
        assert not self.closed
        self.used_before_checks |= self.checked[-2:] != list(SESSION)
        return nullcontext(
            SimpleNamespace(
                execute=self.execute,
                stream=self.stream,
                description=[SimpleNamespace(name="value", type_display="text", null_ok=None)],
                fetchall=self.fetchall,
            )
        )

    def execute(self, sql: str):
        self.statements.append(sql)
        self.events.append("execute")
        if sql in self.statement_errors:
            raise self.statement_errors[sql]
        for setting, value, local in self.statement_settings.get(sql, []):
            if local:
                self.local_settings.setdefault(setting, self.reported[setting])
            self.reported[setting] = value
        if self.autocommit and not self.in_transaction:
            self.end_transaction()

    def fetchall(self):
        self.events.append("fetchall")
        return [("value",)]

    def stream(self, sql: str, *, size: int):
        assert size == 1
        self.execute(sql)
        return iter([(1,)])


def invoke(factory, run):
    if factory is make_postgres_runtime_probe:
        return run(question(timeout_ms=50).model_copy(update={"dialect": "postgres"}))
    return run("SELECT 'value'")


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("password", ["", "test-password"])
def test_factory_requests_and_checks_session(monkeypatch, factory, password):
    connection = Connection(SESSION)
    calls: list[dict[str, Any]] = []

    def connect(**kwargs):
        calls.append(kwargs)
        connection.autocommit = kwargs["autocommit"]
        return connection

    monkeypatch.setattr(psycopg, "connect", connect)
    settings = Settings(
        _env_file=None,
        gp_host="db.example",
        gp_port=5544,
        gp_database="warehouse",
        gp_user="reader",
        gp_password=password,
    )
    run = factory(settings)
    if factory is make_postgres_runtime_probe:
        assert calls == []
        run(question(max_scanned_bytes=100))
        assert calls == []
    invoke(factory, run)
    invoke(factory, run)
    expected = dict(
        host="db.example",
        port=5544,
        dbname="warehouse",
        user="reader",
        password=password or None,
        autocommit=factory is not make_postgres_runtime_probe,
        client_encoding="UTF8",
        options="-c standard_conforming_strings=on",
    )
    if factory is make_run_query_pg:
        expected["row_factory"] = dict_row
    assert calls == [expected]
    assert set(connection.checked) == set(SESSION)
    assert not connection.used_before_checks
    assert not connection.closed
    statements = (
        ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = 50", question().sql] * 2
        if factory is make_postgres_runtime_probe
        else ["SELECT 'value'"] * 2
    )
    assert connection.statements == statements


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize(
    "setting,value",
    [
        ("client_encoding", "EUC_JP"),
        ("client_encoding", "SJIS"),
        ("client_encoding", "LATIN1"),
        ("client_encoding", "SQL_ASCII"),
        ("client_encoding", None),
        ("standard_conforming_strings", "off"),
        ("standard_conforming_strings", "ON"),
        ("standard_conforming_strings", None),
    ],
)
def test_factory_refuses_unconfirmed_session(monkeypatch, factory, setting, value):
    connection = Connection({**SESSION, setting: value})
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: connection)
    error_type = ResultCaptureError if factory is make_postgres_result_query else psycopg.Error
    with pytest.raises(error_type) as caught:
        invoke(factory, factory(Settings(_env_file=None)))
    message = (
        f"PostgreSQL session requires {setting}={SESSION[setting]!r}; server reported {value!r}"
    )
    if factory is make_postgres_result_query:
        message = "PostgreSQL connection failed: OperationalError: " + message
    assert str(caught.value) == message
    assert connection.closed
    assert connection.statements == []


@pytest.mark.parametrize(
    "setting,value", [("client_encoding", "LATIN1"), ("standard_conforming_strings", "off")]
)
def test_capture_refuses_transaction_local_setting(monkeypatch, setting, value):
    connection = Connection(SESSION)
    sql = f"SELECT set_config('{setting}', '{value}', true)"
    connection.statement_settings[sql] = [(setting, value, True)]
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: connection)
    run = make_postgres_result_query(Settings(_env_file=None))
    with pytest.raises(ResultCaptureError) as caught:
        run(sql)
    assert_session_refusal(caught, make_postgres_result_query, setting, value)
    assert connection.statements == [sql]
    assert connection.transactions == 1
    assert connection.closed
    assert connection.reported == SESSION
    assert not connection.in_transaction


@pytest.mark.parametrize("restore_inside_statement", [False, True])
def test_capture_checks_inside_transaction(monkeypatch, restore_inside_statement):
    connection = Connection(SESSION)
    sql = "SELECT 'value'"
    if restore_inside_statement:
        connection.statement_settings[sql] = [
            ("client_encoding", "LATIN1", True),
            ("standard_conforming_strings", "off", True),
            ("client_encoding", "UTF8", True),
            ("standard_conforming_strings", "on", True),
        ]
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: connection)
    result = make_postgres_result_query(Settings(_env_file=None))(sql)
    checks = ["check " + setting for setting in SESSION]
    assert connection.events == (
        checks + checks + ["begin"] + checks + ["execute", "fetchall"] + checks + ["commit"]
    )
    assert result.rows == (("value",),)
    assert result.columns[0].name == "value"
    assert connection.reported == SESSION
    assert not connection.in_transaction
    assert not connection.closed


@pytest.mark.parametrize("close_connection", [False, True])
def test_capture_wraps_failed_commit(monkeypatch, close_connection):
    connection = Connection(SESSION)
    error = (
        psycopg.OperationalError("connection lost at commit")
        if close_connection
        else psycopg.errors.SerializationFailure("commit rejected")
    )
    connection.end_errors["commit"] = error
    connection.close_on_end_error = close_connection
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: connection)
    run = make_postgres_result_query(Settings(_env_file=None))
    with pytest.raises(ResultCaptureError) as caught:
        run("SELECT 'value'")
    assert str(caught.value) == f"PostgreSQL query failed: {type(error).__name__}: {error}"
    assert caught.value.__context__ is error
    checks = ["check " + setting for setting in SESSION]
    assert connection.events == (
        checks
        + checks
        + ["begin"]
        + checks
        + ["execute", "fetchall"]
        + checks
        + ["commit", "commit failed"]
    )
    assert connection.statements == ["SELECT 'value'"]
    assert connection.transactions == 1
    assert connection.closed is close_connection
    assert not connection.in_transaction


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_capture_keeps_statement_error_after_rollback(monkeypatch, caplog, rollback_fails):
    connection = Connection(SESSION)
    sql = "SELECT 'value'"
    error = psycopg.DataError("statement rejected")
    connection.statement_errors[sql] = error
    if rollback_fails:
        connection.end_errors["rollback"] = psycopg.OperationalError("connection lost at rollback")
        connection.close_on_end_error = True
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: connection)
    run = make_postgres_result_query(Settings(_env_file=None))
    with pytest.raises(ResultCaptureError) as caught:
        run(sql)
    assert str(caught.value) == "PostgreSQL query failed: DataError: statement rejected"
    assert caught.value.__context__ is error
    checks = ["check " + setting for setting in SESSION]
    assert connection.events == (
        checks
        + checks
        + ["begin"]
        + checks
        + ["execute", "rollback"]
        + (["rollback failed"] if rollback_fails else [])
    )
    assert caplog.record_tuples == (
        [("psycopg", logging.WARNING, "error ignored in rollback: connection lost at rollback")]
        if rollback_fails
        else []
    )
    assert connection.statements == [sql]
    assert connection.transactions == 1
    assert connection.closed is rollback_fails
    assert not connection.in_transaction


@pytest.mark.parametrize(
    "setting,value", [("client_encoding", "LATIN1"), ("standard_conforming_strings", "off")]
)
def test_capture_does_not_reconnect_after_session_refusal(monkeypatch, setting, value):
    connection = Connection(SESSION)
    sql = f"SELECT set_config('{setting}', '{value}', true)"
    connection.statement_settings[sql] = [(setting, value, True)]
    connections = []

    def connect(**kwargs):
        opened = connection if not connections else Connection(SESSION)
        connections.append(opened)
        return opened

    monkeypatch.setattr(psycopg, "connect", connect)
    run = make_postgres_result_query(Settings(_env_file=None))
    with pytest.raises(ResultCaptureError) as caught:
        run(sql)
    assert_session_refusal(caught, make_postgres_result_query, setting, value)
    checks = ["check " + name for name in SESSION]
    final_checks = checks[: checks.index("check " + setting) + 1]
    assert connection.events == (
        checks + checks + ["begin"] + checks + ["execute", "fetchall"] + final_checks + ["closed"]
    )
    assert connection.closed
    for name in SESSION:
        with pytest.raises(psycopg.OperationalError, match=r"^the connection is closed$"):
            connection.info.parameter_status(name)
    events = list(connection.events)
    with pytest.raises(ResultCaptureError) as second:
        run("SELECT 'value'")
    assert str(second.value) == (
        "PostgreSQL connection failed: OperationalError: the connection is closed"
    )
    assert connection.events == [*events, "check client_encoding"]
    assert connection.statements == [sql]
    assert connection.transactions == 1
    assert connection.closed
    assert not connection.in_transaction
    assert connections == [connection]


def test_runtime_reopens_after_session_refusal(monkeypatch):
    rejected = Connection({**SESSION, "standard_conforming_strings": "off"})
    accepted = Connection(SESSION)
    connections = iter([rejected, accepted])
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: next(connections))
    run = make_postgres_runtime_probe(Settings(_env_file=None))
    with pytest.raises(psycopg.Error, match="standard_conforming_strings"):
        invoke(make_postgres_runtime_probe, run)
    invoke(make_postgres_runtime_probe, run)
    assert rejected.closed and rejected.statements == []
    assert set(accepted.checked) == set(SESSION)


@pytest.mark.parametrize("factory", FACTORIES)
def test_factory_keeps_connect_error_contract(monkeypatch, factory):
    error = psycopg.OperationalError("connection refused")

    def connect(**kwargs):
        raise error

    monkeypatch.setattr(psycopg, "connect", connect)
    error_type = ResultCaptureError if factory is make_postgres_result_query else psycopg.Error
    with pytest.raises(error_type) as caught:
        invoke(factory, factory(Settings(_env_file=None)))
    if factory is make_postgres_result_query:
        assert str(caught.value) == (
            "PostgreSQL connection failed: OperationalError: connection refused"
        )
    else:
        assert caught.value is error


CHANGED_SETTINGS = [("client_encoding", "EUC_JP"), ("standard_conforming_strings", "off")]


def assert_session_refusal(caught, factory, setting, value):
    message = (
        f"PostgreSQL session requires {setting}={SESSION[setting]!r}; server reported {value!r}"
    )
    if factory is make_postgres_result_query:
        message = "PostgreSQL connection failed: OperationalError: " + message
    assert str(caught.value) == message


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("setting,value", CHANGED_SETTINGS)
def test_factory_refuses_changed_session_before_next_statement(
    monkeypatch, factory, setting, value
):
    connection = Connection(SESSION)
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: connection)
    run = factory(Settings(_env_file=None))
    invoke(factory, run)
    sent = list(connection.statements)
    transactions = connection.transactions
    connection.reported[setting] = value
    error_type = (
        ResultCaptureError if factory is make_postgres_result_query else psycopg.OperationalError
    )
    with pytest.raises(error_type) as caught:
        invoke(factory, run)
    assert_session_refusal(caught, factory, setting, value)
    assert connection.statements == sent
    assert connection.transactions == transactions
    assert connection.closed


@pytest.mark.parametrize("factory", [make_run_query_pg, make_postgres_runtime_probe])
@pytest.mark.parametrize("setting,value", CHANGED_SETTINGS)
def test_query_setting_change_refuses_following_question(monkeypatch, factory, setting, value):
    connection = Connection(SESSION)

    def execute(sql):
        connection.statements.append(sql)
        if sql.startswith("SELECT"):
            connection.reported[setting] = value

    monkeypatch.setattr(connection, "execute", execute)
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: connection)
    run = factory(Settings(_env_file=None))
    invoke(factory, run)
    assert connection.reported[setting] == value
    sent = list(connection.statements)
    transactions = connection.transactions
    with pytest.raises(psycopg.OperationalError) as caught:
        invoke(factory, run)
    assert_session_refusal(caught, factory, setting, value)
    assert connection.statements == sent
    assert connection.transactions == transactions


@pytest.mark.parametrize(
    "statement", ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = 50"]
)
@pytest.mark.parametrize("setting,value", CHANGED_SETTINGS)
def test_runtime_checks_between_setup_statements(monkeypatch, statement, setting, value):
    connection = Connection(SESSION)

    def execute(sql):
        connection.statements.append(sql)
        if sql == statement:
            connection.reported[setting] = value

    monkeypatch.setattr(connection, "execute", execute)
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: connection)
    run = make_postgres_runtime_probe(Settings(_env_file=None))
    with pytest.raises(psycopg.OperationalError) as caught:
        invoke(make_postgres_runtime_probe, run)
    assert_session_refusal(caught, make_postgres_runtime_probe, setting, value)
    assert connection.statements[-1] == statement
    assert connection.transactions == 1


@pytest.mark.parametrize("phase", ["execute", "fetchall"])
@pytest.mark.parametrize("setting,value", CHANGED_SETTINGS)
def test_capture_refuses_settings_changed_during_statement(monkeypatch, phase, setting, value):
    connection = Connection(SESSION)
    original = getattr(connection, phase)

    def change_setting(*args):
        result = original(*args)
        connection.reported[setting] = value
        return result

    monkeypatch.setattr(connection, phase, change_setting)
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: connection)
    run = make_postgres_result_query(Settings(_env_file=None))
    with pytest.raises(ResultCaptureError) as caught:
        run("SELECT 'value'")
    assert_session_refusal(caught, make_postgres_result_query, setting, value)
    assert connection.statements == ["SELECT 'value'"]
    assert connection.closed
