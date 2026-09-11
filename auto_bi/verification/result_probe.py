"""Capture a reviewed query as a canonical, attested candidate result.

The release gate stays pure: it consumes ``RecordedResult`` values and never opens a DWH
connection.  This module is the IO side of that port.  It executes the exact SQL sealed in
a ``VerifiedAsset``, keeps the driver's column metadata even for an empty answer, and
normalises native values into the string representation used by ``ResultSet``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.engine import CLICKHOUSE, GREENPLUM, sqlglot_dialect
from auto_bi.scalars import Sha256Digest, Timestamp
from auto_bi.verification.provenance import ResultAttestation, ResultOrigin
from auto_bi.verification.reconcile import ColumnSchema, ColumnType, ResultSet
from auto_bi.verification.release_gate import RecordedResult
from auto_bi.verification.repository import ExpectationKind, VerifiedAsset

if TYPE_CHECKING:
    from auto_bi.config import Settings


class ResultCaptureError(ValueError):
    """The reviewed query could not be represented by its canonical result contract."""


@dataclass(frozen=True)
class QueryColumn:
    """One output column as the engine driver describes it."""

    name: str
    native_type: str
    #: DB-API cannot report this for arbitrary PostgreSQL expressions, hence ``None``.
    nullable: bool | None = None


@dataclass(frozen=True)
class QueryResult:
    """Driver rows plus metadata that survives an empty result set."""

    columns: tuple[QueryColumn, ...]
    rows: tuple[tuple[Any, ...], ...]


ResultQuery = Callable[[str], QueryResult]


def capture(
    asset: VerifiedAsset,
    run_query: ResultQuery,
    *,
    candidate_digest: Sha256Digest,
    environment_ref: str,
    executed_by: str,
    captured_at: Timestamp,
) -> RecordedResult:
    """Execute one positive asset's reviewed SQL and attest to exactly what was run."""
    if asset.expectation.kind is not ExpectationKind.RESULT or asset.expectation.result is None:
        raise ResultCaptureError(
            f"{asset.metadata.id} has no expected ResultSet to canonicalise a capture against"
        )
    query = asset.reference_query
    if query is None:
        raise ResultCaptureError(f"{asset.metadata.id} has no reviewed reference_query")

    # ReferenceQuery validates this at load time; guarding again keeps the execution boundary
    # safe even when a future caller constructs an asset through a different path.
    guard_sql(query.sql, dialect=sqlglot_dialect(query.engine))
    raw = run_query(query.sql)
    columns = _checked_columns(query.engine, raw.columns, asset.expectation.result.columns)
    rows = [_canonical_row(row, columns, index) for index, row in enumerate(raw.rows)]

    return RecordedResult(
        result=ResultSet(columns=columns, rows=rows),
        attestation=ResultAttestation(
            origin=ResultOrigin.QUERY_RUN,
            captured_at=captured_at,
            environment_ref=environment_ref,
            semantic_digest=candidate_digest,
            query_digest=query.digest(),
            executed_by=executed_by,
        ),
    )


def make_clickhouse_result_query(settings: Settings) -> ResultQuery:
    """Build the metadata-preserving ClickHouse capture callable."""
    import clickhouse_connect
    from clickhouse_connect.driver.exceptions import ClickHouseError

    try:
        client = clickhouse_connect.get_client(
            host=settings.ch_host,
            port=settings.ch_port,
            username=settings.ch_user,
            password=settings.ch_password,
            database=settings.ch_database,
        )
    except ClickHouseError as exc:
        raise ResultCaptureError(f"ClickHouse connection failed: {exc}") from None

    def run(sql: str) -> QueryResult:
        try:
            result = client.query(sql, tz_mode="aware")
            if not result.column_names:
                statement = sql.strip().rstrip(";").strip()
                result = client.query(
                    f"SELECT * FROM ({statement}) LIMIT 0",
                    tz_mode="aware",
                )
        except ClickHouseError as exc:
            raise ResultCaptureError(f"ClickHouse query failed: {exc}") from None
        return QueryResult(
            columns=tuple(
                QueryColumn(name, column_type.name, nullable=column_type.nullable)
                for name, column_type in zip(result.column_names, result.column_types, strict=True)
            ),
            rows=tuple(tuple(row) for row in result.result_rows),
        )

    return run


def make_postgres_result_query(settings: Settings) -> ResultQuery:
    """Build the metadata-preserving PostgreSQL-family capture callable."""
    import psycopg

    try:
        connection = psycopg.connect(
            host=settings.gp_host,
            port=settings.gp_port,
            dbname=settings.gp_database,
            user=settings.gp_user,
            password=settings.gp_password or None,
            autocommit=True,
        )
    except psycopg.Error as exc:
        raise ResultCaptureError(f"PostgreSQL connection failed: {exc}") from None

    def run(sql: str) -> QueryResult:
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                description = cursor.description
                if description is None:
                    raise ResultCaptureError("a reviewed SELECT returned no column metadata")
                return QueryResult(
                    columns=tuple(
                        QueryColumn(
                            column.name,
                            column.type_display,
                            nullable=column.null_ok,
                        )
                        for column in description
                    ),
                    rows=tuple(tuple(row) for row in cursor.fetchall()),
                )
        except psycopg.Error as exc:
            raise ResultCaptureError(f"PostgreSQL query failed: {exc}") from None

    return run


