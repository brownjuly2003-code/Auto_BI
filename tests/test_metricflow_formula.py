"""A contract's formula resolved from a dbt semantic manifest (ADR 0008, slice S2).

Fixtures are the artifacts of `test_metricflow_manifest.py` (`dbt parse`, dbt-core 1.12.6,
metricflow 0.213.0), changed in memory and pinned again where a test needs a construct they do
not carry, and `parity/`: the artifact and the result of the parity matrix
(`scripts/metricflow_parity.py`) on PostgreSQL 16.15.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import re
import sys
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

from auto_bi.evidence.canonical import digest_bytes
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.semantic.metric_contract import MetricContract, MetricFormula
from auto_bi.semantic.metricflow import ManifestRead, read_semantic_manifest
from auto_bi.semantic.metricflow_formula import (
    _DEFAULT_AGG_PARAMS,
    _INPUT_MEASURE,
    _METRIC,
    _METRIC_INPUT,
    _RATIO_PARAMS,
    _SIMPLE_PARAMS,
    ADDITIVITY_UNPROVEN,
    CONSTRUCT_UNSUPPORTED,
    OUTSIDE_GRAIN,
    PARITY_UNPROVEN,
    PRIVATE_METRIC,
    PROVEN_PARITY,
    RATIO_UNPROVEN,
    REF_MISSING,
    RELATION_MISMATCH,
    RELATION_UNPROVEN,
    REVISION_MISMATCH,
    SOURCE_MISSING,
    TIME_MISMATCH,
    TIME_UNPROVEN,
    UNCLASSIFIED,
    UNDECLARED,
    DeclaredTable,
    FormulaResolution,
    is_dbt_formula,
    resolve_formula,
)
from auto_bi.verification.sql_contract import compile_metric_queries

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "dbt_semantic_manifest"
PARITY = FIXTURES / "parity"
TABLE = "dm.sales_daily"
AGGS = ("sum", "count", "count_distinct", "min", "max", "average")
#: The context of every row below: the grain table `SALES` declares, its event time a grain key.
CONTEXT = "postgres.date"
KINDS = ("decimal", "integer", "float", "date", UNCLASSIFIED, UNDECLARED)
TERMS = [
    f"{dialect}.{agg}.{kind}" for dialect in ("legacy", "v2") for agg in AGGS for kind in KINDS
]
EVERY_ROW = frozenset(
    f"{CONTEXT}:{row}"
    for row in (
        "legacy.measure_without_expr",
        "legacy.metric_expr",
        *TERMS,
        *(f"ratio.{top}/{bottom}" for top in TERMS for bottom in TERMS),
    )
)
SALES = DeclaredTable(
    "postgres",
    {
        "date": "date",
        "store_id": "integer",
        "revenue": "numeric(18,2)",
        "gross_margin": "numeric(18,2)",
    },
)
REVIEW = "review"
BLOCK = "block"
FILTER = {"where_filters": [{"where_sql_template": "{{ Dimension('sale__region') }} = 'north'"}]}


def _data(kind: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / kind / "semantic_manifest.json").read_bytes())
    return data


def _read(data: Any) -> ManifestRead:
    return read_semantic_manifest(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _index(items: list[dict[str, Any]], name: str) -> int:
    return next(index for index, item in enumerate(items) if item["name"] == name)


def _metric(data: dict[str, Any], name: str) -> dict[str, Any]:
    return data["metrics"][_index(data["metrics"], name)]


def _model(data: dict[str, Any], name: str) -> dict[str, Any]:
    return data["semantic_models"][_index(data["semantic_models"], name)]


def _measure(data: dict[str, Any], name: str) -> dict[str, Any]:
    return _model(data, "sales_daily")["measures"][
        _index(_model(data, "sales_daily")["measures"], name)
    ]


def _metric_path(data: dict[str, Any], name: str) -> str:
    return f"$.metrics[{_index(data['metrics'], name)}]"


def _measure_path(data: dict[str, Any], name: str) -> str:
    model = _index(data["semantic_models"], "sales_daily")
    position = _index(_model(data, "sales_daily")["measures"], name)
    return f"$.semantic_models[{model}].measures[{position}]"


def _dimension_path(data: dict[str, Any], name: str) -> str:
    model = _index(data["semantic_models"], "sales_daily")
    position = _index(_model(data, "sales_daily")["dimensions"], name)
    return f"$.semantic_models[{model}].dimensions[{position}]"


def _rows(*forms: str) -> frozenset[str]:
    return frozenset(f"{CONTEXT}:{form}" for form in forms)


def _contract(
    ref: str,
    pin: str,
    *,
    table: str = TABLE,
    keys: tuple[str, ...] = ("date", "store_id"),
    additivity: str = "non_additive",
    event_time: str = "date",
    source: str = "dbt_semantic_manifest",
) -> MetricContract:
    return MetricContract.model_validate(
        {
            "metadata": {
                "id": f"metric://sales/{ref}",
                "version": 1,
                "owner": "commercial-analytics",
                "status": "active",
            },
            "formula": {
                "kind": "external_ref",
                "external": {"source": source, "ref": ref, "source_revision": pin},
            },
            "grain": {"table": table, "keys": list(keys)},
            "additivity": additivity,
            "non_additive_axes": ["date"] if additivity == "semi_additive" else [],
            "time": {"event_time_column": event_time, "timezone": "Europe/Moscow"},
            "unit": {
                "unit": "rub",
                "precision": 2,
                "null_semantics": "null_is_missing",
                "zero_semantics": "zero_is_value",
            },
            "freshness": {"max_age": "PT6H"},
            "classification": "internal",
        }
    )


def _resolve(
    data: dict[str, Any],
    ref: str,
    *,
    database: str | None = "x",
    declared: DeclaredTable = SALES,
    proven: frozenset[str] = EVERY_ROW,
    **contract: Any,
) -> FormulaResolution:
    read = _read(data)
    assert read.manifest is not None, read.problems
    pin = read.manifest.closure_digest(ref)
    return resolve_formula(
        _contract(ref, pin, **contract), read, database=database, declared=declared, proven=proven
    )


def _gaps(resolution: FormulaResolution) -> list[tuple[str, str, str]]:
    return [(gap.code, gap.severity.value, gap.path) for gap in resolution.gaps]


def _aggregate(column: str, agg: str) -> MetricFormula:
    return MetricFormula.model_validate(
        {"kind": "typed_aggregate", "aggregate": {"table": TABLE, "column": column, "agg": agg}}
    )


def _ratio(top: tuple[str, str], bottom: tuple[str, str]) -> MetricFormula:
    return MetricFormula.model_validate(
        {
            "kind": "typed_ratio",
            "numerator": {"table": TABLE, "column": top[0], "agg": top[1]},
            "denominator": {"table": TABLE, "column": bottom[0], "agg": bottom[1]},
        }
    )


def _input(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "filter": None,
        "alias": None,
        "offset_window": None,
        "offset_to_grain": None,
    }


def _measure_input(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "filter": None,
        "alias": None,
        "join_to_timespine": False,
        "fill_nulls_with": None,
    }


def _v2_with_granularity(kind: str = "v2") -> dict[str, Any]:
    """A V2 artifact as dbt would write it if it kept the time dimension's granularity."""
    data = _data(kind)
    dimension = _model(data, "sales_daily")["dimensions"][0]
    assert dimension["name"] == "date" and dimension["type_params"] is None
    dimension["type_params"] = {"time_granularity": "day", "validity_params": None}
    return data


# --- what resolves --------------------------------------------------------------------


SUPPORTED: list[tuple[str, str, MetricFormula, frozenset[str]]] = [
    ("legacy", "revenue", _aggregate("revenue", "sum"), _rows("legacy.sum.decimal")),
    (
        "legacy",
        "gross_margin_total",
        _aggregate("gross_margin", "sum"),
        _rows("legacy.sum.decimal"),
    ),
    (
        "legacy",
        "active_stores",
        _aggregate("store_id", "count_distinct"),
        _rows("legacy.count_distinct.integer"),
    ),
    (
        "legacy",
        "gross_margin_pct",
        _ratio(("gross_margin", "sum"), ("revenue", "sum")),
        _rows("legacy.sum.decimal", "ratio.legacy.sum.decimal/legacy.sum.decimal"),
    ),
    (
        "wide_legacy",
        "gross_margin_total",
        _aggregate("gross_margin", "sum"),
        _rows("legacy.sum.decimal", "legacy.measure_without_expr"),
    ),
    (
        "wide_legacy",
        "revenue_created",
        _aggregate("revenue", "sum"),
        _rows("legacy.sum.decimal", "legacy.metric_expr"),
    ),
    ("wide_legacy", "sales_rows", _aggregate("store_id", "count"), _rows("legacy.count.integer")),
    ("wide_legacy", "revenue_min", _aggregate("revenue", "min"), _rows("legacy.min.decimal")),
    ("wide_legacy", "revenue_avg", _aggregate("revenue", "avg"), _rows("legacy.average.decimal")),
]


