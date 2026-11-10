"""Capture savepoint SQL keeps the driver's transaction count and original errors."""

import logging
from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from psycopg.transaction import OutOfOrderTransactionNesting

from auto_bi.config import Settings
from auto_bi.verification.result_probe import ResultCaptureError, make_postgres_result_query

SAVEPOINT = "SAVEPOINT capture_query"
RESTORE = "ROLLBACK TO SAVEPOINT capture_query"
TEXT = "SELECT 1 AS value"
METADATA = f"SELECT * FROM (\n{TEXT}\n) AS capture_metadata LIMIT 0"
SETUP = [
    "BEGIN",
    "SET TRANSACTION READ ONLY",
    "SET LOCAL statement_timeout = 75",
    "SHOW transaction_read_only",
]
CANCELED = "canceling statement due to statement timeout"


class CountingConnection:
    """Model psycopg 3.3.4 entry counting and its exit before rollback SQL."""

    def __init__(self, *, empty=False, fail=None, refusal=None, rows=None):
        self.count = 0
        self.transaction_calls = 0
        self.statements = []
        self.closed = False
        self.fail = fail
        self.refusal = refusal
        self.rows = rows if rows is not None else (() if empty else ((1,),))
        self.description = None
        self.empty = empty
        self.error = psycopg.errors.QueryCanceled(CANCELED)
        self.info = SimpleNamespace(
            transaction_status=TransactionStatus.IDLE, parameter_status=self.parameter_status
        )

    def parameter_status(self, name):
        if self.closed:
            raise psycopg.OperationalError("the connection is closed")
        if name == "client_encoding":
            return "UTF8"
        last = self.statements[-1] if self.statements else ""
        if last.startswith("SAVEPOINT "):
            last = SAVEPOINT
        return "off" if self.refusal == last else "on"

    def close(self):
        self.closed = True

    def command(self, sql):
        assert not self.closed
        self.statements.append(sql)
        # The nested transaction uses a driver-generated name, unlike cursor SQL.
        stage = (
            SAVEPOINT
            if sql.startswith("SAVEPOINT ")
            else RESTORE if sql.startswith("ROLLBACK TO SAVEPOINT ") else sql
        )
        if self.fail == stage:
            self.info.transaction_status = TransactionStatus.INERROR
            raise self.error
        if sql == "BEGIN" or stage == RESTORE:
            self.info.transaction_status = TransactionStatus.INTRANS
        elif sql in {"ROLLBACK", "COMMIT"}:
            self.info.transaction_status = TransactionStatus.IDLE

    @contextmanager
    def transaction(self, *, force_rollback=False):
        self.transaction_calls += 1
        index = self.count
        self.count += 1
        name = f"_pg3_{self.count}"
        self.command(f"SAVEPOINT {name}" if index else "BEGIN")
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            if not self.closed:
                self.count -= 1
                if self.count != index:
                    raise OutOfOrderTransactionNesting(
                        "transaction rollback at the wrong nesting level"
                    )
                try:
                    if failed or force_rollback:
                        self.command(f"ROLLBACK TO SAVEPOINT {name}" if index else "ROLLBACK")
                    else:
                        self.command(f"RELEASE SAVEPOINT {name}" if index else "COMMIT")
                except psycopg.Error as error:
                    logging.getLogger("psycopg").warning("error ignored in rollback: %s", error)

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql):
        self.command(sql)
        if sql == METADATA:
            self.describe()

    def fetchone(self):
        return ("on",)

    def describe(self):
        self.description = [SimpleNamespace(name="value", type_display="bigint", null_ok=None)]

    def stream(self, sql, *, size):
        assert size == 1
        self.command(sql)
        yield from self.rows
        if not self.empty:
            self.describe()


def capture_with(monkeypatch, connection, **limits):
    monkeypatch.setattr(psycopg, "connect", lambda **kw: connection)
    return make_postgres_result_query(Settings(_env_file=None, capture_timeout_ms=75, **limits))


