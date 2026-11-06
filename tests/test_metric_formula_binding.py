"""A dbt-defined formula bound in the metric node (ADR 0008, slice S3).

The snapshot resolves the formula of a `dbt_semantic_manifest` contract from the artifact it is
given and keeps the binding in the node; the diff names a moved binding; the gate compares a
bound metric with its validated contract by the declaration; a native node is written as source
version 3 wrote it. Artifacts are the fixtures of `test_metricflow_manifest.py` (`dbt parse`,
dbt-core 1.12.6, metricflow 0.213.0), changed in memory where a test needs it.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.decision.impact import ChartRelation, resolve_impact
from auto_bi.decision.validate import load_contracts
from auto_bi.evidence.canonical import digest_bytes, sha256_digest
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.diff import ChangeKind, diff_snapshots
from auto_bi.semantic.gateway import (
    NATIVE_SOURCE_VERSION,
    FormulaBinding,
    MetricNode,
    NativeModelSource,
    SemanticSnapshot,
    declared_node,
    metric_node,
)
from auto_bi.semantic.metric_contract import MetricContract, MetricFormula
from auto_bi.semantic.metricflow import read_semantic_manifest
from auto_bi.semantic.metricflow_formula import DeclaredTable, resolve_formula
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    DataClassification,
    Physical,
    SemanticModel,
    Table,
)
from auto_bi.severity import Severity
from auto_bi.verification.release_gate import (
    CHECK_VERSION,
    VERIFIER_VERSION,
    CheckStatus,
    GateInputs,
    run_gate,
)
from auto_bi.verification.repository import VerifiedAsset, VerifiedAssetRepository
from auto_bi.verification.sql_contract import compile_metric_queries
from tests.test_bi_contract_validation import REF, spec
from tests.test_decision_chart_impact import DASHBOARD, both_products, dashboard, impact_of
from tests.test_decision_verify import _check_payload, status_of
from tests.test_verified_assets import asset

FIXTURES = Path(__file__).parent / "fixtures"
MANIFESTS = FIXTURES / "dbt_semantic_manifest"
PRODUCT = FIXTURES / "decision" / "weekly_margin.yaml"
#: What source version 3 (`f904584`) wrote for `_contract(formula=NATIVE_RATIO)` and for an
#: external formula of another source, each without an artifact.
SNAPSHOT_V3 = FIXTURES / "semantic_snapshot_v3.json"
ID = "metric://sales/gross_margin_pct"
PIN = f"{ID}@3"
FORMULA = f"metrics.{ID}.formula"
TABLE = "dm.sales_daily"
SOURCE = "dbt_semantic_manifest"
UNPINNED = "sha256:" + "0" * 64
#: What a version 3 node holds, key for key.
V3_KEYS = {
    "id",
    "version",
    "owner",
    "status",
    "description",
    "formula",
    "grain",
    "time",
    "unit",
    "additivity",
    "non_additive_axes",
    "dimensions",
    "join_paths",
    "classification",
    "policy_refs",
    "fields",
    "budgets",
    "freshness",
}


def _data(kind: str = "legacy") -> dict[str, Any]:
    data: dict[str, Any] = json.loads((MANIFESTS / kind / "semantic_manifest.json").read_bytes())
    return data


def _bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _pin(data: dict[str, Any], ref: str) -> str:
    read = read_semantic_manifest(_bytes(data))
    assert read.manifest is not None, read.problems
    return read.manifest.closure_digest(ref)


def _model(
    engine: str = "postgres", *, revenue: tuple[str, ...] = ("numeric(18,2)",)
) -> SemanticModel:
    """The pilot mart as PostgreSQL declares it; each type in *revenue* declares the column."""
    columns = [
        Column(name="date", type="date", role=ColumnRole.TIME),
        Column(name="store_id", type="integer", role=ColumnRole.DIMENSION),
        Column(name="region", type="text", role=ColumnRole.DIMENSION),
        Column(name="category", type="text", role=ColumnRole.DIMENSION),
        *(
            Column(name="revenue", type=kind, role=ColumnRole.MEASURE, agg=Aggregation.SUM)
            for kind in revenue
        ),
        Column(
            name="gross_margin", type="numeric(18,2)", role=ColumnRole.MEASURE, agg=Aggregation.SUM
        ),
    ]
    return SemanticModel(
        tables=[
            Table(
                name=TABLE,
                grain=["date", "store_id"],
                classification=DataClassification.INTERNAL,
                columns=columns,
                physical=Physical(engine=engine),
            )
        ]
    )


def _contract(
    ref: str = "gross_margin_pct",
    pin: str | None = None,
    *,
    source: str = SOURCE,
    formula: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if formula is None:
        formula = {
            "kind": "external_ref",
            "external": {
                "source": source,
                "ref": ref,
                "source_revision": pin if pin is not None else _pin(_data(), ref),
            },
        }
    return {
        "api_version": "auto-bi.io/v2alpha1",
        "kind": "MetricContract",
        "metadata": {
            "id": ID,
            "version": 3,
            "owner": "commercial-analytics",
            "status": "active",
        },
        "formula": formula,
        "grain": {"table": TABLE, "keys": ["date", "store_id"]},
        "dimensions": ["region", "category"],
        "additivity": "non_additive",
        "time": {"event_time_column": "date", "timezone": "Europe/Moscow"},
        "unit": {
            "unit": "ratio",
            "precision": 6,
            "null_semantics": "null_is_missing",
            "zero_semantics": "zero_is_value",
        },
        "freshness": {"max_age": "PT6H"},
        "classification": "internal",
        "policy_refs": ["policy://release/default@1"],
    }


NATIVE_RATIO = {
    "kind": "typed_ratio",
    "numerator": {"table": TABLE, "column": "gross_margin", "agg": "sum"},
    "denominator": {"table": TABLE, "column": "revenue", "agg": "sum"},
}


def _snapshot(
    folder: Path,
    contract: dict[str, Any],
    artifact: dict[str, Any] | bytes | None = None,
    *,
    database: str | None = "x",
    model: SemanticModel | None = None,
) -> SemanticSnapshot:
    folder.mkdir(parents=True)
    # LF on every platform: the stored version 3 payload hashes the bytes of LF files
    (model or _model()).dump(folder / "model.yaml")
    written = (folder / "model.yaml").read_bytes()
    (folder / "model.yaml").write_bytes(written.replace(b"\r\n", b"\n"))
    (folder / "metrics").mkdir()
    (folder / "metrics" / "gross_margin_pct.yaml").write_bytes(
        yaml.safe_dump(contract, allow_unicode=True).encode("utf-8")
    )
    manifest = None
    if artifact is not None:
        manifest = folder / "semantic_manifest.json"
        manifest.write_bytes(artifact if isinstance(artifact, bytes) else _bytes(artifact))
    return NativeModelSource(
        folder / "model.yaml",
        folder / "metrics",
        semantic_manifest=manifest,
        dbt_database=database,
    ).snapshot()


def _gaps(snapshot: SemanticSnapshot) -> list[tuple[str, str, str, str]]:
    return [(gap.code, gap.severity.value, gap.path, gap.source_value_ref) for gap in snapshot.gaps]


def _opaque(contract: dict[str, Any]) -> tuple[str, str, str, str]:
    formula = MetricFormula.model_validate(contract["formula"])
    return (
        "gap.opaque_metric_formula",
        "review",
        FORMULA,
        sha256_digest(formula.model_dump(mode="json")),
    )


def _changes(diff: Any) -> list[tuple[str, ChangeKind, Severity, str, list[str], str]]:
    return [
        (c.code, c.kind, c.severity, c.subject, c.impact.metrics, c.detail) for c in diff.changes
    ]


# --- the node --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "formula", "fields"),
    [
        (
            "gross_margin_pct",
            NATIVE_RATIO,
            ["date", "gross_margin", "revenue", "store_id"],
        ),
        (
            "revenue",
            {
                "kind": "typed_aggregate",
                "aggregate": {"table": TABLE, "column": "revenue", "agg": "sum"},
            },
            ["date", "revenue", "store_id"],
        ),
    ],
)
def test_a_resolved_formula_is_typed_and_bound(
    tmp_path: Path, ref: str, formula: dict[str, Any], fields: list[str]
) -> None:
    pin = _pin(_data(), ref)
    snapshot = _snapshot(tmp_path / "s", _contract(ref, pin), _data())
    node = snapshot.graph.metrics[0]
    assert node.formula == MetricFormula.model_validate(formula)
    assert node.binding == FormulaBinding(
        source=SOURCE, ref=ref, source_revision=pin, closure_digest=pin
    )
    assert node.fields == [f"{TABLE}.{name}" for name in fields]
    assert snapshot.gaps == []
    assert snapshot.source_version == NATIVE_SOURCE_VERSION == "4"
    dumped = node.model_dump(mode="json")
    assert set(dumped) == V3_KEYS | {"binding"}
    assert dumped["binding"] == {
        "source": SOURCE,
        "ref": ref,
        "source_revision": pin,
        "closure_digest": pin,
    }


def test_without_an_artifact_the_formula_is_declared_and_blocks(tmp_path: Path) -> None:
    contract = _contract()
    snapshot = _snapshot(tmp_path / "s", contract)
    node = snapshot.graph.metrics[0]
    assert node.formula == MetricFormula.model_validate(contract["formula"])
    assert node.binding is not None and node.binding.closure_digest is None
    assert node.fields == [f"{TABLE}.date", f"{TABLE}.store_id"]
    assert _gaps(snapshot) == [
        _opaque(contract),
        ("gap.external_source_missing", "block", FORMULA, ""),
    ]


def test_a_refused_artifact_is_its_problems_and_bytes_the_snapshot_read(tmp_path: Path) -> None:
    contract = _contract()
    refused = _snapshot(tmp_path / "refused", contract, b"{")
    missing = _snapshot(tmp_path / "missing", contract)
    assert _gaps(refused) == [
        _opaque(contract),
        ("gap.external_artifact_invalid", "block", f"{FORMULA}:$", ""),
    ]
    node = refused.graph.metrics[0]
    assert node.binding is not None and node.binding.closure_digest is None
    # one graph, two sources: the refused bytes are in the source digest
    assert refused.canonical_digest == missing.canonical_digest
    assert refused.source_digest != missing.source_digest


def test_a_metric_the_artifact_lacks_is_ref_missing(tmp_path: Path) -> None:
    contract = _contract("nowhere", UNPINNED)
    snapshot = _snapshot(tmp_path / "s", contract, _data())
    assert _gaps(snapshot) == [
        _opaque(contract),
        ("gap.external_ref_missing", "block", f"{FORMULA}:$.metrics", ""),
    ]
    node = snapshot.graph.metrics[0]
    assert node.binding is not None and node.binding.closure_digest is None


def test_another_pin_binds_the_closure_the_artifact_gave(tmp_path: Path) -> None:
    contract = _contract(pin=UNPINNED)
    snapshot = _snapshot(tmp_path / "s", contract, _data())
    actual = _pin(_data(), "gross_margin_pct")
    node = snapshot.graph.metrics[0]
    assert node.formula.kind.value == "external_ref"
    assert node.binding == FormulaBinding(
        source=SOURCE, ref="gross_margin_pct", source_revision=UNPINNED, closure_digest=actual
    )
    assert _gaps(snapshot) == [
        _opaque(contract),
        ("gap.external_revision_mismatch", "block", FORMULA, actual),
    ]


@pytest.mark.parametrize("kind", ["legacy", "wide_legacy", "v2", "wide_v2"])
@pytest.mark.parametrize("database", ["x", None, "y"])
def test_the_node_is_the_resolution(tmp_path: Path, kind: str, database: str | None) -> None:
    """Every metric of every fixture: the node's formula and gaps are what the resolver says."""
    data = _data(kind)
    read = read_semantic_manifest(_bytes(data))
    assert read.manifest is not None
    declared = DeclaredTable(
        "postgres",
        {
            "date": "date",
            "store_id": "integer",
            "region": "text",
            "category": "text",
            "revenue": "numeric(18,2)",
            "gross_margin": "numeric(18,2)",
        },
    )
    for name in read.manifest.metric_names():
        contract = _contract(name, read.manifest.closure_digest(name))
        resolution = resolve_formula(
            MetricContract.model_validate(contract), read, database=database, declared=declared
        )
        snapshot = _snapshot(
            tmp_path / kind / name / str(database), contract, data, database=database
        )
        node = snapshot.graph.metrics[0]
        external = [
            (
                gap.code,
                gap.severity.value,
                FORMULA + (f":{gap.path}" if gap.path else ""),
                resolution.closure_digest,
            )
            for gap in resolution.gaps
        ]
        if resolution.formula is not None:
            assert node.formula == resolution.formula
            expected = external
        else:
            assert node.formula == MetricFormula.model_validate(contract["formula"])
            expected = [_opaque(contract), *external]
        referenced = {*node.fields, *(f"{TABLE}.{d}" for d in node.dimensions)}
        missing = referenced - {f"{TABLE}.{column}" for column in declared.column_types}
        expected += [("gap.metric_field_missing", "block", f"metrics.{ID}", "")] * len(missing)
        assert _gaps(snapshot) == expected, name
        assert node.binding is not None
        assert node.binding.closure_digest == (resolution.closure_digest or None)


