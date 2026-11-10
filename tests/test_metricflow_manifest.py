"""Strict reader of dbt `semantic_manifest.json` and the metric closure (ADR 0008, slice S1).

Fixtures are artifacts `dbt parse` wrote (dbt-core 1.12.6, metricflow 0.213.0): `legacy` and
`v2` are the S0 projects in both YAML dialects, `wide_*` add every aggregation, a measure
without `expr`, a private metric, offsets, `grain_to_date`, a conversion metric, a custom
granularity, a metric `time_granularity` and a saved query with an export; `wide_legacy` adds
a measure with `create_metric: true`. `dsi_0.213.0_schema.json` is the field tree of every
pydantic model `PydanticSemanticManifest` reaches in metricflow 0.213.0 and the values of its
enums, dumped from the installed package.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from auto_bi.evidence.canonical import digest_bytes
from auto_bi.semantic.metricflow import (
    _SCHEMAS,
    ARTIFACT_INVALID,
    MAX_NESTING,
    SCHEMA_UNSUPPORTED,
    SOURCE,
    SUPPORTED_SCHEMA_VERSIONS,
    ManifestRead,
    SemanticManifest,
    _depends_on_itself,
    read_semantic_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dbt_semantic_manifest"
KINDS = ("legacy", "v2", "wide_legacy", "wide_v2")
ALL = "every"
SURROGATE = chr(0xD800)


def _raw(kind: str) -> bytes:
    return (FIXTURES / kind / "semantic_manifest.json").read_bytes()


def _data(kind: str) -> dict[str, Any]:
    return json.loads(_raw(kind))


def _read(data: Any) -> ManifestRead:
    return read_semantic_manifest(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _read_escaped(data: Any) -> ManifestRead:
    """Read `data` written with every non-ASCII character as a JSON escape."""
    return read_semantic_manifest(json.dumps(data).encode("ascii"))


def _manifest(data: Any) -> SemanticManifest:
    read = _read(data)
    assert read.problems == ()
    assert read.manifest is not None
    return read.manifest


def _index(items: list[dict[str, Any]], name: str) -> int:
    return next(index for index, item in enumerate(items) if item["name"] == name)


def _metric(data: dict[str, Any], name: str) -> dict[str, Any]:
    return data["metrics"][_index(data["metrics"], name)]


def _model(data: dict[str, Any], name: str) -> dict[str, Any]:
    return data["semantic_models"][_index(data["semantic_models"], name)]


def _problems(read: ManifestRead) -> list[tuple[str, str, str]]:
    assert read.manifest is None
    return [(problem.code, problem.path, problem.reason) for problem in read.problems]


def _names(manifest: SemanticManifest, metric: str) -> tuple[list[str], list[str]]:
    closure = manifest.closure(metric)
    return (
        [item["name"] for item in closure["metrics"]],
        [item["name"] for item in closure["semantic_models"]],
    )


# --- fixtures dbt wrote ---------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_dbt_artifacts_read_clean(kind: str) -> None:
    raw = _raw(kind)
    read = read_semantic_manifest(raw)
    assert read.problems == ()
    assert read.schema_version == "0.213.0"
    assert read.artifact_digest == digest_bytes(raw)
    assert read.manifest is not None
    assert read.manifest.schema_version == "0.213.0"
    assert read.manifest.metric_names() == sorted(m["name"] for m in _data(kind)["metrics"])


def test_supported_versions_are_the_encoded_one() -> None:
    assert frozenset({"0.213.0"}) == SUPPORTED_SCHEMA_VERSIONS


def test_wide_fixtures_carry_the_constructs_they_are_for() -> None:
    legacy = _data("wide_legacy")
    measures = {m["name"]: m for model in legacy["semantic_models"] for m in model["measures"]}
    assert {m["agg"] for m in measures.values()} == {
        "sum",
        "min",
        "max",
        "count",
        "count_distinct",
        "average",
        "percentile",
        "median",
        "sum_boolean",
    }
    assert measures["gross_margin"]["expr"] is None
    assert measures["revenue_last"]["non_additive_dimension"] is not None
    assert _metric(legacy, "visit_to_purchase")["type"] == "conversion"
    v2 = _data("wide_v2")
    assert _metric(v2, "revenue_hidden")["type_params"]["is_private"] is True
    assert _metric(v2, "visit_to_purchase")["type_params"]["conversion_type_params"][
        "base_metric"
    ] == {
        "name": "visits",
        "filter": None,
        "alias": None,
        "offset_window": None,
        "offset_to_grain": None,
    }
    for data in (legacy, v2):
        [spine] = data["project_configuration"]["time_spines"]
        assert spine["custom_granularities"] == [{"name": "fiscal_quarter", "column_name": None}]
        assert _metric(data, "revenue_max")["time_granularity"] == "month"
        [query] = data["saved_queries"]
        assert query["exports"][0]["config"] == {
            "export_as": "table",
            "schema_name": "exports",
            "alias": "revenue_by_region",
        }
    # dbt turns `create_metric: true` into a metric of its own and writes the flag as false.
    assert measures["revenue_created"]["create_metric"] is False
    created = _metric(legacy, "revenue_created")["type_params"]["measure"]
    assert (created["name"], _metric(legacy, "revenue_created")["type"]) == (
        "revenue_created",
        "simple",
    )


def _time_dimensions(data: dict[str, Any]) -> dict[tuple[str, str], Any]:
    return {
        (model["name"], dimension["name"]): dimension["type_params"]
        for model in data["semantic_models"]
        for dimension in model["dimensions"]
        if dimension["type"] == "time"
    }


def test_v2_artifacts_drop_what_the_yaml_says() -> None:
    # ADR 0008, item 8, the note for S2: the V2 YAML of `revenue_filled` has
    # `fill_nulls_with: 0` and time dimensions with `granularity: day`; dbt 1.12.6 writes
    # neither. Legacy artifacts keep the granularity.
    day = {"time_granularity": "day", "validity_params": None}
    assert _time_dimensions(_data("legacy")) == {("sales_daily", "date"): day}
    assert _time_dimensions(_data("wide_legacy")) == {
        ("sales_daily", "date"): day,
        ("events", "ds"): day,
    }
    assert _time_dimensions(_data("v2")) == {("sales_daily", "date"): None}
    wide_v2 = _data("wide_v2")
    assert _time_dimensions(wide_v2) == {("sales_daily", "date"): None, ("events", "ds"): None}
    params = _metric(wide_v2, "revenue_filled")["type_params"]
    assert (params["fill_nulls_with"], params["join_to_timespine"]) == (None, True)
    aggregation = params["metric_aggregation_params"]
    assert (aggregation["semantic_model"], aggregation["agg_time_dimension"]) == (
        "sales_daily",
        "date",
    )


# --- the schema table -----------------------------------------------------------------


def _kind_of(annotation: str) -> tuple[Any, ...]:
    """The reader's kind for a pydantic annotation as `dsi_0.213.0_schema.json` writes it."""
    simple = {
        "<class 'str'>": ("str",),
        "<class 'int'>": ("int",),
        "<class 'bool'>": ("bool",),
        "<class 'float'>": ("number",),
        "typing.Dict[str, typing.Any]": ("mapping",),
        "typing.Union[str, typing.List[str]]": ("tags",),
    }
    if annotation in simple:
        return simple[annotation]
    sequence = re.fullmatch(r"typing\.(?:List|Sequence)\[([\w.]+)\]", annotation)
    if sequence:
        inner = sequence.group(1)
        return ("list", _kind_of(f"<class '{inner}'>"))
    enum = re.fullmatch(r"<enum '(\w+)'>", annotation)
    if enum:
        return ("enum", enum.group(1))
    model = re.fullmatch(r"<class '[\w.]+\.Pydantic(\w+)'>", annotation)
    assert model, annotation
    return ("model", model.group(1))