@pytest.mark.parametrize(("kind", "ref", "formula", "rows"), SUPPORTED)
def test_supported_metrics_resolve(
    kind: str, ref: str, formula: MetricFormula, rows: frozenset[str]
) -> None:
    data = _data(kind)
    resolution = _resolve(data, ref)
    assert resolution.gaps == ()
    assert resolution.formula == formula
    assert resolution.parity_rows == rows
    read = _read(data)
    assert read.manifest is not None
    assert resolution.closure_digest == read.manifest.closure_digest(ref)
    # the rows the recorded parity run proved are enough for all of them
    default = resolve_formula(
        _contract(ref, resolution.closure_digest), read, database="x", declared=SALES
    )
    assert (default.formula, default.gaps) == (formula, ())


@pytest.mark.parametrize(("kind", "ref", "formula", "rows"), SUPPORTED)
def test_each_unproven_row_is_named(
    kind: str, ref: str, formula: MetricFormula, rows: frozenset[str]
) -> None:
    data = _data(kind)
    path = _metric_path(data, ref)
    for row in rows:
        resolution = _resolve(data, ref, proven=frozenset(rows - {row}))
        assert resolution.formula is None
        assert _gaps(resolution) == [(PARITY_UNPROVEN, REVIEW, path)]
        assert resolution.gaps[0].reason.endswith(f"to agree on {row}")
        assert resolution.parity_rows == rows
    nothing = _resolve(data, ref, proven=frozenset())
    assert nothing.gaps[0].reason.endswith("to agree on " + ", ".join(sorted(rows)))


def test_parity_is_asked_only_of_a_formula_that_resolves() -> None:
    data = _data("legacy")
    resolution = _resolve(data, "cost_of_sales", proven=frozenset())
    assert _gaps(resolution) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_measure_path(data, 'revenue_expr')}.expr")
    ]
    assert resolution.parity_rows == frozenset()
    # a formula was built, and another gap keeps it: nothing is asked of parity
    other = _resolve(data, "revenue", proven=frozenset(), event_time="day")
    assert _gaps(other) == [(TIME_MISMATCH, BLOCK, f"{_dimension_path(data, 'date')}.name")]
    assert other.parity_rows == frozenset()


def test_one_fact_is_one_gap() -> None:
    data = _data("legacy")
    _model(data, "sales_daily")["dimensions"][0]["type_params"]["time_granularity"] = "month"
    params = _metric(data, "gross_margin_pct")["type_params"]
    params["denominator"]["name"] = "gross_margin_total"
    # a ratio over one metric twice: dbt writes its measure once
    params["input_measures"] = params["input_measures"][:1]
    path = f"{_dimension_path(data, 'date')}.type_params.time_granularity"
    assert _gaps(_resolve(data, "gross_margin_pct")) == [(TIME_UNPROVEN, REVIEW, path)]


def test_relation_and_time_are_checked_whatever_else_the_metric_holds() -> None:
    data = _data("legacy")
    _metric(data, "revenue")["filter"] = FILTER
    _measure(data, "revenue")["expr"] = "revenue - gross_margin"
    assert _gaps(_resolve(data, "revenue", table="dm.sales", event_time="day")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_metric_path(data, 'revenue')}.filter"),
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_measure_path(data, 'revenue')}.expr"),
        (TIME_MISMATCH, BLOCK, f"{_dimension_path(data, 'date')}.name"),
        (RELATION_MISMATCH, BLOCK, "$.semantic_models[0].node_relation"),
    ]


def test_reasons_carry_names_and_no_other_values_of_the_artifact() -> None:
    data = _data("legacy")
    _measure(data, "revenue")["expr"] = "revenue * 7919"
    _measure(data, "revenue")["agg_params"] = {"percentile": 0.7919}
    _model(data, "sales_daily")["dimensions"][0]["expr"] = "date_trunc('day', ts_7919)"
    _metric(data, "revenue")["type_params"]["measure"]["fill_nulls_with"] = 7919
    _metric(data, "revenue")["type_params"]["input_measures"][0]["fill_nulls_with"] = 7919
    resolution = _resolve(data, "revenue")
    assert len(resolution.gaps) == 4
    assert all("7919" not in gap.reason for gap in resolution.gaps), resolution.gaps


def test_v2_metrics_resolve_once_their_time_has_a_granularity() -> None:
    data = _v2_with_granularity()
    revenue = _resolve(data, "revenue")
    assert (revenue.formula, revenue.parity_rows) == (
        _aggregate("revenue", "sum"),
        _rows("v2.sum.decimal"),
    )
    ratio = _resolve(data, "gross_margin_pct")
    assert ratio.formula == _ratio(("gross_margin", "sum"), ("revenue", "sum"))
    assert ratio.parity_rows == _rows("v2.sum.decimal", "ratio.v2.sum.decimal/v2.sum.decimal")
    stores = _resolve(data, "active_stores")
    assert stores.formula == _aggregate("store_id", "count_distinct")
    # no V2 row is proven
    unproven = _resolve(data, "revenue", proven=PROVEN_PARITY)
    assert _gaps(unproven) == [(PARITY_UNPROVEN, REVIEW, _metric_path(data, "revenue"))]


def test_v2_time_dimension_without_granularity_is_review() -> None:
    data = _data("v2")
    for ref in ("revenue", "gross_margin_pct", "active_stores"):
        assert _gaps(_resolve(data, ref)) == [
            (TIME_UNPROVEN, REVIEW, f"{_dimension_path(data, 'date')}.type_params")
        ]


def test_v2_metric_takes_no_default_time() -> None:
    data = _v2_with_granularity()
    assert _model(data, "sales_daily")["defaults"]["agg_time_dimension"] == "date"
    _metric(data, "revenue")["type_params"]["metric_aggregation_params"][
        "agg_time_dimension"
    ] = None
    path = f"{_metric_path(data, 'revenue')}.type_params.metric_aggregation_params"
    assert _gaps(_resolve(data, "revenue")) == [
        (TIME_UNPROVEN, REVIEW, f"{path}.agg_time_dimension")
    ]


def test_v2_metric_without_expression_is_a_construct() -> None:
    data = _v2_with_granularity()
    _metric(data, "revenue")["type_params"]["expr"] = None
    path = _metric_path(data, "revenue")
    assert _gaps(_resolve(data, "revenue")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{path}.type_params.expr")
    ]


def test_v2_metric_with_input_measures_is_a_construct() -> None:
    data = _v2_with_granularity()
    # a measure for it to name, as the legacy artifact holds it
    _model(data, "sales_daily")["measures"] = [_measure(_data("legacy"), "revenue")]
    _metric(data, "revenue")["type_params"]["input_measures"] = [_measure_input("revenue")]
    path = _metric_path(data, "revenue")
    assert _gaps(_resolve(data, "revenue")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{path}.type_params.input_measures")
    ]


def test_v2_constructs_dbt_wrote() -> None:
    data = _v2_with_granularity("wide_v2")

    def at(ref: str, where: str) -> tuple[str, str, str]:
        params = f"{_metric_path(data, ref)}.type_params"
        return CONSTRUCT_UNSUPPORTED, REVIEW, f"{params}.{where}"

    aggregation = "metric_aggregation_params"
    assert _gaps(_resolve(data, "revenue_p90")) == [
        at("revenue_p90", f"{aggregation}.agg"),
        at("revenue_p90", f"{aggregation}.agg_params"),
    ]
    assert _gaps(_resolve(data, "revenue_median")) == [at("revenue_median", f"{aggregation}.agg")]
    assert _gaps(_resolve(data, "revenue_last")) == [
        at("revenue_last", f"{aggregation}.non_additive_dimension")
    ]
    assert _gaps(_resolve(data, "revenue_filled")) == [at("revenue_filled", "join_to_timespine")]
    hidden = f"{_metric_path(data, 'revenue_hidden')}.type_params.is_private"
    assert _gaps(_resolve(data, "revenue_hidden")) == [(PRIVATE_METRIC, REVIEW, hidden)]


