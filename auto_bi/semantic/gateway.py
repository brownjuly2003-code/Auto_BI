"""Semantic sources and immutable snapshots (DECISIONOPS_ARCHITECTURE §7.4).

A snapshot is the frozen meaning a verification run is judged against: a canonical
projection of `model.yaml` and the metric contracts, addressed by digest. Everything that
matters for a number — fields and their classification, joins, physical keys, and each
metric's formula, grain, event time, unit and additivity — is inside the canonical graph,
so two snapshots of the same source are literally the same snapshot, and any difference has
a name (§8.3).

What the projection cannot represent becomes a :class:`SemanticGap` (invariant 6). A raw
SQL metric in `model.yaml` is opaque to a structural diff; recording it as a gap says so
out loud, whereas dropping it would let a formula change through unnoticed. Gaps carry a
digest of the unrepresentable value, never the value itself (§10.2).

Physical statistics (`rows`, `bytes`, `cardinality`, `captured_at`) are deliberately not in
the graph: they change with every load and would make an unchanged model produce a new
digest every day. Keys and partitioning — the parts the physical gate reasons about — are.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import Field, ValidationError, model_validator

from auto_bi.evidence.canonical import digest_bytes, sha256_digest
from auto_bi.scalars import Sha256Digest
from auto_bi.semantic.metric_contract import (
    FormulaKind,
    MetricContract,
    MetricFormula,
    MetricGrain,
    MetricLifecycle,
    MetricTime,
    MetricUnit,
)
from auto_bi.semantic.model import (
    Additivity,
    Aggregation,
    ColumnRole,
    DataClassification,
    SemanticModel,
)
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel

#: Generation of the native canonical projection. Bumped when the graph shape changes,
#: because that changes every canonical digest and therefore every snapshot identity.
NATIVE_SOURCE_VERSION = "1"

NATIVE_CAPABILITIES = ["fields", "joins", "metric_contracts", "physical_keys"]

_YAML_SUFFIXES = (".yaml", ".yml")


class SourceKind(StrEnum):
    NATIVE_MODEL_YAML = "native_model_yaml"


class SemanticGap(StrictModel):
    """Something the source declares that the canonical graph does not carry."""

    code: str = Field(min_length=1)
    severity: Severity
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source_value_ref: str = ""


class FieldNode(StrictModel):
    id: str
    table: str
    column: str
    type: str
    role: ColumnRole
    classification: DataClassification
    agg: Aggregation | None = None
    additivity: Additivity | None = None
    fk: str = ""
    description: str = ""


class JoinNode(StrictModel):
    id: str
    left: str
    right: str
    type: str


class TableNode(StrictModel):
    id: str
    grain: list[str] = Field(default_factory=list)
    classification: DataClassification
    description: str = ""
    engine: str = ""
    table_engine: str = ""
    sorting_key: list[str] = Field(default_factory=list)
    distribution_key: list[str] = Field(default_factory=list)
    partition_key: str = ""


class MetricNode(StrictModel):
    id: str
    version: int = Field(ge=1)
    owner: str
    status: MetricLifecycle
    description: str = ""
    formula: MetricFormula
    grain: MetricGrain
    time: MetricTime
    unit: MetricUnit
    additivity: Additivity
    non_additive_axes: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    classification: DataClassification
    policy_refs: list[str] = Field(default_factory=list)
    #: Fields the metric's value depends on: formula terms, grain keys, event time.
    fields: list[str] = Field(default_factory=list)


class SemanticGraph(StrictModel):
    tables: list[TableNode] = Field(default_factory=list)
    fields: list[FieldNode] = Field(default_factory=list)
    joins: list[JoinNode] = Field(default_factory=list)
    metrics: list[MetricNode] = Field(default_factory=list)


class SemanticSnapshot(StrictModel):
    source_kind: SourceKind
    source_version: str = Field(min_length=1)
    source_revision: str = ""
    capabilities: list[str] = Field(default_factory=list)
    source_digest: Sha256Digest
    canonical_digest: Sha256Digest
    graph: SemanticGraph
    gaps: list[SemanticGap] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_digest(self) -> SemanticSnapshot:
        expected = sha256_digest(self.graph.model_dump(mode="json"))
        if self.canonical_digest != expected:
            raise ValueError(f"canonical_digest does not address this graph (expected {expected})")
        return self

    def metric(self, metric_id: str) -> MetricNode | None:
        return next((node for node in self.graph.metrics if node.id == metric_id), None)

    def field(self, field_id: str) -> FieldNode | None:
        return next((node for node in self.graph.fields if node.id == field_id), None)


class SemanticSource(Protocol):
    """Port: something that can freeze a semantic source into a snapshot."""

    def snapshot(self, revision: str = "") -> SemanticSnapshot: ...


class NativeModelSource:
    """First source adapter: this project's own `model.yaml` plus metric contracts."""

    kind = SourceKind.NATIVE_MODEL_YAML

    def __init__(self, model_path: str | Path, contracts_path: str | Path | None = None) -> None:
        self._model_path = Path(model_path)
        self._contracts_path = Path(contracts_path) if contracts_path is not None else None

    def snapshot(self, revision: str = "") -> SemanticSnapshot:
        gaps: list[SemanticGap] = []
        model_bytes = self._model_path.read_bytes()
        model = SemanticModel.model_validate(yaml.safe_load(model_bytes.decode("utf-8")) or {})

        digests = {"model": digest_bytes(model_bytes)}
        contracts: list[MetricContract] = []
        for path in self._contract_files():
            raw = path.read_bytes()
            digests[path.name] = digest_bytes(raw)
            contract = _parse_contract(path, raw, gaps)
            if contract is not None:
                contracts.append(contract)

        graph = _build_graph(model, contracts, gaps)
        return SemanticSnapshot(
            source_kind=self.kind,
            source_version=NATIVE_SOURCE_VERSION,
            source_revision=revision,
            capabilities=list(NATIVE_CAPABILITIES),
            source_digest=sha256_digest(digests),
            canonical_digest=sha256_digest(graph.model_dump(mode="json")),
            graph=graph,
            gaps=gaps,
        )

    def _contract_files(self) -> list[Path]:
        if self._contracts_path is None:
            return []
        if self._contracts_path.is_file():
            return [self._contracts_path]
        return sorted(
            child
            for child in self._contracts_path.rglob("*")
            if child.suffix in _YAML_SUFFIXES and child.is_file()
        )


