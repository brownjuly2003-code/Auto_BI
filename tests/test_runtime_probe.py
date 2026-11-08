"""Exact-query runtime cost evidence, without a live warehouse."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from psycopg.pq import TransactionStatus

from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.verification import query_limits, runtime_probe
from auto_bi.verification.environment import CostBound, PlanQuestion

SQL = "SELECT date, store_id, sum(revenue) AS m FROM dm.sales_daily GROUP BY date, store_id"
METRIC_REF = "metric://sales/gross_margin_pct@3"


def question(**budgets: int) -> PlanQuestion:
    return PlanQuestion(
        metric_ref=METRIC_REF,
        table="dm.sales_daily",
        dialect="clickhouse",
        sql=SQL,
        budgets=MetricBudget(**budgets),
    )


class FakeClickHouseError(Exception):
    pass


class FakeClickHouse:
    def __init__(self, *, summary: dict[str, Any] | None = None, error: Exception | None = None):
        self.summary = summary or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.query_calls = 0
        self.command_calls = 0

    def query(self, sql: str, *, settings: dict[str, Any]) -> Any:
        self.query_calls += 1
        return self._execute(sql, settings)

    def command(self, sql: str, *, settings: dict[str, Any] | None = None) -> Any:
        if "getSetting('max_execution_time')" in sql:
            return [0, 0] + ["throw"] * (sql.count("getSetting") - 2)
        if sql == "SET readonly = 2":
            return None
        if sql == "SELECT getSetting('readonly')":
            return 2
        self.command_calls += 1
        return self._execute(sql, settings or {})

    def _execute(self, sql: str, settings: dict[str, Any]) -> Any:
        self.calls.append((sql, settings))
        if self.error is not None:
            raise self.error
        return type("Result", (), {"summary": self.summary})()


def test_clickhouse_runs_the_exact_select_with_a_null_sink_and_engine_limits() -> None:
    client = FakeClickHouse(
        summary={"read_bytes": "48000", "elapsed_ns": "12000001", "query_id": "cost-17"}
    )

    fact = runtime_probe._clickhouse_execution(
        client,
        question(max_scanned_bytes=50_000, timeout_ms=2_500),
        error_type=FakeClickHouseError,
    )

    sql, settings = client.calls[0]
    assert sql == f"{SQL}\nFORMAT Null"
    assert settings == {
        **runtime_probe.clickhouse_query_settings(2500),
        "max_bytes_to_read": 50_000,
        "read_overflow_mode": "throw",
        "max_execution_time": 2.5,
        "timeout_overflow_mode": "throw",
    }
    assert fact.scanned_bytes == 48_000
    assert fact.elapsed_ms == 13
    assert fact.checked_bounds == [CostBound.SCANNED_BYTES, CostBound.TIMEOUT]
    assert fact.exceeded_bound is None
    assert "query_id=cost-17" in fact.evidence


def test_clickhouse_preserves_format_null_instead_of_asking_query_to_append_native() -> None:
    """clickhouse-connect's query API appends FORMAT Native; command preserves our Null sink."""
    client = FakeClickHouse(summary={"read_bytes": "1", "elapsed_ns": "1"})

    runtime_probe._clickhouse_execution(
        client,
        question(max_scanned_bytes=50_000, timeout_ms=2_500),
        error_type=FakeClickHouseError,
    )

    assert client.command_calls == 1
    assert client.query_calls == 0


def test_clickhouse_zero_contract_limits_are_not_sent_as_unlimited_zeroes() -> None:
    client = FakeClickHouse(summary={"read_bytes": 0, "elapsed_ns": 0})

    runtime_probe._clickhouse_execution(
        client,
        question(max_scanned_bytes=0, timeout_ms=0),
        error_type=FakeClickHouseError,
    )

    settings = client.calls[0][1]
    assert settings["max_bytes_to_read"] == 1
    assert settings["max_execution_time"] == 0.001


