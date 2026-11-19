"""The Superset inventory (ADR 0012, D6): delivered dashboards read into the gate's specs.

The answers are those of the pinned Superset 4.1.2 (`tests/fixtures/superset_inventory`): its
`load-examples` corpus and a dashboard over the ClickHouse demo DM, trimmed to the fields the
inventory reads. What is pinned here is the read model: a chart is typed only when the IR says
exactly what it reads and computes, every other chart is read as SQL that reads at least what
Superset reads, and a dashboard is written whole or not at all.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import socket
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlglot
import yaml
from sqlglot import expressions as exp

from auto_bi.adapters.superset.client import SupersetClient
from auto_bi.cli import main
from auto_bi.config import get_settings
from auto_bi.decision.use_cases import (
    INVENTORY_REPORT,
    ImpactRequest,
    InventoryRequest,
    VerifyError,
    VerifyRequest,
    impact,
    inventory,
    verify,
)
from auto_bi.inventory import superset as inventory_module
from auto_bi.inventory.superset import (
    MAX_CHARTS,
    ChartForm,
    DashboardRead,
    RestSupersetSource,
    SupersetAnswerError,
    SupersetSourceError,
    dump_spec,
    gate_refusals,
    inventory_dashboard,
)
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec, FilterOp, TimeGrain, Viz
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    SemanticModel,
    Table,
)
from auto_bi.verification.release_gate import CheckStatus

FIXTURES = Path(__file__).parent / "fixtures" / "superset_inventory"
PILOT = Path(__file__).resolve().parents[1] / "docs" / "pilot" / "2026-08-25-ch-control"
CORPUS = tuple(range(1, 12))
LIVE = 12

#: The demo DM's `dm.sales_daily`, as the ClickHouse pilot's semantic model holds it.
MODEL = SemanticModel(
    tables=[
        Table(
            name="dm.sales_daily",
            columns=[
                Column(name="date", type="Date", role=ColumnRole.TIME),
                Column(name="store_id", type="UInt32", role=ColumnRole.DIMENSION),
                Column(name="region", type="String", role=ColumnRole.DIMENSION),
                Column(name="revenue", type="Decimal(18, 2)", role=ColumnRole.MEASURE),
                Column(name="orders", type="UInt32", role=ColumnRole.MEASURE),
            ],
        )
    ]
)


def _answers(ident: int | str) -> dict[str, Any]:
    path = FIXTURES / f"dashboard_{ident}.json"
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


class Recorded:
    """The recorded answers, by dashboard id or slug; each call is counted."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def _find(self, ident: str) -> dict[str, Any]:
        for path in sorted(FIXTURES.glob("dashboard_*.json")):
            answers = json.loads(path.read_text(encoding="utf-8"))
            if ident in (str(answers["dashboard"]["id"]), answers["dashboard"]["slug"]):
                found: dict[str, Any] = answers
                return found
        raise SupersetSourceError(f"GET /api/v1/dashboard/{ident} -> HTTP 404")

    def dashboard(self, ident: str) -> Mapping[str, Any]:
        self.calls.append(("dashboard", ident))
        found: Mapping[str, Any] = self._find(ident)["dashboard"]
        return found

    def charts(self, ident: str) -> Sequence[Mapping[str, Any]]:
        self.calls.append(("charts", ident))
        found: Sequence[Mapping[str, Any]] = self._find(ident)["charts"]
        return found

    def datasets(self, ident: str) -> Sequence[Mapping[str, Any]]:
        self.calls.append(("datasets", ident))
        found: Sequence[Mapping[str, Any]] = self._find(ident)["datasets"]
        return found

    def saves(self, dashboard_id: int) -> Sequence[Mapping[str, Any]]:
        self.calls.append(("saves", str(dashboard_id)))
        # D6 recordings predate saved contexts; no chart-list evidence is available.
        return []

    def chart(self, chart_id: int) -> Mapping[str, Any]:
        raise AssertionError("an unsaved D6 chart must not fetch its detail")

    def rendered(self, chart_id: int) -> tuple[int, Mapping[str, Any]]:
        raise AssertionError("an unsaved D6 chart must not fetch rendered SQL")

    def dataset_definition(self, dataset_id: int) -> tuple[int, Mapping[str, Any]]:
        self.calls.append(("definition", str(dataset_id)))
        return 404, {}  # The D6 recording contains no full dataset definitions.


class Given:
    """One dashboard of the given charts and datasets."""

    def __init__(
        self,
        charts: Sequence[Mapping[str, Any]],
        datasets: Sequence[Mapping[str, Any]],
        dashboard: Mapping[str, Any] | None = None,
    ) -> None:
        self._charts = charts
        self._datasets = datasets
        self._dashboard = dashboard or {"id": 7, "dashboard_title": "Given", "changed_on": "x"}

    def dashboard(self, ident: str) -> Mapping[str, Any]:
        return self._dashboard

    def charts(self, ident: str) -> Sequence[Mapping[str, Any]]:
        return self._charts

    def datasets(self, ident: str) -> Sequence[Mapping[str, Any]]:
        return self._datasets

    def saves(self, dashboard_id: int) -> Sequence[Mapping[str, Any]]:
        return []

    def chart(self, chart_id: int) -> Mapping[str, Any]:
        raise AssertionError("a D6 chart without saved context must not fetch its detail")

    def rendered(self, chart_id: int) -> tuple[int, Mapping[str, Any]]:
        raise AssertionError("a D6 chart without saved context must not fetch rendered SQL")

    def dataset_definition(self, dataset_id: int) -> tuple[int, Mapping[str, Any]]:
        return 404, {}  # The D6 fixture contains only the trimmed dashboard answer.


def _live_chart(chart_id: int) -> dict[str, Any]:
    chart: dict[str, Any] = next(
        chart for chart in _answers(LIVE)["charts"] if chart["id"] == chart_id
    )
    return copy.deepcopy(chart)


def _live_dataset(dataset_id: int = 25) -> dict[str, Any]:
    dataset: dict[str, Any] = next(
        dataset for dataset in _answers(LIVE)["datasets"] if dataset["id"] == dataset_id
    )
    return copy.deepcopy(dataset)


PIE = 122
LINE = 123


def _read(
    form_data: Mapping[str, Any] | None = None,
    *,
    base: int = PIE,
    drop: Sequence[str] = (),
    dataset: Mapping[str, Any] | None = None,
    model: SemanticModel = MODEL,
) -> DashboardRead:
    """The live chart *base* with *form_data* merged into its own and *drop* removed."""
    chart = _live_chart(base)
    chart["form_data"].update(form_data or {})
    for key in drop:
        chart["form_data"].pop(key, None)
    source = Given([chart], [dataset if dataset is not None else _live_dataset()])
    return inventory_dashboard("dashboard://live@1", "7", source, model)


def _form(read: DashboardRead) -> tuple[ChartForm, list[str]]:
    assert read.spec is not None, read.record.refusals
    (chart,) = read.record.charts
    return chart.form, chart.reasons


def _typed(read: DashboardRead) -> Any:
    form, reasons = _form(read)
    assert form is ChartForm.TYPED, reasons
    assert read.spec is not None
    return read.spec.charts[0]


def _not_typed(read: DashboardRead) -> str:
    form, reasons = _form(read)
    assert form is ChartForm.READ_SQL
    assert len(reasons) >= 1
    return reasons[0]


# --- the corpus -------------------------------------------------------------------------------