def test_simple_metric_with_both_or_neither_way_to_aggregate_is_a_construct() -> None:
    both = _data("legacy")
    _metric(both, "revenue")["type_params"]["metric_aggregation_params"] = {
        "semantic_model": "sales_daily",
        "agg": "sum",
        "agg_params": None,
        "agg_time_dimension": "date",
        "non_additive_dimension": None,
    }
    path = f"{_metric_path(both, 'revenue')}.type_params"
    assert _gaps(_resolve(both, "revenue")) == [(CONSTRUCT_UNSUPPORTED, REVIEW, path)]
    neither = _data("legacy")
    params = _metric(neither, "revenue")["type_params"]
    params["measure"], params["input_measures"] = None, []
    assert _gaps(_resolve(neither, "revenue")) == [(CONSTRUCT_UNSUPPORTED, REVIEW, path)]


# --- the artifact and the pin ---------------------------------------------------------


def test_only_a_dbt_formula_is_resolved() -> None:
    read = _read(_data("legacy"))
    pin = "sha256:" + "0" * 64
    other = _contract("revenue", pin, source="ossie")
    assert not is_dbt_formula(other)
    with pytest.raises(ValueError, match="dbt_semantic_manifest"):
        resolve_formula(other, read, database="x", declared=SALES)
    typed = _contract("revenue", pin).model_copy(update={"formula": _aggregate("revenue", "sum")})
    assert not is_dbt_formula(typed)
    with pytest.raises(ValueError):
        resolve_formula(typed, read, database="x", declared=SALES)
    assert is_dbt_formula(_contract("revenue", pin))


def test_no_artifact_is_source_missing() -> None:
    resolution = resolve_formula(
        _contract("revenue", "sha256:" + "0" * 64), None, database="x", declared=SALES
    )
    assert _gaps(resolution) == [(SOURCE_MISSING, BLOCK, "")]
    assert (resolution.formula, resolution.closure_digest) == (None, "")


def test_refused_artifact_answers_with_its_problems() -> None:
    data = _data("legacy")
    data["metrics"][0]["type"] = "nonsense"
    read = _read(data)
    assert read.problems
    resolution = resolve_formula(
        _contract("revenue", "sha256:" + "0" * 64), read, database="x", declared=SALES
    )
    assert _gaps(resolution) == [(p.code, BLOCK, p.path) for p in read.problems]
    assert [gap.reason for gap in resolution.gaps] == [p.reason for p in read.problems]


def test_unknown_metric_is_ref_missing() -> None:
    read = _read(_data("legacy"))
    resolution = resolve_formula(
        _contract("nowhere", "sha256:" + "0" * 64), read, database="x", declared=SALES
    )
    assert _gaps(resolution) == [(REF_MISSING, BLOCK, "$.metrics")]


def test_another_pin_is_the_only_answer() -> None:
    # cost_of_sales also has a construct gap: nothing after the pin is read
    read = _read(_data("legacy"))
    assert read.manifest is not None
    pin = "sha256:" + "0" * 64
    resolution = resolve_formula(
        _contract("cost_of_sales", pin), read, database="x", declared=SALES
    )
    digest = read.manifest.closure_digest("cost_of_sales")
    assert _gaps(resolution) == [(REVISION_MISMATCH, BLOCK, "")]
    assert resolution.closure_digest == digest
    assert digest in resolution.gaps[0].reason and pin in resolution.gaps[0].reason


# --- compatibility with the contract --------------------------------------------------


@pytest.mark.parametrize(
    ("database", "relation", "expected"),
    [
        ("x", "x", []),
        (None, "x", [(RELATION_UNPROVEN, REVIEW)]),
        ("", "x", [(RELATION_MISMATCH, BLOCK)]),
        ("y", "x", [(RELATION_MISMATCH, BLOCK)]),
        ("x", None, [(RELATION_MISMATCH, BLOCK)]),
        ("", None, []),
        (None, None, []),
        ("", "", []),
        (None, "", []),
    ],
)
def test_database_of_the_relation(
    database: str | None, relation: str | None, expected: list[tuple[str, str]]
) -> None:
    data = _data("legacy")
    _model(data, "sales_daily")["node_relation"]["database"] = relation
    path = "$.semantic_models[0].node_relation.database"
    resolution = _resolve(data, "revenue", database=database)
    assert _gaps(resolution) == [(code, severity, path) for code, severity in expected]
    assert (resolution.formula is not None) is (expected == [])


def test_another_table_is_a_relation_mismatch() -> None:
    data = _data("legacy")
    path = "$.semantic_models[0].node_relation"
    assert _gaps(_resolve(data, "revenue", table="dm.sales")) == [(RELATION_MISMATCH, BLOCK, path)]
    _model(data, "sales_daily")["node_relation"]["schema_name"] = "mart"
    assert _gaps(_resolve(data, "revenue")) == [(RELATION_MISMATCH, BLOCK, path)]


def test_grain_table_a_typed_term_does_not_take_is_a_construct() -> None:
    data = _data("legacy")
    _model(data, "sales_daily")["node_relation"]["alias"] = "Sales_Daily"
    assert _gaps(_resolve(data, "revenue", table="dm.Sales_Daily")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, _metric_path(data, "revenue"))
    ]


def test_another_event_time_is_a_time_mismatch() -> None:
    data = _data("legacy")
    assert _gaps(_resolve(data, "revenue", event_time="day")) == [
        (TIME_MISMATCH, BLOCK, f"{_dimension_path(data, 'date')}.name")
    ]


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("date", []),
        ("order_date", [(TIME_MISMATCH, BLOCK)]),
        ("DATE", [(TIME_UNPROVEN, REVIEW)]),
        ("date_trunc('day', ts)", [(TIME_UNPROVEN, REVIEW)]),
        ("current_date", [(TIME_UNPROVEN, REVIEW)]),
    ],
)
def test_time_dimension_expression(expr: str, expected: list[tuple[str, str]]) -> None:
    data = _data("legacy")
    _model(data, "sales_daily")["dimensions"][0]["expr"] = expr
    path = f"{_dimension_path(data, 'date')}.expr"
    assert _gaps(_resolve(data, "revenue")) == [(code, sev, path) for code, sev in expected]


def test_time_granularity_other_than_day_or_absent_is_review() -> None:
    month = _data("legacy")
    _model(month, "sales_daily")["dimensions"][0]["type_params"]["time_granularity"] = "month"
    path = _dimension_path(month, "date")
    assert _gaps(_resolve(month, "revenue")) == [
        (TIME_UNPROVEN, REVIEW, f"{path}.type_params.time_granularity")
    ]
    absent = _data("legacy")
    _model(absent, "sales_daily")["dimensions"][0]["type_params"] = None
    assert _gaps(_resolve(absent, "revenue")) == [(TIME_UNPROVEN, REVIEW, f"{path}.type_params")]


def test_aggregation_time_that_is_not_a_time_dimension_is_review() -> None:
    data = _data("legacy")
    _model(data, "sales_daily")["dimensions"][0]["type"] = "categorical"
    assert _gaps(_resolve(data, "revenue")) == [
        (TIME_UNPROVEN, REVIEW, f"{_dimension_path(data, 'date')}.type")
    ]


def test_no_aggregation_time_is_review() -> None:
    data = _data("legacy")
    _model(data, "sales_daily")["defaults"] = None
    assert _gaps(_resolve(data, "revenue")) == [
        (TIME_UNPROVEN, REVIEW, f"{_measure_path(data, 'revenue')}.agg_time_dimension")
    ]


def _second_time_dimension(data: dict[str, Any], name: str, expr: str | None) -> None:
    _model(data, "sales_daily")["dimensions"].append(
        {
            "name": name,
            "type": "time",
            "expr": expr,
            "type_params": {"time_granularity": "day", "validity_params": None},
        }
    )


def test_measure_aggregation_time_comes_before_the_default() -> None:
    data = _data("legacy")
    _second_time_dimension(data, "ship_date", None)
    _measure(data, "revenue")["agg_time_dimension"] = "ship_date"
    assert _gaps(_resolve(data, "revenue")) == [
        (TIME_MISMATCH, BLOCK, f"{_dimension_path(data, 'ship_date')}.name")
    ]
    shipped = _resolve(
        data,
        "revenue",
        event_time="ship_date",
        keys=("ship_date", "store_id"),
        declared=DeclaredTable("postgres", {**SALES.column_types, "ship_date": "date"}),
    )
    assert shipped.formula == _aggregate("revenue", "sum")


def _second_revenue(data: dict[str, Any], measure: dict[str, Any]) -> None:
    """A simple metric `revenue_b` over `measure`, and the ratio over it as its denominator."""
    metric = copy.deepcopy(_metric(data, "revenue"))
    metric["name"] = "revenue_b"
    params = metric["type_params"]
    params["measure"]["name"] = params["input_measures"][0]["name"] = measure["name"]
    data["metrics"].append(metric)
    ratio = _metric(data, "gross_margin_pct")["type_params"]
    ratio["denominator"]["name"] = "revenue_b"
    ratio["input_measures"] = [
        *_metric(data, "gross_margin_total")["type_params"]["input_measures"],
        *params["input_measures"],
    ]


