"""Canonical result comparison (DECISIONOPS_ARCHITECTURE §8.4).

This is where a release gate decides whether two runs produced *the same numbers*, so the
comparator is built to refuse rather than to reassure. Three outcomes, not two: two results
are `equivalent`, they are `different`, or the comparison is `insufficient` — the inputs
could not settle the question. A bounded or truncated result, an undeclared float tolerance
and a business key that does not identify a row all land in the third bucket, because
calling any of them "equivalent" is how a false pass gets manufactured.

The rules, in the order they run:

1. schema before values — names, logical types, nullability and units. A schema difference
   ends the comparison: comparing values across different shapes proves nothing.
2. rows are matched by the declared business key, never by position. If the key is not
   unique the comparison is insufficient — sorting by "something" would make the result
   depend on the sort.
3. integers, decimals, booleans, dates and strings compare exactly (a decimal compares as a
   decimal, so `0.4120` equals `0.412`); floats compare only inside a declared absolute or
   relative tolerance.
4. `NULL`, zero, a missing row and an empty result are four distinct states with four
   distinct codes.
5. order is a difference only when the contract says the order is part of the answer
   (rank/top-N).

Values travel as strings — decimals as decimal strings — for the same reason contracts do:
a binary float in the payload would make the comparison depend on the serializer.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import Field, model_validator

from auto_bi.scalars import DecimalString, NonBlankText
from auto_bi.strict import StrictModel

_EXACT_TYPES = frozenset({"string", "integer", "decimal", "boolean", "date", "timestamp"})


class ColumnType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"


class ComparisonOutcome(StrEnum):
    EQUIVALENT = "equivalent"
    DIFFERENT = "different"
    #: The inputs cannot settle the question — never reported as a pass.
    INSUFFICIENT = "insufficient"


class ColumnSchema(StrictModel):
    name: NonBlankText
    type: ColumnType
    nullable: bool = True
    #: Declared unit ("RUB", "ratio"); a unit change is a schema change, not a value change.
    unit: str = ""


class ResultSet(StrictModel):
    columns: list[ColumnSchema] = Field(min_length=1)
    #: Row values as canonical strings; ``None`` is SQL NULL, which is not the empty string.
    rows: list[list[str | None]] = Field(default_factory=list)
    #: True when the query was bounded (LIMIT) and the result is not the whole answer.
    truncated: bool = False

    @model_validator(mode="after")
    def _check_shape(self) -> ResultSet:
        names = [column.name for column in self.columns]
        if len(set(names)) != len(names):
            raise ValueError("column names must be unique")
        width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"row {index} has {len(row)} values for {width} columns")
        return self

    def column_index(self, name: str) -> int | None:
        return next(
            (index for index, column in enumerate(self.columns) if column.name == name), None
        )


class ResultTolerance(StrictModel):
    """What the comparison is allowed to forgive, declared up front."""

    #: Columns that identify a row. Without it rows cannot be matched at all.
    business_key: list[str] = Field(default_factory=list)
    float_absolute: DecimalString | None = None
    float_relative: DecimalString | None = None
    #: True only when the order is part of the answer (rank, top-N).
    ordered: bool = False

    def has_float_tolerance(self) -> bool:
        return self.float_absolute is not None or self.float_relative is not None


class ResultDifference(StrictModel):
    code: NonBlankText
    detail: NonBlankText


class ComparisonReport(StrictModel):
    outcome: ComparisonOutcome
    compared_rows: int = Field(ge=0)
    differences: list[ResultDifference] = Field(default_factory=list)


def compare_results(
    base: ResultSet, candidate: ResultSet, tolerance: ResultTolerance
) -> ComparisonReport:
    """Compare two typed results under *tolerance*."""
    schema_differences = _compare_schema(base, candidate)
    if schema_differences:
        return ComparisonReport(
            outcome=ComparisonOutcome.DIFFERENT, compared_rows=0, differences=schema_differences
        )

    if base.truncated or candidate.truncated:
        return _insufficient(
            "result.truncated",
            "a bounded result cannot prove that two full results agree",
        )

    if not tolerance.business_key:
        return _insufficient(
            "key.undeclared",
            "no business key declared: rows can only be matched by position, which is not a fact",
        )

    key_indexes: list[int] = []
    for name in tolerance.business_key:
        index = base.column_index(name)
        if index is None:
            return _insufficient(
                "key.unknown_column", f"business key column {name!r} is not in the result schema"
            )
        key_indexes.append(index)

    float_columns = [c.name for c in base.columns if c.type is ColumnType.FLOAT]
    if float_columns and not tolerance.has_float_tolerance():
        return _insufficient(
            "tolerance.missing",
            f"float columns need a declared tolerance: {', '.join(sorted(float_columns))}",
        )

    try:
        base_rows = _index_rows(base, key_indexes)
        candidate_rows = _index_rows(candidate, key_indexes)
    except _DuplicateKey as exc:
        return _insufficient(
            "key.not_unique",
            f"business key does not identify a row: {exc.key} appears more than once",
        )

    differences: list[ResultDifference] = []
    differences.extend(_compare_membership(base_rows, candidate_rows))
    compared = 0
    for key, row in base_rows.items():
        other = candidate_rows.get(key)
        if other is None:
            continue
        compared += 1
        differences.extend(_compare_row(base.columns, key, row, other, tolerance))

    if tolerance.ordered and not differences:
        base_order = list(base_rows)
        candidate_order = list(candidate_rows)
        if base_order != candidate_order:
            differences.append(
                ResultDifference(
                    code="row.order_changed",
                    detail=f"row order changed: {base_order} -> {candidate_order}",
                )
            )

    outcome = ComparisonOutcome.DIFFERENT if differences else ComparisonOutcome.EQUIVALENT
    return ComparisonReport(outcome=outcome, compared_rows=compared, differences=differences)


class _DuplicateKey(Exception):
    def __init__(self, key: tuple[str | None, ...]) -> None:
        self.key = _render_key(key)
        super().__init__(self.key)


def _insufficient(code: str, detail: str) -> ComparisonReport:
    return ComparisonReport(
        outcome=ComparisonOutcome.INSUFFICIENT,
        compared_rows=0,
        differences=[ResultDifference(code=code, detail=detail)],
    )


def _compare_schema(base: ResultSet, candidate: ResultSet) -> list[ResultDifference]:
    differences: list[ResultDifference] = []
    base_columns = {column.name: column for column in base.columns}
    candidate_columns = {column.name: column for column in candidate.columns}

    for name in base_columns:
        if name not in candidate_columns:
            differences.append(
                ResultDifference(code="schema.column_removed", detail=f"column {name!r} is gone")
            )
    for name in candidate_columns:
        if name not in base_columns:
            differences.append(
                ResultDifference(code="schema.column_added", detail=f"new column {name!r}")
            )

    for name, column in base_columns.items():
        other = candidate_columns.get(name)
        if other is None:
            continue
        if column.type is not other.type:
            differences.append(
                ResultDifference(
                    code="schema.type_changed",
                    detail=f"{name}: {column.type.value} -> {other.type.value}",
                )
            )
        if column.unit != other.unit:
            differences.append(
                ResultDifference(
                    code="schema.unit_changed",
                    detail=f"{name}: unit {column.unit or 'unset'} -> {other.unit or 'unset'}",
                )
            )
        if column.nullable != other.nullable:
            differences.append(
                ResultDifference(
                    code="schema.nullability_changed",
                    detail=f"{name}: nullable {column.nullable} -> {other.nullable}",
                )
            )
    return differences


def _index_rows(
    result: ResultSet, key_indexes: list[int]
) -> dict[tuple[str | None, ...], list[str | None]]:
    indexed: dict[tuple[str | None, ...], list[str | None]] = {}
    for row in result.rows:
        key = tuple(row[index] for index in key_indexes)
        if key in indexed:
            raise _DuplicateKey(key)
        indexed[key] = row
    return indexed


def _compare_membership(
    base_rows: dict[tuple[str | None, ...], list[str | None]],
    candidate_rows: dict[tuple[str | None, ...], list[str | None]],
) -> list[ResultDifference]:
    differences: list[ResultDifference] = []
    for key in base_rows:
        if key not in candidate_rows:
            differences.append(
                ResultDifference(code="row.missing", detail=f"row {_render_key(key)} is gone")
            )
    for key in candidate_rows:
        if key not in base_rows:
            differences.append(
                ResultDifference(code="row.added", detail=f"new row {_render_key(key)}")
            )
    return differences


def _compare_row(
    columns: list[ColumnSchema],
    key: tuple[str | None, ...],
    base_row: list[str | None],
    candidate_row: list[str | None],
    tolerance: ResultTolerance,
) -> list[ResultDifference]:
    differences: list[ResultDifference] = []
    location = _render_key(key)
    for index, column in enumerate(columns):
        before, after = base_row[index], candidate_row[index]
        if before is None and after is None:
            continue
        if before is None:
            differences.append(
                ResultDifference(
                    code="value.null_became_value",
                    detail=f"{location}.{column.name}: NULL -> {after!r}",
                )
            )
            continue
        if after is None:
            differences.append(
                ResultDifference(
                    code="value.value_became_null",
                    detail=f"{location}.{column.name}: {before!r} -> NULL",
                )
            )
            continue
        if not _values_equal(column, before, after, tolerance):
            differences.append(
                ResultDifference(
                    code="value.changed",
                    detail=f"{location}.{column.name}: {before!r} -> {after!r}",
                )
            )
    return differences


def _values_equal(
    column: ColumnSchema, before: str, after: str, tolerance: ResultTolerance
) -> bool:
    if column.type is ColumnType.FLOAT:
        return _floats_equal(before, after, tolerance)
    if column.type in (ColumnType.DECIMAL, ColumnType.INTEGER):
        try:
            # A decimal compares as a decimal: "0.4120" and "0.412" are one number.
            return Decimal(before) == Decimal(after)
        except InvalidOperation:
            return before == after
    return before == after


def _floats_equal(before: str, after: str, tolerance: ResultTolerance) -> bool:
    try:
        left, right = float(before), float(after)
    except ValueError:
        return before == after
    difference = abs(left - right)
    if tolerance.float_absolute is not None and difference <= float(tolerance.float_absolute):
        return True
    if tolerance.float_relative is not None:
        scale = max(abs(left), abs(right))
        if scale == 0.0:
            return difference == 0.0
        return difference / scale <= float(tolerance.float_relative)
    return difference == 0.0


def _render_key(key: tuple[str | None, ...]) -> str:
    return "|".join("NULL" if part is None else part for part in key)