def _corpus_model() -> SemanticModel:
    """Every physical table of the corpus with its plain columns: a model the charts are on."""
    tables: dict[str, Table] = {}
    numeric = ("INT", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL", "REAL")
    for ident in CORPUS:
        for dataset in _answers(ident)["datasets"]:
            if dataset["sql"] or not dataset["schema"]:
                continue
            name = f"{dataset['schema']}.{dataset['table_name']}"
            columns = []
            for column in dataset["columns"]:
                if column["expression"]:
                    continue
                kind = (column["type"] or "").upper()
                role = (
                    ColumnRole.TIME
                    if column["is_dttm"]
                    else (
                        ColumnRole.MEASURE
                        if any(word in kind for word in numeric)
                        else ColumnRole.DIMENSION
                    )
                )
                columns.append(Column(name=column["column_name"], type=kind or "STRING", role=role))
            tables.setdefault(name, Table(name=name, columns=columns))
    return SemanticModel(tables=list(tables.values()))


def test_the_corpus_is_read_whole_or_refused_by_name() -> None:
    """The 110 charts of Superset's own examples: what is typed, what is read as SQL, what not.

    Pinned per dashboard, so a rule that types one more chart, or one fewer, is seen here.
    """
    model = _corpus_model()
    measured: dict[int, tuple[bool, list[int], int]] = {}
    for ident in CORPUS:
        read = inventory_dashboard(f"dashboard://d{ident}@1", str(ident), Recorded(), model)
        forms = [chart.form for chart in read.record.charts]
        measured[ident] = (
            read.spec is not None,
            [chart.superset_id for chart in read.record.charts if chart.form is ChartForm.TYPED],
            forms.count(ChartForm.READ_SQL),
        )
        if read.spec is None:
            assert read.record.refusals
            assert read.record.written is None
        else:
            assert not read.record.refusals
            assert [chart.id for chart in read.spec.charts] == [
                chart.chart for chart in read.record.charts
            ]
            assert gate_refusals(read.spec, model) == []

    # (written, the Superset ids of the typed charts, how many are read as SQL)
    assert measured == {
        1: (True, [], 9),
        2: (True, [], 11),
        3: (True, [], 3),
        4: (True, [], 7),
        5: (True, [], 9),
        6: (True, [67], 7),
        7: (True, [112, 116, 119, 120], 6),
        8: (True, [], 7),
        9: (False, [44], 22),
        10: (True, [], 1),
        11: (False, [], 21),
    }


def test_a_dashboard_past_the_spec_limit_is_refused_with_its_count() -> None:
    read = inventory_dashboard("dashboard://d@1", "11", Recorded(), _corpus_model())

    assert read.spec is None
    assert read.record.refusals == [f"the dashboard holds 21 charts; a spec holds {MAX_CHARTS}"]


def test_a_dataset_sql_clickhouse_does_not_parse_refuses_the_dashboard() -> None:
    """Superset's own example SQL has a bare `UNION`: ClickHouse wants `ALL` or `DISTINCT`."""
    read = inventory_dashboard("dashboard://d@1", "9", Recorded(), _corpus_model())

    assert read.spec is None
    (refusal,) = [line for line in read.record.refusals if line.startswith("chart_46:")]
    assert refusal.startswith("chart_46: its read SQL is not one plain SELECT: ")
    assert "\x1b" not in refusal
    assert len(refusal) <= len("chart_46: its read SQL is not one plain SELECT: ") + 200


# --- the live dashboard: typed and read as SQL ------------------------------------------------


def test_the_live_dashboard_types_three_charts_and_reads_three_as_sql() -> None:
    read = inventory_dashboard("dashboard://weekly_revenue@1", "d6-live", Recorded(), MODEL)

    assert read.spec is not None
    assert read.record.refusals == []
    assert (read.record.superset_id, read.record.title) == (12, "D6 live")
    assert read.record.changed_on == "2026-11-14T11:01:08.621262"
    assert [(chart.chart, chart.viz_type, chart.form) for chart in read.record.charts] == [
        ("chart_121", "big_number_total", ChartForm.TYPED),
        ("chart_122", "pie", ChartForm.TYPED),
        ("chart_123", "echarts_timeseries_line", ChartForm.TYPED),
        ("chart_124", "table", ChartForm.READ_SQL),
        ("chart_125", "echarts_timeseries_bar", ChartForm.READ_SQL),
        ("chart_126", "table", ChartForm.READ_SQL),
    ]
    by_id = {chart.id: chart for chart in read.spec.charts}
    assert by_id["chart_121"].model_dump(mode="json", exclude_defaults=True) == {
        "id": "chart_121",
        "title": "Выручка",
        "viz": "big_number",
        "query": {
            "table": "dm.sales_daily",
            "measures": [{"column": "revenue", "agg": "sum"}],
            "limit": 50000,
        },
    }
    assert by_id["chart_122"].model_dump(mode="json", exclude_defaults=True)["query"] == {
        "table": "dm.sales_daily",
        "dimensions": ["store_id"],
        "measures": [{"column": "revenue", "agg": "sum"}],
        "limit": 100,
    }
    line = by_id["chart_123"]
    assert (line.viz, line.query.dimensions, line.query.time_grain) == (
        Viz.LINE,
        ["date"],
        TimeGrain.WEEK,
    )
    assert by_id["chart_124"].query.raw_sql == "SELECT\n*,\n(\nCOUNT(*)\n)\nFROM dm.sales_daily"
    assert by_id["chart_125"].query.raw_sql == (
        "SELECT\n*,\n(\nSUM(revenue) / NULLIF(SUM(orders), 0)\n),\n(\nCOUNT(*)\n)\n"
        "FROM dm.sales_daily"
    )
    virtual = by_id["chart_126"]
    assert virtual.query.table == "dm.d6_weekly_revenue"
    assert virtual.query.raw_sql == (
        "SELECT\n*\nFROM (\nSELECT toStartOfWeek(date) AS week, sum(revenue) AS revenue\n"
        "FROM dm.sales_daily\nGROUP BY week\n) AS virtual_table"
    )
    assert [chart.reasons for chart in read.record.charts[3:]] == [
        [
            "form_data.metrics[0]: the saved metric 'count' is not AGG(column)",
            "saved: the last change is not a save within two seconds",
            "composite_sql: the dataset definition is not readable",
        ],
        [
            "form_data carries a SQL expression",
            "saved: the last change is not a save within two seconds",
            "composite_sql: the dataset definition is not readable",
        ],
        [
            "the dataset is virtual (its own SQL)",
            "saved: the last change is not a save within two seconds",
            "composite_sql: the dataset definition is not readable",
        ],
    ]
    assert all(chart.title for chart in read.spec.charts)
    assert read.spec.title == "D6 live"


def test_every_chart_of_a_dashboard_is_asked_of_one_dashboard() -> None:
    source = Recorded()

    inventory_dashboard("dashboard://weekly_revenue@1", "d6-live", source, MODEL)

    assert source.calls == [
        ("dashboard", "d6-live"),
        ("charts", "d6-live"),
        ("datasets", "d6-live"),
        ("saves", "12"),
        ("definition", "25"),
        ("definition", "26"),
    ]


# --- what keeps a chart from being typed ------------------------------------------------------


@pytest.mark.parametrize(
    ("form_data", "reason"),
    [
        ({"viz_type": "treemap_v2"}, "viz_type 'treemap_v2' is outside the typed set"),
        ({"limit": 10}, "form_data.limit is not a key the typed form reads"),
        ({"percent_metrics": ["count"]}, "form_data.percent_metrics is not a key the typed form"),
        ({"rolling_type": "mean"}, "form_data.rolling_type = 'mean' post-processes the result"),
        ({"comparison_type": "ratio"}, "form_data.comparison_type = 'ratio' post-processes"),
        ({"contributionMode": "row"}, "form_data.contributionMode = 'row' post-processes"),
        ({"time_compare": ["1 week ago"]}, "form_data.time_compare = ['1 week ago'] post-proc"),
        ({"resample_rule": "1D"}, "form_data.resample_rule = '1D' post-processes"),
        ({"resample_method": "asfreq"}, "form_data.resample_method = 'asfreq' post-processes"),
        ({"forecastEnabled": True}, "form_data.forecastEnabled = True post-processes"),
        ({"aggregateFunction": "Count"}, "form_data.aggregateFunction = 'Count' post-processes"),
        ({"where": "store_id > 3"}, "form_data carries a SQL expression"),
        ({"having": "SUM(revenue) > 0"}, "form_data carries a SQL expression"),
        (
            {"metric": {"expressionType": "SQL", "sqlExpression": "SUM(revenue)"}},
            "form_data carries a SQL expression",
        ),
        (
            {
                "metric": {
                    "expressionType": "SIMPLE",
                    "aggregate": "MEDIAN",
                    "column": {"column_name": "revenue"},
                }
            },
            "form_data.metric aggregates with 'MEDIAN'",
        ),
        (
            {"metric": {"expressionType": "SIMPLE", "aggregate": "SUM", "column": None}},
            "form_data.metric.column is not a plain column name",
        ),
        ({"metric": "no_such_metric"}, "form_data.metric names no metric of the dataset"),
        ({"metric": "count"}, "form_data.metric: the saved metric 'count' is not AGG(column)"),
        ({"metric": None}, "the chart aggregates nothing"),
        ({"groupby": [{"label": "x", "sqlExpression": "1"}]}, "form_data carries a SQL"),
        ({"groupby": [{"label": "x"}]}, "form_data.groupby[0] is not a plain column name"),
        ({"x_axis": "date"}, "form_data.x_axis is not read by pie"),
        ({"time_range": "Last week"}, "form_data.time_range = 'Last week' filters time"),
        ({"row_limit": 60000}, "form_data.row_limit = 60000 is outside the IR limit"),
        ({"row_limit": "100"}, "form_data.row_limit = '100' is outside the IR limit"),
        ({"query_mode": "raw"}, "the table lists raw rows, not aggregates"),
        ({"all_columns": ["store_id"]}, "the table lists raw rows, not aggregates"),
    ],
)
def test_a_chart_the_ir_does_not_say_exactly_is_read_as_sql(
    form_data: dict[str, Any], reason: str
) -> None:
    assert _not_typed(_read(form_data)).startswith(reason)


@pytest.mark.parametrize(
    ("adhoc", "reason"),
    [
        (
            {"expressionType": "SQL", "clause": "WHERE", "sqlExpression": "store_id = 1"},
            "form_data carries a SQL expression",
        ),
        (
            {
                "expressionType": "SIMPLE",
                "clause": "HAVING",
                "operator": "==",
                "subject": "store_id",
                "comparator": 1,
            },
            "form_data.adhoc_filters[1] filters the 'HAVING' clause",
        ),
        (
            {
                "expressionType": "SIMPLE",
                "clause": "WHERE",
                "operator": "NOT IN",
                "subject": "store_id",
                "comparator": [1],
            },
            "form_data.adhoc_filters[1] filters with 'NOT IN'",
        ),
        (
            {
                "expressionType": "SIMPLE",
                "clause": "WHERE",
                "operator": "TEMPORAL_RANGE",
                "subject": "date",
                "comparator": "Last month",
            },
            "form_data.adhoc_filters[1] limits time to 'Last month'",
        ),
        (
            {
                "expressionType": "SIMPLE",
                "clause": "WHERE",
                "operator": "IN",
                "subject": "store_id",
                "comparator": [],
            },
            "form_data.adhoc_filters[1] compares with a value the IR has no form for",
        ),
        (
            {
                "expressionType": "SIMPLE",
                "clause": "WHERE",
                "operator": "IN",
                "subject": "store_id",
                "comparator": [1, "2"],
            },
            "form_data.adhoc_filters[1] compares with a value the IR has no form for",
        ),
        (
            {
                "expressionType": "SIMPLE",
                "clause": "WHERE",
                "operator": "==",
                "subject": "store_id",
                "comparator": True,
            },
            "form_data.adhoc_filters[1] compares with a value the IR has no form for",
        ),
        (
            {
                "expressionType": "SIMPLE",
                "clause": "WHERE",
                "operator": "==",
                "subject": "store_id",
                "comparator": None,
            },
            "form_data.adhoc_filters[1] compares with a value the IR has no form for",
        ),
        ("store_id = 1", "form_data.adhoc_filters[1] is not a simple filter"),
    ],
)
def test_a_filter_the_ir_has_no_form_for_reads_the_chart_as_sql(adhoc: Any, reason: str) -> None:
    chart = _live_chart(PIE)
    filters = [*chart["form_data"]["adhoc_filters"], adhoc]
    assert _not_typed(_read({"adhoc_filters": filters})).startswith(reason)


@pytest.mark.parametrize(
    ("operator", "comparator", "op"),
    [
        ("==", 3, FilterOp.EQ),
        ("!=", 4, FilterOp.NEQ),
        ("IN", [1, 2], FilterOp.IN),
        (">=", 1.5, FilterOp.GTE),
        ("<=", 9, FilterOp.LTE),
    ],
)
def test_a_filter_of_the_ir_operators_is_typed(
    operator: str, comparator: Any, op: FilterOp
) -> None:
    chart = _live_chart(PIE)
    adhoc = {
        "expressionType": "SIMPLE",
        "clause": "WHERE",
        "operator": operator,
        "subject": "store_id",
        "comparator": comparator,
    }
    typed = _typed(_read({"adhoc_filters": [*chart["form_data"]["adhoc_filters"], adhoc]}))

    (only,) = typed.query.filters
    assert (only.column, only.op, only.value) == ("store_id", op, comparator)


def test_no_time_filter_and_a_legacy_no_filter_are_typed_without_a_filter() -> None:
    typed = _typed(_read({"time_range": "No filter", "granularity_sqla": "date"}))

    assert typed.query.filters == []


def test_a_virtual_dataset_and_a_dataset_without_schema_are_not_typed() -> None:
    virtual = {**_live_dataset(), "sql": "SELECT * FROM dm.sales_daily"}
    assert _not_typed(_read(dataset=virtual)) == "the dataset is virtual (its own SQL)"

    read = _read(dataset={**_live_dataset(), "schema": None})
    assert read.spec is None
    assert read.record.refusals == [
        "chart_122: the dataset names no schema, so its table could not be named"
    ]


def test_a_calculated_column_is_not_typed_wherever_it_is_read() -> None:
    dataset = _live_dataset()
    dataset["columns"].append(
        {"column_name": "store_x", "expression": "store_id * 2", "is_dttm": False, "type": None}
    )
    calculated = {
        "expressionType": "SIMPLE",
        "aggregate": "SUM",
        "column": {"column_name": "store_x"},
    }
    subject = {
        "expressionType": "SIMPLE",
        "clause": "WHERE",
        "operator": "==",
        "subject": "store_x",
        "comparator": 1,
    }

    assert _not_typed(_read({"groupby": ["store_x"]}, dataset=dataset)) == (
        "form_data.groupby[0] is the calculated column 'store_x'"
    )
    assert _not_typed(_read({"metric": calculated}, dataset=dataset)) == (
        "form_data.metric.column is the calculated column 'store_x'"
    )
    assert _not_typed(_read({"adhoc_filters": [subject]}, dataset=dataset)) == (
        "form_data.adhoc_filters[0].subject is the calculated column 'store_x'"
    )
    dataset["metrics"].append({"metric_name": "sx", "expression": "SUM(store_x)"})
    assert _not_typed(_read({"metric": "sx"}, dataset=dataset)) == (
        "form_data.metric: the saved metric reads the calculated column 'store_x'"
    )


@pytest.mark.parametrize(
    ("expression", "agg"),
    [
        ("SUM(revenue)", Aggregation.SUM),
        ('sum("revenue")', Aggregation.SUM),
        ("AVG(revenue)", Aggregation.AVG),
        ("MIN(revenue)", Aggregation.MIN),
        ("MAX(revenue)", Aggregation.MAX),
        ("COUNT(revenue)", Aggregation.COUNT),
        ("COUNT(DISTINCT store_id)", Aggregation.COUNT_DISTINCT),
    ],
)
def test_a_saved_metric_that_is_exactly_one_aggregate_is_typed(
    expression: str, agg: Aggregation
) -> None:
    dataset = _live_dataset()
    dataset["metrics"].append({"metric_name": "m", "expression": expression})

    typed = _typed(_read({"metric": "m"}, dataset=dataset))

    (measure,) = typed.query.measures
    assert (measure.agg, measure.column) == (
        agg,
        "store_id" if "store_id" in expression else "revenue",
    )


@pytest.mark.parametrize(
    "expression",
    [
        "COUNT(*)",
        "SUM(revenue) + 1",
        "SUM(revenue) / SUM(orders)",
        "SUM(t.revenue)",
        "SUM(revenue * 2)",
        "COUNT(DISTINCT store_id, region)",
        "quantile(0.5)(revenue)",
        "",
    ],
)
def test_a_saved_metric_that_is_more_than_one_aggregate_is_not_typed(expression: str) -> None:
    dataset = _live_dataset()
    dataset["metrics"].append({"metric_name": "m", "expression": expression})

    reason = _not_typed(_read({"metric": "m"}, dataset=dataset))

    assert reason.startswith("form_data.metric: the saved metric")


def test_a_saved_metric_that_does_not_parse_refuses_the_dashboard() -> None:
    """Every saved metric is a fragment of the read SQL: one that does not parse spoils it."""
    dataset = _live_dataset()
    dataset["metrics"].append({"metric_name": "m", "expression": "SUM("})

    read = _read({"metric": "m"}, dataset=dataset)

    assert read.spec is None
    (refusal,) = read.record.refusals
    assert refusal.startswith("chart_122: its read SQL is not one plain SELECT: ")


@pytest.mark.parametrize(
    ("aggregate", "agg"),
    [
        ("SUM", Aggregation.SUM),
        ("AVG", Aggregation.AVG),
        ("MIN", Aggregation.MIN),
        ("MAX", Aggregation.MAX),
        ("COUNT", Aggregation.COUNT),
        ("COUNT_DISTINCT", Aggregation.COUNT_DISTINCT),
    ],
)
def test_each_simple_aggregate_has_its_ir_aggregation(aggregate: str, agg: Aggregation) -> None:
    metric = {
        "expressionType": "SIMPLE",
        "aggregate": aggregate,
        "column": {"column_name": "revenue"},
    }

    (measure,) = _typed(_read({"metric": metric})).query.measures

    assert (measure.column, measure.agg) == ("revenue", agg)


def test_a_typed_chart_the_model_does_not_take_is_read_as_sql() -> None:
    """A spec the gate's `bi.contract` refuses would block every run: it is read as SQL."""
    model = MODEL.model_copy(deep=True)
    model.tables[0].columns = [c for c in model.tables[0].columns if c.name != "store_id"]

    read = _read(model=model)

    form, reasons = _form(read)
    assert form is ChartForm.READ_SQL
    assert reasons[0].startswith("the spec does not validate: ")
    assert "store_id" in reasons[0]


def test_a_typed_chart_the_compiler_refuses_is_read_as_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    class Refusing:
        def compile_contract(self, spec: DashboardSpec, model: SemanticModel) -> None:
            if any(chart.query.raw_sql is None for chart in spec.charts):
                raise ValueError("no\nbuilder\x1b[4m for it")

    monkeypatch.setattr(inventory_module, "SupersetContractCompiler", Refusing)

    form, reasons = _form(_read())

    assert (form, reasons) == (
        ChartForm.READ_SQL,
        [
            "the spec does not compile: no builder for it",
            "saved: the last change is not a save within two seconds",
            "composite_sql: the dataset definition is not readable",
        ],
    )


# --- what each viz is typed as ----------------------------------------------------------------


def _metric() -> dict[str, Any]:
    return {"expressionType": "SIMPLE", "aggregate": "SUM", "column": {"column_name": "revenue"}}


def test_a_line_with_series_is_not_typed_and_a_stacked_bar_is() -> None:
    assert _not_typed(_read({"groupby": ["store_id"]}, base=LINE)) == (
        "echarts_timeseries_line with groupby draws series the IR has no form for"
    )
    bar = {"viz_type": "echarts_timeseries_bar", "groupby": ["store_id"]}
    assert _not_typed(_read(bar, base=LINE)).startswith("echarts_timeseries_bar with groupby")

    stacked = _typed(_read({**bar, "stack": "Stack"}, base=LINE))
    assert (stacked.viz, stacked.query.dimensions, stacked.query.series) == (
        Viz.STACKED_BAR,
        ["date"],
        ["store_id"],
    )
    plain = _typed(_read({"viz_type": "echarts_timeseries_bar"}, base=LINE))
    assert (plain.viz, plain.query.series) == (Viz.BAR, [])


def test_an_area_draws_its_groupby_as_series() -> None:
    area = _typed(_read({"viz_type": "echarts_area", "groupby": ["store_id"]}, base=LINE))

    assert (area.viz, area.query.dimensions, area.query.series) == (
        Viz.AREA,
        ["date"],
        ["store_id"],
    )


def test_a_heatmap_reads_its_x_axis_then_its_groupby() -> None:
    """A grain buckets no x-axis that is not temporal."""
    heatmap = _typed(
        _read(
            {
                "viz_type": "heatmap_v2",
                "x_axis": "region",
                "groupby": "store_id",
                "time_grain_sqla": "P1D",
            },
            drop=("row_limit",),
            dataset=_regions(),
        )
    )

    assert (heatmap.viz, heatmap.query.dimensions) == (Viz.HEATMAP, ["region", "store_id"])
    assert (heatmap.query.time_grain, heatmap.query.limit) == (None, 10000)


def test_a_pivot_reads_its_rows_and_columns() -> None:
    pivot = _typed(
        _read(
            {
                "viz_type": "pivot_table_v2",
                "groupbyRows": ["region"],
                "groupbyColumns": ["store_id"],
                "metrics": [_metric()],
                "aggregateFunction": "Sum",
            },
            drop=("groupby", "metric"),
            dataset=_regions(),
        )
    )

    assert (pivot.viz, pivot.query.rows, pivot.query.columns, pivot.query.dimensions) == (
        Viz.PIVOT,
        ["region"],
        ["store_id"],
        [],
    )


def test_a_table_of_aggregates_is_typed() -> None:
    table = _typed(
        _read(
            {"viz_type": "table", "query_mode": "aggregate", "metrics": [_metric()]},
            drop=("metric",),
        )
    )

    assert (table.viz, table.query.dimensions) == (Viz.TABLE, ["store_id"])


@pytest.mark.parametrize(
    ("grain", "expected"),
    [
        ("P1D", TimeGrain.DAY),
        ("P1W", TimeGrain.WEEK),
        ("P1M", TimeGrain.MONTH),
        ("P3M", TimeGrain.QUARTER),
        ("P1Y", TimeGrain.YEAR),
    ],
)
def test_a_time_grain_of_a_temporal_axis_is_the_ir_grain(grain: str, expected: TimeGrain) -> None:
    assert _typed(_read({"time_grain_sqla": grain}, base=LINE)).query.time_grain is expected


def test_a_grain_the_ir_has_not_is_not_typed_and_a_grain_off_time_is_not_read() -> None:
    assert _not_typed(_read({"time_grain_sqla": "PT1H"}, base=LINE)) == (
        "form_data.time_grain_sqla = 'PT1H' has no IR grain"
    )
    # The grain of a pie on a dimension buckets nothing: Superset does not apply it.
    assert _typed(_read({"time_grain_sqla": "P1W"})).query.time_grain is None


@pytest.mark.parametrize(
    ("changes", "drop", "limit"),
    [
        ({}, (), 100),
        ({"viz_type": "big_number_total"}, ("groupby",), 50000),
        (
            {"viz_type": "table", "query_mode": "aggregate", "metrics": [_metric()]},
            ("metric",),
            1000,
        ),
        ({"viz_type": "echarts_timeseries_line", "x_axis": "date"}, ("groupby",), 10000),
        ({"viz_type": "echarts_timeseries_bar", "x_axis": "date"}, ("groupby",), 10000),
        ({"viz_type": "echarts_area", "x_axis": "date"}, ("groupby",), 10000),
        ({"viz_type": "heatmap_v2", "x_axis": "region", "groupby": "store_id"}, (), 10000),
        (
            {"viz_type": "pivot_table_v2", "groupbyRows": ["store_id"], "metrics": [_metric()]},
            ("groupby", "metric"),
            10000,
        ),
    ],
)
def test_a_chart_without_row_limit_reads_the_default_of_its_viz(
    changes: dict[str, Any], drop: tuple[str, ...], limit: int
) -> None:
    """A dashboard fills a key the chart left unsaved with its control's default."""
    chart = _typed(_read(changes, drop=("row_limit", *drop), dataset=_regions()))

    assert chart.query.limit == limit


@pytest.mark.parametrize("row_limit", [None, ""])
def test_a_row_limit_saved_empty_reads_the_superset_row_limit(row_limit: Any) -> None:
    assert _typed(_read({"row_limit": row_limit})).query.limit == 50000


def test_display_keys_do_not_keep_a_chart_from_being_typed() -> None:
    typed = _typed(
        _read(
            {
                "color_scheme": "supersetColors",
                "donut": True,
                "show_labels": False,
                "number_format": "SMART_NUMBER",
                "label_type": "key",
            }
        )
    )

    assert typed.viz is Viz.PIE


# --- read SQL ---------------------------------------------------------------------------------


def test_read_sql_carries_every_fragment_of_the_chart_and_its_dataset_on_lines_of_its_own() -> None:
    dataset = _live_dataset()
    dataset["columns"].append(
        {
            "column_name": "big",
            "expression": "revenue > 100 -- a big day",
            "is_dttm": False,
            "type": None,
        }
    )
    chart = {
        "viz_type": "echarts_timeseries_scatter",
        "adhoc_filters": [
            {"expressionType": "SQL", "clause": "WHERE", "sqlExpression": "store_id IN (1, 2)"}
        ],
        "where": "orders > 0",
        "deep": [{"nested": {"sqlExpression": "max(orders)"}}],
    }

    read = _read(chart, dataset=dataset)

    assert read.spec is not None
    sql = read.spec.charts[0].query.raw_sql
    assert sql is not None
    tree = sqlglot.parse_one(sql, read="clickhouse")
    read_columns = {column.name for column in tree.find_all(exp.Column)}
    # The comment in the calculated column ends with its line: the fragments after it are read.
    assert {"store_id", "orders", "revenue"} <= read_columns
    assert sql.startswith("SELECT\n*,\n(\nstore_id IN (1, 2)\n),\n(\norders > 0\n),\n")
    assert sql.endswith("(\nrevenue > 100 -- a big day\n)\nFROM dm.sales_daily")
    assert sql.count("COUNT(*)") == 1


def test_a_fragment_repeated_is_read_once() -> None:
    read = _read({"viz_type": "word_cloud", "where": "COUNT(*)"})

    assert read.spec is not None
    assert read.spec.charts[0].query.raw_sql == ("SELECT\n*,\n(\nCOUNT(*)\n)\nFROM dm.sales_daily")


def test_a_fragment_that_makes_the_read_more_than_a_select_refuses_the_dashboard() -> None:
    read = _read({"viz_type": "word_cloud", "where": "1) FROM t; DROP TABLE dm.x; SELECT (1"})

    assert read.spec is None
    (refusal,) = read.record.refusals
    assert refusal.startswith("chart_122: its read SQL is not one plain SELECT: ")


def test_a_dataset_sql_with_a_trailing_semicolon_is_read_as_its_select() -> None:
    dataset = {**_live_dataset(26), "sql": "SELECT week FROM dm.w ;\n  "}
    chart = _live_chart(126)

    read = inventory_dashboard("dashboard://d@1", "7", Given([chart], [dataset]), MODEL)

    assert read.spec is not None
    assert read.spec.charts[0].query.raw_sql == (
        "SELECT\n*\nFROM (\nSELECT week FROM dm.w\n) AS virtual_table"
    )


def test_a_table_name_is_quoted_where_clickhouse_needs_it() -> None:
    dataset = {**_live_dataset(), "table_name": "sales daily"}

    read = _read({"viz_type": "word_cloud"}, dataset=dataset)

    assert read.spec is not None
    assert read.spec.charts[0].query.raw_sql == (
        'SELECT\n*,\n(\nCOUNT(*)\n)\nFROM dm."sales daily"'
    )
    assert read.spec.charts[0].query.table == "dm.sales daily"


# --- refusals and answers that are not Superset's ---------------------------------------------


def test_a_chart_whose_dataset_the_dashboard_does_not_hold_refuses_it() -> None:
    chart = _live_chart(PIE)
    chart["form_data"]["datasource"] = "99__table"
    other = _live_chart(LINE)
    other["form_data"]["datasource"] = "25__query"

    read = inventory_dashboard(
        "dashboard://d@1", "7", Given([chart, other], [_live_dataset()]), MODEL
    )

    assert read.spec is None
    assert read.record.refusals == [
        "chart_122: its dataset '99__table' is not among the datasets of the dashboard",
        "chart_123: its dataset '25__query' is not among the datasets of the dashboard",
    ]


def test_a_dashboard_without_charts_is_refused() -> None:
    read = inventory_dashboard("dashboard://d@1", "7", Given([], []), MODEL)

    assert (read.spec, read.record.refusals) == (None, ["the dashboard holds no chart"])


def test_charts_are_written_in_the_order_of_their_superset_ids() -> None:
    charts = [_live_chart(LINE), _live_chart(PIE)]

    read = inventory_dashboard("dashboard://d@1", "7", Given(charts, [_live_dataset()]), MODEL)

    assert read.spec is not None
    assert [chart.id for chart in read.spec.charts] == ["chart_122", "chart_123"]


def test_a_chart_without_a_title_is_titled_by_its_id() -> None:
    chart = {**_live_chart(PIE), "slice_name": None}

    read = inventory_dashboard("dashboard://d@1", "7", Given([chart], [_live_dataset()]), MODEL)

    assert read.spec is not None
    assert read.spec.charts[0].title == "chart_122"


@pytest.mark.parametrize(
    ("charts", "datasets", "message"),
    [
        ([{"slice_name": "x", "form_data": {}}], [], "an answer has no integer 'id'"),
        ([{"id": True, "form_data": {}}], [], "an answer has no integer 'id'"),
        ([{"id": 1, "form_data": "{}"}], [], "chart_1: form_data is not an object"),
        ([], [{"name": "x"}], "an answer has no integer 'id'"),
        (
            [_live_chart(PIE)],
            [{**_live_dataset(), "columns": "date"}],
            "an answer's 'columns' is not a list of objects",
        ),
        (
            [_live_chart(126)],
            [{**_live_dataset(26), "sql": 7}],
            "chart_126: the dataset sql is not text",
        ),
    ],
)
def test_an_answer_not_shaped_as_superset_answers_is_said(
    charts: list[Any], datasets: list[Any], message: str
) -> None:
    with pytest.raises(SupersetAnswerError, match="^" + message.replace("(", r"\(")):
        inventory_dashboard("dashboard://d@1", "7", Given(charts, datasets), MODEL)


# --- the spec file ----------------------------------------------------------------------------


def test_a_spec_file_reads_back_as_the_spec_and_keeps_sql_as_a_block() -> None:
    read = inventory_dashboard("dashboard://weekly_revenue@1", "12", Recorded(), MODEL)
    assert read.spec is not None

    text = dump_spec(read.spec)

    assert b"\r" not in text
    assert DashboardSpec.model_validate(yaml.safe_load(text)) == read.spec
    assert "    raw_sql: |-\n      SELECT\n      *,\n" in text.decode("utf-8")
    assert "title: Выручка\n" in text.decode("utf-8")


@pytest.mark.parametrize("sql", ["SELECT 1 \nFROM t", "SELECT 1\r\nFROM t", "SELECT '\t'\nFROM t"])
def test_sql_yaml_cannot_keep_as_a_block_still_reads_back(sql: str) -> None:
    read = _read({"viz_type": "word_cloud"})
    assert read.spec is not None
    spec = read.spec.model_copy(deep=True)
    spec.charts[0].query.raw_sql = sql

    assert DashboardSpec.model_validate(yaml.safe_load(dump_spec(spec))) == spec


# --- the use case -----------------------------------------------------------------------------


def _semantic(tmp_path: Path) -> Path:
    root = tmp_path / "semantic"
    root.mkdir()
    (root / "model.yaml").write_text(
        yaml.safe_dump(MODEL.model_dump(mode="json", exclude_defaults=True)), encoding="utf-8"
    )
    return root


def _request(tmp_path: Path, pairs: Sequence[tuple[str, str]], **kwargs: Any) -> InventoryRequest:
    source = kwargs.pop("source", Recorded())
    return InventoryRequest(
        semantic=kwargs.pop("semantic", _semantic(tmp_path)),
        dashboards=pairs,
        output=kwargs.pop("output", tmp_path / "out"),
        source=lambda: source,
    )


def test_the_inventory_writes_each_spec_where_the_gate_reads_its_ref(tmp_path: Path) -> None:
    report = inventory(
        _request(
            tmp_path,
            [("dashboard://sales/weekly_revenue@1", "d6-live"), ("dashboard://misc@2", "11")],
        )
    )

    out = tmp_path / "out"
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == [
        INVENTORY_REPORT,
        "sales/weekly_revenue@1.yaml",
    ]
    assert [(d.ref, d.written) for d in report.dashboards] == [
        ("dashboard://sales/weekly_revenue@1", "sales/weekly_revenue@1.yaml"),
        ("dashboard://misc@2", None),
    ]
    assert [d.ref for d in report.refused] == ["dashboard://misc@2"]
    written = (out / INVENTORY_REPORT).read_bytes()
    assert written.endswith(b"}\n") and b"\r" not in written
    assert json.loads(written) == report.model_dump(mode="json")
    assert report.superset_version == "4.1.2"
    spec = DashboardSpec.model_validate(
        yaml.safe_load((out / "sales" / "weekly_revenue@1.yaml").read_bytes())
    )
    assert len(spec.charts) == 6
    assert [path.name for path in tmp_path.iterdir()] == sorted(["out", "semantic"]) or sorted(
        path.name for path in tmp_path.iterdir()
    ) == ["out", "semantic"]


@pytest.mark.parametrize(
    ("pairs", "message"),
    [
        ([], "name at least one dashboard: --dashboard REF=SUPERSET_ID"),
        ([("weekly", "1")], "--dashboard 'weekly': malformed ref"),
        ([("metric://sales/revenue@1", "1")], "--dashboard 'metric://sales/revenue@1': expected"),
        ([("dashboard://weekly", "1")], "--dashboard 'dashboard://weekly': expected"),
        ([("dashboard://weekly@1", " ")], "--dashboard 'dashboard://weekly@1': name the Superset"),
        (
            [("dashboard://weekly@1", "1"), ("dashboard://weekly@1", "2")],
            "--dashboard 'dashboard://weekly@1' is named twice",
        ),
    ],
)
def test_arguments_the_inventory_cannot_take_ask_superset_nothing(
    tmp_path: Path, pairs: list[tuple[str, str]], message: str
) -> None:
    asked: list[bool] = []

    def source() -> Recorded:
        asked.append(True)
        return Recorded()

    request = InventoryRequest(
        semantic=_semantic(tmp_path), dashboards=pairs, output=tmp_path / "out", source=source
    )
    with pytest.raises(VerifyError) as raised:
        inventory(request)

    assert str(raised.value).startswith(message)
    assert asked == []
    assert not (tmp_path / "out").exists()


def test_an_output_that_exists_is_not_written_into(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()

    with pytest.raises(VerifyError, match=r"output already exists: .*writes a new directory"):
        inventory(_request(tmp_path, [("dashboard://w@1", "12")]))

    assert list((tmp_path / "out").iterdir()) == []


def test_a_model_the_gate_cannot_read_stops_the_inventory(tmp_path: Path) -> None:
    with pytest.raises(VerifyError, match=r"cannot read .*model.yaml"):
        inventory(_request(tmp_path, [("dashboard://w@1", "12")], semantic=tmp_path))


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            SupersetSourceError("GET /api/v1/dashboard/12/charts -> HTTP 500"),
            "cannot read Superset dashboard '12': GET /api/v1/dashboard/12/charts -> HTTP 500",
        ),
        (
            SupersetAnswerError("an answer has no integer 'id'"),
            "Superset dashboard '12': an answer has no integer 'id'",
        ),
    ],
)
def test_superset_that_does_not_answer_leaves_no_output(
    tmp_path: Path, error: Exception, message: str
) -> None:
    class Failing(Recorded):
        def charts(self, ident: str) -> Sequence[Mapping[str, Any]]:
            if ident == "12":
                raise error
            return super().charts(ident)

    with pytest.raises(VerifyError) as raised:
        inventory(
            _request(
                tmp_path, [("dashboard://a@1", "10"), ("dashboard://w@1", "12")], source=Failing()
            )
        )

    assert str(raised.value) == message
    assert sorted(path.name for path in tmp_path.iterdir()) == ["semantic"]


