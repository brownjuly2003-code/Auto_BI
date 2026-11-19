"""E4b-2: reads from the 4.1.2 query builders, not every saved SQL fragment."""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from auto_bi.decision.impact import _ChartResolver
from auto_bi.inventory import superset as reader
from auto_bi.inventory.composite import VIZ_RULES
from tests.test_superset_inventory_rendered import MODEL, Rendered, rest


class Composite(Rendered):
    def __init__(self, viz: str = "pie", **form: Any) -> None:
        super().__init__()
        self.row["last_saved_at"] = None
        self.form.clear()
        self.form.update(
            datasource=f"{self.dataset['id']}__table",
            viz_type=viz,
            metric="total",
            **form,
        )
        self.definition["metrics"] = [
            {"metric_name": "total", "expression": "SUM(revenue) + 0"},
            {"metric_name": "other", "expression": "SUM(orders)"},
        ]
        self.definition["columns"].extend(
            [
                {"column_name": "region", "expression": None, "is_dttm": False},
                {"column_name": "orders", "expression": None, "is_dttm": False},
                {"column_name": "calculated", "expression": "orders + store_id"},
                {"column_name": "other_date", "expression": "date + 1", "is_dttm": True},
            ]
        )


def reads(source: Composite) -> tuple[set[str], str]:
    result = source.read()
    assert result.spec is not None, result.record.refusals
    assert result.record.charts[0].form.value == "composite_sql", result.record.charts[0].reasons
    tile = result.spec.charts[0]
    assert tile.query.raw_sql is not None and not tile.query.measures
    refs = extract_column_refs(tile.query.raw_sql)
    return {c.name for c in refs.attributed} | set(refs.unattributed), tile.query.raw_sql


@pytest.mark.parametrize(
    "key", ["metric", "metrics", "metric_2", "secondary_metric", "x", "y", "size"]
)
def test_metric_aliases_resolve_saved_expression(key: str) -> None:
    source = Composite()
    source.form.pop("metric")
    source.form[key] = ["other"] if key == "metrics" else "other"
    assert reads(source)[0] == {"orders"}


@pytest.mark.parametrize("key", ["columns", "all_columns", "groupby", "series", "series_columns"])
def test_column_keys_resolve_definition_not_trimmed_answer(key: str) -> None:
    source = Composite(**{key: ["calculated"]})
    names, sql = reads(source)
    assert names == {"revenue", "orders", "store_id"}
    assert "calculated" not in sql


@pytest.mark.parametrize(
    "mode,expected", [("aggregate", {"region", "revenue"}), ("raw", {"orders"})]
)
def test_query_mode_drops_inactive_columns_or_metrics(mode: str, expected: set[str]) -> None:
    source = Composite(query_mode=mode, groupby=["region"], all_columns=["orders"])
    assert reads(source)[0] == expected


@pytest.mark.parametrize(
    "axis",
    [
        "calculated",
        {"expressionType": "SQL", "sqlExpression": "orders + store_id", "label": "axis"},
    ],
)
def test_x_axis_plain_and_adhoc(axis: Any) -> None:
    assert reads(Composite("echarts_timeseries_line", x_axis=axis))[0] == {
        "revenue",
        "orders",
        "store_id",
    }


@pytest.mark.parametrize(
    "metric",
    [
        {"expressionType": "SIMPLE", "aggregate": "SUM", "column": {"column_name": "calculated"}},
        {"expressionType": "SQL", "sqlExpression": "SUM(orders + store_id)", "label": "m"},
    ],
)
def test_adhoc_metrics_read_expression_or_resolved_column(metric: Any) -> None:
    source = Composite()
    source.form["metric"] = metric
    assert reads(source)[0] == {"orders", "store_id"}


@pytest.mark.parametrize("key", ["series_limit_metric", "timeseries_limit_metric"])
def test_series_sort_metric_reads_its_expression(key: str) -> None:
    source = Composite(**{key: "other", "series_limit": 5, "groupby": ["region"]})
    assert reads(source)[0] == {"revenue", "region", "orders"}