def test_the_parity_context_is_the_engine_the_graph_holds(tmp_path: Path) -> None:
    clickhouse = _snapshot(tmp_path / "ch", _contract(), _data(), model=_model("clickhouse"))
    node = clickhouse.graph.metrics[0]
    assert node.formula.kind.value == "external_ref"
    assert [gap.code for gap in clickhouse.gaps] == [
        "gap.opaque_metric_formula",
        "gap.external_parity_unproven",
    ]
    assert "clickhouse." in clickhouse.gaps[1].reason


def test_a_column_the_graph_does_not_hold_has_no_type(tmp_path: Path) -> None:
    """A column declared twice and not alike is no field of the graph and no proven row."""
    conflict = _snapshot(
        tmp_path / "s", _contract(), _data(), model=_model(revenue=("numeric(18,2)", "bigint"))
    )
    codes = [(gap.code, gap.path) for gap in conflict.gaps]
    assert ("gap.field_conflict", f"fields.{TABLE}.revenue") in codes
    assert ("gap.external_parity_unproven", f"{FORMULA}:$.metrics[2]") in codes
    assert "legacy.sum.undeclared" in next(
        gap.reason for gap in conflict.gaps if gap.code == "gap.external_parity_unproven"
    )
    assert conflict.graph.metrics[0].formula.kind.value == "external_ref"


