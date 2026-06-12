"""Gaps report unit tests: deterministic findings on synthetic semantic models."""

from auto_bi.introspect.gaps import GapSeverity, find_gaps
from auto_bi.semantic.model import (
    Column,
    ColumnRole,
    Join,
    Physical,
    SemanticModel,
    Table,
)


def _column(name: str, role: ColumnRole, **kwargs) -> Column:
    types = {ColumnRole.TIME: "Date", ColumnRole.MEASURE: "Float64", ColumnRole.DIMENSION: "String"}
    return Column(name=name, type=types[role], role=role, **kwargs)


def _physical(rows: int = 1000) -> Physical:
    return Physical(engine="clickhouse", table_engine="MergeTree", rows=rows)


def aggregated_marts_model() -> SemanticModel:
    """Shape of the DE_project marts: documented-by-comment-less, isolated, pre-aggregated."""
    return SemanticModel(
        tables=[
            Table(
                name="marts.branch_pnl",
                grain=["branch", "month"],
                columns=[
                    _column("branch", ColumnRole.DIMENSION),
                    _column("month", ColumnRole.TIME),
                    _column("gross_revenue", ColumnRole.MEASURE),
                ],
                physical=_physical(),
            ),
            Table(
                name="marts.customer_360",
                grain=["branch", "customer_hk"],
                columns=[
                    _column("customer_hk", ColumnRole.DIMENSION),
                    _column("branch", ColumnRole.DIMENSION),
                    _column("store_hk", ColumnRole.DIMENSION),
                    _column("lifetime_value", ColumnRole.MEASURE),
                ],
                physical=_physical(),
            ),
        ],
        joins=[],
    )


def fake_run_query_preaggregated(sql: str) -> list[dict]:
    if "toDayOfMonth" in sql and "branch_pnl" in sql:
        return [{"off": 0, "non_null": 1000}]  # month column holds month starts only
    raise AssertionError(f"unexpected query: {sql}")


def test_marts_shape_yields_core_gaps() -> None:
    report = find_gaps(aggregated_marts_model(), fake_run_query_preaggregated)
    codes = {f.code for f in report.findings}
    assert "no_relationships" in codes
    assert "preaggregated_time_grain" in codes
    assert "no_fine_time_grain" in codes
    assert "table_no_description" in codes
    assert "columns_no_description" in codes


def test_entity_without_dimension_table_skips_grain_keys() -> None:
    report = find_gaps(aggregated_marts_model(), fake_run_query_preaggregated)
    entity_gaps = [f for f in report.findings if f.code == "entity_without_dimension_table"]
    # customer_hk is the table's own grain -> not a gap; store_hk has no dim table -> gap
    assert [(f.table, f.column) for f in entity_gaps] == [("marts.customer_360", "store_hk")]
    assert all(f.dm_change_request for f in entity_gaps)


def test_documented_joined_daily_model_is_clean() -> None:
    model = SemanticModel(
        tables=[
            Table(
                name="dm.sales_daily",
                description="Дневные продажи",
                grain=["date", "store_id"],
                columns=[
                    _column("date", ColumnRole.TIME, description="День"),
                    _column(
                        "store_id",
                        ColumnRole.DIMENSION,
                        description="Магазин",
                        fk="dm.stores.id",
                    ),
                    _column("revenue", ColumnRole.MEASURE, description="Выручка"),
                ],
                physical=_physical(rows=1_000_000),
            ),
            Table(
                name="dm.stores",
                description="Справочник магазинов",
                grain=["id"],
                columns=[
                    _column("id", ColumnRole.DIMENSION, description="ID"),
                    _column("city", ColumnRole.DIMENSION, description="Город"),
                ],
                physical=_physical(rows=4200),
            ),
        ],
        joins=[Join(left="dm.sales_daily.store_id", right="dm.stores.id")],
    )

    def run_query(sql: str) -> list[dict]:
        if "toDayOfMonth" in sql or "toStartOfWeek" in sql:
            # genuinely daily values fail both pre-agg probes
            return [{"off": 17, "non_null": 1_000_000}]
        raise AssertionError(f"unexpected query: {sql}")

    report = find_gaps(model, run_query)
    assert report.findings == []


def test_offline_mode_uses_name_heuristics() -> None:
    report = find_gaps(aggregated_marts_model(), run_query=None)
    codes = {f.code for f in report.findings}
    assert "preaggregated_time_grain" in codes  # column named "month"
    assert "no_fine_time_grain" in codes


def test_markdown_lists_dm_change_request_candidates() -> None:
    report = find_gaps(aggregated_marts_model(), fake_run_query_preaggregated)
    markdown = report.to_markdown()
    assert "# Gaps report" in markdown
    assert "## critical" in markdown
    assert "Кандидаты в dm_change_request" in markdown
    assert "store_hk" in markdown


def test_all_null_time_column_is_reported_not_misclassified() -> None:
    model = SemanticModel(
        tables=[
            Table(
                name="marts.customer_360",
                grain=["customer_hk"],
                columns=[
                    _column("customer_hk", ColumnRole.DIMENSION),
                    _column("last_visit_at", ColumnRole.TIME),
                    _column("first_order_dt", ColumnRole.TIME),
                ],
                physical=_physical(),
            )
        ]
    )

    def run_query(sql: str) -> list[dict]:
        if "last_visit_at" in sql and "toDayOfMonth" in sql:
            return [{"off": 0, "non_null": 0}]  # all-NULL: off==0 must NOT mean "month"
        if "first_order_dt" in sql:
            return [{"off": 5, "non_null": 900}]
        raise AssertionError(f"unexpected query: {sql}")

    report = find_gaps(model, run_query)
    codes = {(f.code, f.column) for f in report.findings}
    assert ("column_all_null", "last_visit_at") in codes
    assert ("preaggregated_time_grain", "last_visit_at") not in codes
    assert "no_fine_time_grain" not in {f.code for f in report.findings}  # first_order_dt is fine


def test_zero_cardinality_dimension_is_reported() -> None:
    physical = Physical(
        engine="clickhouse",
        table_engine="MergeTree",
        rows=1000,
        cardinality={"pii_source": 0, "branch": 5},
    )
    model = SemanticModel(
        tables=[
            Table(
                name="marts.customer_360",
                grain=["customer_hk"],
                columns=[
                    _column("branch", ColumnRole.DIMENSION),
                    _column("pii_source", ColumnRole.DIMENSION),
                ],
                physical=physical,
            )
        ]
    )
    report = find_gaps(model, run_query=None)
    null_cols = [f.column for f in report.findings if f.code == "column_all_null"]
    assert null_cols == ["pii_source"]


def test_severity_ordering_and_counts() -> None:
    report = find_gaps(aggregated_marts_model(), fake_run_query_preaggregated)
    severities = [f.severity for f in report.findings]
    rank = {GapSeverity.CRITICAL: 0, GapSeverity.WARN: 1, GapSeverity.INFO: 2}
    assert severities == sorted(severities, key=lambda s: rank[s])
    assert len(report.by_severity(GapSeverity.CRITICAL)) == 2  # no_relationships + no_fine_grain
