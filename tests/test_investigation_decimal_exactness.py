"""Exact investigation observations, independent of ambient decimal arithmetic."""

from __future__ import annotations

import decimal
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

import auto_bi.cli as cli
from auto_bi.investigation.claims import BriefCompilationError, compile_brief
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan, InvestigationSkill, InvestigationStep
from auto_bi.investigation.skills import (
    ConcentrationResult,
    ContributionResult,
    DataHealthResult,
    GapCode,
    InvestigationGap,
    PeriodVarianceResult,
    run_skill,
)
from auto_bi.verification.model import EXIT_ERROR

FIXTURES = Path(__file__).parent / "fixtures" / "investigation"
PLAN_FIXTURE = FIXTURES / "weekly_margin.yaml"
RESULT_FIXTURE = FIXTURES / "revenue_results.yaml"


def raw_result() -> dict[str, Any]:
    return deepcopy(yaml.safe_load(RESULT_FIXTURE.read_text(encoding="utf-8")))


def plan() -> InvestigationPlan:
    return InvestigationPlan.model_validate(
        yaml.safe_load(PLAN_FIXTURE.read_text(encoding="utf-8"))
    )


def step(skill: InvestigationSkill) -> InvestigationStep:
    return next(item for item in plan().steps if item.skill is skill)


def result(groups: list[tuple[str, str, str]]) -> CanonicalMetricResult:
    raw = raw_result()
    raw["rows"] = [
        {"period": period, "dimensions": {"category": name}, "value": values[index]}
        for index, period in enumerate(("comparison", "current"))
        for name, *values in groups
    ]
    return CanonicalMetricResult.model_validate(raw)


def variance(data: CanonicalMetricResult) -> PeriodVarianceResult:
    output = run_skill(step(InvestigationSkill.PERIOD_VARIANCE), data, plan().budget)
    assert isinstance(output, PeriodVarianceResult)
    return output


def contributions(data: CanonicalMetricResult) -> ContributionResult:
    output = run_skill(step(InvestigationSkill.CONTRIBUTION_DECOMPOSITION), data, plan().budget)
    assert isinstance(output, ContributionResult)
    return output


def concentration(data: CanonicalMetricResult) -> ConcentrationResult:
    output = run_skill(step(InvestigationSkill.CONCENTRATION_SHIFT), data, plan().budget)
    assert isinstance(output, ConcentrationResult)
    return output


def contribution_values(output: ContributionResult) -> list[tuple[str, str, str, str, str | None]]:
    return [
        (c.dimension_value, c.comparison_value, c.current_value, c.delta, c.share_of_total_delta)
        for c in output.contributions
    ]


def test_difference_of_one_between_29_digit_values() -> None:
    data = result([("A", "10000000000000000000000000000", "10000000000000000000000000001")])
    v = variance(data)
    assert (v.comparison_value, v.current_value, v.absolute_delta, v.relative_delta) == (
        "10000000000000000000000000000",
        "10000000000000000000000000001",
        "1",
        "0",
    )
    c = contributions(data)
    assert c.total_delta == "1"
    assert contribution_values(c) == [
        ("A", "10000000000000000000000000000", "10000000000000000000000000001", "1", "1")
    ]
    assert c.reconciled is True
    p = plan()
    brief = compile_brief(p, [run_skill(s, data, p.budget) for s in p.steps])
    claim = next(c for c in brief.claims if c.summary_code == "investigation.period_variance")
    assert claim.value == "1"
    blocks = {b.title: b.body for b in brief.blocks}
    assert blocks["Изменение периода"] == (
        "Наблюдаемое значение: 10000000000000000000000000000 RUB -> "
        "10000000000000000000000000001 RUB; delta 1 RUB; relative 0."
    )
    assert blocks["Декомпозиция наблюдаемой дельты"] == (
        "Общая наблюдаемая дельта: 1 RUB; reconciliation=pass.\n- A: 1 RUB; share 1"
    )