def test_ratio_over_two_semantic_models_is_review() -> None:
    data = _data("legacy")
    other = copy.deepcopy(_model(data, "sales_daily"))
    other["name"] = "sales_daily_b"
    measure = copy.deepcopy(_measure(data, "revenue"))
    measure["name"] = "revenue_b"
    other["measures"] = [measure]
    data["semantic_models"].append(other)
    _second_revenue(data, measure)
    path = f"{_metric_path(data, 'gross_margin_pct')}.type_params.denominator"
    resolution = _resolve(data, "gross_margin_pct")
    assert _gaps(resolution) == [(RATIO_UNPROVEN, REVIEW, path)]
    assert "'sales_daily_b'" in resolution.gaps[0].reason


def test_ratio_over_two_aggregation_times_is_review() -> None:
    data = _data("legacy")
    _second_time_dimension(data, "date_b", "date")
    measure = copy.deepcopy(_measure(data, "revenue"))
    measure["name"], measure["agg_time_dimension"] = "revenue_b", "date_b"
    _model(data, "sales_daily")["measures"].append(measure)
    _second_revenue(data, measure)
    # each side on its own reads the event time column
    assert _resolve(data, "revenue_b").formula == _aggregate("revenue", "sum")
    path = f"{_metric_path(data, 'gross_margin_pct')}.type_params.denominator"
    assert _gaps(_resolve(data, "gross_margin_pct")) == [(RATIO_UNPROVEN, REVIEW, path)]


def test_ratio_over_a_metric_that_is_not_simple_is_a_construct() -> None:
    data = _data("legacy")
    params = _metric(data, "gross_margin_pct")["type_params"]
    params["numerator"] = _input("revenue_cum")
    params["input_measures"] = [_measure_input("revenue"), _measure_input("revenue")]
    assert _gaps(_resolve(data, "gross_margin_pct")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_metric_path(data, 'revenue_cum')}.type")
    ]


def test_ratio_input_measures_are_those_of_its_inputs() -> None:
    data = _data("legacy")
    params = _metric(data, "gross_margin_pct")["type_params"]
    measures = params["input_measures"]
    path = f"{_metric_path(data, 'gross_margin_pct')}.type_params.input_measures"
    # one short, or the denominator's first
    for other in (measures[:1], measures[::-1]):
        params["input_measures"] = other
        assert _gaps(_resolve(data, "gross_margin_pct")) == [(CONSTRUCT_UNSUPPORTED, REVIEW, path)]


def test_ratio_over_one_metric_twice_reads_its_measure_once() -> None:
    data = _data("legacy")
    params = _metric(data, "gross_margin_pct")["type_params"]
    params["denominator"]["name"] = "gross_margin_total"
    params["input_measures"] = params["input_measures"][:1]
    resolution = _resolve(data, "gross_margin_pct")
    assert resolution.gaps == ()
    assert resolution.formula == _ratio(("gross_margin", "sum"), ("gross_margin", "sum"))
    params["input_measures"] = params["input_measures"] * 2
    path = f"{_metric_path(data, 'gross_margin_pct')}.type_params.input_measures"
    assert _gaps(_resolve(data, "gross_margin_pct")) == [(CONSTRUCT_UNSUPPORTED, REVIEW, path)]


def test_ratio_over_two_metrics_of_one_measure_reads_it_once() -> None:
    data = _data("legacy")
    again = copy.deepcopy(_metric(data, "gross_margin_total"))
    again["name"] = "gross_margin_again"
    data["metrics"].append(again)
    params = _metric(data, "gross_margin_pct")["type_params"]
    params["denominator"]["name"] = "gross_margin_again"
    params["input_measures"] = params["input_measures"][:1]
    resolution = _resolve(data, "gross_margin_pct")
    assert resolution.gaps == ()
    assert resolution.formula == _ratio(("gross_margin", "sum"), ("gross_margin", "sum"))


def test_ratio_without_a_side_is_a_construct() -> None:
    data = _data("legacy")
    _metric(data, "gross_margin_pct")["type_params"]["numerator"] = None
    path = f"{_metric_path(data, 'gross_margin_pct')}.type_params.numerator"
    assert _gaps(_resolve(data, "gross_margin_pct")) == [(CONSTRUCT_UNSUPPORTED, REVIEW, path)]


@pytest.mark.parametrize(
    ("kind", "ref", "where"),
    [
        ("legacy", "active_stores", ("measure", "stores_count")),
        ("wide_legacy", "revenue_min", ("measure", "revenue_min")),
        ("wide_legacy", "revenue_max", ("measure", "revenue_max")),
        ("wide_legacy", "revenue_avg", ("measure", "revenue_avg")),
        ("legacy", "gross_margin_pct", ("metric", "gross_margin_pct")),
    ],
)
def test_aggregate_that_does_not_add_up_under_additive_is_review(
    kind: str, ref: str, where: tuple[str, str]
) -> None:
    data = _data(kind)
    _metric(data, ref)["time_granularity"] = None
    path = (
        f"{_measure_path(data, where[1])}.agg"
        if where[0] == "measure"
        else f"{_metric_path(data, where[1])}.type"
    )
    assert _gaps(_resolve(data, ref, additivity="additive")) == [
        (ADDITIVITY_UNPROVEN, REVIEW, path)
    ]
    for additivity in ("semi_additive", "non_additive"):
        assert _resolve(data, ref, additivity=additivity).gaps == ()


@pytest.mark.parametrize(("kind", "ref"), [("legacy", "revenue"), ("wide_legacy", "sales_rows")])
def test_sum_and_count_may_be_additive(kind: str, ref: str) -> None:
    assert _resolve(_data(kind), ref, additivity="additive").gaps == ()


def test_private_metric_is_review_and_a_private_input_is_not() -> None:
    data = _data("legacy")
    _metric(data, "revenue")["type_params"]["is_private"] = True
    assert _gaps(_resolve(data, "revenue")) == [
        (PRIVATE_METRIC, REVIEW, f"{_metric_path(data, 'revenue')}.type_params.is_private")
    ]
    assert _resolve(data, "gross_margin_pct").gaps == ()
    _metric(data, "revenue")["type_params"]["is_private"] = False
    assert _resolve(data, "revenue").gaps == ()


# --- the supported subset -------------------------------------------------------------

#: A value the reader takes for each key of the tables, none of them neutral.
SET_VALUES: dict[str, Any] = {
    "filter": FILTER,
    "time_granularity": "month",
    "datatype": "decimal",
    "window": {"count": 7, "granularity": "day"},
    "grain_to_date": "month",
    "metrics": [_input("gross_margin_total")],
    "conversion_type_params": {
        "entity": "store",
        "base_metric": _input("gross_margin_total"),
        "conversion_metric": _input("active_stores"),
    },
    "cumulative_type_params": {
        "window": None,
        "grain_to_date": "month",
        "period_agg": "first",
        "metric": None,
    },
    "join_to_timespine": True,
    "fill_nulls_with": 0,
    "numerator": _input("gross_margin_total"),
    "denominator": _input("gross_margin_total"),
    "measure": _measure_input("revenue"),
    "expr": "revenue",
    "metric_aggregation_params": {
        "semantic_model": "sales_daily",
        "agg": "sum",
        "agg_params": None,
        "agg_time_dimension": "date",
        "non_additive_dimension": None,
    },
    "alias": "renamed",
    "offset_window": {"count": 1, "granularity": "day"},
    "offset_to_grain": "month",
}


def _set_metric(name: str, key: str) -> Callable[[dict[str, Any]], tuple[str, str]]:
    def change(data: dict[str, Any]) -> tuple[str, str]:
        _metric(data, name)[key] = SET_VALUES[key]
        return name, f"{_metric_path(data, name)}.{key}"

    return change


def _set_params(name: str, key: str) -> Callable[[dict[str, Any]], tuple[str, str]]:
    def change(data: dict[str, Any]) -> tuple[str, str]:
        _metric(data, name)["type_params"][key] = SET_VALUES[key]
        return name, f"{_metric_path(data, name)}.type_params.{key}"

    return change


def _set_measure_input(key: str) -> Callable[[dict[str, Any]], tuple[str, str]]:
    def change(data: dict[str, Any]) -> tuple[str, str]:
        # dbt writes the measure input twice; both say the same
        params = _metric(data, "revenue")["type_params"]
        params["measure"][key] = SET_VALUES[key]
        params["input_measures"][0][key] = SET_VALUES[key]
        return "revenue", f"{_metric_path(data, 'revenue')}.type_params.measure.{key}"

    return change


