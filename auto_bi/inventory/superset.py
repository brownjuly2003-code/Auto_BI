"""The delivered dashboards of a Superset, read into the gate's `DashboardSpec` (ADR 0012, D6).

`decision verify --dashboards` reads a spec per delivered ref, and until D6 an operator wrote
each one by hand. This module reads what Superset 4.1 holds — the dashboard, its charts and
their datasets, by `GET` only — and writes the spec the gate reads. It is a *read model*: what
the spec must get right is what each tile reads and computes, because that is all the gate asks
of it (chart impact, `bi.contract`). Two forms, and no third:

* **typed** — the chart is one the IR says exactly: a viz of the closed set, a physical dataset,
  aggregates `AGG(column)` over plain columns the dataset answer describes, plain dimensions,
  the time grain Superset groups them by, filters the IR has an operator for, no
  post-processing, every `form_data` key that holds a value one this module knows, and the spec
  validates against the semantic model. Only this form can be named `computes_metric`.
* **read SQL** — every other chart: operator `raw_sql` selecting `*` from the chart's source
  (the table, or the dataset's own SQL as `virtual_table`, as Superset wraps it) plus every SQL
  fragment the chart and its dataset answer carry. It is not what Superset runs; it reads at
  least what Superset reads from the source, and the gate names such a tile read in part
  (`chart.sql_partial`) rather than resolved. A typed spec that does not validate against the
  model, or that the Superset contract compiler refuses, is read SQL too: a spec the gate's
  `bi.contract` refuses would block every run for a reason no change made.

A tile is read as the dashboard opens: what a viewer picks after that — a native filter's
value, a cross-filter, a drill, an area chart's extra controls — is not read.

A dashboard is written whole or not at all: a spec missing a tile would read as the dashboard
without it. What keeps one from being written is named, and the gate then says the spec is
missing (`bi.spec_missing`) instead of answering for tiles nobody read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote

import httpx
import sqlglot
import yaml
from pydantic import Field, ValidationError
from sqlglot import expressions as exp
from sqlglot.errors import ParseError, SqlglotError

from auto_bi.adapters.superset.client import SupersetAPIError, SupersetClient
from auto_bi.agent.sql_guard import SQLGuardError, guard_sql
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    FilterOp,
    Measure,
    QueryFilter,
    TargetBI,
    TimeGrain,
    Viz,
)
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import Aggregation, SemanticModel
from auto_bi.strict import StrictModel
from auto_bi.verification.bi_contract import SupersetContractCompiler

#: The Superset the mapping below was read off (docker/superset/Dockerfile, CLAUDE.md inv. 7).
SUPERSET_VERSION = "4.1.2"

#: `DashboardSpec.charts` holds at most this many tiles.
MAX_CHARTS = 12

#: Dialect of the read SQL: chart impact reads operator SQL as ClickHouse (`decision/impact.py`).
READ_SQL_DIALECT = "clickhouse"


class SupersetSource(Protocol):
    """What the inventory asks of a Superset. `GET` answers, nothing else."""

    def dashboard(self, ident: str) -> Mapping[str, Any]: ...

    def charts(self, ident: str) -> Sequence[Mapping[str, Any]]: ...

    def datasets(self, ident: str) -> Sequence[Mapping[str, Any]]: ...


class ChartForm(StrEnum):
    TYPED = "typed"
    READ_SQL = "read_sql"


class InventoriedChart(StrictModel):
    #: Id of the tile in the spec: `chart_<superset id>`.
    chart: str = Field(min_length=1)
    superset_id: int
    viz_type: str
    form: ChartForm
    #: Why the chart is not typed; empty for a typed chart.
    reasons: list[str] = Field(default_factory=list)
    #: The Superset database of the chart's dataset — its id and engine, as Superset names
    #: them; `None` and empty when the answer names none.
    database: int | None = None
    backend: str = ""


class InventoriedDashboard(StrictModel):
    ref: str = Field(min_length=1)
    #: The id or slug the operator named.
    superset: str = Field(min_length=1)
    superset_id: int | None = None
    title: str = ""
    changed_on: str = ""
    #: Path of the written spec, relative to the output directory; `None` when not written.
    written: str | None = None
    charts: list[InventoriedChart] = Field(default_factory=list)
    #: Why the spec was not written; empty when it was.
    refusals: list[str] = Field(default_factory=list)


class SupersetAnswerError(ValueError):
    """An answer of Superset that is not the shape 4.1 gives where the inventory depends on it
    — said, never guessed around. What it only names or records (a title, a schema, a
    database) is read leniently: missing or not text, it is empty."""


class SupersetSourceError(Exception):
    """Superset gave no answer the inventory can read: it was not reached, refused the login or
    a request, or answered with what is not its JSON.

    The text names the request, with the HTTP status or the transport error's class, never a
    body or a credential.
    """


class RestSupersetSource:
    """A `SupersetSource` over the REST API of a running Superset.

    Content is read by `GET` only; the other exchange is the client's login
    (`POST /api/v1/security/login`, then the `GET` of a CSRF token), made before the first
    request and again after an answer 401. A dashboard is named by its id or its slug, as the
    API takes either.
    """

    def __init__(self, client: SupersetClient) -> None:
        self._client = client

    def dashboard(self, ident: str) -> Mapping[str, Any]:
        result = self._result(f"/api/v1/dashboard/{quote(ident, safe='')}")
        if not isinstance(result, Mapping):
            raise SupersetAnswerError(f"dashboard {ident!r}: the answer holds no object")
        return result

    def charts(self, ident: str) -> Sequence[Mapping[str, Any]]:
        return self._objects(f"/api/v1/dashboard/{quote(ident, safe='')}/charts", ident)

    def datasets(self, ident: str) -> Sequence[Mapping[str, Any]]:
        return self._objects(f"/api/v1/dashboard/{quote(ident, safe='')}/datasets", ident)

    def _objects(self, path: str, ident: str) -> Sequence[Mapping[str, Any]]:
        result = self._result(path)
        if not isinstance(result, list) or not all(isinstance(item, Mapping) for item in result):
            raise SupersetAnswerError(f"dashboard {ident!r}: {path} holds no list of objects")
        return result

    def _result(self, path: str) -> Any:
        try:
            answer: Any = self._client.get(path)
        except SupersetAPIError as error:
            raise SupersetSourceError(str(error)) from None
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            raise SupersetSourceError(f"GET {path}: {type(error).__name__}") from None
        except (KeyError, TypeError, ValueError, RecursionError):
            # A login or an answer that is not the JSON Superset sends, or JSON nested deeper
            # than Python decodes.
            raise SupersetSourceError(f"GET {path}: the answer is not Superset's JSON") from None
        if not isinstance(answer, Mapping):
            raise SupersetSourceError(f"GET {path}: the answer is not Superset's JSON")
        return answer.get("result")


@dataclass(frozen=True)
class DashboardRead:
    spec: DashboardSpec | None
    record: InventoriedDashboard


# --- what Superset's form_data says, key by key ---------------------------------------------

#: Keys whose value says what the chart reads or computes; each is interpreted below.
_INTERPRETED = frozenset(
    {
        "adhoc_filters",
        "all_columns",
        "granularity_sqla",
        "groupby",
        "groupbyColumns",
        "groupbyRows",
        "metric",
        "metrics",
        "query_mode",
        "row_limit",
        "stack",
        "temporal_columns_lookup",
        "time_grain_sqla",
        "time_range",
        "x_axis",
    }
)

#: Keys that name the chart and its links, not its query.
_IDENTITY = frozenset({"dashboards", "datasource", "slice_id", "url_params", "viz_type"})

#: Keys of the closed viz set that change how the result is drawn or ordered, never what a row
#: of it says. Four of them (`order_desc`, `sort_by_metric`, a heatmap's `sort_x_axis` and
#: `sort_y_axis`) order the rows a row limit keeps: a tile over its limit shows the rows they
#: choose, each still the measure the IR names. `show_extra_controls` gives an area chart's
#: viewer a choice of stacking; the tile opens as `stack` says. A key that holds a value and is
#: in none of these three sets and `_NO_POST_PROCESSING` keeps a chart from being typed.
_DISPLAY = frozenset(
    {
        "allow_render_html",
        "bottom_margin",
        "colOrder",
        "color_pn",
        "color_scheme",
        "compare_lag",
        "compare_suffix",
        "date_format",
        "donut",
        "forecastInterval",
        "forecastPeriods",
        "header_font_size",
        "include_search",
        "innerRadius",
        "label_colors",
        "label_line",
        "label_type",
        "labels_outside",
        "left_margin",
        "legendOrientation",
        "legendType",
        "legend_type",
        "linear_color_scheme",
        "markerSize",
        "markup_type",
        "metricsLayout",
        "normalize_across",
        "normalized",
        "number_format",
        "only_total",
        "opacity",
        "order_desc",
        "orientation",
        "outerRadius",
        "page_length",
        "queryFields",
        "rich_tooltip",
        "rowOrder",
        "seriesType",
        "server_page_length",
        "show_cell_bars",
        "show_empty_columns",
        "show_extra_controls",
        "show_labels",
        "show_labels_threshold",
        "show_legend",
        "show_percentage",
        "show_values",
        "sort_by_metric",
        "sort_series_type",
        "sort_x_axis",
        "sort_y_axis",
        "subheader",
        "subheader_font_size",
        "table_timestamp_format",
        "time_format",
        "tooltipTimeFormat",
        "truncateXAxis",
        "truncateYAxis",
        "truncate_metric",
        "valueFormat",
        "value_bounds",
        "xAxisLabelRotation",
        "x_axis_sort_asc",
        "x_axis_sort_series",
        "x_axis_sort_series_ascending",
        "x_axis_time_format",
        "x_axis_title",
        "x_axis_title_margin",
        "xscale_interval",
        "y_axis_bounds",
        "y_axis_format",
        "y_axis_title",
        "y_axis_title_margin",
        "y_axis_title_position",
        "yscale_interval",
    }
)

#: Post-processing keys and the values at which Superset applies none. Any other value is a
#: number the IR measure would not say (a rolling mean, a share, a shifted period); a list or
#: an object is one too, except an empty `time_compare` (see `_post_processes`).
_NO_POST_PROCESSING: dict[str, frozenset[Any]] = {
    # pivot_table_v2 sums the records of a cell with it (a missing key is "Sum"); null or ""
    # fail the tile.
    "aggregateFunction": frozenset({"Sum"}),
    "comparison_type": frozenset({None, "", False, "values"}),
    # Superset tests it for truth: the text "null" is a share too.
    "contributionMode": frozenset({None, "", False}),
    "forecastEnabled": frozenset({None, "", False}),
    "resample_method": frozenset({None, "", False}),
    "resample_rule": frozenset({None, "", False}),
    "rolling_type": frozenset({None, "", False, "None", "null"}),
    "time_compare": frozenset({None}),
}

_VIZ = {
    "big_number_total": Viz.BIG_NUMBER,
    "echarts_area": Viz.AREA,
    "echarts_timeseries_bar": Viz.BAR,
    "echarts_timeseries_line": Viz.LINE,
    "heatmap_v2": Viz.HEATMAP,
    "pie": Viz.PIE,
    "pivot_table_v2": Viz.PIVOT,
    "table": Viz.TABLE,
}

_AGGREGATE = {
    "SUM": Aggregation.SUM,
    "AVG": Aggregation.AVG,
    "MIN": Aggregation.MIN,
    "MAX": Aggregation.MAX,
    "COUNT": Aggregation.COUNT,
    "COUNT_DISTINCT": Aggregation.COUNT_DISTINCT,
}

_SAVED_AGGREGATE: dict[type[exp.Expr], Aggregation] = {
    exp.Sum: Aggregation.SUM,
    exp.Avg: Aggregation.AVG,
    exp.Min: Aggregation.MIN,
    exp.Max: Aggregation.MAX,
    exp.Count: Aggregation.COUNT,
}

_OPERATOR = {
    "==": FilterOp.EQ,
    "!=": FilterOp.NEQ,
    "IN": FilterOp.IN,
    ">=": FilterOp.GTE,
    "<=": FilterOp.LTE,
}

_GRAIN = {
    "P1D": TimeGrain.DAY,
    "P1W": TimeGrain.WEEK,
    "P1M": TimeGrain.MONTH,
    "P3M": TimeGrain.QUARTER,
    "P1Y": TimeGrain.YEAR,
}

_NO_TIME_RANGE = frozenset({None, "", "No filter"})

#: Stackings that draw each value as it is: none, a stack, a stream (a baseline under one).
#: "Expand" draws a value's share of its stack instead.
_STACKS = frozenset({None, "", False, True, "Stack", "Stream"})

#: The row limit of a chart whose form_data has no `row_limit`: a dashboard fills a missing key
#: with the default of the viz's control (`applyDefaultFormData`).
_VIZ_ROW_LIMIT = {
    Viz.PIE: 100,
    Viz.TABLE: 1000,
    Viz.LINE: 10_000,
    Viz.BAR: 10_000,
    Viz.AREA: 10_000,
    Viz.HEATMAP: 10_000,
    Viz.PIVOT: 10_000,
}
#: What Superset 4.1.2 applies where the query carries no limit — a `row_limit` that is null or
#: "", a viz without the control (`big_number_total`): its `ROW_LIMIT` config default.
_SUPERSET_ROW_LIMIT = 50_000
_MAX_IR_LIMIT = 50_000

#: The measures a pivot cell still says when it sums the records it folds into one (a NULL and
#: an empty text share a cell: the pivot keys a cell by its values joined as text).
_ADDITIVE = frozenset({Aggregation.SUM, Aggregation.COUNT})

_SQL_KEYS = frozenset({"sqlExpression", "where", "having"})


def inventory_dashboard(
    ref: str,
    ident: str,
    source: SupersetSource,
    model: SemanticModel,
    *,
    database: int | None = None,
) -> DashboardRead:
    """The spec of one Superset dashboard for *ref*, or why there is none.

    With *database*, a chart whose dataset is on another Superset database refuses the
    dashboard: the model names tables of one database, and a table of the same name on
    another is not one of them.

    Raises `SupersetAnswerError` when an answer is not the shape Superset 4.1 gives: the
    inventory does not guess what a chart reads from an answer it cannot read. A value nested
    past Python's recursion limit is one — no form_data Superset writes is that deep.
    """
    try:
        return _read_dashboard(ref, ident, source, model, database)
    except RecursionError:
        raise SupersetAnswerError("an answer is nested deeper than the inventory reads") from None


def _read_dashboard(
    ref: str, ident: str, source: SupersetSource, model: SemanticModel, database: int | None
) -> DashboardRead:
    dashboard = source.dashboard(ident)
    charts = sorted(source.charts(ident), key=lambda chart: _int(chart, "id"))
    datasets = {_int(dataset, "id"): dataset for dataset in source.datasets(ident)}
    record = InventoriedDashboard(
        ref=ref,
        superset=ident,
        superset_id=_int(dashboard, "id"),
        title=_text(dashboard, "dashboard_title"),
        changed_on=str(dashboard.get("changed_on") or ""),
    )
    native_filters = _native_filters(dashboard)
    refusals: list[str] = []
    specs: list[ChartSpec] = []
    if not charts:
        refusals.append("the dashboard holds no chart")
    if len(charts) > MAX_CHARTS:
        refusals.append(f"the dashboard holds {len(charts)} charts; a spec holds {MAX_CHARTS}")
    for chart in charts:
        chart_id = f"chart_{_int(chart, 'id')}"
        form_data = chart.get("form_data")
        if not isinstance(form_data, Mapping):
            raise SupersetAnswerError(f"{chart_id}: form_data is not an object")
        viz_type = str(form_data.get("viz_type") or "")
        dataset_id = _dataset_id(form_data)
        dataset = None if dataset_id is None else datasets.get(dataset_id)
        if dataset is None:
            refusals.append(
                f"{chart_id}: its dataset {form_data.get('datasource')!r} is not among the "
                "datasets of the dashboard"
            )
            continue
        on, backend = _database(dataset)
        if database is not None and on != database:
            where = (
                "names no Superset database"
                if on is None
                else f"is on Superset database {on} ({backend or 'engine not named'})"
            )
            refusals.append(f"{chart_id}: its dataset {where}, not on {database}")
            continue
        title = _text(chart, "slice_name") or chart_id
        filtered = [
            f"the dashboard's native filter {native.name!r} applies to it"
            for native in native_filters
            if native.applies_to(_int(chart, "id"))
        ]
        if filtered:
            typed, reasons = None, filtered
        else:
            typed, reasons = _typed_chart(chart_id, title, form_data, dataset, model)
        if typed is not None:
            specs.append(typed)
            record.charts.append(
                InventoriedChart(
                    chart=chart_id,
                    superset_id=_int(chart, "id"),
                    viz_type=viz_type,
                    form=ChartForm.TYPED,
                    database=on,
                    backend=backend,
                )
            )
            continue
        read, refusal = _read_sql_chart(chart_id, title, form_data, dataset)
        if read is None:
            refusals.append(f"{chart_id}: {refusal}")
            continue
        specs.append(read)
        record.charts.append(
            InventoriedChart(
                chart=chart_id,
                superset_id=_int(chart, "id"),
                viz_type=viz_type,
                form=ChartForm.READ_SQL,
                reasons=reasons,
                database=on,
                backend=backend,
            )
        )
    if refusals:
        record.refusals = refusals
        return DashboardRead(spec=None, record=record)
    spec = DashboardSpec(title=record.title or ref, target_bi=TargetBI.SUPERSET, charts=specs)
    errors = gate_refusals(spec, model)
    if errors:
        # Each typed chart passed on its own; what is left is the dashboard's.
        record.refusals = errors
        return DashboardRead(spec=None, record=record)
    return DashboardRead(spec=spec, record=record)


def gate_refusals(spec: DashboardSpec, model: SemanticModel) -> list[str]:
    """What the gate's `bi.contract` would refuse in *spec*: it validates, then compiles.

    A written spec the gate refuses blocks every run for a reason no change made, so the
    inventory asks both questions the check asks before it writes one.
    """
    errors = validate_spec(spec, model)
    if errors:
        return [f"the spec does not validate: {error}" for error in errors]
    try:
        SupersetContractCompiler().compile_contract(spec, model)
    except ValueError as error:
        return [f"the spec does not compile: {_plain(str(error))}"]
    return []


class _SpecDumper(yaml.SafeDumper):
    """Text of several lines as a literal block, where YAML allows one: SQL stays readable.

    Text holding U+0085 (NEL) is double-quoted instead: PyYAML writes that character as it
    stands into a block or a single-quoted text and does not read it back as itself, so the
    file would say another SQL than the one checked. Double-quoted, it is escaped.
    """


def _represent_text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style: str | None = None
    if "\x85" in value:
        style = '"'
    elif "\n" in value:
        style = "|"
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_SpecDumper.add_representer(str, _represent_text)


def dump_spec(spec: DashboardSpec) -> bytes:
    """The spec file: YAML in UTF-8 with LF line ends, without the fields left at defaults."""
    return yaml.dump(
        spec.model_dump(mode="json", exclude_defaults=True),
        Dumper=_SpecDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        line_break="\n",
    ).encode("utf-8")


# --- native filters ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _NativeFilter:
    name: str
    #: Charts the filter's scope keeps out of it.
    excluded: frozenset[int]
    #: Charts Superset listed as in its scope when the filter was saved.
    listed: frozenset[int]

    def applies_to(self, chart: int) -> bool:
        """In scope unless kept out of it. The tabs a scope names are not read, so a filter
        scoped to some tabs is taken to apply on every other tab as well."""
        return chart in self.listed or chart not in self.excluded


def _native_filters(dashboard: Mapping[str, Any]) -> list[_NativeFilter]:
    """The native filters of a dashboard, from its `json_metadata`.

    A filter with a default adds it to the query of every chart in its scope as the dashboard
    opens — a value saved with it, or the first value it lists. Whether a filter has one is
    not read here, so a chart in the scope of any native filter is not typed: its numbers may
    not be what its own form_data says. Its read SQL still holds what a filter reads —
    `SELECT *` reads every column a filter can name. What a viewer picks later, in a native
    filter or a cross-filter, is not read at all (the module docstring).
    """
    raw = dashboard.get("json_metadata")
    if raw is None or raw == "":
        return []
    if not isinstance(raw, str):
        raise SupersetAnswerError("the dashboard's json_metadata is not text")
    try:
        metadata = json.loads(raw)
    except ValueError:
        raise SupersetAnswerError("the dashboard's json_metadata is not JSON") from None
    if not isinstance(metadata, Mapping):
        raise SupersetAnswerError("the dashboard's json_metadata is not an object")
    filters: list[_NativeFilter] = []
    for item in _list(metadata, "native_filter_configuration"):
        if item.get("type") == "DIVIDER":
            continue
        scope = item.get("scope")
        filters.append(
            _NativeFilter(
                name=_text(item, "name") or _text(item, "id") or "(unnamed)",
                excluded=_ids(scope.get("excluded") if isinstance(scope, Mapping) else None),
                listed=_ids(item.get("chartsInScope")),
            )
        )
    return filters


def _ids(value: Any) -> frozenset[int]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, int) and not isinstance(item, bool))


# --- typed ------------------------------------------------------------------------------------


class _NotTyped(Exception):
    """One reason a chart is not typed."""


def _typed_chart(
    chart_id: str,
    title: str,
    form_data: Mapping[str, Any],
    dataset: Mapping[str, Any],
    model: SemanticModel,
) -> tuple[ChartSpec | None, list[str]]:
    """The typed spec of a chart, or the reasons it has none."""
    try:
        chart = _typed(chart_id, title, form_data, dataset)
    except _NotTyped as reason:
        return None, [str(reason)]
    except ValidationError as error:
        return None, [f"the IR does not take it: {error.errors()[0]['msg']}"]
    errors = gate_refusals(DashboardSpec(title=title, charts=[chart]), model)
    if errors:
        return None, errors
    return chart, []


def _typed(
    chart_id: str, title: str, form_data: Mapping[str, Any], dataset: Mapping[str, Any]
) -> ChartSpec:
    viz_type = form_data.get("viz_type")
    viz = _VIZ.get(viz_type) if isinstance(viz_type, str) else None
    if viz is None:
        raise _NotTyped(f"viz_type {viz_type!r} is outside the typed set")
    default_limit = _VIZ_ROW_LIMIT.get(viz, _SUPERSET_ROW_LIMIT)
    if _has_sql(form_data):
        raise _NotTyped("form_data carries a SQL expression")
    for key in sorted(form_data):
        value = form_data[key]
        if key in _NO_POST_PROCESSING:
            if _post_processes(key, value):
                raise _NotTyped(f"form_data.{key} = {value!r} post-processes the result")
        elif key not in _INTERPRETED | _IDENTITY | _DISPLAY and not _empty(value):
            # A key is the answer's own text: one that is no name is quoted, escapes and all.
            where = f"form_data.{key}" if key.isidentifier() else f"form_data[{key!r}]"
            raise _NotTyped(f"{where} is not a key the typed form reads")
    if dataset.get("sql"):
        raise _NotTyped("the dataset is virtual (its own SQL)")
    table = _table(dataset)
    if table is None:
        raise _NotTyped("the dataset names no schema")
    # Superset 4.1.2 trims the datasets of a dashboard to the columns some form_data keys name
    # (`data_for_slices`): a column it leaves out is one whose expression, type and time flag
    # are not known here, though Superset reads them all when it runs the chart.
    described = {str(column.get("column_name")): column for column in _list(dataset, "columns")}
    calculated = {name for name, column in described.items() if column.get("expression")}
    temporal = {name for name, column in described.items() if column.get("is_dttm") is True}
    types = {name: str(column.get("type") or "") for name, column in described.items()}
    saved = {str(metric.get("metric_name")): metric for metric in _list(dataset, "metrics")}

    def column(value: Any, where: str) -> str:
        if not isinstance(value, str) or not value:
            raise _NotTyped(f"form_data.{where} is not a plain column name")
        if value not in described:
            raise _NotTyped(
                f"form_data.{where} names {value!r}, a column the dataset answer does not describe"
            )
        if value in calculated:
            raise _NotTyped(f"form_data.{where} is the calculated column {value!r}")
        if not isinstance(described[value].get("is_dttm"), bool):
            raise _NotTyped(
                f"form_data.{where}: the dataset answer does not say whether {value!r} is temporal"
            )
        return value

    def columns(key: str) -> list[str]:
        value = form_data.get(key)
        if _empty(value):
            return []
        values = value if isinstance(value, list) else [value]
        return [column(item, f"{key}[{index}]") for index, item in enumerate(values)]

    def measure(value: Any, where: str) -> Measure:
        if isinstance(value, str):
            metric = saved.get(value)
            if metric is None:
                raise _NotTyped(f"form_data.{where} names no metric of the dataset: {value!r}")
            return _saved_measure(value, str(metric.get("expression") or ""), where, calculated)
        if not isinstance(value, Mapping) or value.get("expressionType") != "SIMPLE":
            raise _NotTyped(f"form_data.{where} is not a simple aggregate")
        aggregate = _AGGREGATE.get(str(value.get("aggregate")))
        if aggregate is None:
            raise _NotTyped(f"form_data.{where} aggregates with {value.get('aggregate')!r}")
        target = value.get("column")
        name = target.get("column_name") if isinstance(target, Mapping) else None
        return Measure(column=column(name, f"{where}.column"), agg=aggregate)

    if form_data.get("query_mode") == "raw" or not _empty(form_data.get("all_columns")):
        raise _NotTyped("the table lists raw rows, not aggregates")
    for key in ("metrics", "adhoc_filters"):
        if not _empty(form_data.get(key)) and not isinstance(form_data.get(key), list):
            raise _NotTyped(f"form_data.{key} is not a list")
    metrics = form_data.get("metrics")
    named = [
        (f"metrics[{index}]", item)
        for index, item in enumerate(metrics if isinstance(metrics, list) else [])
    ]
    if not _empty(form_data.get("metric")):
        named.append(("metric", form_data.get("metric")))
    measures = [measure(item, where) for where, item in named]
    if not measures:
        raise _NotTyped("the chart aggregates nothing")
    labels = [_metric_label(item) for _, item in named]
    for index, label in enumerate(labels):
        if label in labels[:index]:
            raise _NotTyped(
                f"form_data.{named[index][0]} has the label {label!r} of another metric: "
                "Superset computes the first of them only"
            )
    if viz is Viz.PIVOT and any(item.agg not in _ADDITIVE for item in measures):
        raise _NotTyped(
            "pivot_table_v2 sums the records it folds into one cell; only SUM and COUNT stay "
            "the measure"
        )
    filters, time_ranges = _filters(form_data, column, types, temporal)
    if _hashable(form_data.get("time_range")) not in _NO_TIME_RANGE:
        raise _NotTyped(f"form_data.time_range = {form_data.get('time_range')!r} filters time")
    stack = form_data.get("stack")
    if stack == "Expand":
        raise _NotTyped("form_data.stack = 'Expand' draws each value as its share of the stack")
    if _hashable(stack) not in _STACKS:
        raise _NotTyped(f"form_data.stack = {stack!r} is not a stacking the typed form reads")
    x_axis = columns("x_axis")
    groupby = columns("groupby")
    rows, cols = columns("groupbyRows"), columns("groupbyColumns")
    dimensions: list[str] = []
    series: list[str] = []
    if viz in (Viz.LINE, Viz.BAR, Viz.AREA, Viz.HEATMAP):
        if not x_axis:
            # Superset groups such a chart by its legacy time column `__timestamp` instead.
            raise _NotTyped(f"{viz_type} without form_data.x_axis is not read by the typed form")
        dimensions = x_axis
        if viz is Viz.HEATMAP:
            dimensions = [*x_axis, *groupby]
        elif groupby:
            if viz is Viz.LINE or (viz is Viz.BAR and not form_data.get("stack")):
                raise _NotTyped(f"{viz_type} with groupby draws series the IR has no form for")
            if viz is Viz.BAR:
                viz = Viz.STACKED_BAR
            series = groupby
    else:
        if x_axis:
            raise _NotTyped(f"form_data.x_axis is not read by {viz_type}")
        dimensions = groupby
    grouped = [*dimensions, *series, *rows, *cols]
    for index, name in enumerate(grouped):
        if name in grouped[:index]:
            raise _NotTyped(f"form_data groups by {name!r} twice")
    granularity = _granularity(form_data, column, temporal, x_axis, filters, time_ranges)
    dimensions, grain = _time_grain(
        viz, form_data, dimensions, [*series, *rows, *cols], temporal, types, granularity
    )
    row_limit: Any = form_data.get("row_limit", default_limit)
    limit = _SUPERSET_ROW_LIMIT if row_limit is None or row_limit == "" else row_limit
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_IR_LIMIT:
        raise _NotTyped(f"form_data.row_limit = {row_limit!r} is outside the IR limit")
    return ChartSpec(
        id=chart_id,
        title=title,
        viz=viz,
        query=ChartQuery(
            table=table,
            dimensions=dimensions,
            series=series,
            rows=rows,
            columns=cols,
            measures=measures,
            filters=filters,
            limit=limit,
            time_grain=grain,
        ),
    )


def _saved_measure(name: str, expression: str, where: str, calculated: Collection[str]) -> Measure:
    """A saved metric is typed only when its SQL is exactly `AGG(column)` or
    `COUNT(DISTINCT column)`.

    A reason names the metric by its name, never by a literal or an expression of its SQL: that
    SQL is the dataset's and can hold a credential, and a reason goes to `inventory.json` and to
    the `--json` answer. The one column of a typed `AGG(column)` is the measure's own, and the
    model's check names it when the model has no such column.
    sqlglot fails on some malformed SQL with an error of another kind than its own (an
    `AttributeError`, a `ValueError`); that, too, is a metric that does not parse.
    """
    try:
        node = sqlglot.parse_one(expression, read=READ_SQL_DIALECT)
    except Exception:
        raise _NotTyped(f"form_data.{where}: the saved metric {name!r} does not parse") from None
    aggregate = _SAVED_AGGREGATE.get(type(node))
    argument = node.this if aggregate is not None else None
    if aggregate is Aggregation.COUNT and isinstance(argument, exp.Distinct):
        aggregate = Aggregation.COUNT_DISTINCT
        argument = argument.expressions[0] if len(argument.expressions) == 1 else None
    if (
        aggregate is None
        or not isinstance(argument, exp.Column)
        or argument.table
        or len(node.expressions) > 0
        or isinstance(argument.this, exp.Star)
    ):
        raise _NotTyped(f"form_data.{where}: the saved metric {name!r} is not AGG(column)")
    column = argument.name
    if column in calculated:
        raise _NotTyped(
            f"form_data.{where}: the saved metric reads the calculated column {column!r}"
        )
    return Measure(column=column, agg=aggregate)


def _metric_label(value: Any) -> str:
    """The label Superset 4.1.2 tells a chart's metrics apart by (`getMetricLabel`): a saved
    metric's name, an adhoc metric's own label, else `AGG(column)`. It queries one metric of
    each label (`extractQueryFields`), the first."""
    if isinstance(value, str):
        return value
    label = value.get("label")
    if isinstance(label, str) and label:
        return label
    return f"{value.get('aggregate')}({value['column'].get('column_name')})"


#: Comparator texts Superset 4.1 reads as another value (`NULL_STRING`, `EMPTY_STRING`).
_SPECIAL_VALUES = frozenset({"<NULL>", "<empty string>"})

#: Column types whose filter values Superset casts (`filter_values_handler`: a number, a bool).
_CAST_TYPE = re.compile(r"(?i)int|float|decimal|double|real|numeric|bool")

#: Column types Superset reads a number on as epoch milliseconds (`filter_values_handler`).
_TIME_TYPE = re.compile(r"(?i)date|time|interval")

#: Column types Superset reads a number on as true or false (`cast_to_boolean`).
_BOOL_TYPE = re.compile(r"(?i)bool")


def _filters(
    form_data: Mapping[str, Any],
    column: Any,
    types: Mapping[str, str],
    temporal: Collection[str],
) -> tuple[list[QueryFilter], list[Any]]:
    """The filters of a chart, and the subjects of its time ranges (each reads no time).

    A value is typed only where Superset 4.1.2 compares the column with it as written: not
    text it trims (tabs, line ends) or casts (on a number or bool column), not a number on a
    time column (epoch milliseconds) or a number other than 0 and 1 on a bool column.
    """
    filters: list[QueryFilter] = []
    time_ranges: list[Any] = []
    raw = form_data.get("adhoc_filters")
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        where = f"adhoc_filters[{index}]"
        if not isinstance(item, Mapping) or item.get("expressionType") != "SIMPLE":
            raise _NotTyped(f"form_data.{where} is not a simple filter")
        if item.get("clause") != "WHERE":
            raise _NotTyped(f"form_data.{where} filters the {item.get('clause')!r} clause")
        operator = item.get("operator")
        if operator == "TEMPORAL_RANGE":
            # Only "No filter" reads no time: Superset reads "" as up to today, and fails None.
            if item.get("comparator") != "No filter":
                raise _NotTyped(f"form_data.{where} limits time to {item.get('comparator')!r}")
            time_ranges.append(item.get("subject"))
            continue
        op = _OPERATOR.get(str(operator))
        if op is None:
            raise _NotTyped(f"form_data.{where} filters with {operator!r}")
        value = _filter_value(item.get("comparator"), op)
        if value is None:
            raise _NotTyped(f"form_data.{where} compares with a value the IR has no form for")
        subject = column(item.get("subject"), f"{where}.subject")
        values = value if isinstance(value, list) else [value]
        texts = [item for item in values if isinstance(item, str)]
        numbers = [item for item in values if not isinstance(item, str)]
        native = types.get(subject, "")
        if any(text in _SPECIAL_VALUES or text != text.strip("\t\n") for text in texts):
            raise _NotTyped(f"form_data.{where} compares with a value Superset reads as another")
        if texts and _CAST_TYPE.search(native):
            raise _NotTyped(f"form_data.{where} compares text Superset casts to the column's type")
        if numbers and (subject in temporal or _TIME_TYPE.search(native)):
            raise _NotTyped(
                f"form_data.{where} compares time with a number Superset reads as a date"
            )
        if _BOOL_TYPE.search(native) and any(
            type(item) is not int or item not in (0, 1) for item in numbers
        ):
            raise _NotTyped(
                f"form_data.{where} compares a bool with a number Superset reads as one"
            )
        filters.append(QueryFilter(column=subject, op=op, value=value))
    return filters, time_ranges


def _granularity(
    form_data: Mapping[str, Any],
    column: Any,
    temporal: Collection[str],
    x_axis: Sequence[str],
    filters: Sequence[QueryFilter],
    time_ranges: Sequence[Any],
) -> str | None:
    """The column `granularity_sqla` names, once what it changes and the IR would not say is
    refused.

    Superset 4.1.2 sends it as the query's `granularity`. The query context factory
    (`_apply_granularity`) puts it in place of a temporal x-axis and drops every filter on one
    column — the x-axis when it is temporal, else the column of a time range — to filter that
    column by the chart's time range instead. `get_sqla_query` puts the dataset's main time
    column in place of a granularity that is not temporal, and groups by the time grain any
    other column that is the granularity (`_time_grain`).
    """
    granularity = form_data.get("granularity_sqla")
    if _empty(granularity):
        return None
    if not isinstance(granularity, str):
        raise _NotTyped("form_data.granularity_sqla is not a column name")
    column(granularity, "granularity_sqla")
    if granularity not in temporal:
        raise _NotTyped(
            f"form_data.granularity_sqla = {granularity!r} is not temporal: Superset puts the "
            "dataset's main time column in its place"
        )
    temporal_x = x_axis[0] if x_axis and x_axis[0] in temporal else None
    if temporal_x is not None and granularity != temporal_x:
        raise _NotTyped(
            f"form_data.granularity_sqla = {granularity!r} takes the place of the x-axis "
            f"{temporal_x!r}"
        )
    dropped = temporal_x
    if dropped is None and time_ranges:
        dropped = granularity if granularity in time_ranges else time_ranges[0]
    if dropped is not None and any(item.column == dropped for item in filters):
        raise _NotTyped(
            f"form_data.granularity_sqla = {granularity!r} drops the filters on {dropped!r}"
        )
    return granularity


def _time_grain(
    viz: Viz,
    form_data: Mapping[str, Any],
    dimensions: list[str],
    others: Sequence[str],
    temporal: Collection[str],
    types: Mapping[str, str],
    granularity: str | None,
) -> tuple[list[str], TimeGrain | None]:
    """The dimensions in the order Superset groups them, and the IR grain of the first.

    Superset 4.1.2 groups by `time_grain_sqla` each temporal column it sends as an axis
    (`BASE_AXIS`): the x-axis of a line, bar, area or heatmap; the first column a table's
    `temporal_columns_lookup` flags, moved to the front (a table with none sends no grain at
    all); each pivot row or column the lookup flags or that is the granularity. It groups by
    the grain, too, any other column that is the granularity (`get_sqla_query`). The IR grains
    the first dimension, as the start of its period, and nothing else: a chart Superset groups
    otherwise is not typed. Superset starts the period of the column cast to `DateTime`, so a
    column of a wider type is not typed, and a day is typed on a `Date` only: the IR's day is
    the column as it is.
    """
    raw = form_data.get("time_grain_sqla")
    if _empty(raw):
        return dimensions, None
    axis: list[str] = []
    if viz in (Viz.LINE, Viz.BAR, Viz.STACKED_BAR, Viz.AREA, Viz.HEATMAP):
        axis = dimensions[:1]
    elif viz is Viz.TABLE:
        lookup = _lookup(form_data)
        first = next((name for name in dimensions if lookup.get(name)), None)
        if first is None:
            return dimensions, None
        rest = list(dimensions)
        rest.remove(first)
        dimensions, axis = [first, *rest], [first]
    elif viz is Viz.PIVOT:
        lookup = _lookup(form_data)
        axis = [name for name in others if lookup.get(name) or name == granularity]
    truncated = [name for name in axis if name in temporal]
    truncated += [
        name for name in (*dimensions, *others) if name == granularity and name not in axis
    ]
    if not truncated:
        return dimensions, None
    if viz is Viz.PIVOT or truncated != dimensions[:1]:
        raise _NotTyped(
            f"form_data.time_grain_sqla = {raw!r} groups {', '.join(map(repr, truncated))}; "
            "the IR grains the first dimension only"
        )
    grain = _GRAIN.get(str(raw))
    if grain is None:
        raise _NotTyped(f"form_data.time_grain_sqla = {raw!r} has no IR grain")
    native = types.get(dimensions[0], "")
    if not _is_date_or_datetime(native):
        raise _NotTyped(
            f"form_data.time_grain_sqla = {raw!r}: Superset casts {dimensions[0]!r}, a column of "
            f"{native or 'no named type'}, to DateTime (1970 to 2106) first; a grain is typed on "
            "a Date or DateTime column only"
        )
    if grain is TimeGrain.DAY and not _is_date(native):
        raise _NotTyped(
            f"form_data.time_grain_sqla = 'P1D' starts the day of {dimensions[0]!r}, a column of "
            f"{native}; the IR's day is the column as it is"
        )
    return dimensions, grain


def _lookup(form_data: Mapping[str, Any]) -> Mapping[str, Any]:
    """`temporal_columns_lookup`: each column, and whether Explore saw it as temporal."""
    lookup = form_data.get("temporal_columns_lookup")
    if _empty(lookup):
        return {}
    if not isinstance(lookup, Mapping) or not all(
        flag is None or isinstance(flag, bool) for flag in lookup.values()
    ):
        raise _NotTyped("form_data.temporal_columns_lookup is not a map of columns to flags")
    return lookup


#: `Nullable(...)` or `LowCardinality(...)` around a ClickHouse type.
_WRAPPED = re.compile(r"(?i)(?:nullable|lowcardinality)\((.*)\)")


def _unwrapped(native: str) -> str:
    """A type without the `Nullable(...)` or `LowCardinality(...)` around it."""
    text = native.strip()
    while (match := _WRAPPED.fullmatch(text)) is not None:
        text = match.group(1).strip()
    return text


def _is_date(native: str) -> bool:
    """A `Date` type, wrapped in `Nullable` or `LowCardinality` or not, any case."""
    return re.fullmatch(r"(?i)date", _unwrapped(native)) is not None


def _is_date_or_datetime(native: str) -> bool:
    """A `Date`, `DateTime` or `DateTime('<zone>')` type, wrapped in `Nullable` or
    `LowCardinality` or not, any case. Superset 4.1.2 casts a column to `DateTime` (1970 to
    2106) before it starts a period (`toStartOfMonth(toDateTime(col))`,
    `ClickHouseBaseEngineSpec`); the IR starts the period of the column as it is. Any other type
    — `Date32`, `DateTime64`, an aggregate state of one, a type not named — may hold a value
    outside that range, which falls into another period there than in the IR. So does a `Date`
    whose midnight in the server's time zone is past 2106-02-07 06:28:15 UTC (wrapped into
    1970), and, at `P1D`, 1970-01-01 when that zone was east of UTC in 1970 (moved to
    1970-01-02): the spec names both as a known boundary."""
    return re.fullmatch(r"(?i)date|datetime(?:\(\s*'[^']*'\s*\))?", _unwrapped(native)) is not None


def _post_processes(key: str, value: Any) -> bool:
    """Whether Superset 4.1.2 post-processes a chart whose form_data holds *value* at *key*.

    A `time_compare` of any value but null is a list of offsets (`ensureIsArray`: "" and false
    too), so only null and an empty list compare nothing. Any other list or object is true
    where Superset tests a key for truth.
    """
    if key == "time_compare" and value == []:
        return False
    try:
        return value not in _NO_POST_PROCESSING[key]
    except TypeError:
        return True


def _filter_value(value: Any, op: FilterOp) -> Any:
    if op is FilterOp.IN:
        if not isinstance(value, list) or not value:
            return None
        kinds = {type(item) for item in value}
        if len(kinds) != 1 or kinds.pop() not in (str, int, float):
            return None
        return list(value)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return value


# --- read SQL ---------------------------------------------------------------------------------


def _read_sql_chart(
    chart_id: str, title: str, form_data: Mapping[str, Any], dataset: Mapping[str, Any]
) -> tuple[ChartSpec | None, str]:
    """A tile that selects `*` from its source plus every SQL fragment it carries.

    The fragments are each a select item on lines of their own, so a line comment in one ends
    with it. They come from the chart (any `sqlExpression`, `where` or `having`, at any depth)
    and from the dataset answer (every saved metric and calculated column it holds): which of
    them a viz outside the typed set reads is not known here, so all of them are. Superset
    trims that answer to what some form_data keys name, so a calculated column a chart reads
    through another key is not among them; `*` still reads every column of the source.
    """
    sql_text = dataset.get("sql")
    table_name = _text(dataset, "table_name")
    schema = _text(dataset, "schema")
    if not schema:
        return None, "the dataset names no schema, so its table could not be named"
    if sql_text:
        if not isinstance(sql_text, str):
            raise SupersetAnswerError(f"{chart_id}: the dataset sql is not text")
        source = "(\n" + sql_text.strip(_VIRTUAL_EDGES) + "\n) AS virtual_table"
    else:
        source = exp.Table(this=exp.to_identifier(table_name), db=exp.to_identifier(schema)).sql(
            dialect=READ_SQL_DIALECT
        )
    fragments = [*_sql_fragments(form_data), *_dataset_fragments(dataset)]
    items = ["*", *(f"(\n{fragment}\n)" for fragment in dict.fromkeys(fragments))]
    raw_sql = "SELECT\n" + ",\n".join(items) + "\nFROM " + source
    try:
        guard_sql(raw_sql, dialect=READ_SQL_DIALECT)
    except Exception as error:
        return None, f"its read SQL is not one plain SELECT: {_guard_reason(error)}"
    return (
        ChartSpec(
            id=chart_id,
            title=title,
            viz=Viz.TABLE,
            query=ChartQuery(table=f"{schema}.{table_name}", raw_sql=raw_sql),
        ),
        "",
    )


#: What Superset 4.1.2 strips off both ends of a virtual dataset's SQL (`get_rendered_sql`).
_VIRTUAL_EDGES = "\t\r\n; "


def _sql_fragments(value: Any) -> Iterator[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _SQL_KEYS and isinstance(item, str) and item.strip():
                yield item.strip()
            else:
                yield from _sql_fragments(item)
    elif isinstance(value, list):
        for item in value:
            yield from _sql_fragments(item)


def _dataset_fragments(dataset: Mapping[str, Any]) -> Iterator[str]:
    for metric in _list(dataset, "metrics"):
        expression = metric.get("expression")
        if isinstance(expression, str) and expression.strip():
            yield expression.strip()
    for column in _list(dataset, "columns"):
        expression = column.get("expression")
        if isinstance(expression, str) and expression.strip():
            yield expression.strip()


# --- answers ----------------------------------------------------------------------------------


#: The colour codes sqlglot marks the place of a parse error with.
_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """A message on one line and at most 200 characters, without terminal colour codes."""
    return " ".join(_SGR.sub("", text).split())[:200] or "(no message)"


def _guard_reason(error: Exception) -> str:
    """Why the read SQL was refused, without quoting the SQL.

    A dataset's SQL can hold a credential (a table function's password, say), and the parser's
    message quotes the text around the place it stopped at and the token it met there; so a
    SQL that does not parse is named by that place only. sqlglot fails on some malformed SQL
    with an error of another kind than its own (an `AttributeError`, a `ValueError`), whose
    message can quote a token as well: that is named by its class. The guard's own messages
    name a construct or a function, never the text.
    """
    cause = error.__cause__ if isinstance(error, SQLGuardError) else error
    if isinstance(cause, ParseError):
        place = cause.errors[0] if cause.errors else {}
        return f"it does not parse (line {place.get('line')}, column {place.get('col')})"
    if isinstance(cause, SqlglotError):
        return "it does not parse"
    if not isinstance(error, SQLGuardError):
        return f"it does not parse (the parser failed with {type(error).__name__})"
    return _plain(str(error))


def _table(dataset: Mapping[str, Any]) -> str | None:
    schema, name = _text(dataset, "schema"), _text(dataset, "table_name")
    return f"{schema}.{name}" if schema and name else None


def _database(dataset: Mapping[str, Any]) -> tuple[int | None, str]:
    """The Superset database a dataset is on: its id and engine, where the answer names them."""
    database = dataset.get("database")
    if not isinstance(database, Mapping):
        return None, ""
    ident = database.get("id")
    if not isinstance(ident, int) or isinstance(ident, bool):
        return None, _text(database, "backend")
    return ident, _text(database, "backend")


def _dataset_id(form_data: Mapping[str, Any]) -> int | None:
    """`"17__table"` → 17. A datasource of another kind is no dataset of the dashboard."""
    match = re.fullmatch(r"([0-9]+)__table", str(form_data.get("datasource") or ""))
    return int(match.group(1)) if match else None


def _has_sql(value: Any) -> bool:
    return next(_sql_fragments(value), None) is not None


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {} or value is False


def _hashable(value: Any) -> Any:
    try:
        hash(value)
    except TypeError:
        return json.dumps(value, sort_keys=True)
    return value


def _int(answer: Mapping[str, Any], key: str) -> int:
    value = answer.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SupersetAnswerError(f"an answer has no integer {key!r}")
    return value


def _text(answer: Mapping[str, Any], key: str) -> str:
    value = answer.get(key)
    return value if isinstance(value, str) else ""


def _list(answer: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = answer.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise SupersetAnswerError(f"an answer's {key!r} is not a list of objects")
    return value
