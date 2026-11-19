"""E4b-1: rendered reads, area boundaries and the cost/order of GETs."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from auto_bi.decision.impact import _ChartResolver
from auto_bi.inventory import superset as reader
from tests.test_superset_inventory import MODEL, PIE, Given, _client, _live_chart, _live_dataset

SQL = "SELECT region, SUM(revenue) AS total FROM dm.sales_daily GROUP BY region"


class Rendered(Given):
    def __init__(self) -> None:
        chart = _live_chart(PIE)
        chart["form_data"]["contributionMode"] = "row"
        dataset = _live_dataset()
        dataset["database"]["backend"] = "clickhousedb"
        super().__init__([chart], [dataset])
        self.form = chart["form_data"]
        self.dataset = dataset
        self.params = copy.deepcopy(self.form)
        self.context: dict[str, Any] = {
            "datasource": {"id": dataset["id"], "type": "table"},
            "form_data": copy.deepcopy(self.params),
            "queries": [{"post_processing": [{"operation": "contribution"}]}],
        }
        self.row = {
            "id": PIE,
            "changed_on_utc": "2026-11-19T10:00:00Z",
            "last_saved_at": "2026-11-19T10:00:00",
        }
        self.status = 200
        self.body: dict[str, Any] = {"result": [{"query": SQL + ";"}]}
        #: What `GET chart/{id}` adds or overrides: its `viz_type` column is the dashboard's.
        self.detail: dict[str, Any] = {}
        #: `GET dataset/{id}`: the whole definition, of which the dashboard answer is a part.
        self.definition_status = 200
        self.definition: dict[str, Any] = copy.deepcopy(dataset)
        self.calls: list[tuple[str, int]] = []

    def saves(self, dashboard_id: int) -> Sequence[Mapping[str, Any]]:
        self.calls.append(("saves", dashboard_id))
        return [dict(self.row, id=c["id"]) for c in self._charts]

    def chart(self, chart_id: int) -> Mapping[str, Any]:
        self.calls.append(("chart", chart_id))
        return {
            "viz_type": self.form.get("viz_type"),
            "params": json.dumps(self.params),
            "query_context": json.dumps(self.context),
            **self.detail,
        }

    def rendered(self, chart_id: int) -> tuple[int, Mapping[str, Any]]:
        self.calls.append(("rendered", chart_id))
        return self.status, self.body

    def dataset_definition(self, dataset_id: int) -> tuple[int, Mapping[str, Any]]:
        self.calls.append(("definition", dataset_id))
        return self.definition_status, {"result": self.definition}

    def read(self) -> reader.DashboardRead:
        return reader.inventory_dashboard("dashboard://rendered@1", "a-slug", self, MODEL)


def fallback(source: Rendered, check: str) -> reader.DashboardRead:
    read = source.read()
    assert read.spec is not None, read.record.refusals
    chart = read.record.charts[0]
    assert chart.form is reader.ChartForm.READ_SQL
    assert chart.reasons[0].startswith("form_data.")
    assert any(reason.startswith(f"{check}:") for reason in chart.reasons[1:]), chart.reasons
    return read


def test_rendered_is_raw_sql_and_never_computes_the_postprocessed_metric() -> None:
    source = Rendered()
    read = source.read()
    assert read.spec is not None
    assert read.record.charts[0].form.value == "rendered_sql"
    assert read.record.charts[0].reasons == [
        "form_data.contributionMode = 'row' post-processes the result"
    ]
    tile = read.spec.charts[0]
    assert tile.query.raw_sql == SQL
    assert tile.query.table == "dm.sales_daily"
    assert not tile.query.measures
    resolver = _ChartResolver([], {}, [])
    impact = resolver._match_raw(
        "dashboard://rendered@1",
        tile.id,
        tile.query,
        {},
        {"dm.sales_daily.revenue"},
        reaches_product=True,
    )
    assert impact is not None and impact.relation.value == "reads_field"
    assert not any(g.code in {"chart.sql_partial", "chart.opaque_sql"} for g in resolver.gaps())
    assert source.calls == [
        ("saves", 7),
        ("chart", PIE),
        ("definition", source.dataset["id"]),
        ("rendered", PIE),
    ]


@pytest.mark.parametrize("delta", ["00", "02", "02.000001", "03"])
def test_saved_two_seconds_is_inclusive(delta: str) -> None:
    source = Rendered()
    source.row["last_saved_at"] = f"2026-11-19T10:00:{delta}"
    if delta in {"00", "02"}:
        assert source.read().record.charts[0].form.value == "rendered_sql"
    else:
        fallback(source, "saved")
        assert source.calls == [("saves", 7)]


@pytest.mark.parametrize("field", ["changed_on_utc", "last_saved_at"])
@pytest.mark.parametrize("value", [None, "", "invalid"])
def test_saved_requires_both_dates(field: str, value: Any) -> None:
    source = Rendered()
    source.row[field] = value
    fallback(source, "saved")


@pytest.mark.parametrize("side", ["params", "form"])
@pytest.mark.parametrize("value", [123, None])
def test_fresh_compares_keys_on_both_sides_even_null(side: str, value: Any) -> None:
    source = Rendered()
    target = source.params if side == "params" else source.context["form_data"]
    target["only_one_side"] = value
    fallback(source, "fresh")
    assert ("rendered", PIE) not in source.calls


@pytest.mark.parametrize(
    "key", ["slice_id", "dashboards", "url_params", "force", "result_format", "result_type"]
)
def test_fresh_ignores_identity_and_request_keys(key: str) -> None:
    source = Rendered()
    source.params[key] = "different"
    assert source.read().record.charts[0].form.value == "rendered_sql"


@pytest.mark.parametrize("kind", ["missing", "empty", "dataset", "form_dataset", "type"])
def test_fresh_requires_context_and_the_dashboard_dataset(kind: str) -> None:
    source = Rendered()
    if kind == "missing":
        source.context = None  # type: ignore[assignment]
    elif kind == "empty":
        source.context["form_data"] = {}
    elif kind == "dataset":
        source.context["datasource"]["id"] = 999
    elif kind == "form_dataset":
        source.context["form_data"]["datasource"] = "999__table"
    else:
        source.context["datasource"]["type"] = "query"
    fallback(source, "fresh")


def test_fresh_identity_datasource_is_ignored_only_in_params() -> None:
    source = Rendered()
    source.params["datasource"] = "ignored__table"
    source.context["form_data"].pop("datasource")
    assert source.read().record.charts[0].form.value == "rendered_sql"


@pytest.mark.parametrize("viz", ["line", "deck_scatter", "time_table", "future_viz"])
def test_api_is_a_positive_list_and_costs_no_new_get_for_legacy_or_unknown(viz: str) -> None:
    source = Rendered()
    source.form["viz_type"] = viz
    read = source.read()
    assert read.record.charts[0].form is reader.ChartForm.READ_SQL
    assert any(r.startswith("api:") for r in read.record.charts[0].reasons)
    assert source.calls == []


@pytest.mark.parametrize(
    "viz",
    [
        "big_number",
        "big_number_total",
        "box_plot",
        "bubble_v2",
        "echarts_area",
        "echarts_timeseries",
        "echarts_timeseries_bar",
        "echarts_timeseries_line",
        "echarts_timeseries_scatter",
        "echarts_timeseries_smooth",
        "echarts_timeseries_step",
        "funnel",
        "gauge_chart",
        "graph_chart",
        "handlebars",
        "heatmap_v2",
        "histogram_v2",
        "mixed_timeseries",
        "pie",
        "pivot_table_v2",
        "pop_kpi",
        "radar",
        "sankey_v2",
        "sunburst_v2",
        "table",
        "tree_chart",
        "treemap_v2",
        "waterfall",
        "word_cloud",
    ],
)
def test_every_modern_chart_viz_in_the_412_preset_can_use_rendered_sql(viz: str) -> None:
    source = Rendered()
    for form in (source.form, source.params, source.context["form_data"]):
        form["viz_type"] = viz
    assert source.read().record.charts[0].form is reader.ChartForm.RENDERED_SQL


@pytest.mark.parametrize("backend", ["clickhouse", "clickhousedb"])
def test_rendered_sql_is_the_account_and_time_specific_answer(backend: str) -> None:
    source = Rendered()
    source.dataset["database"]["backend"] = backend
    for form in (source.form, source.params, source.context["form_data"]):
        form["time_range"] = "Last week"
    texts = []
    # The source has already rendered relative dates and the account's row predicates.
    # Neither belongs to a viewer-independent measure or to an inventory-side RLS check.
    for day, region in (("2026-11-12", "East"), ("2026-11-19", "West")):
        sql = (
            "SELECT region, revenue FROM dm.sales_daily\n"
            f"WHERE date >= '{day}' AND region = '{region}'"
        )
        source.body = {"result": [{"query": sql + ";"}]}
        read = source.read()
        assert read.spec is not None
        assert read.record.charts[0].form is reader.ChartForm.RENDERED_SQL
        assert not read.spec.charts[0].query.measures
        texts.append(read.spec.charts[0].query.raw_sql)
        assert texts[-1] == sql
    assert texts[0] != texts[1]


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_reports_rendered_sql_as_a_separate_form(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    from auto_bi.cli import main
    from auto_bi.decision import use_cases

    record = Rendered().read().record
    record.written = "rendered@1.yaml"
    report = use_cases.InventoryReport(reader_version=2, dashboards=[record])
    monkeypatch.setattr(use_cases, "inventory", lambda request: report)
    args = [
        "decision",
        "inventory",
        "semantic",
        "--dashboard",
        "dashboard://rendered@1=7",
        "--output",
        "out",
    ]
    assert main(args + (["--json"] if as_json else [])) == 0
    out = capsys.readouterr().out
    if as_json:
        assert json.loads(out) == report.model_dump(mode="json")
    else:
        assert "0 typed, 1 rendered SQL, 0 read as SQL" in out


@pytest.mark.parametrize("where", ["dataset", "column", "metric", "form", "params", "context"])
@pytest.mark.parametrize("marker", ["{{ current_username() }}", "{% if true %}x{% endif %}"])
def test_plain_jinja_is_checked_before_data(where: str, marker: str) -> None:
    source = Rendered()
    if where == "dataset":
        source.dataset["sql"] = f"SELECT revenue FROM dm.sales_daily WHERE region = '{marker}'"
    elif where in {"column", "metric"}:
        # Superset 4.1.2 renders a calculated column's and a metric's expression as well.
        key, name = ("columns", "column_name") if where == "column" else ("metrics", "metric_name")
        source.dataset.setdefault(key, []).append(
            {name: "templated", "expression": f"concat(region, '{marker}')"}
        )
    elif where == "form":
        source.form["nested"] = {"expression": marker}
    else:
        source.params["nested"] = marker
        source.context["form_data"]["nested"] = marker
    # The string literal lets the conservative D6 SQL pass its guard.
    fallback(source, "plain")
    assert ("rendered", PIE) not in source.calls
    if where in {"dataset", "column", "metric", "form"}:
        assert source.calls == []


@pytest.mark.parametrize("excluded", [[], [PIE]])
@pytest.mark.parametrize("listed", [[], [PIE]])
def test_plain_takes_a_chart_out_only_by_excluded_and_never_by_charts_in_scope(
    excluded: list[int], listed: list[int]
) -> None:
    source = Rendered()
    source._dashboard["json_metadata"] = json.dumps(
        {
            "native_filter_configuration": [
                {
                    "id": "filter",
                    "scope": {"excluded": excluded},
                    "chartsInScope": listed,
                }
            ]
        }
    )
    read = source.read()
    assert read.spec is not None
    # Out of the scope only when `excluded` names the chart and `chartsInScope` does not:
    # an empty `chartsInScope` keeps no chart out, and one that names an excluded chart is
    # read as D6 reads it — the filter applies.
    if excluded and not listed:
        assert read.record.charts[0].form.value == "rendered_sql"
    else:
        assert read.record.charts[0].form is reader.ChartForm.READ_SQL
        assert any(r.startswith("plain:") for r in read.record.charts[0].reasons)
        assert source.calls == []


@pytest.mark.parametrize("where", ["form", "params", "query"])
@pytest.mark.parametrize("key", ["annotation_layers", "time_compare"])
def test_single_checks_every_place_before_rendering(where: str, key: str) -> None:
    source = Rendered()
    if where == "form":
        source.form[key] = ["extra"]
    elif where == "params":
        source.params[key] = ["extra"]
        source.context["form_data"][key] = ["extra"]
    else:
        source.context["queries"].append(
            {"time_offsets" if key == "time_compare" else key: ["extra"]}
        )
    fallback(source, "single")
    assert ("rendered", PIE) not in source.calls
    if where == "form":
        assert source.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"result": []},
        {"result": [{}]},
        {"result": [{"query": SQL, "error": "secret"}]},
        {"result": [{"query": SQL}, {"error": "secret"}]},
    ],
)
def test_answer_requires_nonempty_queries_without_errors(body: dict[str, Any]) -> None:
    source = Rendered()
    source.body = body
    fallback(source, "answer")


def test_typed_chart_needs_no_new_get_even_with_no_saved_context() -> None:
    source = Rendered()
    source.form.pop("contributionMode")
    assert source.read().record.charts[0].form is reader.ChartForm.TYPED
    assert source.calls == []


def test_chart_list_is_lazy_and_shared_and_limit_adds_no_get() -> None:
    source = Rendered()
    second = copy.deepcopy(source._charts[0])
    second["id"] = PIE + 1
    source._charts = [source._charts[0], second]
    read = source.read()
    assert read.spec is not None
    # The dataset definition is asked for once per dataset of the dashboard.
    assert source.calls == [
        ("saves", 7),
        ("chart", PIE),
        ("definition", source.dataset["id"]),
        ("rendered", PIE),
        ("chart", PIE + 1),
        ("rendered", PIE + 1),
    ]
    source._charts = [dict(second, id=n) for n in range(13)]
    source.calls.clear()
    assert source.read().spec is None
    assert source.calls == []


def test_postgres_is_translated_without_changing_column_reads() -> None:
    source = Rendered()
    source.dataset["database"]["backend"] = "postgresql"
    sql = "SELECT date::date, SUM(revenue) FROM dm.sales_daily GROUP BY date::date"
    source.body = {"result": [{"query": sql}]}
    read = source.read()
    assert read.spec is not None and read.record.charts[0].form.value == "rendered_sql"
    translated = read.spec.charts[0].query.raw_sql
    assert translated is not None and translated != sql
    assert extract_column_refs(sql, dialect="postgres") == extract_column_refs(translated)


def test_translation_that_changes_reads_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Rendered()
    source.dataset["database"]["backend"] = "postgresql"
    monkeypatch.setattr(reader.sqlglot, "transpile", lambda *a, **kw: ["SELECT 1"])
    fallback(source, "rendered_sql")


@pytest.mark.parametrize("backend", ["", "unknown"])
def test_unknown_backend_is_conservative(backend: str) -> None:
    source = Rendered()
    source.dataset["database"]["backend"] = backend
    fallback(source, "rendered_sql")


def test_multiple_queries_are_not_silently_truncated() -> None:
    source = Rendered()
    source.body["result"].append({"query": "SELECT orders FROM dm.sales_daily"})
    fallback(source, "rendered_sql")


@pytest.mark.parametrize(
    "sql", ["DELETE FROM dm.sales_daily", "SELECT 'secret", SQL + "; SELECT 1"]
)
def test_guard_failure_falls_back_and_keeps_dashboard(sql: str) -> None:
    source = Rendered()
    source.body = {"result": [{"query": sql}]}
    read = fallback(source, "rendered_sql")
    assert "secret" not in str(read.record)


def test_gate_refusal_of_rendered_tile_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Rendered()
    original = reader.gate_refusals

    def refuses(spec: Any, model: Any) -> list[str]:
        if spec.charts[0].query.raw_sql == SQL:
            return ["a refused rendered spec"]
        return original(spec, model)

    monkeypatch.setattr(reader, "gate_refusals", refuses)
    fallback(source, "rendered_sql")


# --- r1: the viz the dashboard shows, the whole query_context, the whole dataset definition ---


@pytest.mark.parametrize("where", ["column", "params", "detail"])
def test_fresh_requires_the_saved_params_of_the_viz_the_dashboard_shows(where: str) -> None:
    source = Rendered()
    if where == "column":
        # A REST save of `viz_type` alone: the dashboard draws a histogram of `orders` from the
        # chart's column while params and query_context are still the pie's.
        source.form["viz_type"] = "histogram_v2"
        source.form["column"] = "orders"
    elif where == "params":
        for form in (source.params, source.context["form_data"]):
            form["viz_type"] = "histogram_v2"
    else:
        source.detail["viz_type"] = "histogram_v2"
    read = source.read()
    assert read.spec is not None
    chart = read.record.charts[0]
    assert chart.form is reader.ChartForm.READ_SQL
    assert "fresh: the saved params are not of the viz the dashboard shows" in chart.reasons
    assert ("definition", source.dataset["id"]) not in source.calls
    assert ("rendered", PIE) not in source.calls
    # The read SQL selects `*`: the gate does not call the tile untouched by `orders`; it
    # names the tile read in part.
    tile = read.spec.charts[0]
    resolver = _ChartResolver([], {}, [])
    resolver._match_raw(
        "dashboard://rendered@1",
        tile.id,
        tile.query,
        {},
        {"dm.sales_daily.orders"},
        reaches_product=True,
    )
    assert [(g.code, g.subject) for g in resolver.gaps()] == [
        ("chart.sql_partial", f"dashboard://rendered@1#{tile.id}")
    ]


def test_a_rendered_tile_is_read_as_its_sql_reads_and_a_star_keeps_sql_partial() -> None:
    # r2: the gate reads a rendered tile through its SQL like any SQL tile; over a virtual
    # dataset `SELECT *` Superset renders the dataset as a subquery, and the tile stays read in
    # part even though it is `rendered_sql`.
    source = Rendered()
    for dataset in (source.dataset, source.definition):
        dataset["sql"] = "SELECT * FROM dm.sales_daily"
    source.body = {
        "result": [
            {
                "query": "SELECT region, SUM(revenue) AS total "
                "FROM (SELECT * FROM dm.sales_daily) AS virtual_table GROUP BY region"
            }
        ]
    }
    read = source.read()
    assert read.spec is not None
    assert read.record.charts[0].form is reader.ChartForm.RENDERED_SQL
    tile = read.spec.charts[0]
    resolver = _ChartResolver([], {}, [])
    resolver._match_raw(
        "dashboard://rendered@1",
        tile.id,
        tile.query,
        {},
        {"dm.sales_daily.orders"},
        reaches_product=True,
    )
    assert [(g.code, g.subject) for g in resolver.gaps()] == [
        ("chart.sql_partial", f"dashboard://rendered@1#{tile.id}")
    ]


@pytest.mark.parametrize("where", ["where", "having", "column"])
def test_plain_checks_the_queries_of_the_context_too(where: str) -> None:
    source = Rendered()
    query = source.context["queries"][0]
    if where == "column":
        query["columns"] = [{"sqlExpression": "concat(region, '{{ 1 }}')", "label": "r"}]
    else:
        query["extras"] = {where: "1 = {{ 1 }}"}
    fallback(source, "plain")
    assert ("definition", source.dataset["id"]) not in source.calls
    assert ("rendered", PIE) not in source.calls


@pytest.mark.parametrize("key", ["columns", "metrics"])
def test_plain_reads_the_whole_dataset_definition_not_the_dashboard_answer(key: str) -> None:
    source = Rendered()
    # Not in the dashboard answer (Superset trims it to what form_data keys name), yet a
    # chart's sort or time column can read it, and Superset renders its template.
    name = "column_name" if key == "columns" else "metric_name"
    source.definition.setdefault(key, []).append(
        {name: "secret_sort", "expression": "concat(region, '{{ current_username() }}')"}
    )
    fallback(source, "plain")
    assert source.calls == [("saves", 7), ("chart", PIE), ("definition", source.dataset["id"])]


@pytest.mark.parametrize("kind", ["404", "no_result", "not_object", "other_id", "float_id"])
def test_an_unreadable_dataset_definition_falls_back(kind: str) -> None:
    source = Rendered()
    if kind == "404":
        source.definition_status = 404
    elif kind == "no_result":
        source.definition = None  # type: ignore[assignment]
    elif kind == "not_object":
        source.definition = [source.definition]  # type: ignore[assignment]
    elif kind == "other_id":
        source.definition["id"] = source.dataset["id"] + 1
    else:
        # Equal to the id in Python, yet not Superset's integer id.
        source.definition["id"] = float(source.dataset["id"])
    fallback(source, "plain")
    assert ("rendered", PIE) not in source.calls


def rest(
    source: Rendered,
    response: httpx.Response | Exception,
    definition: httpx.Response | None = None,
) -> reader.RestSupersetSource:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/login"):
            return httpx.Response(200, json={"access_token": "offline"})
        if path.endswith("/csrf_token/"):
            return httpx.Response(200, json={"result": "offline"})
        assert request.method == "GET"
        if path == "/api/v1/chart/":
            assert request.url.params["q"] == (
                "(columns:!(id,changed_on_utc,last_saved_at),"
                "filters:!((col:dashboards,opr:rel_m_m,value:7)),page_size:12)"
            )
            return httpx.Response(200, json={"result": source.saves(7)})
        if path == f"/api/v1/chart/{PIE}":
            return httpx.Response(200, json={"result": source.chart(PIE)})
        if path == f"/api/v1/chart/{PIE}/data/":
            assert dict(request.url.params) == {"type": "query", "format": "json"}
            if isinstance(response, Exception):
                raise response
            return response
        if path == f"/api/v1/dataset/{source.dataset['id']}":
            assert not request.url.params
            if definition is not None:
                return definition
            status, body = source.dataset_definition(source.dataset["id"])
            return httpx.Response(status, json=body)
        value = (
            source._charts
            if path.endswith("/charts")
            else source._datasets if path.endswith("/datasets") else source._dashboard
        )
        return httpx.Response(200, json={"result": value})

    return reader.RestSupersetSource(_client(handler))


@pytest.mark.parametrize("status", [200, 400, 404])
def test_rest_uses_numeric_dashboard_id_and_query_errors_keep_the_dashboard(status: int) -> None:
    source = Rendered()
    response = httpx.Response(status, json=source.body if status == 200 else {"error": "secret"})
    read = reader.inventory_dashboard("dashboard://rest@1", "a-slug", rest(source, response), MODEL)
    assert read.spec is not None
    assert read.record.charts[0].form.value == ("rendered_sql" if status == 200 else "read_sql")
    if status != 200:
        assert any(r.startswith("answer:") for r in read.record.charts[0].reasons)
    assert "secret" not in str(read.record)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "secret"}),
        httpx.Response(403, json={"error": "secret"}),
        httpx.Response(401, json={"error": "secret"}),
        httpx.Response(200, content=b"secret-not-json"),
        httpx.ConnectError("secret"),
    ],
)
def test_rest_source_failures_never_become_a_conservative_tile(response: Any) -> None:
    with pytest.raises(reader.SupersetSourceError) as raised:
        reader.inventory_dashboard(
            "dashboard://rest@1", "a-slug", rest(Rendered(), response), MODEL
        )
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("status", [202, 204, 301, 400, 404])
@pytest.mark.parametrize("body", [b"not-json-secret", b"", b"[]", b"null"])
def test_rest_requires_json_even_for_query_refusals(status: int, body: bytes) -> None:
    with pytest.raises(reader.SupersetSourceError):
        reader.inventory_dashboard(
            "dashboard://rest@1",
            "a-slug",
            rest(Rendered(), httpx.Response(status, content=body)),
            MODEL,
        )


@pytest.mark.parametrize("status", [202, 301])
def test_rest_success_shaped_body_cannot_hide_a_non_200_status(status: int) -> None:
    source = Rendered()
    with pytest.raises(reader.SupersetSourceError):
        reader.inventory_dashboard(
            "dashboard://rest@1",
            "a-slug",
            rest(source, httpx.Response(status, json=source.body)),
            MODEL,
        )


@pytest.mark.parametrize("status", [400, 404])
@pytest.mark.parametrize("stage", ["login", "csrf_token/"])
def test_a_query_request_login_failure_is_not_an_answer_failure(status: int, stage: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(stage):
            return httpx.Response(status, json={"error": "secret"})
        return httpx.Response(200, json={"access_token": "t"})

    with pytest.raises(reader.SupersetSourceError):
        reader.RestSupersetSource(_client(handler)).rendered(PIE)


def test_rest_dataset_definition_not_found_keeps_the_dashboard() -> None:
    source = Rendered()
    read = reader.inventory_dashboard(
        "dashboard://rest@1",
        "a-slug",
        rest(
            source, httpx.Response(200, json=source.body), httpx.Response(404, json={"m": "secret"})
        ),
        MODEL,
    )
    assert read.spec is not None
    assert read.record.charts[0].form is reader.ChartForm.READ_SQL
    assert "plain: the dataset definition is not readable" in read.record.charts[0].reasons
    assert "secret" not in str(read.record)


@pytest.mark.parametrize(
    "definition",
    [
        httpx.Response(403, json={"error": "secret"}),
        httpx.Response(500, json={"error": "secret"}),
        httpx.Response(401, json={"error": "secret"}),
        httpx.Response(400, json={"error": "secret"}),
        httpx.Response(200, content=b"secret-not-json"),
        httpx.Response(404, content=b"secret-not-json"),
    ],
)
def test_rest_dataset_definition_other_answers_are_source_errors(
    definition: httpx.Response,
) -> None:
    source = Rendered()
    with pytest.raises(reader.SupersetSourceError) as raised:
        reader.inventory_dashboard(
            "dashboard://rest@1",
            "a-slug",
            rest(source, httpx.Response(200, json=source.body), definition),
            MODEL,
        )
    assert "secret" not in str(raised.value)