def _parse_contract(path: Path, raw: bytes, gaps: list[SemanticGap]) -> MetricContract | None:
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        gaps.append(
            SemanticGap(
                code="gap.contract_invalid",
                severity=Severity.BLOCK,
                path=f"contracts.{path.name}",
                reason=f"metric contract cannot be read: {type(exc).__name__}",
                source_value_ref=digest_bytes(raw),
            )
        )
        return None
    if not isinstance(data, dict) or data.get("kind") != "MetricContract":
        gaps.append(
            SemanticGap(
                code="gap.contract_invalid",
                severity=Severity.BLOCK,
                path=f"contracts.{path.name}",
                reason="file in the contracts path is not a MetricContract",
                source_value_ref=digest_bytes(raw),
            )
        )
        return None
    try:
        return MetricContract.model_validate(data)
    except ValidationError as exc:
        gaps.append(
            SemanticGap(
                code="gap.contract_invalid",
                severity=Severity.BLOCK,
                path=f"contracts.{path.name}",
                reason=f"metric contract does not validate ({exc.error_count()} error(s))",
                source_value_ref=digest_bytes(raw),
            )
        )
        return None


def _build_graph(
    model: SemanticModel, contracts: list[MetricContract], gaps: list[SemanticGap]
) -> SemanticGraph:
    tables: list[TableNode] = []
    fields: list[FieldNode] = []

    for table in model.tables:
        table_class = table.classification or DataClassification.INTERNAL
        physical = table.physical
        if physical is None:
            gaps.append(
                SemanticGap(
                    code="gap.missing_physical",
                    severity=Severity.ADVISORY,
                    path=f"tables.{table.name}.physical",
                    reason="no engine or key facts declared; the physical gate cannot run",
                )
            )
        tables.append(
            TableNode(
                id=table.name,
                grain=list(table.grain),
                classification=table_class,
                description=table.description,
                engine=physical.engine if physical else "",
                table_engine=physical.table_engine if physical else "",
                sorting_key=list(physical.sorting_key) if physical else [],
                distribution_key=list(physical.distribution_key) if physical else [],
                partition_key=physical.partition_key if physical else "",
            )
        )
        for column in table.columns:
            fields.append(
                FieldNode(
                    id=f"{table.name}.{column.name}",
                    table=table.name,
                    column=column.name,
                    type=column.type,
                    role=column.role,
                    classification=column.classification or table_class,
                    agg=column.agg,
                    additivity=column.additivity,
                    fk=column.fk or "",
                    description=column.description,
                )
            )

    known = {field.id for field in fields}
    joins: list[JoinNode] = []
    for join in model.joins:
        if join.left not in known or join.right not in known:
            missing = [side for side in (join.left, join.right) if side not in known]
            gaps.append(
                SemanticGap(
                    code="gap.dangling_join",
                    severity=Severity.BLOCK,
                    path=f"joins.{join.left}->{join.right}",
                    reason=f"join endpoint is not a declared field: {', '.join(missing)}",
                )
            )
            continue
        joins.append(
            JoinNode(
                id=f"{join.left}->{join.right}", left=join.left, right=join.right, type=join.type
            )
        )

    for metric in model.metrics:
        gaps.append(
            SemanticGap(
                code="gap.raw_sql_metric",
                severity=Severity.REVIEW,
                path=f"metrics.{metric.name}",
                reason="model.yaml metric is raw SQL: opaque to the canonical graph",
                source_value_ref=sha256_digest(metric.sql),
            )
        )

    metrics = [_metric_node(contract, known, gaps) for contract in contracts]

    return SemanticGraph(
        tables=sorted(tables, key=lambda node: node.id),
        fields=sorted(fields, key=lambda node: node.id),
        joins=sorted(joins, key=lambda node: node.id),
        metrics=sorted(metrics, key=lambda node: node.id),
    )


