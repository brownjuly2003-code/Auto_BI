"""Physical success evidence is limited to comparisons the engine actually answered."""

from collections.abc import Callable
from pathlib import Path

import pytest

from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.model import Column, ColumnRole
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    LayoutClaim,
    LayoutFacts,
    check_physical,
)
from auto_bi.verification.environment_probe import collect
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
from tests.test_environment_probe import (
    FakeEngine,
    RefusingEngine,
    clickhouse_engine,
    postgres_engine,
)
from tests.test_environment_question_identity import facts, questions, reading
from tests.test_freshness_coverage import two_clock_case


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
    expected = "physical.not_run" if coverage == "none" else "physical.as_declared"
    assert check.summary_code == expected
    assert _check_payload(tmp_path, report, "dwh.physical")["findings"] == []
    assert f"{columns} of 4 declared column types" in check.detail
    assert f"{layouts} of 3 declared layout fields" in check.detail
    if coverage == "complete":
        assert "coverage is complete" in check.detail
    else:
        assert "coverage is incomplete" in check.detail
    if coverage == "none":
        assert "no physical comparison was made" in check.detail
        summary = (
            FilesystemArtifactStore(tmp_path / "evidence")
            .get(report.run_id, "reports/summary.md")
            .decode("utf-8")
        )
        undone = summary.split("## What was not done")[1].split("\n## ")[0]
        assert "**dwh.physical**" in undone
        assert "| `dwh.physical` | pass | `physical.not_run` |" in summary
    assert "every column" not in check.detail
    assert "the layout matches" not in check.detail


@pytest.mark.parametrize("coverage", ["unknown", "unasked", "type", "layout", "deny_all"])
def test_public_physical_code_requires_a_declared_comparison(tmp_path: Path, coverage: str) -> None:
    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        observed = environment()
        table = observed.tables[0]
        question = scope.tables[0]
        table.columns = None
        table.layout = None
        if coverage == "unasked":
            table.columns = {"unasked": "String"}
            table.column_reads = None
            table.layout = LayoutFacts(distribution_key=["unasked"])
            assert not question.layout.distribution_key
        elif coverage in {"type", "deny_all"}:
            column = next(iter(question.columns))
            table.columns = {column: question.columns[column]}
            table.column_reads = None
            if coverage == "deny_all":
                table.row_security_enabled = True
                table.row_policies = []
                table.rows_seen = 0
        elif coverage == "layout":
            table.layout = LayoutFacts(table_engine=question.layout.table_engine)
        return observed

    report = Case(tmp_path).run(probe=probe)
    check = next(item for item in report.checks if item.check_id == "dwh.physical")
    compared = coverage in {"type", "layout", "deny_all"}
    expected = "physical.as_declared" if compared else "physical.not_run"
    assert check.status is CheckStatus.PASS
    assert check.summary_code == expected
    payload = _check_payload(tmp_path, report, "dwh.physical")
    assert payload["findings"] == []
    assert f"{int(coverage in {'type', 'deny_all'})} of 4 declared column types" in check.detail
    assert f"{int(coverage == 'layout')} of 3 declared layout fields" in check.detail
    summary = (
        FilesystemArtifactStore(tmp_path / "evidence")
        .get(report.run_id, "reports/summary.md")
        .decode("utf-8")
    )
    undone = summary.split("## What was not done")[1].split("\n## ")[0]
    assert ("**dwh.physical**" in undone) is not compared
    assert f"| `dwh.physical` | pass | `{expected}` |" in summary


