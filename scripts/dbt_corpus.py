"""Corpus of ADR 0008, slice S5b: what dbt-core writes for each construct of the inventory.

Manual and stand-only, like ``metricflow_parity.py``; CI does not run it. The inventory
(``docs/specs/dbt-formula-inventory.md``) names every reason a dbt formula stays
``external_ref``, and its tests make most cases by changing, in memory, an artifact dbt wrote.
The corpus asks dbt itself. An entry is one construct as an author writes it in a dbt project —
in the legacy dialect (semantic models with measures, metrics apart) or in V2 (the semantic
model and its metrics on the model) — with the rows of the inventory its metric is to give: the
rows it aims at, and those dbt's writing brings along (every V2 metric's
``time.no-granularity``, the ``percentile`` a percentile measure must set).

``run`` parses the base project of each dialect, alone and with each entry alone; an entry dbt
refuses keeps dbt's error. The base with every entry dbt accepted is parsed again, once per
dialect: that artifact is a test fixture, and ``result.json`` records the versions, the recipe
(each project dbt parsed, what each entry is to give), the digest of each artifact and, for
each entry, dbt's error or what the resolver answers for its metric there — against a contract
on the grain table the metric aggregates in, its event time the ``date`` column, and the parity
rows proven today. The run stops, writing nothing, unless each metric, the controls' too,
resolves there as on its own project, the indexes in its paths aside: no entry, and no flag one
sets, changes how another resolves.

Steps (the Mac stand)::

    python scripts/dbt_corpus.py run --dbt DBT --out tests/fixtures/dbt_semantic_manifest/corpus

``DBT`` is the ``dbt`` of an environment with dbt-core and dbt-postgres. No database is
reached: ``dbt parse`` alone.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from auto_bi.evidence.canonical import digest_bytes, sha256_digest
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.semantic.metricflow import ManifestRead, read_semantic_manifest
from auto_bi.semantic.metricflow_formula import PROVEN_PARITY, DeclaredTable, resolve_formula

DIALECTS = ("legacy", "v2")
SCHEMA = "dm"
#: The database of the profile: dbt-postgres writes it into every relation.
DATABASE = "x"
TABLE = f"{SCHEMA}.sales_daily"
EVENT_TIME = "date"
KEYS = (EVENT_TIME, "store_id")
#: The grain table as the native model declares it: on PostgreSQL, its event time a `date`.
DECLARED = DeclaredTable(
    "postgres",
    {
        "date": "date",
        "store_id": "integer",
        "revenue": "numeric(18,2)",
        "gross_margin": "numeric(18,2)",
    },
)
FILTER = "{{ Dimension('sale__region') }} = 'north'"
#: The metrics of each base project; each resolves.
CONTROLS = ("revenue", "gross_margin_total", "gross_margin_pct")
NO_GRANULARITY = "time.no-granularity"
_UNNESTED = {"require_nested_cumulative_type_params": False}

_SALES_SQL = (
    "select cast(null as date) as date, 1 as store_id, 'r' as region, 1.0 as revenue,"
    " 1.0 as gross_margin, true as is_promo, cast(null as date) as order_date\n"
)
_SPINE = {
    "name": "metricflow_time_spine",
    "time_spine": {"standard_granularity_column": "date_day"},
    "columns": [{"name": "date_day", "granularity": "day"}],
}


@dataclass(frozen=True)
class Entry:
    """One construct as dbt is given it, and the rows of the inventory its metric is to give."""

    metric: str
    dialect: str
    aims: tuple[str, ...]
    #: The rows its metric gives besides, as dbt writes the construct.
    also: tuple[str, ...] = ()
    #: What it adds to the base project of its dialect: `measures` (legacy) or `metrics` (V2)
    #: of the semantic model `sales_daily`; `semantic_models` and `metrics` (legacy);
    #: `models` (properties) and `sql` (name -> select).
    add: dict[str, Any] = field(default_factory=dict)
    table: str = TABLE
    #: The contract's additivity: `additive` is what the additivity rows ask of a contract.
    additivity: str = "non_additive"
    #: Behaviour flags of `dbt_project.yml` the construct needs.
    flags: dict[str, Any] = field(default_factory=dict)


# --- the base projects -------------------------------------------------------------------


def _legacy_sales(measures: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": "sales_daily",
        "model": "ref('sales_daily')",
        "primary_entity": "sale",
        "defaults": {"agg_time_dimension": EVENT_TIME},
        "entities": [{"name": "store", "type": "foreign", "expr": "store_id"}],
        "dimensions": [
            {"name": EVENT_TIME, "type": "time", "type_params": {"time_granularity": "day"}},
            {"name": "region", "type": "categorical"},
        ],
        "measures": [
            {"name": "revenue", "agg": "sum", "expr": "revenue"},
            {"name": "gross_margin", "agg": "sum"},
            *measures,
        ],
    }


_LEGACY_CONTROLS = [
    {
        "name": "revenue",
        "label": "revenue",
        "type": "simple",
        "type_params": {"measure": "revenue"},
    },
    {
        "name": "gross_margin_total",
        "label": "gross_margin_total",
        "type": "simple",
        "type_params": {"measure": "gross_margin"},
    },
    {
        "name": "gross_margin_pct",
        "label": "gross_margin_pct",
        "type": "ratio",
        "type_params": {"numerator": "gross_margin_total", "denominator": "revenue"},
    },
]


def _v2_sales(metrics: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": "sales_daily",
        "semantic_model": True,
        "primary_entity": "sale",
        "agg_time_dimension": EVENT_TIME,
        "columns": [
            {"name": EVENT_TIME, "granularity": "day", "dimension": "time"},
            {"name": "store_id", "entity": {"type": "foreign", "name": "store"}},
            {"name": "region", "dimension": "categorical"},
        ],
        "metrics": [
            {"name": "revenue", "type": "simple", "agg": "sum", "expr": "revenue"},
            {"name": "gross_margin_total", "type": "simple", "agg": "sum", "expr": "gross_margin"},
            {
                "name": "gross_margin_pct",
                "type": "ratio",
                "numerator": "gross_margin_total",
                "denominator": "revenue",
            },
            *metrics,
        ],
    }


def project_files(dialect: str, entries: Sequence[Entry]) -> dict[str, str]:
    """The base project of `dialect` with `entries` added: path -> text."""
    added: dict[str, list[Any]] = {}
    sql = {
        "sales_daily": _SALES_SQL,
        "metricflow_time_spine": "select cast('2026-01-01' as date) as date_day\n",
    }
    flags: dict[str, Any] = {}
    for entry in entries:
        assert entry.dialect == dialect, entry
        for key, items in entry.add.items():
            if key == "sql":
                sql.update(items)
            else:
                added.setdefault(key, []).extend(items)
        for flag, value in entry.flags.items():
            assert flags.setdefault(flag, value) == value, f"{entry.metric} sets {flag} otherwise"
    if dialect == "legacy":
        schema: dict[str, Any] = {
            "models": [_SPINE, *added.pop("models", [])],
            "semantic_models": [
                _legacy_sales(added.pop("measures", [])),
                *added.pop("semantic_models", []),
            ],
            "metrics": [*_LEGACY_CONTROLS, *added.pop("metrics", [])],
        }
    else:
        schema = {
            "models": [_SPINE, _v2_sales(added.pop("metrics", [])), *added.pop("models", [])],
        }
    assert not added, f"{dialect} takes no {sorted(added)}"
    project = (
        "name: corpus\nversion: '1.0'\nconfig-version: 2\nprofile: corpus\n"
        "model-paths: ['models']\n"
    )
    if flags:
        project += "flags:\n" + "".join(
            f"  {flag}: {json.dumps(value)}\n" for flag, value in sorted(flags.items())
        )
    files = {
        "dbt_project.yml": project,
        "profiles.yml": (
            "corpus:\n  target: dev\n  outputs:\n    dev:\n      type: postgres\n"
            "      host: localhost\n      user: corpus\n      password: corpus\n      port: 5432\n"
            f"      dbname: {DATABASE}\n      schema: {SCHEMA}\n      threads: 1\n"
        ),
        "models/schema.yml": json.dumps(schema, indent=1, ensure_ascii=False) + "\n",
    }
    files.update({f"models/{name}.sql": text for name, text in sql.items()})
    return files


# --- the entries -------------------------------------------------------------------------


def _metric(name: str, params: dict[str, Any], kind: str = "simple", **keys: Any) -> dict[str, Any]:
    return {"name": name, "label": name, "type": kind, "type_params": params, **keys}


def _simple(name: str, measure: Any = "revenue", **keys: Any) -> dict[str, Any]:
    params = keys.pop("params", {})
    return _metric(name, {"measure": measure, **params}, **keys)


def _ratio(
    name: str, numerator: Any = "gross_margin_total", denominator: Any = "revenue", **keys: Any
) -> dict[str, Any]:
    params = keys.pop("params", {})
    return _metric(
        name, {"numerator": numerator, "denominator": denominator, **params}, "ratio", **keys
    )


def _legacy(
    metric: dict[str, Any],
    *aims: str,
    also: tuple[str, ...] = (),
    table: str = TABLE,
    additivity: str = "non_additive",
    flags: dict[str, Any] | None = None,
    **add: Any,
) -> Entry:
    metrics = [*add.pop("metrics", []), metric]
    return Entry(
        metric["name"],
        "legacy",
        aims,
        also,
        {"metrics": metrics, **add},
        table,
        additivity,
        flags or {},
    )


def _measured(
    name: str,
    measure: dict[str, Any],
    *aims: str,
    also: tuple[str, ...] = (),
    additivity: str = "non_additive",
) -> Entry:
    """A legacy metric over a measure of its own in `sales_daily`."""
    return _legacy(
        _simple(name, measure["name"]), *aims, also=also, additivity=additivity, measures=[measure]
    )


def _own_model(
    name: str,
    dimensions: list[dict[str, Any]],
    measures: list[dict[str, Any]],
    *,
    default: str | None,
    model: str = "sales_daily",
) -> dict[str, Any]:
    """A legacy semantic model of its own, on `model`, with an entity of its own."""
    semantic: dict[str, Any] = {
        "name": name,
        "model": f"ref('{model}')",
        "primary_entity": f"{name}_row",
    }
    if default is not None:
        semantic["defaults"] = {"agg_time_dimension": default}
    return {**semantic, "dimensions": dimensions, "measures": measures}


def _time(name: str, grain: str = "day", **keys: Any) -> dict[str, Any]:
    return {"name": name, "type": "time", "type_params": {"time_granularity": grain}, **keys}


def _sum(name: str, column: str | None = "revenue", **keys: Any) -> dict[str, Any]:
    measure: dict[str, Any] = {"name": name, "agg": "sum"}
    if column is not None:
        measure["expr"] = column
    return {**measure, **keys}


def _timed(name: str, dimension: dict[str, Any], *aims: str, default: str | None = "") -> Entry:
    """A legacy metric over a semantic model of its own whose time dimension is `dimension`."""
    model = _own_model(
        name,
        [dimension],
        [_sum(f"{name}_m")],
        default=dimension["name"] if default == "" else default,
    )
    return _legacy(_simple(name, f"{name}_m"), *aims, semantic_models=[model])


def _v2(
    name: str,
    *aims: str,
    also: tuple[str, ...] = (NO_GRANULARITY,),
    additivity: str = "non_additive",
    **keys: Any,
) -> Entry:
    """A V2 metric of `sales_daily`: a sum of `revenue` unless `keys` say otherwise."""
    metric = {"name": name, "type": "simple", "agg": "sum", "expr": "revenue", **keys}
    return Entry(name, "v2", aims, also, {"metrics": [metric]}, additivity=additivity)


def _legacy_entries() -> list[Entry]:
    entries = [
        _legacy(_simple("c_metric_filter", filter=FILTER), "metric.filter"),
        _legacy(
            _simple("c_metric_time_granularity", time_granularity="month"),
            "metric.time_granularity",
        ),
        _legacy(_simple("c_metric_datatype", datatype="decimal"), "metric.datatype"),
        _legacy(_simple("c_params_window", params={"window": "7 days"}), "params.window"),
        _legacy(
            _simple("c_params_grain_to_date", params={"grain_to_date": "month"}),
            "params.grain_to_date",
        ),
        # The keys of the cumulative parameters at the top of `type_params`, as dbt took them
        # before it nested them: a behaviour flag of the project keeps that form.
        _legacy(
            _simple("c_params_window_flag", params={"window": "7 days"}),
            "params.window",
            flags=_UNNESTED,
        ),
        _legacy(
            _simple("c_params_grain_to_date_flag", params={"grain_to_date": "month"}),
            "params.grain_to_date",
            flags=_UNNESTED,
        ),
        _legacy(
            _simple("c_params_metrics", params={"metrics": [{"name": "gross_margin_total"}]}),
            "params.metrics",
        ),
        _legacy(
            _simple(
                "c_params_conversion_type_params",
                params={
                    "conversion_type_params": {
                        "base_measure": "revenue",
                        "conversion_measure": "gross_margin",
                        "entity": "store",
                        "calculation": "conversion_rate",
                        "window": "7 days",
                    }
                },
            ),
            "params.conversion_type_params",
        ),
        _legacy(
            _simple(
                "c_params_cumulative_type_params",
                params={"cumulative_type_params": {"grain_to_date": "month", "period_agg": "last"}},
            ),
            "params.cumulative_type_params",
        ),
        _legacy(
            _simple("c_params_join_to_timespine", params={"join_to_timespine": True}),
            "params.join_to_timespine",
        ),
        _legacy(
            _simple("c_params_fill_nulls_with", params={"fill_nulls_with": 5}),
            "params.fill_nulls_with",
        ),
        _legacy(
            _simple("c_params_numerator", params={"numerator": "gross_margin_total"}),
            "params.numerator",
        ),
        _legacy(
            _simple("c_params_denominator", params={"denominator": "revenue"}), "params.denominator"
        ),
        _legacy(_ratio("c_params_measure", params={"measure": "revenue"}), "params.measure"),
        _legacy(_ratio("c_params_expr", params={"expr": "revenue"}), "params.expr"),
        _legacy(
            _ratio(
                "c_params_metric_aggregation_params",
                params={
                    "metric_aggregation_params": {"semantic_model": "sales_daily", "agg": "sum"}
                },
            ),
            "params.metric_aggregation_params",
        ),
        _legacy(
            _simple("c_measure_input_filter", {"name": "revenue", "filter": FILTER}),
            "measure-input.filter",
        ),
        _legacy(
            _simple("c_measure_input_alias", {"name": "revenue", "alias": "revenue_alias"}),
            "measure-input.alias",
        ),
        _legacy(
            _simple(
                "c_measure_input_join_to_timespine", {"name": "revenue", "join_to_timespine": True}
            ),
            "measure-input.join_to_timespine",
        ),
        _legacy(
            _simple("c_measure_input_fill_nulls_with", {"name": "revenue", "fill_nulls_with": 0}),
            "measure-input.fill_nulls_with",
        ),
    ]
    values = {"filter": FILTER, "offset_window": "7 days", "offset_to_grain": "month"}
    for side, metric in (("numerator", "gross_margin_total"), ("denominator", "revenue")):
        for key in ("filter", "alias", "offset_window", "offset_to_grain"):
            value = values.get(key, f"{side}_alias")
            entries.append(
                _legacy(
                    _ratio(f"c_metric_input_{side}_{key}", **{side: {"name": metric, key: value}}),
                    f"metric-input.{side}.{key}",
                )
            )
    entries += [
        _legacy(_metric("c_simple_neither", {}), "simple.both-or-neither"),
        _legacy(
            _simple(
                "c_simple_both",
                params={
                    "metric_aggregation_params": {"semantic_model": "sales_daily", "agg": "sum"}
                },
            ),
            "simple.both-or-neither",
        ),
        _measured(
            "c_legacy_agg_sum_boolean",
            {"name": "c_sum_boolean", "agg": "sum_boolean", "expr": "is_promo"},
            "legacy.agg.sum_boolean",
        ),
        _measured(
            "c_legacy_agg_percentile",
            {
                "name": "c_percentile",
                "agg": "percentile",
                "expr": "revenue",
                "agg_params": {"percentile": 0.9},
            },
            "legacy.agg.percentile",
            also=("legacy.agg_params.percentile",),
        ),
        _measured(
            "c_legacy_agg_median",
            {"name": "c_median", "agg": "median", "expr": "revenue"},
            "legacy.agg.median",
        ),
        _measured(
            "c_legacy_agg_params_percentile",
            _sum("c_sum_percentile", agg_params={"percentile": 0.5}),
            "legacy.agg_params.percentile",
        ),
    ]
    for key in ("use_discrete_percentile", "use_approximate_percentile"):
        entries += [
            _measured(
                f"c_legacy_agg_params_{key}",
                {
                    "name": f"c_percentile_{key}",
                    "agg": "percentile",
                    "expr": "revenue",
                    "agg_params": {"percentile": 0.9, key: True},
                },
                f"legacy.agg_params.{key}",
                also=("legacy.agg.percentile", "legacy.agg_params.percentile"),
            ),
            _measured(
                f"c_legacy_sum_{key}",
                _sum(f"c_sum_{key}", agg_params={key: True}),
                f"legacy.agg_params.{key}",
            ),
        ]
    entries.append(
        _measured(
            "c_legacy_non_additive_dimension",
            _sum(
                "c_non_additive",
                non_additive_dimension={"name": EVENT_TIME, "window_choice": "max"},
            ),
            "legacy.non-additive-dimension",
        )
    )
    for index, expr in enumerate(
        ("revenue - gross_margin", '"revenue"', "Revenue", "null", "true", "")
    ):
        entries.append(
            _measured(f"c_legacy_expr_{index}", _sum(f"c_expr_{index}", expr), "legacy.expr")
        )
    # A name that is no column of the resolver's: a reserved word of dbt's, an upper-case one,
    # and a word SQL reads as a value that dbt does not reserve.
    names = ("null", "Gross_Margin", "true")
    for index, name in enumerate(names):
        entries.append(
            _measured(f"c_legacy_measure_name_{index}", _sum(name, None), "legacy.measure-name")
        )
    entries.append(
        _legacy(
            _simple("c_legacy_metric_expr", params={"expr": "gross_margin"}), "legacy.metric-expr"
        )
    )
    entries.append(
        _timed(
            "c_time_expression",
            _time("day_expr", expr="date_trunc('day', order_date)"),
            "time.expression",
        )
    )
    # The same for a time dimension. dbt takes no name as a measure and a dimension both, so the
    # word SQL reads as a value is another one here: today's date.
    for index, name in enumerate(("null", "Date", "current_date")):
        entries.append(_timed(f"c_time_name_{index}", _time(name), "time.name"))
    entries += [
        _timed("c_time_column_name", _time("order_date"), "time.column-name"),
        _timed("c_time_column_expr", _time("ordered", expr="order_date"), "time.column-expr"),
        _timed(
            "c_time_granularity", _time("date_month", "month", expr=EVENT_TIME), "time.granularity"
        ),
        _timed("c_time_none_legacy", _time(EVENT_TIME), "time.none-legacy", default=None),
    ]
    not_time = _own_model(
        "c_time_not_time",
        [_time(EVENT_TIME), {"name": "kind", "type": "categorical"}],
        [_sum("c_time_not_time_m")],
        default="kind",
    )
    entries.append(
        _legacy(
            _simple("c_time_not_time", "c_time_not_time_m"),
            "time.not-time",
            semantic_models=[not_time],
        )
    )
    models = _own_model(
        "c_ratio_models",
        [_time(EVENT_TIME)],
        [_sum("c_ratio_models_m", "gross_margin")],
        default=EVENT_TIME,
    )
    entries.append(
        _legacy(
            _ratio("c_ratio_models", "c_ratio_models_part"),
            "ratio.models",
            semantic_models=[models],
            metrics=[_simple("c_ratio_models_part", "c_ratio_models_m")],
        )
    )
    times = _own_model(
        "c_ratio_times",
        [_time(EVENT_TIME), _time("date_b", expr=EVENT_TIME)],
        [
            _sum("c_ratio_times_a", "gross_margin", agg_time_dimension=EVENT_TIME),
            _sum("c_ratio_times_b", "revenue", agg_time_dimension="date_b"),
        ],
        default=EVENT_TIME,
    )
    entries.append(
        _legacy(
            _ratio("c_ratio_times", "c_ratio_times_top", "c_ratio_times_bottom"),
            "ratio.times",
            semantic_models=[times],
            metrics=[
                _simple("c_ratio_times_top", "c_ratio_times_a"),
                _simple("c_ratio_times_bottom", "c_ratio_times_b"),
            ],
        )
    )
    cumulative = {"grain_to_date": "month", "period_agg": "last"}
    entries += [
        _legacy(
            _metric(
                "c_type_cumulative",
                {"measure": "revenue", "cumulative_type_params": cumulative},
                "cumulative",
            ),
            "type.cumulative",
        ),
        _legacy(
            _metric(
                "c_type_derived",
                {
                    "expr": "revenue - revenue_prev",
                    "metrics": [
                        {"name": "revenue"},
                        {"name": "revenue", "offset_window": "7 days", "alias": "revenue_prev"},
                    ],
                },
                "derived",
            ),
            "type.derived",
        ),
        # dbt takes a conversion only between counts.
        _legacy(
            _metric(
                "c_type_conversion",
                {
                    "conversion_type_params": {
                        "base_measure": "c_conversion_base",
                        "conversion_measure": "c_conversion_done",
                        "entity": "store",
                        "calculation": "conversion_rate",
                        "window": "7 days",
                    }
                },
                "conversion",
            ),
            "type.conversion",
            measures=[
                {"name": "c_conversion_base", "agg": "count_distinct", "expr": "store_id"},
                {"name": "c_conversion_done", "agg": "count_distinct", "expr": "store_id"},
            ],
        ),
        _legacy(
            _ratio("c_ratio_input_type", "c_ratio_input_type_part"),
            "ratio.input-type",
            metrics=[
                _metric(
                    "c_ratio_input_type_part",
                    {"measure": "revenue", "cumulative_type_params": cumulative},
                    "cumulative",
                )
            ],
        ),
        _legacy(
            _metric("c_ratio_numerator_missing", {"denominator": "revenue"}, "ratio"),
            "ratio.numerator-missing",
        ),
        _legacy(
            _metric("c_ratio_denominator_missing", {"numerator": "gross_margin_total"}, "ratio"),
            "ratio.denominator-missing",
        ),
        _legacy(
            _ratio("c_parity_ratio_average", "c_parity_average"),
            "parity.ratio-average",
            measures=[{"name": "c_average", "agg": "average", "expr": "revenue"}],
            metrics=[_simple("c_parity_average", "c_average")],
        ),
        _legacy(_simple("c_private", hidden=True), "private"),
    ]
    upper = _own_model(
        "c_formula_table",
        [_time(EVENT_TIME)],
        [_sum("c_formula_table_m")],
        default=EVENT_TIME,
        model="sales_upper",
    )
    entries.append(
        _legacy(
            _simple("c_formula_table", "c_formula_table_m"),
            "formula.table",
            semantic_models=[upper],
            models=[{"name": "sales_upper", "config": {"alias": "Sales_Daily"}}],
            sql={"sales_upper": _SALES_SQL},
            table=f"{SCHEMA}.Sales_Daily",
        )
    )
    other = _own_model(
        "c_relation_table",
        [_time(EVENT_TIME)],
        [_sum("c_relation_table_m")],
        default=EVENT_TIME,
        model="sales_other",
    )
    elsewhere = _own_model(
        "c_relation_database",
        [_time(EVENT_TIME)],
        [_sum("c_relation_database_m")],
        default=EVENT_TIME,
        model="sales_elsewhere",
    )
    entries += [
        # The grain table of the contract is `sales_daily`; the semantic model reads another.
        _legacy(
            _simple("c_relation_table", "c_relation_table_m"),
            "relation.table",
            semantic_models=[other],
            sql={"sales_other": _SALES_SQL},
        ),
        _legacy(
            _simple("c_relation_database", "c_relation_database_m"),
            "relation.database",
            semantic_models=[elsewhere],
            models=[{"name": "sales_elsewhere", "config": {"database": "y"}}],
            sql={"sales_elsewhere": _SALES_SQL},
            table=f"{SCHEMA}.sales_elsewhere",
        ),
        _measured(
            "c_additivity_legacy",
            {"name": "c_distinct_stores", "agg": "count_distinct", "expr": "store_id"},
            "additivity.aggregate-legacy",
            additivity="additive",
        ),
        _legacy(_ratio("c_additivity_ratio"), "additivity.ratio", additivity="additive"),
        # dbt computes `input_measures`; an author who writes them.
        _legacy(
            _simple(
                "c_legacy_input_measures", params={"input_measures": [{"name": "gross_margin"}]}
            ),
            "legacy.input-measures",
        ),
        _legacy(
            _ratio("c_ratio_input_measures", params={"input_measures": [{"name": "revenue"}]}),
            "ratio.input-measures",
        ),
    ]
    return entries


def _v2_entries() -> list[Entry]:
    percentile = ("v2.agg.percentile", "v2.agg_params.percentile", NO_GRANULARITY)
    entries = [
        _v2("c_v2_plain", NO_GRANULARITY, also=()),
        # Parity is asked of a formula with no other gap, and every V2 metric has one.
        _v2("c_parity_v2", "parity.v2"),
        _v2("c_v2_agg_sum_boolean", "v2.agg.sum_boolean", agg="sum_boolean", expr="is_promo"),
        _v2(
            "c_v2_agg_percentile",
            "v2.agg.percentile",
            also=("v2.agg_params.percentile", NO_GRANULARITY),
            agg="percentile",
            percentile=0.9,
        ),
        _v2("c_v2_agg_median", "v2.agg.median", agg="median"),
        _v2("c_v2_agg_params_percentile", "v2.agg_params.percentile", percentile=0.5),
        # dbt sets `use_discrete_percentile` for the type `discrete`, `use_approximate_percentile`
        # for `continuous`, and neither for any other.
        _v2(
            "c_v2_agg_params_use_discrete_percentile",
            "v2.agg_params.use_discrete_percentile",
            also=percentile,
            agg="percentile",
            percentile=0.9,
            percentile_type="discrete",
        ),
        _v2(
            "c_v2_agg_params_use_approximate_percentile",
            "v2.agg_params.use_approximate_percentile",
            also=percentile,
            agg="percentile",
            percentile=0.9,
            percentile_type="continuous",
        ),
        _v2(
            "c_v2_percentile_type_other",
            "v2.agg_params.use_approximate_percentile",
            also=percentile,
            agg="percentile",
            percentile=0.9,
            percentile_type="approximate_continuous",
        ),
        _v2(
            "c_additivity_v2",
            "additivity.aggregate-v2",
            additivity="additive",
            agg="count_distinct",
            expr="store_id",
        ),
        _v2("c_v2_metric_datatype", "metric.datatype", datatype="decimal"),
        _v2("c_v2_input_measures", "v2.input-measures", input_measures=[{"name": "revenue"}]),
        _v2(
            "c_v2_non_additive_dimension",
            "v2.non-additive-dimension",
            non_additive_dimension={"name": EVENT_TIME, "window_agg": "max"},
        ),
        _v2("c_v2_no_expr", "v2.no-expr", expr=None),
        _v2("c_v2_expr_0", "v2.expr", expr="revenue - gross_margin"),
        _v2("c_v2_expr_1", "v2.expr", expr=1),
        _v2("c_v2_private", "private", hidden=True),
        _v2("c_v2_join_to_timespine", "params.join_to_timespine", join_to_timespine=True),
        _v2("c_v2_fill_nulls_with_0", "params.fill_nulls_with", fill_nulls_with=0),
        _v2("c_v2_fill_nulls_with_1", "params.fill_nulls_with", fill_nulls_with=5),
        _v2("c_v2_metric_filter", "metric.filter", filter=FILTER),
        _v2("c_v2_metric_time_granularity", "metric.time_granularity", time_granularity="month"),
        _v2("c_v2_params_window", "params.window", window="7 days"),
        _v2("c_v2_params_grain_to_date", "params.grain_to_date", grain_to_date="month"),
        _v2("c_v2_simple_neither", "simple.both-or-neither", also=(), agg=None),
    ]
    entries.append(
        Entry(
            "c_v2_params_metric_aggregation_params",
            "v2",
            ("params.metric_aggregation_params",),
            (NO_GRANULARITY,),
            {
                "metrics": [
                    {
                        "name": "c_v2_params_metric_aggregation_params",
                        "type": "ratio",
                        "numerator": "gross_margin_total",
                        "denominator": "revenue",
                        "agg": "sum",
                        "expr": "revenue",
                    }
                ]
            },
        )
    )
    entries.append(
        Entry(
            "c_v2_time_none",
            "v2",
            ("time.none-v2",),
            (),
            {
                "models": [
                    {
                        "name": "sales_untimed",
                        "semantic_model": True,
                        "primary_entity": "untimed",
                        "columns": [
                            {"name": EVENT_TIME, "granularity": "day", "dimension": "time"}
                        ],
                        "metrics": [
                            {
                                "name": "c_v2_time_none",
                                "type": "simple",
                                "agg": "sum",
                                "expr": "revenue",
                            }
                        ],
                    }
                ],
                "sql": {"sales_untimed": _SALES_SQL},
            },
            f"{SCHEMA}.sales_untimed",
        )
    )
    entries.append(
        Entry(
            "c_v2_time_not_time",
            "v2",
            ("time.not-time",),
            (NO_GRANULARITY,),
            {
                "models": [
                    {
                        "name": "sales_kind",
                        "semantic_model": True,
                        "primary_entity": "kind",
                        "agg_time_dimension": "region",
                        "columns": [
                            {"name": EVENT_TIME, "granularity": "day", "dimension": "time"},
                            {"name": "region", "dimension": "categorical"},
                        ],
                        "metrics": [
                            {
                                "name": "c_v2_time_not_time",
                                "type": "simple",
                                "agg": "sum",
                                "expr": "revenue",
                            }
                        ],
                    }
                ],
                "sql": {"sales_kind": _SALES_SQL},
            },
            f"{SCHEMA}.sales_kind",
        )
    )
    return [_without_none(entry) for entry in entries]


def _without_none(entry: Entry) -> Entry:
    """An entry whose V2 metrics leave out a key set to None: the author did not write it."""
    metrics = [
        {key: value for key, value in metric.items() if value is not None}
        for metric in entry.add.get("metrics", [])
    ]
    models = [
        {
            **model,
            "metrics": [
                {k: v for k, v in metric.items() if v is not None}
                for metric in model.get("metrics", [])
            ],
        }
        for model in entry.add.get("models", [])
    ]
    add = {**entry.add}
    if metrics:
        add["metrics"] = metrics
    if models:
        add["models"] = models
    return replace(entry, add=add)


ENTRIES: tuple[Entry, ...] = (*_legacy_entries(), *_v2_entries())


def recipe(accepted: Mapping[str, Sequence[Entry]]) -> str:
    """The digest of what a run gives dbt and the resolver: each project dbt parses — the base
    of each dialect, each entry alone, the entries of `accepted` together — what each entry is
    to give, the contract and the declared table. The resolver and the parity it takes as
    proven are not in it: the tests resolve each metric again."""
    return sha256_digest(
        {
            "bases": {dialect: project_files(dialect, []) for dialect in DIALECTS},
            "entries": [
                {
                    "metric": entry.metric,
                    "dialect": entry.dialect,
                    "aims": list(entry.aims),
                    "also": list(entry.also),
                    "table": entry.table,
                    "additivity": entry.additivity,
                    "files": project_files(entry.dialect, [entry]),
                }
                for entry in ENTRIES
            ],
            "corpus": {dialect: project_files(dialect, accepted[dialect]) for dialect in DIALECTS},
            "declared": {"engine": DECLARED.engine, "column_types": dict(DECLARED.column_types)},
            "contract": _contract("metric", "pin", TABLE, "non_additive"),
        }
    )


# --- the run -----------------------------------------------------------------------------


def _contract(name: str, pin: str, table: str, additivity: str) -> dict[str, Any]:
    return {
        "metadata": {
            "id": f"metric://corpus/{name}",
            "version": 1,
            "owner": "corpus",
            "status": "active",
        },
        "formula": {
            "kind": "external_ref",
            "external": {"source": "dbt_semantic_manifest", "ref": name, "source_revision": pin},
        },
        "grain": {"table": table, "keys": list(KEYS)},
        "additivity": additivity,
        "time": {"event_time_column": EVENT_TIME, "timezone": "UTC"},
        "unit": {
            "unit": "value",
            "precision": 2,
            "null_semantics": "null_is_missing",
            "zero_semantics": "zero_is_value",
        },
        "freshness": {"max_age": "PT6H"},
        "classification": "internal",
    }


def outcome(
    read: ManifestRead, metric: str, table: str, additivity: str = "non_additive"
) -> dict[str, Any]:
    """What the resolver answers for `metric` of the artifact `read`: its formula or its gaps."""
    assert read.manifest is not None, read.problems
    pin = read.manifest.closure_digest(metric)
    contract = MetricContract.model_validate(_contract(metric, pin, table, additivity))
    resolution = resolve_formula(
        contract, read, database=DATABASE, declared=DECLARED, proven=PROVEN_PARITY
    )
    if resolution.formula is not None:
        return {"formula": resolution.formula.model_dump(mode="json")}
    return {
        "gaps": [
            {
                "code": gap.code,
                "severity": gap.severity.value,
                "path": gap.path,
                "reason": gap.reason,
            }
            for gap in resolution.gaps
        ]
    }


_TIMESTAMP = re.compile(r"^\d\d:\d\d:\d\d\s+")
#: When dbt made a node: a refused node's repr carries it.
_CREATED = re.compile(r"created_at=[0-9.]+")
_FILE = re.compile(r"FilePath\(searched_path='([^']*)', relative_path='([^']*)'[^)]*\)")
_YAML = re.compile(r"( @ \w+: )\{.*\}( - at path)")
_LONG = 300


def _environment(home: Path) -> dict[str, str]:
    """What a dbt run sees of the caller: its PATH. No profile, no tracking."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "LANG": "en_US.UTF-8",
        "PYTHONUTF8": "1",
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DO_NOT_TRACK": "1",
    }