def test_small_group_beside_large_one() -> None:
    data = result(
        [
            ("A", "10000000000000000000000000000", "10000000000000000000000000000"),
            ("B", "1", "3"),
        ]
    )
    v = variance(data)
    assert (v.comparison_value, v.current_value, v.absolute_delta, v.relative_delta) == (
        "10000000000000000000000000001",
        "10000000000000000000000000003",
        "2",
        "0",
    )
    c = contributions(data)
    assert (c.comparison_value, c.current_value, c.total_delta) == (
        "10000000000000000000000000001",
        "10000000000000000000000000003",
        "2",
    )
    assert contribution_values(c) == [
        ("B", "1", "3", "2", "1"),
        ("A", "10000000000000000000000000000", "10000000000000000000000000000", "0", "0"),
    ]
    assert c.reconciled is True
    s = concentration(data)
    assert s.comparison.total == "10000000000000000000000000001"
    assert s.current.total == "10000000000000000000000000003"
    for snapshot, small_value in ((s.comparison, "1"), (s.current, "3")):
        assert [(m.dimension_value, m.value, m.share) for m in snapshot.leaders] == [
            ("A", "10000000000000000000000000000", "1"),
            ("B", small_value, "0"),
        ]
        assert (snapshot.hhi, snapshot.top_n_share) == ("1", "1")
    assert (s.hhi_delta, s.top_n_share_delta) == ("0", "0")


def test_fractional_digits_past_28th_significant_digit() -> None:
    data = result(
        [
            ("A", "0.1234567890123456789012345678", "0.1234567890123456789012345679"),
            ("B", "1000000", "1000000"),
        ]
    )
    v = variance(data)
    assert (v.comparison_value, v.current_value, v.absolute_delta, v.relative_delta) == (
        "1000000.1234567890123456789012345678",
        "1000000.1234567890123456789012345679",
        "0.0000000000000000000000000001",
        "0",
    )
    c = contributions(data)
    assert c.total_delta == "0.0000000000000000000000000001"
    assert contribution_values(c) == [
        (
            "A",
            "0.1234567890123456789012345678",
            "0.1234567890123456789012345679",
            "0.0000000000000000000000000001",
            "1",
        ),
        ("B", "1000000", "1000000", "0", "0"),
    ]
    assert c.reconciled is True


def test_row_order_does_not_change_observation() -> None:
    groups = [
        ("A", "10000000000000000000000000000", "10000000000000000000000000000"),
        ("B", "-10000000000000000000000000000", "-10000000000000000000000000000"),
        ("C", "5", "7"),
    ]
    outputs = []
    for ordered in (groups, [groups[0], groups[2], groups[1]]):
        data = result(ordered)
        v, c = variance(data), contributions(data)
        assert (v.comparison_value, v.current_value, v.absolute_delta, v.relative_delta) == (
            "5",
            "7",
            "2",
            "0.4",
        )
        assert (c.comparison_value, c.current_value, c.total_delta) == ("5", "7", "2")
        assert [(x.dimension_value, x.delta, x.share_of_total_delta) for x in c.contributions] == [
            ("C", "2", "1"),
            ("A", "0", "0"),
            ("B", "0", "0"),
        ]
        assert c.reconciled is True
        outputs.append((v, c))
    assert outputs[0] == outputs[1]
    assert [o.digest() for o in outputs[0]] == [o.digest() for o in outputs[1]]


def test_contributions_ranked_by_exact_change() -> None:
    c = contributions(
        result(
            [
                ("A", "0", "10000000000000000000000000001"),
                ("B", "0", "10000000000000000000000000002"),
            ]
        )
    )
    assert (c.current_value, c.total_delta) == (
        "20000000000000000000000000003",
        "20000000000000000000000000003",
    )
    assert contribution_values(c) == [
        ("B", "0", "10000000000000000000000000002", "10000000000000000000000000002", "0.5"),
        ("A", "0", "10000000000000000000000000001", "10000000000000000000000000001", "0.5"),
    ]
    assert c.reconciled is True


def test_leaders_ranked_by_exact_value() -> None:
    s = concentration(
        result(
            [
                ("A", "10000000000000000000000000001", "10000000000000000000000000003"),
                ("B", "10000000000000000000000000002", "10000000000000000000000000002"),
                ("C", "10000000000000000000000000003", "10000000000000000000000000001"),
            ]
        )
    )
    assert s.top_n == 2
    for snapshot, first in ((s.comparison, "C"), (s.current, "A")):
        assert snapshot.total == "30000000000000000000000000006"
        assert [(m.dimension_value, m.value, m.share) for m in snapshot.leaders] == [
            (first, "10000000000000000000000000003", "0.333333"),
            ("B", "10000000000000000000000000002", "0.333333"),
        ]
        assert (snapshot.hhi, snapshot.top_n_share) == ("0.333333", "0.666667")
    assert (s.hhi_delta, s.top_n_share_delta) == ("0", "0")


CONTEXTS = [
    (2, decimal.ROUND_HALF_EVEN, False),
    (6, decimal.ROUND_HALF_EVEN, False),
    (50, decimal.ROUND_UP, False),
    (28, decimal.ROUND_HALF_EVEN, True),
]