def _stores(name: str, engine: str) -> Table:
    return Table(
        name=name,
        grain=["id"],
        classification=DataClassification.INTERNAL,
        columns=[
            Column(name="id", type="integer", role=ColumnRole.DIMENSION),
            Column(name="revenue", type="text", role=ColumnRole.DIMENSION),
        ],
        physical=Physical(engine=engine),
    )


def test_the_context_is_the_grain_table_s_own(tmp_path: Path) -> None:
    """Another table's engine, or its column of the same name, is not the grain table's."""
    model = SemanticModel(
        tables=[
            _stores("dm.stores_ch", "clickhouse"),
            *_model().tables,
            _stores("dm.stores_pg", "postgres"),
        ]
    )
    snapshot = _snapshot(tmp_path / "s", _contract(), _data(), model=model)
    assert snapshot.gaps == []
    assert snapshot.graph.metrics[0].formula == MetricFormula.model_validate(NATIVE_RATIO)


def test_another_external_source_is_not_bound(tmp_path: Path) -> None:
    contract = _contract(source="ossie", pin=UNPINNED)
    snapshot = _snapshot(tmp_path / "s", contract, _data())
    node = snapshot.graph.metrics[0]
    assert node.binding is None
    assert set(node.model_dump(mode="json")) == V3_KEYS
    assert _gaps(snapshot) == [_opaque(contract)]


@pytest.mark.parametrize("case", ["native", "other_source"])
def test_an_unbound_node_is_written_as_version_3_wrote_it(tmp_path: Path, case: str) -> None:
    """Against the snapshots source version 3 (`f904584`) wrote from these inputs."""
    old = json.loads(SNAPSHOT_V3.read_bytes())[case]
    assert old["source_version"] == "3"
    contract = (
        _contract(formula=NATIVE_RATIO)
        if case == "native"
        else _contract(source="ossie", pin=UNPINNED)
    )
    snapshot = _snapshot(tmp_path / "s", contract)
    assert snapshot.source_version == NATIVE_SOURCE_VERSION == "4"
    payload = snapshot.model_dump(mode="json")
    assert set(payload["graph"]["metrics"][0]) == V3_KEYS
    assert snapshot.canonical_digest == old["canonical_digest"]
    assert {**payload, "source_version": "3"} == old
    # the payload version 3 wrote reads back, with the digest it was written with
    read = SemanticSnapshot.model_validate(old)
    assert read.model_dump(mode="json") == old
    assert read.graph == snapshot.graph
    explicit = copy.deepcopy(old)
    explicit["graph"]["metrics"][0]["binding"] = None
    assert SemanticSnapshot.model_validate(explicit) == read


def test_a_bound_node_reads_back(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "s", _contract(), _data())
    payload = snapshot.model_dump(mode="json")
    assert SemanticSnapshot.model_validate(payload) == snapshot
    tampered = copy.deepcopy(payload)
    tampered["graph"]["metrics"][0]["binding"]["closure_digest"] = UNPINNED
    with pytest.raises(ValueError, match="canonical_digest does not address this graph"):
        SemanticSnapshot.model_validate(tampered)


def test_a_contract_file_named_like_the_artifact_keeps_both_digests(tmp_path: Path) -> None:
    """A contracts path that is one file is read whatever its name, and the artifact's digest
    is kept under `dbt_semantic_manifest`: the name keeps both, each moving with its file."""
    _model().dump(tmp_path / "model.yaml")
    named = tmp_path / SOURCE
    named.write_text(yaml.safe_dump(_contract(), allow_unicode=True), encoding="utf-8")
    artifact = tmp_path / "semantic_manifest.json"
    artifact.write_bytes(_bytes(_data()))
    source = NativeModelSource(
        tmp_path / "model.yaml", named, semantic_manifest=artifact, dbt_database="x"
    )

    def expected() -> str:
        files = (named.read_bytes(), artifact.read_bytes())
        return sha256_digest(
            {
                "model": digest_bytes((tmp_path / "model.yaml").read_bytes()),
                SOURCE: sorted(digest_bytes(raw) for raw in files),
            }
        )

    first = source.snapshot()
    assert first.source_digest == expected()
    assert [metric.id for metric in first.graph.metrics] == [ID]
    named.write_bytes(named.read_bytes() + b"# a comment that changes no meaning\n")
    second = source.snapshot()
    assert second.source_digest == expected() != first.source_digest
    artifact.write_bytes(json.dumps(_data(), ensure_ascii=False, indent=2).encode("utf-8"))
    third = source.snapshot()
    assert third.source_digest == expected() != second.source_digest
    assert first.canonical_digest == second.canonical_digest == third.canonical_digest


def test_the_artifact_bytes_are_in_the_source_digest_and_its_closure_in_the_graph(
    tmp_path: Path,
) -> None:
    data = _data()
    compact = _snapshot(tmp_path / "compact", _contract(), data)
    indented = _snapshot(
        tmp_path / "indented",
        _contract(),
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    assert compact.canonical_digest == indented.canonical_digest
    assert compact.source_digest != indented.source_digest
    model = (tmp_path / "compact" / "model.yaml").read_bytes()
    contract = (tmp_path / "compact" / "metrics" / "gross_margin_pct.yaml").read_bytes()
    assert compact.source_digest == sha256_digest(
        {
            "model": digest_bytes(model),
            "gross_margin_pct.yaml": digest_bytes(contract),
            SOURCE: digest_bytes(_bytes(data)),
        }
    )


# --- the declaration ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pin", "artifact"),
    [(None, "legacy"), (None, None), (UNPINNED, "legacy"), (None, "bytes")],
    ids=["resolved", "no-artifact", "another-pin", "refused"],
)
def test_a_node_as_declared_is_the_node_of_its_contract(
    tmp_path: Path, pin: str | None, artifact: str | None
) -> None:
    contract = _contract(pin=pin)
    given: dict[str, Any] | bytes | None = (
        b"{" if artifact == "bytes" else _data(artifact) if artifact else None
    )
    node = _snapshot(tmp_path / "s", contract, given).graph.metrics[0]
    declared = declared_node(node)
    assert declared == metric_node(MetricContract.model_validate(contract))
    assert declared.binding is None
    assert set(declared.model_dump(mode="json")) == V3_KEYS


def test_a_node_without_a_binding_is_its_own_declaration(tmp_path: Path) -> None:
    node = _snapshot(tmp_path / "s", _contract(formula=NATIVE_RATIO)).graph.metrics[0]
    assert declared_node(node) is node


def test_the_contract_node_has_no_binding() -> None:
    node = metric_node(MetricContract.model_validate(_contract()))
    assert node.binding is None
    assert node.formula.kind.value == "external_ref"
    assert isinstance(node, MetricNode)


# --- the diff ------------------------------------------------------------------------------------