def _set_metric_input(key: str) -> Callable[[dict[str, Any]], tuple[str, str]]:
    def change(data: dict[str, Any]) -> tuple[str, str]:
        _metric(data, "gross_margin_pct")["type_params"]["numerator"][key] = SET_VALUES[key]
        path = f"{_metric_path(data, 'gross_margin_pct')}.type_params.numerator.{key}"
        return "gross_margin_pct", path

    return change


SET_KEYS = [
    *(pytest.param(_set_metric("revenue", key), id=f"simple-{key}") for key in _METRIC),
    *(pytest.param(_set_metric("gross_margin_pct", key), id=f"ratio-{key}") for key in _METRIC),
    *(pytest.param(_set_params("revenue", k), id=f"simple-params-{k}") for k in _SIMPLE_PARAMS),
    *(
        pytest.param(_set_params("gross_margin_pct", key), id=f"ratio-params-{key}")
        for key in _RATIO_PARAMS
    ),
    *(pytest.param(_set_measure_input(key), id=f"measure-input-{key}") for key in _INPUT_MEASURE),
    *(pytest.param(_set_metric_input(key), id=f"metric-input-{key}") for key in _METRIC_INPUT),
]


@pytest.mark.parametrize("change", SET_KEYS)
def test_each_key_outside_the_subset_is_a_construct_at_its_path(
    change: Callable[[dict[str, Any]], tuple[str, str]],
) -> None:
    data = _data("legacy")
    ref, path = change(data)
    assert _gaps(_resolve(data, ref)) == [(CONSTRUCT_UNSUPPORTED, REVIEW, path)]


def test_absent_keys_read_as_unset() -> None:
    data = _data("legacy")
    for name in ("revenue", "gross_margin_total", "gross_margin_pct"):
        metric = _metric(data, name)
        for key in _METRIC:
            metric.pop(key)
        params = metric["type_params"]
        for key in {*_SIMPLE_PARAMS, *_RATIO_PARAMS} - {"measure", "numerator", "denominator"}:
            params.pop(key)
        for item in (params.get("measure"), *params["input_measures"]):
            if item is not None:
                for key in _INPUT_MEASURE:
                    item.pop(key)
        for side in ("numerator", "denominator"):
            if params.get(side) is not None:
                for key in _METRIC_INPUT:
                    params[side].pop(key)
            else:
                params.pop(side)
        if params.get("measure") is None:
            params.pop("measure")
    assert _resolve(data, "revenue").formula == _aggregate("revenue", "sum")
    ratio = _resolve(data, "gross_margin_pct")
    assert ratio.formula == _ratio(("gross_margin", "sum"), ("revenue", "sum"))


def _schema_fields(model: str) -> set[str]:
    schema = json.loads((FIXTURES / "dsi_0.213.0_schema.json").read_bytes())
    return set(schema["models"][model]) - {"__extra__"}


#: Keys no number depends on.
NEUTRAL = {"description", "label", "metadata", "config"}


def test_every_key_of_what_a_resolution_reads_is_classified() -> None:
    assert _schema_fields("PydanticMetric") == {
        *_METRIC,
        "name",
        "type",
        "type_params",
        *NEUTRAL,
    }
    assert _schema_fields("PydanticMetricTypeParams") == {
        *_SIMPLE_PARAMS,
        *_RATIO_PARAMS,
        "input_measures",
        "is_private",
    }
    assert _schema_fields("PydanticMetricInputMeasure") == {*_INPUT_MEASURE, "name"}
    assert _schema_fields("PydanticMetricInput") == {*_METRIC_INPUT, "name"}
    measure_read = {"name", "agg", "agg_params", "agg_time_dimension", "expr"}
    assert _schema_fields("PydanticMeasure") == {
        *measure_read,
        "non_additive_dimension",
        "create_metric",
        *NEUTRAL,
    }
    assert _schema_fields("PydanticMetricAggregationParams") == {
        "semantic_model",
        "agg",
        "agg_params",
        "agg_time_dimension",
        "non_additive_dimension",
    }
    assert _schema_fields("PydanticMeasureAggregationParameters") == set(_DEFAULT_AGG_PARAMS)


def test_neutral_keys_leave_the_formula_alone() -> None:
    data = _data("legacy")
    metadata = {
        "repo_file_path": "models/schema.yml",
        "file_slice": {
            "filename": "schema.yml",
            "content": "metrics:",
            "start_line_number": 1,
            "end_line_number": 2,
        },
    }
    for item in (_metric(data, "revenue"), _measure(data, "revenue")):
        item.update(
            description="changed", label="Changed", metadata=metadata, config={"meta": {"a": 1}}
        )
    _measure(data, "revenue")["create_metric"] = True
    assert _resolve(data, "revenue").formula == _aggregate("revenue", "sum")


def test_aggregation_parameters_other_than_the_defaults_are_a_construct() -> None:
    data = _data("legacy")
    _measure(data, "revenue")["agg_params"] = {"use_discrete_percentile": False}
    assert _resolve(data, "revenue").gaps == ()
    _measure(data, "revenue")["agg_params"] = {"percentile": 0.5}
    assert _gaps(_resolve(data, "revenue")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_measure_path(data, 'revenue')}.agg_params")
    ]


@pytest.mark.parametrize("expr", ["Revenue", "true", "null", "revenue - gross_margin", "1"])
def test_measure_expression_that_is_not_one_column_is_a_construct(expr: str) -> None:
    data = _data("legacy")
    _measure(data, "revenue")["expr"] = expr
    assert _gaps(_resolve(data, "revenue")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_measure_path(data, 'revenue')}.expr")
    ]


def test_measure_name_read_as_a_column_must_be_one() -> None:
    data = _data("wide_legacy")
    _measure(data, "gross_margin")["name"] = "null"
    for metric in data["metrics"]:
        params = metric["type_params"]
        for measure in (params.get("measure"), *params["input_measures"]):
            if measure is not None and measure["name"] == "gross_margin":
                measure["name"] = "null"
    assert _gaps(_resolve(data, "gross_margin_total")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_measure_path(data, 'null')}.name")
    ]


def test_legacy_metric_expression_must_say_what_the_measure_says() -> None:
    data = _data("wide_legacy")
    _metric(data, "revenue_created")["type_params"]["expr"] = "gross_margin"
    path = f"{_metric_path(data, 'revenue_created')}.type_params.expr"
    assert _gaps(_resolve(data, "revenue_created")) == [(CONSTRUCT_UNSUPPORTED, REVIEW, path)]
    # without a measure expr the metric expr must be the measure's name
    _metric(data, "gross_margin_total")["type_params"]["expr"] = "gross_margin"
    resolution = _resolve(data, "gross_margin_total")
    assert resolution.formula == _aggregate("gross_margin", "sum")
    assert resolution.parity_rows == _rows(
        "legacy.sum.decimal", "legacy.measure_without_expr", "legacy.metric_expr"
    )


def test_legacy_input_measures_are_the_metric_measure() -> None:
    data = _data("legacy")
    _metric(data, "revenue")["type_params"]["input_measures"].append(_measure_input("gross_margin"))
    path = f"{_metric_path(data, 'revenue')}.type_params.input_measures"
    assert _gaps(_resolve(data, "revenue")) == [(CONSTRUCT_UNSUPPORTED, REVIEW, path)]


@pytest.mark.parametrize(
    ("ref", "where"),
    [
        ("revenue_p90", "agg"),
        ("revenue_median", "agg"),
        ("revenue_last", "non_additive_dimension"),
    ],
)
def test_constructs_dbt_wrote(ref: str, where: str) -> None:
    data = _data("wide_legacy")
    measure = _metric(data, ref)["type_params"]["measure"]["name"]
    assert (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_measure_path(data, measure)}.{where}") in _gaps(
        _resolve(data, ref)
    )


def test_metric_time_granularity_dbt_wrote_is_a_construct() -> None:
    data = _data("wide_legacy")
    assert _metric(data, "revenue_max")["time_granularity"] == "month"
    assert _gaps(_resolve(data, "revenue_max")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_metric_path(data, 'revenue_max')}.time_granularity")
    ]
    _metric(data, "revenue_max")["time_granularity"] = None
    assert _resolve(data, "revenue_max").formula == _aggregate("revenue", "max")


@pytest.mark.parametrize(
    ("kind", "ref"),
    [
        ("legacy", "revenue_cum"),
        ("legacy", "margin_derived"),
        ("wide_legacy", "visit_to_purchase"),
        ("wide_v2", "revenue_growth"),
    ],
)
def test_metric_neither_simple_nor_ratio_is_answered_by_its_type(kind: str, ref: str) -> None:
    data = _data(kind)
    assert _gaps(_resolve(data, ref)) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, f"{_metric_path(data, ref)}.type")
    ]