def test_order_by_json_resolves_calculated_column_and_saved_metric() -> None:
    source = Composite(
        order_by_cols=[json.dumps(["calculated", True]), json.dumps(["other", False])]
    )
    assert reads(source)[0] == {"revenue", "orders", "store_id"}


@pytest.mark.parametrize(
    "form,expected",
    [
        (
            {
                "adhoc_filters": [
                    {
                        "expressionType": "SIMPLE",
                        "clause": "WHERE",
                        "subject": "calculated",
                        "operator": "==",
                        "comparator": 1,
                    }
                ]
            },
            {"orders", "store_id"},
        ),
        (
            {
                "adhoc_filters": [
                    {
                        "expressionType": "SQL",
                        "clause": "WHERE",
                        "sqlExpression": "orders > 1",
                    }
                ]
            },
            {"orders"},
        ),
        (
            {
                "adhoc_filters": [
                    {
                        "expressionType": "SQL",
                        "clause": "HAVING",
                        "sqlExpression": "SUM(orders) > 1",
                    }
                ]
            },
            {"orders"},
        ),
        ({"where": "store_id > 0 -- end"}, {"store_id"}),
        ({"having": "SUM(orders) > 0"}, {"orders"}),
        ({"filters": [{"col": "store_id", "op": "==", "val": 1}]}, {"store_id"}),
        ({"extra_filters": [{"col": "orders", "op": ">", "val": 1}]}, {"orders"}),
    ],
)
def test_every_filter_read_is_kept(form: dict[str, Any], expected: set[str]) -> None:
    assert reads(Composite(**form))[0] == {"revenue"} | expected


@pytest.mark.parametrize("time_key", ["granularity", "granularity_sqla"])
def test_time_range_reads_time_column_expression(time_key: str) -> None:
    source = Composite(**{time_key: "other_date", "time_range": "Last week"})
    assert reads(source)[0] == {"date", "revenue"}


def test_granularity_replaces_temporal_axis_even_without_time_range() -> None:
    source = Composite(
        "echarts_timeseries_line",
        x_axis="date",
        granularity_sqla="offset_time",
        time_range="No filter",
    )
    source.definition["columns"].append(
        {"column_name": "offset_time", "expression": "store_id + 1", "is_dttm": True}
    )
    assert "store_id" in reads(source)[0]


def test_a_non_temporal_time_column_reads_the_main_time_column() -> None:
    # helpers.get_sqla_query: a granularity outside the temporal columns becomes main_dttm_col.
    source = Composite(granularity_sqla="region", time_range="Last week")
    source.definition["main_dttm_col"] = "other_date"
    assert reads(source)[0] == {"date", "revenue"}


def test_a_temporal_range_filter_alone_applies_the_time_column() -> None:
    # QueryObjectFactory takes the time range from the first TEMPORAL_RANGE filter.
    temporal = {
        "expressionType": "SIMPLE",
        "clause": "WHERE",
        "subject": "orders",
        "operator": "TEMPORAL_RANGE",
        "comparator": "Last week",
    }
    source = Composite(granularity_sqla="other_date", adhoc_filters=[temporal])
    assert reads(source)[0] == {"orders", "date", "revenue"}
    source = Composite(
        granularity_sqla="other_date", adhoc_filters=[temporal | {"comparator": "No filter"}]
    )
    assert reads(source)[0] == {"orders", "revenue"}


@pytest.mark.parametrize("viz", ["waterfall", "box_plot"])
def test_the_legacy_time_column_stands_in_for_an_empty_axis(viz: str) -> None:
    source = Composite(viz, granularity_sqla="other_date", groupby=["region"])
    assert reads(source)[0] == {"date", "region", "revenue"}


@pytest.mark.parametrize(
    "axis",
    [
        {"expressionType": "SIMPLE", "sqlExpression": "orders", "label": "bad_axis"},
        {"expressionType": None, "sqlExpression": "orders", "label": "bad_axis"},
        {"expressionType": "SQL", "sqlExpression": "orders"},
        {"expressionType": "SQL", "label": "bad_axis"},
        5,
    ],
)
@pytest.mark.parametrize("viz", ["echarts_timeseries_line", "big_number", "heatmap_v2"])
def test_an_axis_isxaxisset_does_not_take_is_not_composed(axis: Any, viz: str) -> None:
    # Not set, the axis gives way to is_timeseries or DTTM_ALIAS: Superset reads the time
    # column, not the object's expression (review r1 counterexample: `date`, not `orders`).
    source = Composite(viz, x_axis=axis, granularity_sqla="date", time_range="No filter")
    result = source.read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1] == (
        "composite_sql: form_data['x_axis'] is not a column the 4.1.2 builders take for an axis"
    )