def test_one_definition_on_both_sides_is_no_change(tmp_path: Path) -> None:
    base = _snapshot(tmp_path / "base", _contract(), _data())
    candidate = _snapshot(tmp_path / "candidate", _contract(), _data())
    assert base.canonical_digest == candidate.canonical_digest
    assert diff_snapshots(base, candidate).changes == []


def _relabelled() -> dict[str, Any]:
    data = _data()
    model = next(item for item in data["semantic_models"] if item["name"] == "sales_daily")
    model["description"] = "Продажи по дням"
    return data


def test_a_definition_the_typed_formula_does_not_show_is_named(tmp_path: Path) -> None:
    after = _relabelled()
    before_pin, after_pin = _pin(_data(), "gross_margin_pct"), _pin(after, "gross_margin_pct")
    assert before_pin != after_pin
    base = _snapshot(tmp_path / "base", _contract(), _data())
    candidate = _snapshot(tmp_path / "candidate", _contract(pin=after_pin), after)
    assert base.graph.metrics[0].formula == candidate.graph.metrics[0].formula
    assert base.canonical_digest != candidate.canonical_digest
    assert _changes(diff_snapshots(base, candidate)) == [
        (
            "metric.binding_changed",
            ChangeKind.MODIFIED,
            Severity.REVIEW,
            ID,
            [ID],
            f"external definition changed: pin {before_pin} -> {after_pin}, "
            f"closure {before_pin} -> {after_pin}",
        )
    ]


def test_a_new_aggregate_is_a_formula_and_a_binding_change(tmp_path: Path) -> None:
    after = _data()
    model = next(item for item in after["semantic_models"] if item["name"] == "sales_daily")
    next(item for item in model["measures"] if item["name"] == "revenue")["agg"] = "min"
    before_pin, after_pin = _pin(_data(), "revenue"), _pin(after, "revenue")
    base = _snapshot(tmp_path / "base", _contract("revenue"), _data())
    candidate = _snapshot(tmp_path / "candidate", _contract("revenue", after_pin), after)
    aggregate = candidate.graph.metrics[0].formula.aggregate
    assert aggregate is not None and aggregate.agg is Aggregation.MIN
    assert _changes(diff_snapshots(base, candidate)) == [
        (
            "metric.formula_changed",
            ChangeKind.MODIFIED,
            Severity.REVIEW,
            ID,
            [ID],
            "formula changed",
        ),
        (
            "metric.binding_changed",
            ChangeKind.MODIFIED,
            Severity.REVIEW,
            ID,
            [ID],
            f"external definition changed: pin {before_pin} -> {after_pin}, "
            f"closure {before_pin} -> {after_pin}",
        ),
    ]


def test_a_definition_moved_under_an_old_pin_is_named_and_blocks(tmp_path: Path) -> None:
    before_pin, after_pin = (
        _pin(_data(), "gross_margin_pct"),
        _pin(_relabelled(), "gross_margin_pct"),
    )
    base = _snapshot(tmp_path / "base", _contract(), _data())
    candidate = _snapshot(tmp_path / "candidate", _contract(), _relabelled())
    changes = _changes(diff_snapshots(base, candidate))
    assert changes[:2] == [
        (
            "metric.formula_changed",
            ChangeKind.MODIFIED,
            Severity.REVIEW,
            ID,
            [ID],
            "formula changed",
        ),
        (
            "metric.binding_changed",
            ChangeKind.MODIFIED,
            Severity.REVIEW,
            ID,
            [ID],
            f"external definition changed: closure {before_pin} -> {after_pin}",
        ),
    ]
    # sorted by the gap's key: the revision gap, then the opaque one the typed formula left
    assert [
        (code, severity, detail.split(":")[0]) for code, _, severity, _, _, detail in changes[2:]
    ] == [
        ("gap.appeared", Severity.BLOCK, "gap.external_revision_mismatch"),
        ("gap.appeared", Severity.REVIEW, "gap.opaque_metric_formula"),
    ]


