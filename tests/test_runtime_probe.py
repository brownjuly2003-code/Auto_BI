"""Exact-query runtime cost evidence, without a live warehouse."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.verification import runtime_probe
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

    def command(self, sql: str, *, settings: dict[str, Any]) -> Any:
        self.command_calls += 1
        return self._execute(sql, settings)

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
            parameter_status={
                "client_encoding": "UTF8",
                "standard_conforming_strings": "on",
            }.get
        )
        self.cursor_value = FakeCursor(error=error)
        self.transactions = 0

    def transaction(self) -> FakeTransaction:
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
