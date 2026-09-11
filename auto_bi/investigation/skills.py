"""Four deterministic v1 investigation skills over canonical metric results."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from fractions import Fraction
from typing import Literal

from pydantic import Field

from auto_bi.evidence.canonical import canonical_bytes, sha256_digest
from auto_bi.investigation.data import (
    CanonicalMetricResult,
    InvestigationPeriod,
    MetricResultRow,
)
from auto_bi.investigation.model import (
    InvestigationBudget,
    InvestigationSkill,
    InvestigationStep,
)
from auto_bi.refs import MetricRef
from auto_bi.scalars import DecimalString, NonBlankText, Slug
from auto_bi.semantic.model import Additivity
from auto_bi.strict import StrictModel

RATIO_SCALE: Literal[6] = 6
_RATIO_QUANTUM = Decimal(1).scaleb(-RATIO_SCALE)
_FIXED_DURATION_RE = re.compile(
    r"^P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


class GapCode(StrEnum):
    BUDGET_INPUT_ROWS_EXCEEDED = "budget.input_rows_exceeded"
    BUDGET_GROUPS_EXCEEDED = "budget.groups_exceeded"
    SCOPE_METRIC_MISMATCH = "scope.metric_mismatch"
    SCOPE_UNSUPPORTED_DIMENSION = "scope.unsupported_dimension"
    INPUT_INCOMPLETE = "input.incomplete"
    INPUT_MISSING_PERIOD = "input.missing_period"
    INPUT_NULL_VALUE = "input.null_value"
    INPUT_NULL_DIMENSION = "input.null_dimension"
    INPUT_DUPLICATE_GRAIN = "input.duplicate_grain"
    METHOD_WRONG_SKILL = "method.wrong_skill"
    METHOD_NON_ADDITIVE_METRIC = "method.non_additive_metric"
    METHOD_NEGATIVE_VALUE = "method.negative_value"
    METHOD_NON_POSITIVE_TOTAL = "method.non_positive_total"


class FreshnessStatus(StrEnum):
    PASS = "pass"
    STALE = "stale"
    UNKNOWN = "unknown"
    FUTURE = "future"


class _CanonicalSkillOutput(StrictModel):
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.model_dump(mode="json"))

    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


class InvestigationGap(_CanonicalSkillOutput):
    kind: Literal["gap"] = "gap"
    step_id: Slug
    skill: InvestigationSkill
    skill_version: Literal[1] = 1
    metric_ref: MetricRef
    code: GapCode
    detail: NonBlankText


class DataHealthResult(_CanonicalSkillOutput):
    kind: Literal["data_health_result"] = "data_health_result"
    step_id: Slug
    skill: Literal[InvestigationSkill.DATA_HEALTH] = InvestigationSkill.DATA_HEALTH
    skill_version: Literal[1] = 1
    metric_ref: MetricRef
    complete: bool
    current_rows: int = Field(ge=0)
    comparison_rows: int = Field(ge=0)
    null_values: int = Field(ge=0)
    duplicate_grains: int = Field(ge=0)
    freshness: FreshnessStatus
    freshness_age_seconds: DecimalString | None = None


class PeriodVarianceResult(_CanonicalSkillOutput):
    kind: Literal["period_variance_result"] = "period_variance_result"
    step_id: Slug
    skill: Literal[InvestigationSkill.PERIOD_VARIANCE] = InvestigationSkill.PERIOD_VARIANCE
    skill_version: Literal[1] = 1
    metric_ref: MetricRef
    unit: str = ""
    comparison_value: DecimalString
    current_value: DecimalString
    absolute_delta: DecimalString
    relative_delta: DecimalString | None
    ratio_scale: Literal[6] = RATIO_SCALE


class Contribution(StrictModel):
    dimension_value: NonBlankText
    comparison_value: DecimalString
    current_value: DecimalString
    delta: DecimalString
    share_of_total_delta: DecimalString | None


class ContributionResult(_CanonicalSkillOutput):
    kind: Literal["contribution_result"] = "contribution_result"
    step_id: Slug
    skill: Literal[InvestigationSkill.CONTRIBUTION_DECOMPOSITION] = (
        InvestigationSkill.CONTRIBUTION_DECOMPOSITION
    )
    skill_version: Literal[1] = 1
    metric_ref: MetricRef
    unit: str = ""
    dimension: NonBlankText
    comparison_value: DecimalString
    current_value: DecimalString
    total_delta: DecimalString
    contributions: list[Contribution]
    reconciled: bool
    ratio_scale: Literal[6] = RATIO_SCALE


class ConcentrationMember(StrictModel):
    dimension_value: NonBlankText
    value: DecimalString
    share: DecimalString


class ConcentrationSnapshot(StrictModel):
    total: DecimalString
    hhi: DecimalString
    top_n_share: DecimalString
    leaders: list[ConcentrationMember]


class ConcentrationResult(_CanonicalSkillOutput):
    kind: Literal["concentration_result"] = "concentration_result"
    step_id: Slug
    skill: Literal[InvestigationSkill.CONCENTRATION_SHIFT] = InvestigationSkill.CONCENTRATION_SHIFT
    skill_version: Literal[1] = 1
    metric_ref: MetricRef
    unit: str = ""
    dimension: NonBlankText
    top_n: int = Field(ge=1)
    comparison: ConcentrationSnapshot
    current: ConcentrationSnapshot
    hhi_delta: DecimalString
    top_n_share_delta: DecimalString
    ratio_scale: Literal[6] = RATIO_SCALE


type SkillResult = (
    DataHealthResult
    | PeriodVarianceResult
    | ContributionResult
    | ConcentrationResult
    | InvestigationGap
)


def data_health(
    step: InvestigationStep,
    data: CanonicalMetricResult,
    budget: InvestigationBudget,
) -> DataHealthResult | InvestigationGap:
    gap = _boundary_gap(step, data, budget, InvestigationSkill.DATA_HEALTH)
    if gap is not None:
        return gap

    freshness, freshness_age = _freshness(data)
    return DataHealthResult(
        step_id=step.step_id,
        metric_ref=data.metric_ref,
        complete=data.complete,
        current_rows=_period_count(data.rows, InvestigationPeriod.CURRENT),
        comparison_rows=_period_count(data.rows, InvestigationPeriod.COMPARISON),
        null_values=sum(row.value is None for row in data.rows),
        duplicate_grains=_duplicate_grains(data),
        freshness=freshness,
        freshness_age_seconds=freshness_age,
    )


def period_variance(
    step: InvestigationStep,
    data: CanonicalMetricResult,
    budget: InvestigationBudget,
) -> PeriodVarianceResult | InvestigationGap:
    gap = _analytic_gap(step, data, budget, InvestigationSkill.PERIOD_VARIANCE)
    if gap is not None:
        return gap

    by_period = _values_by_period(data)
    if data.additivity is not Additivity.ADDITIVE and any(
        len(values) != 1 for values in by_period.values()
    ):
        return _gap(
            step,
            data,
            GapCode.METHOD_NON_ADDITIVE_METRIC,
            "period_variance cannot sum multiple rows of a non-additive metric",
        )

    comparison = sum(by_period[InvestigationPeriod.COMPARISON], Decimal())
    current = sum(by_period[InvestigationPeriod.CURRENT], Decimal())
    delta = current - comparison
    relative = None if comparison == 0 else _fraction_text(Fraction(delta) / Fraction(comparison))
    return PeriodVarianceResult(
        step_id=step.step_id,
        metric_ref=data.metric_ref,
        unit=data.unit,
        comparison_value=_decimal_text(comparison),
        current_value=_decimal_text(current),
        absolute_delta=_decimal_text(delta),
        relative_delta=relative,
    )


def contribution_decomposition(
    step: InvestigationStep,
    data: CanonicalMetricResult,
    budget: InvestigationBudget,
) -> ContributionResult | InvestigationGap:
    gap = _analytic_gap(step, data, budget, InvestigationSkill.CONTRIBUTION_DECOMPOSITION)
    if gap is not None:
        return gap
    if data.additivity is not Additivity.ADDITIVE:
        return _gap(
            step,
            data,
            GapCode.METHOD_NON_ADDITIVE_METRIC,
            f"metric {data.metric_ref} is {data.additivity.value}, not additive",
        )

    dimension = step.dimensions[0]
    grouped, group_gap = _grouped_values(step, data, budget, dimension)
    if group_gap is not None:
        return group_gap

    comparison_groups = grouped[InvestigationPeriod.COMPARISON]
    current_groups = grouped[InvestigationPeriod.CURRENT]
    names = sorted(set(comparison_groups) | set(current_groups))
    total_comparison = sum(comparison_groups.values(), Decimal())
    total_current = sum(current_groups.values(), Decimal())
    total_delta = total_current - total_comparison

    entries: list[tuple[str, Decimal, Decimal, Decimal]] = []
    for name in names:
        comparison = comparison_groups.get(name, Decimal())
        current = current_groups.get(name, Decimal())
        entries.append((name, comparison, current, current - comparison))
    entries.sort(key=lambda item: (-abs(item[3]), item[0]))

    contributions = [
        Contribution(
            dimension_value=name,
            comparison_value=_decimal_text(comparison),
            current_value=_decimal_text(current),
            delta=_decimal_text(delta),
            share_of_total_delta=(
                None
                if total_delta == 0
                else _fraction_text(Fraction(delta) / Fraction(total_delta))
            ),
        )
        for name, comparison, current, delta in entries
    ]
    reconstructed = sum((Decimal(item.delta) for item in contributions), Decimal())
    return ContributionResult(
        step_id=step.step_id,
        metric_ref=data.metric_ref,
        unit=data.unit,
        dimension=dimension,
        comparison_value=_decimal_text(total_comparison),
        current_value=_decimal_text(total_current),
        total_delta=_decimal_text(total_delta),
        contributions=contributions,
        reconciled=reconstructed == total_delta,
    )


def concentration_shift(
    step: InvestigationStep,
    data: CanonicalMetricResult,
    budget: InvestigationBudget,
) -> ConcentrationResult | InvestigationGap:
    gap = _analytic_gap(step, data, budget, InvestigationSkill.CONCENTRATION_SHIFT)
    if gap is not None:
        return gap
    if data.additivity is not Additivity.ADDITIVE:
        return _gap(
            step,
            data,
            GapCode.METHOD_NON_ADDITIVE_METRIC,
            f"metric {data.metric_ref} is {data.additivity.value}, not additive",
        )

    dimension = step.dimensions[0]
    grouped, group_gap = _grouped_values(step, data, budget, dimension)
    if group_gap is not None:
        return group_gap
    if any(value < 0 for values in grouped.values() for value in values.values()):
        return _gap(
            step,
            data,
            GapCode.METHOD_NEGATIVE_VALUE,
            "concentration requires non-negative additive values",
        )

    top_n = step.top_n
    if top_n is None:  # rejected by InvestigationStep; keeps this function total for typing
        return _gap(
            step,
            data,
            GapCode.METHOD_WRONG_SKILL,
            "concentration_shift step has no top_n",
        )

    comparison, comparison_hhi, comparison_top = _concentration_snapshot(
        grouped[InvestigationPeriod.COMPARISON], top_n
    )
    if comparison is None:
        return _gap(
            step,
            data,
            GapCode.METHOD_NON_POSITIVE_TOTAL,
            "comparison period has no positive total for concentration",
        )
    current, current_hhi, current_top = _concentration_snapshot(
        grouped[InvestigationPeriod.CURRENT], top_n
    )
    if current is None:
        return _gap(
            step,
            data,
            GapCode.METHOD_NON_POSITIVE_TOTAL,
            "current period has no positive total for concentration",
        )

    return ConcentrationResult(
        step_id=step.step_id,
        metric_ref=data.metric_ref,
        unit=data.unit,
        dimension=dimension,
        top_n=top_n,
        comparison=comparison,
        current=current,
        hhi_delta=_fraction_text(current_hhi - comparison_hhi),
        top_n_share_delta=_fraction_text(current_top - comparison_top),
    )


def run_skill(
    step: InvestigationStep,
    data: CanonicalMetricResult,
    budget: InvestigationBudget,
) -> SkillResult:
    dispatch = {
        InvestigationSkill.DATA_HEALTH: data_health,
        InvestigationSkill.PERIOD_VARIANCE: period_variance,
        InvestigationSkill.CONTRIBUTION_DECOMPOSITION: contribution_decomposition,
        InvestigationSkill.CONCENTRATION_SHIFT: concentration_shift,
    }
    return dispatch[step.skill](step, data, budget)


def _boundary_gap(
    step: InvestigationStep,
    data: CanonicalMetricResult,
    budget: InvestigationBudget,
    expected_skill: InvestigationSkill,
) -> InvestigationGap | None:
    if step.skill is not expected_skill:
        return _gap(
            step,
            data,
            GapCode.METHOD_WRONG_SKILL,
            f"{expected_skill.value} received a {step.skill.value} step",
        )
    if data.metric_ref not in step.metrics:
        return _gap(
            step,
            data,
            GapCode.SCOPE_METRIC_MISMATCH,
            f"metric {data.metric_ref} is outside step {step.step_id} scope",
        )
    unsupported = sorted(set(step.dimensions) - set(data.dimensions))
    if unsupported:
        return _gap(
            step,
            data,
            GapCode.SCOPE_UNSUPPORTED_DIMENSION,
            f"dimensions are absent from canonical result: {', '.join(unsupported)}",
        )
    if len(data.rows) > budget.max_input_rows:
        return _gap(
            step,
            data,
            GapCode.BUDGET_INPUT_ROWS_EXCEEDED,
            f"input has {len(data.rows)} rows; budget allows {budget.max_input_rows}",
        )
    return None


def _analytic_gap(
    step: InvestigationStep,
    data: CanonicalMetricResult,
    budget: InvestigationBudget,
    expected_skill: InvestigationSkill,
) -> InvestigationGap | None:
    gap = _boundary_gap(step, data, budget, expected_skill)
    if gap is not None:
        return gap
    if not data.complete:
        return _gap(
            step,
            data,
            GapCode.INPUT_INCOMPLETE,
            "canonical result is marked incomplete",
        )
    if any(row.value is None for row in data.rows):
        return _gap(
            step,
            data,
            GapCode.INPUT_NULL_VALUE,
            "canonical result contains NULL metric values",
        )
    duplicates = _duplicate_grains(data)
    if duplicates:
        return _gap(
            step,
            data,
            GapCode.INPUT_DUPLICATE_GRAIN,
            f"canonical result contains {duplicates} duplicate grains",
        )
    periods = {row.period for row in data.rows}
    missing = [period.value for period in InvestigationPeriod if period not in periods]
    if missing:
        return _gap(
            step,
            data,
            GapCode.INPUT_MISSING_PERIOD,
            f"canonical result is missing periods: {', '.join(missing)}",
        )
    return None


def _gap(
    step: InvestigationStep,
    data: CanonicalMetricResult,
    code: GapCode,
    detail: str,
) -> InvestigationGap:
    return InvestigationGap(
        step_id=step.step_id,
        skill=step.skill,
        metric_ref=data.metric_ref,
        code=code,
        detail=detail,
    )


def _period_count(rows: list[MetricResultRow], period: InvestigationPeriod) -> int:
    return sum(row.period is period for row in rows)


def _grain_key(data: CanonicalMetricResult, row: MetricResultRow) -> tuple[object, ...]:
    return (row.period, *(row.dimensions[name] for name in data.dimensions))


def _duplicate_grains(data: CanonicalMetricResult) -> int:
    counts = Counter(_grain_key(data, row) for row in data.rows)
    return sum(count - 1 for count in counts.values() if count > 1)


def _values_by_period(
    data: CanonicalMetricResult,
) -> dict[InvestigationPeriod, list[Decimal]]:
    values: dict[InvestigationPeriod, list[Decimal]] = {
        period: [] for period in InvestigationPeriod
    }
    for row in data.rows:
        if row.value is not None:
            values[row.period].append(Decimal(row.value))
    return values


def _grouped_values(
    step: InvestigationStep,
    data: CanonicalMetricResult,
    budget: InvestigationBudget,
    dimension: str,
) -> tuple[
    dict[InvestigationPeriod, dict[str, Decimal]],
    InvestigationGap | None,
]:
    grouped: dict[InvestigationPeriod, dict[str, Decimal]] = {
        period: {} for period in InvestigationPeriod
    }
    for row in data.rows:
        dimension_value = row.dimensions[dimension]
        if dimension_value is None:
            return grouped, _gap(
                step,
                data,
                GapCode.INPUT_NULL_DIMENSION,
                f"dimension {dimension!r} contains NULL",
            )
        if row.value is None:  # rejected by _analytic_gap; keeps this helper total
            continue
        period_groups = grouped[row.period]
        period_groups[dimension_value] = period_groups.get(dimension_value, Decimal()) + Decimal(
            row.value
        )

    groups = set().union(*(set(values) for values in grouped.values()))
    if len(groups) > budget.max_groups_per_step:
        return grouped, _gap(
            step,
            data,
            GapCode.BUDGET_GROUPS_EXCEEDED,
            f"result has {len(groups)} groups; budget allows {budget.max_groups_per_step}",
        )
    return grouped, None


def _concentration_snapshot(
    values: dict[str, Decimal], top_n: int
) -> tuple[ConcentrationSnapshot | None, Fraction, Fraction]:
    total = sum(values.values(), Decimal())
    if total <= 0:
        return None, Fraction(), Fraction()

    ranked = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    total_fraction = Fraction(total)
    shares = [(name, value, Fraction(value) / total_fraction) for name, value in ranked]
    hhi = sum((share * share for _, _, share in shares), Fraction())
    top_share = sum((share for _, _, share in shares[:top_n]), Fraction())
    snapshot = ConcentrationSnapshot(
        total=_decimal_text(total),
        hhi=_fraction_text(hhi),
        top_n_share=_fraction_text(top_share),
        leaders=[
            ConcentrationMember(
                dimension_value=name,
                value=_decimal_text(value),
                share=_fraction_text(share),
            )
            for name, value, share in shares[:top_n]
        ],
    )
    return snapshot, hhi, top_share


def _freshness(data: CanonicalMetricResult) -> tuple[FreshnessStatus, str | None]:
    if data.source_max_event_time is None or data.freshness_max_age is None:
        return FreshnessStatus.UNKNOWN, None
    max_age_seconds = _fixed_duration_seconds(data.freshness_max_age)
    if max_age_seconds is None:
        return FreshnessStatus.UNKNOWN, None

    observed = _timestamp(data.observed_at)
    source = _timestamp(data.source_max_event_time)
    delta = observed - source
    microseconds = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    age = Decimal(microseconds) / Decimal(1_000_000)
    if age < 0:
        return FreshnessStatus.FUTURE, _decimal_text(age)
    status = FreshnessStatus.PASS if age <= max_age_seconds else FreshnessStatus.STALE
    return status, _decimal_text(age)


def _fixed_duration_seconds(value: str) -> Decimal | None:
    match = _FIXED_DURATION_RE.match(value)
    if match is None or not any(match.groupdict().values()):
        return None
    parts = {name: int(raw or 0) for name, raw in match.groupdict().items()}
    return Decimal(
        parts["weeks"] * 7 * 86_400
        + parts["days"] * 86_400
        + parts["hours"] * 3_600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _fraction_text(value: Fraction) -> str:
    digits = max(len(str(abs(value.numerator))), len(str(abs(value.denominator))))
    with localcontext() as context:
        context.prec = max(32, digits * 2 + RATIO_SCALE + 2)
        decimal = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
            _RATIO_QUANTUM,
            rounding=ROUND_HALF_EVEN,
        )
    return _decimal_text(decimal)


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered
