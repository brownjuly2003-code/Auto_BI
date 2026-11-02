"""Generated names and values retain their characters and SQL structure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import sqlglot
from sqlglot import exp

from auto_bi.adapters.datalens.dataset import build_dataset_payload
from auto_bi.adapters.superset.form_data import build_form_data
from auto_bi.agent.sql_guard import guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql, generate_source_sql
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec, Measure, QueryFilter
from auto_bi.semantic.gateway import metric_node
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.verification.sql_contract import _render
from tests.test_catalog_literals import read_literals
from tests.test_sql_contract import aggregate_contract, postgres_model, semantic_model
from tests.test_superset_adapter import make_spec as superset_spec

VALUES = [
    'x\\" FROM mart WHERE 1=1 --',
    "x\\' OR 1=1 --",
    r"a\nb",
    r"a\x27b",
    "a\\\\b",
    "tail\\",
    'a"b',
    "a'b",
    "a\nb",
    "a\u00a5b",
]
SITES = ["flat", "window", "compare", "histogram", "source", "reference", "dataset", "scaled"]
NO_BACKSLASH_VALUES = {
    "single_quote": "a'b",
    "double_quote": 'a"b',
    "backtick": "a`b",
    "line_feed": "a\nb",
    "tab": "a\tb",
    "unicode": "a\u00a5b",
}


def chart_query(site: str, name: str, value: str, op: str = "=") -> ChartQuery:
    measure: dict[str, Any] = {"column": "revenue", "agg": "sum", "label": name}
    extra: dict[str, Any] = {}
    dimensions = ["date"]
    if site == "window":
        measure["transform"] = "running_total"
    elif site == "compare":
        measure["compare"] = {"column": "date", "grain": "month"}
        dimensions = []
    elif site == "histogram":
        dimensions = ["revenue"]
        measure["agg"] = "count"
        extra["bins"] = 5
    return ChartQuery(
        table="dm.sales_daily",
        dimensions=dimensions,
        measures=[Measure.model_validate(measure)],
        filters=[
            QueryFilter.model_validate(
                {"column": "city", "op": op, "value": value if op != "in" else [value]}
            )
        ],
        **extra,
    )


def site_sql(site: str, name: str, value: str, dialect: str, op: str = "=") -> str:
    query = chart_query(site, name, value, op)
    if site == "source":
        return generate_source_sql(query.table, [name], [], dialect=dialect)
    if site == "reference":
        node = metric_node(MetricContract.model_validate(aggregate_contract()))
        node.grain.keys = [name]
        return _render(node, exp.Sum(this=exp.column(name, quoted=True)), dialect)
    if site in {"dataset", "scaled"}:
        model = semantic_model() if dialect == "clickhouse" else postgres_model()
        payload = build_dataset_payload(
            query,
            model,
            workbook_id="w",
            connection_id="c",
            name="d",
            measure_scale=(1000.0, [name]) if site == "scaled" else None,
        )
        result: str = payload["dataset"]["sources"][0]["parameters"]["subsql"]
        return result
    return generate_chart_sql(query, dialect=dialect)


def signature(sql: str, dialect: str, name: str, value: str) -> list[dict[str, Any]]:
    guard_sql(sql, dialect=dialect)
    trees = sqlglot.parse(sql, read=dialect)
    assert len(trees) == 1
    tree = trees[0]
    assert tree is not None
    assert name in [node.name for node in tree.find_all(exp.Identifier)]
    for node in tree.walk():
        node.meta.clear()
        assert not node.comments
        if isinstance(node, exp.Identifier) and node.name == name:
            node.set("this", "marker_name")
        elif isinstance(node, (exp.Literal, exp.ByteString)) and node.this == value:
            node.replace(exp.Literal.string("marker_value"))
    return tree.dump()


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("value", VALUES)
def test_identifiers_preserve_structure(site: str, dialect: str, value: str) -> None:
    actual = site_sql(site, value, "marker_value", dialect)
    baseline = site_sql(site, "marker_name", "marker_value", dialect)
    assert signature(actual, dialect, value, "marker_value") == signature(
        baseline, dialect, "marker_name", "marker_value"
    )


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
@pytest.mark.parametrize("site", [s for s in SITES if s not in {"source", "reference"}])
@pytest.mark.parametrize("op", ["=", "!=", ">=", "<=", "in"])
@pytest.mark.parametrize("value", VALUES)
def test_filter_values_preserve_structure(site: str, dialect: str, op: str, value: str) -> None:
    actual = site_sql(site, "marker_name", value, dialect, op)
    baseline = site_sql(site, "marker_name", "marker_value", dialect, op)
    tree = sqlglot.parse_one(actual, read=dialect)
    literals = [
        n.this
        for n in tree.walk()
        if isinstance(n, exp.ByteString) or (isinstance(n, exp.Literal) and n.is_string)
    ]
    assert value in literals
    for standard in (True, False):
        outside, values = read_literals(actual, dialect, standard)
        base_outside, base_values = read_literals(baseline, dialect, standard)
        assert values == [value if v == "marker_value" else v for v in base_values]
        assert outside == base_outside
    assert signature(actual, dialect, "marker_name", value) == signature(
        baseline, dialect, "marker_name", "marker_value"
    )


def superset_sql(site: str, name: str) -> str:
    measure = Measure(
        column=name if site in {"source", "ratio"} else "revenue", agg="sum", label=name
    )
    if site == "ratio":
        measure.denominator = Measure(column=name, agg="count_distinct")
    query = ChartQuery(
        table="dm.sales_daily",
        dimensions=["date", name] if site == "heatmap" else ["date"],
        measures=[measure],
    )
    chart = ChartSpec.model_validate(
        {
            "id": "c1",
            "title": "c",
            "viz": "heatmap" if site == "heatmap" else "big_number" if site == "kpi" else "line",
            "query": query,
        }
    )
    payload = build_form_data(
        chart,
        1,
        from_source=site in {"source", "ratio"},
        heatmap_y_pad=2 if site == "heatmap" else None,
    )
    metric = (
        payload["groupby"]
        if site == "heatmap"
        else payload["metric"] if site == "kpi" else payload["metrics"][0]
    )
    return "SELECT " + str(metric["sqlExpression"])


@pytest.mark.parametrize("site", ["own", "source", "ratio", "kpi", "heatmap"])
@pytest.mark.parametrize("value", VALUES)
def test_superset_identifiers(site: str, value: str) -> None:
    assert signature(superset_sql(site, value), "clickhouse", value, "unused") == signature(
        superset_sql(site, "marker_name"), "clickhouse", "marker_name", "unused"
    )


def ordinary_sql() -> dict[str, str]:
    result = {
        f"{dialect}/{site}": site_sql(site, "revenue_total", "London", dialect)
        for dialect in ("clickhouse", "postgres")
        for site in SITES
    }
    result.update(
        {
            f"superset/{site}": superset_sql(site, "revenue_total")
            for site in ("own", "source", "ratio", "kpi", "heatmap")
        }
    )
    result.update(fixture_sql())
    for case, text in NO_BACKSLASH_VALUES.items():
        for mode in ("name", "value"):
            name, value = (text, "London") if mode == "name" else ("revenue_total", text)
            for dialect in ("clickhouse", "postgres"):
                for site in SITES:
                    if mode == "value" and site in {"source", "reference"}:
                        continue
                    result[f"hostile/{case}/{mode}/{dialect}/{site}"] = site_sql(
                        site, name, value, dialect
                    )
            if mode == "name":
                for site in ("own", "source", "ratio", "kpi", "heatmap"):
                    if case == "double_quote" and site == "heatmap":
                        continue
                    result[f"hostile/{case}/name/superset/{site}"] = superset_sql(site, name)
            variants = fixture_sql(
                name=text if mode == "name" else None,
                value=text if mode == "value" else None,
            )
            result.update({f"hostile/{case}/{mode}/{key}": sql for key, sql in variants.items()})
    return result


def varied_query(query: ChartQuery, name: str | None, value: str | None) -> ChartQuery:
    query = query.model_copy(deep=True)
    if name is not None:
        for measure in query.measures:
            measure.label = name
    if value is not None:
        query.filters.append(QueryFilter(column="region", op="=", value=value))
    return query


def fixture_sql(name: str | None = None, value: str | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for chart in superset_spec().charts:
        chart.query = varied_query(chart.query, name, value)
        for from_source in (False, True):
            if value is not None:
                continue  # These metric fragments contain no filter SQL.
            payload = build_form_data(chart, 42, from_source=from_source)
            metric = payload["metric"] if chart.id == "kpi" else payload["metrics"][0]
            result[f"adapter/superset/{chart.id}/{from_source}"] = metric["sqlExpression"]
        for dialect in ("clickhouse", "postgres"):
            payload = build_dataset_payload(
                chart.query,
                semantic_model() if dialect == "clickhouse" else postgres_model(),
                workbook_id="wb1",
                connection_id="conn1",
                name="auto_bi__ds",
            )
            result[f"adapter/datalens/{chart.id}/{dialect}"] = payload["dataset"]["sources"][0][
                "parameters"
            ]["subsql"]
    for path in sorted((Path(__file__).parent / "fixtures/golden_llm").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for call in raw["calls"]:
            if call["schema"] != "DashboardSpec":
                continue
            spec = DashboardSpec.model_validate(call["response"])
            for chart in spec.charts:
                chart.query = varied_query(chart.query, name, value)
                for dialect in ("clickhouse", "postgres"):
                    key = f"golden/{path.stem}/{dialect}"
                    result[key] = (
                        result.get(key, "")
                        + generate_chart_sql(chart.query, dialect=dialect)
                        + "\n"
                    )
    return result


def test_ordinary_sql_sha256() -> None:
    expected = json.loads(
        (Path(__file__).parent / "fixtures/generated_sql_sha256.json").read_text(encoding="utf-8")
    )
    assert {
        key: hashlib.sha256(sql.encode("utf-8")).hexdigest() for key, sql in ordinary_sql().items()
    } == expected


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("value", ["a\0b", "a\ud800b", "a\udfffb"])
def test_unrepresentable_names_refused(site: str, dialect: str, value: str) -> None:
    with pytest.raises(ValueError, match="NUL or surrogate"):
        site_sql(site, value, "ordinary", dialect)


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
@pytest.mark.parametrize("site", [s for s in SITES if s not in {"source", "reference"}])
@pytest.mark.parametrize("value", ["a\0b", "a\ud800b", "a\udfffb"])
def test_unrepresentable_values_refused(site: str, dialect: str, value: str) -> None:
    with pytest.raises(ValueError, match="NUL or surrogate"):
        site_sql(site, "ordinary", value, dialect)


@pytest.mark.parametrize("site", ["own", "source", "ratio", "kpi", "heatmap"])
@pytest.mark.parametrize("value", ["a\0b", "a\ud800b", "a\udfffb"])
def test_superset_unrepresentable_names_refused(site: str, value: str) -> None:
    with pytest.raises(ValueError, match="NUL or surrogate"):
        superset_sql(site, value)


def test_generated_sql_verifier_version() -> None:
    from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION

    assert int(VERIFIER_VERSION) >= 126
    assert CHECK_VERSION == "81"