def _enums_in(kind: tuple[Any, ...]) -> Iterator[str]:
    if kind[0] == "enum":
        yield kind[1]
    if kind[0] == "list":
        yield from _enums_in(kind[1])


def test_schema_table_is_the_pydantic_models_of_metricflow_0_213_0() -> None:
    dump = json.loads((FIXTURES / "dsi_0.213.0_schema.json").read_text(encoding="utf-8"))
    for fields in dump["models"].values():
        fields.pop("__extra__")
    expected: dict[str, dict[str, tuple[str, tuple[Any, ...]]]] = {}
    for name, fields in dump["models"].items():
        table = expected.setdefault(name.removeprefix("Pydantic"), {})
        for field, info in fields.items():
            if info["required"]:
                presence = "required"
            else:
                presence = "nullable" if info["allow_none"] else "defaulted"
            # The key is the field name even where pydantic has an alias: dbt writes
            # `ExportConfig.schema_name` (the wide fixtures), not its alias `schema`.
            table[field] = (presence, _kind_of(info["type"]))
    models, enums = _SCHEMAS["0.213.0"]
    assert models == expected
    used = {
        enum
        for table in expected.values()
        for _, kind in table.values()
        for enum in _enums_in(kind)
    }
    assert enums == {name: frozenset(dump["enums"][name]) for name in used}


# --- 1. JSON ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b'{"metrics": [], "metrics": []}', "an object repeats the key 'metrics'"),
        (b'{"a": {"b": 1, "b": 1}}', "an object repeats the key 'b'"),
        (b'{"a": NaN}', "NaN is not a JSON number"),
        (b'{"a": Infinity}', "Infinity is not a JSON number"),
        (b'{"a": -Infinity}', "-Infinity is not a JSON number"),
        (b'{"a": 1e400}', "a number does not fit a finite double"),
        (b'{"a": -1e400}', "a number does not fit a finite double"),
        (b'{"a": 9007199254740992}', "an integer is outside the IEEE-754 safe integer range"),
        (b'{"a": -9007199254740992}', "an integer is outside the IEEE-754 safe integer range"),
        (b'{"a": ' + b"7" * 5000 + b"}", "an integer has too many digits"),
        (b'{"a": "\xff"}', "not UTF-8 at byte 7"),
        (b"\xef\xbb\xbf{}", "not JSON: Unexpected UTF-8 BOM (decode using utf-8-sig)"),
        (
            json.dumps({"a": SURROGATE}).encode("ascii"),
            "a string or key holds a lone surrogate escape",
        ),
        (
            json.dumps({"a": [{SURROGATE: 1}]}).encode("ascii"),
            "a string or key holds a lone surrogate escape",
        ),
        (b'{"a": ', "not JSON: Expecting value"),
        (b"[" * 100_000 + b"]" * 100_000, f"nested deeper than {MAX_NESTING} levels"),
        (
            b"[" * (MAX_NESTING + 1) + b"]" * (MAX_NESTING + 1),
            f"nested deeper than {MAX_NESTING} levels",
        ),
    ],
    ids=[
        "repeated-key",
        "repeated-nested-key",
        "nan",
        "infinity",
        "minus-infinity",
        "overflow",
        "minus-overflow",
        "past-safe-int",
        "past-minus-safe-int",
        "past-int-digit-limit",
        "not-utf8",
        "byte-order-mark",
        "lone-surrogate-value",
        "lone-surrogate-key",
        "not-json",
        "past-interpreter-depth",
        "past-nesting-limit",
    ],
)
def test_json_the_reader_would_change_is_rejected(raw: bytes, reason: str) -> None:
    read = read_semantic_manifest(raw)
    assert _problems(read) == [(ARTIFACT_INVALID, "$", reason)]
    assert read.schema_version == ""
    assert read.artifact_digest == digest_bytes(raw)


def _nested(levels: int) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for _ in range(levels - 1):
        value = {"a": value}
    return value


def test_nesting_limit_is_inclusive() -> None:
    # $ (1) > semantic_models (2) > the model (3) > config (4) > meta and what it nests.
    data = _data("wide_legacy")
    _model(data, "stores")["config"] = {"meta": _nested(MAX_NESTING - 4)}
    assert _read(data).problems == ()
    _model(data, "stores")["config"] = {"meta": _nested(MAX_NESTING - 3)}
    assert _problems(_read(data)) == [
        (ARTIFACT_INVALID, "$", f"nested deeper than {MAX_NESTING} levels")
    ]


def test_integer_limit_is_inclusive() -> None:
    data = _data("wide_legacy")
    measure = _metric(data, "revenue_filled")["type_params"]["measure"]
    measure["fill_nulls_with"] = 2**53 - 1
    assert _read(data).problems == ()
    measure["fill_nulls_with"] = -(2**53 - 1)
    assert _read(data).problems == ()


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(description=SURROGATE),
        lambda m: m["config"]["meta"].update({SURROGATE: "x"}),
        lambda m: m["config"]["meta"].update(owner=["x", "y" + SURROGATE]),
    ],
    ids=["value", "meta-key", "meta-value"],
)
def test_lone_surrogate_anywhere_is_invalid(change: Callable[[dict[str, Any]], Any]) -> None:
    data = _data("legacy")
    change(_metric(data, "revenue"))
    assert _problems(_read_escaped(data)) == [
        (ARTIFACT_INVALID, "$", "a string or key holds a lone surrogate escape")
    ]


def test_surrogate_pair_is_one_character_and_one_pin() -> None:
    data = _data("legacy")
    _metric(data, "revenue")["description"] = chr(0x1F600)
    _metric(data, "revenue")["config"]["meta"] = {chr(0x1F600): chr(0x1F600)}
    escaped = json.dumps(data).encode("ascii")
    assert b"\\ud83d\\ude00" in escaped
    digests = {_manifest(data).closure_digest("revenue")}
    read = read_semantic_manifest(escaped)
    assert read.manifest is not None
    digests.add(read.manifest.closure_digest("revenue"))
    assert len(digests) == 1