def test_a_write_that_fails_leaves_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Path, data: bytes) -> int:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "write_bytes", refuse)

    with pytest.raises(VerifyError, match=r"cannot write the inventory to .*Permission denied"):
        inventory(_request(tmp_path, [("dashboard://w@1", "12")]))

    monkeypatch.undo()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["semantic"]


# --- the gate reads what the inventory wrote ---------------------------------------------------


def test_the_gate_names_typed_tiles_as_the_metric_and_read_sql_as_read_in_part(
    tmp_path: Path,
) -> None:
    """The ClickHouse pilot with revenue read in UTC: its dashboard is the inventoried one."""
    pilot = tmp_path / "pilot"
    shutil.copytree(PILOT, pilot, ignore=shutil.ignore_patterns("evidence", "dashboards"))
    revenue = pilot / "candidate" / "metrics" / "revenue.yaml"
    text = revenue.read_text(encoding="utf-8")
    assert "timezone: Europe/Moscow" in text
    revenue.write_text(text.replace("timezone: Europe/Moscow", "timezone: UTC"), encoding="utf-8")
    inventory(
        InventoryRequest(
            semantic=pilot / "candidate",
            dashboards=[("dashboard://weekly_revenue@1", "d6-live")],
            output=tmp_path / "dashboards",
            source=Recorded,
        )
    )

    answer = impact(
        ImpactRequest(
            contract_paths=[pilot / "contracts"],
            base=pilot / "base",
            candidate=pilot / "candidate",
            dashboards_dir=tmp_path / "dashboards",
            product_id="decision://sales/weekly_revenue",
        )
    )

    (change,) = answer.impact.impacts
    assert change.subject == "metric://sales/revenue"
    assert [(chart.chart, chart.relation.value) for chart in change.charts] == [
        ("chart_121", "computes_metric"),
        ("chart_122", "computes_metric"),
        ("chart_123", "computes_metric"),
    ]
    gaps = {(gap.code, gap.subject.rpartition("#")[2]) for gap in answer.impact.chart_gaps}
    assert {("chart.sql_partial", f"chart_{n}") for n in (124, 125, 126)} <= gaps


# --- the REST source --------------------------------------------------------------------------


def _client(handler: Any) -> SupersetClient:
    http = httpx.Client(base_url="http://superset.test", transport=httpx.MockTransport(handler))
    return SupersetClient("http://superset.test", "reader", "pw", http=http)


def test_the_rest_source_reads_by_get_after_its_login() -> None:
    seen: list[tuple[str, str]] = []
    answers = _answers(LIVE)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode()))
        path = request.url.path
        if path == "/api/v1/chart/":
            return httpx.Response(200, json={"result": []})
        if path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "t"})
        if path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "c"})
        if path.endswith("/charts"):
            return httpx.Response(200, json={"result": answers["charts"]})
        if path.endswith("/datasets"):
            return httpx.Response(200, json={"result": answers["datasets"]})
        return httpx.Response(200, json={"result": answers["dashboard"]})

    source = RestSupersetSource(_client(handler))
    read = inventory_dashboard("dashboard://w@1", "d6 live/x", source, MODEL)

    assert read.spec is not None
    assert seen == [
        ("POST", "/api/v1/security/login"),
        ("GET", "/api/v1/security/csrf_token/"),
        ("GET", "/api/v1/dashboard/d6%20live%2Fx"),
        ("GET", "/api/v1/dashboard/d6%20live%2Fx/charts"),
        ("GET", "/api/v1/dashboard/d6%20live%2Fx/datasets"),
        (
            "GET",
            "/api/v1/chart/?q=%28columns%3A%21%28id%2Cchanged_on_utc%2Clast_saved_at%29"
            "%2Cfilters%3A%21%28%28col%3Adashboards%2Copr%3Arel_m_m%2Cvalue%3A12%29%29"
            "%2Cpage_size%3A12%29",
        ),
        ("GET", "/api/v1/dataset/25"),
        ("GET", "/api/v1/dataset/26"),
    ]