@pytest.mark.parametrize(
    "form",
    [
        # review r2 counterexample: the axis reads the dataset column, not the time column.
        {"x_axis": "__timestamp", "granularity_sqla": "date", "time_range": "No filter"},
        {
            "adhoc_filters": [
                {
                    "expressionType": "SIMPLE",
                    "clause": "WHERE",
                    "subject": "__timestamp",
                    "operator": "==",
                    "comparator": 1,
                }
            ]
        },
    ],
)
def test_a_dataset_column_named_like_the_time_alias_is_not_composed(form: dict[str, Any]) -> None:
    source = Composite("echarts_timeseries_line", **form)
    source.definition["columns"].append(
        {"column_name": "__timestamp", "expression": "orders", "is_dttm": False}
    )
    result = source.read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1] == (
        "composite_sql: the dataset has a column named __timestamp, Superset's time alias"
    )


TIME_ALIAS = (
    "composite_sql: form_data['{key}'] names the time alias __timestamp, which a builder can make"
    " SQL"
)


@pytest.mark.parametrize("grain", [None, "P1D"])
@pytest.mark.parametrize(
    "viz,form,key",
    [
        # review r3: normalizeTimeColumn makes the set axis an expression.
        ("echarts_timeseries_line", {"x_axis": "__timestamp"}, "x_axis"),
        ("waterfall", {"x_axis": "__timestamp"}, "x_axis"),
        ("heatmap_v2", {"x_axis": "__timestamp"}, "x_axis"),
        # review r4: the temporal column lookups of table, box plot and pivot table.
        (
            "table",
            {
                "query_mode": "aggregate",
                "groupby": ["__timestamp"],
                "temporal_columns_lookup": {"__timestamp": True},
            },
            "groupby",
        ),
        (
            "box_plot",
            {"columns": ["__timestamp"], "temporal_columns_lookup": {"__timestamp": True}},
            "columns",
        ),
        (
            "pivot_table_v2",
            {"groupbyRows": ["__timestamp"], "temporal_columns_lookup": {"__timestamp": True}},
            "groupbyRows",
        ),
        # Without a lookup the string stays the alias; refused all the same.
        ("table", {"query_mode": "aggregate", "groupby": ["__timestamp"]}, "groupby"),
    ],
)
def test_the_time_alias_as_a_column_is_not_composed(
    viz: str, form: dict[str, Any], key: str, grain: str | None
) -> None:
    # Absent from the metadata, the expression is literal SQL `__timestamp` — a physical
    # column the definition does not list — not the time column.
    form = {**form, "granularity_sqla": "date", "time_range": "No filter"}
    if grain:
        form["time_grain_sqla"] = grain
    result = Composite(viz, **form).read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1] == TIME_ALIAS.format(key=key)


def test_an_axis_without_expression_type_is_an_adhoc_axis() -> None:
    axis = {"sqlExpression": "orders", "label": "o"}
    source = Composite("echarts_timeseries_line", x_axis=axis, granularity_sqla="date")
    assert reads(source)[0] == {"orders", "revenue"}


@pytest.mark.parametrize("column", [{"sqlExpression": "orders"}, {"label": "orders"}])
def test_a_column_object_that_is_not_an_expression_is_not_composed(column: Any) -> None:
    result = Composite("table", query_mode="aggregate", groupby=[column]).read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1] == (
        "composite_sql: form_data['groupby'] has a column object Superset does not read as an"
        " expression"
    )


def test_an_adhoc_column_naming_a_calculated_column_reads_its_expression() -> None:
    axis = {"expressionType": "SQL", "sqlExpression": "calculated", "label": "calculated"}
    names, sql = reads(Composite("echarts_timeseries_line", x_axis=axis))
    assert names == {"orders", "store_id", "revenue"}
    assert "calculated" not in sql


PROCESSED_NAME = (
    "composite_sql: the dataset has a column name Superset's expression processing can change"
)


@pytest.mark.parametrize(
    "name",
    ["(orders) --tail", "orders /* note */", "orders # note", "orders\r", "orders\n"],
)
def test_a_column_name_expression_processing_can_change_is_not_composed(name: str) -> None:
    # review r5: adhoc_column_to_sqla processes the axis expression before it looks the name
    # up — sanitize_clause ends a trailing comment with a line feed, the template step cuts
    # one — so the metadata column is missed and literal SQL reads `orders`.
    source = Composite("echarts_timeseries_line", x_axis=name, granularity_sqla="date")
    source.definition["columns"].append(
        {"column_name": name, "expression": "revenue", "is_dttm": False}
    )
    result = source.read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1] == PROCESSED_NAME


def test_such_a_column_name_unused_by_the_chart_still_stops_composition() -> None:
    # Any processed expression can land on it; the dataset is refused as a whole.
    source = Composite("pie", groupby=["region"])
    source.definition["columns"].append(
        {"column_name": "region --old", "expression": "orders", "is_dttm": False}
    )
    assert source.read().record.charts[0].reasons[-1] == PROCESSED_NAME


@pytest.mark.parametrize("sql", ["calculated\n", "calculated\r\n", "calculated\r"])
def test_an_expression_the_template_step_makes_a_column_name_is_not_composed(sql: str) -> None:
    # Jinja's rendering cuts a trailing line feed and makes CR a line feed: the processed
    # expression names `calculated`, read through its metadata as `orders + store_id`.
    axis = {"expressionType": "SQL", "sqlExpression": sql, "label": "c"}
    result = Composite("echarts_timeseries_line", x_axis=axis).read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1] == (
        "composite_sql: form_data['x_axis'] has an expression the template step can turn into a"
        " column name"
    )


CARRIAGE_RETURN = (
    "composite_sql: form_data['{key}'] reads an expression with a carriage return, which the"
    " template step changes"
)


@pytest.mark.parametrize("cr", ["\r", "\r\n"])
@pytest.mark.parametrize(
    "key,value",
    [
        # review r6: Jinja makes `"orders<CR>id"` the name `orders<LF>id` — another column.
        ("x_axis", {"expressionType": "SQL", "sqlExpression": '"orders{cr}id"', "label": "c"}),
        ("metric", {"expressionType": "SQL", "sqlExpression": 'SUM("orders{cr}id")', "label": "m"}),
        ("where", 'region = "a{cr}b"'),
    ],
)
def test_an_expression_with_a_carriage_return_is_not_composed(
    key: str, value: Any, cr: str
) -> None:
    value = json.loads(json.dumps(value).replace("{cr}", json.dumps(cr)[1:-1]))
    source = Composite("echarts_timeseries_line", granularity_sqla="date")
    source.form[key] = value
    result = source.read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1] == CARRIAGE_RETURN.format(key=key)


def test_a_saved_expression_with_a_carriage_return_is_not_composed() -> None:
    source = Composite("pie", groupby=["calculated"])
    source.definition["columns"][-2]["expression"] = '"orders\rid" + store_id'
    assert source.read().record.charts[0].reasons[-1] == CARRIAGE_RETURN.format(key="groupby")


def test_dataset_sql_with_a_carriage_return_is_not_composed() -> None:
    source = Composite("pie", groupby=["region"])
    source.definition["sql"] = 'SELECT "a\rb" AS region, revenue FROM dm.sales_daily'
    assert source.read().record.charts[0].reasons[-1] == (
        "composite_sql: the dataset SQL has a carriage return, which the template step changes"
    )


def test_a_trailing_line_feed_naming_no_column_is_still_composed() -> None:
    axis = {"expressionType": "SQL", "sqlExpression": "orders + 1\n", "label": "c"}
    assert reads(Composite("echarts_timeseries_line", x_axis=axis))[0] == {"orders", "revenue"}