def test_numbers_are_doubles_as_rfc8785_has_them() -> None:
    data = _data("legacy")
    _metric(data, "revenue")["config"]["meta"] = {"n": "zz_number"}
    raw = json.dumps(data).encode("ascii")
    assert raw.count(b'"zz_number"') == 1

    def digest(number: bytes) -> str:
        read = read_semantic_manifest(raw.replace(b'"zz_number"', number))
        assert read.manifest is not None
        return read.manifest.closure_digest("revenue")

    assert digest(b"1") == digest(b"1.0") == digest(b"1e0") == digest(b"10e-1")
    assert digest(b"0") == digest(b"-0") == digest(b"-0.0")
    assert digest(b"1") != digest(b"2")


# --- 2. Schema version -----------------------------------------------------------------


def _set_version(data: dict[str, Any], value: Any) -> None:
    data["project_configuration"]["dsi_package_version"] = value


def test_unsupported_version_is_the_only_answer() -> None:
    data = _data("v2")
    data["project_configuration"]["dsi_package_version"]["minor_version"] = "214"
    data["unknown"] = "zz_value"
    data["metrics"].append(copy.deepcopy(data["metrics"][0]))
    read = _read(data)
    assert read.schema_version == "0.214.0"
    assert _problems(read) == [
        (
            SCHEMA_UNSUPPORTED,
            "$.project_configuration.dsi_package_version",
            "dsi_package_version 0.214.0 is not one this reader encodes (0.213.0)",
        )
    ]


@pytest.mark.parametrize(
    ("change", "version"),
    [
        (lambda pc: pc.pop("dsi_package_version"), "0.0.0"),
        (lambda pc: pc["dsi_package_version"].update(patch_version=None), "0.213"),
        (lambda pc: pc["dsi_package_version"].pop("patch_version"), "0.213"),
        (lambda pc: pc["dsi_package_version"].update(minor_version="0213"), "0.0213.0"),
        (lambda pc: pc["dsi_package_version"].update(minor_version="123456789"), "0.123456789.0"),
    ],
    ids=["absent", "patch-null", "patch-absent", "leading-zero", "nine-digits"],
)
def test_absent_or_partial_version_is_unsupported(
    change: Callable[[dict[str, Any]], Any], version: str
) -> None:
    data = _data("legacy")
    change(data["project_configuration"])
    read = _read(data)
    assert read.schema_version == version
    assert [problem.code for problem in read.problems] == [SCHEMA_UNSUPPORTED]


VERSION = "$.project_configuration.dsi_package_version"


def _version_parts(**parts: Any) -> Callable[[dict[str, Any]], None]:
    def change(data: dict[str, Any]) -> None:
        _set_version(data, {"patch_version": None, **parts})

    return change


@pytest.mark.parametrize(
    ("change", "path", "reason"),
    [
        (
            lambda d: d.clear() or d.update(x=[]),
            "$.project_configuration",
            "project_configuration is not an object",
        ),
        (lambda d: _set_version(d, "0.213.0"), VERSION, "dsi_package_version is not an object"),
        (lambda d: _set_version(d, None), VERSION, "dsi_package_version is not an object"),
        (
            _version_parts(major_version="0", minor_version=213),
            f"{VERSION}.minor_version",
            "minor_version is not a string of 1 to 9 digits",
        ),
        (
            _version_parts(major_version="0", minor_version="213", patch_version=0),
            f"{VERSION}.patch_version",
            "patch_version is not a string of 1 to 9 digits",
        ),
        (
            _version_parts(minor_version="213"),
            f"{VERSION}.major_version",
            "major_version is not a string of 1 to 9 digits",
        ),
        (
            # Joined, "0.213" and "0" would read as 0.213 with no patch part.
            _version_parts(major_version="0.213", minor_version="0"),
            f"{VERSION}.major_version",
            "major_version is not a string of 1 to 9 digits",
        ),
        (
            # Joined, "0" and "213.0" would read as the supported 0.213.0.
            _version_parts(major_version="0", minor_version="213.0"),
            f"{VERSION}.minor_version",
            "minor_version is not a string of 1 to 9 digits",
        ),
        (
            _version_parts(major_version="0", minor_version="213", patch_version="0\nzz_value"),
            f"{VERSION}.patch_version",
            "patch_version is not a string of 1 to 9 digits",
        ),
        (
            _version_parts(major_version="0", minor_version="213", patch_version="0\n"),
            f"{VERSION}.patch_version",
            "patch_version is not a string of 1 to 9 digits",
        ),
        (
            _version_parts(major_version="0", minor_version="1234567890"),
            f"{VERSION}.minor_version",
            "minor_version is not a string of 1 to 9 digits",
        ),
        (
            _version_parts(major_version="0", minor_version=""),
            f"{VERSION}.minor_version",
            "minor_version is not a string of 1 to 9 digits",
        ),
        (
            _version_parts(major_version="0", minor_version=chr(0x0662) + "13"),
            f"{VERSION}.minor_version",
            "minor_version is not a string of 1 to 9 digits",
        ),
    ],
    ids=[
        "configuration-not-object",
        "version-a-string",
        "version-null",
        "part-an-integer",
        "patch-an-integer",
        "major-absent",
        "dot-in-major",
        "dot-in-minor",
        "text-in-patch",
        "trailing-newline",
        "ten-digits",
        "empty",
        "non-ascii-digit",
    ],
)
def test_malformed_version_is_invalid(
    change: Callable[[dict[str, Any]], Any], path: str, reason: str
) -> None:
    data = _data("legacy")
    change(data)
    read = _read(data)
    assert read.schema_version == ""
    assert _problems(read) == [(ARTIFACT_INVALID, path, reason)]
    assert "zz_value" not in repr(read.problems)


def test_a_document_that_is_not_an_object_is_invalid() -> None:
    assert _problems(_read([])) == [(ARTIFACT_INVALID, "$", "the artifact is not a JSON object")]


# --- 3. Schema -------------------------------------------------------------------------


def _sales_measure(data: dict[str, Any], name: str) -> dict[str, Any]:
    measures = _model(data, "sales_daily")["measures"]
    return measures[_index(measures, name)]


