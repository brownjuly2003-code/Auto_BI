"""A contract's formula resolved from a dbt semantic manifest (ADR 0008, slice S2).

Fixtures are the artifacts of `test_metricflow_manifest.py` (`dbt parse`, dbt-core 1.12.6,
metricflow 0.213.0), changed in memory and pinned again where a test needs a construct they do
not carry, and `parity/`: the artifact and the result of the parity matrix
(`scripts/metricflow_parity.py`) on PostgreSQL 16.15 and ClickHouse 24.8.14.39.
"""

from __future__ import annotations

import copy
import functools
import importlib.util
import io
import json
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise, permutations
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

from auto_bi.evidence.canonical import digest_bytes, sha256_digest
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
    CLICKHOUSE_CLASSES,
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
from auto_bi.verification.sql_contract import (
    _CLICKHOUSE_DIALECT,
    _Exactness,
    _exactness,
    compile_metric_queries,
)

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


def _relate(data: dict[str, Any], **fields: str | None) -> None:
    """Set fields of the sales relation, and its `relation_name` as dbt-postgres writes it."""
    relation = _model(data, "sales_daily")["node_relation"]
    relation.update(fields)
    parts = [relation["database"]] if relation["database"] else []
    parts += [relation["schema_name"], relation["alias"]]
    relation["relation_name"] = ".".join('"' + part.replace('"', '""') + '"' for part in parts)


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
    _relate(data, database=relation)
    path = "$.semantic_models[0].node_relation.database"
    resolution = _resolve(data, "revenue", database=database)
    assert _gaps(resolution) == [(code, severity, path) for code, severity in expected]
    assert (resolution.formula is not None) is (expected == [])


#: Stands for a relation without `relation_name`, which metricflow builds from the fields.
ABSENT = object()


@pytest.mark.parametrize(
    ("database", "spelled", "expected"),
    [
        # as dbt writes it, quoted or not, part by part, and as metricflow builds it
        ("x", '"x"."dm"."sales_daily"', []),
        ("x", "x.dm.sales_daily", []),
        ("x", '"x".dm."sales_daily"', []),
        ("x", ABSENT, []),
        ("x", "", []),
        (None, '"dm"."sales_daily"', []),
        (None, "dm.sales_daily", []),
        (None, ABSENT, []),
        ("", '"dm"."sales_daily"', []),
        # a name other than its field, quoted or one lower-case identifier
        ("x", '"x"."dm"."sales_other"', [(RELATION_MISMATCH, BLOCK)]),
        ("x", '"x"."mart"."sales_daily"', [(RELATION_MISMATCH, BLOCK)]),
        ("x", '"y"."dm"."sales_daily"', [(RELATION_MISMATCH, BLOCK)]),
        ("x", "x.dm.sales_other", [(RELATION_MISMATCH, BLOCK)]),
        ("x", "y.dm.sales_daily", [(RELATION_MISMATCH, BLOCK)]),
        ("x", '"x"."dm"."Sales_Daily"', [(RELATION_MISMATCH, BLOCK)]),
        (None, '"mart"."sales_daily"', [(RELATION_MISMATCH, BLOCK)]),
        (None, '"dm"."sales_other"', [(RELATION_MISMATCH, BLOCK)]),
        ("x", 'x.Dm."sales_other"', [(RELATION_MISMATCH, BLOCK)]),
        ("x", 'Y."dm"."sales_other"', [(RELATION_MISMATCH, BLOCK)]),
        # parts MetricFlow does not read as a relation
        ("x", '"x"."dm"."sales"."daily"', [(RELATION_MISMATCH, BLOCK)]),
        ("x", '"sales_daily"', [(RELATION_MISMATCH, BLOCK)]),
        (None, "sales_daily", [(RELATION_MISMATCH, BLOCK)]),
        # a part engines read apart, or a database on one side only
        ("x", "x.dm.Sales_Daily", [(RELATION_UNPROVEN, REVIEW)]),
        ("x", '"x"."dm".Sales_Daily', [(RELATION_UNPROVEN, REVIEW)]),
        ("x", 'X."dm"."sales_daily"', [(RELATION_UNPROVEN, REVIEW)]),
        ("x", 'x.dm."sales_daily', [(RELATION_UNPROVEN, REVIEW)]),
        ("x", 'x.dm.sales_daily"', [(RELATION_UNPROVEN, REVIEW)]),
        ("x", '"x"."dm"."sales"daily"', [(RELATION_UNPROVEN, REVIEW)]),
        ("x", "`x`.`dm`.`sales_daily`", [(RELATION_UNPROVEN, REVIEW)]),
        (None, "`dm`.`sales_daily`", [(RELATION_UNPROVEN, REVIEW)]),
        ("x", '"dm"."sales_daily"', [(RELATION_UNPROVEN, REVIEW)]),
        (None, '"x"."dm"."sales_daily"', [(RELATION_UNPROVEN, REVIEW)]),
        ("", '"x"."dm"."sales_daily"', [(RELATION_UNPROVEN, REVIEW)]),
        ("", ABSENT, [(RELATION_UNPROVEN, REVIEW)]),
    ],
)
def test_relation_name_is_the_relation_metricflow_reads(
    database: str | None, spelled: object, expected: list[tuple[str, str]]
) -> None:
    # metricflow 0.213.0 reads FROM `relation_name`, split at each dot
    # (`SqlTable.from_string`); a relation_name other than its fields types the formula of
    # another table
    data = _data("legacy")
    relation = _model(data, "sales_daily")["node_relation"]
    relation["database"] = database
    if spelled is ABSENT:
        del relation["relation_name"]
    else:
        relation["relation_name"] = spelled
    path = "$.semantic_models[0].node_relation.relation_name"
    resolution = _resolve(data, "revenue", database=database)
    assert _gaps(resolution) == [(code, severity, path) for code, severity in expected]
    assert (resolution.formula is not None) is (expected == [])


def test_a_doubled_quote_in_a_quoted_part_is_one_quote() -> None:
    data = _data("legacy")
    _relate(data, alias='sales"daily')
    path = "$.semantic_models[0].node_relation.relation_name"
    relation = _model(data, "sales_daily")["node_relation"]
    assert relation["relation_name"] == '"x"."dm"."sales""daily"'
    for spelled, expected in [
        ('"x"."dm"."sales""daily"', []),
        ('"x"."dm"."sales""""daily"', [(RELATION_MISMATCH, BLOCK, path)]),
        ('"x"."dm"."sales"daily"', [(RELATION_UNPROVEN, REVIEW, path)]),
    ]:
        relation["relation_name"] = spelled
        gaps = _gaps(_resolve(data, "revenue"))
        assert [gap for gap in gaps if gap[2] == path] == expected, spelled
        # the alias is not the grain table's either
        assert (RELATION_MISMATCH, BLOCK, "$.semantic_models[0].node_relation") in gaps