@pytest.mark.parametrize(
    ("respond", "error", "message"),
    [
        (
            lambda: httpx.Response(404, json={"message": "Not found"}),
            SupersetSourceError,
            "GET /api/v1/dashboard/7 -> HTTP 404",
        ),
        (
            lambda: httpx.Response(200, content=b"<html>"),
            SupersetSourceError,
            "GET /api/v1/dashboard/7: the answer is not Superset's JSON",
        ),
        (
            lambda: httpx.Response(200, json={"result": []}),
            SupersetAnswerError,
            "dashboard '7': the answer holds no object",
        ),
    ],
)
def test_the_rest_source_says_what_superset_did_not_answer(
    respond: Any, error: type[Exception], message: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "t"})
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "c"})
        response: httpx.Response = respond()
        return response

    with pytest.raises(error) as raised:
        RestSupersetSource(_client(handler)).dashboard("7")

    assert str(raised.value) == message


def test_the_rest_source_says_a_refused_login_and_an_unreachable_superset() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(SupersetSourceError, match=r"^login failed: HTTP 401$"):
        RestSupersetSource(_client(refused)).charts("7")

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(
        SupersetSourceError, match=r"^GET /api/v1/dashboard/7/datasets: ConnectError$"
    ):
        RestSupersetSource(_client(unreachable)).datasets("7")

    def not_a_list(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "t"})
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "c"})
        return httpx.Response(200, json={"result": [1]})

    with pytest.raises(SupersetAnswerError, match="holds no list of objects"):
        RestSupersetSource(_client(not_a_list)).charts("7")


# --- the command ------------------------------------------------------------------------------


def _command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *dashboards: str, json_out: bool = False
) -> tuple[int, str, str]:
    monkeypatch.setattr(inventory_module, "RestSupersetSource", lambda client: Recorded())
    argv = ["decision", "inventory", str(_semantic(tmp_path)), "--output", str(tmp_path / "o")]
    for dashboard in dashboards:
        argv += ["--dashboard", dashboard]
    if json_out:
        argv.append("--json")
    return main(argv), "", ""


def test_the_command_names_what_it_wrote_and_what_it_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = _command(
        tmp_path, monkeypatch, "dashboard://weekly_revenue@1=d6-live", "dashboard://big@1=11"
    )

    out, err = capsys.readouterr()
    assert (code, err) == (3, "")
    assert out.splitlines() == [
        "wrote    weekly_revenue@1.yaml  "
        "(d6-live: 3 typed, 0 rendered SQL, 0 composite SQL, 3 read as SQL)",
        "refused  dashboard://big@1  (11)",
        f"  - the dashboard holds 21 charts; a spec holds {MAX_CHARTS}",
        f"report   {tmp_path / 'o' / INVENTORY_REPORT}",
    ]


def test_the_command_exits_0_when_every_dashboard_is_written_and_says_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = _command(tmp_path, monkeypatch, "dashboard://w@1=12", json_out=True)

    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    report = json.loads(out)
    assert report == json.loads((tmp_path / "o" / INVENTORY_REPORT).read_text(encoding="utf-8"))
    assert [d["written"] for d in report["dashboards"]] == ["w@1.yaml"]


def test_the_command_escapes_what_superset_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    chart = _live_chart(PIE)
    chart["form_data"]["datasource"] = "9\x1b[2J__table"
    monkeypatch.setattr(
        inventory_module, "RestSupersetSource", lambda client: Given([chart], [_live_dataset()])
    )

    code = main(
        [
            "decision",
            "inventory",
            str(_semantic(tmp_path)),
            "--output",
            str(tmp_path / "o"),
            "--dashboard",
            "dashboard://w@1=x\x07",
        ]
    )

    out, _ = capsys.readouterr()
    assert code == 3
    assert "\x1b" not in out and "\x07" not in out
    assert "(x\\x07)" in out
    assert "'9\\x1b[2J__table'" in out


@pytest.mark.parametrize(
    ("dashboard", "message"),
    [
        ("dashboard://w@1", "error: --dashboard 'dashboard://w@1': expected REF=SUPERSET_ID"),
        ("w@1=12", "error: --dashboard 'w@1': malformed ref"),
    ],
)
def test_the_command_exits_2_on_what_it_cannot_take(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dashboard: str,
    message: str,
) -> None:
    code, _, _ = _command(tmp_path, monkeypatch, dashboard)

    out, err = capsys.readouterr()
    assert (code, out) == (2, "")
    assert err.startswith(message)
    assert err.endswith("\n") and err.count("\n") == 1
    assert not (tmp_path / "o").exists()


def _charts(count: int) -> list[dict[str, Any]]:
    """*count* copies of the live pie, each a chart of its own Superset id."""
    made = []
    for ident in range(1, count + 1):
        chart = _live_chart(PIE)
        chart["id"] = ident
        made.append(chart)
    return made


def test_a_dashboard_of_twelve_charts_is_written_and_of_thirteen_refused() -> None:
    twelve = inventory_dashboard(
        "dashboard://d@1", "7", Given(_charts(12), [_live_dataset()]), MODEL
    )
    assert twelve.spec is not None
    assert len(twelve.spec.charts) == 12

    thirteen = inventory_dashboard(
        "dashboard://d@1", "7", Given(_charts(13), [_live_dataset()]), MODEL
    )
    assert (thirteen.spec, thirteen.record.refusals) == (
        None,
        ["the dashboard holds 13 charts; a spec holds 12"],
    )


def test_a_chart_answered_twice_refuses_the_dashboard() -> None:
    """Each chart passes on its own; what only the dashboard can fail is asked of it too."""
    chart = _live_chart(PIE)

    read = inventory_dashboard(
        "dashboard://d@1", "7", Given([chart, copy.deepcopy(chart)], [_live_dataset()]), MODEL
    )

    assert read.spec is None
    (refusal,) = read.record.refusals
    assert refusal.startswith("the spec does not validate: chart ids are not unique")


@pytest.mark.parametrize(
    "expression", ["MAX(revenue, orders)", "COUNT(revenue, orders)", "COUNT(``.*)"]
)
def test_a_saved_metric_not_of_one_named_column_is_not_typed(expression: str) -> None:
    """ClickHouse reads `MAX(a, b)` as one aggregate with a second argument, and `COUNT(``.*)`
    as a star under an empty qualifier: neither is AGG(column)."""
    dataset = _live_dataset()
    dataset["metrics"].append({"metric_name": "m", "expression": expression})

    assert _not_typed(_read({"metric": "m"}, dataset=dataset)) == (
        "form_data.metric: the saved metric 'm' is not AGG(column)"
    )


@pytest.mark.parametrize("operator", [">", "<", "NOT IN", "LIKE"])
def test_an_operator_the_ir_has_no_op_for_reads_the_chart_as_sql(operator: str) -> None:
    """`>` is not `>=`: an operator outside the five the IR has is never mapped to a near one."""
    chart = _live_chart(PIE)
    adhoc = {
        "expressionType": "SIMPLE",
        "clause": "WHERE",
        "operator": operator,
        "subject": "store_id",
        "comparator": 3,
    }

    assert _not_typed(_read({"adhoc_filters": [*chart["form_data"]["adhoc_filters"], adhoc]})) == (
        f"form_data.adhoc_filters[1] filters with {operator!r}"
    )


@pytest.mark.parametrize(
    "kind", [{"expressionType": "SQL", "sqlExpression": ""}, {"expressionType": None}]
)
def test_a_filter_not_simple_is_not_typed_by_the_simple_fields_it_keeps(
    kind: dict[str, Any],
) -> None:
    """A filter is typed from its simple fields only when it says it is a simple one."""
    chart = _live_chart(PIE)
    kept = {"clause": "WHERE", "operator": "==", "subject": "store_id", "comparator": 1, **kind}

    assert _not_typed(_read({"adhoc_filters": [*chart["form_data"]["adhoc_filters"], kept]})) == (
        "form_data.adhoc_filters[1] is not a simple filter"
    )


@pytest.mark.parametrize("kind", [{"expressionType": "SQL", "sqlExpression": ""}, {}])
def test_a_metric_not_simple_is_not_typed_by_the_simple_fields_it_keeps(
    kind: dict[str, Any],
) -> None:
    metric = {"aggregate": "SUM", "column": {"column_name": "revenue"}, **kind}

    assert _not_typed(_read({"metric": metric})) == "form_data.metric is not a simple aggregate"


def test_a_row_limit_at_the_bounds_of_the_ir_is_typed() -> None:
    assert _typed(_read({"row_limit": 50000})).query.limit == 50000
    assert _typed(_read({"row_limit": 1})).query.limit == 1


@pytest.mark.parametrize("row_limit", [0, -1, 50001, True])
def test_a_row_limit_past_the_bounds_of_the_ir_is_named(row_limit: Any) -> None:
    assert _not_typed(_read({"row_limit": row_limit})) == (
        f"form_data.row_limit = {row_limit!r} is outside the IR limit"
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("rolling_type", "None"),
        ("rolling_type", "null"),
        ("contributionMode", None),
        ("contributionMode", ""),
        ("contributionMode", False),
        ("comparison_type", "values"),
        ("forecastEnabled", False),
        ("time_compare", None),
        ("time_compare", []),
        ("resample_rule", ""),
        ("aggregateFunction", "Sum"),
    ],
)
def test_a_post_processing_key_at_its_no_op_value_keeps_a_chart_typed(key: str, value: Any) -> None:
    assert _typed(_read({key: value})).viz is Viz.PIE


@pytest.mark.parametrize(
    ("key", "value"),
    [
        # Superset tests contributionMode for truth: the text "null" is a mode.
        ("contributionMode", "null"),
        ("contributionMode", "row"),
        # ensureIsArray: "" and false are a list of one offset.
        ("time_compare", ""),
        ("time_compare", False),
        ("time_compare", ["1 week ago"]),
        ("aggregateFunction", None),
        ("aggregateFunction", ""),
        ("aggregateFunction", "Average"),
        ("comparison_type", ["values"]),
        ("rolling_type", "cumsum"),
        ("forecastEnabled", True),
        ("resample_method", "asfreq"),
    ],
)
def test_a_post_processing_key_at_any_other_value_is_not_typed(key: str, value: Any) -> None:
    assert _not_typed(_read({key: value})) == (
        f"form_data.{key} = {value!r} post-processes the result"
    )


@pytest.mark.parametrize(
    ("key", "value"), [("url_params", {"standalone": "1"}), ("dashboards", [12])]
)
def test_keys_that_name_the_chart_do_not_keep_it_from_being_typed(key: str, value: Any) -> None:
    assert _typed(_read({key: value})).viz is Viz.PIE


def test_an_in_filter_of_booleans_is_not_typed() -> None:
    chart = _live_chart(PIE)
    adhoc = {
        "expressionType": "SIMPLE",
        "clause": "WHERE",
        "operator": "IN",
        "subject": "store_id",
        "comparator": [True],
    }

    assert _not_typed(_read({"adhoc_filters": [*chart["form_data"]["adhoc_filters"], adhoc]})) == (
        "form_data.adhoc_filters[1] compares with a value the IR has no form for"
    )


def test_a_fragment_is_read_once_whatever_space_surrounds_it() -> None:
    read = _read({"viz_type": "word_cloud", "where": "  COUNT(*)\n"})

    assert read.spec is not None
    assert read.spec.charts[0].query.raw_sql == ("SELECT\n*,\n(\nCOUNT(*)\n)\nFROM dm.sales_daily")


def test_a_datasource_only_prefixed_as_a_table_is_no_dataset_of_the_dashboard() -> None:
    chart = _live_chart(PIE)
    chart["form_data"]["datasource"] = "25__tables"

    read = inventory_dashboard("dashboard://d@1", "7", Given([chart], [_live_dataset()]), MODEL)

    assert read.spec is None
    assert read.record.refusals == [
        "chart_122: its dataset '25__tables' is not among the datasets of the dashboard"
    ]


def test_a_spec_file_leaves_out_the_fields_at_their_defaults() -> None:
    read = inventory_dashboard("dashboard://weekly_revenue@1", "12", Recorded(), MODEL)
    assert read.spec is not None

    written = yaml.safe_load(dump_spec(read.spec))

    assert written == read.spec.model_dump(mode="json", exclude_defaults=True)
    assert "filters" not in written["charts"][0]["query"]


def test_a_calculated_column_on_an_axis_or_a_pivot_row_is_not_typed() -> None:
    dataset = _live_dataset()
    dataset["columns"].append(
        {"column_name": "store_x", "expression": "store_id * 2", "is_dttm": False, "type": None}
    )

    assert _not_typed(_read({"x_axis": "store_x"}, base=LINE, dataset=dataset)) == (
        "form_data.x_axis[0] is the calculated column 'store_x'"
    )
    pivot = {
        "viz_type": "pivot_table_v2",
        "groupbyRows": ["store_x"],
        "groupbyColumns": ["store_id"],
        "metrics": [_metric()],
    }
    assert _not_typed(_read(pivot, drop=("groupby", "metric"), dataset=dataset)) == (
        "form_data.groupbyRows[0] is the calculated column 'store_x'"
    )
    pivot = {**pivot, "groupbyRows": ["store_id"], "groupbyColumns": ["store_x"]}
    assert _not_typed(_read(pivot, drop=("groupby", "metric"), dataset=dataset)) == (
        "form_data.groupbyColumns[0] is the calculated column 'store_x'"
    )


def test_an_output_taken_or_a_model_unread_asks_superset_nothing(tmp_path: Path) -> None:
    asked: list[bool] = []

    def source() -> Recorded:
        asked.append(True)
        return Recorded()

    (tmp_path / "out").mkdir()
    taken = InventoryRequest(
        semantic=_semantic(tmp_path),
        dashboards=[("dashboard://w@1", "12")],
        output=tmp_path / "out",
        source=source,
    )
    with pytest.raises(VerifyError, match=r"^output already exists: "):
        inventory(taken)
    unread = InventoryRequest(
        semantic=tmp_path,
        dashboards=[("dashboard://w@1", "12")],
        output=tmp_path / "new",
        source=source,
    )
    with pytest.raises(VerifyError, match=r"cannot read .*model.yaml"):
        inventory(unread)

    assert asked == []


def test_an_output_made_while_superset_is_read_is_not_written_over(tmp_path: Path) -> None:
    out = tmp_path / "out"

    def source() -> Recorded:
        out.mkdir()
        return Recorded()

    request = InventoryRequest(
        semantic=_semantic(tmp_path),
        dashboards=[("dashboard://w@1", "12")],
        output=out,
        source=source,
    )
    with pytest.raises(VerifyError, match=r"^output already exists: .*writes a new directory"):
        inventory(request)

    assert list(out.iterdir()) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out", "semantic"]


def test_the_inventory_is_staged_beside_its_output_until_it_is_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    seen: list[bool] = []
    written: list[Path] = []
    write_bytes = Path.write_bytes

    def spy(self: Path, data: bytes) -> int:
        seen.append(out.exists())
        written.append(self)
        return write_bytes(self, data)

    request = _request(tmp_path, [("dashboard://w@1", "12")])
    monkeypatch.setattr(Path, "write_bytes", spy)
    inventory(request)
    monkeypatch.undo()

    assert seen and not any(seen)
    # Each file went into one directory beside the output, named after it.
    (staging,) = {path.relative_to(tmp_path).parts[0] for path in written}
    assert staging.startswith(".out.") and len(staging) == len(".out.") + 16
    assert sorted(path.name for path in out.iterdir()) == [INVENTORY_REPORT, "w@1.yaml"]


def test_the_inventory_makes_the_directories_above_its_output(tmp_path: Path) -> None:
    out = tmp_path / "a" / "b" / "out"

    inventory(_request(tmp_path, [("dashboard://w@1", "12")], output=out))

    assert (out / "w@1.yaml").is_file()