SCHEMA_CASES: list[tuple[str, str, Callable[[dict[str, Any]], Any], str, str]] = [
    (
        "unknown top-level key",
        "wide_legacy",
        lambda d: d.update(extra="zz_value"),
        "$.extra",
        "SemanticManifest has no key 'extra'",
    ),
    (
        "unknown nested key",
        "wide_legacy",
        lambda d: _sales_measure(d, "revenue").update(window="zz_value"),
        "$.semantic_models[0].measures[0].window",
        "Measure has no key 'window'",
    ),
    (
        "required key missing",
        "wide_v2",
        lambda d: _metric(d, "revenue").pop("type_params"),
        "$.metrics[0].type_params",
        "required Metric.type_params is missing",
    ),
    (
        "required key null",
        "wide_v2",
        lambda d: _metric(d, "revenue").update(type=None),
        "$.metrics[0].type",
        "Metric.type may not be null",
    ),
    (
        "string expected",
        "wide_v2",
        lambda d: _metric(d, "revenue").update(label=5),
        "$.metrics[0].label",
        "expected a string",
    ),
    (
        "integer expected, float given",
        "wide_v2",
        lambda d: _metric(d, "revenue_filled")["type_params"].update(fill_nulls_with=1.5),
        "$.metrics[10].type_params.fill_nulls_with",
        "expected an integer",
    ),
    (
        "integer expected, boolean given",
        "wide_v2",
        lambda d: _metric(d, "revenue_filled")["type_params"].update(fill_nulls_with=True),
        "$.metrics[10].type_params.fill_nulls_with",
        "expected an integer",
    ),
    (
        "boolean expected",
        "wide_legacy",
        lambda d: _sales_measure(d, "revenue").update(create_metric=1),
        "$.semantic_models[0].measures[0].create_metric",
        "expected a boolean",
    ),
    (
        "number expected, boolean given",
        "wide_legacy",
        lambda d: _sales_measure(d, "revenue_p90")["agg_params"].update(percentile=True),
        "$.semantic_models[0].measures[7].agg_params.percentile",
        "expected a number",
    ),
    (
        "object expected",
        "wide_legacy",
        lambda d: _model(d, "stores").update(config={"meta": ["zz_value"]}),
        "$.semantic_models[1].config.meta",
        "expected an object",
    ),
    (
        "enum value of another version",
        "wide_legacy",
        lambda d: _sales_measure(d, "revenue").update(agg="zz_value"),
        "$.semantic_models[0].measures[0].agg",
        "not a AggregationType value of this version",
    ),
    (
        "enum value not a string",
        "wide_v2",
        lambda d: _metric(d, "revenue").update(type=1),
        "$.metrics[0].type",
        "not a MetricType value of this version",
    ),
    (
        "list expected",
        "wide_legacy",
        lambda d: _model(d, "sales_daily").update(dimensions={"name": "zz_value"}),
        "$.semantic_models[0].dimensions",
        "expected a list",
    ),
    (
        "list item of a wrong kind",
        "wide_legacy",
        lambda d: _sales_measure(d, "revenue_last")["non_additive_dimension"].update(
            window_groupings=["store", 7]
        ),
        "$.semantic_models[0].measures[10].non_additive_dimension.window_groupings[1]",
        "expected a string",
    ),
    (
        "model object expected",
        "wide_v2",
        lambda d: _metric(d, "revenue").update(type_params=["zz_value"]),
        "$.metrics[0].type_params",
        "expected a MetricTypeParams object",
    ),
    (
        "tags of a wrong kind",
        "wide_v2",
        lambda d: d["saved_queries"].append(
            {"name": "q", "query_params": {"metrics": ["revenue"]}, "tags": [1]}
        ),
        "$.saved_queries[1].tags",
        "expected a string or a list of strings",
    ),
    (
        "defaulted key null",
        "wide_legacy",
        lambda d: _sales_measure(d, "revenue_p90")["agg_params"].update(
            use_discrete_percentile=None
        ),
        "$.semantic_models[0].measures[7].agg_params.use_discrete_percentile",
        "MeasureAggregationParameters.use_discrete_percentile may not be null",
    ),
    (
        "defaulted list null",
        "wide_legacy",
        lambda d: _model(d, "stores").update(measures=None),
        "$.semantic_models[1].measures",
        "SemanticModel.measures may not be null",
    ),
    (
        "unknown key that is not an identifier",
        "wide_legacy",
        lambda d: d.update({"a.b[0]": "zz_value"}),
        '$["a.b[0]"]',
        "SemanticManifest has no key 'a.b[0]'",
    ),
    (
        "metric time granularity not a string",
        "wide_v2",
        lambda d: _metric(d, "revenue_max").update(time_granularity=1),
        "$.metrics[4].time_granularity",
        "expected a string",
    ),
    (
        "custom granularity name not a string",
        "wide_v2",
        lambda d: d["project_configuration"]["time_spines"][0]["custom_granularities"][0].update(
            name=1
        ),
        "$.project_configuration.time_spines[0].custom_granularities[0].name",
        "expected a string",
    ),
    (
        "export destination of another version",
        "wide_v2",
        lambda d: d["saved_queries"][0]["exports"][0]["config"].update(export_as="zz_value"),
        "$.saved_queries[0].exports[0].config.export_as",
        "not a ExportDestinationType value of this version",
    ),
    (
        "export schema under its pydantic alias",
        "wide_v2",
        lambda d: d["saved_queries"][0]["exports"][0]["config"].update(
            schema=d["saved_queries"][0]["exports"][0]["config"].pop("schema_name")
        ),
        "$.saved_queries[0].exports[0].config.schema",
        "ExportConfig has no key 'schema'",
    ),
]


@pytest.mark.parametrize(
    ("kind", "change", "path", "reason"),
    [case[1:] for case in SCHEMA_CASES],
    ids=[case[0] for case in SCHEMA_CASES],
)
def test_schema_mismatch_is_invalid(
    kind: str, change: Callable[[dict[str, Any]], Any], path: str, reason: str
) -> None:
    data = _data(kind)
    change(data)
    read = _read(data)
    assert read.schema_version == "0.213.0"
    assert _problems(read) == [(ARTIFACT_INVALID, path, reason)]
    assert "zz_value" not in repr(read.problems)


def test_every_mismatch_is_reported() -> None:
    data = _data("wide_v2")
    _metric(data, "revenue").update(label=5, extra=1)
    _metric(data, "revenue_min").pop("type")
    assert [problem.path for problem in _read(data).problems] == [
        "$.metrics[0].extra",
        "$.metrics[0].label",
        "$.metrics[3].type",
    ]


@pytest.mark.parametrize(
    ("key", "path"),
    [
        ('a"b', '$["a\\"b"]'),
        ("a\\b", '$["a\\\\b"]'),
        ("a\nb", '$["a\\nb"]'),
        ("1a", '$["1a"]'),
        ("a_1", "$.a_1"),
    ],
    ids=["quote", "backslash", "newline", "leading-digit", "identifier"],
)
def test_a_key_that_is_not_an_identifier_is_a_json_string_in_the_path(key: str, path: str) -> None:
    data = _data("legacy")
    data[key] = 1
    assert _problems(_read(data)) == [
        (ARTIFACT_INVALID, path, f"SemanticManifest has no key {key!r}")
    ]