def _excerpt(output: str, where: Path) -> str:
    """dbt's reasons, from its first warning or error: no times, no file stamps, long lines cut."""
    lines = []
    for raw in output.splitlines():
        line = _TIMESTAMP.sub("", raw).rstrip().replace(str(where), "<project>")
        line = _CREATED.sub("created_at=…", line)
        line = _YAML.sub(r"\1{…}\2", _FILE.sub(r"\1/\2", line))
        if len(line) > _LONG:
            line = f"{line[: _LONG // 2]} … {line[-_LONG // 2 :]}"
        if line.strip():
            lines.append(line)
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(("[WARNING]", "[ERROR]"))), 0
    )
    return "\n".join(lines[start : start + 12])


def _dbt(dbt: str, args: Sequence[str], home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [dbt, "--no-version-check", "--no-use-colors", "--log-format", "text", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_environment(home),
        cwd=home,
        check=False,
    )


def _parse(dbt: str, files: dict[str, str], where: Path) -> tuple[bytes | None, str]:
    """`dbt parse` of the project `files` written under `where`: the artifact, or dbt's error."""
    for name, text in files.items():
        path = where / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    done = _dbt(
        dbt,
        ["parse", "--project-dir", str(where), "--profiles-dir", str(where), "--no-partial-parse"],
        where,
    )
    artifact = where / "target" / "semantic_manifest.json"
    if done.returncode == 0 and artifact.is_file():
        return artifact.read_bytes(), ""
    return None, _excerpt(done.stdout + done.stderr, where)


def _versions(dbt: str) -> dict[str, str]:
    """dbt-core, its adapter, and metricflow, whose semantic interfaces dbt-core 1.12 uses."""
    python = Path(dbt).with_name("python")
    code = (
        "import importlib.metadata as m, json\n"
        "print(json.dumps({d.metadata['Name'].lower(): d.version for d in m.distributions()"
        " if d.metadata['Name'].lower() in ('dbt-core', 'dbt-postgres', 'metricflow')}))"
    )
    done = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=True)
    versions: dict[str, str] = json.loads(done.stdout)
    return dict(sorted(versions.items()))