def test_a_jinja_comment_is_jinja_for_composition() -> None:
    # The template step drops `{# … #}`; what is left can name a dataset column.
    axis = {"expressionType": "SQL", "sqlExpression": "calculated{# note #}", "label": "c"}
    result = Composite("echarts_timeseries_line", x_axis=axis).read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1] == "composite_sql: form_data carries Jinja"


def test_order_by_the_label_of_a_selected_metric_reads_that_metric() -> None:
    metric = {"expressionType": "SQL", "sqlExpression": "MAX(orders)", "label": "peak"}
    source = Composite("table", metrics=[metric], order_by_cols=['["peak", false]'])
    source.form.pop("metric")
    assert reads(source)[0] == {"orders"}


def test_jinja_in_the_definition_sql_alone_is_not_composed() -> None:
    source = Composite()
    source.definition["sql"] = "SELECT * FROM dm.sales_daily WHERE {{ 1 }} = 1"
    result = source.read()
    assert result.spec is not None
    chart = result.record.charts[0]
    assert chart.form is reader.ChartForm.READ_SQL
    assert chart.reasons[-1] == "composite_sql: the dataset SQL carries Jinja"


def test_always_filter_main_time_and_legacy_timeseries() -> None:
    source = Composite(granularity_sqla="other_date", time_range="Last week")
    source.definition["columns"].append(
        {"column_name": "indexed_time", "expression": "store_id + 1", "is_dttm": True}
    )
    source.definition.update(main_dttm_col="indexed_time", always_filter_main_dttm=True)
    assert reads(source)[0] == {"date", "store_id", "revenue"}
    source = Composite("big_number", granularity_sqla="date", time_range="No filter")
    assert reads(source)[0] == {"date", "revenue"}


def test_mixed_suffix_applies_to_filters_columns_metrics_and_order() -> None:
    source = Composite(
        "mixed_timeseries",
        x_axis="date",
        groupby=["region"],
        metrics_b=["other"],
        groupby_b=["store_id"],
        where_b="orders > 1",
        order_by_cols_b=['["calculated", true]'],
    )
    assert reads(source)[0] == {"revenue", "orders", "store_id", "region", "date"}


@pytest.mark.parametrize(
    "viz,form",
    [
        ("pivot_table_v2", {"groupbyRows": ["store_id"], "groupbyColumns": ["region"]}),
        ("graph_chart", {"source": "store_id", "target": "region", "source_category": "orders"}),
        ("tree_chart", {"id": "store_id", "parent": "region", "name": "orders"}),
        ("sankey_v2", {"source": "store_id", "target": "region"}),
        ("bubble_v2", {"entity": "store_id", "series": "region"}),
        ("histogram_v2", {"column": "orders", "groupby": ["region"]}),
        ("table", {"percent_metrics": ["other"], "groupby": ["region"]}),
        ("pop_kpi", {"cols": ["region"]}),
        ("heatmap_v2", {"x_axis": "store_id", "groupby": ["region"]}),
        ("waterfall", {"x_axis": "date", "groupby": ["region"]}),
        ("box_plot", {"columns": ["store_id"], "groupby": ["region"]}),
    ],
)
def test_viz_specific_query_fields(viz: str, form: dict[str, Any]) -> None:
    expected = {
        "pivot_table_v2": {"revenue", "store_id", "region"},
        "graph_chart": {"revenue", "store_id", "region", "orders"},
        "tree_chart": {"revenue", "store_id", "region", "orders"},
        "sankey_v2": {"revenue", "store_id", "region"},
        "bubble_v2": {"revenue", "store_id", "region"},
        "histogram_v2": {"orders", "region"},
        "table": {"revenue", "orders", "region"},
        "pop_kpi": {"revenue", "region"},
        "heatmap_v2": {"revenue", "store_id", "region"},
        "waterfall": {"revenue", "date", "region"},
        "box_plot": {"revenue", "store_id", "region"},
    }
    assert reads(Composite(viz, **form))[0] == expected[viz]