@pytest.mark.parametrize("percentile", [1, 0.5])
def test_number_takes_integers_and_floats(percentile: float) -> None:
    data = _data("wide_legacy")
    _sales_measure(data, "revenue_p90")["agg_params"]["percentile"] = percentile
    assert _read(data).problems == ()


@pytest.mark.parametrize("tags", ["finance", ["finance", "daily"], []])
def test_tags_take_a_string_or_a_list_of_strings(tags: Any) -> None:
    data = _data("wide_v2")
    data["saved_queries"].append(
        {"name": "q", "query_params": {"metrics": ["revenue"]}, "tags": tags}
    )
    assert _read(data).problems == ()


def test_optional_key_may_be_absent_or_null() -> None:
    data = _data("wide_legacy")
    _model(data, "sales_daily").pop("description")
    _model(data, "stores")["label"] = None
    assert _read(data).problems == ()


def test_defaulted_key_may_be_absent() -> None:
    data = _data("wide_legacy")
    _model(data, "stores").pop("measures")
    _sales_measure(data, "revenue_p90")["agg_params"].pop("use_discrete_percentile")
    data["project_configuration"].pop("time_spine_table_configurations")
    assert _read(data).problems == ()


def test_schema_problems_stop_the_reading() -> None:
    data = _data("wide_v2")
    data["extra"] = 1
    data["metrics"].append(copy.deepcopy(_metric(data, "revenue")))
    assert [problem.path for problem in _read(data).problems] == ["$.extra"]


# --- 4. Names and references ----------------------------------------------------------


def _duplicate_metric(data: dict[str, Any]) -> None:
    data["metrics"].append(copy.deepcopy(_metric(data, "revenue")))


def _duplicate_model(data: dict[str, Any]) -> None:
    data["semantic_models"].append(copy.deepcopy(_model(data, "stores")))


def _duplicate_measure_across_models(data: dict[str, Any]) -> None:
    _model(data, "events")["measures"].append(copy.deepcopy(_sales_measure(data, "revenue")))


def _duplicate_dimension(data: dict[str, Any]) -> None:
    dimensions = _model(data, "sales_daily")["dimensions"]
    dimensions.append(copy.deepcopy(dimensions[_index(dimensions, "region")]))


def _duplicate_entity(data: dict[str, Any]) -> None:
    entities = _model(data, "events")["entities"]
    entities.append(copy.deepcopy(entities[0]))


@pytest.mark.parametrize(
    ("change", "path", "reason"),
    [
        (_duplicate_metric, "$.metrics[16].name", "metric 'revenue' is declared more than once"),
        (
            _duplicate_model,
            "$.semantic_models[3].name",
            "semantic model 'stores' is declared more than once",
        ),
        (
            _duplicate_measure_across_models,
            "$.semantic_models[2].measures[2].name",
            "measure 'revenue' is declared more than once",
        ),
        (
            _duplicate_dimension,
            "$.semantic_models[0].dimensions[3].name",
            "dimension 'region' is declared more than once",
        ),
        (
            _duplicate_entity,
            "$.semantic_models[2].entities[1].name",
            "entity 'user' is declared more than once",
        ),
    ],
)
def test_repeated_names_are_invalid(
    change: Callable[[dict[str, Any]], None], path: str, reason: str
) -> None:
    data = _data("wide_legacy")
    change(data)
    assert _problems(_read(data)) == [(ARTIFACT_INVALID, path, reason)]


def test_a_name_may_repeat_across_semantic_models() -> None:
    data = _data("wide_legacy")
    events = _model(data, "events")
    events["dimensions"].append(copy.deepcopy(_model(data, "sales_daily")["dimensions"][1]))
    events["entities"].append(copy.deepcopy(_model(data, "stores")["entities"][0]))
    assert _read(data).problems == ()


def test_repeated_names_stop_the_reading() -> None:
    data = _data("wide_v2")
    _duplicate_metric(data)
    _metric(data, "revenue_growth")["type_params"]["metrics"][0]["name"] = "absent"
    assert [problem.reason for problem in _read(data).problems] == [
        "metric 'revenue' is declared more than once"
    ]


def _refer(path: list[Any], name: str) -> Callable[[dict[str, Any]], None]:
    """Set the key at `path` to `name`; a path that starts with `None` starts at the root."""

    def change(data: dict[str, Any]) -> None:
        target: Any = data if path[0] is None else _metric(data, path[0])
        for step in path[1:-1]:
            target = target[step]
        target[path[-1]] = name

    return change


CONVERSION = ["visit_to_purchase", "type_params", "conversion_type_params"]

REFERENCE_CASES: list[tuple[str, Callable[[dict[str, Any]], None], str, str]] = [
    (
        "legacy",
        _refer(["revenue", "type_params", "measure", "name"], "absent"),
        "$.metrics[0].type_params.measure.name",
        "refers to measure 'absent', which is absent",
    ),
    (
        "legacy",
        _refer(["revenue", "type_params", "input_measures", 0, "name"], "absent"),
        "$.metrics[0].type_params.input_measures[0].name",
        "refers to measure 'absent', which is absent",
    ),
    (
        "wide_legacy",
        _refer([*CONVERSION, "base_measure", "name"], "x"),
        "$.metrics[14].type_params.conversion_type_params.base_measure.name",
        "refers to measure 'x', which is absent",
    ),
    (
        "wide_legacy",
        _refer(
            [*CONVERSION, "conversion_measure", "name"],
            "x",
        ),
        "$.metrics[14].type_params.conversion_type_params.conversion_measure.name",
        "refers to measure 'x', which is absent",
    ),
    (
        "wide_v2",
        _refer(["margin_pct_north", "type_params", "numerator", "name"], "x"),
        "$.metrics[12].type_params.numerator.name",
        "refers to metric 'x', which is absent",
    ),
    (
        "wide_v2",
        _refer(["margin_pct_north", "type_params", "denominator", "name"], "x"),
        "$.metrics[12].type_params.denominator.name",
        "refers to metric 'x', which is absent",
    ),
    (
        "wide_v2",
        _refer(["revenue_growth", "type_params", "metrics", 1, "name"], "x"),
        "$.metrics[13].type_params.metrics[1].name",
        "refers to metric 'x', which is absent",
    ),
    (
        "wide_v2",
        _refer(["revenue_mtd", "type_params", "cumulative_type_params", "metric", "name"], "x"),
        "$.metrics[14].type_params.cumulative_type_params.metric.name",
        "refers to metric 'x', which is absent",
    ),
    (
        "wide_v2",
        _refer([*CONVERSION, "base_metric", "name"], "x"),
        "$.metrics[17].type_params.conversion_type_params.base_metric.name",
        "refers to metric 'x', which is absent",
    ),
    (
        "wide_v2",
        _refer(
            [*CONVERSION, "conversion_metric", "name"],
            "x",
        ),
        "$.metrics[17].type_params.conversion_type_params.conversion_metric.name",
        "refers to metric 'x', which is absent",
    ),
    (
        "wide_v2",
        _refer(["revenue", "type_params", "metric_aggregation_params", "semantic_model"], "x"),
        "$.metrics[0].type_params.metric_aggregation_params.semantic_model",
        "refers to semantic model 'x', which is absent",
    ),
    (
        "wide_v2",
        # `ds` is a dimension of `events`, not of the semantic model the metric aggregates.
        _refer(["revenue", "type_params", "metric_aggregation_params", "agg_time_dimension"], "ds"),
        "$.metrics[0].type_params.metric_aggregation_params.agg_time_dimension",
        "refers to dimension 'ds', which is absent",
    ),
    (
        "wide_legacy",
        _refer([None, "semantic_models", 0, "defaults", "agg_time_dimension"], "ds"),
        "$.semantic_models[0].defaults.agg_time_dimension",
        "refers to dimension 'ds', which is absent",
    ),
    (
        "wide_legacy",
        _refer([None, "semantic_models", 0, "measures", 0, "agg_time_dimension"], "ds"),
        "$.semantic_models[0].measures[0].agg_time_dimension",
        "refers to dimension 'ds', which is absent",
    ),
]