def test_the_command_counts_and_escapes_a_written_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        inventory_module,
        "RestSupersetSource",
        lambda client: Given([_live_chart(PIE)], [_live_dataset()]),
    )
    output = tmp_path / "o\x7f"

    code = main(
        [
            "decision",
            "inventory",
            str(_semantic(tmp_path)),
            "--output",
            str(output),
            "--dashboard",
            "dashboard://w@1=x\x07",
        ]
    )

    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "wrote    w@1.yaml  (x\\x07: 1 typed, 0 rendered SQL, 0 composite SQL, 0 read as SQL)",
        "report   " + str(output / INVENTORY_REPORT).replace("\x7f", "\\x7f"),
    ]


def test_a_refusal_of_sql_that_does_not_parse_quotes_none_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """sqlglot quotes the SQL it could not parse; the refusal names the place, never the SQL."""
    chart = _live_chart(PIE)
    chart["form_data"].update({"viz_type": "word_cloud", "where": "SUM(\x07"})
    monkeypatch.setattr(
        inventory_module, "RestSupersetSource", lambda client: Given([chart], [_live_dataset()])
    )

    code = main(
        [
            "decision",
            "inventory",
            str(_semantic(tmp_path)),
            "--output",
            str(tmp_path / "o"),
            "--dashboard",
            "dashboard://w@1=7",
        ]
    )

    out, err = capsys.readouterr()
    assert (code, err) == (3, "")
    (refusal,) = [line for line in out.splitlines() if line.startswith("  - ")]
    assert refusal == (
        "  - chart_122: its read SQL is not one plain SELECT: it does not parse (line 9, column 4)"
    )
    assert "\x07" not in out and "SUM(" not in out


def test_the_command_keeps_parser_warnings_off_its_error_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """sqlglot warns with the SQL of a statement it reads as a `Command`; no `decision` command
    configures logging, so `logging.lastResort` would print the warning on stderr."""
    # Pytest's root handlers would otherwise hide the ordinary CLI lastResort output.
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    monkeypatch.setattr(logging.getLogger("sqlglot"), "handlers", [])
    chart = _live_chart(PIE)
    chart["form_data"].update(
        {"viz_type": "word_cloud", "where": "1) FROM t; SHOW TABLES; SELECT (1"}
    )
    monkeypatch.setattr(
        inventory_module, "RestSupersetSource", lambda client: Given([chart], [_live_dataset()])
    )

    code = main(
        [
            "decision",
            "inventory",
            str(_semantic(tmp_path)),
            "--output",
            str(tmp_path / "o"),
            "--dashboard",
            "dashboard://w@1=7",
        ]
    )

    out, err = capsys.readouterr()
    assert (code, err) == (3, "")
    assert (
        "  - chart_122: its read SQL is not one plain SELECT: expected exactly one statement, got 3"
        in out.splitlines()
    )


def test_the_command_closes_its_superset_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []
    close = SupersetClient.close

    def spy(self: SupersetClient) -> None:
        closed.append(True)
        close(self)

    monkeypatch.setattr(SupersetClient, "close", spy)

    code, _, _ = _command(tmp_path, monkeypatch, "dashboard://w@1=12")

    assert (code, closed) == (0, [True])


def test_a_superset_address_that_is_not_a_url_is_said_without_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AUTO_BI_SUPERSET_URL", "http://reader:hunter2@[::1")
    get_settings.cache_clear()
    try:
        code = main(
            [
                "decision",
                "inventory",
                str(_semantic(tmp_path)),
                "--output",
                str(tmp_path / "o"),
                "--dashboard",
                "dashboard://w@1=12",
            ]
        )
    finally:
        get_settings.cache_clear()

    out, err = capsys.readouterr()
    assert (code, out) == (2, "")
    assert err == ("error: cannot reach Superset: AUTO_BI_SUPERSET_URL is not a URL (InvalidURL)\n")
    assert not (tmp_path / "o").exists()


def test_an_interrupted_write_leaves_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(self: Path, data: bytes) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "write_bytes", interrupt)

    with pytest.raises(KeyboardInterrupt):
        inventory(_request(tmp_path, [("dashboard://w@1", "12")]))

    monkeypatch.undo()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["semantic"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_output_is_made_under_the_umask_as_a_directory_is(tmp_path: Path) -> None:
    out = tmp_path / "out"
    previous = os.umask(0o022)
    try:
        inventory(_request(tmp_path, [("dashboard://w@1", "12")], output=out))
    finally:
        os.umask(previous)

    assert stat.S_IMODE(out.stat().st_mode) == 0o755


# --- what a dashboard and Superset 4.1.2 add to a chart's query --------------------------------


def _native(**changes: Any) -> dict[str, Any]:
    native: dict[str, Any] = {
        "id": "NATIVE_FILTER-a",
        "type": "NATIVE_FILTER",
        "name": "Store",
        "filterType": "filter_select",
        "targets": [{"datasetId": 25, "column": {"name": "store_id"}}],
        "defaultDataMask": {"extraFormData": {}, "filterState": {}},
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    }
    native.update(changes)
    return native


def _filtered(*natives: Mapping[str, Any], metadata: Any = None) -> DashboardRead:
    dashboard = {
        "id": 7,
        "dashboard_title": "Given",
        "changed_on": "x",
        "json_metadata": (
            json.dumps({"native_filter_configuration": list(natives)})
            if metadata is None
            else metadata
        ),
    }
    source = Given([_live_chart(PIE), _live_chart(LINE)], [_live_dataset()], dashboard)
    return inventory_dashboard("dashboard://live@1", "7", source, MODEL)


def _forms(read: DashboardRead) -> dict[int, tuple[ChartForm, list[str]]]:
    assert read.spec is not None, read.record.refusals
    return {chart.superset_id: (chart.form, chart.reasons) for chart in read.record.charts}


def test_a_chart_in_the_scope_of_a_native_filter_is_read_as_sql() -> None:
    read = _filtered(_native(scope={"rootPath": ["ROOT_ID"], "excluded": [LINE]}))

    assert _forms(read) == {
        PIE: (
            ChartForm.READ_SQL,
            [
                "the dashboard's native filter 'Store' applies to it",
                "plain: a native dashboard filter applies to the chart",
                "composite_sql: a native dashboard filter applies to the chart",
            ],
        ),
        LINE: (ChartForm.TYPED, []),
    }


def test_a_divider_filters_nothing_and_a_chart_listed_in_scope_is_in_it() -> None:
    read = _filtered(
        {"id": "NATIVE_FILTER_DIVIDER-b", "type": "DIVIDER", "title": "Section"},
        _native(scope={"excluded": [PIE, LINE]}, chartsInScope=[LINE]),
    )

    assert {ident: form for ident, (form, _) in _forms(read).items()} == {
        PIE: ChartForm.TYPED,
        LINE: ChartForm.READ_SQL,
    }


@pytest.mark.parametrize(
    "scope",
    [
        None,
        "ROOT_ID",
        {"excluded": str(PIE)},
        {"excluded": PIE},
        {"excluded": [str(PIE), str(LINE)]},
    ],
)
def test_a_scope_the_inventory_cannot_read_takes_in_every_chart(scope: Any) -> None:
    read = _filtered(_native(scope=scope))

    assert {form for form, _ in _forms(read).values()} == {ChartForm.READ_SQL}


def test_a_scope_names_a_chart_by_an_integer_only() -> None:
    """JSON `true` and `1.0` name no chart, though Python counts both equal to 1."""
    chart = _live_chart(PIE)
    chart["id"] = 1
    natives = [_native(scope={"excluded": [True, 1.0, "1"]}, chartsInScope=[])]
    dashboard = {
        "id": 7,
        "dashboard_title": "Given",
        "changed_on": "x",
        "json_metadata": json.dumps({"native_filter_configuration": natives}),
    }

    read = inventory_dashboard(
        "dashboard://live@1", "7", Given([chart], [_live_dataset()], dashboard), MODEL
    )

    assert _forms(read) == {
        1: (
            ChartForm.READ_SQL,
            [
                "the dashboard's native filter 'Store' applies to it",
                "plain: a native dashboard filter applies to the chart",
                "composite_sql: a native dashboard filter applies to the chart",
            ],
        )
    }


@pytest.mark.parametrize(
    ("changes", "name"),
    [({"name": ""}, "NATIVE_FILTER-a"), ({"name": None, "id": None}, "(unnamed)")],
)
def test_a_native_filter_without_a_name_is_named_by_its_id(
    changes: dict[str, Any], name: str
) -> None:
    read = _filtered(_native(**changes))

    assert _forms(read)[PIE][1] == [
        f"the dashboard's native filter {name!r} applies to it",
        "plain: a native dashboard filter applies to the chart",
        "composite_sql: a native dashboard filter applies to the chart",
    ]


@pytest.mark.parametrize("metadata", ["", "{}", json.dumps({"color_scheme": "x"})])
def test_a_dashboard_without_native_filters_types_its_charts(metadata: str) -> None:
    read = _filtered(metadata=metadata)

    assert {form for form, _ in _forms(read).values()} == {ChartForm.TYPED}


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"native_filter_configuration": []}, "the dashboard's json_metadata is not text"),
        ("{", "the dashboard's json_metadata is not JSON"),
        ("[]", "the dashboard's json_metadata is not an object"),
        (
            json.dumps({"native_filter_configuration": {}}),
            "an answer's 'native_filter_configuration' is not a list of objects",
        ),
    ],
)
def test_json_metadata_not_shaped_as_superset_answers_is_said(metadata: Any, message: str) -> None:
    with pytest.raises(SupersetAnswerError) as raised:
        _filtered(metadata=metadata)

    assert str(raised.value) == message


def _on_date(operator: str = ">=", comparator: str = "2024-01-01") -> dict[str, Any]:
    return {
        "clause": "WHERE",
        "comparator": comparator,
        "expressionType": "SIMPLE",
        "operator": operator,
        "subject": "date",
    }


def _ship_date() -> dict[str, Any]:
    dataset = _live_dataset()
    dataset["columns"].append(
        {"column_name": "ship_date", "expression": None, "is_dttm": True, "type": "DATE"}
    )
    return dataset


def test_a_granularity_off_a_temporal_x_axis_takes_its_place_and_is_not_typed() -> None:
    reason = _not_typed(_read({"granularity_sqla": "ship_date"}, base=LINE, dataset=_ship_date()))

    assert reason == "form_data.granularity_sqla = 'ship_date' takes the place of the x-axis 'date'"


def test_a_granularity_on_the_x_axis_is_typed_unless_the_x_axis_is_filtered() -> None:
    assert _typed(_read({"granularity_sqla": "date"}, base=LINE)).query.dimensions == ["date"]
    filtered = {"adhoc_filters": [_on_date()]}
    assert _typed(_read(filtered, base=LINE)).query.filters[0].column == "date"

    reason = _not_typed(_read({**filtered, "granularity_sqla": "date"}, base=LINE))

    assert reason == "form_data.granularity_sqla = 'date' drops the filters on 'date'"


def test_a_granularity_off_a_plain_x_axis_neither_replaces_it_nor_drops_a_filter() -> None:
    form_data = {"x_axis": "store_id", "granularity_sqla": "date", "adhoc_filters": [_on_date()]}

    typed = _typed(_read(form_data, base=LINE))

    assert (typed.query.dimensions, typed.query.filters[0].column) == (["store_id"], "date")


@pytest.mark.parametrize("granularity", ["date", "ship_date"])
def test_a_granularity_drops_the_filters_on_the_column_of_a_time_range(granularity: str) -> None:
    form_data = {
        "granularity_sqla": granularity,
        "adhoc_filters": [_on_date("TEMPORAL_RANGE", "No filter"), _on_date()],
    }

    reason = _not_typed(_read(form_data, dataset=_ship_date()))

    assert reason == f"form_data.granularity_sqla = {granularity!r} drops the filters on 'date'"


def test_a_granularity_drops_the_filters_on_itself_when_it_is_a_time_range() -> None:
    no_time = {**_on_date("TEMPORAL_RANGE", "No filter"), "subject": "ship_date"}
    on_ship = {**_on_date(), "subject": "ship_date"}
    form_data = {
        "granularity_sqla": "ship_date",
        "adhoc_filters": [_on_date("TEMPORAL_RANGE", "No filter"), no_time, on_ship],
    }

    reason = _not_typed(_read(form_data, dataset=_ship_date()))

    assert reason == "form_data.granularity_sqla = 'ship_date' drops the filters on 'ship_date'"


def test_a_granularity_without_a_time_range_drops_no_filter() -> None:
    form_data = {"granularity_sqla": "date", "adhoc_filters": [_on_date()]}

    assert [item.column for item in _typed(_read(form_data)).query.filters] == ["date"]


def test_a_granularity_that_is_not_a_name_is_not_typed() -> None:
    reason = _not_typed(_read({"granularity_sqla": ["date"]}, base=LINE))

    assert reason == "form_data.granularity_sqla is not a column name"


@pytest.mark.parametrize("stack", [None, "", False, True, "Stack", "Stream"])
def test_a_stacking_that_draws_each_value_keeps_a_stacked_bar_typed(stack: Any) -> None:
    bar = {"viz_type": "echarts_timeseries_bar", "groupby": ["store_id"], "stack": stack}

    if stack:
        assert _typed(_read(bar, base=LINE)).viz is Viz.STACKED_BAR
    else:
        assert _not_typed(_read(bar, base=LINE)).startswith("echarts_timeseries_bar with groupby")


def test_a_stack_of_shares_and_a_stacking_unknown_are_not_typed() -> None:
    area = {"viz_type": "echarts_area", "groupby": ["store_id"]}

    assert _not_typed(_read({**area, "stack": "Expand"}, base=LINE)) == (
        "form_data.stack = 'Expand' draws each value as its share of the stack"
    )
    assert _not_typed(_read({**area, "stack": "Percent"}, base=LINE)) == (
        "form_data.stack = 'Percent' is not a stacking the typed form reads"
    )


# --- what Superset 4.1.2 makes of a value -----------------------------------------------------


def _adhoc(operator: str, comparator: Any, subject: str = "store_id") -> dict[str, Any]:
    return {
        "expressionType": "SIMPLE",
        "clause": "WHERE",
        "operator": operator,
        "subject": subject,
        "comparator": comparator,
    }


def _regions() -> dict[str, Any]:
    dataset = _live_dataset()
    dataset["columns"].append(
        {"column_name": "region", "expression": None, "is_dttm": False, "type": "String"}
    )
    return dataset


@pytest.mark.parametrize("comparator", ["", None, "Last week", {"since": "x"}])
def test_a_time_range_filter_reads_no_time_only_at_no_filter(comparator: Any) -> None:
    reason = _not_typed(_read({"adhoc_filters": [_adhoc("TEMPORAL_RANGE", comparator, "date")]}))

    assert reason == f"form_data.adhoc_filters[0] limits time to {comparator!r}"


@pytest.mark.parametrize("time_range", [["No filter"], {"since": None}, "Last week"])
def test_a_time_range_that_is_not_no_filter_is_not_typed(time_range: Any) -> None:
    reason = _not_typed(_read({"time_range": time_range}))

    assert reason == f"form_data.time_range = {time_range!r} filters time"


@pytest.mark.parametrize("comparator", ["<NULL>", "<empty string>", ["north", "<NULL>"]])
def test_a_value_superset_reads_as_another_is_not_typed(comparator: Any) -> None:
    operator = "IN" if isinstance(comparator, list) else "=="
    adhoc = _adhoc(operator, comparator, "region")

    reason = _not_typed(_read({"adhoc_filters": [adhoc]}, dataset=_regions()))

    assert reason == "form_data.adhoc_filters[0] compares with a value Superset reads as another"


@pytest.mark.parametrize(("operator", "comparator"), [("==", "4"), ("IN", ["1", "2"])])
def test_text_compared_with_a_number_column_is_not_typed(operator: str, comparator: Any) -> None:
    reason = _not_typed(_read({"adhoc_filters": [_adhoc(operator, comparator)]}))

    assert reason == "form_data.adhoc_filters[0] compares text Superset casts to the column's type"