def test_unused_fragments_are_not_read_and_items_are_deduplicated() -> None:
    source = Composite(groupby=["region", "region"], where="orders > 0", having="orders > 0")
    source.definition["metrics"].append({"metric_name": "unused", "expression": "store_id + 1"})
    names, sql = reads(source)
    assert names == {"revenue", "region", "orders"}
    assert sql.count("orders > 0") == sql.count("region") == 1


@pytest.mark.parametrize(
    "form",
    [
        {"extra_form_data": {"interactive_groupby": ["orders"]}},
        {"annotation_layers": [{"name": "layer"}]},
        {"where": "orders > {{ value }}"},
    ],
)
def test_unrepeated_input_names_its_key(form: dict[str, Any]) -> None:
    source = Composite(**form)
    # Jinja in a display value still disqualifies composition; fallback SQL remains valid.
    if "where" in form:
        source.form["label"] = source.form.pop("where")
    result = source.read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    reason = result.record.charts[0].reasons[-1]
    assert reason.startswith("composite_sql:")
    assert "form_data" in reason
    if "extra_form_data" in form:
        assert "form_data['extra_form_data']" in reason
    else:
        assert source.calls == []


def test_legacy_residue_and_postprocessing_do_not_add_reads() -> None:
    source = Composite(
        "echarts_timeseries_line",
        x_axis="date",
        compare_lag=10,
        markup_type="html",
        time_compare=["1 year ago"],
        rolling_type="mean",
        rolling_periods=7,
        contributionMode="row",
        resample_rule="1D",
        resample_method="asfreq",
        cumulative=True,
    )
    assert reads(source)[0] == {"date", "revenue"}


@pytest.mark.parametrize("kind", ["404", "metric", "column", "jinja"])
def test_unreadable_definition_or_reference_falls_back(kind: str) -> None:
    source = Composite()
    if kind == "404":
        source.definition_status = 404
    elif kind == "metric":
        source.definition["metrics"] = []
    elif kind == "column":
        source.form["groupby"] = ["unknown"]
    else:
        source.definition["metrics"][0]["expression"] = "SUM(revenue) + {{ 1 }}"
    result = source.read()
    assert result.spec is not None
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1].startswith("composite_sql:")


@pytest.mark.parametrize("backend", ["", "unknown", "mysql"])
def test_backend_without_read_dialect_is_not_composed_nor_asked_for(backend: str) -> None:
    source = Composite()
    source.dataset["database"]["backend"] = backend
    result = source.read()
    assert result.spec is not None
    chart = result.record.charts[0]
    assert chart.form is reader.ChartForm.READ_SQL
    assert chart.reasons[-1] == "composite_sql: the dataset backend has no known read dialect"
    assert ("definition", source.dataset["id"]) not in source.calls


def test_postgresql_composite_is_translated_with_equal_reads() -> None:
    source = Composite()
    source.dataset["database"]["backend"] = "postgresql"
    source.definition["metrics"][0]["expression"] = "SUM(revenue::numeric)"
    names, sql = reads(source)
    assert names == {"revenue"}
    assert "::" not in sql and "CAST(" in sql


def test_postgresql_translation_that_changes_reads_is_not_composed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Composite()
    source.dataset["database"]["backend"] = "postgresql"
    monkeypatch.setattr(reader.sqlglot, "transpile", lambda *a, **kw: ["SELECT 1"])
    result = source.read()
    assert result.spec is not None
    chart = result.record.charts[0]
    assert chart.form is reader.ChartForm.READ_SQL
    assert chart.reasons[-1] == "composite_sql: translation changes column reads"


def test_virtual_dataset_remains_a_subquery_and_star_stays_partial() -> None:
    source = Composite()
    source.dataset["sql"] = source.definition["sql"] = " ; SELECT * FROM dm.sales_daily; \n"
    _, sql = reads(source)
    assert "FROM (\nSELECT * FROM dm.sales_daily\n) AS virtual_table" in sql
    result = source.read()
    assert result.spec is not None
    tile = result.spec.charts[0]
    resolver = _ChartResolver([], {}, [])
    resolver._match_raw(
        "dashboard://composite@1", tile.id, tile.query, {}, set(), reaches_product=False
    )
    assert [g.code for g in resolver.gaps()] == ["chart.sql_partial"]