@pytest.mark.parametrize(("kind", "change", "path", "reason"), REFERENCE_CASES)
def test_broken_reference_is_invalid(
    kind: str, change: Callable[[dict[str, Any]], None], path: str, reason: str
) -> None:
    data = _data(kind)
    change(data)
    assert _problems(_read(data)) == [(ARTIFACT_INVALID, path, reason)]


@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("wide_legacy", _refer([*CONVERSION, "entity"], "absent")),
        (
            "wide_v2",
            lambda d: _metric(d, "visit_to_purchase")["type_params"][
                "conversion_type_params"
            ].update(constant_properties=[{"base_property": "x", "conversion_property": "x"}]),
        ),
        (
            "wide_legacy",
            lambda d: _sales_measure(d, "revenue_last")["non_additive_dimension"].update(
                name="absent", window_groupings=["absent"]
            ),
        ),
        ("wide_v2", _refer(["revenue_max", "time_granularity"], "absent")),
        (
            "wide_v2",
            _refer(["revenue_growth", "type_params", "metrics", 1, "offset_to_grain"], "absent"),
        ),
        ("wide_v2", _refer([None, "semantic_models", 0, "primary_entity"], "absent")),
        (
            "v2",
            _refer(
                ["revenue_cum", "type_params", "cumulative_type_params", "window", "granularity"],
                "absent",
            ),
        ),
        (
            "wide_v2",
            _refer(
                ["revenue_mtd", "type_params", "cumulative_type_params", "grain_to_date"], "absent"
            ),
        ),
        ("wide_v2", lambda d: d["saved_queries"][0]["query_params"].update(metrics=["absent"])),
    ],
    ids=[
        "conversion-entity",
        "constant-properties",
        "non-additive-dimension",
        "time-granularity",
        "offset-to-grain",
        "primary-entity",
        "window-granularity",
        "grain-to-date",
        "saved-query-metric",
    ],
)
def test_references_left_to_later_slices_are_not_resolved(
    kind: str, change: Callable[[dict[str, Any]], None]
) -> None:
    # Module docstring, step 4: a formula with one of these is a construct ADR 0008, item 8
    # does not resolve, and saved queries are in no formula.
    data = _data(kind)
    change(data)
    assert _read(data).problems == ()


def test_broken_references_stop_the_reading() -> None:
    data = _data("wide_v2")
    params = _metric(data, "revenue_growth")["type_params"]
    params["metrics"][0]["name"] = "revenue_growth"
    params["metrics"][1]["name"] = "absent"
    assert [problem.reason for problem in _read(data).problems] == [
        "refers to metric 'absent', which is absent"
    ]


def test_metric_that_takes_itself_as_input_is_invalid() -> None:
    data = _data("wide_v2")
    _metric(data, "revenue_growth")["type_params"]["metrics"][1]["name"] = "revenue_growth"
    assert _problems(_read(data)) == [
        (
            ARTIFACT_INVALID,
            "$.metrics[13]",
            "metric 'revenue_growth' depends on itself through its inputs",
        )
    ]


def test_only_metrics_that_depend_on_themselves_are_named() -> None:
    data = _data("wide_v2")
    # margin_pct_north -> revenue_growth -> margin_pct_north; revenue_mtd only takes one.
    _metric(data, "margin_pct_north")["type_params"]["denominator"]["name"] = "revenue_growth"
    _metric(data, "revenue_growth")["type_params"]["metrics"][0]["name"] = "margin_pct_north"
    cumulative = _metric(data, "revenue_mtd")["type_params"]["cumulative_type_params"]
    cumulative["metric"]["name"] = "margin_pct_north"
    assert [problem.reason for problem in _read(data).problems] == [
        "metric 'margin_pct_north' depends on itself through its inputs",
        "metric 'revenue_growth' depends on itself through its inputs",
    ]


#: Longer than Python's default recursion limit.
LONG = 5000


@pytest.mark.parametrize(
    ("step", "named"),
    [(-1, 0), (1, LONG - 1)],
    ids=["inputs-already-seen", "inputs-not-yet-seen"],
)
def test_a_long_chain_with_one_self_input_names_only_that_metric(step: int, named: int) -> None:
    # m<named> takes itself; every other m<i> takes m<i+step>. With step 1 the chain is
    # deeper than Python's default recursion limit; a search from each metric would walk it
    # once per metric either way.
    data = _data("legacy")
    first = len(data["metrics"])
    for index in range(LONG):
        _derive(data, f"m{index}", f"m{index if index == named else index + step}")
    assert _problems(_read(data)) == [
        (
            ARTIFACT_INVALID,
            f"$.metrics[{first + named}]",
            f"metric 'm{named}' depends on itself through its inputs",
        )
    ]


class _CountingInputs(Mapping[str, Sequence[str]]):
    """Inputs that count how often a metric's list is fetched, how many names are read and how
    often a name is hashed or compared — a copy of the lists still holds the same counted names."""

    def __init__(self, inputs: dict[str, list[str]]) -> None:
        self.operations = 0
        self.budget: float = math.inf
        self._inputs = {
            _CountedName(name, self): _CountingNames(self, [_CountedName(n, self) for n in names])
            for name, names in inputs.items()
        }
        self.lists = 0
        self.names = 0

    def __getitem__(self, name: str) -> _CountingNames:
        self.lists += 1
        return self._inputs[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._inputs)

    def __len__(self) -> int:
        return len(self._inputs)


class _CountingNames(Sequence[str]):
    def __init__(self, owner: _CountingInputs, names: list[str]) -> None:
        self._owner = owner
        self._names = names

    def __getitem__(self, index: Any) -> Any:
        self._owner.names += 1
        return self._names[index]

    def __len__(self) -> int:
        return len(self._names)

    def __iter__(self) -> Iterator[str]:
        for name in self._names:
            self._owner.names += 1
            yield name

    def __contains__(self, value: object) -> bool:
        return any(name == value for name in self)


