"""E4a: the own ClickHouse corpus C2 and the probe dashboards, on the running Superset (41|61).

C2 is one chart per viz of the examples corpus that can be built over the demo DM, on the
stand-only table dm.sales_wide (sales_daily joined to stores and products; made by stand_up).
Geo viz
(world_map, country_map, mapbox, deck_*) and tree_chart (needs a parent-child table) are excluded.
Charts are created over the REST API, like the project's adapter does (no query_context); the UI
save is a separate step (measure.py --ui-save). Dashboards hold at most 12 charts (the IR's limit).

Probes (one factor each): native filter with a default, Jinja, RLS, relative time, several queries
of one tile, a stale query_context (made later by c2_stale.py), a dataset without a schema.
Idempotent per slug. Runs in the repo venv on the Mac as the stand admin; prints ids only.
Usage: venv/bin/python c2_setup.py 41|61
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/tmp/autobi_t47/repo")

from auto_bi.adapters.superset.client import SupersetClient, rison_eq_filter

STAND = Path(os.environ.get("E4A_STAND", Path.home() / "e4astand"))
ENV = dict(line.split("=", 1) for line in (STAND / ".env").read_text().splitlines() if "=" in line)
version = sys.argv[1]
base = "http://127.0.0.1:" + ("8088" if version == "41" else "8089")
client = SupersetClient(base, ENV["AUTO_BI_SUPERSET_USER"], ENV["AUTO_BI_SUPERSET_PASSWORD"])
ids: dict[str, Any] = {}


def find(path: str, column: str, value: str) -> int | None:
    found = client.get(path, params={"q": rison_eq_filter(column, value)})
    result = found.get("result") or []
    return int(result[0]["id"]) if result else None


database = find("/api/v1/database/", "database_name", "e4a ClickHouse")
if database is None:
    uri = f"clickhousedb://auto_bi_ro:{ENV['AUTO_BI_CH_PASSWORD']}@clickhouse:8123/dm"
    database = int(
        client.post(
            "/api/v1/database/",
            json={
                "database_name": "e4a ClickHouse",
                "sqlalchemy_uri": uri,
                "expose_in_sqllab": True,
            },
        )["id"]
    )
ids["database"] = database


def dataset(name: str, *, schema: str | None = "dm", sql: str | None = None) -> int:
    existing = find("/api/v1/dataset/", "table_name", name)
    if existing is not None:
        return existing
    body: dict[str, Any] = {"database": database, "table_name": name}
    if schema is not None:
        body["schema"] = schema
    if sql is not None:
        body["sql"] = sql
    return int(client.post("/api/v1/dataset/", json=body)["id"])


wide = dataset("sales_wide")
# The table of the connection's default database, named without a schema.
noschema = dataset("sales_daily", schema=None)
jinja = dataset(
    "sales_wide_jinja",
    sql=(
        "SELECT date, region, category, revenue, orders\nFROM dm.sales_wide\n"
        "WHERE region != '{{ current_username() }}'\n"
        "{% if url_param('region') %}AND region = '{{ url_param('region') }}'{% endif %}\n"
        "{% if filter_values('category') %}AND category IN {{ filter_values('category') | where_in }}"  # noqa: E501
        "{% endif %}"
    ),
)
stores = dataset("stores")
ids["datasets"] = {
    "stores": stores,
    "sales_wide": wide,
    "sales_wide_jinja": jinja,
    "sales_daily (no schema)": noschema,
}

NOF = {
    "clause": "WHERE",
    "comparator": "No filter",
    "expressionType": "SIMPLE",
    "operator": "TEMPORAL_RANGE",
    "subject": "date",
}


def simple(column: str, aggregate: str = "SUM") -> dict[str, Any]:
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": column},
        "aggregate": aggregate,
        "label": f"{aggregate}({column})",
    }


REV, ORD, ITEMS = simple("revenue"), simple("orders"), simple("items")
TS = {
    "x_axis": "date",
    "time_grain_sqla": "P1W",
    "metrics": [REV],
    "groupby": ["region"],
    "row_limit": 10000,
    "series_limit": 0,
    "order_desc": True,
    "comparison_type": "values",
}

VIZ: dict[str, dict[str, Any]] = {
    "table": {
        "query_mode": "aggregate",
        "groupby": ["region"],
        "metrics": [REV],
        "row_limit": 100,
        "order_desc": True,
    },
    "pivot_table_v2": {
        "groupbyRows": ["region"],
        "groupbyColumns": ["category"],
        "metrics": [REV],
        "aggregateFunction": "Sum",
        "row_limit": 10000,
    },
    "big_number_total": {"metric": REV},
    "big_number": {"metric": REV, "x_axis": "date", "time_grain_sqla": "P1W"},
    "pie": {"groupby": ["category"], "metric": REV, "row_limit": 100},
    "echarts_timeseries_line": TS,
    "echarts_timeseries_bar": {**TS, "orientation": "vertical"},
    "echarts_area": TS,
    "echarts_timeseries_scatter": TS,
    "echarts_timeseries_smooth": TS,
    "echarts_timeseries_step": TS,
    "mixed_timeseries": {
        "x_axis": "date",
        "time_grain_sqla": "P1W",
        "metrics": [REV],
        "groupby": [],
        "metrics_b": [ORD],
        "groupby_b": [],
        "adhoc_filters_b": [NOF],
        "row_limit": 10000,
        "row_limit_b": 10000,
    },
    "heatmap_v2": {"x_axis": "region", "groupby": "category", "metric": REV, "row_limit": 10000},
    "treemap_v2": {"groupby": ["region", "category"], "metric": REV, "row_limit": 10000},
    "sunburst_v2": {"columns": ["region", "category"], "metric": REV, "row_limit": 10000},
    "word_cloud": {"series": "city", "metric": REV, "row_limit": 100},
    "histogram_v2": {"column": "revenue", "groupby": ["region"], "bins": 10, "row_limit": 10000},
    "box_plot": {
        "columns": ["city"],
        "groupby": ["region"],
        "metrics": [REV],
        "whiskerOptions": "Tukey",
        "row_limit": 10000,
    },
    "funnel": {"groupby": ["format"], "metric": REV, "row_limit": 10},
    "gauge_chart": {"groupby": ["format"], "metric": REV, "row_limit": 10},
    "radar": {"groupby": ["region"], "metrics": [REV, ORD], "row_limit": 10},
    "sankey_v2": {"source": "region", "target": "category", "metric": REV, "row_limit": 1000},
    "graph_chart": {"source": "region", "target": "category", "metric": REV, "row_limit": 1000},
    "waterfall": {"x_axis": "date", "time_grain_sqla": "P1M", "groupby": ["region"], "metric": REV},
    "bubble_v2": {
        "entity": "city",
        "series": "region",
        "x": ORD,
        "y": ITEMS,
        "size": REV,
        "row_limit": 1000,
        "max_bubble_size": "25",
    },
    "chord": {"groupby": "region", "columns": "category", "metric": REV, "row_limit": 1000},
    "horizon": {
        "granularity_sqla": "date",
        "time_grain_sqla": "P1W",
        "groupby": ["region"],
        "metrics": [REV],
    },
    "para": {"series": "city", "metrics": [REV, ORD], "row_limit": 100},
    "line": {
        "granularity_sqla": "date",
        "time_grain_sqla": "P1W",
        "groupby": ["region"],
        "metrics": [REV],
    },
    "area": {
        "granularity_sqla": "date",
        "time_grain_sqla": "P1W",
        "groupby": ["region"],
        "metrics": [REV],
    },
    "dist_bar": {"groupby": ["region"], "metrics": [REV], "row_limit": 100},
    "bubble": {
        "entity": "city",
        "series": "region",
        "x": ORD,
        "y": ITEMS,
        "size": REV,
        "limit": 100,
        "max_bubble_size": "25",
    },
}


def chart(name: str, source: int, form_data: dict[str, Any], dashboard: int) -> int:
    params = {
        "time_range": "No filter",
        "adhoc_filters": [NOF],
        **form_data,
        "datasource": f"{source}__table",
    }
    return int(
        client.post(
            "/api/v1/chart/",
            json={
                "slice_name": name,
                "viz_type": form_data["viz_type"],
                "datasource_id": source,
                "datasource_type": "table",
                "params": json.dumps(params, ensure_ascii=False),
                "dashboards": [dashboard],
            },
        )["id"]
    )


def board(
    slug: str,
    title: str,
    charts: list[tuple[str, int, dict[str, Any]]],
    metadata: dict[str, Any] | None = None,
) -> int:
    existing = find("/api/v1/dashboard/", "slug", slug)
    if existing is not None:
        return existing
    made = int(
        client.post(
            "/api/v1/dashboard/", json={"dashboard_title": title, "slug": slug, "published": True}
        )["id"]
    )
    ids.setdefault("charts", {})[slug] = [chart(n, s, f, made) for n, s, f in charts]
    if metadata is not None:
        client.put(f"/api/v1/dashboard/{made}", json={"json_metadata": json.dumps(metadata)})
    return made


viz = [(f"C2 {name}", wide, {"viz_type": name, **form}) for name, form in VIZ.items()]
ids["dashboards"] = {}
for part in range(0, len(viz), 12):
    slug = f"c2-{part // 12 + 1}"
    ids["dashboards"][slug] = board(slug, f"C2 viz {part // 12 + 1}", viz[part : part + 12])

TABLE = {
    "viz_type": "table",
    "query_mode": "aggregate",
    "groupby": ["region"],
    "metrics": [REV],
    "row_limit": 100,
    "order_desc": True,
}
BAR = {"viz_type": "echarts_timeseries_bar", **TS}

# Native filter on region with a default value, scoped to the whole dashboard.
native = {
    "native_filter_configuration": [
        {
            "id": "NATIVE_FILTER-e4a-region",
            "name": "Регион",
            "filterType": "filter_select",
            "type": "NATIVE_FILTER",
            "targets": [{"datasetId": wide, "column": {"name": "region"}}],
            "controlValues": {
                "multiSelect": True,
                "enableEmptyFilter": False,
                "defaultToFirstItem": False,
                "inverseSelection": False,
                "searchAllOptions": False,
            },
            "defaultDataMask": {
                "extraFormData": {"filters": [{"col": "region", "op": "IN", "val": ["ЦФО"]}]},
                "filterState": {"value": ["ЦФО"]},
                "ownState": {},
            },
            "cascadeParentIds": [],
            "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
            "chartsInScope": [],
            "tabsInScope": [],
            "description": "",
        }
    ],
    "chart_configuration": {},
    "global_chart_configuration": {
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "chartsInScope": [],
    },
    "cross_filters_enabled": True,
}
ids["dashboards"]["p-filter"] = board(
    "p-filter",
    "P native filter",
    [("P filter table", wide, TABLE), ("P filter bar", wide, BAR)],
    native,
)
ids["dashboards"]["p-jinja"] = board(
    "p-jinja", "P Jinja", [("P jinja table", jinja, {**TABLE, "groupby": ["region", "category"]})]
)
ids["dashboards"]["p-rls"] = board(
    "p-rls",
    "P RLS",
    [
        (
            "P rls table",
            stores,
            {
                "viz_type": "table",
                "query_mode": "aggregate",
                "groupby": ["format"],
                "metrics": [simple("id", "COUNT")],
                "row_limit": 100,
                "adhoc_filters": [],
            },
        )
    ],
)
# The rule binds the role of the user probe's viewer (reader_role.py runs first).
rls = client.get(
    "/api/v1/rowlevelsecurity/", params={"q": rison_eq_filter("name", "e4a stores region")}
)
if not rls.get("result"):
    role_id = int(os.environ.get("E4A_READER_ROLE_ID", "0"))
    if role_id:
        client.post(
            "/api/v1/rowlevelsecurity/",
            json={
                "name": "e4a stores region",
                "filter_type": "Regular",
                "tables": [stores],
                "roles": [role_id],
                "clause": "region = 'ЦФО'",
                "group_key": "",
                "description": "E4a",
            },
        )
        ids["rls"] = "made"
ids["dashboards"]["p-time"] = board(
    "p-time",
    "P relative time",
    [
        (
            "P time last week",
            wide,
            {
                **TABLE,
                "time_range": "Last week",
                "adhoc_filters": [{**NOF, "comparator": "Last week"}],
            },
        ),
        (
            "P time grain",
            wide,
            {
                **BAR,
                "time_range": "Last quarter",
                "adhoc_filters": [{**NOF, "comparator": "Last quarter"}],
            },
        ),
    ],
)
ids["dashboards"]["p-multi"] = board(
    "p-multi",
    "P several queries",
    [
        (
            "P multi time_compare",
            wide,
            {
                **TS,
                "viz_type": "echarts_timeseries_line",
                "time_compare": ["1 year ago"],
                "comparison_type": "values",
            },
        ),
        ("P multi totals", wide, {**TABLE, "show_totals": True}),
        (
            "P multi big number trend",
            wide,
            {"viz_type": "big_number", "metric": REV, "x_axis": "date", "time_grain_sqla": "P1W"},
        ),
        (
            "P multi rolling",
            wide,
            {
                **TS,
                "viz_type": "echarts_timeseries_line",
                "rolling_type": "mean",
                "rolling_periods": 4,
            },
        ),
    ],
)
ids["dashboards"]["p-stale"] = board(
    "p-stale", "P stale query_context", [("P stale table", wide, TABLE)]
)
ids["dashboards"]["p-schema"] = board(
    "p-schema",
    "P dataset without schema",
    [("P schema table", noschema, {**TABLE, "groupby": ["store_id"]})],
)
print(json.dumps(ids, ensure_ascii=False))
(STAND / f"c2_ids_{version}.json").write_text(json.dumps(ids), encoding="utf-8")