@pytest.mark.parametrize("unusable", ["unreadable", "absent", "unattributed", "duplicate"])
@pytest.mark.parametrize("reverse", [False, True])
def test_public_physical_supplied_facts_do_not_cover_unusable_readings(
    tmp_path: Path, unusable: str, reverse: bool
) -> None:
    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        observed = environment()
        template = observed.tables[0]
        observed.tables = []
        for question in scope.tables:
            table = template.model_copy(deep=True)
            table.event_time_column = question.event_time_column
            table.columns = dict(question.columns)
            table.layout = LayoutFacts(table_engine=question.layout.table_engine)
            if unusable == "unreadable":
                table.readable = False
            elif unusable == "absent":
                table.exists = False
            elif unusable == "unattributed":
                table.event_time_column = None
            else:
                observed.tables.append(table.model_copy(deep=True))
            observed.tables.append(table)
        if reverse:
            observed.tables.reverse()
        return observed

    report = two_clock_case(tmp_path).run(probe=probe)
    check = next(item for item in report.checks if item.check_id == "dwh.physical")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "physical.not_run"
    assert _check_payload(tmp_path, report, "dwh.physical")["findings"] == []


@pytest.mark.parametrize(
    "finding,status",
    [
        ("column_absent", CheckStatus.BLOCK),
        ("type_drift", CheckStatus.REVIEW),
        ("layout_drift", CheckStatus.REVIEW),
    ],
)
def test_public_physical_findings_keep_precedence(
    tmp_path: Path, finding: str, status: CheckStatus
) -> None:
    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        observed = environment()
        table = observed.tables[0]
        question = scope.tables[0]
        table.columns = None
        table.layout = None
        if finding == "column_absent":
            table.columns = {}
        elif finding == "type_drift":
            table.columns = dict(question.columns)
            table.columns["revenue"] = "String"
        else:
            assert question.layout.sorting_key
            table.layout = LayoutFacts(sorting_key=[])
        return observed

    report = Case(tmp_path).run(probe=probe)
    check = next(item for item in report.checks if item.check_id == "dwh.physical")
    assert check.status is status
    assert check.summary_code == f"physical.{finding}"
    assert {
        item["code"] for item in _check_payload(tmp_path, report, "dwh.physical")["findings"]
    } == {f"physical.{finding}"}


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


@pytest.mark.parametrize("refused", [False, True], ids=["answered", "refused"])
@pytest.mark.parametrize(
    ("engine", "base", "catalog", "sqlstate"),
    [
        ("clickhouse", clickhouse_engine, "FROM system.columns", None),
        ("postgres", postgres_engine, "FROM pg_attribute", "42501"),
    ],
    ids=["clickhouse", "postgres"],
)
def test_a_column_catalog_the_role_may_not_read_marks_no_column_absent(
    tmp_path: Path,
    engine: str,
    base: Callable[[], FakeEngine],
    catalog: str,
    sqlstate: str | None,
    refused: bool,
) -> None:
    """ADR 0010, F1, through the gate. The engine's catalog lists `date` and `revenue` of the
    four columns the product reads: answered, the other two are gone and block. Refused, it
    was read as empty until verifier 133 — all four gone, a block over a mart whose rows the
    gate counted and whose columns it never saw; now none of them is compared. On PostgreSQL
    the same refusal leaves the role's reach unread, and that is what asks for review."""
    asked = base()
    run_query = RefusingEngine(asked, catalog, sqlstate=sqlstate) if refused else asked

    report = Case(tmp_path).run(
        probe=lambda scope: collect(run_query, scope, engine=engine, target="127.0.0.1:8124/dm")
    )

    check = next(item for item in report.checks if item.check_id == "dwh.physical")
    findings = _check_payload(tmp_path, report, "dwh.physical")["findings"]
    if refused:
        assert check.status is CheckStatus.PASS
        # ClickHouse still answered the layout from `system.tables`; PostgreSQL reports none
        assert check.summary_code == (
            "physical.as_declared" if engine == "clickhouse" else "physical.not_run"
        )
        assert findings == []
        assert "0 of 4 declared column types" in check.detail
        assert "coverage is incomplete" in check.detail
        if engine == "postgres":
            security = next(item for item in report.checks if item.check_id == "dwh.security")
            assert (security.status, security.summary_code) == (
                CheckStatus.REVIEW,
                "security.not_observed",
            )
    else:
        assert check.status is CheckStatus.BLOCK
        assert sorted(
            finding["subject"]
            for finding in findings
            if finding["code"] == "physical.column_absent"
        ) == ["dm.sales_daily.gross_margin", "dm.sales_daily.store_id"]