def test_a_construct_moved_under_its_pin_is_named_though_the_formula_reads_the_same(
    tmp_path: Path,
) -> None:
    """The formula of a construct stays `external_ref`; the closure still moves."""
    after = _data()
    cumulative = next(item for item in after["metrics"] if item["name"] == "revenue_cum")
    cumulative["type_params"]["cumulative_type_params"]["period_agg"] = "last"
    before_pin, after_pin = _pin(_data(), "revenue_cum"), _pin(after, "revenue_cum")
    base = _snapshot(tmp_path / "base", _contract("revenue_cum"), _data())
    candidate = _snapshot(tmp_path / "candidate", _contract("revenue_cum"), after)
    assert base.graph.metrics[0].formula == candidate.graph.metrics[0].formula
    changes = _changes(diff_snapshots(base, candidate))
    assert [change[0] for change in changes if change[0].startswith("metric.")] == [
        "metric.binding_changed"
    ]
    assert changes[0] == (
        "metric.binding_changed",
        ChangeKind.MODIFIED,
        Severity.REVIEW,
        ID,
        [ID],
        f"external definition changed: closure {before_pin} -> {after_pin}",
    )


def test_a_closure_no_artifact_gave_is_not_read(tmp_path: Path) -> None:
    pin = _pin(_data(), "gross_margin_pct")
    base = _snapshot(tmp_path / "base", _contract())
    candidate = _snapshot(tmp_path / "candidate", _contract(), _data())
    binding = [
        change
        for change in _changes(diff_snapshots(base, candidate))
        if change[0] == "metric.binding_changed"
    ]
    assert [change[-1] for change in binding] == [
        f"external definition changed: closure not read -> {pin}"
    ]
    back = [
        change
        for change in _changes(diff_snapshots(candidate, base))
        if change[0] == "metric.binding_changed"
    ]
    assert [change[-1] for change in back] == [
        f"external definition changed: closure {pin} -> not read"
    ]


@pytest.mark.parametrize("direction", ["now", "no longer"])
def test_a_formula_that_came_to_or_left_dbt_is_named(tmp_path: Path, direction: str) -> None:
    native = _snapshot(tmp_path / "native", _contract(formula=NATIVE_RATIO), _data())
    bound = _snapshot(tmp_path / "bound", _contract(), _data())
    assert native.graph.metrics[0].formula == bound.graph.metrics[0].formula
    base, candidate = (native, bound) if direction == "now" else (bound, native)
    assert _changes(diff_snapshots(base, candidate)) == [
        (
            "metric.binding_changed",
            ChangeKind.MODIFIED,
            Severity.REVIEW,
            ID,
            [ID],
            f"formula {direction} defined by {SOURCE} metric gross_margin_pct",
        )
    ]


def test_a_moved_binding_invalidates_the_metric_s_verified_assets(tmp_path: Path) -> None:
    after = _relabelled()
    base = _snapshot(tmp_path / "base", _contract(), _data())
    candidate = _snapshot(
        tmp_path / "candidate", _contract(pin=_pin(after, "gross_margin_pct")), after
    )
    repository = VerifiedAssetRepository(
        [VerifiedAsset.model_validate(asset(semantic_digest=base.canonical_digest))]
    )
    [found] = repository.invalidate(diff_snapshots(base, candidate))
    assert (found.code, found.severity, found.detail) == (
        "asset.metric_changed",
        Severity.REVIEW,
        f"re-review required: metric.binding_changed on {ID}",
    )
    assert repository.invalidate(diff_snapshots(base, base)) == []


def test_a_metric_pointed_at_another_dbt_metric_names_the_ref(tmp_path: Path) -> None:
    before_pin, after_pin = _pin(_data(), "gross_margin_pct"), _pin(_data(), "revenue")
    base = _snapshot(tmp_path / "base", _contract(), _data())
    candidate = _snapshot(tmp_path / "candidate", _contract("revenue"), _data())
    [binding] = [
        change[-1]
        for change in _changes(diff_snapshots(base, candidate))
        if change[0] == "metric.binding_changed"
    ]
    assert binding == (
        "external definition changed: ref gross_margin_pct -> revenue, "
        f"pin {before_pin} -> {after_pin}, closure {before_pin} -> {after_pin}"
    )


# --- what reads the node -------------------------------------------------------------------


def test_reference_sql_reads_the_resolved_formula(tmp_path: Path) -> None:
    native = _snapshot(tmp_path / "native", _contract(formula=NATIVE_RATIO))
    bound = _snapshot(tmp_path / "bound", _contract(), _data())

    def compiled(snapshot: SemanticSnapshot) -> tuple[list[str], list[str]]:
        found = compile_metric_queries(snapshot, [PIN])
        return [query.sql for query in found.queries], [item.code for item in found.findings]

    queries, findings = compiled(bound)
    assert len(queries) == 1
    assert (queries, findings) == compiled(native)