def _read(raw: bytes, what: str) -> ManifestRead:
    read = read_semantic_manifest(raw)
    if read.manifest is None:
        raise SystemExit(f"the reader refuses {what}: {read.problems}")
    return read


def _folded(found: dict[str, Any]) -> str:
    """An outcome without the indexes of its paths, to compare it across artifacts."""
    return re.sub(r"\[\d+\]", "[]", json.dumps(found, sort_keys=True))


def run(dbt: str, out: Path) -> int:
    results: dict[str, dict[str, Any]] = {}
    #: Each accepted entry and each control as its own project resolves it, folded.
    alone: dict[str, str] = {}
    artifacts: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for dialect in DIALECTS:
            raw, error = _parse(dbt, project_files(dialect, []), work / f"base_{dialect}")
            if raw is None:
                raise SystemExit(f"dbt refuses the base project of {dialect}:\n{error}")
            base = _read(raw, f"the base project of {dialect}")
            for name in CONTROLS:
                alone[f"{dialect}:{name}"] = _folded(outcome(base, name, TABLE))
        accepted: dict[str, list[Entry]] = {dialect: [] for dialect in DIALECTS}
        for entry in ENTRIES:
            raw, error = _parse(dbt, project_files(entry.dialect, [entry]), work / entry.metric)
            if raw is None:
                results[entry.metric] = {"refused": error}
            else:
                accepted[entry.dialect].append(entry)
                own = _read(raw, f"the project of {entry.metric}")
                found = outcome(own, entry.metric, entry.table, entry.additivity)
                alone[entry.metric] = _folded(found)
            print(entry.metric, "refused" if raw is None else "accepted", flush=True)
        corpus: dict[str, bytes] = {}
        for dialect in DIALECTS:
            raw, error = _parse(dbt, project_files(dialect, accepted[dialect]), work / dialect)
            if raw is None:
                raise SystemExit(f"dbt refuses the corpus of {dialect}:\n{error}")
            corpus[dialect] = raw
            read = _read(raw, f"the corpus of {dialect}")
            for name in CONTROLS:
                results[f"{dialect}:{name}"] = outcome(read, name, TABLE)
            for entry in accepted[dialect]:
                results[entry.metric] = outcome(read, entry.metric, entry.table, entry.additivity)
    # The corpus answers for each metric what its own project does: no entry, and no flag an
    # entry sets, changes how another resolves.
    moved = sorted(name for name, found in alone.items() if _folded(results[name]) != found)
    if moved:
        raise SystemExit(f"the corpus resolves otherwise than alone: {', '.join(moved)}")
    for dialect, raw in corpus.items():
        target = out / dialect / "semantic_manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        artifacts[dialect] = digest_bytes(raw)
    record = {
        "versions": _versions(dbt),
        "recipe": recipe(accepted),
        "artifacts": artifacts,
        "outcomes": results,
    }
    (out / "result.json").write_text(
        json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    running = commands.add_parser("run", help="parse each entry, then the corpus; record")
    running.add_argument("--dbt", required=True)
    running.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.dbt, args.out)


if __name__ == "__main__":
    sys.exit(main())