def test_definition_is_shared_between_unsaved_charts() -> None:
    source = Composite()
    source._charts = [source._charts[0], copy.deepcopy(source._charts[0])]
    source._charts[1]["id"] += 1
    source.read()
    assert source.calls == [("saves", 7), ("definition", source.dataset["id"])]


def test_definition_is_shared_after_rendered_request_fails() -> None:
    source = Rendered()
    source.status = 400
    source._charts = [source._charts[0], copy.deepcopy(source._charts[0])]
    first = source._charts[0]["id"]
    source._charts[1]["id"] = first + 1
    result = source.read()
    assert all(c.form is reader.ChartForm.COMPOSITE_SQL for c in result.record.charts)
    assert source.calls == [
        ("saves", 7),
        ("chart", first),
        ("definition", source.dataset["id"]),
        ("rendered", first),
        ("chart", first + 1),
        ("rendered", first + 1),
    ]


def test_composite_whitelist_is_exactly_the_29_rendered_viz() -> None:
    assert set(VIZ_RULES) == reader._RENDERED_VIZ
    assert len(VIZ_RULES) == 29


def test_unknown_nonempty_key_is_named_and_escaped() -> None:
    result = Composite(**{"unread\nkey": "value"}).read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1] == (
        "composite_sql: form_data['unread\\nkey'] is not a key whose query effect is known"
    )


@pytest.mark.parametrize("excluded,listed", [(False, False), (True, True), (True, False)])
def test_composite_native_filter_uses_d6_scope(excluded: bool, listed: bool) -> None:
    source = Composite()
    chart = source._charts[0]["id"]
    source._dashboard["json_metadata"] = json.dumps(
        {
            "native_filter_configuration": [
                {
                    "id": "filter",
                    "scope": {"excluded": [chart] if excluded else []},
                    "chartsInScope": [chart] if listed else [],
                }
            ]
        }
    )
    result = source.read()
    if excluded and not listed:
        assert result.record.charts[0].form is reader.ChartForm.COMPOSITE_SQL
    else:
        assert result.record.charts[0].form is reader.ChartForm.READ_SQL
        assert result.record.charts[0].reasons[-1] == (
            "composite_sql: a native dashboard filter applies to the chart"
        )
        assert source.calls == []


def test_reader_version_is_three() -> None:
    assert reader.READER_VERSION == 3


@pytest.mark.parametrize("reverse", [False, True])
def test_reserved_time_column_wins_over_legacy_granularity(reverse: bool) -> None:
    filters = [
        {"col": "__time_col", "val": "date"},
        {"col": "__granularity", "val": "calculated"},
    ]
    source = Composite(
        granularity_sqla="other_date",
        time_range="Last week",
        extra_filters=list(reversed(filters)) if reverse else filters,
    )
    assert reads(source)[0] == {"date", "revenue"}


def test_reserved_legacy_granularity_does_not_override_form_column() -> None:
    source = Composite(
        granularity_sqla="date",
        time_range="Last week",
        extra_filters=[{"col": "__granularity", "val": "calculated"}],
    )
    assert reads(source)[0] == {"date", "revenue"}


def test_time_comparison_reads_axis_label_resolved_by_backend() -> None:
    source = Composite(
        "echarts_timeseries_line",
        x_axis={"expressionType": "SQL", "sqlExpression": "date", "label": "offset_time"},
        time_range="Last week",
        time_compare=["1 year ago"],
    )
    source.definition["columns"].append(
        {"column_name": "offset_time", "expression": "store_id + 1", "is_dttm": True}
    )
    source.definition["main_dttm_col"] = "date"
    assert reads(source)[0] == {"date", "store_id", "revenue"}


def test_mixed_uses_original_axis_for_both_queries() -> None:
    source = Composite(
        "mixed_timeseries",
        x_axis_b="region",
        granularity_sqla_b="other_date",
        time_range="No filter",
    )
    assert reads(source)[0] == {"date", "revenue"}