def test_clickhouse_does_not_claim_an_unreported_zero_bound_was_checked() -> None:
    client = FakeClickHouse()

    fact = runtime_probe._clickhouse_execution(
        client,
        question(max_scanned_bytes=0, timeout_ms=0),
        error_type=FakeClickHouseError,
    )

    assert fact.checked_bounds == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Code: 307. TOO_MANY_BYTES: max_bytes_to_read exceeded", CostBound.SCANNED_BYTES),
        ("Code: 159. TIMEOUT_EXCEEDED: max_execution_time exceeded", CostBound.TIMEOUT),
    ],
)
def test_clickhouse_limit_exceptions_are_typed_budget_evidence(
    message: str, expected: CostBound
) -> None:
    client = FakeClickHouse(error=FakeClickHouseError(message))

    fact = runtime_probe._clickhouse_execution(
        client,
        question(max_scanned_bytes=50_000, timeout_ms=2_500),
        error_type=FakeClickHouseError,
    )

    assert fact.checked_bounds == [expected]
    assert fact.exceeded_bound is expected
    assert expected.value in fact.evidence


def test_clickhouse_safety_timeout_is_not_misreported_as_a_contract_violation() -> None:
    client = FakeClickHouse(
        error=FakeClickHouseError("Code: 159. TIMEOUT_EXCEEDED: max_execution_time exceeded")
    )

    fact = runtime_probe._clickhouse_execution(
        client,
        question(max_scanned_bytes=50_000, timeout_ms=120_000),
        error_type=FakeClickHouseError,
    )

    assert fact.checked_bounds == []
    assert fact.exceeded_bound is None
    assert "safety timeout" in fact.evidence


#: The server's own lines, as ClickHouse 24.8.14.39 said them live
#: (tests/test_decision_live_clickhouse.py), with the URL and version a fact must not carry.
SERVER_SAID = (
    "HTTPDriver for http://127.0.0.1:18125 received ClickHouse error code {code}\n Code: {code}. "
    "DB::Exception: {text}. ({name}) (version 24.8.14.39 (official build))"
)


@pytest.mark.parametrize(
    ("code", "name", "text"),
    [
        (43, "ILLEGAL_TYPE_OF_ARGUMENT", "Illegal type Float64 of 1st argument of divideDecimal"),
        (47, "UNKNOWN_IDENTIFIER", "Unknown expression identifier `revenue`"),
    ],
)
def test_clickhouse_exact_select_its_plan_accepted_but_cannot_run_ends_the_run(
    code: int, name: str, text: str
) -> None:
    """environment_probe.py does not send the exact SELECT of a plan the mart's drift no
    longer fits, so `43`/`47` here means a query its own plan accepted that the engine then
    could not run: the gate's defect, raised as it was rather than sealed as a fact."""
    error = FakeClickHouseError(SERVER_SAID.format(code=code, name=name, text=text))
    client = FakeClickHouse(error=error)

    with pytest.raises(FakeClickHouseError) as raised:
        runtime_probe._clickhouse_execution(
            client,
            question(max_scanned_bytes=50_000, timeout_ms=2_500),
            error_type=FakeClickHouseError,
        )

    assert raised.value is error


def test_clickhouse_exact_select_refused_for_a_column_is_an_unmeasured_fact() -> None:
    """`SELECT(gross_margin)` revoked: `dwh.security` blocks on it; the exact SELECT that
    names the column is refused, and that refusal is an answer, not a broken run."""
    client = FakeClickHouse(
        error=FakeClickHouseError(
            SERVER_SAID.format(
                code=497,
                name="ACCESS_DENIED",
                text="gate_ro: Not enough privileges. To execute this query, it's necessary to "
                "have the grant SELECT(gross_margin) ON dm.sales_daily",
            )
        )
    )

    fact = runtime_probe._clickhouse_execution(
        client, question(max_scanned_bytes=50_000, timeout_ms=2_500), error_type=FakeClickHouseError
    )

    assert fact.checked_bounds == []
    assert fact.exceeded_bound is None
    assert fact.evidence == (
        "ClickHouse refused the exact SELECT with Code 497 ACCESS_DENIED; no declared runtime "
        "bound was measured"
    )


@pytest.mark.parametrize(
    "message",
    [
        "Code: 62. DB::Exception: Syntax error: failed at position 1. (SYNTAX_ERROR)",
        "Code: 210. DB::NetException: Connection refused. (NETWORK_ERROR)",
        "ACCESS_DENIED with no code line",
    ],
)
def test_clickhouse_exact_select_failing_otherwise_still_ends_the_run(message: str) -> None:
    error = FakeClickHouseError(message)
    client = FakeClickHouse(error=error)

    with pytest.raises(FakeClickHouseError) as raised:
        runtime_probe._clickhouse_execution(
            client,
            question(max_scanned_bytes=50_000, timeout_ms=2_500),
            error_type=FakeClickHouseError,
        )

    assert raised.value is error