# --- the context of a parity row ------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("numeric(38,10)", "decimal"),
        ("numeric (18, 2)", "decimal"),
        ("numeric", "decimal"),
        ("DECIMAL", "decimal"),
        ("smallint", "integer"),
        ("integer", "integer"),
        ("bigint", "integer"),
        ("INT", "integer"),
        ("int2", "integer"),
        ("int4", "integer"),
        ("int8", "integer"),
        ("real", "float"),
        ("Double Precision", "float"),
        ("float", "float"),
        ("float(24)", "float"),
        ("float4", "float"),
        ("float8", "float"),
        ("date", "date"),
        ("text", UNCLASSIFIED),
        ("timestamp", UNCLASSIFIED),
        ("timestamp(3) with time zone", UNCLASSIFIED),
        ("integer[]", UNCLASSIFIED),
        ("numeric(18,2)[]", UNCLASSIFIED),
        ("numeric(18,2) ARRAY", UNCLASSIFIED),
        ("numeric ARRAY", UNCLASSIFIED),
        ("float(24)[]", UNCLASSIFIED),
        ("numeric(18,2,3)", UNCLASSIFIED),
        ("", UNDECLARED),
        (None, UNDECLARED),
    ],
)
def test_parity_row_names_the_class_of_the_column_type(kind: str | None, expected: str) -> None:
    types = {name: value for name, value in SALES.column_types.items() if name != "revenue"}
    if kind is not None:
        types["revenue"] = kind
    declared = DeclaredTable("postgres", types)
    resolution = _resolve(_data("legacy"), "revenue", declared=declared)
    assert resolution.parity_rows == _rows(f"legacy.sum.{expected}")
    # what the run has proven resolves; any other type stays external
    run = _resolve(_data("legacy"), "revenue", declared=declared, proven=PROVEN_PARITY)
    assert (run.formula is not None) is (expected in ("decimal", "integer", "float"))


@pytest.mark.parametrize(
    ("engine", "event_time_type", "keys", "context"),
    [
        ("postgres", "date", ("date", "store_id"), "postgres.date"),
        ("PostgreSQL", "date", ("store_id", "date"), "postgres.date"),
        ("postgresql", "date", ("date", "store_id"), "postgres.date"),
        ("postgres", "timestamp", ("date", "store_id"), f"postgres.{UNCLASSIFIED}"),
        ("postgres", None, ("date", "store_id"), f"postgres.{UNDECLARED}"),
        ("postgres", "date", ("store_id",), f"postgres.{OUTSIDE_GRAIN}"),
        ("clickhouse", "Date", ("date", "store_id"), f"clickhouse.{UNCLASSIFIED}"),
        ("greenplum", "date", ("date", "store_id"), f"greenplum.{UNCLASSIFIED}"),
        ("", "date", ("date", "store_id"), f"{UNDECLARED}.{UNCLASSIFIED}"),
    ],
)
def test_parity_row_holds_in_the_engine_and_event_time_of_the_table(
    engine: str, event_time_type: str | None, keys: tuple[str, ...], context: str
) -> None:
    data = _data("legacy")
    types = {name: value for name, value in SALES.column_types.items() if name != "date"}
    if event_time_type is not None:
        types["date"] = event_time_type
    declared = DeclaredTable(engine, types)
    kind = "decimal" if context.startswith("postgres.") else UNCLASSIFIED
    row = f"{context}:legacy.sum.{kind}"
    resolution = _resolve(data, "revenue", declared=declared, keys=keys)
    assert resolution.parity_rows == {row}
    # what the recorded run proved holds in its own context only
    default = _resolve(data, "revenue", declared=declared, keys=keys, proven=PROVEN_PARITY)
    if context == "postgres.date":
        assert (default.formula, default.gaps) == (_aggregate("revenue", "sum"), ())
    else:
        assert default.formula is None
        assert _gaps(default) == [(PARITY_UNPROVEN, REVIEW, _metric_path(data, "revenue"))]
        assert default.gaps[0].reason.endswith(f"to agree on {row}")


def test_float_ratio_term_is_proven_under_a_count_only() -> None:
    # the reference SQL of a ratio over a float sum is refused (sql.ratio_inexact_term), and
    # the one over a float count is not: only the run of that form proves it
    data = _data("legacy")
    declared = DeclaredTable("postgres", {**SALES.column_types, "gross_margin": "double precision"})
    over_sum = _resolve(data, "gross_margin_pct", declared=declared, proven=PROVEN_PARITY)
    assert _gaps(over_sum) == [(PARITY_UNPROVEN, REVIEW, _metric_path(data, "gross_margin_pct"))]
    assert over_sum.gaps[0].reason.endswith(
        f"to agree on {CONTEXT}:ratio.legacy.sum.float/legacy.sum.decimal"
    )
    _measure(data, "gross_margin")["agg"] = "count"
    over_count = _resolve(data, "gross_margin_pct", declared=declared, proven=PROVEN_PARITY)
    assert over_count.gaps == ()
    assert over_count.formula == _ratio(("gross_margin", "count"), ("revenue", "sum"))
    assert over_count.parity_rows == _rows(
        "legacy.count.float", "legacy.sum.decimal", "ratio.legacy.count.float/legacy.sum.decimal"
    )


# --- the recorded parity run ----------------------------------------------------------


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "metricflow_parity", REPO / "scripts" / "metricflow_parity.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result() -> dict[str, Any]:
    result: dict[str, Any] = json.loads((PARITY / "result.json").read_bytes())
    return result


def test_proven_parity_is_the_recorded_run() -> None:
    result = _result()
    raw = (PARITY / "semantic_manifest.json").read_bytes()
    assert result["artifact_digest"] == digest_bytes(raw)
    assert result["versions"]["metricflow"] == "0.213.0"
    assert result["versions"]["server"].startswith("PostgreSQL 16.15 ")
    assert frozenset(result["proven"]) == PROVEN_PARITY


def test_recorded_run_is_its_own_proof() -> None:
    script, result = _script(), _result()
    read = read_semantic_manifest((PARITY / "semantic_manifest.json").read_bytes())
    assert read.manifest is not None
    assert sorted(script.subjects()) == read.manifest.metric_names() == sorted(result["metrics"])
    assert script._proven(result["metrics"]) == set(result["proven"])
    outcomes = {case["outcome"] for case in result["metrics"].values()}
    assert outcomes == {"pass", "no_reference_sql"}
    for name, case in result["metrics"].items():
        assert case["subject"] == script.subjects()[name]
        if case["outcome"] == "no_reference_sql":
            # only a ratio with an average term goes without reference SQL
            terms = (case["formula"]["numerator"], case["formula"]["denominator"])
            assert "avg" in {term["agg"] for term in terms}, name
            continue
        values = case["values"]
        assert values and case["mismatches"] == []
        ours = [{"k1": day, "k2": int(store), "v": ref} for day, store, ref, _ in values]
        theirs = [{"k1": day, "k2": int(store), "v": mf} for day, store, _, mf in values]
        ratio = case["formula"]["kind"] == "typed_ratio"
        assert script._compare(ours, theirs, ratio=ratio) == [], name
    assert result["controls"] and all(control["mismatches"] > 0 for control in result["controls"])


def test_recorded_formulas_are_what_resolution_gives_now() -> None:
    script, result = _script(), _result()
    read = read_semantic_manifest((PARITY / "semantic_manifest.json").read_bytes())
    assert read.manifest is not None
    every = frozenset(script.subjects().values())
    for name, case in result["metrics"].items():
        contract = script.metric_contract(read.manifest, name)
        assert contract.additivity.value == (
            "additive"
            if case["formula"].get("aggregate", {}).get("agg") in ("sum", "count")
            else "non_additive"
        ), name
        resolution = resolve_formula(
            contract, read, database=script.DATABASE, declared=script.DECLARED, proven=every
        )
        assert resolution.formula is not None, name
        assert resolution.formula.model_dump(mode="json", exclude_none=True) == case["formula"]
        assert sorted(resolution.parity_rows) == case["rows"]
        assert case["subject"] in case["rows"]