class _CountedName(str):
    """A metric name that counts each time it is hashed (a lookup in a dict or set) or compared
    (a scan of a list, or a lookup that meets an equal hash), and fails the test as soon as the
    count passes the owner's budget: a walk that hashes or compares names a quadratic number of
    times ends early. Quadratic work on anything other than these names is not seen."""

    owner: _CountingInputs

    def __new__(cls, value: str, owner: _CountingInputs) -> _CountedName:
        name = super().__new__(cls, value)
        name.owner = owner
        return name

    def _spend(self) -> None:
        self.owner.operations += 1
        if self.owner.operations > self.owner.budget:
            raise AssertionError(f"more than {self.owner.budget} hashes and comparisons of names")

    def __hash__(self) -> int:
        self._spend()
        return str.__hash__(self)

    def __eq__(self, other: object) -> bool:
        self._spend()
        return str.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        self._spend()
        return str.__ne__(self, other)


@pytest.mark.parametrize(
    ("inputs", "found"),
    [
        ({"m0": ["m0"], **{f"m{i}": [f"m{i - 1}"] for i in range(1, LONG)}}, {"m0"}),
        (
            {f"m{i}": [f"m{i + 1}"] for i in range(LONG - 1)} | {f"m{LONG - 1}": [f"m{LONG - 1}"]},
            {f"m{LONG - 1}"},
        ),
        ({f"m{i}": [f"m{i + 1}"] for i in range(LONG - 1)} | {f"m{LONG - 1}": []}, set()),
        ({f"m{i}": [f"m{(i + 1) % LONG}"] for i in range(LONG)}, {f"m{i}" for i in range(LONG)}),
        ({"a": ["b"], "b": ["a"], "c": ["a", "c"], "d": ["c"], "e": ["d", "a"]}, {"a", "b", "c"}),
    ],
    ids=["self-input-then-chain", "chain-then-self-input", "chain", "one-ring", "mixed"],
)
def test_self_dependence_reads_each_input_list_once_without_recursion(
    inputs: dict[str, list[str]], found: set[str]
) -> None:
    edges = sum(len(names) for names in inputs.values())
    # A dict or set compares only names whose hashes are equal, so with distinct hashes a lookup
    # compares at most once; colliding names can be compared more and invalidate the budget.
    assert len({hash(name) for name in inputs}) == len(inputs)
    counting = _CountingInputs(inputs)
    # A metric is looked up at most 12 times in the walk's tables (entered, finished, popped,
    # named) and an input at most 5 times (seen, on the stack, lowering its parent). A lookup
    # that finds an equal name compares it once; popping the stack compares each metric once and
    # testing a self-input each input once. A search from every metric, even over copied lists,
    # or a stack kept as a list, hashes or compares a name on each step.
    lookups = 12 * len(inputs) + 5 * edges
    counting.operations = 0
    counting.budget = 2 * lookups + len(inputs) + edges
    result = _depends_on_itself(counting)
    counting.budget = math.inf
    assert result == found
    assert counting.lists == len(inputs)
    # Every input once while walking, and at most once more to test a component's root.
    assert counting.names <= 2 * edges


# --- Closure ---------------------------------------------------------------------------


CLOSURES = [
    ("legacy", "revenue", ["revenue"], ["sales_daily"]),
    ("legacy", "revenue_cum", ["revenue_cum"], ["sales_daily"]),
    (
        "legacy",
        "gross_margin_pct",
        ["gross_margin_pct", "gross_margin_total", "revenue"],
        ["sales_daily"],
    ),
    (
        "legacy",
        "margin_derived",
        ["gross_margin_total", "margin_derived", "revenue"],
        ["sales_daily"],
    ),
    ("legacy", "revenue_north", ALL, ALL),
    ("v2", "revenue_cum", ["revenue", "revenue_cum"], ["sales_daily"]),
    ("v2", "revenue_north", ALL, ALL),
    ("wide_legacy", "revenue_growth", ["revenue", "revenue_growth"], ["sales_daily"]),
    ("wide_legacy", "gross_margin_total", ["gross_margin_total"], ["sales_daily"]),
    ("wide_legacy", "margin_pct_north", ALL, ALL),
    ("wide_legacy", "visit_to_purchase", ALL, ALL),
    ("wide_v2", "visits", ["visits"], ["events"]),
    ("wide_v2", "revenue_mtd", ["revenue", "revenue_mtd"], ["sales_daily"]),
    ("wide_v2", "revenue_hidden", ["revenue_hidden"], ["sales_daily"]),
    ("wide_v2", "margin_pct_north", ALL, ALL),
    ("wide_v2", "visit_to_purchase", ALL, ALL),
]


@pytest.mark.parametrize(("kind", "metric", "metrics", "models"), CLOSURES)
def test_closure_members(kind: str, metric: str, metrics: Any, models: Any) -> None:
    data = _data(kind)
    manifest = _manifest(data)
    expected_metrics = sorted(m["name"] for m in data["metrics"]) if metrics == ALL else metrics
    every_model = sorted(m["name"] for m in data["semantic_models"])
    expected_models = every_model if models == ALL else models
    assert _names(manifest, metric) == (expected_metrics, expected_models)


def test_closure_holds_the_objects_as_the_artifact_has_them() -> None:
    data = _data("wide_legacy")
    closure = _manifest(data).closure("revenue_growth")
    assert closure == {
        "source": SOURCE,
        "metric": "revenue_growth",
        "project_configuration": data["project_configuration"],
        "metrics": [_metric(data, "revenue"), _metric(data, "revenue_growth")],
        "semantic_models": [_model(data, "sales_daily")],
    }
    assert None in closure["metrics"][0]["type_params"]["measure"].values()


def test_closure_digest_does_not_depend_on_formatting() -> None:
    data = _data("wide_v2")
    pretty = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")

    def reversed_keys(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: reversed_keys(value[key]) for key in reversed(list(value))}
        if isinstance(value, list):
            return [reversed_keys(item) for item in value]
        return value

    reordered = json.dumps(reversed_keys(data), separators=(",", ":")).encode("utf-8")
    reads = [read_semantic_manifest(raw) for raw in (_raw("wide_v2"), pretty, reordered)]
    assert len({read.artifact_digest for read in reads}) == 3
    manifests: list[SemanticManifest] = []
    for read in reads:
        assert read.manifest is not None
        manifests.append(read.manifest)
    for metric in manifests[0].metric_names():
        assert len({manifest.closure_digest(metric) for manifest in manifests}) == 1