@pytest.mark.parametrize("skill", list(InvestigationSkill))
@pytest.mark.parametrize("small_group", [False, True])
@pytest.mark.parametrize(("precision", "rounding", "trap"), CONTEXTS)
def test_caller_context_unchanged(
    skill: InvestigationSkill, small_group: bool, precision: int, rounding: str, trap: bool
) -> None:
    data = (
        result(
            [
                ("A", "10000000000000000000000000000", "10000000000000000000000000000"),
                ("B", "1", "3"),
            ]
        )
        if small_group
        else CanonicalMetricResult.model_validate(raw_result())
    )
    p, s = plan(), step(skill)
    default = run_skill(s, data, p.budget)
    with decimal.localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        context.traps[decimal.Inexact] = trap
        traps = context.traps.copy()
        context.clear_flags()
        output = run_skill(s, data, p.budget)
        assert output == default
        assert output.digest() == default.digest()
        assert context.prec == precision
        assert context.rounding == rounding
        assert context.traps == traps
        assert not any(context.flags.values())


def test_trapped_inexact_does_not_end_ratio() -> None:
    data = result([("A", "3", "4")])
    default = variance(data)
    with decimal.localcontext() as context:
        context.traps[decimal.Inexact] = True
        precision, rounding, traps = context.prec, context.rounding, context.traps.copy()
        context.clear_flags()
        output = variance(data)
        assert (output.absolute_delta, output.relative_delta) == ("1", "0.333333")
        assert output == default
        assert output.digest() == default.digest()
        assert (context.prec, context.rounding, context.traps) == (precision, rounding, traps)
        assert not any(context.flags.values())


@pytest.mark.parametrize(
    ("comparison", "current", "expected", "rounding"),
    [
        ("2000000", "2000001", "0", decimal.ROUND_HALF_EVEN),
        ("2000000", "2000003", "0.000002", decimal.ROUND_HALF_EVEN),
        ("2000000", "2000005", "0.000002", decimal.ROUND_HALF_EVEN),
        ("2000000", "1999999", "0", decimal.ROUND_HALF_EVEN),
        ("2000000", "1999997", "-0.000002", decimal.ROUND_HALF_EVEN),
        ("20000000", "20000011", "0.000001", decimal.ROUND_HALF_EVEN),
        ("100", "101", "0.01", decimal.ROUND_HALF_EVEN),
        ("2000000", "2000001", "0", decimal.ROUND_UP),
    ],
)
def test_ratios_six_places_half_even(
    comparison: str, current: str, expected: str, rounding: str
) -> None:
    data = result([("A", comparison, current)])
    default = variance(data)
    with decimal.localcontext() as context:
        context.rounding = rounding
        precision, traps = context.prec, context.traps.copy()
        context.clear_flags()
        output = variance(data)
        assert output.relative_delta == expected
        assert output == default
        assert output.digest() == default.digest()
        assert (context.prec, context.rounding, context.traps) == (precision, rounding, traps)
        assert not any(context.flags.values())


@pytest.mark.parametrize("run", ["forward", "reverse", "small_group", "fractional"])
def test_long_values_answered(run: str) -> None:
    n = "9" * 5000
    if run == "forward":
        v = variance(result([("A", "7", n)]))
        assert v.current_value == n
        assert v.absolute_delta == "9" * 4999 + "2"
        assert v.relative_delta == "142857" * 833 + "13.142857"
    elif run == "reverse":
        v = variance(result([("A", n, "7")]))
        assert v.absolute_delta == "-" + "9" * 4999 + "2"
        assert v.relative_delta == "-1"
    elif run == "fractional":
        v = variance(result([("A", "0." + "0" * 4999 + "1", "1")]))
        assert v.absolute_delta == "0." + "9" * 5000
        assert v.relative_delta == n
    else:
        data = result([("A", n, n), ("B", "7", "8")])
        c = contributions(data)
        assert (c.comparison_value, c.current_value, c.total_delta) == (
            "1" + "0" * 4999 + "6",
            "1" + "0" * 4999 + "7",
            "1",
        )
        assert contribution_values(c) == [("B", "7", "8", "1", "1"), ("A", n, n, "0", "0")]
        assert c.reconciled is True
        s = concentration(data)
        for snapshot in (s.comparison, s.current):
            assert [(m.dimension_value, m.share) for m in snapshot.leaders] == [
                ("A", "1"),
                ("B", "0"),
            ]
            assert snapshot.hhi == "1"


