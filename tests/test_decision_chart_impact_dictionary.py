"""A ClickHouse dictionary call in an operator tile: the read it names, or a named hole.

`dictGet('dm.rev', 'revenue', region)` reads `dm.rev.revenue`, while `extract_column_refs`
sees only the key column `region`. Before this rule the tile looked read in whole and could
be ruled out against a changed metric. A dictionary call now either names its read — put
through the same reconciliation as a table read — or leaves the tile read in part. Silence
is the one outcome that is not allowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.decision.impact import ChartRelation, ImpactReport, resolve_impact
from auto_bi.semantic.diff import SemanticChange, SemanticImpact, diff_snapshots
from tests.test_decision_chart_impact import (
    DASHBOARD,
    _reformulated,
    _relabelled,
    both_products,
    dashboard,
    impact_of,
    operator_dashboard,
    resolved,
    snapshots,
)

TILE = f"{DASHBOARD}#operator_table"


def field_change(tmp_path: Path, sql: str, *fields: str) -> tuple[SemanticChange, ImpactReport]:
    """One review-grade change touching *fields*, resolved against a tile running *sql*.

    The governed test model has no dictionary table, so the change is written by hand: what
    is under test is whether the tile names `<dictionary>.<attribute>`, not the diff.
    """
    base, head = snapshots(tmp_path)
    change = SemanticChange(
        code="field.description_changed",
        kind="modified",
        severity="advisory",
        subject=fields[0],
        detail="description changed",
        impact=SemanticImpact(fields=list(fields)),
    )
    diff = diff_snapshots(base, head).model_copy(update={"changes": [change]})
    report = resolve_impact(
        diff,
        both_products(),
        dashboards={DASHBOARD: operator_dashboard(sql)},
        metrics=[*base.graph.metrics, *head.graph.metrics],
    )
    return change, report


def charts(report: ImpactReport) -> list[tuple[str, ChartRelation, str]]:
    return [
        (chart.chart, chart.relation, chart.detail)
        for impact in report.impacts
        for chart in impact.charts
    ]


def gaps(report: ImpactReport) -> list[tuple[str, str]]:
    return [(gap.code, gap.subject) for gap in report.chart_gaps]


# --- resolved: a literal dictionary and attribute name the read ---------------------------


def test_a_literal_dictget_resolves_into_dictionary_dot_attribute(tmp_path: Path) -> None:
    sql = "SELECT region, dictGet('dm.rev', 'revenue', region) FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.revenue")
    ]
    assert report.chart_gaps == []


@pytest.mark.parametrize(
    "call",
    [
        "dictGetOrDefault('dm.rev', 'revenue', region, 0)",
        "dictGetOrNull('dm.rev', 'revenue', region)",
        "dictGetString('dm.rev', 'revenue', region)",
        "dictGetUInt64OrDefault('dm.rev', 'revenue', region, 0)",
        "DICTGET('dm.rev', 'revenue', region)",
    ],
)
def test_every_attribute_reading_form_resolves_the_same_way(tmp_path: Path, call: str) -> None:
    _, report = field_change(tmp_path, f"SELECT {call} FROM dm.sales_daily", "dm.rev.revenue")
    assert [(chart, relation) for chart, relation, _ in charts(report)] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert report.chart_gaps == []


def test_a_tuple_of_attributes_resolves_into_each_of_them(tmp_path: Path) -> None:
    sql = "SELECT dictGet('dm.rev', ('revenue', 'cost'), region) FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.revenue", "dm.rev.cost")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.cost, dm.rev.revenue")
    ]
    assert report.chart_gaps == []


def test_a_resolved_dictionary_read_of_another_field_is_not_this_tile(tmp_path: Path) -> None:
    sql = "SELECT dictGet('dm.rev', 'revenue', region) FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.cost")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_a_dictionary_name_that_cannot_be_reconciled_is_named_as_unread(tmp_path: Path) -> None:
    """A bare `rev` is not the tile's table: same rule as a bare table name."""
    sql = "SELECT dictGet('rev', 'revenue', region) FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert "rev could not be reconciled" in report.chart_gaps[0].detail


def test_a_dictionary_read_of_a_metric_term_keeps_the_metric_from_being_ruled_out(
    tmp_path: Path,
) -> None:
    """Read through a dictionary, `revenue` is a term column: the tile cannot be ruled out."""
    sql = "SELECT sum(store_id), dictGet('dm.sales_daily', 'revenue', store_id) FROM dm.sales_daily"
    _, report = resolved(
        tmp_path, candidate=_reformulated(), dashboards={DASHBOARD: operator_dashboard(sql)}
    )
    assert gaps(report) == [("chart.metric_unread", TILE)]


def test_a_dictionary_read_of_no_metric_term_still_rules_the_metric_out(tmp_path: Path) -> None:
    sql = "SELECT sum(store_id), dictGet('dm.rev', 'revenue', store_id) FROM dm.sales_daily"
    _, report = resolved(
        tmp_path, candidate=_reformulated(), dashboards={DASHBOARD: operator_dashboard(sql)}
    )
    assert impact_of(report, "metric.formula_changed").charts == []
    assert report.chart_gaps == []


# --- every other dictionary call leaves the tile read in part ------------------------------


