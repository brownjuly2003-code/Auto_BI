"""Physical success evidence is limited to comparisons the engine actually answered."""

from pathlib import Path

import pytest

from auto_bi.semantic.model import Column, ColumnRole
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    LayoutClaim,
    LayoutFacts,
    check_physical,
)
from auto_bi.verification.release_gate import CheckStatus, _physical_clean
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _write_yaml,
    environment,
    metric_contract,
    product_contract,
    semantic_model,
)
from tests.test_environment_question_identity import facts, questions, reading


@pytest.mark.parametrize(
    "coverage,columns,layouts",
    [("none", 0, 0), ("engine_only", 4, 1), ("complete", 4, 3), ("hidden", 3, 3)],
)
def test_public_physical_detail_reports_only_observed_comparisons(
    tmp_path: Path, coverage: str, columns: int, layouts: int
) -> None:
    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        observed = environment()
        question = scope.tables[0]
        assert len(question.columns) == 4
        table = observed.tables[0]
        table.columns = dict(question.columns)
        table.layout = LayoutFacts(
            table_engine=question.layout.table_engine,
            sorting_key=question.layout.sorting_key,
            partition_key=question.layout.partition_key,
        )
        if coverage == "none":
            table.columns = None
            table.layout = LayoutFacts()
        elif coverage == "engine_only":
            table.layout = LayoutFacts(table_engine=question.layout.table_engine)
        elif coverage == "hidden":
            del table.columns["revenue"]
            table.column_reads = None
        return observed

    report = Case(tmp_path).run(probe=probe)
    check = next(check for check in report.checks if check.check_id == "dwh.physical")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "physical.as_declared"
    assert _check_payload(tmp_path, report, "dwh.physical")["findings"] == []
    assert f"{columns} of 4 declared column types" in check.detail
    assert f"{layouts} of 3 declared layout fields" in check.detail
    if coverage == "complete":
        assert "coverage is complete" in check.detail
    else:
        assert "coverage is incomplete" in check.detail
    if coverage == "none":
        assert "no physical comparison was made" in check.detail
    assert "every column" not in check.detail
    assert "the layout matches" not in check.detail


@pytest.mark.parametrize("unusable", ["unreadable", "absent", "unattributed", "duplicate"])
@pytest.mark.parametrize("reverse", [False, True])
def test_coverage_counts_each_clock_and_excludes_unusable_readings(
    unusable: str, reverse: bool
) -> None:
    scope = questions()
    for question in scope.tables:
        question.layout = LayoutClaim(table_engine="MergeTree", sorting_key=["id"])
    tables = [
        reading(clock, layout=LayoutFacts(table_engine="MergeTree", sorting_key=["id"]))
        for clock in ("booked_at", "shipped_at")
    ]
    if unusable == "unreadable":
        tables[1].readable = False
    elif unusable == "absent":
        tables[1].exists = False
    elif unusable == "unattributed":
        tables[1].event_time_column = None
    else:
        tables.append(tables[1].model_copy(deep=True))
    observed = facts(*(tables[::-1] if reverse else tables))
    if reverse:
        scope.tables.reverse()
    assert check_physical(scope, observed) == []
    detail = _physical_clean(scope, observed)
    assert "2 of 4 declared column types" in detail
    assert "2 of 4 declared layout fields" in detail
    assert "coverage is incomplete" in detail


def test_unclaimed_layout_and_unasked_columns_do_not_add_comparisons() -> None:
    scope = questions("booked_at")
    table = reading("booked_at", layout=LayoutFacts(table_engine="MergeTree"))
    assert len(table.columns) > len(scope.tables[0].columns)
    detail = _physical_clean(scope, facts(table))
    assert "2 of 2 declared column types" in detail
    assert "0 of 0 declared layout fields" in detail
    assert "coverage is complete" in detail


def test_empty_reported_layout_value_is_compared_when_declared() -> None:
    scope = questions("booked_at")
    scope.tables[0].layout = LayoutClaim(sorting_key=["id"])
    observed = facts(reading("booked_at", layout=LayoutFacts(sorting_key=[])))
    assert [item.code for item in check_physical(scope, observed)] == ["physical.layout_drift"]
    assert "1 of 1 declared layout fields" in _physical_clean(scope, observed)


@pytest.mark.parametrize("field", ["sorting_key", "distribution_key"])
def test_empty_joined_layout_claim_is_not_a_comparison(field: str) -> None:
    scope = questions("booked_at")
    setattr(scope.tables[0].layout, field, [""])
    layout = LayoutFacts()
    setattr(layout, field, ["observed"])
    observed = facts(reading("booked_at", layout=layout))
    assert check_physical(scope, observed) == []
    detail = _physical_clean(scope, observed)
    assert "0 of 0 declared layout fields" in detail


def test_no_questions_does_not_claim_a_physical_comparison() -> None:
    detail = _physical_clean(EnvironmentQuestions(), facts())
    assert "0 of 0 declared column types" in detail
    assert "0 of 0 declared layout fields" in detail
    assert "no physical comparison was made" in detail


@pytest.mark.parametrize("reverse", [False, True])
def test_public_detail_does_not_cover_an_unreadable_clock(tmp_path: Path, reverse: bool) -> None:
    case = Case(tmp_path)
    model = semantic_model()
    model.tables[0].columns.append(Column(name="shipped_at", type="Date", role=ColumnRole.TIME))
    model.dump(case.base / "model.yaml")
    case.with_candidate(model=model)
    contract = metric_contract()
    contract["metadata"]["id"] = "metric://sales/shipped_margin_pct"
    contract["time"]["event_time_column"] = "shipped_at"
    for root in (case.contracts, case.base / "metrics", case.candidate / "metrics"):
        _write_yaml(root / "shipped.yaml", contract)
    product = product_contract()
    product["scope"]["metrics"].append("metric://sales/shipped_margin_pct@3")
    _write_yaml(case.contracts / "weekly_margin.yaml", product)

    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        observed = environment()
        template = observed.tables[0]
        observed.tables = []
        for question in scope.tables:
            observed.tables.append(
                template.model_copy(
                    update={
                        "event_time_column": question.event_time_column,
                        "readable": question.event_time_column == "date",
                        "columns": dict(question.columns),
                        "layout": LayoutFacts(
                            table_engine=question.layout.table_engine,
                            sorting_key=question.layout.sorting_key,
                            partition_key=question.layout.partition_key,
                        ),
                    }
                )
            )
        if reverse:
            observed.tables.reverse()
        return observed

    report = case.run(probe=probe)
    physical = next(check for check in report.checks if check.check_id == "dwh.physical")
    access = next(check for check in report.checks if check.check_id == "dwh.access")
    assert access.summary_code == "access.table_unreadable"
    assert access.status is CheckStatus.BLOCK
    assert physical.status is CheckStatus.PASS
    assert physical.summary_code == "physical.as_declared"
    assert "4 of 9 declared column types" in physical.detail
    assert "3 of 6 declared layout fields" in physical.detail
    assert "coverage is incomplete" in physical.detail
    assert _check_payload(tmp_path, report, "dwh.physical")["findings"] == []