@pytest.mark.parametrize(
    ("reference", "metricflow", "ratio", "same"),
    [
        ("1.50", "1.5", False, True),
        ("1.50", "1.51", False, False),
        (None, None, False, True),
        (None, "0", False, False),
        ("0", None, True, False),
        ("0.333333", "0.3333333333333333", True, True),
        ("0.333333", "0.3333341", True, False),
        ("0.333333", "0.3333329", True, False),
        ("-0.333333", "-0.3333333333333333", True, True),
        ("-0.333333", "-0.3333343", True, False),
        ("-0.333333", "-0.3333329", True, False),
        ("0.000000", "3.00000000000003E-12", True, True),
        ("0.000000", "-3E-7", True, True),
        ("0.000000", "-0.0000011", True, False),
        ("0.000000", "0.0000011", True, False),
        # the error of a double is |mf| * 1e-15: within it a value agrees, beyond it not
        ("1.000000", "0.9999999999999995", True, True),
        ("1.000000", "0.999999999999995", True, False),
        ("1.000000", "1.0000010000000005", True, True),
        ("1.000000", "1.000001000000005", True, False),
        # a double keeps about sixteen digits: the rest is its error, not another quotient
        ("142857142857.142857", "142857142857.14285", True, True),
        ("142857142857.142857", "142857142857.14", True, False),
    ],
)
def test_parity_rule(
    reference: str | None, metricflow: str | None, ratio: bool, same: bool
) -> None:
    def as_number(text: str | None) -> Decimal | None:
        return None if text is None else Decimal(text)

    script = _script()
    assert script._same(as_number(reference), as_number(metricflow), ratio=ratio) is same


def test_proven_rows_need_their_metrics_and_the_rows_those_rely_on() -> None:
    script = _script()

    def case(subject: str, outcome: str, *rows: str) -> dict[str, Any]:
        return {"subject": subject, "outcome": outcome, "rows": [subject, *rows]}

    cases = {
        "a": case("legacy.sum", "pass"),
        "b": case("legacy.sum", "pass"),
        "c": case("legacy.count", "pass"),
        "d": case("legacy.count", "fail"),
        "e": case("ratio.legacy.sum/legacy.sum", "pass", "legacy.sum"),
        "f": case("ratio.legacy.sum/legacy.count", "pass", "legacy.sum", "legacy.count"),
        "g": case("legacy.min", "no_reference_sql"),
        # every metric passed, and the row relies on one that does not hold: it falls, and so
        # does the row relying on it
        "h": case("legacy.measure_without_expr", "pass", "legacy.count"),
        "i": case("legacy.metric_expr", "pass", "legacy.measure_without_expr"),
    }
    assert script._proven(cases) == {"legacy.sum", "ratio.legacy.sum/legacy.sum"}


def test_keys_on_one_side_are_mismatches() -> None:
    script = _script()
    ours = [{"k1": "2026-01-01", "k2": 1, "v": "1"}, {"k1": "2026-01-02", "k2": 1, "v": "2"}]
    theirs = [{"k1": "2026-01-01", "k2": 1, "v": "1"}, {"k1": "2026-01-03", "k2": 1, "v": "2"}]
    assert len(script._compare(ours, theirs, ratio=False)) == 2
    assert script._compare(ours, ours, ratio=False) == []


def test_recorded_run_is_the_run_of_the_recipe() -> None:
    script, result = _script(), _result()
    assert result["recipe"] == script.recipe()
    recorded = [(control["reference"], control["metricflow"]) for control in result["controls"]]
    assert recorded == list(script.CONTROLS)


def test_matrix_data_holds_the_cases_it_names() -> None:
    script = _script()
    groups: dict[tuple[str, int], list[tuple[Any, ...]]] = {}
    for day, store, *values in script.ROWS:
        groups.setdefault((day, store), []).append(tuple(values))
    columns = range(len(script.COLUMNS))

    def present(found: list[tuple[Any, ...]], column: int) -> list[Decimal]:
        return [Decimal(str(row[column])) for row in found if row[column] is not None]

    qty = list(script.COLUMNS).index("qty")
    # a nullable column under count; a group where a column is all NULL (a NULL denominator)
    assert all(any(row[column] is None for row in script.ROWS) for column in range(2, 5))
    assert any(not present(found, qty) for found in groups.values())
    assert all(any(not present(found, column) for found in groups.values()) for column in columns)
    # a zero denominator: a group whose values sum to zero
    assert any(present(found, qty) and sum(present(found, qty)) == 0 for found in groups.values())
    # negative values in every column, a large numerator
    for column in columns:
        assert any(value < 0 for found in groups.values() for value in present(found, column))
    assert any(value >= 10**11 for found in groups.values() for value in present(found, 0))
    # repeated values under count_distinct
    for column in columns:
        assert any(
            len(present(found, column)) > len(set(present(found, column)))
            for found in groups.values()
        )
    # a date without rows between two with rows
    days = sorted({date.fromisoformat(day) for day, _ in groups})
    assert any((later - earlier).days > 1 for earlier, later in pairwise(days))


def test_both_sides_group_by_the_grain_keys() -> None:
    script, result = _script(), _result()
    read = read_semantic_manifest((PARITY / "semantic_manifest.json").read_bytes())
    assert read.manifest is not None
    model = read.manifest.semantic_model(script.TABLE)
    assert script.KEYS == (script.EVENT_TIME, "store_id")
    assert model["defaults"]["agg_time_dimension"] == script.EVENT_TIME
    assert "store_id" in [dimension["name"] for dimension in model["dimensions"]]
    assert ("metric_time__day", f"{model['primary_entity']}__store_id") == script.GROUP_BY
    # every compared metric has one value per (day, store) group of the data on either side
    keys = {(day, str(store)) for day, store, *_ in script.ROWS}
    for name, case in result["metrics"].items():
        if case["outcome"] == "pass":
            assert {(day, store) for day, store, _, _ in case["values"]} == keys, name


# --- MetricFlow's side ----------------------------------------------------------------


def _bridge(
    monkeypatch: pytest.MonkeyPatch, explain: Callable[[Any], Any]
) -> tuple[ModuleType, list[Any]]:
    """`metricflow_parity_sql.py` over stand-ins of the MetricFlow modules it imports."""
    seen: list[Any] = []

    class Engine:
        def __init__(self, lookup: Any, client: Any) -> None:
            seen.extend((lookup, client))

        def explain(self, request: Any) -> Any:
            seen.append(request)
            return explain(request)

    class Request:
        @staticmethod
        def create(**fields: Any) -> dict[str, Any]:
            return fields

    class Renderer:
        pass

    stand_ins: dict[str, dict[str, Any]] = {
        "metricflow.engine.metricflow_engine": {
            "MetricFlowEngine": Engine,
            "MetricFlowQueryRequest": Request,
        },
        "metricflow.protocols.sql_client": {"SqlEngine": SimpleNamespace(POSTGRES="postgres")},
        "metricflow.sql.render.postgres": {"PostgresSQLSqlPlanRenderer": Renderer},
        "metricflow_semantics.model.dbt_manifest_parser": {
            "parse_manifest_from_dbt_generated_manifest": lambda raw: ("parsed", raw)
        },
        "metricflow_semantics.model.semantic_manifest_lookup": {
            "SemanticManifestLookup": lambda manifest: ("lookup", manifest)
        },
    }
    for name, attributes in stand_ins.items():
        parts = name.split(".")
        for end in range(1, len(parts)):
            parent = ".".join(parts[:end])
            monkeypatch.setitem(sys.modules, parent, ModuleType(parent))
        module = ModuleType(name)
        for attribute, value in attributes.items():
            setattr(module, attribute, value)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "metricflow_parity_sql", REPO / "scripts" / "metricflow_parity_sql.py"
    )
    assert spec is not None and spec.loader is not None
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    return bridge, seen


def _statement(sql: str, params: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        sql_statement=SimpleNamespace(
            sql=sql, bind_parameter_set=SimpleNamespace(param_dict=params)
        )
    )