def test_chart_impact_reads_the_resolved_formula(tmp_path: Path) -> None:
    after = _relabelled()
    base = _snapshot(tmp_path / "base", _contract(), _data())
    candidate = _snapshot(
        tmp_path / "candidate", _contract(pin=_pin(after, "gross_margin_pct")), after
    )
    report = resolve_impact(
        diff_snapshots(base, candidate),
        both_products(),
        dashboards={DASHBOARD: dashboard()},
        metrics=[*base.graph.metrics, *candidate.graph.metrics],
    )
    impact = impact_of(report, "metric.binding_changed")
    assert [(chart.ref, chart.relation) for chart in impact.charts] == [
        (f"{DASHBOARD}#margin_by_region", ChartRelation.COMPUTES_METRIC)
    ]
    assert report.chart_gaps == []


# --- the gate ----------------------------------------------------------------------------------


def _gate(
    tmp_path: Path,
    validated: dict[str, Any],
    base: SemanticSnapshot,
    candidate: SemanticSnapshot,
) -> tuple[Any, dict[str, Any]]:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    shutil.copy(PRODUCT, contracts / PRODUCT.name)
    (contracts / "gross_margin_pct.yaml").write_text(
        yaml.safe_dump(validated, allow_unicode=True), encoding="utf-8"
    )
    loaded = load_contracts([contracts])
    report = run_gate(
        GateInputs(
            product=next(iter(loaded.products.values())),
            validation=loaded.report,
            base=base,
            candidate=candidate,
            candidate_model=_model(),
            dashboards={REF: spec()},
            validated_metrics=loaded.metrics,
        ),
        store=FilesystemArtifactStore(tmp_path / "evidence"),
        created_at="2026-11-06T00:00:00Z",
    )
    return report, _check_payload(tmp_path, report, "contracts.validate")


def test_a_resolved_formula_matches_the_contract_that_pins_it(tmp_path: Path) -> None:
    base = _snapshot(tmp_path / "base", _contract(), _data())
    candidate = _snapshot(tmp_path / "candidate", _contract(), _data())
    report, payload = _gate(tmp_path, _contract(), base, candidate)
    assert payload["candidate_pins"] == [
        {"ref": PIN, "candidate_versions": [3], "outcome": "match"}
    ]
    assert [issue["code"] for issue in payload["issues"]] == []
    assert status_of(report, "semantic.gaps") is CheckStatus.PASS
    assert status_of(report, "semantic.diff") is CheckStatus.PASS


def test_a_repinned_candidate_is_the_change_under_review(tmp_path: Path) -> None:
    after = _relabelled()
    base = _snapshot(tmp_path / "base", _contract(), _data())
    candidate = _snapshot(
        tmp_path / "candidate", _contract(pin=_pin(after, "gross_margin_pct")), after
    )
    report, payload = _gate(tmp_path, _contract(), base, candidate)
    assert payload["candidate_pins"] == [
        {"ref": PIN, "candidate_versions": [3], "outcome": "match"}
    ]
    assert status_of(report, "semantic.diff") is CheckStatus.REVIEW
    diff = _check_payload(tmp_path, report, "semantic.diff")
    assert [change["code"] for change in diff["changes"]] == ["metric.binding_changed"]


def test_a_pin_neither_snapshot_holds_is_another_definition(tmp_path: Path) -> None:
    after = _relabelled()
    repinned = _contract(pin=_pin(after, "gross_margin_pct"))
    base = _snapshot(tmp_path / "base", repinned, after)
    candidate = _snapshot(tmp_path / "candidate", repinned, after)
    report, payload = _gate(tmp_path, _contract(), base, candidate)
    assert payload["candidate_pins"] == [
        {
            "ref": PIN,
            "candidate_versions": [3],
            "outcome": "content_mismatch",
            "differences": ["metric.formula_changed"],
        }
    ]
    assert [(issue["code"], issue["severity"]) for issue in payload["issues"]] == [
        ("metric_ref.candidate_content_mismatch", "block")
    ]
    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK


# --- versions ----------------------------------------------------------------------------------


def test_the_versions() -> None:
    assert NATIVE_SOURCE_VERSION == "4"
    assert int(VERIFIER_VERSION) >= 128
    assert int(CHECK_VERSION) >= 82
