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

A formula a dbt semantic manifest defines (ADR 0008) is resolved from the artifact the source
is given (`metricflow_formula.resolve_formula`). Where it resolves, the node holds the typed
formula and the fields it reads, so impact and reference SQL read it as they read a native
one; where it does not, the node keeps the declared `external_ref` and the resolution's gaps
say why. Either way the node carries a :class:`FormulaBinding` — the declaration and the
digest of the closure the artifact gave — so a change of the definition in dbt moves the
canonical digest whether the typed formula shows it or not, and a comparison with the
contract reads the declaration (:func:`declared_node`).

Physical statistics (`rows`, `bytes`, `cardinality`, `captured_at`) are deliberately not in
the graph: they change with every load and would make an unchanged model produce a new
digest every day. Keys and partitioning — the parts the physical gate reasons about — are,
and so are the metric's declared budgets: a bound the contract states is part of what the
metric *means* and moves only when someone edits it, unlike the measurement it is compared
against.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import Field, ValidationError, model_validator

from auto_bi import filesystem
from auto_bi.evidence.canonical import digest_bytes, sha256_digest
from auto_bi.scalars import Sha256Digest
from auto_bi.semantic.metric_contract import (
    ExternalFormula,
    FormulaKind,
    MetricBudget,
    MetricContract,
    MetricFormula,
    MetricFreshness,
    MetricGrain,
    MetricLifecycle,
    MetricTime,
    MetricUnit,
)
from auto_bi.semantic.metricflow import SOURCE as DBT_SOURCE
from auto_bi.semantic.metricflow import JsonObject, ManifestRead, read_semantic_manifest
from auto_bi.semantic.metricflow_formula import (
    DeclaredTable,
    FormulaResolution,
    is_dbt_formula,
    resolve_formula,
)
from auto_bi.semantic.model import (
    Additivity,
    Aggregation,
    ColumnRole,
    DataClassification,
    SemanticModel,
    Table,
)
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel
from auto_bi.yaml_document import load_yaml_document

#: Generation of the native canonical projection. Bumped when the graph shape changes,
#: because that changes every canonical digest and therefore every snapshot identity.
#: 2 — metric nodes now carry freshness, moving the canonical digest of every native
#: snapshot with metrics; a snapshot without metrics keeps its digest.
#: 3 — metric nodes now carry their declared join paths, moving the canonical digest of
#: every native snapshot with metrics; a snapshot without metrics keeps its digest.
#: 4 — the node of a metric whose formula a dbt semantic manifest defines (ADR 0008) carries
#: its binding and, where the formula resolves, the typed formula in place of `external_ref`.
#: A node without a binding is written as version 3 wrote it, so the graph of a snapshot
#: without such a metric, and its canonical digest, are the ones version 3 gave; the snapshot
#: says version 4, as every snapshot this source writes does.
NATIVE_SOURCE_VERSION = "4"

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
    #: The name the model gives the table, and the one id of the graph that is a free text
    #: alone: a field id holds a ".", a join id a "->", a metric id is a ref its contract's
    #: schema has checked. A diff names a change by the id of what changed, and has no name
    #: for a table whose id is empty text.
    id: str = Field(min_length=1)
    grain: list[str] = Field(default_factory=list)
    classification: DataClassification
    description: str = ""
    engine: str = ""
    table_engine: str = ""
    sorting_key: list[str] = Field(default_factory=list)
    distribution_key: list[str] = Field(default_factory=list)
    partition_key: str = ""


class FormulaBinding(ExternalFormula):
    """Where the formula of a metric is defined, when a dbt semantic manifest defines it.

    `source`, `ref` and `source_revision` are the contract's declaration, as its `external_ref`
    formula states them. `closure_digest` is the digest of the metric's closure in the artifact
    the snapshot read — `None` where no artifact gave one: none was given, the reader refused
    it, or it holds no such metric. Set, it differs from the pin exactly when the snapshot
    carries `gap.external_revision_mismatch`.
    """

    closure_digest: Sha256Digest | None = None

    def declared(self) -> MetricFormula:
        """The formula as the contract declares it."""
        return MetricFormula(
            kind=FormulaKind.EXTERNAL_REF,
            external=ExternalFormula(
                source=self.source, ref=self.ref, source_revision=self.source_revision
            ),
        )