def invoke(tmp_path: Path, raw: dict[str, Any], raw_plan: dict[str, Any]) -> tuple[int, Path]:
    result_path, plan_path = tmp_path / "result.yaml", tmp_path / "plan.yaml"
    result_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    plan_path.write_text(
        yaml.safe_dump(raw_plan, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    output = tmp_path / "out"
    code = cli.main(
        [
            "decision",
            "investigate",
            str(plan_path),
            "--results",
            str(result_path),
            "--output",
            str(output),
        ]
    )
    return code, output


def test_cli_writes_artifacts_for_long_value(tmp_path: Path) -> None:
    raw = raw_result()
    next(r for r in raw["rows"] if r["period"] == "current" and r["dimensions"]["category"] == "A")[
        "value"
    ] = ("9" * 5000)
    code, output = invoke(tmp_path, raw, plan().model_dump(mode="json"))
    assert code == 0
    assert {p.name for p in output.iterdir()} == {
        "plan.json",
        "results.json",
        "brief.json",
        "brief.md",
    }


FRESHNESS_CONTEXTS = [
    (28, decimal.ROUND_HALF_EVEN, False),
    (2, decimal.ROUND_HALF_EVEN, False),
    (6, decimal.ROUND_HALF_EVEN, False),
    (6, decimal.ROUND_UP, False),
    (28, decimal.ROUND_HALF_EVEN, True),
]


@pytest.mark.parametrize(("precision", "rounding", "trap"), FRESHNESS_CONTEXTS)
@pytest.mark.parametrize(
    ("observed", "source", "status", "age"),
    [
        ("2026-09-04T12:00:00.000001Z", "2026-09-04T10:00:00Z", "stale", "7200.000001"),
        ("2026-09-04T11:59:59.999999Z", "2026-09-04T10:00:00Z", "pass", "7199.999999"),
        ("2026-09-04T12:00:00Z", "2026-09-04T10:00:00Z", "pass", "7200"),
        ("2026-09-04T12:00:00Z", "2026-09-04T12:00:00.000001Z", "future", "-0.000001"),
    ],
)
def test_freshness_age_exact(
    precision: int, rounding: str, trap: bool, observed: str, source: str, status: str, age: str
) -> None:
    raw = raw_result()
    raw.update(observed_at=observed, source_max_event_time=source, freshness_max_age="PT2H")
    data = CanonicalMetricResult.model_validate(raw)
    p, s = plan(), step(InvestigationSkill.DATA_HEALTH)
    default = run_skill(s, data, p.budget)
    with decimal.localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        context.traps[decimal.Inexact] = trap
        traps = context.traps.copy()
        context.clear_flags()
        output = run_skill(s, data, p.budget)
        assert isinstance(output, DataHealthResult)
        assert (output.freshness, output.freshness_age_seconds) == (status, age)
        assert output == default
        assert output.digest() == default.digest()
        assert (context.prec, context.rounding, context.traps) == (precision, rounding, traps)
        assert not any(context.flags.values())


@pytest.mark.parametrize(
    ("window", "status", "age"),
    [
        ("PT" + "0" * 5000 + "6H", "pass", "7200"),
        ("PT" + "0" * 5000 + "2H", "pass", "7200"),
        ("PT" + "0" * 5000 + "1H", "stale", "7200"),
        ("PT" + "9" * 5000 + "S", "pass", "7200"),
        ("P1W2DT3H4M5S", "pass", "7200"),
        ("P1M", "unknown", None),
        ("P1Y2M", "unknown", None),
        ("P0Y1D", "unknown", None),
    ],
    ids=[
        "long-6h",
        "long-2h",
        "long-1h",
        "long-seconds",
        "fixed",
        "month",
        "year-month",
        "zero-year",
    ],
)
def test_freshness_window_any_length(
    window: str, status: str, age: str | None, tmp_path: Path
) -> None:
    raw = raw_result()
    raw.update(
        observed_at="2026-09-04T12:00:00Z",
        source_max_event_time="2026-09-04T10:00:00Z",
        freshness_max_age=window,
    )
    output = run_skill(
        step(InvestigationSkill.DATA_HEALTH),
        CanonicalMetricResult.model_validate(raw),
        plan().budget,
    )
    assert isinstance(output, DataHealthResult)
    assert (output.freshness, output.freshness_age_seconds) == (status, age)
    if window == "PT" + "0" * 5000 + "6H":
        code, directory = invoke(tmp_path, raw, plan().model_dump(mode="json"))
        assert code == 0
        assert {p.name for p in directory.iterdir()} == {
            "plan.json",
            "results.json",
            "brief.json",
            "brief.md",
        }


def test_ordinary_values_unchanged_at_new_version() -> None:
    data = CanonicalMetricResult.model_validate(raw_result())
    v, c, s = variance(data), contributions(data), concentration(data)
    assert (v.comparison_value, v.current_value, v.absolute_delta, v.relative_delta) == (
        "200",
        "180",
        "-20",
        "-0.1",
    )
    assert [(x.dimension_value, x.delta, x.share_of_total_delta) for x in c.contributions] == [
        ("A", "-30", "1.5"),
        ("B", "10", "-0.5"),
        ("C", "0", "0"),
    ]
    assert c.reconciled is True
    assert (s.comparison.hhi, s.current.hhi) == ("0.375", "0.339506")
    assert (s.comparison.top_n_share, s.current.top_n_share) == ("0.75", "0.722222")
    assert (s.hhi_delta, s.top_n_share_delta) == ("-0.035494", "-0.027778")
    assert [o.skill_version for o in (v, c, s)] == [2, 2, 2]
    health = run_skill(step(InvestigationSkill.DATA_HEALTH), data, plan().budget)
    assert health.skill_version == 1
    assert (
        health.digest() == "sha256:80a0370edd1278f29b5a2aa34e07134bcaf067cdb07deb16d9778da1887f2bf5"
    )
    equal = result([("A", "5", "5")])
    v = variance(equal)
    assert (v.absolute_delta, v.relative_delta) == ("0", "0")
    assert contributions(equal).contributions[0].share_of_total_delta is None


@pytest.mark.parametrize(
    ("skill", "wrong_version", "message"),
    [
        ("period_variance", 1, "skill_version for period_variance must be 2"),
        ("contribution_decomposition", 1, "skill_version for contribution_decomposition must be 2"),
        ("concentration_shift", 1, "skill_version for concentration_shift must be 2"),
        ("data_health", 2, "skill_version for data_health must be 1"),
    ],
)
def test_plan_declares_skill_versions(skill: str, wrong_version: int, message: str) -> None:
    raw = yaml.safe_load(PLAN_FIXTURE.read_text(encoding="utf-8"))
    declared = [
        (s.skill.value, s.skill_version) for s in InvestigationPlan.model_validate(raw).steps
    ]
    assert declared == [
        ("data_health", 1),
        ("period_variance", 2),
        ("contribution_decomposition", 2),
        ("concentration_shift", 2),
    ]
    next(s for s in raw["steps"] if s["skill"] == skill)["skill_version"] = wrong_version
    with pytest.raises(ValidationError, match=message):
        InvestigationPlan.model_validate(raw)
    dimensional = plan().model_dump(mode="json")
    next(s for s in dimensional["steps"] if s["skill"] == "period_variance")["dimensions"] = [
        "category"
    ]
    with pytest.raises(ValidationError) as error:
        InvestigationPlan.model_validate(dimensional)
    assert "period_variance does not accept dimensions" in str(error.value)
    assert "in v1" not in str(error.value)


@pytest.mark.parametrize("complete", [True, False])
def test_output_and_gap_versions(complete: bool) -> None:
    raw = raw_result()
    raw["complete"] = complete
    data, p = CanonicalMetricResult.model_validate(raw), plan()
    outputs = [run_skill(s, data, p.budget) for s in p.steps]
    assert [o.skill_version for o in outputs] == [1, 2, 2, 2]
    if not complete:
        for output in outputs[1:]:
            assert isinstance(output, InvestigationGap)
            assert output.code == GapCode.INPUT_INCOMPLETE
            assert output.skill_version == 2


def test_brief_refuses_wrong_output_version() -> None:
    data, p = CanonicalMetricResult.model_validate(raw_result()), plan()
    outputs = [run_skill(s, data, p.budget) for s in p.steps]
    outputs[1] = InvestigationGap(
        step_id="variance",
        skill=InvestigationSkill.PERIOD_VARIANCE,
        skill_version=1,
        metric_ref=data.metric_ref,
        code=GapCode.INPUT_INCOMPLETE,
        detail="canonical result is marked incomplete",
    )
    with pytest.raises(
        BriefCompilationError, match=r"^output for step variance has skill_version 1, expected 2$"
    ):
        compile_brief(p, outputs)


def test_cli_refuses_numeric_version_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = yaml.safe_load(PLAN_FIXTURE.read_text(encoding="utf-8"))
    for s in raw["steps"]:
        s["skill_version"] = 1
    code, directory = invoke(tmp_path, raw_result(), raw)
    assert code == EXIT_ERROR
    assert not directory.exists()
    assert "skill_version for period_variance must be 2" in capsys.readouterr().err
