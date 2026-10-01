"""Canonical result comparison (DECISIONOPS_ARCHITECTURE §8.4).

This is where a release gate decides whether two runs produced *the same numbers*, so the
comparator is built to refuse rather than to reassure. Three outcomes, not two: two results
are `equivalent`, they are `different`, or the comparison is `insufficient` — the inputs
could not settle the question. A bounded or truncated result, an undeclared float tolerance
and a business key that does not identify a row all land in the third bucket, because
calling any of them "equivalent" is how a false pass gets manufactured.

The rules, in the order they run:

1. schema before values — names, logical types, nullability and units. Columns are read
   by name in each result. A schema difference ends the comparison: comparing values
   across different shapes proves nothing.
2. rows are matched by the declared business key, read by column name in each result,
   never by position. An integer/decimal key or group value is identified by its number.
   If the key is not unique the comparison is insufficient — sorting by "something"
   would make the result depend on the sort.
3. finite integers/decimals and other exact types compare exactly (`0.4120` equals
   `0.412`); finite floats compare inside a declared tolerance, and non-finite numbers
   compare by kind, with infinities equal only at the same sign.
4. `NULL`, zero, a missing row and an empty result keep distinct codes. An expected NaN
   cannot settle its cell: `value.expected_nan` is insufficient unless another finding
   proves a difference.
5. order is a difference only when the contract says the order is part of the answer
   (rank/top-N).
6. aggregate invariants run only when explicitly declared: partition conservation, share
   sum, ratio reconstruction and additive decomposition. A broken reference result is
   insufficient evidence; a broken candidate is different.

Values travel as strings — decimals as decimal strings — for the same reason contracts do:
a binary float in the payload would make the comparison depend on the serializer.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction
from math import isfinite, isnan
from typing import Annotated, Literal

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


class _AggregateCheckBase(StrictModel):
    absolute_tolerance: DecimalString = "0"

    @model_validator(mode="after")
    def _check_common_contract(self) -> _AggregateCheckBase:
        if Decimal(self.absolute_tolerance) < 0:
            raise ValueError("aggregate check absolute_tolerance cannot be negative")
        return self


class PartitionConservationCheck(_AggregateCheckBase):
    """Within each group, partition values sum to its repeated declared total."""

    kind: Literal["partition_conservation"] = "partition_conservation"
    value_column: NonBlankText
    total_column: NonBlankText
    group_by: list[NonBlankText] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_grouping(self) -> PartitionConservationCheck:
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("aggregate check group_by columns must be unique")
        return self


class ShareSumCheck(_AggregateCheckBase):
    """Within each group, shares sum to the declared whole (normally one)."""

    kind: Literal["share_sum"] = "share_sum"
    share_column: NonBlankText
    expected_sum: DecimalString = "1"
    group_by: list[NonBlankText] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_grouping(self) -> ShareSumCheck:
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("aggregate check group_by columns must be unique")
        return self


class RatioReconstructionCheck(_AggregateCheckBase):
    """Each ratio reconstructs as numerator / denominator times a declared factor."""

    kind: Literal["ratio_reconstruction"] = "ratio_reconstruction"
    ratio_column: NonBlankText
    numerator_column: NonBlankText
    denominator_column: NonBlankText
    factor: DecimalString = "1"

    @model_validator(mode="after")
    def _check_roles(self) -> RatioReconstructionCheck:
        roles = (self.ratio_column, self.numerator_column, self.denominator_column)
        if len(set(roles)) != len(roles):
            raise ValueError("ratio, numerator and denominator columns must be distinct")
        return self


class AdditiveDecompositionCheck(_AggregateCheckBase):
    """Each total equals the sum of its declared component columns."""

    kind: Literal["additive_decomposition"] = "additive_decomposition"
    total_column: NonBlankText
    component_columns: list[NonBlankText] = Field(min_length=2)

    @model_validator(mode="after")
    def _check_components(self) -> AdditiveDecompositionCheck:
        if len(set(self.component_columns)) != len(self.component_columns):
            raise ValueError("additive component columns must be unique")
        if self.total_column in self.component_columns:
            raise ValueError("additive total cannot also be a component")
        return self


type AggregateCheck = Annotated[
    PartitionConservationCheck
    | ShareSumCheck
    | RatioReconstructionCheck
    | AdditiveDecompositionCheck,
    Field(discriminator="kind"),
]


class ResultTolerance(StrictModel):
    """What the comparison is allowed to forgive, declared up front."""

    #: Columns that identify a row. Without it rows cannot be matched at all.
    business_key: list[str] = Field(default_factory=list)
    float_absolute: DecimalString | None = None
    float_relative: DecimalString | None = None
    #: True only when the order is part of the answer (rank, top-N).
    ordered: bool = False
    #: Explicit aggregate invariants; never inferred from column names.
    aggregate_checks: list[AggregateCheck] = Field(default_factory=list)

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
    candidate_column_indexes = {
        column.name: index for index, column in enumerate(candidate.columns)
    }
    candidate_indexes = [candidate_column_indexes[column.name] for column in base.columns]
    candidate_key_indexes: list[int] = []
    for name in tolerance.business_key:
        index = base.column_index(name)
        if index is None:
            return _insufficient(
                "key.unknown_column", f"business key column {name!r} is not in the result schema"
            )
        key_indexes.append(index)
        candidate_key_indexes.append(candidate_column_indexes[name])

    float_columns = [c.name for c in base.columns if c.type is ColumnType.FLOAT]
    if float_columns and not tolerance.has_float_tolerance():
        return _insufficient(
            "tolerance.missing",
            f"float columns need a declared tolerance: {', '.join(sorted(float_columns))}",
        )

    try:
        base_rows = _index_rows(base, key_indexes)
        candidate_rows = _index_rows(candidate, candidate_key_indexes)
    except _DuplicateKey as exc:
        return _insufficient(
            "key.not_unique",
            f"business key does not identify a row: {exc.key} appears more than once",
        )

    base_aggregate_issues, _ = _evaluate_aggregate_checks(
        base, tolerance.aggregate_checks, "base", key_indexes
    )
    if base_aggregate_issues:
        return ComparisonReport(
            outcome=ComparisonOutcome.INSUFFICIENT,
            compared_rows=0,
            differences=base_aggregate_issues,
        )

    differences: list[ResultDifference] = []
    differences.extend(
        _compare_membership(base_rows, candidate_rows, key_indexes, candidate_key_indexes)
    )
    expected_nan_issues: list[ResultDifference] = []
    compared = 0
    for key, row in base_rows.items():
        other = candidate_rows.get(key)
        if other is None:
            continue
        compared += 1
        for difference in _compare_row(
            base.columns,
            tuple(row[index] for index in key_indexes),
            row,
            other,
            tolerance,
            candidate_indexes,
            key_indexes,
        ):
            if difference.code == "value.expected_nan":
                expected_nan_issues.append(difference)
            else:
                differences.append(difference)

    if tolerance.ordered and not differences:
        base_order = list(base_rows)
        candidate_order = list(candidate_rows)
        if base_order != candidate_order:
            base_written_order = [
                tuple(row[index] for index in key_indexes) for row in base_rows.values()
            ]
            candidate_written_order = [
                tuple(row[index] for index in candidate_key_indexes)
                for row in candidate_rows.values()
            ]
            differences.append(
                ResultDifference(
                    code="row.order_changed",
                    detail=f"row order changed: {base_written_order} -> {candidate_written_order}",
                )
            )

    candidate_aggregate_issues, candidate_aggregate_failed = _evaluate_aggregate_checks(
        candidate, tolerance.aggregate_checks, "candidate", candidate_key_indexes
    )
    proven_different = bool(differences) or candidate_aggregate_failed
    differences.extend(expected_nan_issues)
    differences.extend(candidate_aggregate_issues)
    if proven_different:
        outcome = ComparisonOutcome.DIFFERENT
    elif differences:
        outcome = ComparisonOutcome.INSUFFICIENT
    else:
        outcome = ComparisonOutcome.EQUIVALENT
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


def _key_component(value: str | None, logical_type: ColumnType) -> str | Decimal | None:
    if value is not None and logical_type in (ColumnType.INTEGER, ColumnType.DECIMAL):
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            return value
        if parsed.is_finite():
            return parsed
    return value


def _index_rows(
    result: ResultSet, key_indexes: list[int]
) -> dict[tuple[str | Decimal | None, ...], list[str | None]]:
    indexed: dict[tuple[str | Decimal | None, ...], list[str | None]] = {}
    for row in result.rows:
        key = tuple(_key_component(row[index], result.columns[index].type) for index in key_indexes)
        if key in indexed:
            raise _DuplicateKey(tuple(row[index] for index in key_indexes))
        indexed[key] = row
    return indexed


def _compare_membership(
    base_rows: dict[tuple[str | Decimal | None, ...], list[str | None]],
    candidate_rows: dict[tuple[str | Decimal | None, ...], list[str | None]],
    key_indexes: list[int],
    candidate_key_indexes: list[int],
) -> list[ResultDifference]:
    differences: list[ResultDifference] = []
    for key, row in base_rows.items():
        if key not in candidate_rows:
            written_key = tuple(row[index] for index in key_indexes)
            differences.append(
                ResultDifference(
                    code="row.missing", detail=f"row {_render_key(written_key)} is gone"
                )
            )
    for key, row in candidate_rows.items():
        if key not in base_rows:
            written_key = tuple(row[index] for index in candidate_key_indexes)
            differences.append(
                ResultDifference(code="row.added", detail=f"new row {_render_key(written_key)}")
            )
    return differences


def _compare_row(
    columns: list[ColumnSchema],
    key: tuple[str | None, ...],
    base_row: list[str | None],
    candidate_row: list[str | None],
    tolerance: ResultTolerance,
    candidate_indexes: list[int],
    key_indexes: list[int],
) -> list[ResultDifference]:
    differences: list[ResultDifference] = []
    location = _render_key(key)
    for index, column in enumerate(columns):
        if index in key_indexes:
            continue
        before, after = base_row[index], candidate_row[candidate_indexes[index]]
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
        equal = _values_equal(column, before, after, tolerance)
        if equal is None:
            differences.append(
                ResultDifference(
                    code="value.expected_nan",
                    detail=f"{location}.{column.name}: expected {before!r} is not a number",
                )
            )
        elif not equal:
            differences.append(
                ResultDifference(
                    code="value.changed",
                    detail=f"{location}.{column.name}: {before!r} -> {after!r}",
                )
            )
    return differences


def _values_equal(
    column: ColumnSchema, before: str, after: str, tolerance: ResultTolerance
) -> bool | None:
    if column.type is ColumnType.FLOAT:
        return _floats_equal(before, after, tolerance)
    if column.type in (ColumnType.DECIMAL, ColumnType.INTEGER):
        try:
            left = Decimal(before)
        except InvalidOperation:
            return before == after
        if left.is_nan():
            return None
        try:
            right = Decimal(after)
        except InvalidOperation:
            return before == after
        # A decimal compares as a decimal: "0.4120" and "0.412" are one number.
        return not right.is_nan() and left == right
    return before == after


def _floats_equal(before: str, after: str, tolerance: ResultTolerance) -> bool | None:
    try:
        left = float(before)
    except ValueError:
        return before == after
    if isnan(left):
        return None
    try:
        right = float(after)
    except ValueError:
        return before == after
    if not isfinite(left) or not isfinite(right):
        return left == right
    left_exact, right_exact = Fraction(left), Fraction(right)
    difference = abs(left_exact - right_exact)
    if tolerance.float_absolute is not None and difference <= Fraction(
        Decimal(tolerance.float_absolute)
    ):
        return True
    if tolerance.float_relative is not None:
        scale = max(abs(left_exact), abs(right_exact))
        if scale == 0:
            return difference == 0
        return difference <= Fraction(Decimal(tolerance.float_relative)) * scale
    return difference == 0


class _AggregateInputError(Exception):
    pass


def _evaluate_aggregate_checks(
    result: ResultSet,
    checks: list[AggregateCheck],
    side: Literal["base", "candidate"],
    key_indexes: list[int],
) -> tuple[list[ResultDifference], bool]:
    issues: list[ResultDifference] = []
    failed = False
    for check in checks:
        try:
            detail = _evaluate_aggregate_check(result, check, key_indexes)
        except _AggregateInputError as exc:
            issues.append(
                ResultDifference(
                    code=f"aggregate.{check.kind}.{side}_insufficient",
                    detail=f"{side} {check.kind}: {exc}",
                )
            )
            continue
        if detail is not None:
            failed = True
            issues.append(
                ResultDifference(
                    code=f"aggregate.{check.kind}.{side}_failed",
                    detail=f"{side} {check.kind}: {detail}",
                )
            )
    return issues, failed


def _evaluate_aggregate_check(
    result: ResultSet, check: AggregateCheck, key_indexes: list[int]
) -> str | None:
    _validate_aggregate_columns(result, check)
    if isinstance(check, PartitionConservationCheck):
        return _partition_conservation(result, check)
    if isinstance(check, ShareSumCheck):
        return _share_sum(result, check)
    if isinstance(check, RatioReconstructionCheck):
        return _ratio_reconstruction(result, check, key_indexes)
    return _additive_decomposition(result, check, key_indexes)


def _validate_aggregate_columns(result: ResultSet, check: AggregateCheck) -> None:
    numeric_names: list[str]
    if isinstance(check, PartitionConservationCheck):
        numeric_names = [check.value_column, check.total_column]
    elif isinstance(check, ShareSumCheck):
        numeric_names = [check.share_column]
    elif isinstance(check, RatioReconstructionCheck):
        numeric_names = [
            check.ratio_column,
            check.numerator_column,
            check.denominator_column,
        ]
    else:
        numeric_names = [check.total_column, *check.component_columns]

    group_by = (
        check.group_by if isinstance(check, (PartitionConservationCheck, ShareSumCheck)) else []
    )
    for name in [*group_by, *numeric_names]:
        if result.column_index(name) is None:
            raise _AggregateInputError(f"declared column {name!r} is not in the result schema")
    numeric_types = {ColumnType.INTEGER, ColumnType.DECIMAL, ColumnType.FLOAT}
    columns = {column.name: column for column in result.columns}
    for name in numeric_names:
        if columns[name].type not in numeric_types:
            raise _AggregateInputError(f"declared numeric column {name!r} is {columns[name].type}")


def _partition_conservation(result: ResultSet, check: PartitionConservationCheck) -> str | None:
    groups = _group_rows(result, check.group_by)
    if not groups:
        raise _AggregateInputError("an empty result has no declared total to conserve")
    value_index = _required_index(result, check.value_column)
    total_index = _required_index(result, check.total_column)
    group_indexes = [_required_index(result, name) for name in check.group_by]
    tolerance = Decimal(check.absolute_tolerance)
    for rows in groups.values():
        group = tuple(rows[0][index] for index in group_indexes) or ("all",)
        values = [_decimal_value(row[value_index], check.value_column) for row in rows]
        totals = {_decimal_value(row[total_index], check.total_column) for row in rows}
        if len(totals) != 1:
            rendered_totals = ", ".join(str(total) for total in sorted(totals))
            return f"group {_render_key(group)} declares conflicting totals [{rendered_totals}]"
        actual = sum((Fraction(value) for value in values), Fraction())
        expected = Fraction(next(iter(totals)))
        if abs(actual - expected) > Fraction(tolerance):
            return (
                f"group {_render_key(group)} sums to {_render_fraction(actual)}, "
                f"expected {_render_fraction(expected)}"
            )
    return None


def _share_sum(result: ResultSet, check: ShareSumCheck) -> str | None:
    groups = _group_rows(result, check.group_by)
    if not groups:
        raise _AggregateInputError("an empty result has no shares to sum")
    share_index = _required_index(result, check.share_column)
    group_indexes = [_required_index(result, name) for name in check.group_by]
    expected = Decimal(check.expected_sum)
    tolerance = Decimal(check.absolute_tolerance)
    for rows in groups.values():
        group = tuple(rows[0][index] for index in group_indexes) or ("all",)
        actual = sum(
            (Fraction(_decimal_value(row[share_index], check.share_column)) for row in rows),
            Fraction(),
        )
        if abs(actual - Fraction(expected)) > Fraction(tolerance):
            return (
                f"group {_render_key(group)} sums to {_render_fraction(actual)}, "
                f"expected {expected}"
            )
    return None


def _ratio_reconstruction(
    result: ResultSet, check: RatioReconstructionCheck, key_indexes: list[int]
) -> str | None:
    if not result.rows:
        raise _AggregateInputError("an empty result has no ratios to reconstruct")
    ratio_index = _required_index(result, check.ratio_column)
    numerator_index = _required_index(result, check.numerator_column)
    denominator_index = _required_index(result, check.denominator_column)
    factor = Decimal(check.factor)
    tolerance = Decimal(check.absolute_tolerance)
    for row in result.rows:
        ratio = _decimal_value(row[ratio_index], check.ratio_column)
        numerator = _decimal_value(row[numerator_index], check.numerator_column)
        denominator = _decimal_value(row[denominator_index], check.denominator_column)
        if denominator == 0:
            raise _AggregateInputError(
                f"row {_render_key(tuple(row[index] for index in key_indexes))} "
                "has a zero denominator"
            )
        expected = Fraction(numerator) / Fraction(denominator) * Fraction(factor)
        if abs(Fraction(ratio) - expected) > Fraction(tolerance):
            return (
                f"row {_render_key(tuple(row[index] for index in key_indexes))} has ratio "
                f"{ratio}, reconstructed {_render_fraction(expected)}"
            )
    return None


def _additive_decomposition(
    result: ResultSet, check: AdditiveDecompositionCheck, key_indexes: list[int]
) -> str | None:
    if not result.rows:
        raise _AggregateInputError("an empty result has no totals to decompose")
    total_index = _required_index(result, check.total_column)
    component_indexes = [(_required_index(result, name), name) for name in check.component_columns]
    tolerance = Decimal(check.absolute_tolerance)
    for row in result.rows:
        total = _decimal_value(row[total_index], check.total_column)
        reconstructed = sum(
            (Fraction(_decimal_value(row[index], name)) for index, name in component_indexes),
            Fraction(),
        )
        if abs(Fraction(total) - reconstructed) > Fraction(tolerance):
            return (
                f"row {_render_key(tuple(row[index] for index in key_indexes))} has total "
                f"{total}, components sum to {_render_fraction(reconstructed)}"
            )
    return None


def _group_rows(
    result: ResultSet, group_by: list[str]
) -> dict[tuple[str | Decimal | None, ...], list[list[str | None]]]:
    indexes = [_required_index(result, name) for name in group_by]
    groups: dict[tuple[str | Decimal | None, ...], list[list[str | None]]] = {}
    for row in result.rows:
        key = tuple(
            _key_component(row[index], result.columns[index].type) for index in indexes
        ) or ("all",)
        groups.setdefault(key, []).append(row)
    return groups


def _required_index(result: ResultSet, name: str) -> int:
    index = result.column_index(name)
    if index is None:  # validated before evaluation; keeps this helper total for type checking
        raise _AggregateInputError(f"declared column {name!r} is not in the result schema")
    return index


def _decimal_value(value: str | None, column: str) -> Decimal:
    if value is None:
        raise _AggregateInputError(f"numeric column {column!r} contains NULL")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise _AggregateInputError(
            f"numeric column {column!r} contains non-decimal value {value!r}"
        ) from None
    if not parsed.is_finite():
        raise _AggregateInputError(f"numeric column {column!r} contains non-finite value {value!r}")
    return parsed


def _render_fraction(value: Fraction) -> str:
    """Render a finite decimal exactly; retain an exact fraction for recurring values."""
    if value.denominator == 1:
        return str(value.numerator)

    denominator = value.denominator
    twos = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    fives = 0
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        return str(value)

    scale = max(twos, fives)
    scaled = abs(value.numerator) * 2 ** (scale - twos) * 5 ** (scale - fives)
    digits = str(scaled).rjust(scale + 1, "0")
    rendered = f"{digits[:-scale]}.{digits[-scale:]}"
    return f"-{rendered}" if value.numerator < 0 else rendered


def _render_key(key: tuple[str | None, ...]) -> str:
    return "|".join("NULL" if part is None else part for part in key)
