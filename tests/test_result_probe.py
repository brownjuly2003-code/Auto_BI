"""Live-result capture at the verified-asset boundary (architecture section 8.4.1)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import clickhouse_connect
import psycopg
import pytest

from auto_bi.config import Settings
from auto_bi.verification.repository import ReferenceQuery, VerifiedAsset
from auto_bi.verification.result_probe import (
    QueryColumn,
    QueryResult,
    ResultCaptureError,
    capture,
    make_clickhouse_result_query,
    make_postgres_result_query,
)

ASSET_ID = "asset://sales/weekly_margin_by_region"
CANDIDATE_DIGEST = "sha256:" + "1" * 64
CAPTURED_AT = "2026-09-11T14:30:00Z"


def _asset(
    *,
    engine: str = "clickhouse",
    sql: str = "SELECT region, gross_margin_pct FROM dm.sales_daily",
    columns: list[dict[str, object]] | None = None,
) -> VerifiedAsset:
    result_columns = columns or [
        {"name": "region", "type": "string", "nullable": False},
        {"name": "gross_margin_pct", "type": "decimal", "unit": "ratio"},
    ]
    return VerifiedAsset.model_validate(
        {
            "metadata": {
                "id": ASSET_ID,
                "version": 1,
                "owner": "commercial-analytics",
                "risk_tier": "medium",
            },
            "question": "Какая валовая маржа по регионам?",
            "intent": "breakdown",
            "metric_refs": ["metric://sales/gross_margin_pct@3"],
            "expectation": {
                "kind": "result",
                "result": {"columns": result_columns, "rows": []},
            },
            "tolerance": {"business_key": [result_columns[0]["name"]]},
            "reference_query": {"engine": engine, "sql": sql},
            "semantic_digest": "sha256:" + "0" * 64,
            "review": {
                "reviewer": "commercial-analytics",
                "reviewed_at": "2026-09-10T12:00:00Z",
                "rationale": "reviewed against the governed mart",
            },
        }
    )


def test_capture_runs_the_exact_reviewed_query_and_builds_its_attestation() -> None:
    asset = _asset()
    executed: list[str] = []

    def run_query(sql: str) -> QueryResult:
        executed.append(sql)
        return QueryResult(
            columns=(
                QueryColumn("region", "String", nullable=False),
                QueryColumn("gross_margin_pct", "Nullable(Decimal(18, 6))", nullable=True),
            ),
            rows=(("Москва", Decimal("0.412000")), ("Казань", None)),
        )

    recorded = capture(
        asset,
        run_query,
        candidate_digest=CANDIDATE_DIGEST,
        environment_ref="clickhouse-stage:8123/dm",
        executed_by="gate_ro",
        captured_at=CAPTURED_AT,
    )

    assert executed == [asset.reference_query.sql]
    assert recorded.result.columns == asset.expectation.result.columns
    assert recorded.result.rows == [["Москва", "0.412000"], ["Казань", None]]
    assert recorded.attestation is not None
    assert recorded.attestation.model_dump(mode="json") == {
        "origin": "query_run",
        "captured_at": "2026-09-11T14:30:00Z",
        "environment_ref": "clickhouse-stage:8123/dm",
        "semantic_digest": CANDIDATE_DIGEST,
        "query_digest": ReferenceQuery(engine="clickhouse", sql=asset.reference_query.sql).digest(),
        "executed_by": "gate_ro",
    }


def test_clickhouse_query_requests_timezone_aware_datetimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def query(sql: str, *, tz_mode: str | None = None) -> SimpleNamespace:
        calls.append((sql, tz_mode))
        return SimpleNamespace(
            column_names=("captured",),
            column_types=(SimpleNamespace(name="DateTime64(0, 'UTC')", nullable=False),),
            result_rows=((datetime(2026, 9, 11, 13, 14, 15, tzinfo=UTC),),),
        )

    client = SimpleNamespace(query=query)
    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **_kwargs: client)

    run_query = make_clickhouse_result_query(Settings(_env_file=None))
    run_query("SELECT now64(0, 'UTC') AS captured")

    assert calls == [("SELECT now64(0, 'UTC') AS captured", "aware")]


def test_clickhouse_empty_result_uses_a_read_only_metadata_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []
    responses = [
        SimpleNamespace(column_names=(), column_types=(), result_rows=()),
        SimpleNamespace(
            column_names=("label",),
            column_types=(SimpleNamespace(name="String", nullable=False),),
            result_rows=(),
        ),
    ]

    def query(sql: str, *, tz_mode: str | None = None) -> SimpleNamespace:
        calls.append((sql, tz_mode))
        return responses.pop(0)

    client = SimpleNamespace(query=query)
    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **_kwargs: client)

    run_query = make_clickhouse_result_query(Settings(_env_file=None))
    result = run_query("SELECT 'sample' AS label WHERE 0;")

    assert calls == [
        ("SELECT 'sample' AS label WHERE 0;", "aware"),
        ("SELECT * FROM (SELECT 'sample' AS label WHERE 0) LIMIT 0", "aware"),
    ]
    assert result == QueryResult(
        columns=(QueryColumn("label", "String", nullable=False),),
        rows=(),
    )


def test_clickhouse_query_failure_is_a_result_capture_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def query(_sql: str, *, tz_mode: str | None = None) -> SimpleNamespace:
        del tz_mode
        raise clickhouse_connect.driver.exceptions.OperationalError("connection closed")

    monkeypatch.setattr(
        clickhouse_connect,
        "get_client",
        lambda **_kwargs: SimpleNamespace(query=query),
    )

    run_query = make_clickhouse_result_query(Settings(_env_file=None))

    with pytest.raises(ResultCaptureError, match=r"ClickHouse query failed.*connection closed"):
        run_query("SELECT 1")


def test_postgres_connection_failure_is_a_result_capture_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def connect(**_kwargs: object) -> object:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", connect)

    with pytest.raises(ResultCaptureError, match=r"PostgreSQL connection failed.*refused"):
        make_postgres_result_query(Settings(_env_file=None))


def test_postgres_values_are_normalised_without_binary_float_or_datetime_ambiguity() -> None:
    columns: list[dict[str, object]] = [
        {"name": "label", "type": "string", "nullable": False},
        {"name": "count", "type": "integer", "nullable": False},
        {"name": "amount", "type": "decimal"},
        {"name": "score", "type": "float"},
        {"name": "enabled", "type": "boolean", "nullable": False},
        {"name": "day", "type": "date", "nullable": False},
        {"name": "captured", "type": "timestamp", "nullable": False},
    ]
    asset = _asset(
        engine="postgres",
        sql="SELECT label, count, amount, score, enabled, day, captured FROM dm.capture_types",
        columns=columns,
    )

    recorded = capture(
        asset,
        lambda _sql: QueryResult(
            columns=(
                QueryColumn("label", "text"),
                QueryColumn("count", "bigint"),
                QueryColumn("amount", "numeric(20,4)"),
                QueryColumn("score", "double precision"),
                QueryColumn("enabled", "boolean"),
                QueryColumn("day", "date"),
                QueryColumn("captured", "timestamp with time zone"),
            ),
            rows=(
                (
                    "sample",
                    7,
                    Decimal("12.3400"),
                    1.25,
                    False,
                    date(2026, 9, 11),
                    datetime(2026, 9, 11, 13, 14, 15, tzinfo=UTC),
                ),
            ),
        ),
        candidate_digest=CANDIDATE_DIGEST,
        environment_ref="postgres-stage:5432/analytics",
        executed_by="gate_ro",
        captured_at=CAPTURED_AT,
    )

    assert recorded.result.rows == [
        ["sample", "7", "12.3400", "1.25", "false", "2026-09-11", "2026-09-11T13:14:15Z"]
    ]


def test_psycopg_type_display_names_are_understood() -> None:
    asset = _asset(
        engine="postgres",
        sql="SELECT label, captured FROM dm.capture_types",
        columns=[
            {"name": "label", "type": "string", "nullable": False},
            {"name": "captured", "type": "timestamp", "nullable": False},
        ],
    )

    recorded = capture(
        asset,
        lambda _sql: QueryResult(
            columns=(
                QueryColumn("label", "varchar"),
                QueryColumn("captured", "timestamptz"),
            ),
            rows=(
                (
                    "sample",
                    datetime(
                        2026,
                        9,
                        11,
                        9,
                        14,
                        15,
                        tzinfo=timezone(-timedelta(hours=4)),
                    ),
                ),
            ),
        ),
        candidate_digest=CANDIDATE_DIGEST,
        environment_ref="postgres-stage:5432/analytics",
        executed_by="gate_ro",
        captured_at=CAPTURED_AT,
    )

    assert recorded.result.rows == [["sample", "2026-09-11T13:14:15Z"]]


def test_an_empty_answer_still_carries_driver_checked_columns() -> None:
    asset = _asset(
        engine="postgres",
        sql="SELECT region FROM dm.sales_daily WHERE false",
        columns=[{"name": "region", "type": "string", "nullable": False}],
    )

    recorded = capture(
        asset,
        lambda _sql: QueryResult(columns=(QueryColumn("region", "text"),), rows=()),
        candidate_digest=CANDIDATE_DIGEST,
        environment_ref="postgres-stage:5432/analytics",
        executed_by="gate_ro",
        captured_at=CAPTURED_AT,
    )

    assert recorded.result.columns == asset.expectation.result.columns
    assert recorded.result.rows == []


def test_an_empty_answer_cannot_hide_a_different_result_schema() -> None:
    asset = _asset(
        engine="postgres",
        sql="SELECT region FROM dm.sales_daily WHERE false",
        columns=[{"name": "region", "type": "string", "nullable": False}],
    )

    with pytest.raises(ResultCaptureError, match=r"expected column 'region'.*got 'store_id'"):
        capture(
            asset,
            lambda _sql: QueryResult(columns=(QueryColumn("store_id", "bigint"),), rows=()),
            candidate_digest=CANDIDATE_DIGEST,
            environment_ref="postgres-stage:5432/analytics",
            executed_by="gate_ro",
            captured_at=CAPTURED_AT,
        )