class MetricNode(StrictModel):
    id: str
    version: int = Field(ge=1)
    owner: str
    status: MetricLifecycle
    description: str = ""
    #: The formula the metric's number is computed by: the contract's own, or, for a bound
    #: metric whose formula resolved, the typed formula resolved from the artifact.
    formula: MetricFormula
    #: Set exactly when a dbt semantic manifest defines the formula (ADR 0008, items 9 and
    #: 10). Absent, it is not written at all: the node of any other metric is the one source
    #: version 3 wrote, and so is the canonical digest of a snapshot without a bound metric.
    binding: FormulaBinding | None = Field(default=None, exclude_if=lambda value: value is None)
    grain: MetricGrain
    time: MetricTime
    unit: MetricUnit
    additivity: Additivity
    non_additive_axes: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    #: The contract's declared join paths are part of the metric's meaning (§7.3 "grain,
    #: allowed dimensions и join paths"), so they live in the graph: a removed, re-typed
    #: or added path moves the canonical digest and is named in the diff. They are the
    #: metric's own paths, compared apart from the model's joins.
    join_paths: list[JoinNode] = Field(default_factory=list)
    classification: DataClassification
    policy_refs: list[str] = Field(default_factory=list)
    #: Fields the metric's value depends on: formula terms, grain keys, event time.
    fields: list[str] = Field(default_factory=list)
    #: What the metric says a run of it may cost (§7.3 "physical budgets"). It is in the
    #: graph rather than read from the contract at grading time because the plan step 7
    #: estimates is compiled from *this* node: taking the query from the snapshot and the
    #: bound from a file beside it would let a release be graded against a budget that
    #: belongs to a different version of the metric. `None` is "declares no bound", which
    #: `MetricContract` already normalises `budgets: {}` into.
    budgets: MetricBudget | None = None
    #: The contract's declared freshness is part of the metric's meaning (§7.3), so it
    #: lives in the graph: changing it moves the canonical digest and is named in the diff.
    freshness: MetricFreshness