@pytest.mark.parametrize("empty", [False, True])
def test_capture_sql_savepoint_order(monkeypatch, empty):
    connection = CountingConnection(empty=empty)
    result = capture_with(monkeypatch, connection)(TEXT)
    assert connection.statements == [
        *SETUP,
        SAVEPOINT,
        TEXT,
        RESTORE,
        *([METADATA] if empty else []),
        "ROLLBACK",
    ]
    assert result.rows == connection.rows and result.columns[0].name == "value"
    assert connection.transaction_calls == 1 and connection.count == 0
    assert connection.info.transaction_status == TransactionStatus.IDLE


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("stage", [SAVEPOINT, RESTORE])
def test_capture_savepoint_failure_keeps_cause_and_rolls_back(monkeypatch, empty, stage):
    connection = CountingConnection(empty=empty, fail=stage)
    with pytest.raises(ResultCaptureError) as caught:
        capture_with(monkeypatch, connection)(TEXT)
    assert str(caught.value) == f"PostgreSQL session preparation failed: QueryCanceled: {CANCELED}"
    assert caught.value.__context__ is connection.error
    assert connection.statements == [
        *SETUP,
        SAVEPOINT,
        *([TEXT, RESTORE] if stage == RESTORE else []),
        "ROLLBACK",
    ]
    assert connection.transaction_calls == 1 and connection.count == 0
    assert connection.info.transaction_status == TransactionStatus.IDLE
    assert not connection.closed


@pytest.mark.parametrize(
    "error,expected",
    [
        pytest.param(
            psycopg.errors.QueryCanceled(CANCELED),
            "capture_timeout_ms=75 exceeded (PostgreSQL)",
            id="timeout",
        ),
        pytest.param(
            psycopg.DataError("metadata rejected"),
            "PostgreSQL query failed: DataError: metadata rejected",
            id="data-error",
        ),
    ],
)
def test_capture_metadata_failure_keeps_cause_and_rolls_back(monkeypatch, error, expected):
    connection = CountingConnection(empty=True, fail=METADATA)
    connection.error = error
    with pytest.raises(ResultCaptureError) as caught:
        capture_with(monkeypatch, connection)(TEXT)
    assert str(caught.value) == expected
    assert caught.value.__context__ is error
    assert connection.statements == [*SETUP, SAVEPOINT, TEXT, RESTORE, METADATA, "ROLLBACK"]
    assert connection.transaction_calls == 1 and connection.count == 0
    assert connection.info.transaction_status == TransactionStatus.IDLE
    assert not connection.closed


@pytest.mark.parametrize("failure", ["text", "timeout", "before", "after", "rows", "bytes"])
def test_capture_error_sends_no_savepoint_rollback(monkeypatch, failure):
    connection = CountingConnection(rows=((12,), (34,)))
    limits = {}
    if failure in {"text", "timeout"}:
        connection.fail = TEXT
        if failure == "text":
            connection.error = psycopg.DataError("statement rejected")
            expected = "PostgreSQL query failed: DataError: statement rejected"
        else:
            expected = "capture_timeout_ms=75 exceeded (PostgreSQL)"
    elif failure in {"before", "after"}:
        connection.refusal = SAVEPOINT if failure == "before" else TEXT
        expected = (
            "PostgreSQL connection failed: OperationalError: "
            "PostgreSQL session requires standard_conforming_strings='on'; server reported 'off'"
        )
    else:
        limit = "capture_max_rows" if failure == "rows" else "capture_max_bytes"
        limits[limit] = 1
        expected = f"{limit}=1 exceeded"
    with pytest.raises(ResultCaptureError) as caught:
        capture_with(monkeypatch, connection, **limits)(TEXT)
    assert str(caught.value) == expected
    assert connection.statements == [
        *SETUP,
        SAVEPOINT,
        *([] if failure == "before" else [TEXT]),
        *([] if failure in {"before", "after"} else ["ROLLBACK"]),
    ]
    assert connection.transaction_calls == 1
    if failure in {"before", "after"}:
        assert connection.closed
    else:
        assert connection.count == 0
        assert connection.info.transaction_status == TransactionStatus.IDLE