def test_time_comparison_reads_base_axis_column_label() -> None:
    source = Composite(
        "table",
        time_compare=["1 year ago"],
        time_range="Last week",
        columns=[
            {
                "expressionType": "SQL",
                "sqlExpression": "date",
                "label": "offset_time",
                "columnType": "BASE_AXIS",
            }
        ],
    )
    source.definition["columns"].append(
        {"column_name": "offset_time", "expression": "store_id + 1", "is_dttm": True}
    )
    source.definition["main_dttm_col"] = "date"
    assert reads(source)[0] == {"date", "store_id", "revenue"}


@pytest.mark.parametrize("value", [None, "", False])
def test_empty_time_override_retains_form_granularity(value: Any) -> None:
    source = Composite(
        granularity_sqla="date",
        time_range="Last week",
        extra_filters=[{"col": "__time_col", "val": value}],
    )
    assert reads(source)[0] == {"date", "revenue"}


def test_sql_sort_metric_remains_literal_even_when_it_names_a_calculated_column() -> None:
    source = Composite(
        order_by_cols=[
            [
                {"expressionType": "SQL", "sqlExpression": "orders", "label": "sort"},
                True,
            ]
        ]
    )
    source.definition["columns"] = [
        {**col, "expression": "store_id + 1"} if col["column_name"] == "orders" else col
        for col in source.definition["columns"]
    ]
    assert "orders" in reads(source)[0]


@pytest.mark.parametrize("expression_type", ["UNKNOWN", None, []])
def test_unreadable_sort_metric_names_order_key(expression_type: Any) -> None:
    source = Composite(
        order_by_cols=[
            [
                {"expressionType": expression_type, "sqlExpression": "orders", "label": "sort"},
                True,
            ]
        ]
    )
    result = source.read()
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert "composite_sql: form_data['order_by_cols']" in result.record.charts[0].reasons[-1]


@pytest.mark.parametrize("boundary", ["guard", "gate"])
def test_composite_guard_and_gate_refusals_preserve_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    source = Composite()
    if boundary == "guard":
        source.definition["metrics"][0]["expression"] = "'credential-secret"
    else:
        original = reader.gate_refusals

        def refusal(spec: Any, model: Any) -> list[str]:
            if spec.charts[0].query.raw_sql and "SUM(revenue) + 0" in spec.charts[0].query.raw_sql:
                return ["credential-secret"]
            return original(spec, model)

        monkeypatch.setattr(reader, "gate_refusals", refusal)
    result = source.read()
    assert result.spec is not None
    assert result.record.charts[0].form is reader.ChartForm.READ_SQL
    assert result.record.charts[0].reasons[-1].startswith("composite_sql:")
    assert "credential-secret" not in result.record.model_dump_json()


@pytest.mark.parametrize("status", [200, 404, 403, 500])
def test_rest_composite_definition_status(status: int) -> None:
    source = Composite()
    definition = httpx.Response(
        status,
        json={"result": source.definition} if status == 200 else {"error": "secret"},
    )
    api = rest(source, httpx.Response(500, json={}), definition)
    if status in {403, 500}:
        with pytest.raises(reader.SupersetSourceError, match=f"HTTP {status}") as error:
            reader.inventory_dashboard("dashboard://rest@1", "slug", api, MODEL)
        assert "secret" not in str(error.value)
    else:
        result = reader.inventory_dashboard("dashboard://rest@1", "slug", api, MODEL)
        assert result.spec is not None
        assert result.record.charts[0].form.value == (
            "composite_sql" if status == 200 else "read_sql"
        )


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_reports_composite_separately(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    from auto_bi.cli import main
    from auto_bi.decision import use_cases

    record = Composite().read().record
    record.written = "composite@1.yaml"
    report = use_cases.InventoryReport(reader_version=reader.READER_VERSION, dashboards=[record])
    monkeypatch.setattr(use_cases, "inventory", lambda request: report)
    args = [
        "decision",
        "inventory",
        "semantic",
        "--dashboard",
        "dashboard://composite@1=7",
        "--output",
        "out",
    ]
    assert main(args + (["--json"] if as_json else [])) == 0
    out = capsys.readouterr().out
    if as_json:
        assert json.loads(out) == report.model_dump(mode="json")
    else:
        assert "0 typed, 0 rendered SQL, 1 composite SQL, 0 read as SQL" in out