def test_another_table_is_a_relation_mismatch() -> None:
    data = _data("legacy")
    path = "$.semantic_models[0].node_relation"
    assert _gaps(_resolve(data, "revenue", table="dm.sales")) == [(RELATION_MISMATCH, BLOCK, path)]
    _relate(data, schema_name="mart")
    assert _gaps(_resolve(data, "revenue")) == [(RELATION_MISMATCH, BLOCK, path)]


def test_grain_table_a_typed_term_does_not_take_is_a_construct() -> None:
    data = _data("legacy")
    _relate(data, alias="Sales_Daily")
    assert _gaps(_resolve(data, "revenue", table="dm.Sales_Daily")) == [
        (CONSTRUCT_UNSUPPORTED, REVIEW, _metric_path(data, "revenue"))
    ]


def test_another_event_time_is_a_time_mismatch() -> None:
    data = _data("legacy")
    assert _gaps(_resolve(data, "revenue", event_time="day")) == [
        (TIME_MISMATCH, BLOCK, f"{_dimension_path(data, 'date')}.name")
    ]


#: Each bare word PostgreSQL reads as a value, spelled here and not taken from the module: one
#: the resolver forgot would be read as a column.
VALUE_WORDS = (
    "true",
    "false",
    "null",
    "current_date",
    "current_time",
    "current_timestamp",
    "localtime",
    "localtimestamp",
    "current_user",
    "current_role",
    "current_catalog",
    "current_schema",
    "session_user",
    "system_user",
    "user",
)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("date", []),
        ("order_date", [(TIME_MISMATCH, BLOCK)]),
        ("DATE", [(TIME_UNPROVEN, REVIEW)]),
        ("date_trunc('day', ts)", [(TIME_UNPROVEN, REVIEW)]),
        *((word, [(TIME_UNPROVEN, REVIEW)]) for word in VALUE_WORDS),
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


def _set_metric_input(
    key: str, side: str = "numerator"
) -> Callable[[dict[str, Any]], tuple[str, str]]:
    def change(data: dict[str, Any]) -> tuple[str, str]:
        _metric(data, "gross_margin_pct")["type_params"][side][key] = SET_VALUES[key]
        path = f"{_metric_path(data, 'gross_margin_pct')}.type_params.{side}.{key}"
        return "gross_margin_pct", path

    return change


#: What each kind of metric takes its number from in `type_params`.
OWN_PARAMS = {
    "simple": ("measure", "expr", "metric_aggregation_params"),
    "ratio": ("numerator", "denominator"),
}
#: The keys of `type_params` each kind of metric reads as a construct, in the schema's order: each
#: but those it takes its number from, `input_measures` and `is_private`. Held apart from the
#: resolver's tables, which a test holds to them.
CONSTRUCT_PARAMS = {
    kind: [
        key
        for key in json.loads((FIXTURES / "dsi_0.213.0_schema.json").read_bytes())["models"][
            "PydanticMetricTypeParams"
        ]
        if key not in {*own, "input_measures", "is_private", "__extra__"}
    ]
    for kind, own in OWN_PARAMS.items()
}