def test_text_compared_with_a_text_column_is_typed() -> None:
    adhoc = _adhoc("!=", "north", "region")

    (only,) = _typed(_read({"adhoc_filters": [adhoc]}, dataset=_regions())).query.filters

    assert (only.column, only.op, only.value) == ("region", FilterOp.NEQ, "north")


@pytest.mark.parametrize("key", ["metrics", "adhoc_filters"])
def test_a_list_key_that_is_not_a_list_is_not_typed(key: str) -> None:
    assert (
        _not_typed(_read({key: {"expressionType": "SIMPLE"}})) == f"form_data.{key} is not a list"
    )


def test_a_time_compare_left_empty_keeps_a_chart_typed() -> None:
    assert _typed(_read({"time_compare": []})).viz is Viz.PIE


def test_a_post_processing_value_of_any_shape_is_said() -> None:
    assert _not_typed(_read({"rolling_type": {"type": "cumsum"}})) == (
        "form_data.rolling_type = {'type': 'cumsum'} post-processes the result"
    )


def test_an_answer_that_is_a_json_list_is_not_superset_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "t"})
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "c"})
        return httpx.Response(200, json=[{"result": {}}])

    with pytest.raises(SupersetSourceError) as raised:
        RestSupersetSource(_client(handler)).dashboard("7")

    assert str(raised.value) == "GET /api/v1/dashboard/7: the answer is not Superset's JSON"


def test_a_login_answered_with_a_json_list_is_not_superset_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"access_token": "t"}])

    with pytest.raises(SupersetSourceError) as raised:
        RestSupersetSource(_client(handler)).charts("7")

    assert str(raised.value) == "GET /api/v1/dashboard/7/charts: the answer is not Superset's JSON"


def test_a_refusal_of_a_dataset_sql_quotes_none_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dataset's SQL can hold a credential: no line, refusal or report repeats any of it."""
    secret = "DUMMYPASS123"
    unparsed = {**_live_dataset(), "sql": f"SELECT * FROM dm.t WHERE note = '{secret}' AND"}
    function = {
        **_live_dataset(26),
        "sql": f"SELECT * FROM postgresql('pg:5432', 'shop', 'orders', 'svc', '{secret}')",
    }
    charts = [_live_chart(PIE), _live_chart(126)]
    monkeypatch.setattr(
        inventory_module, "RestSupersetSource", lambda client: Given(charts, [unparsed, function])
    )
    semantic = str(_semantic(tmp_path))

    for output, extra in (("o", []), ("j", ["--json"])):
        argv = ["decision", "inventory", semantic, "--output", str(tmp_path / output)]
        code = main([*argv, "--dashboard", "dashboard://w@1=7", *extra])

        out, err = capsys.readouterr()
        report = (tmp_path / output / INVENTORY_REPORT).read_text(encoding="utf-8")
        assert code == 3
        assert secret not in out + err + report

    assert json.loads(report)["dashboards"][0]["refusals"] == [
        "chart_122: its read SQL is not one plain SELECT: it does not parse (line 8, column 1)",
        "chart_126: its read SQL is not one plain SELECT: "
        "forbidden table function in SQL: postgresql()",
    ]


# --- the database a chart is on ---------------------------------------------------------------


def test_each_chart_is_recorded_with_the_database_of_its_dataset() -> None:
    read = inventory_dashboard("dashboard://w@1", "d6-live", Recorded(), MODEL)

    assert {(chart.database, chart.backend) for chart in read.record.charts} == {
        (2, "clickhousedb")
    }


def test_the_database_the_model_describes_writes_the_dashboard() -> None:
    read = inventory_dashboard("dashboard://w@1", "d6-live", Recorded(), MODEL, database=2)

    assert read.spec is not None and read.record.refusals == []


def test_a_chart_on_another_database_refuses_its_dashboard() -> None:
    read = inventory_dashboard("dashboard://w@1", "d6-live", Recorded(), MODEL, database=1)

    assert read.spec is None
    assert read.record.charts == []
    assert read.record.refusals == [
        f"chart_{ident}: its dataset is on Superset database 2 (clickhousedb), not on 1"
        for ident in range(121, 127)
    ]


@pytest.mark.parametrize(
    ("database", "where"),
    [
        (None, "names no Superset database"),
        ({"backend": "sqlite"}, "names no Superset database"),
        ({"id": True, "backend": "sqlite"}, "names no Superset database"),
        ({"id": 1}, "is on Superset database 1 (engine not named)"),
    ],
)
def test_a_dataset_whose_database_is_not_the_one_asked_for_is_named(
    database: Any, where: str
) -> None:
    dataset = _live_dataset()
    dataset["database"] = database
    source = Given([_live_chart(PIE)], [dataset])

    read = inventory_dashboard("dashboard://live@1", "7", source, MODEL, database=2)

    assert read.record.refusals == [f"chart_122: its dataset {where}, not on 2"]


def test_the_command_passes_the_database_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(inventory_module, "RestSupersetSource", lambda client: Recorded())
    argv = ["decision", "inventory", str(_semantic(tmp_path)), "--dashboard", "dashboard://w@1=12"]

    code = main([*argv, "--output", str(tmp_path / "o"), "--database", "1", "--json"])

    out, _ = capsys.readouterr()
    assert code == 3
    (dashboard,) = json.loads(out)["dashboards"]
    assert dashboard["refusals"][0] == (
        "chart_121: its dataset is on Superset database 2 (clickhousedb), not on 1"
    )


# --- the columns a typed chart reads ----------------------------------------------------------


def _with_column(
    name: str, native: str | None, *, is_dttm: bool = False
) -> tuple[dict[str, Any], SemanticModel]:
    """The live dataset and the model, each with one more column."""
    dataset = _live_dataset()
    dataset["columns"].append(
        {"column_name": name, "expression": None, "is_dttm": is_dttm, "type": native}
    )
    model = MODEL.model_copy(deep=True)
    role = ColumnRole.TIME if is_dttm else ColumnRole.DIMENSION
    model.tables[0].columns.append(Column(name=name, type=native or "String", role=role))
    return dataset, model


def _wide() -> dict[str, Any]:
    """The live dataset with `region` and `ship_date` described as well."""
    dataset = _regions()
    dataset["columns"].append(
        {"column_name": "ship_date", "expression": None, "is_dttm": True, "type": "DATE"}
    )
    return dataset


_ORDERS = {"expressionType": "SIMPLE", "aggregate": "SUM", "column": {"column_name": "orders"}}


@pytest.mark.parametrize(
    ("form_data", "base", "where", "name"),
    [
        ({"groupby": ["region"]}, PIE, "groupby[0]", "region"),
        ({"x_axis": "region"}, LINE, "x_axis[0]", "region"),
        ({"metric": _ORDERS}, PIE, "metric.column", "orders"),
        (
            {"adhoc_filters": [_adhoc("==", "north", "region")]},
            PIE,
            "adhoc_filters[0].subject",
            "region",
        ),
        ({"granularity_sqla": "ship_date"}, PIE, "granularity_sqla", "ship_date"),
    ],
)
def test_a_column_the_dataset_answer_does_not_describe_is_not_typed(
    form_data: dict[str, Any], base: int, where: str, name: str
) -> None:
    """Superset trims a dashboard's datasets to the columns some form_data keys name: one it
    left out is a column whose expression, type and time flag are not known here."""
    assert _not_typed(_read(form_data, base=base)) == (
        f"form_data.{where} names {name!r}, a column the dataset answer does not describe"
    )


@pytest.mark.parametrize("is_dttm", [None, 1, "true"])
def test_a_column_the_answer_does_not_flag_as_temporal_or_not_is_not_typed(is_dttm: Any) -> None:
    dataset = _regions()
    dataset["columns"][-1]["is_dttm"] = is_dttm

    assert _not_typed(_read({"groupby": ["region"]}, dataset=dataset)) == (
        "form_data.groupby[0]: the dataset answer does not say whether 'region' is temporal"
    )


def test_a_granularity_that_is_not_temporal_is_not_typed() -> None:
    assert _not_typed(_read({"granularity_sqla": "store_id"})) == (
        "form_data.granularity_sqla = 'store_id' is not temporal: Superset puts the dataset's "
        "main time column in its place"
    )


@pytest.mark.parametrize(
    "viz_type", ["echarts_timeseries_line", "echarts_timeseries_bar", "echarts_area", "heatmap_v2"]
)
def test_a_chart_of_an_x_axis_without_one_is_not_typed(viz_type: str) -> None:
    """Superset groups it by the legacy time column `__timestamp` instead."""
    reason = _not_typed(_read({"viz_type": viz_type}, base=LINE, drop=("x_axis",)))

    assert reason == f"{viz_type} without form_data.x_axis is not read by the typed form"


# --- the grain Superset groups by -------------------------------------------------------------


def test_a_heatmap_buckets_a_temporal_x_axis_by_its_grain() -> None:
    """`buildQueryContext` sends a heatmap's x-axis as a BASE_AXIS column with the grain."""
    heatmap = _typed(
        _read(
            {
                "viz_type": "heatmap_v2",
                "x_axis": "date",
                "groupby": "store_id",
                "time_grain_sqla": "P1M",
            }
        )
    )

    assert (heatmap.query.dimensions, heatmap.query.time_grain) == (
        ["date", "store_id"],
        TimeGrain.MONTH,
    )


_TABLE = {"viz_type": "table", "query_mode": "aggregate"}


@pytest.mark.parametrize(
    ("form_data", "dimensions", "grain"),
    [
        # A pie sends no axis: only a column that is the granularity is grained.
        ({"groupby": ["date"]}, ["date"], None),
        ({"groupby": ["date"], "granularity_sqla": "date"}, ["date"], TimeGrain.MONTH),
        # A table sends the first column its lookup flags, first; with none, no grain at all.
        ({**_TABLE, "groupby": ["date"]}, ["date"], None),
        ({**_TABLE, "groupby": ["date"], "granularity_sqla": "date"}, ["date"], None),
        (
            {**_TABLE, "groupby": ["store_id", "date"], "temporal_columns_lookup": {"date": True}},
            ["date", "store_id"],
            TimeGrain.MONTH,
        ),
        (
            {
                **_TABLE,
                "groupby": ["date", "store_id"],
                "temporal_columns_lookup": {"date": False, "store_id": True},
            },
            ["store_id", "date"],
            None,
        ),
    ],
)
def test_the_grain_is_the_one_superset_groups_the_first_dimension_by(
    form_data: dict[str, Any], dimensions: list[str], grain: TimeGrain | None
) -> None:
    query = _typed(_read({**form_data, "time_grain_sqla": "P1M"})).query

    assert (query.dimensions, query.time_grain) == (dimensions, grain)


@pytest.mark.parametrize(
    ("form_data", "base", "drop", "grouped"),
    [
        # The granularity is grained wherever it stands: a series, a heatmap's y, a pie's second.
        (
            {
                "viz_type": "echarts_timeseries_bar",
                "x_axis": "region",
                "groupby": ["date"],
                "stack": "Stack",
                "granularity_sqla": "date",
            },
            LINE,
            (),
            "'date'",
        ),
        (
            {"viz_type": "echarts_area", "x_axis": "store_id", "groupby": ["date"]}
            | {"granularity_sqla": "date"},
            LINE,
            (),
            "'date'",
        ),
        (
            {"viz_type": "heatmap_v2", "x_axis": "store_id", "groupby": "date"}
            | {"granularity_sqla": "date"},
            LINE,
            (),
            "'date'",
        ),
        ({"groupby": ["store_id", "date"], "granularity_sqla": "date"}, PIE, (), "'date'"),
        # A pivot groups each row and column its lookup flags.
        (
            {
                "viz_type": "pivot_table_v2",
                "groupbyRows": ["date"],
                "groupbyColumns": ["store_id"],
                "metrics": [_metric()],
                "temporal_columns_lookup": {"date": True},
            },
            PIE,
            ("groupby", "metric"),
            "'date'",
        ),
        # A table groups the column its lookup flags and, besides, the granularity.
        (
            {
                **_TABLE,
                "groupby": ["date", "ship_date"],
                "temporal_columns_lookup": {"date": True},
                "granularity_sqla": "ship_date",
            },
            PIE,
            (),
            "'date', 'ship_date'",
        ),
    ],
)
def test_a_grain_superset_puts_on_another_column_than_the_first_is_not_typed(
    form_data: dict[str, Any], base: int, drop: tuple[str, ...], grouped: str
) -> None:
    read = _read({**form_data, "time_grain_sqla": "P1M"}, base=base, drop=drop, dataset=_wide())

    assert _not_typed(read) == (
        f"form_data.time_grain_sqla = 'P1M' groups {grouped}; the IR grains the first "
        "dimension only"
    )


def _date_of_type(native: str | None) -> dict[str, Any]:
    dataset = _live_dataset()
    (date,) = [column for column in dataset["columns"] if column["column_name"] == "date"]
    date["type"] = native
    return dataset


@pytest.mark.parametrize(
    "native",
    ["DATE", "date", "Nullable(Date)", "LowCardinality(Nullable(DATE))", " Date "],
)
def test_a_day_grain_of_a_date_column_is_the_ir_day(native: str) -> None:
    read = _read({"time_grain_sqla": "P1D"}, base=LINE, dataset=_date_of_type(native))

    assert _typed(read).query.time_grain is TimeGrain.DAY


@pytest.mark.parametrize(
    "native", ["DATETIME", "DateTime", "Nullable(DateTime)", "DateTime('UTC')"]
)
def test_a_day_grain_of_a_column_of_time_is_not_typed(native: str) -> None:
    """Superset starts the day of such a column (`toStartOfDay`); the IR's day is the column."""
    read = _read({"time_grain_sqla": "P1D"}, base=LINE, dataset=_date_of_type(native))

    assert _not_typed(read) == (
        "form_data.time_grain_sqla = 'P1D' starts the day of 'date', a column of "
        f"{native}; the IR's day is the column as it is"
    )


@pytest.mark.parametrize("lookup", [["date"], "date", {"date": "yes"}, {"date": 1}])
def test_a_temporal_columns_lookup_that_is_not_a_map_of_flags_is_not_typed(lookup: Any) -> None:
    form_data = {**_TABLE, "groupby": ["date"], "temporal_columns_lookup": lookup}

    reason = _not_typed(_read({**form_data, "time_grain_sqla": "P1M"}))

    assert reason == "form_data.temporal_columns_lookup is not a map of columns to flags"


# --- what Superset 4.1.2 groups and computes once --------------------------------------------


@pytest.mark.parametrize(
    ("form_data", "base", "drop", "name"),
    [
        ({"viz_type": "echarts_area", "groupby": ["date"]}, LINE, (), "date"),
        (
            {"viz_type": "heatmap_v2", "x_axis": "store_id", "groupby": "store_id"},
            PIE,
            (),
            "store_id",
        ),
        ({"groupby": ["store_id", "store_id"]}, PIE, (), "store_id"),
        (
            {
                "viz_type": "pivot_table_v2",
                "groupbyRows": ["store_id"],
                "groupbyColumns": ["store_id"],
                "metrics": [_metric()],
            },
            PIE,
            ("groupby", "metric"),
            "store_id",
        ),
    ],
)
def test_a_column_grouped_twice_is_not_typed(
    form_data: dict[str, Any], base: int, drop: tuple[str, ...], name: str
) -> None:
    assert _not_typed(_read(form_data, base=base, drop=drop)) == (
        f"form_data groups by {name!r} twice"
    )


def _labelled(label: str | None, aggregate: str = "SUM") -> dict[str, Any]:
    metric = {**_metric(), "aggregate": aggregate}
    if label is not None:
        metric["label"] = label
    return metric