def test_clickhouse_code_carried_by_the_driver_is_named_the_same() -> None:
    """Newer drivers carry the server code as an attribute and no `Code:` line."""
    error = FakeClickHouseError("Unknown expression identifier `revenue`")
    error.code = 47  # type: ignore[attr-defined]

    assert query_limits.clickhouse_named_code(error, query_limits.CLICKHOUSE_QUERY_UNFIT) == (
        "Code 47 UNKNOWN_IDENTIFIER"
    )
    assert query_limits.clickhouse_named_code(error, query_limits.CLICKHOUSE_QUERY_REFUSED) is None
    assert (
        query_limits.clickhouse_named_code(
            FakeClickHouseError("no code at all"), query_limits.CLICKHOUSE_QUERY_UNFIT
        )
        is None
    )


class FakeQueryCanceled(Exception):
    sqlstate = "57014"


class FakeCursor:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.statements: list[str] = []
        self.stream_size: int | None = None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str) -> None:
        self.statements.append(sql)

    def fetchone(self):
        return ("on",)

    def stream(self, sql: str, *, size: int = 1) -> Any:
        self.statements.append(sql)
        self.stream_size = size
        if self.error is not None:
            raise self.error
        yield (1,)
        yield (2,)


class FakeTransaction:
    def __enter__(self) -> FakeTransaction:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class FakePostgres:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.info = SimpleNamespace(
            transaction_status=TransactionStatus.IDLE,
            parameter_status={
                "client_encoding": "UTF8",
                "standard_conforming_strings": "on",
            }.get,
        )
        self.cursor_value = FakeCursor(error=error)
        self.transactions = 0

    def transaction(self, *, force_rollback=False) -> FakeTransaction:
        self.transactions += 1
        return FakeTransaction()

    def cursor(self) -> FakeCursor:
        return self.cursor_value


def test_postgres_streams_the_exact_select_under_a_transaction_local_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakePostgres()
    ticks = iter((1_000_000, 18_000_001))
    monkeypatch.setattr(runtime_probe, "perf_counter_ns", lambda: next(ticks))
    asked = question(timeout_ms=250).model_copy(update={"dialect": "postgres"})

    fact = runtime_probe._postgres_execution(connection, asked, cancelled_error=FakeQueryCanceled)

    assert connection.cursor_value.statements == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = 250",
        "SHOW transaction_read_only",
        SQL,
    ]
    assert connection.cursor_value.stream_size == 1
    assert fact.checked_bounds == [CostBound.TIMEOUT]
    assert fact.elapsed_ms == 18


def test_postgres_statement_timeout_is_typed_as_a_budget_violation() -> None:
    connection = FakePostgres(
        error=FakeQueryCanceled("canceling statement due to statement timeout")
    )
    asked = question(timeout_ms=250).model_copy(update={"dialect": "postgres"})

    fact = runtime_probe._postgres_execution(connection, asked, cancelled_error=FakeQueryCanceled)

    assert fact.checked_bounds == [CostBound.TIMEOUT]
    assert fact.exceeded_bound is CostBound.TIMEOUT


def test_postgres_does_not_execute_a_query_only_to_guess_at_scanned_bytes() -> None:
    connection = FakePostgres()
    asked = question(max_scanned_bytes=50_000).model_copy(update={"dialect": "postgres"})

    fact = runtime_probe._postgres_execution(connection, asked, cancelled_error=FakeQueryCanceled)

    assert connection.transactions == 0
    assert fact.checked_bounds == []
    assert fact.scanned_bytes is None
    assert "does not report scanned bytes" in fact.evidence