def _checked_columns(
    engine: str,
    actual: Sequence[QueryColumn],
    expected: Sequence[ColumnSchema],
) -> list[ColumnSchema]:
    if len(actual) != len(expected):
        raise ResultCaptureError(
            f"reviewed query returned {len(actual)} columns; expected {len(expected)}"
        )
    for index, (got, wanted) in enumerate(zip(actual, expected, strict=True)):
        if got.name != wanted.name:
            raise ResultCaptureError(
                f"expected column {wanted.name!r} at position {index + 1}, got {got.name!r}"
            )
        logical_type = _logical_type(engine, got.native_type)
        if logical_type is not wanted.type:
            raise ResultCaptureError(
                f"column {wanted.name!r} is {got.native_type!r} ({logical_type.value}); "
                f"the asset expects {wanted.type.value}"
            )
        if got.nullable is True and not wanted.nullable:
            raise ResultCaptureError(
                f"column {wanted.name!r} is nullable in the query result but not in the asset"
            )
    # Units and the declared nullable contract are business metadata, not driver metadata.
    return [column.model_copy(deep=True) for column in expected]


def _logical_type(engine: str, native_type: str) -> ColumnType:
    dialect = sqlglot_dialect(engine)
    if dialect == sqlglot_dialect(CLICKHOUSE):
        logical = _clickhouse_type(native_type)
    elif dialect == sqlglot_dialect(GREENPLUM):
        logical = _postgres_type(native_type)
    else:  # ReferenceQuery rejects unknown engines, but keep this boundary explicit.
        logical = None
    if logical is None:
        raise ResultCaptureError(
            f"unsupported {engine} result type {native_type!r}; no canonical ResultSet type"
        )
    return logical


def _clickhouse_type(native_type: str) -> ColumnType | None:
    name = native_type.strip()
    while True:
        wrapper = re.fullmatch(r"(?:Nullable|LowCardinality)\((.*)\)", name)
        if wrapper is None:
            break
        name = wrapper.group(1).strip()
    if name == "Bool":
        return ColumnType.BOOLEAN
    if re.fullmatch(r"U?Int(?:8|16|32|64|128|256)", name):
        return ColumnType.INTEGER
    if re.fullmatch(r"Decimal(?:32|64|128|256)?\(.*\)", name):
        return ColumnType.DECIMAL
    if name in {"Float32", "Float64", "BFloat16"}:
        return ColumnType.FLOAT
    if name in {"Date", "Date32"}:
        return ColumnType.DATE
    if name.startswith("DateTime"):
        return ColumnType.TIMESTAMP
    if name == "String" or name.startswith(("FixedString(", "Enum8(", "Enum16(")):
        return ColumnType.STRING
    if name in {"UUID", "IPv4", "IPv6"}:
        return ColumnType.STRING
    return None


def _postgres_type(native_type: str) -> ColumnType | None:
    name = " ".join(native_type.strip().lower().split())
    if name in {"smallint", "integer", "bigint", "int2", "int4", "int8"}:
        return ColumnType.INTEGER
    if name == "numeric" or name == "decimal" or name.startswith(("numeric(", "decimal(")):
        return ColumnType.DECIMAL
    if name in {"real", "double precision", "float4", "float8"}:
        return ColumnType.FLOAT
    if name in {"boolean", "bool"}:
        return ColumnType.BOOLEAN
    if name == "date":
        return ColumnType.DATE
    if name.startswith("timestamp"):
        return ColumnType.TIMESTAMP
    if name in {
        "text",
        "name",
        "uuid",
        "json",
        "jsonb",
        "character",
        "character varying",
        "varchar",
        "bpchar",
        "char",
    } or name.startswith(("character varying(", "character(", "varchar(", "char(")):
        return ColumnType.STRING
    return None


def _canonical_row(
    row: Sequence[Any], columns: Sequence[ColumnSchema], index: int
) -> list[str | None]:
    if len(row) != len(columns):
        raise ResultCaptureError(
            f"query row {index} has {len(row)} values for {len(columns)} columns"
        )
    return [
        _canonical_value(value, column, row=index)
        for value, column in zip(row, columns, strict=True)
    ]


def _canonical_value(value: Any, column: ColumnSchema, *, row: int) -> str | None:
    if value is None:
        if not column.nullable:
            raise ResultCaptureError(
                f"query row {row} returned NULL for non-nullable column {column.name!r}"
            )
        return None

    kind = column.type
    if kind is ColumnType.STRING:
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise _value_error(row, column, value)
        return str(value)
    if kind is ColumnType.INTEGER and isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if (
        kind is ColumnType.DECIMAL
        and isinstance(value, (Decimal, int))
        and not isinstance(value, bool)
    ):
        return format(Decimal(value), "f")
    if kind is ColumnType.FLOAT and isinstance(value, float):
        return repr(value)
    if kind is ColumnType.BOOLEAN and isinstance(value, bool):
        return "true" if value else "false"
    if kind is ColumnType.DATE and isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if kind is ColumnType.TIMESTAMP and isinstance(value, datetime):
        if value.utcoffset() is not None:
            return value.astimezone(UTC).isoformat().removesuffix("+00:00") + "Z"
        return value.isoformat()
    raise _value_error(row, column, value)


def _value_error(row: int, column: ColumnSchema, value: Any) -> ResultCaptureError:
    return ResultCaptureError(
        f"query row {row} column {column.name!r} returned {type(value).__name__}; "
        f"expected {column.type.value}"
    )


__all__ = [
    "QueryColumn",
    "QueryResult",
    "ResultCaptureError",
    "ResultQuery",
    "capture",
    "make_clickhouse_result_query",
    "make_postgres_result_query",
]