SET_KEYS = [
    *(pytest.param(_set_metric("revenue", key), id=f"simple-{key}") for key in _METRIC),
    *(pytest.param(_set_metric("gross_margin_pct", key), id=f"ratio-{key}") for key in _METRIC),
    *(
        pytest.param(_set_params("revenue", key), id=f"simple-params-{key}")
        for key in CONSTRUCT_PARAMS["simple"]
    ),
    *(
        pytest.param(_set_params("gross_margin_pct", key), id=f"ratio-params-{key}")
        for key in CONSTRUCT_PARAMS["ratio"]
    ),
    *(pytest.param(_set_measure_input(key), id=f"measure-input-{key}") for key in _INPUT_MEASURE),
    *(
        pytest.param(_set_metric_input(key, side), id=f"{side}-{key}")
        for side in ("numerator", "denominator")
        for key in _METRIC_INPUT
    ),
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
    # each kind of metric, apart: a key both read is not one either may drop
    assert set(_SIMPLE_PARAMS) == set(CONSTRUCT_PARAMS["simple"])
    assert set(_RATIO_PARAMS) == set(CONSTRUCT_PARAMS["ratio"])
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


@pytest.mark.parametrize("expr", ["Revenue", *VALUE_WORDS, "revenue - gross_margin", "1"])
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


#: A ClickHouse type of an aggregated column, as it may be written, and its class: as the
#: engine writes it in `system.columns`, and nothing else.
CLICKHOUSE_SPELLINGS = [
    *((kind, "integer") for kind in ("Int8", "Int16", "Int32", "Int64", "Nullable(Int64)")),
    *(
        (kind, "decimal")
        for kind in (
            "Decimal(2, 2)",
            "Decimal(18, 2)",
            "Decimal(38, 2)",
            "Nullable(Decimal(18, 2))",
        )
    ),
    ("Float64", "float"),
    ("Nullable(Float64)", "float"),
    ("Date", "date"),
    ("Nullable(Date)", "date"),
    *(
        (kind, UNCLASSIFIED)
        for kind in (
            "Decimal(39, 2)",
            "Decimal(18,2)",
            "Decimal(18, 3)",
            "Decimal(1, 2)",
            "Decimal64(2)",
            "Nullable( Int64 )",
            "Nullable (Int64)",
            "Nullable(Int64",
            "Nullable(Int64 ",
            "Nullable(Int64))",
            " Int64",
            "int64",
            "BIGINT",
            "UInt8",
            "UInt64",
            "Nullable(UInt64)",
            "Int128",
            "Float32",
            "LowCardinality(Int64)",
            "LowCardinality(Nullable(Int64))",
            "Nullable(LowCardinality(Int64))",
            "Nullable(Nullable(Int64))",
            "Date32",
            "String",
        )
    ),
    ("", UNDECLARED),
    (None, UNDECLARED),
]


def _clickhouse_table(**types: str | None) -> DeclaredTable:
    """The grain table on ClickHouse: `types` over its default columns, None leaving one out."""
    found: dict[str, str | None] = {
        "date": "Date",
        "store_id": "Int32",
        "revenue": "Nullable(Decimal(18, 2))",
        "gross_margin": "Decimal(18, 2)",
        **types,
    }
    return DeclaredTable(
        "clickhouse", {name: kind for name, kind in found.items() if kind is not None}
    )


@pytest.mark.parametrize(("kind", "expected"), CLICKHOUSE_SPELLINGS)
def test_clickhouse_class_is_the_type_as_the_engine_writes_it(
    kind: str | None, expected: str
) -> None:
    declared = _clickhouse_table(revenue=kind)
    resolution = _resolve(_data("legacy"), "revenue", declared=declared)
    assert resolution.parity_rows == {f"clickhouse.date:legacy.sum.{expected}"}
    run = _resolve(_data("legacy"), "revenue", declared=declared, proven=PROVEN_PARITY)
    assert (run.formula is not None) is (expected in ("decimal", "integer", "float"))


def test_clickhouse_class_is_how_the_reference_sql_reads_the_column() -> None:
    # the reference SQL of a ratio reads a term as exact by its type, and as nullable where
    # the type holds `Nullable(` anywhere: for a member of a class and a member in `Nullable`,
    # it reads what the class says, and the class says it of no other spelling
    exact = {"decimal": _Exactness.DECIMAL, "integer": _Exactness.INTEGRAL, "float": None}
    members = {kind: found for kind, found in CLICKHOUSE_CLASSES.items() if found != "date"}
    assert set(members.values()) == set(exact)
    spelled = {**members, **{f"Nullable({kind})": found for kind, found in members.items()}}
    for kind, found in spelled.items():
        assert _exactness(_CLICKHOUSE_DIALECT, kind) is exact[found], kind
        assert ("Nullable(" in kind) is kind.startswith("Nullable("), kind
    for kind, found in CLICKHOUSE_SPELLINGS:
        if found in exact:
            assert kind in spelled, kind


@pytest.mark.parametrize(
    ("engine", "event_time_type", "keys", "context"),
    [
        ("postgres", "date", ("date", "store_id"), "postgres.date"),
        ("PostgreSQL", "date", ("store_id", "date"), "postgres.date"),
        ("postgresql", "date", ("date", "store_id"), "postgres.date"),
        ("postgres", "timestamp", ("date", "store_id"), f"postgres.{UNCLASSIFIED}"),
        ("postgres", None, ("date", "store_id"), f"postgres.{UNDECLARED}"),
        ("postgres", "date", ("store_id",), f"postgres.{OUTSIDE_GRAIN}"),
        ("clickhouse", "Date", ("date", "store_id"), "clickhouse.date"),
        ("ClickHouse", "Date", ("store_id", "date"), "clickhouse.date"),
        # the run's event time held no NULL: a nullable one is not its class
        ("clickhouse", "Nullable(Date)", ("date", "store_id"), f"clickhouse.{UNCLASSIFIED}"),
        ("clickhouse", "Date32", ("date", "store_id"), f"clickhouse.{UNCLASSIFIED}"),
        ("clickhouse", "DateTime", ("date", "store_id"), f"clickhouse.{UNCLASSIFIED}"),
        ("clickhouse", "date", ("date", "store_id"), f"clickhouse.{UNCLASSIFIED}"),
        ("clickhouse", None, ("date", "store_id"), f"clickhouse.{UNDECLARED}"),
        ("clickhouse", "Date", ("store_id",), f"clickhouse.{OUTSIDE_GRAIN}"),
        ("greenplum", "date", ("date", "store_id"), f"greenplum.{UNCLASSIFIED}"),
        ("", "date", ("date", "store_id"), f"{UNDECLARED}.{UNCLASSIFIED}"),
    ],
)
def test_parity_row_holds_in_the_engine_and_event_time_of_the_table(
    engine: str, event_time_type: str | None, keys: tuple[str, ...], context: str
) -> None:
    data = _data("legacy")
    on_clickhouse = engine.lower() == "clickhouse"
    table = _clickhouse_table() if on_clickhouse else SALES
    types = {name: value for name, value in table.column_types.items() if name != "date"}
    if event_time_type is not None:
        types["date"] = event_time_type
    declared = DeclaredTable(engine, types)
    # the column is `numeric(18,2)` on PostgreSQL and `Nullable(Decimal(18, 2))` on ClickHouse
    kind = "decimal" if context.startswith(("postgres.", "clickhouse.")) else UNCLASSIFIED
    row = f"{context}:legacy.sum.{kind}"
    resolution = _resolve(data, "revenue", declared=declared, keys=keys)
    assert resolution.parity_rows == {row}
    # what the recorded run proved holds in its own contexts only
    default = _resolve(data, "revenue", declared=declared, keys=keys, proven=PROVEN_PARITY)
    if context in ("postgres.date", "clickhouse.date"):
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
    # a dataclass looks its module up by name while the module runs; each test takes a module
    # of its own, and none stays behind
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def _result() -> dict[str, Any]:
    result: dict[str, Any] = json.loads((PARITY / "result.json").read_bytes())
    return result


def _sides(values: list[list[Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A metric's recorded values as the rows of each side: the reference's, MetricFlow's."""
    ours = [{"k1": day, "k2": int(store), "v": ref} for day, store, ref, _ in values]
    theirs = [{"k1": day, "k2": int(store), "v": mf} for day, store, _, mf in values]
    return ours, theirs


def test_proven_parity_is_the_recorded_run() -> None:
    script, result = _script(), _result()
    raw = (PARITY / "semantic_manifest.json").read_bytes()
    assert result["artifact_digest"] == digest_bytes(raw)
    assert result["versions"]["metricflow"] == "0.213.0"
    assert result["versions"]["postgres"].startswith("PostgreSQL 16.15 ")
    assert result["versions"]["clickhouse"] == "24.8.14.39"
    assert result["clickhouse_settings"] == script.CLICKHOUSE_SETTINGS
    assert frozenset(result["proven"]) == PROVEN_PARITY
    # the numbers ADR 0008 states for the run
    counts = Counter(row.split(":", 1)[0] for row in PROVEN_PARITY)
    assert counts == {"postgres.date": 164, "clickhouse.date": 164}


def test_recorded_types_are_those_a_class_computes_in() -> None:
    script = _script()
    types = _result()["clickhouse_types"]
    assert set(types) == set(script.PROBED_TYPES)
    assert all(set(found) == set(script.PROBED_AGGREGATES) for found in types.values())
    # the run's column of each class, NOT NULL: the type the members are held to
    declared = script.DECLARED["clickhouse.date"].column_types
    run = {script.CLASSES[twin]: declared[twin] for twin in script.TWINS.values()}
    assert run == {"decimal": "Decimal(18, 2)", "integer": "Int64", "float": "Float64"}
    assert {kind: types[kind]["sum"] for kind in run.values()} == {
        "Decimal(18, 2)": "Decimal(38, 2)",
        "Int64": "Int64",
        "Float64": "Float64",
    }
    for kind, found in CLICKHOUSE_CLASSES.items():
        if found == "date":
            continue
        # a member sums and averages as its class's run type does, its min and max are its own
        assert types[kind]["sum"] == types[run[found]]["sum"], kind
        assert types[kind]["avg"] == types[run[found]]["avg"] == "Float64", kind
        assert types[kind]["min"] == types[kind]["max"] == kind, kind
    # an unsigned integer sums in an unsigned type: no class holds it
    for kind in ("UInt8", "UInt16", "UInt32", "UInt64"):
        assert types[kind]["sum"] == "UInt64"
        assert kind not in CLICKHOUSE_CLASSES


def test_recorded_run_is_its_own_proof() -> None:
    script, result = _script(), _result()
    read = read_semantic_manifest((PARITY / "semantic_manifest.json").read_bytes())
    assert read.manifest is not None
    assert sorted(result["contexts"]) == sorted(script.CONTEXTS)
    proven: set[str] = set()
    for context, found in result["contexts"].items():
        cases = found["metrics"]
        subjects = script.subjects(context)
        assert sorted(subjects) == read.manifest.metric_names() == sorted(cases)
        assert script._proven(cases) == set(found["proven"])
        assert all(row.startswith(f"{context}:") for row in found["proven"])
        proven |= set(found["proven"])
        assert {case["outcome"] for case in cases.values()} == {"pass", "no_reference_sql"}
        for name, case in cases.items():
            assert case["subject"] == subjects[name]
            assert case["rule"] == script.rule(context, case["formula"]), name
            if case["outcome"] == "no_reference_sql":
                # only a ratio with an average term goes without reference SQL
                terms = (case["formula"]["numerator"], case["formula"]["denominator"])
                assert "avg" in {term["agg"] for term in terms}, name
                continue
            assert case["values"] and case["mismatches"] == []
            ours, theirs = _sides(case["values"])
            assert script._compare(ours, theirs, rule=case["rule"]) == [], (context, name)
        # each control as its recorded values give it: the reference's rows of one metric
        # against MetricFlow's of the other, by the rule of the reference's
        assert found["controls"]
        for control in found["controls"]:
            ours, _ = _sides(cases[control["reference"]]["values"])
            _, theirs = _sides(cases[control["metricflow"]]["values"])
            rule = cases[control["reference"]]["rule"]
            assert control["mismatches"] == len(script._compare(ours, theirs, rule=rule))
            assert control["numeric"] == script._numeric(ours, theirs, rule=rule) > 0, control
    assert proven == set(result["proven"])


def test_metricflow_side_is_one_query_in_every_context() -> None:
    contexts = _result()["contexts"]
    compared: dict[str, list[list[tuple[str, str, str | None]]]] = {}
    for found in contexts.values():
        for name, case in found["metrics"].items():
            if "values" in case:
                mf = [(day, store, value) for day, store, _, value in case["values"]]
                compared.setdefault(name, []).append(mf)
    # every metric compared in one context is compared in the other, with MetricFlow's
    # values as one query gave them
    assert compared
    for name, sides in compared.items():
        assert len(sides) == len(contexts), name
        assert all(side == sides[0] for side in sides), name


def test_an_average_on_clickhouse_is_compared_as_a_double() -> None:
    script = _script()
    contexts = _result()["contexts"]
    for context, found in contexts.items():
        for name, case in found["metrics"].items():
            average = case["formula"].get("aggregate", {}).get("agg") == "avg"
            on_clickhouse = context == "clickhouse.date"
            assert (case["rule"] == "double") is (average and on_clickhouse), (context, name)
    # the rule is needed: ClickHouse's mean of a decimal and of an integer column is, on some
    # key, not the numeric MetricFlow's PostgreSQL gives (itself rounded at its scale: not the
    # exact mean either), and lies within the rule
    cases = contexts["clickhouse.date"]["metrics"]
    for name in ("amount_average", "qty_average", "amount_nn_average", "qty_nn_average"):
        inexact = [
            (ours, theirs)
            for _, _, ours, theirs in cases[name]["values"]
            if ours is not None and Decimal(ours) != Decimal(theirs)
        ]
        assert inexact, name
        for ours, theirs in inexact:
            assert script._same(ours, theirs, rule="double"), name
            assert not script._same(ours, theirs, rule="equal"), name


def test_recorded_formulas_are_what_resolution_gives_now() -> None:
    script, result = _script(), _result()
    read = read_semantic_manifest((PARITY / "semantic_manifest.json").read_bytes())
    assert read.manifest is not None
    for context, found in result["contexts"].items():
        every = frozenset(script.subjects(context).values())
        for name, case in found["metrics"].items():
            contract = script.metric_contract(read.manifest, name)
            assert contract.additivity.value == (
                "additive"
                if case["formula"].get("aggregate", {}).get("agg") in ("sum", "count")
                else "non_additive"
            ), name
            resolution = resolve_formula(
                contract,
                read,
                database=script.DATABASE,
                declared=script.DECLARED[context],
                proven=every,
            )
            assert resolution.formula is not None, (context, name)
            assert resolution.formula.model_dump(mode="json", exclude_none=True) == case["formula"]
            assert sorted(resolution.parity_rows) == case["rows"]
            assert case["subject"] in case["rows"]
            assert all(row.startswith(f"{context}:") for row in case["rows"])


def test_recorded_reference_sql_is_what_the_compiler_writes_now() -> None:
    # the record holds the digest of the reference SQL each metric ran: SQL the compiler writes
    # otherwise now is not what the run proved, and the record must be run again
    for context, found in _result()["contexts"].items():
        bodies = _reference_bodies(context)
        cases = found["metrics"]
        ran = {name for name, case in cases.items() if "reference_sql" in case}
        passed = {name for name, case in cases.items() if case["outcome"] == "pass"}
        assert ran == set(bodies) == passed, context
        for name in ran:
            assert cases[name]["reference_sql"] == sha256_digest(bodies[name]), (context, name)


@pytest.mark.parametrize(
    ("reference", "metricflow", "rule", "same"),
    [
        ("1.50", "1.5", "equal", True),
        ("1.50", "1.51", "equal", False),
        (None, None, "equal", True),
        (None, "0", "equal", False),
        ("1", "1.000000000000001", "equal", False),
        ("0", None, "ratio", False),
        ("0.333333", "0.3333333333333333", "ratio", True),
        ("0.333333", "0.3333341", "ratio", False),
        ("0.333333", "0.3333329", "ratio", False),
        ("-0.333333", "-0.3333333333333333", "ratio", True),
        ("-0.333333", "-0.3333343", "ratio", False),
        ("-0.333333", "-0.3333329", "ratio", False),
        ("0.000000", "3.00000000000003E-12", "ratio", True),
        ("0.000000", "-3E-7", "ratio", True),
        ("0.000000", "-0.0000011", "ratio", False),
        ("0.000000", "0.0000011", "ratio", False),
        # the error of a double is |mf| * 1e-15: within it a value agrees, beyond it not
        ("1.000000", "0.9999999999999995", "ratio", True),
        ("1.000000", "0.999999999999995", "ratio", False),
        ("1.000000", "1.0000010000000005", "ratio", True),
        ("1.000000", "1.000001000000005", "ratio", False),
        # and mirrored below zero, at both ends of the values truncating to the reference
        ("-1.000000", "-0.9999999999999995", "ratio", True),
        ("-1.000000", "-0.999999999999995", "ratio", False),
        ("-1.000000", "-1.0000010000000005", "ratio", True),
        ("-1.000000", "-1.000001000000005", "ratio", False),
        # a double keeps about sixteen digits: the rest is its error, not another quotient
        ("142857142857.142857", "142857142857.14285", "ratio", True),
        ("142857142857.142857", "142857142857.14", "ratio", False),
        # an average on ClickHouse: the reference, a double, within 1e-15 of its own value
        # from MetricFlow's, and within 1e-300 of zero
        ("1", "1.000000000000001", "double", True),
        ("1", "1.0000000000000011", "double", False),
        ("1", "0.999999999999999", "double", True),
        ("-1", "-1.000000000000001", "double", True),
        ("-1", "-1.0000000000000011", "double", False),
        ("0", "1E-300", "double", True),
        ("0", "2E-300", "double", False),
        (None, None, "double", True),
        ("1", None, "double", False),
        (None, "0", "double", False),
    ],
)
def test_parity_rule(reference: str | None, metricflow: str | None, rule: str, same: bool) -> None:
    def as_number(text: str | None) -> Decimal | None:
        return None if text is None else Decimal(text)

    script = _script()
    assert script._same(as_number(reference), as_number(metricflow), rule=rule) is same


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
    assert len(script._compare(ours, theirs, rule="equal")) == 2
    assert script._compare(ours, ours, rule="equal") == []


def test_recorded_values_hold_the_keys_of_either_side() -> None:
    script = _script()
    ours = [
        {"k1": "2026-01-02", "k2": 10, "v": Decimal("2")},
        {"k1": "2026-01-02", "k2": 2, "v": Decimal("0.00")},
        {"k1": "2026-01-01", "k2": 2, "v": None},
        {"k1": "2026-01-04", "k2": 1, "v": Decimal("1.50")},
    ]
    theirs = [
        {"k1": "2026-01-01", "k2": 2, "v": None},
        {"k1": "2026-01-03", "k2": 1, "v": 3.5},
        {"k1": "2026-01-04", "k2": 1, "v": 1.5},
        {"k1": "2026-01-02", "k2": 2, "v": 0},
    ]
    # a key one side lacks is NULL on that side, a NULL value is NULL on its own side, zero is a
    # value; keys in order of the day, then of the store as a number; each side as it came
    assert script._values(ours, theirs) == [
        ["2026-01-01", "2", None, None],
        ["2026-01-02", "2", "0.00", "0"],
        ["2026-01-02", "10", "2", None],
        ["2026-01-03", "1", None, "3.5"],
        ["2026-01-04", "1", "1.50", "1.5"],
    ]


def test_recorded_run_is_the_run_of_the_recipe() -> None:
    script, result = _script(), _result()
    assert result["recipe"] == script.recipe()
    for found in result["contexts"].values():
        recorded = [(control["reference"], control["metricflow"]) for control in found["controls"]]
        assert recorded == list(script.CONTROLS)


def test_controls_the_adr_names_differ_as_it_says() -> None:
    # an average against the average of its NOT NULL twin: a NULL read as zero shows, on
    # 2026-01-01, store 1, as 13.5 against 10.125; and a ratio truncated at the precision
    # agrees with a double holding fewer digits, on 2026-01-04, store 1
    def value(case: dict[str, Any], key: tuple[str, str], side: int) -> Decimal:
        row = next(row for row in case["values"] if (row[0], row[1]) == key)
        return Decimal(row[side])

    for found in _result()["contexts"].values():
        cases = found["metrics"]
        assert value(cases["amount_average"], ("2026-01-01", "1"), 2) == Decimal("13.5")
        assert value(cases["amount_nn_average"], ("2026-01-01", "1"), 3) == Decimal("10.125")
        ratio = cases["r_amount_sum_by_qty_sum"]
        assert value(ratio, ("2026-01-04", "1"), 2) == Decimal("142857142857.142857")
        assert value(ratio, ("2026-01-04", "1"), 3) == Decimal("142857142857.14285")


def _term_value(values: list[Fraction], agg: str) -> Fraction | None:
    """The aggregate of a group's values, None where it has none and the aggregate is NULL."""
    if agg in ("count", "count_distinct"):
        return Fraction(len(values) if agg == "count" else len(set(values)))
    if not values:
        return None
    if agg == "sum":
        return sum(values, Fraction(0))
    return min(values) if agg == "min" else max(values)


def test_matrix_data_holds_the_cases_it_names() -> None:
    script = _script()
    columns = list(script.COLUMNS)
    rows = script.table_rows()
    assert [row[:2] + tuple(row[2 : 2 + len(script.NULLABLE)]) for row in rows] == [
        tuple(row) for row in script.ROWS
    ]
    groups: dict[tuple[str, int], list[tuple[Any, ...]]] = {}
    for day, store, *values in rows:
        groups.setdefault((day, store), []).append(tuple(values))

    def present(found: list[tuple[Any, ...]], column: str) -> list[Decimal]:
        index = columns.index(column)
        return [Decimal(str(row[index])) for row in found if row[index] is not None]

    # a nullable column under count; a group where a column is all NULL (a NULL denominator)
    for column in script.NULLABLE:
        assert any(row[2 + columns.index(column)] is None for row in rows), column
        assert any(not present(found, column) for found in groups.values()), column
    # a zero denominator: a group whose values sum to zero
    assert any(
        present(found, "qty") and sum(present(found, "qty")) == 0 for found in groups.values()
    )
    # negative values in every column, a large numerator
    for column in columns:
        assert any(value < 0 for found in groups.values() for value in present(found, column))
    assert any(value >= 10**11 for found in groups.values() for value in present(found, "amount"))
    # repeated values under count_distinct
    for column in columns:
        assert any(
            len(present(found, column)) > len(set(present(found, column)))
            for found in groups.values()
        ), column
    # a date without rows between two with rows
    days = sorted({date.fromisoformat(day) for day, _ in groups})
    assert any((later - earlier).days > 1 for earlier, later in pairwise(days))
    # each NOT NULL twin: never NULL, zero where its column is NULL, its value elsewhere
    for column, twin in script.TWINS.items():
        assert script.CLASSES[twin] == script.CLASSES[column] == script.NULLABLE[column]
        for row in rows:
            value, other = row[2 + columns.index(column)], row[2 + columns.index(twin)]
            assert other is not None
            if value is None:
                assert Decimal(str(other)) == 0, (column, row)
            else:
                assert other == value, (column, row)
    # no value two engines may count apart: a negative zero, NaN, an infinity
    for row in rows:
        for value in row[2:]:
            if value is not None:
                number = Decimal(str(value))
                assert number.is_finite(), row
                assert not (number.is_zero() and number.is_signed()), row
    # a group whose mean a double does not hold, of a decimal and of an integer column
    for column in ("amount", "qty"):
        means = [
            sum(map(Fraction, present(found, column))) / len(present(found, column))
            for found in groups.values()
            if present(found, column)
        ]
        assert any(Fraction(float(mean)) != mean for mean in means), column
    # no aggregate leaves the types ClickHouse computes it in: an integer sum within Int64
    # whatever the order, a decimal one within Decimal(38, 2); a float sum exact in any order
    for found in groups.values():
        for column in columns:
            values = present(found, column)
            kind = script.CLASSES[column]
            if kind == "integer":
                assert sum(abs(value) for value in values) < 2**63, column
            elif kind == "decimal":
                assert sum(abs(value) for value in values) < 10**36, column
            else:
                # the sum of the doubles the column holds, which no order of adding rounds
                exact = sum((Fraction(float(value)) for value in values), Fraction(0))
                for order in permutations(values):
                    total = 0.0
                    for value in order:
                        total += float(value)
                    assert Fraction(total) == exact, (column, order)
    # and the quotients of two ratio terms stay far from what `divideDecimal` at the
    # precision holds: their range is under 10**32
    terms = [
        abs(term)
        for found in groups.values()
        for column in columns
        for agg in script._side_aggregates(column)
        if agg != "average"
        for term in [_term_value(list(map(Fraction, present(found, column))), agg)]
        if term
    ]
    assert max(terms) / min(terms) < 10**32
    # a ratio is over any two columns, the same column included, of one nullability or not
    pairs = tuple((top, bottom) for top in script.COLUMNS for bottom in script.COLUMNS)
    assert pairs == script.RATIO_SIDES


def test_a_proven_ratio_row_holds_over_each_nullability_of_its_terms() -> None:
    # the class of a column does not name its nullability, and ClickHouse's reference SQL casts
    # an integral numerator by it: a ratio row is proven over each pair of nullabilities
    script, result = _script(), _result()
    every = {(top, bottom) for top in (True, False) for bottom in (True, False)}
    for context, found in result["contexts"].items():
        held: dict[str, set[tuple[bool, bool]]] = {}
        for top, bottom in script.RATIO_SIDES:
            for top_agg in script._side_aggregates(top):
                for bottom_agg in script._side_aggregates(bottom):
                    case = found["metrics"][script._ratio_name(top, top_agg, bottom, bottom_agg)]
                    if case["outcome"] == "pass":
                        nullable = (top in script.NULLABLE, bottom in script.NULLABLE)
                        held.setdefault(case["subject"], set()).add(nullable)
        ratios = [row for row in found["proven"] if row.startswith(f"{context}:ratio.")]
        assert ratios and sorted(held) == ratios, context
        for row in ratios:
            assert held[row] == every, (context, row)


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
    for context, found in result["contexts"].items():
        for name, case in found["metrics"].items():
            if case["outcome"] == "pass":
                assert {(day, store) for day, store, _, _ in case["values"]} == keys, (
                    context,
                    name,
                )


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

#: How the run asks PostgreSQL and ClickHouse for a metric's rows by two keys.
_KEYED = re.compile(
    r"SELECT json_agg\(t ORDER BY t\.k1, t\.k2\) FROM \("
    r'SELECT CAST\(q\."(?P<k1>[^"]+)" AS date\) AS k1, q\."(?P<k2>[^"]+)" AS k2, '
    r'q\."(?P<v>[^"]+)" AS v FROM \((?P<body>.*)\n\) AS q\) AS t',
    re.DOTALL,
)
_KEYED_CLICKHOUSE = re.compile(
    r'SELECT CAST\(q\."(?P<k1>[^"]+)" AS Date\) AS k1, q\."(?P<k2>[^"]+)" AS k2, '
    r'q\."(?P<v>[^"]+)" AS v FROM \((?P<body>.*)\n\) AS q ORDER BY k1, k2',
    re.DOTALL,
)
#: How the run asks ClickHouse for the types of the aggregates over a type.
_PROBE = re.compile(r"SELECT '(?P<kind>[^']+)' AS probed, (?P<aggregates>.*)")
_PROBED = re.compile(r"toTypeName\((?P<agg>\w+)\(CAST\(0 AS (?P<kind>.+?)\)\)\) AS (?P<alias>\w+)")
_SETTINGS = re.compile(
    r"SELECT name, value FROM system\.settings WHERE name IN \((?P<names>[^)]*)\) ORDER BY name"
)
_COLUMNS_SQL = (
    "SELECT json_agg(json_build_object('c', attname, 't', format_type(atttypid, atttypmod), "
    "'n', attnotnull) ORDER BY attnum) FROM pg_attribute WHERE attrelid = 'dm.sales'::regclass "
    "AND attnum > 0 AND NOT attisdropped"
)
_CLICKHOUSE_COLUMNS_SQL = (
    "SELECT name AS c, type AS t FROM system.columns "
    "WHERE database = 'dm' AND table = 'sales' ORDER BY position"
)
_FORMAT = "\nFORMAT JSONEachRow"


@functools.cache
def _reference_bodies(context: str) -> dict[str, str]:
    """The reference SQL of each formula the context recorded, compiled here from the
    context's native model and the script's contract carrying that formula."""
    script = _script()
    read = read_semantic_manifest((PARITY / "semantic_manifest.json").read_bytes())
    assert read.manifest is not None
    engine = script.DECLARED[context].engine
    refs = {}
    with tempfile.TemporaryDirectory() as folder:
        model = Path(folder) / "model.yaml"
        model.write_text(script.project_files()[f"model.{engine}.yaml"], encoding="utf-8")
        (Path(folder) / "contracts").mkdir()
        for name, case in _result()["contexts"][context]["metrics"].items():
            contract = script.metric_contract(read.manifest, name).model_copy(
                update={"formula": MetricFormula.model_validate(case["formula"])}
            )
            (Path(folder) / "contracts" / f"{name}.yaml").write_text(
                yaml.safe_dump(contract.model_dump(mode="json", exclude_none=True)),
                encoding="utf-8",
            )
            refs[f"{contract.metadata.id}@{contract.metadata.version}"] = name
        snapshot = NativeModelSource(model, Path(folder) / "contracts").snapshot()
    compiled = compile_metric_queries(snapshot, list(refs))
    return {refs[query.metric_ref]: query.sql for query in compiled.queries}


def _body(context: str, name: str) -> str:
    """The metric's reference SQL as the run's keyed query wraps it."""
    return _reference_bodies(context)[name].strip().rstrip(";")


def _number(value: Any) -> str:
    """A value as a JSON number, as both engines write one."""
    return "null" if value is None else str(value)


def _keyed_rows(values: list[list[Any]], column: int) -> list[str]:
    """Recorded values `[day, store, ...]` as the keyed rows of the value at `column`, which
    the run reads back as it recorded them."""
    for row in values:
        assert (
            row[column] is None or str(json.loads(row[column], parse_float=Decimal)) == row[column]
        )
    return [f'{{"k1": "{row[0]}", "k2": {row[1]}, "v": {_number(row[column])}}}' for row in values]


def _replay(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    *,
    postgres_rows: Any = None,
    clickhouse_rows: Any = None,
    postgres_columns: dict[str, tuple[str, bool]] | None = None,
    clickhouse_columns: dict[str, str] | None = None,
    settings: dict[str, str] | None = None,
    wrong: str = "",
    asked: Counter[str] | None = None,
) -> ModuleType:
    """The script with its project written under `project` and its subprocesses answered from
    the recorded run. The bridge plans `MF <metric>` for each metric asked. psql gives its
    version, the table's columns with their types and NOT NULL (`postgres_columns`; by default
    the declared types, NOT NULL on the keys and the twins) and rows (`postgres_rows`, the
    recipe's by default), and the values of a query keyed as one side groups: MetricFlow's
    recorded ones, counted in `asked`, the first of `wrong` another number; the reference's
    of `postgres.date`. `clickhouse local` must run with the recorded settings over the
    project's data; it gives its version, the settings asked (`settings`, the recorded ones by
    default), the table's columns (`clickhouse_columns`, the declared ones by default) and
    rows (`clickhouse_rows`), the recorded types of each probed aggregate, and the reference
    values of `clickhouse.date`. A reference query must be the SQL compiled for the metric's
    recorded formula in its context."""
    script, result = _script(), _result()
    recorded = result["contexts"]
    script.write_project(project)
    (project / "target").mkdir()
    artifact = project / "target" / "semantic_manifest.json"
    artifact.write_bytes((PARITY / "semantic_manifest.json").read_bytes())
    data = script.project_files()["data.clickhouse.sql"]
    counted: Counter[str] = Counter() if asked is None else asked
    # MetricFlow's values of a metric are one in every context that compared it
    metricflow = {
        name: [[day, store, value] for day, store, _, value in case["values"]]
        for found in recorded.values()
        for name, case in found["metrics"].items()
        if "values" in case
    }
    if postgres_columns is None:
        postgres_columns = {
            name: (kind, name in script.KEYS or name.endswith("_nn"))
            for name, kind in script.DECLARED["postgres.date"].column_types.items()
        }
    columns = clickhouse_columns or dict(script.DECLARED["clickhouse.date"].column_types)
    answer = result["clickhouse_settings"] if settings is None else settings

    def table(rows: Any) -> list[str]:
        names = ("store_id", *script.COLUMNS)
        return [
            f'{{"{script.EVENT_TIME}": "{day}", '
            + ", ".join(
                f'"{name}": {_number(value)}' for name, value in zip(names, rest, strict=True)
            )
            + "}"
            for day, *rest in (script.table_rows() if rows is None else rows)
        ]

    def psql(sql: str) -> str:
        if sql == "SELECT json_agg(t) FROM (SELECT version() AS v) t":
            return json.dumps([{"v": result["versions"]["postgres"]}])
        if sql == _COLUMNS_SQL:
            described = [
                {"c": name, "t": kind, "n": nn} for name, (kind, nn) in postgres_columns.items()
            ]
            return json.dumps(described)
        if sql == "SELECT json_agg(t) FROM dm.sales AS t":
            return "[" + ",".join(table(postgres_rows)) + "]"
        found = _KEYED.fullmatch(sql)
        assert found is not None, sql
        name, body = found["v"], found["body"]
        if body.startswith("MF "):
            assert body == f"MF {name}"
            assert (found["k1"], found["k2"]) == script.GROUP_BY
            counted[name] += 1
            values = _keyed_rows(metricflow[name], 2)
            if name == wrong:
                day, store, _ = metricflow[name][0]
                values[0] = f'{{"k1": "{day}", "k2": {store}, "v": 123456}}'
            return "[" + ",".join(values) + "]"
        assert (found["k1"], found["k2"]) == script.KEYS
        assert body == _body("postgres.date", name), name
        return (
            "["
            + ",".join(_keyed_rows(recorded["postgres.date"]["metrics"][name]["values"], 2))
            + "]"
        )

    def probe(sql: str) -> list[str]:
        found = []
        for part in sql.split("\nUNION ALL\n"):
            select = _PROBE.fullmatch(part)
            assert select is not None, part
            kind, aggregates = select["kind"], list(_PROBED.finditer(select["aggregates"]))
            assert ", ".join(match[0] for match in aggregates) == select["aggregates"]
            assert all(match["kind"] == kind for match in aggregates), part
            assert all(match["agg"] == match["alias"] for match in aggregates), part
            types = result["clickhouse_types"][kind]
            found.append(
                json.dumps(
                    {"probed": kind, **{match["agg"]: types[match["agg"]] for match in aggregates}}
                )
            )
        return found

    def clickhouse(argv: list[str]) -> list[str]:
        assert argv[:3] == ["clickhouse", "local", "--multiquery"]
        assert argv[-2] == "-q"
        flags = dict(flag.removeprefix("--").split("=", 1) for flag in argv[3:-2])
        assert flags == result["clickhouse_settings"]
        query = argv[-1]
        # the recipe's data loaded for each query, the rows as JSON
        assert query.startswith(data) and query.endswith(_FORMAT)
        sql = query[len(data) : -len(_FORMAT)]
        if sql == "SELECT version() AS v":
            return [json.dumps({"v": result["versions"]["clickhouse"]})]
        if (settings_asked := _SETTINGS.fullmatch(sql)) is not None:
            names = sorted(name.strip().strip("'") for name in settings_asked["names"].split(","))
            return [
                json.dumps({"name": name, "value": answer[name]})
                for name in names
                if name in answer
            ]
        if sql == _CLICKHOUSE_COLUMNS_SQL:
            return [json.dumps({"c": name, "t": kind}) for name, kind in columns.items()]
        if sql == "SELECT * FROM dm.sales":
            return table(clickhouse_rows)
        if sql.startswith("SELECT '"):
            return probe(sql)
        found = _KEYED_CLICKHOUSE.fullmatch(sql)
        assert found is not None, sql
        name = found["v"]
        assert (found["k1"], found["k2"]) == script.KEYS
        assert found["body"] == _body("clickhouse.date", name), name
        return _keyed_rows(recorded["clickhouse.date"]["metrics"][name]["values"], 2)

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        assert kwargs["check"] is True
        if argv[0] == "psql":
            # no psqlrc, unaligned tuples only, a failing statement fails psql, the SQL a command
            assert argv[:-1] == [
                "psql",
                *("-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"),
                *("-d", "conninfo", "-c"),
            ]
            return SimpleNamespace(stdout=psql(argv[-1]))
        if argv[0] == "clickhouse":
            return SimpleNamespace(stdout="".join(f"{line}\n" for line in clickhouse(argv)))
        assert argv[0] == "mf-python"
        if argv[1] == "-c":
            assert argv[2:] == ["import importlib.metadata as m; print(m.version('metricflow'))"]
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
    code: int = script.run(
        project, "mf-python", "psql", "conninfo", "clickhouse", project / "result.json"
    )
    return code


def _ran(project: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((project / "result.json").read_bytes())
    return result


def test_run_compares_the_reference_with_metricflow_as_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked: Counter[str] = Counter()
    script = _replay(monkeypatch, tmp_path, asked=asked)
    assert _run(script, tmp_path) == 0
    assert _ran(tmp_path) == _result()
    # MetricFlow's query of a metric runs once, whichever contexts compare with it
    compared = {
        name
        for found in _result()["contexts"].values()
        for name, case in found["metrics"].items()
        if case["outcome"] == "pass"
    }
    assert asked == Counter(dict.fromkeys(compared, 1))


def test_run_records_a_metric_that_disagrees_and_what_falls_with_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = _replay(monkeypatch, tmp_path, wrong="qty_sum")
    assert _run(script, tmp_path) == 0
    result = _ran(tmp_path)
    for context, found in result["contexts"].items():
        cases = found["metrics"]
        failed = {name for name, case in cases.items() if case["outcome"] == "fail"}
        assert failed == {"qty_sum"}, context
        assert len(cases["qty_sum"]["mismatches"]) == 1
        # its row, the rows relying on it, and the rows relying on those are no longer proven
        fallen = {
            f"{context}:legacy.{form}"
            for form in ("sum.integer", "measure_without_expr", "metric_expr")
        }
        assert not fallen & set(found["proven"])
        assert not fallen & set(result["proven"])
        assert f"{context}:legacy.sum.decimal" in found["proven"]


@pytest.mark.parametrize(
    ("control", "mismatches"),
    [
        (("amount_sum", "amount_sum"), 0),
        # NULL against zero where the column is all NULL, and no number apart
        (("amount_sum", "amount_nn_sum"), 2),
    ],
)
def test_run_fails_when_a_control_differs_in_no_number(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    control: tuple[str, str],
    mismatches: int,
) -> None:
    script = _replay(monkeypatch, tmp_path)
    monkeypatch.setattr(script, "CONTROLS", (control,))
    assert _run(script, tmp_path) == 1
    for found in _ran(tmp_path)["contexts"].values():
        recorded = [(control["mismatches"], control["numeric"]) for control in found["controls"]]
        assert recorded == [(mismatches, 0)]


def test_run_refuses_rows_other_than_the_recipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = _script().table_rows()
    # a row short, a row repeated, a value changed
    changed = ((*rows[0][:2], "10.01", *rows[0][3:]), *rows[1:])
    for engine, side in (("PostgreSQL", "postgres_rows"), ("ClickHouse", "clickhouse_rows")):
        for index, other in enumerate((rows[:-1], (*rows, rows[0]), changed)):
            project = tmp_path / f"{side}{index}"
            script = _replay(monkeypatch, project, **{side: other})
            with pytest.raises(SystemExit, match=f"on {engine} holds other rows"):
                _run(script, project)


def test_run_refuses_columns_other_than_the_declared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = _script()
    postgres = {
        name: (kind, name in script.KEYS or name.endswith("_nn"))
        for name, kind in script.DECLARED["postgres.date"].column_types.items()
    }
    # the recipe's values in a column of another type, a column more, a twin that may be NULL
    for index, columns in enumerate(
        (
            {**postgres, "qty": ("numeric", False)},
            {**postgres, "note": ("text", False)},
            {**postgres, "qty_nn": ("bigint", False)},
        )
    ):
        project = tmp_path / f"postgres{index}"
        script = _replay(monkeypatch, project, postgres_columns=columns)
        with pytest.raises(SystemExit, match="on PostgreSQL has other columns or types"):
            _run(script, project)
    clickhouse = dict(_script().DECLARED["clickhouse.date"].column_types)
    # a column that may be NULL declared without `Nullable`, a column more
    for index, kinds in enumerate(
        ({**clickhouse, "amount": "Decimal(18, 2)"}, {**clickhouse, "note": "String"})
    ):
        project = tmp_path / f"clickhouse{index}"
        script = _replay(monkeypatch, project, clickhouse_columns=kinds)
        with pytest.raises(SystemExit, match="on ClickHouse has other columns or types"):
            _run(script, project)


def test_run_refuses_settings_and_files_other_than_the_recipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    approximate = {
        **_result()["clickhouse_settings"],
        "count_distinct_implementation": "uniqCombined",
    }
    script = _replay(monkeypatch, tmp_path / "settings", settings=approximate)
    with pytest.raises(SystemExit, match="other settings than the recipe's"):
        _run(script, tmp_path / "settings")
    script = _replay(monkeypatch, tmp_path / "files")
    with (tmp_path / "files" / "model.clickhouse.yaml").open("a", encoding="utf-8") as model:
        model.write("# changed\n")
    with pytest.raises(SystemExit, match=r"model\.clickhouse\.yaml is not the recipe's"):
        _run(script, tmp_path / "files")