def _metric_node(contract: MetricContract, known: set[str], gaps: list[SemanticGap]) -> MetricNode:
    identifier = contract.metadata.id
    grain_table = contract.grain.table
    dependencies: set[str] = {f"{grain_table}.{key}" for key in contract.grain.keys}
    dependencies.add(f"{grain_table}.{contract.time.event_time_column}")

    formula = contract.formula
    terms = [formula.aggregate, formula.numerator, formula.denominator]
    for term in terms:
        if term is not None:
            dependencies.add(f"{term.table}.{term.column}")
    if formula.kind in (FormulaKind.EXTERNAL_REF, FormulaKind.REVIEWED_SQL):
        gaps.append(
            SemanticGap(
                code="gap.opaque_metric_formula",
                severity=Severity.REVIEW,
                path=f"metrics.{identifier}.formula",
                reason=(
                    f"{formula.kind.value} formula is not structurally comparable; "
                    "field-level impact cannot be derived from it"
                ),
                source_value_ref=sha256_digest(formula.model_dump(mode="json")),
            )
        )

    for reference in sorted(dependencies | {f"{grain_table}.{d}" for d in contract.dimensions}):
        if reference not in known:
            gaps.append(
                SemanticGap(
                    code="gap.metric_field_missing",
                    severity=Severity.BLOCK,
                    path=f"metrics.{identifier}",
                    reason=f"metric references a field the model does not declare: {reference}",
                )
            )

    return MetricNode(
        id=identifier,
        version=contract.metadata.version,
        owner=contract.metadata.owner,
        status=contract.metadata.status,
        description=contract.metadata.description,
        formula=formula,
        grain=contract.grain,
        time=contract.time,
        unit=contract.unit,
        additivity=contract.additivity,
        non_additive_axes=list(contract.non_additive_axes),
        dimensions=list(contract.dimensions),
        classification=contract.classification,
        policy_refs=list(contract.policy_refs),
        fields=sorted(dependencies),
    )