@pytest.mark.parametrize(
    ("metrics", "where", "label"),
    [
        ([_labelled("Выручка"), _labelled("Выручка", "AVG")], "metrics[1]", "Выручка"),
        ([_labelled(None), _labelled(None)], "metrics[1]", "SUM(revenue)"),
        ([_labelled(None), _labelled("SUM(revenue)", "AVG")], "metrics[1]", "SUM(revenue)"),
        (["SUM(revenue)", _labelled(None)], "metrics[1]", "SUM(revenue)"),
    ],
)
def test_metrics_of_one_label_are_not_typed(metrics: list[Any], where: str, label: str) -> None:
    """Superset queries one metric of each label (`extractQueryFields`): the first."""
    dataset = _live_dataset()
    dataset["metrics"].append({"metric_name": "SUM(revenue)", "expression": "SUM(revenue)"})

    reason = _not_typed(_read({**_TABLE, "metrics": metrics}, drop=("metric",), dataset=dataset))

    assert reason == (
        f"form_data.{where} has the label {label!r} of another metric: Superset computes the "
        "first of them only"
    )


def test_a_metric_and_metrics_of_one_label_are_not_typed() -> None:
    reason = _not_typed(_read({"metrics": [_labelled(None)]}))

    assert reason == (
        "form_data.metric has the label 'SUM(revenue)' of another metric: Superset computes the "
        "first of them only"
    )


def test_metrics_of_their_own_labels_are_each_a_measure() -> None:
    metrics = [_labelled("a"), _labelled("b", "AVG"), _labelled(None, "MAX")]

    table = _typed(_read({**_TABLE, "metrics": metrics}, drop=("metric",)))

    assert [(item.agg, item.column) for item in table.query.measures] == [
        (Aggregation.SUM, "revenue"),
        (Aggregation.AVG, "revenue"),
        (Aggregation.MAX, "revenue"),
    ]


@pytest.mark.parametrize("aggregate", ["AVG", "MIN", "MAX", "COUNT_DISTINCT"])
def test_a_pivot_of_a_measure_its_sum_of_records_changes_is_not_typed(aggregate: str) -> None:
    pivot = {
        "viz_type": "pivot_table_v2",
        "groupbyRows": ["store_id"],
        "metrics": [{**_metric(), "aggregate": aggregate}],
    }

    assert _not_typed(_read(pivot, drop=("groupby", "metric"))) == (
        "pivot_table_v2 sums the records it folds into one cell; only SUM and COUNT stay the "
        "measure"
    )


@pytest.mark.parametrize(
    ("aggregate", "agg"), [("SUM", Aggregation.SUM), ("COUNT", Aggregation.COUNT)]
)
def test_a_pivot_of_sums_or_counts_is_typed(aggregate: str, agg: Aggregation) -> None:
    pivot = {
        "viz_type": "pivot_table_v2",
        "groupbyRows": ["store_id"],
        "metrics": [{**_metric(), "aggregate": aggregate}],
    }

    (measure,) = _typed(_read(pivot, drop=("groupby", "metric"))).query.measures

    assert measure.agg is agg


# --- what Superset 4.1.2 makes of a filter value, further ------------------------------------


@pytest.mark.parametrize(
    ("name", "native", "is_dttm"),
    [
        ("date", "DATE", True),
        ("ts", "DateTime64(3)", False),
        ("span", "IntervalDay", False),
        ("epoch", "UInt64", True),
    ],
)
def test_a_number_compared_with_a_time_column_is_not_typed(
    name: str, native: str, is_dttm: bool
) -> None:
    """Superset reads it as epoch milliseconds and compares the column with that date: on a column
    of a time type, and on one the dataset flags as temporal."""
    dataset, model = (
        (_live_dataset(), MODEL) if name == "date" else _with_column(name, native, is_dttm=is_dttm)
    )
    adhoc = _adhoc(">=", 1_700_000_000_000, name)

    reason = _not_typed(_read({"adhoc_filters": [adhoc]}, dataset=dataset, model=model))

    assert (
        reason == "form_data.adhoc_filters[0] compares time with a number Superset reads as a date"
    )


@pytest.mark.parametrize(("comparator", "typed"), [(1, True), (0, True), (2, False), (1.0, False)])
def test_a_number_compared_with_a_bool_column_is_typed_at_0_and_1_only(
    comparator: Any, typed: bool
) -> None:
    """Superset compares a bool column with the truth of the number (`cast_to_boolean`)."""
    dataset, model = _with_column("flag", "Bool")
    read = _read(
        {"adhoc_filters": [_adhoc("==", comparator, "flag")]}, dataset=dataset, model=model
    )

    if typed:
        (only,) = _typed(read).query.filters
        assert (only.column, only.value) == ("flag", comparator)
    else:
        assert _not_typed(read) == (
            "form_data.adhoc_filters[0] compares a bool with a number Superset reads as one"
        )


@pytest.mark.parametrize("comparator", ["north\n", "\tnorth", ["north", "south\t"]])
def test_text_superset_trims_is_not_typed(comparator: Any) -> None:
    operator = "IN" if isinstance(comparator, list) else "=="
    adhoc = _adhoc(operator, comparator, "region")

    reason = _not_typed(_read({"adhoc_filters": [adhoc]}, dataset=_regions()))

    assert reason == "form_data.adhoc_filters[0] compares with a value Superset reads as another"


def test_text_with_spaces_at_its_ends_is_compared_as_written() -> None:
    adhoc = _adhoc("==", " north ", "region")

    (only,) = _typed(_read({"adhoc_filters": [adhoc]}, dataset=_regions())).query.filters

    assert only.value == " north "


# --- answers too deep, and SQL a file would change --------------------------------------------


def _nested(depth: int) -> list[Any]:
    value: list[Any] = []
    for _ in range(depth):
        value = [value]
    return value


_TOO_DEEP = r"^an answer is nested deeper than the inventory reads$"


def test_a_form_data_nested_past_the_recursion_limit_is_an_answer_error() -> None:
    with pytest.raises(SupersetAnswerError, match=_TOO_DEEP):
        _read({"label_colors": _nested(20_000)})


def test_a_json_metadata_nested_past_the_recursion_limit_is_an_answer_error() -> None:
    with pytest.raises(SupersetAnswerError, match=_TOO_DEEP):
        _filtered(metadata="[" * 200_000 + "]" * 200_000)


def test_a_rest_answer_nested_past_the_recursion_limit_is_not_superset_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "t"})
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "c"})
        return httpx.Response(200, content=b"[" * 200_000 + b"]" * 200_000)

    with pytest.raises(
        SupersetSourceError, match=r"^GET /api/v1/dashboard/7: the answer is not Superset's JSON$"
    ):
        RestSupersetSource(_client(handler)).dashboard("7")


_NEL = chr(0x85)


@pytest.mark.parametrize(
    "expression", ["concat('a', '" + _NEL + "')", "revenue -- note" + _NEL + ") )"]
)
def test_a_read_sql_holding_a_next_line_reads_back_from_its_file(expression: str) -> None:
    """PyYAML writes U+0085 as it stands into a block or a single-quoted text and does not read
    it back as itself: a string would change, a line comment would end early."""
    dataset = _live_dataset()
    dataset["columns"].append(
        {"column_name": "calc", "expression": expression, "is_dttm": False, "type": "String"}
    )
    read = _read({"viz_type": "word_cloud"}, dataset=dataset)
    assert read.spec is not None, read.record.refusals

    back = DashboardSpec.model_validate(yaml.safe_load(dump_spec(read.spec)))

    assert back == read.spec
    assert gate_refusals(back, MODEL) == []


@pytest.mark.parametrize(
    "text",
    [
        "a" + _NEL + "b",
        "a\n" + _NEL,
        "a" + chr(0x2028) + "b",
        "a" + chr(0x2029) + "b",
        "a\r\nb",
        "a\rb",
        " a\n b ",
        "\n\na\n\n",
        "a\n\tb",
        "# a\n: b\n- c\n|",
    ],
)
def test_a_spec_file_reads_back_as_the_spec(text: str) -> None:
    sql = "SELECT\n" + text + "\nFROM dm.sales_daily"
    query = ChartQuery(table="dm.sales_daily", raw_sql=sql)
    spec = DashboardSpec(
        title=text, charts=[ChartSpec(id="c", title=text, viz=Viz.TABLE, query=query)]
    )

    assert DashboardSpec.model_validate(yaml.safe_load(dump_spec(spec))) == spec


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM dm.sales_daily ; ;",
        "; SELECT * FROM dm.sales_daily",
        "\n\tSELECT * FROM dm.sales_daily;\r\n",
    ],
)
def test_a_virtual_dataset_is_read_without_the_ends_superset_strips(sql: str) -> None:
    read = _read(dataset={**_live_dataset(), "sql": sql})

    assert read.spec is not None, read.record.refusals
    raw = read.spec.charts[0].query.raw_sql
    assert raw is not None
    assert raw.endswith("\nFROM (\nSELECT * FROM dm.sales_daily\n) AS virtual_table")


# --- what the docs say, pinned ---------------------------------------------------------------


def test_the_rest_source_logs_in_again_after_an_answer_401() -> None:
    seen: list[tuple[str, str]] = []
    answers = [
        httpx.Response(401, json={"msg": "Token has expired"}),
        httpx.Response(200, json={"result": {"id": 7}}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "t"})
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "c"})
        return answers.pop(0)

    assert RestSupersetSource(_client(handler)).dashboard("7") == {"id": 7}
    assert seen == [
        ("POST", "/api/v1/security/login"),
        ("GET", "/api/v1/security/csrf_token/"),
        ("GET", "/api/v1/dashboard/7"),
        ("POST", "/api/v1/security/login"),
        ("GET", "/api/v1/security/csrf_token/"),
        ("GET", "/api/v1/dashboard/7"),
    ]


def test_the_rest_source_says_a_csrf_token_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(500, json={"message": "boom"})

    with pytest.raises(SupersetSourceError, match=r"^csrf fetch failed: HTTP 500$"):
        RestSupersetSource(_client(handler)).dashboard("7")


@pytest.mark.parametrize("value", [None, "", [], {}, False])
def test_an_unknown_key_without_a_value_keeps_a_chart_typed(value: Any) -> None:
    assert _typed(_read({"made_up_key": value})).viz is Viz.PIE


@pytest.mark.parametrize("time_range", [None, ""])
def test_a_chart_time_range_left_empty_reads_no_time(time_range: Any) -> None:
    assert _typed(_read({"time_range": time_range})).viz is Viz.PIE


def test_a_chart_that_refuses_its_dashboard_is_named_in_its_refusals_only() -> None:
    read = inventory_dashboard("dashboard://d@1", "9", Recorded(), _corpus_model())

    assert read.spec is None
    assert len(read.record.charts) == 23
    assert "chart_46" not in [chart.chart for chart in read.record.charts]
    assert len(read.record.refusals) == 2
    assert read.record.refusals[0] == f"the dashboard holds 24 charts; a spec holds {MAX_CHARTS}"
    assert read.record.refusals[1].startswith("chart_46: its read SQL is not one plain SELECT: ")


def test_a_dashboard_the_compiler_refuses_whole_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class RefusingTwo:
        def compile_contract(self, spec: DashboardSpec, model: SemanticModel) -> None:
            if len(spec.charts) > 1:
                raise ValueError("two charts")

    monkeypatch.setattr(inventory_module, "SupersetContractCompiler", RefusingTwo)
    second = _live_chart(PIE)
    second["id"] = 1
    source = Given([_live_chart(PIE), second], [_live_dataset()])

    read = inventory_dashboard("dashboard://d@1", "7", source, MODEL)

    assert (read.spec, read.record.refusals) == (None, ["the spec does not compile: two charts"])


def test_a_virtual_dataset_without_schema_refuses_the_dashboard() -> None:
    read = _read(dataset={**_live_dataset(), "schema": None, "sql": "SELECT 1 AS x"})

    assert read.spec is None
    assert read.record.refusals == [
        "chart_122: the dataset names no schema, so its table could not be named"
    ]


def test_a_write_that_fails_leaves_only_the_directories_above_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Path, data: bytes) -> int:
        raise PermissionError(13, "Permission denied", str(self))

    request = _request(tmp_path, [("dashboard://w@1", "12")], output=tmp_path / "a" / "b" / "out")
    monkeypatch.setattr(Path, "write_bytes", refuse)
    with pytest.raises(VerifyError, match=r"cannot write the inventory to "):
        inventory(request)
    monkeypatch.undo()

    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == [
        "a",
        "a/b",
        "semantic",
        "semantic/model.yaml",
    ]


def test_the_inventory_opens_no_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the inventory opened a connection")

    request = _request(tmp_path, [("dashboard://w@1", "12")])
    monkeypatch.setattr(socket.socket, "connect", refuse)

    report = inventory(request)

    assert not report.refused