class FakePostgresError(Exception):
    """A psycopg error as the gate holds it: the SQLSTATE in `sqlstate`, the server's text."""

    def __init__(self, sqlstate: str, message: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def test_postgres_exact_select_refused_for_a_column_is_an_unmeasured_fact() -> None:
    """`SELECT` granted on every column of the mart but `revenue` (PostgreSQL 16.15, live):
    `dwh.security` blocks on it, and the exact SELECT that names the column is refused. That
    refusal is an answer, as on ClickHouse, not a broken run."""
    connection = FakePostgres(
        error=FakePostgresError("42501", "permission denied for table sales_daily")
    )
    asked = question(timeout_ms=250).model_copy(update={"dialect": "postgres"})

    fact = runtime_probe._postgres_execution(connection, asked, cancelled_error=FakeQueryCanceled)

    assert fact.checked_bounds == []
    assert fact.exceeded_bound is None
    assert fact.elapsed_ms is None
    assert fact.evidence == (
        "PostgreSQL refused the exact SELECT with SQLSTATE 42501 insufficient_privilege; no "
        "declared runtime bound was measured"
    )


@pytest.mark.parametrize(
    "error",
    [
        FakePostgresError("42703", 'column "revenue" does not exist'),
        FakePostgresError("42883", "function sum(text) does not exist"),
        FakePostgresError("42601", 'syntax error at or near "FROM"'),
        FakePostgresError("08006", "server closed the connection unexpectedly"),
        Exception("permission denied for table sales_daily"),
    ],
    ids=["42703", "42883", "syntax", "connection", "no-sqlstate"],
)
def test_postgres_exact_select_failing_otherwise_still_ends_the_run(error: Exception) -> None:
    """`42703`/`42883` here mean a query its own plan accepted that the engine then could not
    run (environment_probe.py does not send the exact SELECT of a plan the mart's drift no
    longer fits); the rest are ours or the run's. A refusal is told by its state, never by
    its text."""
    connection = FakePostgres(error=error)
    asked = question(timeout_ms=250).model_copy(update={"dialect": "postgres"})

    with pytest.raises(type(error)) as raised:
        runtime_probe._postgres_execution(connection, asked, cancelled_error=FakeQueryCanceled)

    assert raised.value is error


def test_only_the_drivers_cancellation_is_read_as_a_statement_timeout() -> None:
    """The cancellation class decides, as before S7b: another error whose text happens to
    mention a statement timeout is not a measured bound."""
    error = FakePostgresError("XX000", "could not arm the statement timeout")
    connection = FakePostgres(error=error)
    asked = question(timeout_ms=250).model_copy(update={"dialect": "postgres"})

    with pytest.raises(FakePostgresError) as raised:
        runtime_probe._postgres_execution(connection, asked, cancelled_error=FakeQueryCanceled)

    assert raised.value is error


def test_a_cancellation_other_than_the_statement_timeout_still_ends_the_run() -> None:
    """`pg_cancel_backend` raises the same class; only the statement timeout is a bound."""
    error = FakeQueryCanceled("canceling statement due to user request")
    connection = FakePostgres(error=error)
    asked = question(timeout_ms=250).model_copy(update={"dialect": "postgres"})

    with pytest.raises(FakeQueryCanceled) as raised:
        runtime_probe._postgres_execution(connection, asked, cancelled_error=FakeQueryCanceled)

    assert raised.value is error


def test_postgres_safety_timeout_is_not_misreported_as_a_contract_violation() -> None:
    connection = FakePostgres(
        error=FakeQueryCanceled("canceling statement due to statement timeout")
    )
    asked = question(timeout_ms=120_000).model_copy(update={"dialect": "postgres"})

    fact = runtime_probe._postgres_execution(connection, asked, cancelled_error=FakeQueryCanceled)

    assert (
        f"SET LOCAL statement_timeout = {runtime_probe.SAFETY_TIMEOUT_MS}"
        in connection.cursor_value.statements
    )
    assert fact.checked_bounds == []
    assert fact.exceeded_bound is None
    assert "safety timeout" in fact.evidence


def test_a_postgres_state_carried_as_psycopg2_carries_it_is_named_the_same() -> None:
    """psycopg2 keeps the SQLSTATE in `pgcode`; psycopg 3 in `sqlstate`."""
    error = Exception('column "revenue" does not exist')
    error.pgcode = "42703"  # type: ignore[attr-defined]

    assert query_limits.postgres_named_state(error, query_limits.POSTGRES_QUERY_UNFIT) == (
        "SQLSTATE 42703 undefined_column"
    )
    assert query_limits.postgres_named_state(error, query_limits.POSTGRES_QUERY_REFUSED) is None
    assert (
        query_limits.postgres_named_state(
            Exception("no state at all"), query_limits.POSTGRES_QUERY_UNFIT
        )
        is None
    )
