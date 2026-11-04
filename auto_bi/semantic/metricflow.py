"""dbt semantic manifest reader (ADR 0008, slice S1).

`dbt parse` writes `target/semantic_manifest.json`, the typed model MetricFlow itself reads.
This module reads that artifact strictly and computes the closure a metric's pin addresses;
resolving a formula from it is a later slice.

Strict means four things, checked in this order, and a failure at one step is the answer:

1. **JSON.** UTF-8 without a byte order mark, no repeated key in an object, no
   `NaN`/`Infinity`, no number that does not fit a finite double, no integer past
   ``2**53 - 1``, no string or key with a lone surrogate escape, no nesting deeper than
   :data:`MAX_NESTING`. Python's `json` rejects the first and the deepest nesting by itself
   but takes the rest quietly: the last repeated key wins, `1e400` becomes infinity, an
   integer past ``2**53 - 1`` (within Python's own digit limit) and half of a UTF-16 pair
   become values RFC 8785 writes differently or not at all. An integer past that digit limit
   is rejected too. A pin over a value the reader changed would address something the
   artifact does not say. Numbers are IEEE-754 doubles, as RFC 8785 has them: `1`, `1.0`
   and `1e0` are one value and give one pin, and so are `0` and `-0`. A problem of this
   step sits at `$`.
2. **Schema version.** `project_configuration.dsi_package_version` must be one this module
   encodes (:data:`SUPPORTED_SCHEMA_VERSIONS`). Its parts are strings of digits; any other
   part is ``gap.external_artifact_invalid``. An absent version is the pydantic default
   0.0.0. Any version not in the list is ``gap.external_schema_unsupported`` and nothing
   else is read: a field of another version may mean something else.
3. **Schema.** Every object has exactly the keys of its model in that version — a missing
   required key, an unknown key, `null` where pydantic does not allow it, a wrong JSON type
   or a value outside an enum is ``gap.external_artifact_invalid``. MetricFlow ignores
   unknown keys; this reader does not, because an artifact that does not match its declared
   version is not the one the version list was checked against.
4. **Names and references.** Metric, semantic model and measure names are unique (measures
   across the whole artifact, dimensions and entities within their semantic model). These
   references resolve: the input metrics and measures of a metric, its
   `metric_aggregation_params.semantic_model` and the `agg_time_dimension` there, and the
   default and measure `agg_time_dimension` of a semantic model, among its own dimensions.
   No metric depends on itself through its inputs. A failure is
   ``gap.external_artifact_invalid``. Other references are not resolved here: conversion
   `entity` and `constant_properties`, `non_additive_dimension`, the granularities of
   `time_granularity`, offsets, windows and `grain_to_date`, `primary_entity`, filter
   templates and saved queries. A formula that uses one of them is a construct ADR 0008,
   item 8 does not resolve; saved queries are in no formula.

A problem names a JSON path and, for a reference or a name, the identifier; it never carries
a value from the artifact (§10.2). A key that is not a plain identifier is shown in brackets
as a JSON string.

The closure of a metric (ADR 0008, item 4) is the metric, its input metrics at every depth,
the semantic models their measures and aggregation parameters touch, and
`project_configuration` as a whole — objects exactly as the artifact has them, with `null`
and default values, metrics and semantic models in name order. Lists inside an object keep
the artifact's order, so reordering one moves the pin even where the order means nothing.
A `filter` on one of those metrics or on one of their inputs, or a conversion metric among
them, makes the closure every metric and every semantic model of the artifact: a filter
template may name dimensions and metrics of any semantic model, a conversion joins through
an entity, and neither dependency is read here. Saved queries are never in a closure: they
query metrics, they do not define them. The pin addresses the definition of a formula, not
the result of any query MetricFlow could run on it: joins and grouping come from the native
contract (ADR 0008, item 2).
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from auto_bi.evidence.canonical import digest_bytes, sha256_digest
from auto_bi.scalars import MAX_SAFE_INT

#: `external.source` of a metric contract whose formula this artifact defines.
SOURCE = "dbt_semantic_manifest"

ARTIFACT_INVALID = "gap.external_artifact_invalid"
SCHEMA_UNSUPPORTED = "gap.external_schema_unsupported"

JsonObject = dict[str, Any]

# A field kind: ("str",), ("int",), ("bool",), ("number",), ("mapping",) — any JSON object,
# ("tags",) — a string or a list of strings, ("enum", <name>), ("model", <name>),
# ("list", <kind>).
_Kind = tuple[Any, ...]
_STR: _Kind = ("str",)
_INT: _Kind = ("int",)
_BOOL: _Kind = ("bool",)
_NUMBER: _Kind = ("number",)
_MAPPING: _Kind = ("mapping",)
_TAGS: _Kind = ("tags",)


def _enum(name: str) -> _Kind:
    return ("enum", name)


def _model(name: str) -> _Kind:
    return ("model", name)


def _list(kind: _Kind) -> _Kind:
    return ("list", kind)


# Whether a field may be absent or null, from pydantic's `required` and `allow_none` and not
# from the default: a required field is present and not null; an optional one may be absent,
# and null only where `allow_none` holds (`MetricTypeParams.is_private` defaults to false and
# allows null; `Dimension.is_partition` defaults to false and does not).
_REQUIRED = "required"
_NULLABLE = "nullable"
_DEFAULTED = "defaulted"

_Field = tuple[str, _Kind]

#: The pydantic models of `metricflow_semantic_interfaces` as metricflow 0.213.0 ships them
#: (`dsi_package_version` 0.213.0), each without its `Pydantic` prefix: field -> (presence,
#: kind). The JSON key is the field name, also where pydantic has an alias: dbt writes
#: `ExportConfig.schema_name`, not `schema`.
_MODELS_0_213_0: dict[str, dict[str, _Field]] = {
    "SemanticManifest": {
        "semantic_models": (_REQUIRED, _list(_model("SemanticModel"))),
        "metrics": (_REQUIRED, _list(_model("Metric"))),
        "project_configuration": (_REQUIRED, _model("ProjectConfiguration")),
        "saved_queries": (_DEFAULTED, _list(_model("SavedQuery"))),
    },
    "ProjectConfiguration": {
        "dsi_package_version": (_DEFAULTED, _model("SemanticVersion")),
        "metadata": (_NULLABLE, _model("Metadata")),
        "time_spine_table_configurations": (
            _DEFAULTED,
            _list(_model("TimeSpineTableConfiguration")),
        ),
        "time_spines": (_DEFAULTED, _list(_model("TimeSpine"))),
    },
    "SemanticVersion": {
        "major_version": (_REQUIRED, _STR),
        "minor_version": (_REQUIRED, _STR),
        "patch_version": (_NULLABLE, _STR),
    },
    "Metadata": {
        "repo_file_path": (_REQUIRED, _STR),
        "file_slice": (_REQUIRED, _model("FileSlice")),
    },
    "FileSlice": {
        "filename": (_REQUIRED, _STR),
        "content": (_REQUIRED, _STR),
        "start_line_number": (_REQUIRED, _INT),
        "end_line_number": (_REQUIRED, _INT),
    },
    "TimeSpineTableConfiguration": {
        "location": (_REQUIRED, _STR),
        "column_name": (_REQUIRED, _STR),
        "grain": (_REQUIRED, _enum("TimeGranularity")),
    },
    "TimeSpine": {
        "node_relation": (_REQUIRED, _model("NodeRelation")),
        "primary_column": (_REQUIRED, _model("TimeSpinePrimaryColumn")),
        "custom_granularities": (_DEFAULTED, _list(_model("TimeSpineCustomGranularityColumn"))),
    },
    "TimeSpinePrimaryColumn": {
        "name": (_REQUIRED, _STR),
        "time_granularity": (_REQUIRED, _enum("TimeGranularity")),
    },
    "TimeSpineCustomGranularityColumn": {
        "name": (_REQUIRED, _STR),
        "column_name": (_NULLABLE, _STR),
    },
    "NodeRelation": {
        "alias": (_REQUIRED, _STR),
        "schema_name": (_REQUIRED, _STR),
        "database": (_NULLABLE, _STR),
        "relation_name": (_DEFAULTED, _STR),
    },
    "SemanticLayerElementConfig": {
        "meta": (_DEFAULTED, _MAPPING),
    },
    "SemanticModel": {
        "name": (_REQUIRED, _STR),
        "node_relation": (_REQUIRED, _model("NodeRelation")),
        "defaults": (_NULLABLE, _model("SemanticModelDefaults")),
        "description": (_NULLABLE, _STR),
        "label": (_NULLABLE, _STR),
        "primary_entity": (_NULLABLE, _STR),
        "entities": (_DEFAULTED, _list(_model("Entity"))),
        "measures": (_DEFAULTED, _list(_model("Measure"))),
        "dimensions": (_DEFAULTED, _list(_model("Dimension"))),
        "metadata": (_NULLABLE, _model("Metadata")),
        "config": (_NULLABLE, _model("SemanticLayerElementConfig")),
    },
    "SemanticModelDefaults": {
        "agg_time_dimension": (_NULLABLE, _STR),
    },
    "Entity": {
        "name": (_REQUIRED, _STR),
        "type": (_REQUIRED, _enum("EntityType")),
        "description": (_NULLABLE, _STR),
        "role": (_NULLABLE, _STR),
        "expr": (_NULLABLE, _STR),
        "label": (_NULLABLE, _STR),
        "metadata": (_NULLABLE, _model("Metadata")),
        "config": (_NULLABLE, _model("SemanticLayerElementConfig")),
    },
    "Measure": {
        "name": (_REQUIRED, _STR),
        "agg": (_REQUIRED, _enum("AggregationType")),
        "agg_params": (_NULLABLE, _model("MeasureAggregationParameters")),
        "agg_time_dimension": (_NULLABLE, _STR),
        "create_metric": (_NULLABLE, _BOOL),
        "description": (_NULLABLE, _STR),
        "expr": (_NULLABLE, _STR),
        "label": (_NULLABLE, _STR),
        "non_additive_dimension": (_NULLABLE, _model("NonAdditiveDimensionParameters")),
        "metadata": (_NULLABLE, _model("Metadata")),
        "config": (_NULLABLE, _model("SemanticLayerElementConfig")),
    },
    "MeasureAggregationParameters": {
        "percentile": (_NULLABLE, _NUMBER),
        "use_discrete_percentile": (_DEFAULTED, _BOOL),
        "use_approximate_percentile": (_DEFAULTED, _BOOL),
    },
    "NonAdditiveDimensionParameters": {
        "name": (_REQUIRED, _STR),
        "window_choice": (_DEFAULTED, _enum("AggregationType")),
        "window_groupings": (_DEFAULTED, _list(_STR)),
    },
    "Dimension": {
        "name": (_REQUIRED, _STR),
        "type": (_REQUIRED, _enum("DimensionType")),
        "datatype": (_NULLABLE, _enum("DataType")),
        "description": (_NULLABLE, _STR),
        "expr": (_NULLABLE, _STR),
        "is_partition": (_DEFAULTED, _BOOL),
        "label": (_NULLABLE, _STR),
        "type_params": (_NULLABLE, _model("DimensionTypeParams")),
        "metadata": (_NULLABLE, _model("Metadata")),
        "config": (_NULLABLE, _model("SemanticLayerElementConfig")),
    },
    "DimensionTypeParams": {
        "time_granularity": (_REQUIRED, _enum("TimeGranularity")),
        "validity_params": (_NULLABLE, _model("DimensionValidityParams")),
    },
    "DimensionValidityParams": {
        "is_start": (_DEFAULTED, _BOOL),
        "is_end": (_DEFAULTED, _BOOL),
    },
    "Metric": {
        "name": (_REQUIRED, _STR),
        "type": (_REQUIRED, _enum("MetricType")),
        "type_params": (_REQUIRED, _model("MetricTypeParams")),
        "datatype": (_NULLABLE, _enum("DataType")),
        "description": (_NULLABLE, _STR),
        "filter": (_NULLABLE, _model("WhereFilterIntersection")),
        "label": (_NULLABLE, _STR),
        "time_granularity": (_NULLABLE, _STR),
        "metadata": (_NULLABLE, _model("Metadata")),
        "config": (_NULLABLE, _model("SemanticLayerElementConfig")),
    },
    "MetricTypeParams": {
        "measure": (_NULLABLE, _model("MetricInputMeasure")),
        "numerator": (_NULLABLE, _model("MetricInput")),
        "denominator": (_NULLABLE, _model("MetricInput")),
        "expr": (_NULLABLE, _STR),
        "window": (_NULLABLE, _model("MetricTimeWindow")),
        "grain_to_date": (_NULLABLE, _enum("TimeGranularity")),
        "metrics": (_NULLABLE, _list(_model("MetricInput"))),
        "conversion_type_params": (_NULLABLE, _model("ConversionTypeParams")),
        "cumulative_type_params": (_NULLABLE, _model("CumulativeTypeParams")),
        "input_measures": (_DEFAULTED, _list(_model("MetricInputMeasure"))),
        "metric_aggregation_params": (_NULLABLE, _model("MetricAggregationParams")),
        "join_to_timespine": (_DEFAULTED, _BOOL),
        "fill_nulls_with": (_NULLABLE, _INT),
        "is_private": (_NULLABLE, _BOOL),
    },
    "MetricInputMeasure": {
        "name": (_REQUIRED, _STR),
        "filter": (_NULLABLE, _model("WhereFilterIntersection")),
        "alias": (_NULLABLE, _STR),
        "join_to_timespine": (_DEFAULTED, _BOOL),
        "fill_nulls_with": (_NULLABLE, _INT),
    },
    "MetricInput": {
        "name": (_REQUIRED, _STR),
        "filter": (_NULLABLE, _model("WhereFilterIntersection")),
        "alias": (_NULLABLE, _STR),
        "offset_window": (_NULLABLE, _model("MetricTimeWindow")),
        "offset_to_grain": (_NULLABLE, _STR),
    },
    "MetricTimeWindow": {
        "count": (_REQUIRED, _INT),
        "granularity": (_REQUIRED, _STR),
    },
    "MetricAggregationParams": {
        "semantic_model": (_REQUIRED, _STR),
        "agg": (_REQUIRED, _enum("AggregationType")),
        "agg_params": (_NULLABLE, _model("MeasureAggregationParameters")),
        "agg_time_dimension": (_NULLABLE, _STR),
        "non_additive_dimension": (_NULLABLE, _model("NonAdditiveDimensionParameters")),
    },
    "CumulativeTypeParams": {
        "window": (_NULLABLE, _model("MetricTimeWindow")),
        "grain_to_date": (_NULLABLE, _STR),
        "period_agg": (_DEFAULTED, _enum("PeriodAggregation")),
        "metric": (_NULLABLE, _model("MetricInput")),
    },
    "ConversionTypeParams": {
        "entity": (_REQUIRED, _STR),
        "base_measure": (_NULLABLE, _model("MetricInputMeasure")),
        "conversion_measure": (_NULLABLE, _model("MetricInputMeasure")),
        "base_metric": (_NULLABLE, _model("MetricInput")),
        "conversion_metric": (_NULLABLE, _model("MetricInput")),
        "calculation": (_DEFAULTED, _enum("ConversionCalculationType")),
        "window": (_NULLABLE, _model("MetricTimeWindow")),
        "constant_properties": (_NULLABLE, _list(_model("ConstantPropertyInput"))),
    },
    "ConstantPropertyInput": {
        "base_property": (_REQUIRED, _STR),
        "conversion_property": (_REQUIRED, _STR),
    },
    "WhereFilterIntersection": {
        "where_filters": (_REQUIRED, _list(_model("WhereFilter"))),
    },
    "WhereFilter": {
        "where_sql_template": (_REQUIRED, _STR),
    },
    "SavedQuery": {
        "name": (_REQUIRED, _STR),
        "query_params": (_REQUIRED, _model("SavedQueryQueryParams")),
        "description": (_NULLABLE, _STR),
        "label": (_NULLABLE, _STR),
        "metadata": (_NULLABLE, _model("Metadata")),
        "exports": (_DEFAULTED, _list(_model("Export"))),
        "tags": (_DEFAULTED, _TAGS),
    },
    "SavedQueryQueryParams": {
        "metrics": (_REQUIRED, _list(_STR)),
        "group_by": (_DEFAULTED, _list(_STR)),
        "where": (_NULLABLE, _model("WhereFilterIntersection")),
        "order_by": (_DEFAULTED, _list(_STR)),
        "limit": (_NULLABLE, _INT),
    },
    "Export": {
        "name": (_REQUIRED, _STR),
        "config": (_REQUIRED, _model("ExportConfig")),
    },
    "ExportConfig": {
        "export_as": (_REQUIRED, _enum("ExportDestinationType")),
        "schema_name": (_NULLABLE, _STR),
        "alias": (_NULLABLE, _STR),
    },
}

_TIME_GRANULARITY = frozenset(
    {
        "nanosecond",
        "microsecond",
        "millisecond",
        "second",
        "minute",
        "hour",
        "day",
        "week",
        "month",
        "quarter",
        "year",
    }
)

_ENUMS_0_213_0: dict[str, frozenset[str]] = {
    "AggregationType": frozenset(
        {
            "sum",
            "min",
            "max",
            "count_distinct",
            "sum_boolean",
            "average",
            "percentile",
            "median",
            "count",
        }
    ),
    "MetricType": frozenset({"simple", "ratio", "cumulative", "derived", "conversion"}),
    "DimensionType": frozenset({"categorical", "time"}),
    "EntityType": frozenset({"foreign", "natural", "primary", "unique"}),
    "DataType": frozenset(
        {
            "string",
            "integer",
            "decimal",
            "float",
            "boolean",
            "date",
            "time",
            "datetime",
            "datetime_tz",
            "opaque",
        }
    ),
    "TimeGranularity": _TIME_GRANULARITY,
    "PeriodAggregation": frozenset({"first", "last", "average"}),
    "ConversionCalculationType": frozenset({"conversions", "conversion_rate"}),
    "ExportDestinationType": frozenset({"table", "view"}),
}

_SCHEMAS: dict[str, tuple[dict[str, dict[str, _Field]], dict[str, frozenset[str]]]] = {
    "0.213.0": (_MODELS_0_213_0, _ENUMS_0_213_0),
}

#: `dsi_package_version` values whose schema this module encodes (ADR 0008, item 6). A new
#: one is a separate change with contract tests on artifacts dbt writes at that version.
SUPPORTED_SCHEMA_VERSIONS = frozenset(_SCHEMAS)

#: What `dsi_package_version` is when `project_configuration` omits it — the pydantic
#: default of metricflow 0.213.0, which no reader supports.
_ABSENT_VERSION = "0.0.0"

#: A part of `dsi_package_version`: digits only, so the dotted form names one version.
_VERSION_PART = re.compile(r"[0-9]{1,9}")

#: A key a path may show after a dot; any other key is shown in brackets as a JSON string.
_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: The deepest nesting of objects and lists an artifact may have. The schema itself nests
#: about ten levels; only `config.meta` is free-form, and the canonical form of a closure is
#: written recursively, so a limit keeps every step of the reader bounded.
MAX_NESTING = 64


@dataclass(frozen=True)
class ManifestProblem:
    """Why an artifact cannot be read: a gap code, a JSON path and a reason without values."""

    code: str
    path: str
    reason: str


@dataclass(frozen=True)
class ManifestRead:
    """The result of reading one artifact.

    `manifest` is set exactly when `problems` is empty. `artifact_digest` addresses the bytes
    as given, readable or not; `schema_version` is empty when the bytes did not get as far as
    a version.
    """

    artifact_digest: str
    schema_version: str
    manifest: SemanticManifest | None
    problems: tuple[ManifestProblem, ...]


class _Rejected(ValueError):
    """The bytes are not the strict JSON this reader accepts."""


def read_semantic_manifest(raw: bytes) -> ManifestRead:
    """Read `semantic_manifest.json` bytes strictly (see the module docstring)."""
    artifact_digest = digest_bytes(raw)

    def failed(code: str, path: str, reason: str, version: str = "") -> ManifestRead:
        return ManifestRead(artifact_digest, version, None, (ManifestProblem(code, path, reason),))

    try:
        data = _strict_json(raw)
    except _Rejected as error:
        return failed(ARTIFACT_INVALID, "$", str(error))

    version, where, why = _schema_version(data)
    if why:
        return failed(ARTIFACT_INVALID, where, why)
    if version not in _SCHEMAS:
        supported = ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
        return failed(
            SCHEMA_UNSUPPORTED,
            "$.project_configuration.dsi_package_version",
            f"dsi_package_version {version} is not one this reader encodes ({supported})",
            version,
        )

    models, enums = _SCHEMAS[version]
    problems: list[ManifestProblem] = []
    _check_object(data, "SemanticManifest", "$", models, enums, problems)
    if not problems:
        problems.extend(_check_names_and_references(data))
    if problems:
        return ManifestRead(artifact_digest, version, None, tuple(problems))
    return ManifestRead(artifact_digest, version, SemanticManifest(data, version), ())


class SemanticManifest:
    """A `semantic_manifest.json` that passed every check of :func:`read_semantic_manifest`.

    :meth:`metric` and :meth:`closure` return copies: changing what they return changes
    neither later results nor :meth:`closure_digest`.
    """

    def __init__(self, data: JsonObject, schema_version: str) -> None:
        self._data = data
        self.schema_version = schema_version
        self._metrics: dict[str, JsonObject] = {m["name"]: m for m in data["metrics"]}
        self._semantic_models: dict[str, JsonObject] = {
            model["name"]: model for model in data["semantic_models"]
        }
        self._measure_model: dict[str, str] = {
            measure["name"]: model["name"]
            for model in data["semantic_models"]
            for measure in model.get("measures") or []
        }

    def metric_names(self) -> list[str]:
        return sorted(self._metrics)

    def has_metric(self, name: str) -> bool:
        return name in self._metrics

    def metric(self, name: str) -> JsonObject:
        """The metric object as the artifact has it; `KeyError` if there is none."""
        return copy.deepcopy(self._metrics[name])

    def closure(self, metric: str) -> JsonObject:
        """What the pin of `metric` addresses (module docstring); `KeyError` if no such metric."""
        return copy.deepcopy(self._closure(metric))

    def closure_digest(self, metric: str) -> str:
        """`sha256:` of the RFC 8785 form of :meth:`closure` — the value a pin must equal."""
        return sha256_digest(self._closure(metric), allow_float=True)

    def _closure(self, metric: str) -> JsonObject:
        if metric not in self._metrics:
            raise KeyError(metric)
        names: set[str] = set()
        pending = [metric]
        while pending:
            name = pending.pop()
            if name in names:
                continue
            names.add(name)
            pending.extend(_input_metric_names(self._metrics[name]))
        if any(_reads_beyond_its_models(self._metrics[name]) for name in names):
            names = set(self._metrics)
            models = set(self._semantic_models)
        else:
            models = set()
            for name in names:
                found = self._metrics[name]
                models.update(self._measure_model[m] for m in _measure_names(found))
                params = found["type_params"].get("metric_aggregation_params")
                if params is not None:
                    models.add(params["semantic_model"])
        return {
            "source": SOURCE,
            "metric": metric,
            "project_configuration": self._data["project_configuration"],
            "metrics": [self._metrics[name] for name in sorted(names)],
            "semantic_models": [self._semantic_models[name] for name in sorted(models)],
        }


def _strict_json(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _Rejected(f"not UTF-8 at byte {error.start}") from None

    def pairs(items: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in items:
            if key in result:
                raise _Rejected(f"an object repeats the key {key!r}")
            result[key] = value
        return result

    def constant(name: str) -> Any:
        raise _Rejected(f"{name} is not a JSON number")

    def number(text: str) -> float:
        value = float(text)
        if not math.isfinite(value):
            raise _Rejected("a number does not fit a finite double")
        return value

    def integer(text: str) -> int:
        try:
            value = int(text)
        except ValueError:  # past Python's digit limit for int()
            raise _Rejected("an integer has too many digits") from None
        if abs(value) > MAX_SAFE_INT:
            raise _Rejected("an integer is outside the IEEE-754 safe integer range")
        return value

    try:
        data = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=constant,
            parse_float=number,
            parse_int=integer,
        )
    except _Rejected:
        raise
    except RecursionError:
        raise _Rejected(f"nested deeper than {MAX_NESTING} levels") from None
    except ValueError as error:
        raise _Rejected(f"not JSON: {getattr(error, 'msg', 'unparsable')}") from None
    _check_tree(data)
    return data


def _check_tree(value: Any) -> None:
    """Reject nesting past :data:`MAX_NESTING` and lone surrogates, in one pass, no recursion.

    A JSON escape can write half of a UTF-16 pair; Python keeps it, RFC 8785 cannot write it.
    """
    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        item, level = pending.pop()
        if isinstance(item, str):
            _require_unicode(item)
            continue
        if isinstance(item, dict):
            for key in item:
                _require_unicode(key)
            children: list[Any] = list(item.values())
        elif isinstance(item, list):
            children = item
        else:
            continue
        if level + 1 > MAX_NESTING:
            raise _Rejected(f"nested deeper than {MAX_NESTING} levels")
        pending.extend((child, level + 1) for child in children)


def _require_unicode(text: str) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise _Rejected("a string or key holds a lone surrogate escape") from None


def _schema_version(data: Any) -> tuple[str, str, str]:
    """`(version, path, problem)`: the declared schema version, or where reading it failed."""
    if not isinstance(data, dict):
        return "", "$", "the artifact is not a JSON object"
    configuration = data.get("project_configuration")
    if not isinstance(configuration, dict):
        return "", "$.project_configuration", "project_configuration is not an object"
    path = "$.project_configuration.dsi_package_version"
    if "dsi_package_version" not in configuration:
        return _ABSENT_VERSION, "", ""
    declared = configuration["dsi_package_version"]
    if not isinstance(declared, dict):
        return "", path, "dsi_package_version is not an object"
    parts: list[str] = []
    for key in ("major_version", "minor_version", "patch_version"):
        part = declared.get(key)
        if part is None and key == "patch_version":
            continue
        if not isinstance(part, str) or not _VERSION_PART.fullmatch(part):
            return "", f"{path}.{key}", f"{key} is not a string of 1 to 9 digits"
        parts.append(part)
    return ".".join(parts), "", ""


def _member(path: str, key: str) -> str:
    """The JSON path of `key` in the object at `path`."""
    if _PLAIN_KEY.fullmatch(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key)}]"


def _check_object(
    value: Any,
    model: str,
    path: str,
    models: Mapping[str, Mapping[str, _Field]],
    enums: Mapping[str, frozenset[str]],
    problems: list[ManifestProblem],
) -> None:
    if not isinstance(value, dict):
        problems.append(ManifestProblem(ARTIFACT_INVALID, path, f"expected a {model} object"))
        return
    fields = models[model]
    for key in sorted(set(value) - set(fields)):
        problems.append(
            ManifestProblem(ARTIFACT_INVALID, _member(path, key), f"{model} has no key {key!r}")
        )
    for key, (presence, kind) in fields.items():
        where = _member(path, key)
        if key not in value:
            if presence == _REQUIRED:
                problems.append(
                    ManifestProblem(ARTIFACT_INVALID, where, f"required {model}.{key} is missing")
                )
            continue
        if value[key] is None:
            if presence != _NULLABLE:
                problems.append(
                    ManifestProblem(ARTIFACT_INVALID, where, f"{model}.{key} may not be null")
                )
            continue
        _check_value(value[key], kind, where, models, enums, problems)


def _check_value(
    value: Any,
    kind: _Kind,
    path: str,
    models: Mapping[str, Mapping[str, _Field]],
    enums: Mapping[str, frozenset[str]],
    problems: list[ManifestProblem],
) -> None:
    tag = kind[0]
    if tag == "model":
        _check_object(value, kind[1], path, models, enums, problems)
        return
    if tag == "list":
        if not isinstance(value, list):
            problems.append(ManifestProblem(ARTIFACT_INVALID, path, "expected a list"))
            return
        for index, item in enumerate(value):
            _check_value(item, kind[1], f"{path}[{index}]", models, enums, problems)
        return
    if tag == "enum":
        if not isinstance(value, str) or value not in enums[kind[1]]:
            problems.append(
                ManifestProblem(ARTIFACT_INVALID, path, f"not a {kind[1]} value of this version")
            )
        return
    expected = {
        "str": "a string",
        "int": "an integer",
        "bool": "a boolean",
        "number": "a number",
        "mapping": "an object",
        "tags": "a string or a list of strings",
    }[tag]
    if not _is_kind(value, tag):
        problems.append(ManifestProblem(ARTIFACT_INVALID, path, f"expected {expected}"))


def _is_kind(value: Any, tag: str) -> bool:
    if tag == "str":
        return isinstance(value, str)
    if tag == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if tag == "bool":
        return isinstance(value, bool)
    if tag == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if tag == "mapping":
        return isinstance(value, dict)
    return isinstance(value, str) or (
        isinstance(value, list) and all(isinstance(item, str) for item in value)
    )


def _input_metric_references(metric: JsonObject) -> Iterator[tuple[str, str]]:
    """`(path below the metric, name)` of every metric this metric takes as an input."""
    params = metric["type_params"]
    for key in ("numerator", "denominator"):
        if params.get(key) is not None:
            yield f"type_params.{key}.name", params[key]["name"]
    for index, item in enumerate(params.get("metrics") or []):
        yield f"type_params.metrics[{index}].name", item["name"]
    cumulative = params.get("cumulative_type_params")
    if cumulative is not None and cumulative.get("metric") is not None:
        yield "type_params.cumulative_type_params.metric.name", cumulative["metric"]["name"]
    conversion = params.get("conversion_type_params")
    if conversion is not None:
        for key in ("base_metric", "conversion_metric"):
            if conversion.get(key) is not None:
                yield f"type_params.conversion_type_params.{key}.name", conversion[key]["name"]


def _measure_references(metric: JsonObject) -> Iterator[tuple[str, str]]:
    """`(path below the metric, name)` of every measure this metric reads."""
    params = metric["type_params"]
    if params.get("measure") is not None:
        yield "type_params.measure.name", params["measure"]["name"]
    for index, item in enumerate(params.get("input_measures") or []):
        yield f"type_params.input_measures[{index}].name", item["name"]
    conversion = params.get("conversion_type_params")
    if conversion is not None:
        for key in ("base_measure", "conversion_measure"):
            if conversion.get(key) is not None:
                yield f"type_params.conversion_type_params.{key}.name", conversion[key]["name"]


def _input_metric_names(metric: JsonObject) -> list[str]:
    return [name for _, name in _input_metric_references(metric)]


def _measure_names(metric: JsonObject) -> list[str]:
    return [name for _, name in _measure_references(metric)]


def _reads_beyond_its_models(metric: JsonObject) -> bool:
    """A filter or a conversion: dependencies this module does not read (module docstring)."""
    if metric["type"] == "conversion" or metric.get("filter") is not None:
        return True
    params = metric["type_params"]
    inputs = [params.get(key) for key in ("measure", "numerator", "denominator")]
    inputs += params.get("metrics") or []
    inputs += params.get("input_measures") or []
    cumulative = params.get("cumulative_type_params")
    if cumulative is not None:
        inputs.append(cumulative.get("metric"))
    return any(item is not None and item.get("filter") is not None for item in inputs)


def _check_names_and_references(data: JsonObject) -> list[ManifestProblem]:
    problems: list[ManifestProblem] = []

    def unique(items: list[JsonObject], path: str, what: str) -> dict[str, JsonObject]:
        seen: dict[str, JsonObject] = {}
        for index, item in enumerate(items):
            name = item["name"]
            if name in seen:
                problems.append(
                    ManifestProblem(
                        ARTIFACT_INVALID,
                        f"{path}[{index}].name",
                        f"{what} {name!r} is declared more than once",
                    )
                )
            else:
                seen[name] = item
        return seen

    def missing(path: str, what: str, name: str) -> None:
        problems.append(
            ManifestProblem(ARTIFACT_INVALID, path, f"refers to {what} {name!r}, which is absent")
        )

    metrics = unique(data["metrics"], "$.metrics", "metric")
    models = unique(data["semantic_models"], "$.semantic_models", "semantic model")
    dimensions: dict[str, set[str]] = {}
    measure_names: set[str] = set()
    for index, model in enumerate(data["semantic_models"]):
        path = f"$.semantic_models[{index}]"
        own = unique(model.get("dimensions") or [], f"{path}.dimensions", "dimension")
        unique(model.get("entities") or [], f"{path}.entities", "entity")
        dimensions.setdefault(model["name"], set(own))
        for position, measure in enumerate(model.get("measures") or []):
            if measure["name"] in measure_names:
                problems.append(
                    ManifestProblem(
                        ARTIFACT_INVALID,
                        f"{path}.measures[{position}].name",
                        f"measure {measure['name']!r} is declared more than once",
                    )
                )
            measure_names.add(measure["name"])
    if problems:
        return problems

    for index, model in enumerate(data["semantic_models"]):
        path = f"$.semantic_models[{index}]"
        own_dimensions = dimensions[model["name"]]
        default = (model.get("defaults") or {}).get("agg_time_dimension")
        if default is not None and default not in own_dimensions:
            missing(f"{path}.defaults.agg_time_dimension", "dimension", default)
        for position, measure in enumerate(model.get("measures") or []):
            dimension = measure.get("agg_time_dimension")
            if dimension is not None and dimension not in own_dimensions:
                missing(f"{path}.measures[{position}].agg_time_dimension", "dimension", dimension)
    for index, metric in enumerate(data["metrics"]):
        path = f"$.metrics[{index}]"
        for where, name in _input_metric_references(metric):
            if name not in metrics:
                missing(f"{path}.{where}", "metric", name)
        for where, name in _measure_references(metric):
            if name not in measure_names:
                missing(f"{path}.{where}", "measure", name)
        params = metric["type_params"].get("metric_aggregation_params")
        if params is not None:
            model_name = params["semantic_model"]
            where = f"{path}.type_params.metric_aggregation_params"
            if model_name not in models:
                missing(f"{where}.semantic_model", "semantic model", model_name)
            else:
                dimension = params.get("agg_time_dimension")
                if dimension is not None and dimension not in dimensions[model_name]:
                    missing(f"{where}.agg_time_dimension", "dimension", dimension)
    if problems:
        return problems

    # Only a metric that depends on itself is named, not one that merely takes such a metric
    # as an input.
    inputs = {name: _input_metric_names(metric) for name, metric in metrics.items()}
    circular = _depends_on_itself(inputs)
    for index, metric in enumerate(data["metrics"]):
        if metric["name"] in circular:
            problems.append(
                ManifestProblem(
                    ARTIFACT_INVALID,
                    f"$.metrics[{index}]",
                    f"metric {metric['name']!r} depends on itself through its inputs",
                )
            )
    return problems


def _depends_on_itself(inputs: Mapping[str, Sequence[str]]) -> set[str]:
    """Names that reach themselves through `inputs`: Tarjan's strongly connected components.

    A name is in the result when its component has more than one name or it is its own input.
    Iterative and linear: each name's inputs are read once.
    """
    order: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: set[str] = set()
    for root in inputs:
        if root in order:
            continue
        order[root] = low[root] = len(order)
        stack.append(root)
        on_stack.add(root)
        work: list[tuple[str, Sequence[str], Iterator[str]]] = []
        edges = inputs[root]
        work.append((root, edges, iter(edges)))
        while work:
            name, edges, pending = work[-1]
            for target in pending:
                if target not in order:
                    order[target] = low[target] = len(order)
                    stack.append(target)
                    on_stack.add(target)
                    target_edges = inputs[target]
                    work.append((target, target_edges, iter(target_edges)))
                    break
                if target in on_stack:
                    low[name] = min(low[name], order[target])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[name])
                if low[name] == order[name]:
                    component: list[str] = []
                    while True:
                        item = stack.pop()
                        on_stack.discard(item)
                        component.append(item)
                        if item == name:
                            break
                    if len(component) > 1 or name in edges:
                        found.update(component)
    return found