def test_the_command_reaches_the_configured_superset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[tuple[Any, ...]] = []

    class Spy(SupersetClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            made.append(args)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("auto_bi.adapters.superset.client.SupersetClient", Spy)
    monkeypatch.setenv("AUTO_BI_SUPERSET_URL", "http://superset.test")
    monkeypatch.setenv("AUTO_BI_SUPERSET_USER", "reader")
    monkeypatch.setenv("AUTO_BI_SUPERSET_PASSWORD", "pw")
    get_settings.cache_clear()
    try:
        code, _, _ = _command(tmp_path, monkeypatch, "dashboard://w@1=12")
    finally:
        get_settings.cache_clear()

    assert (code, made) == (0, [("http://superset.test", "reader", "pw")])


def test_the_gate_compiles_what_the_inventory_wrote_and_misses_what_it_refused(
    tmp_path: Path,
) -> None:
    pilot = tmp_path / "pilot"
    shutil.copytree(PILOT, pilot, ignore=shutil.ignore_patterns("evidence", "dashboards"))

    def bi_contract(ident: str) -> tuple[CheckStatus, str]:
        out = tmp_path / f"dashboards-{ident}"
        inventory(
            InventoryRequest(
                semantic=pilot / "candidate",
                dashboards=[("dashboard://weekly_revenue@1", ident)],
                output=out,
                source=Recorded,
            )
        )
        report = verify(
            VerifyRequest(
                contract_paths=[pilot / "contracts"],
                base=pilot / "base",
                candidate=pilot / "candidate",
                evidence_dir=tmp_path / f"evidence-{ident}",
                created_at="2026-08-25T09:00:00Z",
                dashboards_dir=out,
                product_id="decision://sales/weekly_revenue",
            )
        )
        (check,) = [check for check in report.checks if check.check_id == "bi.contract"]
        return check.status, check.summary_code

    assert bi_contract("d6-live") == (CheckStatus.PASS, "bi.compiled")
    assert bi_contract("11") == (CheckStatus.REVIEW, "bi.spec_missing")


def test_the_report_holds_the_keys_the_spec_names(tmp_path: Path) -> None:
    inventory(_request(tmp_path, [("dashboard://w@1", "12"), ("dashboard://big@1", "11")]))

    report = json.loads((tmp_path / "out" / INVENTORY_REPORT).read_text(encoding="utf-8"))
    assert list(report) == ["superset_version", "reader_version", "dashboards"]
    for dashboard in report["dashboards"]:
        assert list(dashboard) == [
            "ref",
            "superset",
            "superset_id",
            "title",
            "changed_on",
            "written",
            "charts",
            "refusals",
        ]
        for chart in dashboard["charts"]:
            assert list(chart) == [
                "chart",
                "superset_id",
                "viz_type",
                "form",
                "reasons",
                "database",
                "backend",
            ]
    assert [dashboard["written"] for dashboard in report["dashboards"]] == ["w@1.yaml", None]


def test_the_command_exits_2_when_superset_refuses_the_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    monkeypatch.setattr(
        inventory_module, "RestSupersetSource", lambda client: RestSupersetSource(_client(refused))
    )
    argv = ["decision", "inventory", str(_semantic(tmp_path)), "--output", str(tmp_path / "o")]

    code = main([*argv, "--dashboard", "dashboard://w@1=12"])

    out, err = capsys.readouterr()
    assert (code, out) == (2, "")
    assert err == "error: cannot read Superset dashboard '12': login failed: HTTP 401\n"
    assert not (tmp_path / "o").exists()


def test_the_fixtures_hold_the_fields_their_readme_names() -> None:
    for ident in (*CORPUS, LIVE):
        answers = _answers(ident)
        assert sorted(answers["dashboard"]) == [
            "changed_on",
            "dashboard_title",
            "id",
            "json_metadata",
            "slug",
        ]
        assert inventory_module._native_filters(answers["dashboard"]) == []
        for chart in answers["charts"]:
            assert sorted(chart) == ["form_data", "id", "slice_name"]
            assert not {"dashboards", "url_params"} & set(chart["form_data"])
        for dataset in answers["datasets"]:
            assert sorted(dataset) == [
                "columns",
                "database",
                "id",
                "metrics",
                "schema",
                "sql",
                "table_name",
            ]
            assert sorted(dataset["database"]) == ["backend", "id"]
            for column in dataset["columns"]:
                assert sorted(column) == ["column_name", "expression", "is_dttm", "type"]
            for metric in dataset["metrics"]:
                assert sorted(metric) == ["expression", "metric_name"]


def test_a_chart_whose_dataset_names_no_database_is_recorded_without_one() -> None:
    dataset = _live_dataset()
    del dataset["database"]

    read = inventory_dashboard(
        "dashboard://live@1", "7", Given([_live_chart(PIE)], [dataset]), MODEL
    )

    assert [(chart.database, chart.backend) for chart in read.record.charts] == [(None, "")]


def test_a_scope_of_some_tabs_is_taken_to_apply_on_every_tab() -> None:
    read = _filtered(_native(scope={"rootPath": ["TAB-a"], "excluded": []}))

    assert {form for form, _ in _forms(read).values()} == {ChartForm.READ_SQL}


def test_superset_is_reached_once_for_every_dashboard(tmp_path: Path) -> None:
    made: list[Recorded] = []

    def source() -> Recorded:
        made.append(Recorded())
        return made[-1]

    inventory(
        InventoryRequest(
            semantic=_semantic(tmp_path),
            dashboards=[("dashboard://a@1", "10"), ("dashboard://w@1", "12")],
            output=tmp_path / "out",
            source=source,
        )
    )

    assert len(made) == 1
    # Dashboard 10's one chart is on a backend with no read dialect, so its chart list is not
    # asked for; dashboard 12's charts qualify, and its list is asked for once.
    assert [(name, ident) for name, ident in made[0].calls] == [
        ("dashboard", "10"),
        ("charts", "10"),
        ("datasets", "10"),
        ("dashboard", "12"),
        ("charts", "12"),
        ("datasets", "12"),
        ("saves", "12"),
        ("definition", "25"),
        ("definition", "26"),
    ]


def test_the_chart_limit_is_the_one_of_the_ir() -> None:
    assert DashboardSpec.model_json_schema()["properties"]["charts"]["maxItems"] == MAX_CHARTS


# --- r2: SQL the parser fails on, a lone surrogate, a saved metric's SQL, a wider type of time ---

#: A column quoted, then a list, the `IN` forgotten: sqlglot 30 fails on it with an
#: `AttributeError`, not with an error of its own kind.
_NO_IN = "\"region\" ('North', 'South')"


@pytest.mark.parametrize(
    ("form_data", "where"),
    [
        (
            {
                "adhoc_filters": [
                    {"expressionType": "SQL", "clause": "WHERE", "sqlExpression": _NO_IN}
                ]
            },
            "",
        ),
        ({"metric": "m"}, "metric"),
        ({"rolling_type": "mean"}, "metric"),
        ({"rolling_type": "mean"}, "column"),
        ({}, "sql"),
    ],
)
def test_sql_the_parser_fails_on_refuses_the_dashboard_by_the_failure(
    form_data: dict[str, Any], where: str
) -> None:
    """Superset keeps such SQL as text and the tile errs. The inventory refuses the dashboard
    and names the parser's failure, never its message: that quotes the SQL."""
    dataset = _live_dataset()
    if where == "metric":
        dataset["metrics"].append({"metric_name": "m", "expression": f"sumIf(revenue, {_NO_IN})"})
    elif where == "column":
        dataset["columns"].append(
            {"column_name": "c", "expression": f"if({_NO_IN}, 1, 0)", "is_dttm": False}
        )
    elif where == "sql":
        dataset["sql"] = f"SELECT * FROM dm.sales_daily WHERE {_NO_IN}"

    read = _read(form_data, dataset=dataset)

    assert read.spec is None
    assert read.record.refusals == [
        "chart_122: its read SQL is not one plain SELECT: it does not parse (the parser failed "
        "with AttributeError)"
    ]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            sqlglot.errors.ParseError("private-parser-text", errors=[{"line": 3, "col": 7}]),
            "it does not parse (line 3, column 7)",
        ),
        (sqlglot.errors.TokenError("private-parser-text"), "it does not parse"),
        (
            AttributeError("private-parser-text"),
            "it does not parse (the parser failed with AttributeError)",
        ),
        (
            ValueError("private-parser-text"),
            "it does not parse (the parser failed with ValueError)",
        ),
        (
            IndexError("private-parser-text"),
            "it does not parse (the parser failed with IndexError)",
        ),
        (
            RecursionError("private-parser-text"),
            "SQL statement nests deeper than this parser reads",
        ),
    ],
    ids=[
        "ParseError",
        "TokenError",
        "AttributeError",
        "ValueError",
        "IndexError",
        "RecursionError",
    ],
)
def test_parser_failures_keep_the_head_inventory_refusal(
    monkeypatch: pytest.MonkeyPatch, error: Exception, reason: str
) -> None:
    """The inventory.json refusal stays as in 63c9d1b through the real guard boundary."""

    def parse(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(sqlglot, "parse", parse)

    read = _read({"rolling_type": "mean"})

    assert read.spec is None
    record = json.loads(read.record.model_dump_json())
    assert record["refusals"] == [f"chart_122: its read SQL is not one plain SELECT: {reason}"]
    assert "private-parser-text" not in read.record.model_dump_json()


@pytest.mark.parametrize("error", [AttributeError, ValueError, IndexError, RecursionError])
def test_any_failure_of_the_guard_refuses_the_dashboard_by_its_class(
    monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    def guard(sql: str, *, dialect: str) -> None:
        raise error(sql)

    monkeypatch.setattr(inventory_module, "guard_sql", guard)

    read = _read({"rolling_type": "mean"})

    assert read.spec is None
    assert read.record.refusals == [
        "chart_122: its read SQL is not one plain SELECT: it does not parse (the parser failed "
        f"with {error.__name__})"
    ]


def test_a_saved_metric_that_does_not_parse_alone_is_named_by_its_name() -> None:
    """Two metrics in one: no expression alone, a tuple in the read SQL, which the guard passes.
    (A saved metric the guard fails on refuses the dashboard, as above.)"""
    dataset = _live_dataset()
    dataset["metrics"].append({"metric_name": "m", "expression": "SUM(revenue), COUNT(orders)"})

    read = _read({"metric": "m"}, dataset=dataset)

    assert _form(read) == (
        ChartForm.READ_SQL,
        [
            "form_data.metric: the saved metric 'm' does not parse",
            "saved: the last change is not a save within two seconds",
            "composite_sql: the dataset definition is not readable",
        ],
    )


def test_any_failure_of_the_parser_on_a_saved_metric_is_a_metric_that_does_not_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Parser:
        @staticmethod
        def parse_one(sql: str, *, read: str) -> Any:
            raise ValueError(sql)

    monkeypatch.setattr(inventory_module, "sqlglot", Parser)
    dataset = _live_dataset()
    dataset["metrics"].append({"metric_name": "m", "expression": "SUM(revenue)"})

    read = _read({"metric": "m"}, dataset=dataset)

    (chart,) = read.record.charts
    assert (chart.form, chart.reasons) == (
        ChartForm.READ_SQL,
        [
            "form_data.metric: the saved metric 'm' does not parse",
            "saved: the last change is not a save within two seconds",
            "composite_sql: the dataset definition is not readable",
        ],
    )


@pytest.mark.parametrize("key", ["my key", "1st", "x" + chr(0xD800)])
def test_a_key_that_is_no_name_is_quoted_in_its_reason(tmp_path: Path, key: str) -> None:
    """JSON's escape carries a lone surrogate into a key of the answer. Quoted, the key is
    written as its escape, and the report is written."""
    chart = _live_chart(PIE)
    chart["form_data"][key] = 1
    source = Given([chart], [_live_dataset()])

    inventory(_request(tmp_path, [("dashboard://w@1", "7")], source=source))

    report = json.loads((tmp_path / "out" / INVENTORY_REPORT).read_text(encoding="utf-8"))
    assert report["dashboards"][0]["charts"][0]["reasons"] == [
        f"form_data[{key!r}] is not a key the typed form reads",
        "saved: the last change is not a save within two seconds",
        "composite_sql: the dataset definition is not readable",
    ]


def test_a_lone_surrogate_the_report_cannot_write_is_named_and_leaves_nothing(
    tmp_path: Path,
) -> None:
    dashboard = {"id": 7, "dashboard_title": "x" + chr(0xD800), "changed_on": "x"}
    source = Given([_live_chart(PIE)], [_live_dataset()], dashboard)

    with pytest.raises(VerifyError) as raised:
        inventory(_request(tmp_path, [("dashboard://w@1", "7")], source=source))

    assert str(raised.value) == (
        f"cannot write the inventory to {tmp_path / 'out'}: a text Superset answered is not "
        "Unicode (a lone surrogate)"
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["semantic"]


def test_an_id_that_is_not_utf8_text_is_refused_before_superset_is_asked(tmp_path: Path) -> None:
    """A byte of an argument that is not UTF-8 reaches Python as a lone surrogate."""

    def source() -> Any:
        pytest.fail("Superset was asked")

    request = InventoryRequest(
        semantic=_semantic(tmp_path),
        dashboards=[("dashboard://w@1", "7" + chr(0xDCFF))],
        output=tmp_path / "out",
        source=source,
    )

    with pytest.raises(VerifyError) as raised:
        inventory(request)

    assert str(raised.value) == (
        "--dashboard 'dashboard://w@1': the Superset id or slug is not UTF-8 text"
    )
    assert not (tmp_path / "out").exists()


def test_the_command_refuses_an_id_that_is_not_utf8_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(inventory_module, "RestSupersetSource", lambda client: Recorded())
    argv = ["decision", "inventory", str(_semantic(tmp_path)), "--output", str(tmp_path / "o")]

    code = main([*argv, "--dashboard", "dashboard://w@1=7" + chr(0xDCFF)])

    out, err = capsys.readouterr()
    assert (code, out) == (2, "")
    assert err == (
        "error: --dashboard 'dashboard://w@1': the Superset id or slug is not UTF-8 text\n"
    )


#: Text a credential could stand for, in a saved metric's SQL.
_SECRET = "DUMMY-SECRET-42"


def _with_secret_metric() -> dict[str, Any]:
    dataset = _live_dataset()
    dataset["metrics"].append(
        {"metric_name": "m", "expression": f"sumIf(revenue, region != '{_SECRET}')"}
    )
    return dataset


def test_a_saved_metric_is_named_by_its_name_never_by_its_sql(tmp_path: Path) -> None:
    """A saved metric's SQL is the dataset's and can hold a credential: the reasons the report
    and the `--json` answer carry name the metric only. The read SQL holds it, by design."""
    chart = _live_chart(PIE)
    chart["form_data"]["metric"] = "m"
    source = Given([chart], [_with_secret_metric()])

    report = inventory(_request(tmp_path, [("dashboard://w@1", "7")], source=source))

    assert report.dashboards[0].charts[0].reasons == [
        "form_data.metric: the saved metric 'm' is not AGG(column)",
        "saved: the last change is not a save within two seconds",
        "composite_sql: the dataset definition is not readable",
    ]
    assert _SECRET not in report.model_dump_json()
    assert _SECRET not in (tmp_path / "out" / INVENTORY_REPORT).read_text(encoding="utf-8")
    assert _SECRET in (tmp_path / "out" / "w@1.yaml").read_text(encoding="utf-8")


def test_a_refused_dashboard_records_no_saved_metric_sql(tmp_path: Path) -> None:
    charts = []
    for ident in range(1, MAX_CHARTS + 2):
        chart = _live_chart(PIE)
        chart["id"] = ident
        chart["form_data"]["metric"] = "m"
        charts.append(chart)
    source = Given(charts, [_with_secret_metric()])

    report = inventory(_request(tmp_path, [("dashboard://w@1", "7")], source=source))

    (dashboard,) = report.dashboards
    assert dashboard.written is None
    assert len(dashboard.charts) == MAX_CHARTS + 1
    assert _SECRET not in (tmp_path / "out" / INVENTORY_REPORT).read_text(encoding="utf-8")


def test_of_a_saved_metric_sql_a_reason_names_only_the_column_it_aggregates(
    tmp_path: Path,
) -> None:
    """`AGG(column)` is the typed measure; the model's check names that column when the model
    has no such column, and nothing else of the metric's SQL."""
    chart = _live_chart(PIE)
    chart["form_data"]["metric"] = "m"
    dataset = _live_dataset()
    dataset["metrics"].append({"metric_name": "m", "expression": 'SUM("no_such_column")'})

    report = inventory(
        _request(tmp_path, [("dashboard://w@1", "7")], source=Given([chart], [dataset]))
    )

    assert report.dashboards[0].charts[0].reasons == [
        "the spec does not validate: chart 'chart_122': unknown measure column 'no_such_column' "
        "in dm.sales_daily",
        "saved: the last change is not a save within two seconds",
        "composite_sql: the dataset definition is not readable",
    ]


@pytest.mark.parametrize("grain", ["P1D", "P1W", "P1M", "P3M", "P1Y"])
@pytest.mark.parametrize(
    "native",
    [
        "Date32",
        "date32",
        "DateTime64(3)",
        "Nullable(DateTime64(6, 'UTC'))",
        "LowCardinality(Nullable(DATE32))",
        "SimpleAggregateFunction(max, DateTime64(3))",
        "SIMPLEAGGREGATEFUNCTION(MAX, DATETIME64(3))",
        "SimpleAggregateFunction(any, Date32)",
        "SimpleAggregateFunction(min, Date)",
        "TIMESTAMP",
        "Date64",
        "NULL",
        "",
        None,
    ],
)
def test_a_grain_of_a_column_not_date_or_datetime_is_not_typed(
    native: str | None, grain: str
) -> None:
    """Superset starts the period of `toDateTime(col)`: a value outside 1970 to 2106 falls into
    another period there than in the IR. A grain is typed on a `Date` or `DateTime` column only;
    the days of a `Date` the cast moves are a boundary the spec names."""
    read = _read({"time_grain_sqla": grain}, base=LINE, dataset=_date_of_type(native))

    assert _not_typed(read) == (
        f"form_data.time_grain_sqla = {grain!r}: Superset casts 'date', a column of "
        f"{native or 'no named type'}, to DateTime (1970 to 2106) first; a grain is typed on "
        "a Date or DateTime column only"
    )


@pytest.mark.parametrize(
    ("grain", "typed"),
    [
        ("P1W", TimeGrain.WEEK),
        ("P1M", TimeGrain.MONTH),
        ("P3M", TimeGrain.QUARTER),
        ("P1Y", TimeGrain.YEAR),
    ],
)
@pytest.mark.parametrize(
    "native",
    [
        "Date",
        "DATE",
        "DateTime",
        "DATETIME",
        "DateTime('UTC')",
        "DATETIME('EUROPE/MOSCOW')",
        "DateTime( 'UTC' )",
        "Nullable(DateTime)",
        "Nullable(DateTime('UTC'))",
        "LowCardinality(Date)",
        " LowCardinality(Nullable(DATETIME)) ",
    ],
)
def test_a_grain_of_a_date_or_datetime_column_is_typed(
    native: str, grain: str, typed: TimeGrain
) -> None:
    read = _read({"time_grain_sqla": grain}, base=LINE, dataset=_date_of_type(native))

    assert _typed(read).query.time_grain is typed