@pytest.mark.parametrize(
    ("call", "name"),
    [
        ("dictGet(dict_name, 'revenue', region)", "dictGet"),
        ("dictGet('dm.rev', attr, region)", "dictGet"),
        ("dictGet('dm.rev', tuple('revenue', 'cost'), region)", "dictGet"),
        ("dictGet('dm.rev')", "dictGet"),
        ("dictGetHierarchy('dm.rev', region)", "dictGetHierarchy"),
        ("dictGetChildren('dm.rev', region)", "dictGetChildren"),
        ("dictGetDescendants('dm.rev', region, 1)", "dictGetDescendants"),
        ("dictSomethingNew('dm.rev', region)", "dictSomethingNew"),
        # A membership test reads the key attribute, a hierarchy test the parent attribute;
        # neither spells the attribute, so silence about them would be the same hole.
        ("dictHas('dm.rev', region)", "dictHas"),
        ("dictIsIn('dm.rev', region, store_id)", "dictIsIn"),
    ],
)
def test_a_dictionary_call_this_run_cannot_name_leaves_the_tile_read_in_part(
    tmp_path: Path, call: str, name: str
) -> None:
    sql = f"SELECT sum(revenue), {call} FROM dm.sales_daily"
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert f"{name}(...)" in report.chart_gaps[0].detail


def test_an_unnamed_dictionary_call_keeps_the_metric_from_being_ruled_out(
    tmp_path: Path,
) -> None:
    sql = "SELECT sum(store_id), dictGet(dict_name, 'x', store_id) FROM dm.sales_daily"
    _, report = resolved(
        tmp_path, candidate=_reformulated(), dashboards={DASHBOARD: operator_dashboard(sql)}
    )
    assert gaps(report) == [("chart.metric_unread", TILE), ("chart.sql_partial", TILE)]


# --- scope: only a call the statement runs --------------------------------------------------


@pytest.mark.parametrize(
    "cte",
    [
        "SELECT dictGet('dm.rev', 'revenue', region) AS r FROM dm.sales_daily",
        "SELECT dictGet(dict_name, 'revenue', region) AS r FROM dm.sales_daily",
    ],
)
def test_a_dictionary_call_in_a_cte_the_from_never_names_changes_nothing(
    tmp_path: Path, cte: str
) -> None:
    sql = f"WITH x AS ({cte}) SELECT sum(store_id) FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_a_dictionary_call_in_a_cte_the_from_names_is_read(tmp_path: Path) -> None:
    sql = (
        "WITH x AS (SELECT dictGet('dm.rev', 'revenue', region) AS r FROM dm.sales_daily) "
        "SELECT r FROM x"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert [(chart, relation) for chart, relation, _ in charts(report)] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]


def test_a_dictionary_call_in_a_subquery_is_read(tmp_path: Path) -> None:
    sql = (
        "SELECT sum(revenue) FROM dm.sales_daily WHERE store_id IN "
        "(SELECT store_id FROM dm.sales_daily WHERE dictGet('dm.rev', 'revenue', store_id) > 0)"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert [(chart, relation) for chart, relation, _ in charts(report)] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    # `store_id` is already reported as unattributed without the dictionary call; the call
    # adds no clause of its own.
    assert not any("dictGet" in gap.detail for gap in report.chart_gaps)


def test_a_dictionary_call_in_an_unreferenced_scalar_with_changes_nothing(
    tmp_path: Path,
) -> None:
    """`WITH <expr> AS r` binds a value; nobody reads `r`, so the call never runs."""
    sql = (
        "WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS r "
        "SELECT sum(revenue) FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_an_unnamed_dictionary_call_in_an_unreferenced_scalar_with_changes_nothing(
    tmp_path: Path,
) -> None:
    sql = "WITH dictGet(dict_name, 'x', toUInt64(1)) AS r SELECT sum(revenue) FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_a_dictionary_call_in_a_referenced_scalar_with_is_read(tmp_path: Path) -> None:
    sql = (
        "WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS r "
        "SELECT sum(revenue) * r FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.revenue")
    ]
    assert not any("dictGet" in gap.detail for gap in report.chart_gaps)


# --- false-reject control: ordinary SQL is unchanged ----------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, sum(revenue) FROM dm.sales_daily GROUP BY region",
        "SELECT count(*), sum(revenue) FROM dm.sales_daily",
        "SELECT formatDateTime(date, '%Y'), sum(revenue) FROM dm.sales_daily GROUP BY 1",
        "SELECT toStartOfInterval(date, INTERVAL 1 DAY), sum(revenue) FROM dm.sales_daily "
        "GROUP BY 1",
        "SELECT dictionary_id, sum(revenue) FROM dm.sales_daily GROUP BY dictionary_id",
    ],
)
def test_ordinary_sql_still_reads_in_whole(tmp_path: Path, sql: str) -> None:
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert report.chart_gaps == []


def test_a_plain_tile_is_still_ruled_out_against_a_metric_it_does_not_name(
    tmp_path: Path,
) -> None:
    sql = "SELECT region, sum(store_id) FROM dm.sales_daily GROUP BY region"
    _, report = resolved(
        tmp_path, candidate=_reformulated(), dashboards={DASHBOARD: operator_dashboard(sql)}
    )
    assert impact_of(report, "metric.formula_changed").charts == []
    assert report.chart_gaps == []


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT * FROM dm.sales_daily", "chart.sql_partial"),
        ("this is not SQL", "chart.opaque_sql"),
    ],
)
def test_a_root_star_and_unparseable_text_keep_their_gaps(
    tmp_path: Path, sql: str, code: str
) -> None:
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    assert gaps(report) == [(code, TILE)]


def test_an_ir_tile_still_computes_the_metric(tmp_path: Path) -> None:
    _, report = resolved(tmp_path, candidate=_reformulated(), dashboards={DASHBOARD: dashboard()})
    impact = impact_of(report, "metric.formula_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("margin_by_region", ChartRelation.COMPUTES_METRIC)
    ]


def test_an_ir_tile_still_reads_a_field(tmp_path: Path) -> None:
    _, report = resolved(tmp_path, candidate={"semantic": _relabelled(1, 0, "Store surrogate key")})
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("stores_covered", ChartRelation.READS_FIELD)
    ]