def _sorted_json_digest(value: Any) -> str:
    # For these artifacts (ASCII keys, shortest-repr floats) RFC 8785 is sorted compact JSON.
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("kind", "metric", "metrics", "models", "pinned"),
    [
        (
            "legacy",
            "revenue",
            ["revenue"],
            ["sales_daily"],
            "sha256:5a4ee6789f6efc7dc2e26c2e838d78cdf06954af5889c2d687bd5cc364786e40",
        ),
        (
            "legacy",
            "gross_margin_pct",
            ["gross_margin_pct", "gross_margin_total", "revenue"],
            ["sales_daily"],
            "sha256:318854cc18766c041ff79d50c440841402e225ef54bd64521e6178be2e29b2db",
        ),
        ("wide_legacy", "revenue_p90", ["revenue_p90"], ["sales_daily"], None),
    ],
)
def test_closure_digest_is_the_rfc8785_digest_of_the_closure(
    kind: str, metric: str, metrics: list[str], models: list[str], pinned: str | None
) -> None:
    data = _data(kind)
    expected = {
        "source": "dbt_semantic_manifest",
        "metric": metric,
        "project_configuration": data["project_configuration"],
        "metrics": [_metric(data, name) for name in metrics],
        "semantic_models": [_model(data, name) for name in models],
    }
    digest = _manifest(data).closure_digest(metric)
    assert digest == _sorted_json_digest(expected)
    if pinned is not None:
        assert digest == pinned


def _digests(data: dict[str, Any]) -> dict[str, str]:
    manifest = _manifest(data)
    return {name: manifest.closure_digest(name) for name in manifest.metric_names()}


def _moved(kind: str, change: Callable[[dict[str, Any]], Any]) -> set[str]:
    data = _data(kind)
    before = _digests(data)
    change(data)
    after = _digests(data)
    return {name for name in before if before[name] != after[name]}


def test_a_metric_moves_its_own_digest_and_its_dependants() -> None:
    moved = _moved("legacy", lambda d: _metric(d, "gross_margin_total").update(description="x"))
    assert moved == {"gross_margin_total", "gross_margin_pct", "margin_derived", "revenue_north"}


def test_project_configuration_moves_every_digest() -> None:
    def change(data: dict[str, Any]) -> None:
        data["project_configuration"]["time_spines"][0]["primary_column"]["name"] = "day"

    assert _moved("v2", change) == {metric["name"] for metric in _data("v2")["metrics"]}


def test_legacy_measure_moves_every_metric_of_its_semantic_model() -> None:
    # Legacy metrics read measures, and a closure takes semantic models whole.
    moved = _moved("wide_legacy", lambda d: _sales_measure(d, "revenue_min").update(expr="x"))
    assert moved == {metric["name"] for metric in _data("wide_legacy")["metrics"]}


def test_v2_metric_does_not_move_its_siblings() -> None:
    moved = _moved("wide_v2", lambda d: _metric(d, "revenue_min").update(description="x"))
    assert moved == {"revenue_min", "margin_pct_north", "visit_to_purchase"}


def test_semantic_model_outside_the_closure_does_not_move_it() -> None:
    moved = _moved("legacy", lambda d: _model(d, "stores").update(description="x"))
    assert moved == {"revenue_north"}


def _derive(data: dict[str, Any], name: str, source: str) -> None:
    """Add a derived metric `name` whose one input is `source`."""
    derived = copy.deepcopy(_metric(data, "margin_derived"))
    derived["name"] = name
    derived["type_params"]["metrics"] = [{**derived["type_params"]["metrics"][0], "name": source}]
    data["metrics"].append(derived)


def test_closure_follows_inputs_at_every_depth() -> None:
    data = _data("legacy")
    _derive(data, "pct_share", "gross_margin_pct")
    _derive(data, "pct_share_share", "pct_share")
    assert _names(_manifest(data), "pct_share_share") == (
        ["gross_margin_pct", "gross_margin_total", "pct_share", "pct_share_share", "revenue"],
        ["sales_daily"],
    )


NORTH = {"where_filters": [{"where_sql_template": "{{ Dimension('sale__region') }} = 'north'"}]}


@pytest.mark.parametrize(
    ("kind", "metric", "slot"),
    [
        ("legacy", "revenue", ["measure"]),
        ("legacy", "revenue", ["input_measures", 0]),
        ("v2", "gross_margin_pct", ["denominator"]),
        ("wide_v2", "revenue_growth", ["metrics", 1]),
        ("wide_v2", "revenue_mtd", ["cumulative_type_params", "metric"]),
    ],
)
def test_a_filter_on_any_input_takes_the_whole_artifact(
    kind: str, metric: str, slot: list[Any]
) -> None:
    data = _data(kind)
    target = _metric(data, metric)["type_params"]
    for step in slot:
        target = target[step]
    target["filter"] = NORTH
    assert _names(_manifest(data), metric) == (
        sorted(m["name"] for m in data["metrics"]),
        sorted(m["name"] for m in data["semantic_models"]),
    )


def test_filter_on_a_deeper_input_takes_the_whole_artifact() -> None:
    data = _data("legacy")
    _derive(data, "north_share", "revenue_north")
    _derive(data, "north_share_share", "north_share")
    metrics, models = _names(_manifest(data), "north_share_share")
    assert metrics == sorted(m["name"] for m in data["metrics"])
    assert models == ["sales_daily", "stores"]


def test_saved_queries_are_never_in_a_closure() -> None:
    def change(data: dict[str, Any]) -> None:
        data["saved_queries"].append({"name": "q", "query_params": {"metrics": ["revenue"]}})

    assert _moved("wide_v2", change) == set()


def test_list_order_inside_an_object_moves_the_pin() -> None:
    # Module docstring: lists inside an object keep the artifact's order.
    moved = _moved("legacy", lambda d: _model(d, "sales_daily")["dimensions"].reverse())
    assert "revenue" in moved


def test_what_metric_and_closure_return_is_a_copy() -> None:
    data = _data("wide_legacy")
    manifest = _manifest(data)
    digest = manifest.closure_digest("revenue_growth")
    closure = manifest.closure("revenue_growth")
    closure["metrics"][0]["type_params"]["measure"]["name"] = "absent"
    closure["semantic_models"][0]["dimensions"].clear()
    closure["project_configuration"]["time_spines"][0]["primary_column"]["name"] = "x"
    metric = manifest.metric("revenue")
    metric["type_params"]["input_measures"][0]["name"] = "absent"
    metric["config"]["meta"]["x"] = 1
    assert manifest.closure_digest("revenue_growth") == digest
    assert manifest.metric("revenue") == _metric(data, "revenue")
    assert manifest.closure("revenue_growth") == _manifest(data).closure("revenue_growth")


def test_unknown_metric_is_a_key_error() -> None:
    manifest = _manifest(_data("legacy"))
    assert manifest.has_metric("revenue")
    assert not manifest.has_metric("absent")
    for call in (manifest.closure, manifest.closure_digest, manifest.metric):
        with pytest.raises(KeyError):
            call("absent")