def _ask(monkeypatch: pytest.MonkeyPatch, bridge: ModuleType) -> int:
    request = {
        "artifact": str(PARITY / "semantic_manifest.json"),
        "metrics": ["b", "a"],
        "group_by": ["day", "store"],
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    answer: int = bridge.main()
    return answer


def test_metricflow_side_plans_each_metric_at_the_grain_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explain(request: dict[str, Any]) -> SimpleNamespace:
        return _statement(f"SELECT {request['metric_names'][0]}", {})

    bridge, seen = _bridge(monkeypatch, explain)
    client = bridge._PlanOnly()
    # a PostgreSQL client: MetricFlow renders the plan in PostgreSQL's dialect
    assert client.sql_engine_type == "postgres"
    assert isinstance(client.sql_plan_renderer, bridge.PostgresSQLSqlPlanRenderer)
    assert client.render_bind_parameter_key("x") == "%(x)s"
    for call in (client.query, client.execute, client.dry_run):
        with pytest.raises(RuntimeError, match="only plans"):
            call("SELECT 1")
    assert _ask(monkeypatch, bridge) == 0
    assert json.loads(capsys.readouterr().out) == {"b": "SELECT b", "a": "SELECT a"}
    # the artifact as MetricFlow's own parser reads it
    artifact = (PARITY / "semantic_manifest.json").read_text(encoding="utf-8")
    assert seen[0] == ("lookup", ("parsed", artifact))
    assert isinstance(seen[1], bridge._PlanOnly)
    assert seen[2:] == [
        {"metric_names": ["b"], "group_by_names": ["day", "store"]},
        {"metric_names": ["a"], "group_by_names": ["day", "store"]},
    ]


def test_metricflow_side_refuses_a_query_with_bind_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _ = _bridge(monkeypatch, lambda request: _statement("SELECT %(x)s", {"x": 1}))
    with pytest.raises(RuntimeError, match="bind parameters"):
        _ask(monkeypatch, bridge)


# --- the run ------------------------------------------------------------------------------

_KEYED = re.compile(
    r"SELECT json_agg\(t ORDER BY t\.k1, t\.k2\) FROM \("
    r'SELECT CAST\(q\."(?P<k1>[^"]+)" AS date\) AS k1, q\."(?P<k2>[^"]+)" AS k2, '
    r'q\."(?P<v>[^"]+)" AS v FROM \((?P<body>.*)\n\) AS q\) AS t',
    re.DOTALL,
)


def _reference_bodies(script: ModuleType, folder: Path) -> dict[str, str]:
    """The reference SQL of each recorded formula, compiled here from the project's model.yaml
    and the script's contract carrying that formula, as `_keyed` wraps it."""
    read = read_semantic_manifest((PARITY / "semantic_manifest.json").read_bytes())
    assert read.manifest is not None
    (folder / "contracts").mkdir(parents=True)
    (folder / "model.yaml").write_text(script.project_files()["model.yaml"], encoding="utf-8")
    refs = {}
    for name, case in _result()["metrics"].items():
        contract = script.metric_contract(read.manifest, name).model_copy(
            update={"formula": MetricFormula.model_validate(case["formula"])}
        )
        (folder / "contracts" / f"{name}.yaml").write_text(
            yaml.safe_dump(contract.model_dump(mode="json", exclude_none=True)), encoding="utf-8"
        )
        refs[f"{contract.metadata.id}@{contract.metadata.version}"] = name
    snapshot = NativeModelSource(folder / "model.yaml", folder / "contracts").snapshot()
    compiled = compile_metric_queries(snapshot, list(refs))
    return {refs[query.metric_ref]: query.sql.strip().rstrip(";") for query in compiled.queries}


_COLUMNS_SQL = (
    "SELECT json_agg(json_build_object('c', attname, 't', format_type(atttypid, atttypmod)) "
    "ORDER BY attnum) FROM pg_attribute WHERE attrelid = 'dm.sales'::regclass "
    "AND attnum > 0 AND NOT attisdropped"
)


def _replay(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    *,
    rows: Any = None,
    types: dict[str, str] | None = None,
    wrong: str = "",
) -> ModuleType:
    """The script with its project written under `project` and its subprocesses answered from
    the recorded run: the bridge plans `MF <metric>` for each metric asked; psql gives the
    table's columns and types (`types`, the declared ones by default) and rows (`rows`, the
    recipe's by default), and, for a query keyed as one side groups, that side's recorded
    values of the metric, MetricFlow's first value of `wrong` another number. A reference query
    must be the SQL compiled for the metric's recorded formula."""
    script, result = _script(), _result()
    reference: dict[str, str] = {}
    script.write_project(project)
    (project / "target").mkdir()
    artifact = project / "target" / "semantic_manifest.json"
    artifact.write_bytes((PARITY / "semantic_manifest.json").read_bytes())
    recorded = {name: case.get("values", []) for name, case in result["metrics"].items()}

    def number(value: Any) -> str:
        return "null" if value is None else str(value)

    def psql(sql: str) -> str:
        if "version()" in sql:
            return json.dumps([{"v": result["versions"]["server"]}])
        if sql == _COLUMNS_SQL:
            declared = script.DECLARED.column_types if types is None else types
            return json.dumps([{"c": column, "t": kind} for column, kind in declared.items()])
        if sql == f"SELECT json_agg(t) FROM {script.SCHEMA}.{script.TABLE} AS t":
            table = [
                f'{{"date": "{day}", "store_id": {store}, "amount": {number(amount)}, '
                f'"qty": {number(qty)}, "price": {number(price)}}}'
                for day, store, amount, qty, price in (script.ROWS if rows is None else rows)
            ]
            return "[" + ",".join(table) + "]"
        found = _KEYED.fullmatch(sql)
        assert found is not None, sql
        name, body = found["v"], found["body"]
        metricflow = body.startswith("MF ")
        assert (found["k1"], found["k2"]) == (script.GROUP_BY if metricflow else script.KEYS)
        if metricflow:
            assert body == f"MF {name}"
        else:
            if not reference:
                reference.update(_reference_bodies(script, project / ".reference"))
            assert body == reference[name], name
        column = 3 if metricflow else 2
        values = [
            f'{{"k1": "{value[0]}", "k2": {value[1]}, "v": {number(value[column])}}}'
            for value in recorded[name]
        ]
        if metricflow and name == wrong:
            values[0] = (
                f'{{"k1": "{recorded[name][0][0]}", "k2": {recorded[name][0][1]}, "v": 123456}}'
            )
        return "[" + ",".join(values) + "]"

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        if argv[0] == "psql":
            assert argv[-4:-2] == ["-d", "conninfo"]
            return SimpleNamespace(stdout=psql(argv[-1]))
        assert argv[0] == "mf-python"
        if argv[1] == "-c":
            return SimpleNamespace(stdout=result["versions"]["metricflow"] + "\n")
        assert argv[1:] == [str(REPO / "scripts" / "metricflow_parity_sql.py")]
        request = json.loads(kwargs["input"])
        assert request["artifact"] == str(artifact)
        assert request["group_by"] == list(script.GROUP_BY)
        planned = {name: f"MF {name}" for name in request["metrics"]}
        return SimpleNamespace(stdout=json.dumps(planned))

    monkeypatch.setattr(script, "subprocess", SimpleNamespace(run=run))
    return script


def _run(script: ModuleType, project: Path) -> int:
    code: int = script.run(project, "mf-python", "psql", "conninfo", project / "result.json")
    return code


def test_run_compares_the_reference_with_metricflow_as_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = _replay(monkeypatch, tmp_path)
    assert _run(script, tmp_path) == 0
    assert json.loads((tmp_path / "result.json").read_bytes()) == _result()


def test_run_records_a_metric_that_disagrees_and_what_falls_with_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = _replay(monkeypatch, tmp_path, wrong="qty_sum")
    assert _run(script, tmp_path) == 0
    result = json.loads((tmp_path / "result.json").read_bytes())
    failed = {name for name, case in result["metrics"].items() if case["outcome"] == "fail"}
    assert failed == {"qty_sum"}
    assert len(result["metrics"]["qty_sum"]["mismatches"]) == 1
    # its row, the rows relying on it, and the rows relying on those are no longer proven
    fallen = {
        f"{CONTEXT}:legacy.{form}"
        for form in ("sum.integer", "measure_without_expr", "metric_expr")
    }
    assert not fallen & set(result["proven"])
    assert f"{CONTEXT}:legacy.sum.decimal" in result["proven"]


def test_run_fails_when_a_control_agrees(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = _replay(monkeypatch, tmp_path)
    monkeypatch.setattr(script, "CONTROLS", (("amount_sum", "amount_sum"),))
    assert _run(script, tmp_path) == 1


def test_run_refuses_inputs_other_than_the_recipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = _script().ROWS
    # a row short, a row repeated, a value changed
    changed = ((*rows[0][:2], "10.01", *rows[0][3:]), *rows[1:])
    for index, other in enumerate((rows[:-1], (*rows, rows[0]), changed)):
        script = _replay(monkeypatch, tmp_path / str(index), rows=other)
        with pytest.raises(SystemExit, match="other rows"):
            _run(script, tmp_path / str(index))
    # the recipe's values in a column of another type, a column more
    declared = dict(_script().DECLARED.column_types)
    for index, types in enumerate(({**declared, "qty": "numeric"}, {**declared, "note": "text"})):
        script = _replay(monkeypatch, tmp_path / f"types{index}", types=types)
        with pytest.raises(SystemExit, match="other columns or types"):
            _run(script, tmp_path / f"types{index}")
    script = _replay(monkeypatch, tmp_path / "files")
    with (tmp_path / "files" / "model.yaml").open("a", encoding="utf-8") as model:
        model.write("# changed\n")
    with pytest.raises(SystemExit, match=r"model\.yaml is not the recipe's"):
        _run(script, tmp_path / "files")