def _one_node_to_an_id(kind: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for node_id in ids:
        if node_id in seen:
            raise ValueError(f"more than one {kind} node of the graph has the id {node_id!r}")
        seen.add(node_id)


class SemanticGraph(StrictModel):
    tables: list[TableNode] = Field(default_factory=list)
    fields: list[FieldNode] = Field(default_factory=list)
    joins: list[JoinNode] = Field(default_factory=list)
    metrics: list[MetricNode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ids(self) -> SemanticGraph:
        # One node to an id. A diff reads a graph by id and keeps the last node of one, the
        # gate reads a table or a field by the first: of a graph that held two nodes under
        # one id the two would answer apart. Metrics are not held to it here: two contracts
        # of one id stay two nodes, whose versions the gate's checks of a pin name, and the
        # snapshot of a source carries `gap.metric_duplicate` for the id.
        _one_node_to_an_id("table", [node.id for node in self.tables])
        _one_node_to_an_id("field", [node.id for node in self.fields])
        _one_node_to_an_id("join", [node.id for node in self.joins])
        return self


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


@dataclass(frozen=True)
class SnapshotRead:
    """A snapshot and the closures behind its bound formulas, from one reading of the source.

    `closures` holds, under the dbt ref of each binding whose `closure_digest` is set, the
    closure the artifact gave for that ref — the definition evidence keeps beside the snapshot
    (ADR 0008, item 11) — in the order of refs. Both come from one reading of the artifact, so
    each closure hashes to the digest its bindings carry.
    """

    snapshot: SemanticSnapshot
    closures: Mapping[str, JsonObject]


class NativeModelSource:
    """First source adapter: this project's own `model.yaml` plus metric contracts."""

    kind = SourceKind.NATIVE_MODEL_YAML

    def __init__(
        self,
        model_path: str | Path,
        contracts_path: str | Path | None = None,
        *,
        semantic_manifest: str | Path | None = None,
        dbt_database: str | None = None,
    ) -> None:
        """`semantic_manifest` is the dbt artifact the formulas of `dbt_semantic_manifest`
        contracts are resolved from, and `dbt_database` the dbt database declared with it —
        empty for an engine with two-part names, `None` when none is declared (ADR 0008,
        item 3). Without an artifact such a formula is `gap.external_source_missing`."""
        self._model_path = Path(model_path)
        self._contracts_path = Path(contracts_path) if contracts_path is not None else None
        self._manifest_path = Path(semantic_manifest) if semantic_manifest is not None else None
        self._dbt_database = dbt_database

    def snapshot(self, revision: str = "") -> SemanticSnapshot:
        return self.read(revision).snapshot

    def read(self, revision: str = "") -> SnapshotRead:
        """The snapshot and, from the same bytes of the artifact, its closures."""
        gaps: list[SemanticGap] = []
        model_bytes = self._model_path.read_bytes()
        model = SemanticModel.model_validate(load_yaml_document(model_bytes.decode("utf-8")))

        read: dict[str, list[str]] = {"model": [digest_bytes(model_bytes)]}
        manifest: ManifestRead | None = None
        if self._manifest_path is not None:
            # The bytes of the artifact as a whole are what the snapshot was read from, so they
            # are in the source digest; a formula's pin addresses only its closure (ADR 0008,
            # item 4). An artifact the reader refuses is still bytes the snapshot read.
            manifest = read_semantic_manifest(self._manifest_path.read_bytes())
            read[DBT_SOURCE] = [manifest.artifact_digest]
        files: list[tuple[str, str, Path, bytes]] = []
        for path in self._contract_files():
            raw = self._held_bytes(path)
            digest = digest_bytes(raw)
            read.setdefault(path.name, []).append(digest)
            files.append((path.name, digest, path, raw))

        # The source digest does not say which directory holds a contract, and nothing else
        # of the snapshot may: the order a listing gives — the directory first and, on
        # Windows, without case — would order the gaps of two contracts and the nodes of one
        # id, and the snapshot, sealed whole and part of the run's identity, would be another
        # one when a file moved or the ref was read on another system. A file that does not
        # load is answered in the order of file names, by code point, then of bytes; a
        # contract that loads, in the order of metric ids, then of versions, then of bytes.
        loaded: list[tuple[str, int, str, MetricContract]] = []
        for _, digest, path, raw in sorted(files, key=lambda file: file[:2]):
            contract = _parse_contract(path, raw, gaps)
            if contract is not None:
                loaded.append((contract.metadata.id, contract.metadata.version, digest, contract))
        contracts = [contract for *_, contract in sorted(loaded, key=lambda item: item[:3])]

        graph = _build_graph(model, contracts, gaps, manifest, self._dbt_database)
        snapshot = SemanticSnapshot(
            source_kind=self.kind,
            source_version=NATIVE_SOURCE_VERSION,
            source_revision=revision,
            capabilities=list(NATIVE_CAPABILITIES),
            source_digest=_source_digest(read),
            canonical_digest=sha256_digest(graph.model_dump(mode="json")),
            graph=graph,
            gaps=gaps,
        )
        return SnapshotRead(snapshot, _closures(graph, manifest))

    def _contract_files(self) -> list[Path]:
        """The contract files of the ref — or what kept one of them from being found.

        A directory the filesystem refuses to list is not one without contracts, and a
        path it refuses to look at is not one that is no file: read as that, the ref
        would be frozen without the metrics they hold and the snapshot would say nothing
        of it. The `OSError` of the listing or of the look is raised.

        Below the contracts directory a symbolic link with a contract suffix is a contract
        file whatever it leads to — a file, a directory, nothing, a path the filesystem
        refuses to look at: the ref holds the link, the look is at the link and not through
        it, and the link is read as its text (`_held_bytes`).
        """
        if self._contracts_path is None:
            return []
        if filesystem.is_file(self._contracts_path):
            return [self._contracts_path]
        return sorted(
            child
            for child in filesystem.paths_under(self._contracts_path)
            if child.suffix in _YAML_SUFFIXES
            and (filesystem.is_link(child) or filesystem.is_file(child))
        )

    def _held_bytes(self, path: Path) -> bytes:
        """What the ref holds at *path*: a symbolic link's text, any other file's bytes.

        Git commits a symbolic link as the text it holds, not as the file it leads to, and a
        checkout makes the link again where `core.symlinks` is true and writes a file of that
        text where it is false. Read through the link, one commit was two snapshots — the
        contract the link leads to where the link was made, a file of its text where it was
        not — and the bytes read first need not be in the commit at all. Read as its text, a
        link below the contracts directory is the file a checkout without links writes, where
        the text the link gives back reads as the text committed (`_link_text`); the bytes are
        then parsed as those of any contract file.

        A hard link is not told apart: git commits it as one more file of the bytes it has,
        and a checkout writes it as a copy. The contracts path itself is the caller's to name
        and is read through a link, as `model.yaml` is.
        """
        if path != self._contracts_path and filesystem.is_link(path):
            return os.fsencode(_link_text(os.readlink(path)))
        return path.read_bytes()


def _link_text(text: str) -> str:
    """A symbolic link's text as git commits it, from the text `os.readlink` gives back.

    On POSIX the two are one text, and a backslash is a character of a name. Git for Windows
    commits a link's text with `/` between names and makes the link with a backslash between
    them, and Windows gives an absolute target back in its long form. So on Windows each
    backslash is read as `/`, and the long form as the text it stands for: `//?/C:/x` as
    `C:/x`, `//?/UNC/server/share/x` as `//server/share/x`.
    """
    if sys.platform == "win32":
        text = text.replace("\\", "/")
        if text.startswith("//?/UNC/"):
            return "//" + text.removeprefix("//?/UNC/")
        return text.removeprefix("//?/")
    return text


def _closures(graph: SemanticGraph, manifest: ManifestRead | None) -> dict[str, JsonObject]:
    """The closure of each ref a binding of *graph* has a closure digest of, by ref.

    A digest is set only where the artifact was read and holds the ref, so each ref named here
    has its closure; two bindings of one ref share it.
    """
    if manifest is None or manifest.manifest is None:
        return {}
    refs = {
        node.binding.ref
        for node in graph.metrics
        if node.binding is not None and node.binding.closure_digest is not None
    }
    return {ref: manifest.manifest.closure(ref) for ref in sorted(refs)}


def _source_digest(read: dict[str, list[str]]) -> str:
    """Content address of the bytes a snapshot was read from, every file of them kept.

    The model's digest is kept under `model`, a dbt semantic manifest's under
    `dbt_semantic_manifest` (none given, none kept), and a contract's under its file name
    alone, not under the directory below the contracts path that holds it, so a contract moved
    between those directories leaves this digest as it was. Two files can then share a name —
    `a/margin.yaml` and `b/margin.yaml`, or the model and a contracts path that is one file
    named `model` — and the name keeps the digest of each: sorted, because which of the
    directories holds which file is no more a part of this digest than the directory of a
    single file is, and with a repeat as often as it was read, because two copies of a
    contract are two files of the ref. A name of one file keeps that file's digest alone, so
    a ref whose files all have names of their own has the source digest it always had.
    """
    return sha256_digest(
        {name: found[0] if len(found) == 1 else sorted(found) for name, found in read.items()}
    )


def _parse_contract(path: Path, raw: bytes, gaps: list[SemanticGap]) -> MetricContract | None:
    try:
        data = load_yaml_document(raw.decode("utf-8"))
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
    model: SemanticModel,
    contracts: list[MetricContract],
    gaps: list[SemanticGap],
    manifest: ManifestRead | None = None,
    dbt_database: str | None = None,
) -> SemanticGraph:
    tables: list[TableNode] = []
    declared: list[FieldNode] = []

    # What a model declares more than once is one answer of the graph or none. The name of
    # a table, the id of a field and the id of a join are what a diff and the gate read a
    # graph by, so the graph holds one node to each: declarations that say the same are
    # one declaration and a named repeat, declarations that do not are a named conflict and
    # no node - there is no telling which of them the model means.
    named: dict[str, list[Table]] = {}
    for table in model.tables:
        named.setdefault(table.name, []).append(table)

    for index, table in enumerate(model.tables):
        if not table.name:
            # The name is the table's id, the first part of the id of each of its fields
            # and what a diff calls a change of either. A table declared without one has
            # no place in the graph, and is named by where it stands in the file.
            gaps.append(
                SemanticGap(
                    code="gap.table_unnamed",
                    severity=Severity.BLOCK,
                    path=f"tables.{index}.name",
                    reason=(
                        "table is declared with no name: it and its "
                        f"{len(table.columns)} column(s) are not in the graph"
                    ),
                )
            )
            continue
        declarations = named.pop(table.name, None)
        if declarations is None:
            continue  # answered where the name first stands
        if len(declarations) > 1:
            if len({_table_says(declaration) for declaration in declarations}) > 1:
                gaps.append(
                    SemanticGap(
                        code="gap.table_conflict",
                        severity=Severity.BLOCK,
                        path=f"tables.{table.name}",
                        reason=(
                            f"table is declared {len(declarations)} times and not alike: "
                            "it and its columns are not in the graph"
                        ),
                    )
                )
                continue
            gaps.append(
                SemanticGap(
                    code="gap.table_repeated",
                    severity=Severity.ADVISORY,
                    path=f"tables.{table.name}",
                    reason=(
                        f"table is declared {len(declarations)} times, alike: "
                        "the graph holds it once"
                    ),
                )
            )
        if table.physical is None:
            gaps.append(
                SemanticGap(
                    code="gap.missing_physical",
                    severity=Severity.ADVISORY,
                    path=f"tables.{table.name}.physical",
                    reason="no engine or key facts declared; the physical gate cannot run",
                )
            )
        tables.append(_table_node(table))
        declared.extend(_field_nodes(table))

    fields: list[FieldNode] = []
    fields_by_id: dict[str, list[FieldNode]] = {}
    for field in declared:
        fields_by_id.setdefault(field.id, []).append(field)
    for field_id, same_field in fields_by_id.items():
        if len(same_field) > 1:
            if not _alike(same_field):
                gaps.append(
                    SemanticGap(
                        code="gap.field_conflict",
                        severity=Severity.BLOCK,
                        path=f"fields.{field_id}",
                        reason=(
                            f"field is declared {len(same_field)} times and not alike: "
                            "it is not in the graph"
                        ),
                    )
                )
                continue
            gaps.append(
                SemanticGap(
                    code="gap.field_repeated",
                    severity=Severity.ADVISORY,
                    path=f"fields.{field_id}",
                    reason=(
                        f"field is declared {len(same_field)} times, alike: the graph holds it once"
                    ),
                )
            )
        fields.append(same_field[0])

    known = {field.id for field in fields}
    joins: list[JoinNode] = []
    joins_by_id: dict[str, list[JoinNode]] = {}
    for join in model.joins:
        node = JoinNode(
            id=f"{join.left}->{join.right}", left=join.left, right=join.right, type=join.type
        )
        joins_by_id.setdefault(node.id, []).append(node)
    for join_id, same_join in joins_by_id.items():
        if not _alike(same_join):
            gaps.append(
                SemanticGap(
                    code="gap.join_conflict",
                    severity=Severity.BLOCK,
                    path=f"joins.{join_id}",
                    reason=(
                        f"join is declared {len(same_join)} times and not alike: "
                        "it is not in the graph"
                    ),
                )
            )
            continue
        first = same_join[0]
        if first.left not in known or first.right not in known:
            missing = [side for side in (first.left, first.right) if side not in known]
            gaps.append(
                SemanticGap(
                    code="gap.dangling_join",
                    severity=Severity.BLOCK,
                    path=f"joins.{join_id}",
                    reason=f"join endpoint is not a declared field: {', '.join(missing)}",
                )
            )
            continue
        if len(same_join) > 1:
            gaps.append(
                SemanticGap(
                    code="gap.join_repeated",
                    severity=Severity.ADVISORY,
                    path=f"joins.{join_id}",
                    reason=(
                        f"join is declared {len(same_join)} times, alike: the graph holds it once"
                    ),
                )
            )
        joins.append(first)

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

    metrics = [
        _metric_node(
            contract, known, gaps, _resolution(contract, manifest, dbt_database, tables, fields)
        )
        for contract in contracts
    ]

    # Two contracts of one metric id are two definitions of the metric, and the ref does not
    # say which of them it means: a diff keeps the last node of an id and the gate reads the
    # first, in the order `snapshot` hands the contracts in — of versions, then of bytes —
    # and neither is one the ref chose. The nodes all stay, for a pin names the versions it
    # was given; the id is a blocking gap, one to the id and in the order of the ids,
    # whatever the order of the files.
    versions: dict[str, list[int]] = {}
    for metric_node in metrics:
        versions.setdefault(metric_node.id, []).append(metric_node.version)
    for metric_id, held in sorted(versions.items()):
        if len(held) > 1:
            gaps.append(
                SemanticGap(
                    code="gap.metric_duplicate",
                    severity=Severity.BLOCK,
                    path=f"metrics.{metric_id}",
                    reason=(
                        f"metric is defined by {len(held)} contracts (versions "
                        f"{', '.join(str(version) for version in sorted(held))}): "
                        "which of them the ref means is not known"
                    ),
                )
            )

    return SemanticGraph(
        tables=sorted(tables, key=lambda node: node.id),
        fields=sorted(fields, key=lambda node: node.id),
        joins=sorted(joins, key=lambda node: node.id),
        metrics=sorted(metrics, key=lambda node: node.id),
    )


def _table_node(table: Table) -> TableNode:
    physical = table.physical
    return TableNode(
        id=table.name,
        grain=list(table.grain),
        classification=table.classification or DataClassification.INTERNAL,
        description=table.description,
        engine=physical.engine if physical else "",
        table_engine=physical.table_engine if physical else "",
        sorting_key=list(physical.sorting_key) if physical else [],
        distribution_key=list(physical.distribution_key) if physical else [],
        partition_key=physical.partition_key if physical else "",
    )


def _field_nodes(table: Table) -> list[FieldNode]:
    table_class = table.classification or DataClassification.INTERNAL
    return [
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
        for column in table.columns
    ]


def _says(node: StrictModel) -> str:
    """A node as text. Declarations are alike when the graph could not tell them apart."""
    return json.dumps(node.model_dump(mode="json"), sort_keys=True)


def _alike(nodes: Sequence[StrictModel]) -> bool:
    return len({_says(node) for node in nodes}) == 1


def _table_says(table: Table) -> tuple[str, bool, tuple[str, ...]]:
    """What one declaration of a table gives the graph: its node, whether it declares
    physical facts (a node cannot say: with none the facts are empty text, as facts declared
    empty are), and the nodes of its columns, each as many times as it declares the column.
    """
    return (
        _says(_table_node(table)),
        table.physical is None,
        tuple(sorted(_says(node) for node in _field_nodes(table))),
    )


def metric_node(contract: MetricContract) -> MetricNode:
    """The node of this contract's definition, as the contract declares it.

    It holds the formula the contract writes and no binding: the form :func:`declared_node`
    gives a snapshot's node, so the two compare whatever artifact the snapshot resolved a
    formula from. What the model does not declare is a gap of a snapshot, and no snapshot is
    built here.
    """
    return _metric_node(contract, set(), [], None)


def declared_node(node: MetricNode) -> MetricNode:
    """*node* as its contract declares it (ADR 0008, item 10).

    A bound node gets the declared ``external_ref`` back as its formula, the fields that
    declaration reads — the grain keys and the event time — and no binding; any other node is
    returned as it is. A snapshot's node in this form is :func:`metric_node` of the contract it
    was built from: a formula resolved from the artifact the contract pins is not a difference
    from that contract.
    """
    if node.binding is None:
        return node
    formula = node.binding.declared()
    return node.model_copy(
        update={
            "formula": formula,
            "binding": None,
            "fields": _dependencies(node.grain, node.time, formula),
        }
    )


def _dependencies(grain: MetricGrain, time: MetricTime, formula: MetricFormula) -> list[str]:
    """The fields a metric's value depends on: its grain keys, event time and formula terms."""
    found = {f"{grain.table}.{key}" for key in grain.keys}
    found.add(f"{grain.table}.{time.event_time_column}")
    for term in (formula.aggregate, formula.numerator, formula.denominator):
        if term is not None:
            found.add(f"{term.table}.{term.column}")
    return sorted(found)


def _resolution(
    contract: MetricContract,
    manifest: ManifestRead | None,
    dbt_database: str | None,
    tables: list[TableNode],
    fields: list[FieldNode],
) -> FormulaResolution | None:
    """The contract's formula resolved from *manifest*, when a dbt semantic manifest defines it.

    The grain table is read as the graph holds it — its engine and the type of each of its
    fields — so a table or a field the model declares in conflict, which the graph does not
    hold, is no context a parity row is proven in.
    """
    if not is_dbt_formula(contract):
        return None
    table = contract.grain.table
    declared = DeclaredTable(
        engine=next((node.engine for node in tables if node.id == table), ""),
        column_types={node.column: node.type for node in fields if node.table == table},
    )
    return resolve_formula(contract, manifest, database=dbt_database, declared=declared)


def _metric_node(
    contract: MetricContract,
    known: set[str],
    gaps: list[SemanticGap],
    resolution: FormulaResolution | None,
) -> MetricNode:
    identifier = contract.metadata.id
    grain_table = contract.grain.table

    formula = contract.formula
    binding: FormulaBinding | None = None
    external = contract.formula.external
    if resolution is not None and external is not None:
        binding = FormulaBinding(
            source=external.source,
            ref=external.ref,
            source_revision=external.source_revision,
            closure_digest=resolution.closure_digest or None,
        )
        if resolution.formula is not None:
            formula = resolution.formula
    dependencies = _dependencies(contract.grain, contract.time, formula)
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
    if resolution is not None:
        # Why a bound formula stays `external_ref`, each at the place in the artifact it is
        # about; the value it carries is the closure the artifact gave, never its text.
        for found in resolution.gaps:
            gaps.append(
                SemanticGap(
                    code=found.code,
                    severity=found.severity,
                    path=f"metrics.{identifier}.formula" + (f":{found.path}" if found.path else ""),
                    reason=found.reason,
                    source_value_ref=resolution.closure_digest,
                )
            )

    for reference in sorted({*dependencies, *(f"{grain_table}.{d}" for d in contract.dimensions)}):
        if reference not in known:
            gaps.append(
                SemanticGap(
                    code="gap.metric_field_missing",
                    severity=Severity.BLOCK,
                    path=f"metrics.{identifier}",
                    reason=f"metric references a field the model does not declare: {reference}",
                )
            )

    join_paths = sorted(
        [
            JoinNode(id=f"{left}->{right}", left=left, right=right, type=cardinality)
            for left, right, cardinality in {
                (join.left, join.right, join.type) for join in contract.join_paths
            }
        ],
        key=lambda node: (node.id, node.left, node.right, node.type),
    )
    path_types: dict[tuple[str, str], set[str]] = {}
    for join in join_paths:
        path_types.setdefault((join.left, join.right), set()).add(join.type)
    for (left, right), types in path_types.items():
        path = f"metrics.{identifier}.join_paths.{left}->{right}"
        missing = list(dict.fromkeys(side for side in (left, right) if side not in known))
        if missing:
            gaps.append(
                SemanticGap(
                    code="gap.metric_join_dangling",
                    severity=Severity.BLOCK,
                    path=path,
                    reason=f"join path endpoint is not a declared field: {', '.join(missing)}",
                )
            )
        if len(types) > 1:
            gaps.append(
                SemanticGap(
                    code="gap.metric_join_conflict",
                    severity=Severity.BLOCK,
                    path=path,
                    reason=(
                        "join path declared with more than one cardinality: "
                        + ", ".join(sorted(types))
                    ),
                )
            )

    return MetricNode(
        id=identifier,
        version=contract.metadata.version,
        owner=contract.metadata.owner,
        status=contract.metadata.status,
        description=contract.metadata.description,
        formula=formula,
        binding=binding,
        grain=contract.grain,
        time=contract.time,
        unit=contract.unit,
        additivity=contract.additivity,
        non_additive_axes=list(contract.non_additive_axes),
        dimensions=list(contract.dimensions),
        join_paths=join_paths,
        classification=contract.classification,
        policy_refs=list(contract.policy_refs),
        fields=dependencies,
        budgets=contract.budgets,
        freshness=MetricFreshness(
            max_age=contract.freshness.max_age,
            quality_preconditions=sorted(set(contract.freshness.quality_preconditions)),
        ),
    )
